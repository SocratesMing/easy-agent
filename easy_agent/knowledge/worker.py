"""Durable MySQL/SQLite-backed worker for knowledge side effects."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import Config
from ..db.database import Database
from .config import KnowledgeConfig
from .domain import DocumentStatus, KnowledgeBaseStatus, OperationPhase, OperationStatus
from .operations_repository import KnowledgeOperationsRepository
from .ragflow import RagflowClient, RagflowError
from .reconciliation import KnowledgeReconciler
from .repository import KnowledgeRepository
from .schema import validate_knowledge_schema
from .storage import OriginalStorageError, create_original_store
from .storage.base import OriginalDocumentStore

logger = logging.getLogger(__name__)


class TaskExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


class KnowledgeTaskWorker:
    def __init__(
        self,
        db: Database,
        config: KnowledgeConfig,
        ragflow: RagflowClient,
        original_store: OriginalDocumentStore | None,
        *,
        worker_id: str | None = None,
    ):
        self.repository = KnowledgeRepository(db)
        self.operations = KnowledgeOperationsRepository(db)
        self.config = config
        self.ragflow = ragflow
        self.original_store = original_store
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.reconciler = KnowledgeReconciler(
            self.repository, self.operations, ragflow, config, original_store
        )
        self._stop = asyncio.Event()
        self._last_reconcile = datetime.min.replace(tzinfo=UTC)
        self._last_maintenance = datetime.min.replace(tzinfo=UTC)

    async def _thread(self, method, *args, **kwargs):
        return await asyncio.to_thread(method, *args, **kwargs)

    async def _task_heartbeat(self, task_id: str) -> None:
        interval = self.config.operations.worker.heartbeat_interval_seconds
        try:
            while True:
                await asyncio.sleep(interval)
                await self._thread(self.operations.heartbeat_task, task_id, self.worker_id)
        except asyncio.CancelledError:
            return

    async def _submit_document(self, task: dict[str, Any], *, replace: bool) -> None:
        document_id = str(task["resource_id"])
        document = await self._thread(self.repository.get_document, document_id)
        if document is None:
            raise TaskExecutionError("KNOWLEDGE_NOT_FOUND", "文档不存在", retryable=False)
        base = await self._thread(self.repository.get_base, str(document["base_id"]))
        original = await self._thread(self.repository.get_original_object, document_id)
        if base is None or not base.get("remote_dataset_id"):
            raise TaskExecutionError("KNOWLEDGE_BASE_NOT_READY", "知识库未就绪", retryable=True)
        if self.original_store is None or original is None or str(original.get("status")) != "available":
            raise TaskExecutionError("ORIGINAL_STORAGE_NOT_AVAILABLE", "原文不可用", retryable=True)
        handle = await self._thread(self.original_store.open, str(original["storage_key"]))
        old_remote_id = document.get("remote_document_id")
        source = handle.open_binary()
        try:
            await self._thread(
                self.repository.update_operation, str(task["operation_id"]),
                phase=OperationPhase.UPLOADING.value, status=OperationStatus.RUNNING.value,
                progress=0.4, error_code=None, error_message=None,
            )
            if replace and old_remote_id:
                await self.ragflow.delete_documents(
                    dataset_id=str(base["remote_dataset_id"]),
                    document_ids=[str(old_remote_id)], request_id=str(task["request_id"]),
                )
            remote = await self.ragflow.upload_document(
                dataset_id=str(base["remote_dataset_id"]), filename=str(document["name"]),
                content=source, content_type=str(document["content_type"]),
                request_id=str(task["request_id"]),
            )
            remote_id = str(remote["id"])
            await self._thread(
                self.repository.update_document, document_id,
                remote_document_id=remote_id, status=DocumentStatus.PENDING.value,
                error_code=None, error_message=None, deleted_at=None, deleted_by=None,
                purge_after=None,
            )
            await self._thread(
                self.repository.update_operation, str(task["operation_id"]),
                phase=OperationPhase.CONFIGURING.value, progress=0.7,
            )
            await self.ragflow.configure_document(
                dataset_id=str(base["remote_dataset_id"]), document_id=remote_id,
                filename=str(document["name"]), request_id=str(task["request_id"]),
            )
            await self.ragflow.start_parsing(
                dataset_id=str(base["remote_dataset_id"]), document_ids=[remote_id],
                request_id=str(task["request_id"]),
            )
            if replace:
                await self._thread(
                    self.operations.supersede_document_dead_letters,
                    resource_id=document_id,
                    successful_task_id=str(task["id"]),
                )
            await self._thread(
                self.repository.update_document, document_id,
                status=DocumentStatus.PROCESSING.value, progress=0.0,
            )
            await self._thread(
                self.repository.update_operation, str(task["operation_id"]),
                phase=OperationPhase.PARSING.value, status=OperationStatus.RUNNING.value,
                progress=0.0,
            )
        finally:
            source.close()

    async def _delete_document(self, task: dict[str, Any]) -> None:
        document_id = str(task["resource_id"])
        document = await self._thread(self.repository.get_document, document_id)
        if document is None:
            return
        if str(document.get("status")) != DocumentStatus.DELETED.value:
            # A restore request won the race before this queued delete started.
            return
        base = await self._thread(self.repository.get_base, str(document["base_id"]))
        if base and document.get("remote_document_id"):
            await self.ragflow.delete_documents(
                dataset_id=str(base["remote_dataset_id"]),
                document_ids=[str(document["remote_document_id"])],
                request_id=str(task["request_id"]),
            )
        # Preserve the original throughout the configured recovery window.  It
        # is quarantined/purged only by the expiry sweep.
        await self._thread(
            self.repository.update_document, document_id,
            remote_document_id=None, status=DocumentStatus.DELETED.value,
            progress=None, error_code=None, error_message=None,
        )
        await self._thread(
            self.repository.update_operation, str(task["operation_id"]),
            phase=OperationPhase.COMPLETED.value, status=OperationStatus.SUCCEEDED.value,
            current_count=1, progress=1.0, retryable=False,
        )

    async def _stage_base_deletion(self, task: dict[str, Any]) -> None:
        """Acknowledge a logical delete; physical destruction waits for retention."""

        base = await self._thread(
            self.repository.get_base, str(task["resource_id"])
        )
        if base is None:
            return
        if str(base.get("status")) != KnowledgeBaseStatus.DELETED.value:
            await self._thread(
                self.repository.update_operation, str(task["operation_id"]),
                phase=OperationPhase.COMPLETED.value,
                status=OperationStatus.CANCELLED.value,
                retryable=False,
            )
            return
        await self._thread(
            self.repository.update_operation, str(task["operation_id"]),
            phase=OperationPhase.COMPLETED.value,
            status=OperationStatus.SUCCEEDED.value,
            current_count=1, progress=1.0, retryable=False,
        )

    async def _purge_expired_bases(self) -> None:
        bases = await self._thread(
            self.repository.list_bases_due_for_purge,
            before=datetime.now(UTC).isoformat(),
        )
        for base in bases:
            base_id = str(base["id"])
            alert_key = f"purge-base:{base_id}"
            try:
                remote_id = base.get("remote_dataset_id")
                if remote_id:
                    await self.ragflow.delete_datasets(
                        [str(remote_id)], request_id=f"purge-{uuid.uuid4()}"
                    )
                    await self._thread(
                        self.repository.update_base,
                        base_id,
                        remote_dataset_id=None,
                    )
                documents = await self._thread(
                    self.repository.list_all_documents,
                    base_id=base_id,
                    include_deleted=True,
                    limit=1_000_000,
                )
                for document in documents:
                    original_key = document.get("original_storage_key")
                    original_status = str(document.get("original_status") or "")
                    if not original_key or original_status == "deleted":
                        continue
                    if self.original_store is None:
                        raise TaskExecutionError(
                            "ORIGINAL_STORAGE_NOT_AVAILABLE",
                            "原文存储不可用",
                            retryable=True,
                        )
                    await self._thread(
                        self.original_store.purge, str(original_key)
                    )
                await self._thread(self.repository.delete_base, base_id)
                await self._thread(self.operations.resolve_alert, alert_key)
            except (RagflowError, OriginalStorageError, TaskExecutionError) as exc:
                await self._thread(
                    self.operations.upsert_alert,
                    dedup_key=alert_key,
                    alert_type="BASE_PURGE_FAILED",
                    severity="critical",
                    summary="过期知识库销毁失败",
                    details={
                        "base_id": base_id,
                        "error_code": getattr(exc, "code", "KNOWLEDGE_PURGE_FAILED"),
                    },
                )

    async def _purge_expired(self) -> None:
        due = await self._thread(
            self.repository.list_due_for_purge, before=datetime.now(UTC).isoformat()
        )
        if self.original_store is None:
            return
        for document in due:
            original = await self._thread(
                self.repository.get_original_object, str(document["id"])
            )
            if not original or str(original.get("status")) == "deleted":
                continue
            try:
                await self._thread(self.original_store.purge, str(original["storage_key"]))
                await self._thread(
                    self.repository.update_original_object, str(document["id"]),
                    status="deleted", deleted_at=datetime.now(UTC).isoformat(),
                    error_code=None, error_message=None,
                )
            except OriginalStorageError as exc:
                self.operations.upsert_alert(
                    dedup_key=f"purge:{document['id']}", alert_type="ORIGINAL_PURGE_FAILED",
                    severity="warning", summary="过期原文清理失败",
                    details={"document_id": document["id"], "error_code": exc.code},
                )

    async def process_one(self) -> bool:
        task = await self._thread(
            self.operations.claim_next_task,
            worker_id=self.worker_id,
            timeout_seconds=self.config.operations.worker.task_timeout_seconds,
        )
        if task is None:
            return False
        heartbeat = asyncio.create_task(self._task_heartbeat(str(task["id"])))
        try:
            async with asyncio.timeout(self.config.operations.worker.task_timeout_seconds):
                task_type = str(task["task_type"])
                if task_type == "document_upload":
                    await self._submit_document(task, replace=False)
                elif task_type == "document_retry":
                    await self._submit_document(task, replace=True)
                elif task_type == "document_delete":
                    await self._delete_document(task)
                elif task_type == "dataset_delete":
                    await self._stage_base_deletion(task)
                elif task_type == "reconcile":
                    await self.reconciler.run(request_id=str(task["request_id"]))
                else:
                    raise TaskExecutionError("KNOWLEDGE_TASK_UNSUPPORTED", "任务类型不支持", retryable=False)
            await self._thread(self.operations.finish_task, str(task["id"]))
            return True
        except (RagflowError, OriginalStorageError, TaskExecutionError, TimeoutError, KeyError) as exc:
            code = getattr(exc, "code", "KNOWLEDGE_TASK_TIMEOUT" if isinstance(exc, TimeoutError) else "KNOWLEDGE_TASK_FAILED")
            retryable = bool(getattr(exc, "retryable", not isinstance(exc, KeyError)))
            safe_message = getattr(exc, "safe_message", "知识任务执行失败")
            result = await self._thread(
                self.operations.fail_task, str(task["id"]), error_code=str(code),
                error_message=str(safe_message), retryable=retryable,
                retry_backoff_seconds=self.config.operations.worker.retry_backoff_seconds,
            )
            final = result == "dead_letter"
            await self._thread(
                self.repository.update_operation, str(task["operation_id"]),
                phase=OperationPhase.FAILED.value if final else OperationPhase.QUEUED.value,
                status=OperationStatus.DEAD_LETTER.value if final else OperationStatus.ACCEPTED.value,
                retryable=not final, error_code=str(code), error_message=str(safe_message),
            )
            if str(task["resource_type"]) == "document" and str(task["task_type"]) != "document_delete":
                await self._thread(
                    self.repository.update_document, str(task["resource_id"]),
                    status=DocumentStatus.FAILED.value if final else DocumentStatus.PENDING.value,
                    error_code=str(code), error_message=str(safe_message),
                )
            return True
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def maintain(self) -> None:
        stale_before = (
            datetime.now(UTC) - timedelta(seconds=self.config.operations.worker.stale_after_seconds)
        ).isoformat()
        recovered = await self._thread(
            self.operations.recover_stale_tasks, stale_before=stale_before
        )
        counts = await self._thread(self.operations.task_counts)
        await self._thread(
            self.operations.upsert_heartbeat, component="knowledge-worker",
            instance_id=self.worker_id, status="ready",
            details={"task_counts": counts, "recovered": recovered},
        )
        backlog = counts.get("queued", 0) + counts.get("retry", 0)
        if backlog >= self.config.observability.task_backlog_warning:
            await self._thread(
                self.operations.upsert_alert, dedup_key="knowledge-task-backlog",
                alert_type="TASK_BACKLOG", severity="warning",
                summary="知识任务积压超过阈值", details={"backlog": backlog},
            )
        else:
            await self._thread(self.operations.resolve_alert, "knowledge-task-backlog")
        failed = await self._thread(self.operations.actionable_dead_letter_count)
        if failed >= self.config.observability.failed_task_warning:
            await self._thread(
                self.operations.upsert_alert, dedup_key="knowledge-dead-letter",
                alert_type="DEAD_LETTER", severity="critical",
                summary="知识任务进入人工处理队列", details={"count": failed},
            )
        else:
            await self._thread(self.operations.resolve_alert, "knowledge-dead-letter")
        try:
            await self.ragflow.health(request_id=f"worker-health-{uuid.uuid4()}")
            await self._thread(self.operations.resolve_alert, "knowledge-ragflow-unavailable")
        except RagflowError as exc:
            await self._thread(
                self.operations.upsert_alert, dedup_key="knowledge-ragflow-unavailable",
                alert_type="RAGFLOW_UNAVAILABLE", severity="critical",
                summary="RAGFlow 健康检查失败", details={"error_code": exc.code},
            )
        nas_ready = self.original_store is not None and await self._thread(self.original_store.health)
        if nas_ready:
            await self._thread(self.operations.resolve_alert, "knowledge-original-storage-unavailable")
        else:
            await self._thread(
                self.operations.upsert_alert, dedup_key="knowledge-original-storage-unavailable",
                alert_type="ORIGINAL_STORAGE_UNAVAILABLE", severity="critical",
                summary="原文存储健康检查失败", details={},
            )
        await self._purge_expired_bases()
        await self._purge_expired()
        if (
            self.config.reconciliation.enabled
            and (datetime.now(UTC) - self._last_reconcile).total_seconds()
            >= self.config.reconciliation.interval_seconds
        ):
            await self.reconciler.run()
            self._last_reconcile = datetime.now(UTC)

    async def run_forever(self) -> None:
        logger.info("knowledge worker started | worker_id=%s", self.worker_id)
        while not self._stop.is_set():
            if (
                datetime.now(UTC) - self._last_maintenance
            ).total_seconds() >= self.config.operations.worker.heartbeat_interval_seconds:
                await self.maintain()
                self._last_maintenance = datetime.now(UTC)
            processed = await self.process_one()
            if not processed:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.config.operations.worker.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stop.set()


def _load_runtime() -> tuple[Database, KnowledgeConfig, RagflowClient, OriginalDocumentStore | None]:
    path = Config.resolve_config_path()
    app_config = Config.from_yaml(path)
    knowledge_config = KnowledgeConfig.load(path)
    if not knowledge_config.enabled:
        raise RuntimeError("knowledge module is disabled")
    db = Database(app_config.database.model_dump())
    validate_knowledge_schema(db)
    return db, knowledge_config, RagflowClient(knowledge_config), create_original_store(knowledge_config)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    db, config, ragflow, store = _load_runtime()
    worker = KnowledgeTaskWorker(db, config, ragflow, store)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run_forever()
    finally:
        await ragflow.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()


__all__ = ["KnowledgeTaskWorker"]
