"""download_document 本地原文缺失时的远端回退兜底单测。

场景：original 记录 status=available 但磁盘文件丢失（容器重建/目录清理），
此时若存在 remote_document_id 应回退 RAGFlow 下载；无远端副本则维持 503。
"""

import io
import shutil
from types import SimpleNamespace

import pytest

from easy_agent.knowledges.api import KnowledgePrincipal
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.service import (
    KnowledgeService,
    KnowledgeServiceError,
    LocalOriginalStore,
    reset_shared_dataset_cache,
)


class _FakeRagflow:
    """假 Ragflow：上传链路 + download_document。"""

    def __init__(self, existing_datasets=None):
        self.existing_datasets = list(existing_datasets or [])
        self.uploaded: list[dict] = []
        self.downloaded: list[dict] = []
        self._next_id = 0

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}-{self._next_id}"

    async def list_datasets(self, *, name=None, **kwargs):
        return {"datasets": list(self.existing_datasets)}

    async def create_dataset(self, **kwargs):
        return {"id": self._new_id("ds-shared")}

    async def upload_documents(self, dataset_id, files):
        self.uploaded.append({"dataset_id": dataset_id, "names": [i[0] for i in files]})
        return [{"id": self._new_id("remote-doc")}]

    async def update_document(self, dataset_id, document_id, **kwargs):
        return None

    async def parse_documents(self, dataset_id, document_ids):
        return None

    async def list_documents(self, dataset_id, **kwargs):
        return {"docs": [], "total": 0}

    async def download_document(self, dataset_id, document_id):
        self.downloaded.append({"dataset_id": dataset_id, "document_id": document_id})
        return SimpleNamespace(
            content=b"remote fallback content", content_type="text/plain"
        )


def _principal(user_id: str = "u-1") -> KnowledgePrincipal:
    return KnowledgePrincipal(user_id=user_id, username="alice", department_id=None)


def _make_service(db, ragflow: _FakeRagflow, tmp_path) -> KnowledgeService:
    return KnowledgeService(
        KnowledgeRepository(db),
        ragflow,
        KnowledgeConfig(enabled=True, base_url="http://ragflow", api_key="rk"),
        original_store=LocalOriginalStore(tmp_path / "originals"),
    )


def _create_base_row(db, *, owner_user_id: str = "u-1") -> dict:
    repository = KnowledgeRepository(db)
    base = repository.create_base(
        name=f"base-{owner_user_id}",
        description="",
        space_type="personal",
        owner_user_id=owner_user_id,
        department_id=None,
        embedding_model="",
    )
    return repository.update_base(base["id"], status="active")


async def _upload_document(service, base_id: str, filename: str = "a.txt"):
    content = f"content of {filename}".encode()
    return await service.upload_document(
        base_id=base_id,
        folder_id=None,
        filename=filename,
        content_type="text/plain",
        size_bytes=len(content),
        content=io.BytesIO(content),
        principal=_principal(),
        request_id=f"req-{filename}",
        relative_path=None,
    )


@pytest.fixture(autouse=True)
def _isolate_shared_dataset_cache():
    reset_shared_dataset_cache()
    yield
    reset_shared_dataset_cache()


async def test_download_reads_local_when_file_present(db, tmp_path):
    """本地原文存在时优先读本地，不打远端。"""
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)
    await _upload_document(service, str(base["id"]))

    repository = KnowledgeRepository(db)
    doc = repository.list_documents(str(base["id"]))[0]

    _, binary = await service.download_document(
        document_id=str(doc["id"]), principal=_principal(), request_id="req-dl"
    )
    assert b"".join(binary.iterator_factory()) == b"content of a.txt"
    assert ragflow.downloaded == []


async def test_download_falls_back_to_remote_when_local_file_missing(db, tmp_path):
    """original 记录 available 但磁盘文件丢失 → 回退 RAGFlow 下载。"""
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)
    await _upload_document(service, str(base["id"]))

    repository = KnowledgeRepository(db)
    doc = repository.list_documents(str(base["id"]))[0]
    assert doc["remote_document_id"]

    # 模拟容器重建/目录清理导致的本地原文丢失
    shutil.rmtree(tmp_path / "originals")

    document, binary = await service.download_document(
        document_id=str(doc["id"]), principal=_principal(), request_id="req-dl"
    )
    assert document["id"] == doc["id"]
    assert b"".join(binary.iterator_factory()) == b"remote fallback content"
    assert [d["document_id"] for d in ragflow.downloaded] == [
        str(doc["remote_document_id"])
    ]


async def test_download_raises_503_when_local_missing_and_no_remote(db, tmp_path):
    """本地文件丢失且无远端副本 → 维持 503 原文暂不可用。"""
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)
    await _upload_document(service, str(base["id"]))

    repository = KnowledgeRepository(db)
    doc = repository.list_documents(str(base["id"]))[0]
    repository.update_document(str(doc["id"]), remote_document_id=None)
    shutil.rmtree(tmp_path / "originals")

    with pytest.raises(KnowledgeServiceError) as excinfo:
        await service.download_document(
            document_id=str(doc["id"]), principal=_principal(), request_id="req-dl"
        )
    assert excinfo.value.code == "ORIGINAL_STORAGE_NOT_AVAILABLE"
    assert excinfo.value.status_code == 503
    assert ragflow.downloaded == []
