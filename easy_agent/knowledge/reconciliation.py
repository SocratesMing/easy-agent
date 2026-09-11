"""Read-only three-way reconciliation with narrowly scoped status repair."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from typing import Any

from starlette.concurrency import run_in_threadpool

from .config import KnowledgeConfig
from .domain import DocumentStatus, OperationPhase, OperationStatus
from .operations_repository import KnowledgeOperationsRepository
from .ragflow import RagflowClient, RagflowError
from .repository import KnowledgeRepository
from .storage.base import OriginalDocumentStore, OriginalStorageError


class KnowledgeReconciler:
    """Compare metadata, authoritative originals and the derived RAG index.

    Unknown upstream resources are deliberately reported but never deleted.
    The sole automatic repair is projecting a known upstream parse status back
    to the matching local document and its active operation.
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        operations: KnowledgeOperationsRepository,
        ragflow: RagflowClient,
        config: KnowledgeConfig,
        original_store: OriginalDocumentStore | None,
    ):
        self.repository = repository
        self.operations = operations
        self.ragflow = ragflow
        self.config = config
        self.original_store = original_store

    async def _repo(self, method, *args, **kwargs):
        return await run_in_threadpool(method, *args, **kwargs)

    async def _record(
        self,
        seen: set[str],
        *,
        run_id: str,
        issue_type: str,
        base_id: str | None,
        document_id: str | None,
        details: Mapping[str, object],
        severity: str = "warning",
    ) -> None:
        key = f"{issue_type}:{base_id or '-'}:{document_id or details.get('remote_id', '-')}"
        seen.add(key)
        await self._repo(
            self.operations.upsert_reconciliation_issue,
            run_id=run_id,
            issue_key=key,
            issue_type=issue_type,
            severity=severity,
            base_id=base_id,
            document_id=document_id,
            details=details,
        )

    async def run(self, *, request_id: str | None = None) -> dict[str, int]:
        request_id = request_id or f"reconcile-{uuid.uuid4()}"
        run_id = await self._repo(
            self.operations.begin_reconciliation, request_id=request_id
        )
        seen: set[str] = set()
        summary = {"bases": 0, "documents": 0, "issues": 0, "repaired": 0}
        try:
            bases = await self._repo(self.repository.list_all_bases)
            remaining = self.config.reconciliation.max_documents_per_run
            for base in bases:
                if remaining <= 0:
                    break
                summary["bases"] += 1
                base_id = str(base["id"])
                local = await self._repo(
                    self.repository.list_all_documents,
                    base_id=base_id,
                    limit=remaining,
                )
                remaining -= len(local)
                summary["documents"] += len(local)
                by_remote = {
                    str(item["remote_document_id"]): item
                    for item in local
                    if item.get("remote_document_id")
                }
                remote_docs: list[dict[str, Any]] = []
                remote_dataset_id = base.get("remote_dataset_id")
                if remote_dataset_id:
                    try:
                        result = await self.ragflow.list_documents(
                            dataset_id=str(remote_dataset_id),
                            page=1,
                            page_size=min(
                                remaining + len(local) + 100,
                                self.config.limits.max_documents_per_query,
                            ),
                            request_id=request_id,
                        )
                        remote_docs = [item for item in result.get("docs", []) if isinstance(item, dict)]
                    except RagflowError as exc:
                        await self._record(
                            seen,
                            run_id=run_id,
                            issue_type="UPSTREAM_UNAVAILABLE",
                            base_id=base_id,
                            document_id=None,
                            details={"error_code": exc.code, "retryable": exc.retryable},
                            severity="critical",
                        )
                remote_ids = {str(item.get("id")) for item in remote_docs if item.get("id")}
                for document in local:
                    document_id = str(document["id"])
                    remote_id = str(document.get("remote_document_id") or "")
                    if not remote_id or remote_id not in remote_ids:
                        await self._record(
                            seen, run_id=run_id, issue_type="LOCAL_WITHOUT_REMOTE",
                            base_id=base_id, document_id=document_id,
                            details={"has_remote_mapping": bool(remote_id)},
                        )
                    if self.original_store is None or not document.get("original_storage_key"):
                        await self._record(
                            seen, run_id=run_id, issue_type="NAS_MISSING",
                            base_id=base_id, document_id=document_id,
                            details={"object_status": document.get("original_status")},
                            severity="critical",
                        )
                    else:
                        try:
                            handle = await self._repo(
                                self.original_store.open, str(document["original_storage_key"])
                            )
                            if handle.size_bytes != int(document.get("original_size_bytes") or document["size_bytes"]):
                                await self._record(
                                    seen, run_id=run_id, issue_type="NAS_SIZE_MISMATCH",
                                    base_id=base_id, document_id=document_id,
                                    details={"expected": document.get("original_size_bytes"), "actual": handle.size_bytes},
                                    severity="critical",
                                )
                            elif self.config.reconciliation.verify_sha256 and document.get("original_sha256"):
                                digest = hashlib.sha256()
                                for chunk in handle.iter_bytes():
                                    digest.update(chunk)
                                if digest.hexdigest() != str(document["original_sha256"]):
                                    await self._record(
                                        seen, run_id=run_id, issue_type="NAS_SHA256_MISMATCH",
                                        base_id=base_id, document_id=document_id,
                                        details={"actual_sha256": digest.hexdigest()}, severity="critical",
                                    )
                        except OriginalStorageError as exc:
                            await self._record(
                                seen, run_id=run_id, issue_type="NAS_MISSING",
                                base_id=base_id, document_id=document_id,
                                details={"error_code": exc.code}, severity="critical",
                            )
                for remote in remote_docs:
                    remote_id = str(remote.get("id") or "")
                    if remote_id and remote_id not in by_remote:
                        await self._record(
                            seen, run_id=run_id, issue_type="REMOTE_WITHOUT_LOCAL",
                            base_id=base_id, document_id=None,
                            details={"remote_id": remote_id},
                        )
                        continue
                    local_doc = by_remote.get(remote_id)
                    if not local_doc:
                        continue
                    projected = self.config.compatibility.map_document_status(remote.get("run"))
                    current = str(local_doc["status"])
                    if projected.value != current:
                        await self._record(
                            seen, run_id=run_id, issue_type="STATUS_MISMATCH",
                            base_id=base_id, document_id=str(local_doc["id"]),
                            details={"local": current, "upstream": projected.value},
                        )
                        if self.config.reconciliation.auto_repair_status:
                            progress = remote.get("progress")
                            if not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
                                progress = 1.0 if projected == DocumentStatus.READY else None
                            await self._repo(
                                self.repository.update_document, str(local_doc["id"]),
                                status=projected.value, progress=progress,
                                error_code=None if projected == DocumentStatus.READY else local_doc.get("error_code"),
                                error_message=None if projected == DocumentStatus.READY else local_doc.get("error_message"),
                            )
                            if projected == DocumentStatus.READY:
                                await self._repo(
                                    self.repository.update_active_document_operations,
                                    str(local_doc["id"]), phase=OperationPhase.COMPLETED.value,
                                    status=OperationStatus.SUCCEEDED.value, current_count=1,
                                    progress=1.0, retryable=False, error_code=None, error_message=None,
                                )
                            summary["repaired"] += 1
            summary["issues"] = len(seen)
            await self._repo(
                self.operations.resolve_unseen_reconciliation_issues, seen_keys=seen
            )
            await self._repo(self.operations.finish_reconciliation, run_id, summary=summary)
            return summary
        except Exception:
            await self._repo(
                self.operations.finish_reconciliation, run_id,
                summary=summary, error_message="reconciliation failed",
            )
            raise


__all__ = ["KnowledgeReconciler"]
