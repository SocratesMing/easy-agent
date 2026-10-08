"""软删除保留期到期清理（purge）单元测试。

不依赖真实 Ragflow：假客户端记录删除调用，临时 SQLite 主库。
覆盖：到期判定、删库场景、共享/存量数据集路由、远端缺失幂等、
瞬时失败留待重试、批量上限、worker 接线。
"""

from datetime import UTC, datetime, timedelta

import pytest

from easy_agent.knowledges.config import KnowledgeConfig
from easy_agent.knowledges.repository import KnowledgeRepository
from easy_agent.knowledges.ragflow import RagflowError, RagflowNotFoundError
from easy_agent.knowledges.service import (
    SHARED_DATASET_NAME,
    KnowledgeService,
    reset_shared_dataset_cache,
)
from easy_agent.knowledges.worker import KnowledgePurgeWorker


class _FakeRagflow:
    """假 Ragflow 客户端：仅实现清理路径所需的最小接口。"""

    def __init__(self, *, existing_datasets=None, delete_error=None):
        self.existing_datasets = list(existing_datasets or [])
        self.delete_calls: list[dict] = []
        self.created_datasets: list[dict] = []
        self._delete_error = delete_error

    async def list_datasets(self, *, name=None, **kwargs):
        return {"datasets": list(self.existing_datasets)}

    async def create_dataset(self, **kwargs):
        self.created_datasets.append(kwargs)
        return {"id": "ds-shared-created"}

    async def delete_documents(self, dataset_id, document_ids):
        if self._delete_error is not None:
            raise self._delete_error
        self.delete_calls.append(
            {"dataset_id": dataset_id, "document_ids": list(document_ids)}
        )


@pytest.fixture(autouse=True)
def _isolate_shared_dataset_cache():
    """共享数据集 ID 是进程级缓存，必须在每个用例前后复位。"""
    reset_shared_dataset_cache()
    yield
    reset_shared_dataset_cache()


def _make_service(db, ragflow: _FakeRagflow) -> KnowledgeService:
    return KnowledgeService(
        KnowledgeRepository(db),
        ragflow,
        KnowledgeConfig(enabled=True, base_url="http://ragflow", api_key="rk"),
    )


def _iso(days_from_now: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days_from_now)).isoformat()


def _create_base(
    db, *, status: str = "active", purge_after: str | None = None,
    remote_dataset_id: str | None = None,
) -> dict:
    repository = KnowledgeRepository(db)
    base = repository.create_base(
        name=f"base-{status}-{remote_dataset_id}",
        description="",
        space_type="personal",
        owner_user_id="u-1",
        department_id=None,
        embedding_model="",
    )
    fields: dict = {"status": status}
    if purge_after is not None:
        fields["purge_after"] = purge_after
    if remote_dataset_id is not None:
        fields["remote_dataset_id"] = remote_dataset_id
    return repository.update_base(base["id"], **fields)


def _add_document(
    db, base_id: str, *, name: str, status: str,
    remote_document_id: str, purge_after: str | None = None,
) -> dict:
    repository = KnowledgeRepository(db)
    document = repository.create_document(
        base_id=base_id,
        folder_id=None,
        name=name,
        content_type="text/plain",
        size_bytes=16,
        created_by="u-1",
        status=status,
    )
    fields: dict = {"remote_document_id": remote_document_id}
    if purge_after is not None:
        fields.update(purge_after=purge_after, deleted_at=_iso(-30), deleted_by="u-1")
    return repository.update_document(document["id"], **fields)


def _remote_ref(db, document_id: str) -> str:
    document = KnowledgeRepository(db).get_document(document_id)
    return str(document["remote_document_id"] or "").strip()


# ---------------------------------------------------------------------------
# 到期判定与基本清理
# ---------------------------------------------------------------------------
async def test_purge_deletes_remote_copy_and_clears_local_ref(db):
    ragflow = _FakeRagflow(
        existing_datasets=[{"id": "ds-shared-1", "name": SHARED_DATASET_NAME}]
    )
    service = _make_service(db, ragflow)
    base = _create_base(db)  # 共享模式：remote_dataset_id 为空
    doc = _add_document(
        db, base["id"], name="a.txt", status="deleted",
        remote_document_id="remote-1", purge_after=_iso(-10),
    )

    assert await service.purge_expired_documents() == 1
    assert ragflow.delete_calls == [
        {"dataset_id": "ds-shared-1", "document_ids": ["remote-1"]}
    ]
    assert _remote_ref(db, doc["id"]) == ""  # 本地引用清空，防死链
    row = KnowledgeRepository(db).get_document(doc["id"])
    assert row["status"] == "deleted"  # 软删除记录保留（审计）


async def test_purge_skips_documents_within_retention(db):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow)
    base = _create_base(db)
    doc = _add_document(
        db, base["id"], name="a.txt", status="deleted",
        remote_document_id="remote-1", purge_after=_iso(10),
    )

    assert await service.purge_expired_documents() == 0
    assert ragflow.delete_calls == []
    assert _remote_ref(db, doc["id"]) == "remote-1"


async def test_purge_skips_active_documents_in_active_base(db):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow)
    base = _create_base(db)
    _add_document(
        db, base["id"], name="a.txt", status="ready", remote_document_id="remote-1"
    )

    assert await service.purge_expired_documents() == 0
    assert ragflow.delete_calls == []


# ---------------------------------------------------------------------------
# 删库场景与数据集路由
# ---------------------------------------------------------------------------
async def test_purge_covers_documents_of_deleted_base(db):
    """删库时文档未逐个软删除：按库的保留期统一清理其远端副本。"""
    ragflow = _FakeRagflow(
        existing_datasets=[{"id": "ds-shared-1", "name": SHARED_DATASET_NAME}]
    )
    service = _make_service(db, ragflow)
    base = _create_base(db, status="deleted", purge_after=_iso(-10))
    _add_document(
        db, base["id"], name="a.txt", status="ready", remote_document_id="remote-1"
    )

    assert await service.purge_expired_documents() == 1
    assert ragflow.delete_calls == [
        {"dataset_id": "ds-shared-1", "document_ids": ["remote-1"]}
    ]


async def test_purge_uses_legacy_dataset_of_base(db):
    """存量自有数据集的库：远端副本从其自有数据集删除，不触碰共享库。"""
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow)
    base = _create_base(
        db, status="deleted", purge_after=_iso(-10), remote_dataset_id="ds-legacy"
    )
    _add_document(
        db, base["id"], name="a.txt", status="ready", remote_document_id="remote-1"
    )

    assert await service.purge_expired_documents() == 1
    assert ragflow.delete_calls == [
        {"dataset_id": "ds-legacy", "document_ids": ["remote-1"]}
    ]
    assert ragflow.created_datasets == []


# ---------------------------------------------------------------------------
# 失败语义与批量上限
# ---------------------------------------------------------------------------
async def test_purge_tolerates_remote_already_missing(db):
    """远端已不存在（404）：视为已清理，仍清空本地引用（幂等）。"""
    ragflow = _FakeRagflow(delete_error=RagflowNotFoundError("gone"))
    service = _make_service(db, ragflow)
    base = _create_base(db)
    doc = _add_document(
        db, base["id"], name="a.txt", status="deleted",
        remote_document_id="remote-1", purge_after=_iso(-10),
    )

    assert await service.purge_expired_documents() == 1
    assert _remote_ref(db, doc["id"]) == ""


async def test_purge_keeps_reference_on_transient_failure(db):
    """瞬时失败：保留本地引用，留待下一轮重试。"""
    ragflow = _FakeRagflow(delete_error=RagflowError("boom"))
    service = _make_service(db, ragflow)
    base = _create_base(db)
    doc = _add_document(
        db, base["id"], name="a.txt", status="deleted",
        remote_document_id="remote-1", purge_after=_iso(-10),
    )

    assert await service.purge_expired_documents() == 0
    assert _remote_ref(db, doc["id"]) == "remote-1"


async def test_purge_respects_batch_limit(db):
    ragflow = _FakeRagflow(
        existing_datasets=[{"id": "ds-shared-1", "name": SHARED_DATASET_NAME}]
    )
    service = _make_service(db, ragflow)
    base = _create_base(db)
    kept = None
    for index in range(3):
        doc = _add_document(
            db, base["id"], name=f"a{index}.txt", status="deleted",
            remote_document_id=f"remote-{index}", purge_after=_iso(-10),
        )
        kept = doc

    assert await service.purge_expired_documents(limit=2) == 2
    assert len(ragflow.delete_calls) == 2
    # 未清到的文档保留引用，下轮继续
    assert _remote_ref(db, kept["id"]) == "remote-2"


# ---------------------------------------------------------------------------
# worker 接线
# ---------------------------------------------------------------------------
def _worker(ragflow: _FakeRagflow, db_provider) -> KnowledgePurgeWorker:
    return KnowledgePurgeWorker(
        config=KnowledgeConfig(enabled=True, base_url="http://ragflow", api_key="rk"),
        ragflow=ragflow,
        db_provider=db_provider,
    )


async def test_purge_worker_runs_service_and_reports_count(db):
    ragflow = _FakeRagflow(
        existing_datasets=[{"id": "ds-shared-1", "name": SHARED_DATASET_NAME}]
    )
    base = _create_base(db)
    _add_document(
        db, base["id"], name="a.txt", status="deleted",
        remote_document_id="remote-1", purge_after=_iso(-10),
    )

    assert await _worker(ragflow, lambda: db).purge_once() == 1
    assert len(ragflow.delete_calls) == 1


async def test_purge_worker_skips_when_db_unavailable():
    ragflow = _FakeRagflow()
    assert await _worker(ragflow, lambda: None).purge_once() == 0


async def test_purge_worker_start_stop_roundtrip(db):
    worker = _worker(_FakeRagflow(), lambda: db)
    worker.start()
    assert worker.is_running
    await worker.stop()
    assert not worker.is_running
