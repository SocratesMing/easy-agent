"""文档上传/解析状态链回归测试。

状态机：pending（已提交上游）→ processing（上游 RUNNING）→
ready/failed；超时是失败终态，暂停/恢复/重试会清空旧计时重新开始。
"""

from __future__ import annotations

import io

from easy_agent.knowledges.core.models import (
    DocumentStatus,
    OperationPhase,
    OperationStatus,
)
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.ragflow import RagflowNotFoundError

from .test_shared_dataset import (
    _FakeRagflow,
    _add_document,
    _create_base_row,
    _make_service,
    _principal,
)


OLD_PARSE_STARTED_AT = "2020-01-01T00:00:00+00:00"


class _StatusFakeRagflow(_FakeRagflow):
    """在共享库假客户端上补充暂停/删除接口。"""

    def __init__(self, *, delete_error: Exception | None = None):
        super().__init__()
        self.deleted: list[str] = []
        self.stopped: list[str] = []
        self.delete_error = delete_error

    async def delete_documents(self, dataset_id, document_ids):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.extend(document_ids)

    async def stop_parsing(self, dataset_id, document_ids):
        self.stopped.extend(document_ids)


async def _upload(service, base) -> tuple[str, str]:
    content = b"document status flow"
    accepted = await service.upload_document(
        base_id=str(base["id"]),
        folder_id=None,
        filename="status.txt",
        content_type="text/plain",
        size_bytes=len(content),
        content=io.BytesIO(content),
        principal=_principal(),
        request_id="req-status-flow",
    )
    return accepted.document.id, accepted.operation.id


def _remote_id(ragflow: _StatusFakeRagflow, index: int = -1) -> str:
    return str(ragflow.parsed[index]["document_ids"][0])


async def test_upload_pending_then_processing_then_ready(db, tmp_path):
    ragflow = _StatusFakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)

    document_id, operation_id = await _upload(service, base)
    repository = KnowledgeRepository(db)

    document = repository.get_document(document_id)
    assert document["status"] == DocumentStatus.PENDING.value
    assert document["progress"] is None
    assert document["parse_started_at"] is None
    operation = repository.get_operation(operation_id)
    assert operation["phase"] == OperationPhase.PARSING.value

    remote_id = _remote_id(ragflow)
    ragflow.documents_by_id[remote_id] = {
        "id": remote_id,
        "run": "RUNNING",
        "progress": 0.25,
    }
    await service.refresh_base_documents(base)

    document = repository.get_document(document_id)
    assert document["status"] == DocumentStatus.PROCESSING.value
    assert document["progress"] == 0.25
    assert document["parse_started_at"] is not None
    operation = repository.get_operation(operation_id)
    assert operation["status"] == OperationStatus.RUNNING.value

    ragflow.documents_by_id[remote_id] = {
        "id": remote_id,
        "run": "DONE",
        "progress": 1.0,
    }
    await service.refresh_base_documents(base)

    document = repository.get_document(document_id)
    assert document["status"] == DocumentStatus.READY.value
    assert document["progress"] == 1.0
    assert document["error_code"] is None
    operation = repository.get_operation(operation_id)
    assert operation["phase"] == OperationPhase.COMPLETED.value
    assert operation["status"] == OperationStatus.SUCCEEDED.value
    assert operation["current_count"] == 1


async def test_retry_orphaned_remote_document_uses_local_original(db, tmp_path):
    """远端旧副本属于另一个数据集时，跳过删除并用本地原文重传。"""

    ragflow = _StatusFakeRagflow(
        delete_error=RagflowNotFoundError(
            "These documents do not belong to dataset ds-other or not found"
        )
    )
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)
    document_id, _operation_id = await _upload(service, base)
    repository = KnowledgeRepository(db)
    repository.update_document(document_id, status=DocumentStatus.FAILED.value)

    accepted = await service.retry_document(
        document_id=document_id,
        principal=_principal(),
        request_id="req-retry-orphan",
    )

    assert accepted.document.id == document_id
    assert len(ragflow.uploaded) == 2
    assert ragflow.uploaded[-1]["dataset_id"] == ragflow.uploaded[0]["dataset_id"]


async def test_parse_timeout_is_terminal_and_stops_polling(db, tmp_path):
    ragflow = _StatusFakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)
    document_id, operation_id = await _upload(service, base)
    repository = KnowledgeRepository(db)
    remote_id = _remote_id(ragflow)
    ragflow.documents_by_id[remote_id] = {
        "id": remote_id,
        "run": "RUNNING",
        "progress": 0.1,
    }
    await service.refresh_base_documents(base)
    repository.update_document(document_id, parse_started_at=OLD_PARSE_STARTED_AT)

    await service.refresh_base_documents(base)

    document = repository.get_document(document_id)
    assert document["status"] == DocumentStatus.FAILED.value
    assert document["error_code"] == "KNOWLEDGE_PARSE_TIMEOUT"
    assert document["progress"] is None
    operation = repository.get_operation(operation_id)
    assert operation["status"] == OperationStatus.FAILED.value
    assert operation["error_code"] == "KNOWLEDGE_PARSE_TIMEOUT"

    # 失败是终态：共享库不再逐篇轮询，也不会被上游 DONE 悄悄拉回 ready
    queries_before = len(ragflow.document_queries)
    ragflow.documents_by_id[remote_id] = {
        "id": remote_id,
        "run": "DONE",
        "progress": 1.0,
    }
    await service.refresh_base_documents(base)
    assert len(ragflow.document_queries) == queries_before
    assert repository.get_document(document_id)["status"] == DocumentStatus.FAILED.value


async def test_retry_resets_parse_timer(db, tmp_path):
    ragflow = _StatusFakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)
    document_id, _ = await _upload(service, base)
    repository = KnowledgeRepository(db)
    old_remote_id = _remote_id(ragflow)
    ragflow.documents_by_id[old_remote_id] = {
        "id": old_remote_id,
        "run": "RUNNING",
        "progress": 0.1,
    }
    await service.refresh_base_documents(base)
    repository.update_document(
        document_id,
        status=DocumentStatus.FAILED.value,
        parse_started_at=OLD_PARSE_STARTED_AT,
        error_code="KNOWLEDGE_PARSE_TIMEOUT",
        error_message="旧超时",
    )

    accepted = await service.retry_document(
        document_id=document_id,
        principal=_principal(),
        request_id="req-retry",
    )

    document = repository.get_document(document_id)
    assert accepted.document.status == DocumentStatus.PENDING
    assert document["status"] == DocumentStatus.PENDING.value
    assert document["parse_started_at"] is None
    assert document["error_code"] is None
    assert old_remote_id in ragflow.deleted

    new_remote_id = _remote_id(ragflow)
    ragflow.documents_by_id[new_remote_id] = {
        "id": new_remote_id,
        "run": "RUNNING",
        "progress": 0.2,
    }
    await service.refresh_base_documents(base)

    document = repository.get_document(document_id)
    assert document["status"] == DocumentStatus.PROCESSING.value
    assert document["parse_started_at"] != OLD_PARSE_STARTED_AT


async def test_pause_and_resume_restart_parse_timer(db, tmp_path):
    ragflow = _StatusFakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)
    document_id, _ = await _upload(service, base)
    repository = KnowledgeRepository(db)
    remote_id = _remote_id(ragflow)
    ragflow.documents_by_id[remote_id] = {
        "id": remote_id,
        "run": "RUNNING",
        "progress": 0.3,
    }
    await service.refresh_base_documents(base)
    repository.update_document(document_id, parse_started_at=OLD_PARSE_STARTED_AT)

    paused = await service.pause_document(
        document_id=document_id,
        principal=_principal(),
        request_id="req-pause",
    )
    assert paused["status"] == DocumentStatus.PAUSED.value
    assert paused["parse_started_at"] is None
    assert remote_id in ragflow.stopped

    resumed = await service.resume_document(
        document_id=document_id,
        principal=_principal(),
        request_id="req-resume",
    )
    assert resumed["status"] == DocumentStatus.PENDING.value
    assert resumed["parse_started_at"] is None

    await service.refresh_base_documents(base)

    document = repository.get_document(document_id)
    assert document["status"] == DocumentStatus.PROCESSING.value
    assert document["parse_started_at"] != OLD_PARSE_STARTED_AT


def test_refreshable_scope_requires_remote_copy_and_active_status(db):
    repository = KnowledgeRepository(db)
    base = _create_base_row(db)
    _add_document(db, str(base["id"]), name="wait-local.txt", status="pending")
    pending = _add_document(
        db,
        str(base["id"]),
        name="wait-remote.txt",
        status="pending",
        remote_document_id="rd-pending",
    )
    processing = _add_document(
        db,
        str(base["id"]),
        name="busy.txt",
        status="processing",
        remote_document_id="rd-processing",
    )
    _add_document(
        db,
        str(base["id"]),
        name="done.txt",
        status="ready",
        remote_document_id="rd-ready",
    )
    failed = _add_document(
        db,
        str(base["id"]),
        name="failed.txt",
        status="failed",
        remote_document_id="rd-failed",
    )
    other = _create_base_row(db, owner_user_id="u-2")
    _add_document(db, str(other["id"]), name="no-remote.txt", status="pending")

    assert repository.list_refreshable_base_ids() == [str(base["id"])]
    rows = repository.list_refreshable_documents(str(base["id"]))
    assert {row["id"] for row in rows} == {pending["id"], processing["id"]}
    assert failed["id"] not in {row["id"] for row in rows}
