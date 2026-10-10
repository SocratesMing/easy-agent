"""文件夹上传保留层级关系的单元测试（relative_path → 逐级 find-or-create）。

不依赖真实 Ragflow：假客户端记录调用，临时 SQLite 主库 + 临时原文目录。
"""

import io

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
    """假 Ragflow 客户端：仅覆盖上传链路用到的方法。"""

    def __init__(self, existing_datasets=None):
        self.existing_datasets = list(existing_datasets or [])
        self.uploaded: list[dict] = []
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


async def _upload(
    service, base_id: str, *, filename: str,
    relative_path: str | None = None, folder_id: str | None = None,
):
    content = f"content of {filename}".encode()
    return await service.upload_document(
        base_id=base_id,
        folder_id=folder_id,
        filename=filename,
        content_type="text/plain",
        size_bytes=len(content),
        content=io.BytesIO(content),
        principal=_principal(),
        request_id=f"req-{filename}",
        relative_path=relative_path,
    )


@pytest.fixture(autouse=True)
def _isolate_shared_dataset_cache():
    reset_shared_dataset_cache()
    yield
    reset_shared_dataset_cache()


# ---------------------------------------------------------------------------
# 层级创建 / 复用 / 起点
# ---------------------------------------------------------------------------
async def test_upload_with_relative_path_creates_nested_folders(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    await _upload(
        service, str(base["id"]),
        filename="q1.txt", relative_path="资料/报告/q1.txt",
    )

    repository = KnowledgeRepository(db)
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}
    assert set(folders) == {"资料", "报告"}
    assert folders["资料"]["parent_id"] is None
    assert folders["报告"]["parent_id"] == folders["资料"]["id"]
    docs = repository.list_documents(str(base["id"]))
    assert [d["name"] for d in docs] == ["q1.txt"]
    assert docs[0]["folder_id"] == folders["报告"]["id"]


async def test_upload_relative_path_reuses_existing_folders(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    await _upload(service, str(base["id"]), filename="a.txt", relative_path="资料/报告/a.txt")
    await _upload(service, str(base["id"]), filename="b.txt", relative_path="资料/报告/b.txt")

    repository = KnowledgeRepository(db)
    folders = repository.list_folders(str(base["id"]))
    assert len(folders) == 2  # 重复上传不产生重复目录
    docs = {d["name"]: d for d in repository.list_documents(str(base["id"]))}
    assert set(docs) == {"a.txt", "b.txt"}
    assert docs["a.txt"]["folder_id"] == docs["b.txt"]["folder_id"]


async def test_upload_relative_path_starts_under_selected_folder(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    repository = KnowledgeRepository(db)
    target = repository.create_folder(
        base_id=str(base["id"]), name="目标", parent_id=None
    )

    await _upload(
        service, str(base["id"]),
        filename="x.txt", relative_path="资料/x.txt", folder_id=str(target["id"]),
    )

    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}
    assert folders["资料"]["parent_id"] == str(target["id"])
    docs = repository.list_documents(str(base["id"]))
    assert docs[0]["folder_id"] == folders["资料"]["id"]


# ---------------------------------------------------------------------------
# 校验与边界
# ---------------------------------------------------------------------------
async def test_upload_relative_path_rejects_traversal(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await _upload(
            service, str(base["id"]),
            filename="x.txt", relative_path="../evil/x.txt",
        )
    assert exc_info.value.code == "KNOWLEDGE_INVALID_UPLOAD_PATH"
    assert KnowledgeRepository(db).list_folders(str(base["id"])) == []
    assert KnowledgeRepository(db).count_documents(str(base["id"])) == 0


async def test_upload_relative_path_enforces_depth_limit(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await _upload(
            service, str(base["id"]),
            filename="f.txt", relative_path="a/b/c/d/e/f/f.txt",
        )
    assert exc_info.value.code == "KNOWLEDGE_FOLDER_DEPTH_LIMIT"
    # 前 5 层已创建，第 6 层被拒；文档未创建
    repository = KnowledgeRepository(db)
    assert len(repository.list_folders(str(base["id"]))) == 5
    assert repository.count_documents(str(base["id"])) == 0


async def test_upload_relative_path_rejects_overlong_segment(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await _upload(
            service, str(base["id"]),
            filename="x.txt", relative_path=f"{'x' * 128}/x.txt",
        )
    assert exc_info.value.code == "KNOWLEDGE_INVALID_UPLOAD_PATH"


async def test_upload_filename_only_relative_path_keeps_folder(db, tmp_path):
    """relative_path 只有文件名（无目录段）时行为与不传一致。"""
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    await _upload(service, str(base["id"]), filename="x.txt", relative_path="x.txt")

    repository = KnowledgeRepository(db)
    assert repository.list_folders(str(base["id"])) == []
    docs = repository.list_documents(str(base["id"]))
    assert docs[0]["folder_id"] is None


async def test_upload_without_relative_path_unchanged(db, tmp_path):
    """回归：不传 relative_path 的普通上传不建目录、落当前 folder_id。"""
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    repository = KnowledgeRepository(db)
    target = repository.create_folder(
        base_id=str(base["id"]), name="目标", parent_id=None
    )

    await _upload(
        service, str(base["id"]), filename="x.txt", folder_id=str(target["id"])
    )

    assert len(repository.list_folders(str(base["id"]))) == 1
    docs = repository.list_documents(str(base["id"]))
    assert docs[0]["folder_id"] == str(target["id"])


# ---------------------------------------------------------------------------
# ensure_folders：空目录补齐（拖拽整棵目录树时使用）
# ---------------------------------------------------------------------------
async def test_ensure_folders_creates_tree_including_empty_dirs(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    result = await service.ensure_folders(
        base_id=str(base["id"]),
        folder_id=None,
        paths=["资料/报告", "资料/空目录"],
        principal=_principal(),
    )
    assert result.ensured == 2

    repository = KnowledgeRepository(db)
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}
    assert set(folders) == {"资料", "报告", "空目录"}
    assert folders["报告"]["parent_id"] == folders["资料"]["id"]
    assert folders["空目录"]["parent_id"] == folders["资料"]["id"]

    # 幂等：重复调用不产生重复目录
    again = await service.ensure_folders(
        base_id=str(base["id"]),
        folder_id=None,
        paths=["资料/报告", "资料/空目录"],
        principal=_principal(),
    )
    assert again.ensured == 2
    assert len(KnowledgeRepository(db).list_folders(str(base["id"]))) == 3


async def test_ensure_folders_rejects_traversal_and_depth(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await service.ensure_folders(
            base_id=str(base["id"]),
            folder_id=None,
            paths=["../evil"],
            principal=_principal(),
        )
    assert exc_info.value.code == "KNOWLEDGE_INVALID_UPLOAD_PATH"

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await service.ensure_folders(
            base_id=str(base["id"]),
            folder_id=None,
            paths=["a/b/c/d/e/f"],
            principal=_principal(),
        )
    assert exc_info.value.code == "KNOWLEDGE_FOLDER_DEPTH_LIMIT"


# ---------------------------------------------------------------------------
# 端点级：表单字段绑定（multipart relative_path / ensure JSON）
# ---------------------------------------------------------------------------
def _endpoint_client(service, principal):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from easy_agent.knowledges.api import _service, get_knowledge_principal, router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_knowledge_principal] = lambda: principal
    app.dependency_overrides[_service] = lambda: service
    return TestClient(app)


def test_upload_endpoint_binds_relative_path_form_field(db, tmp_path):
    """端到端验证 multipart 表单的 relative_path 正确绑定并建目录。

    回归：服务层单测直接调 service，若端点 Form 声明有误（字段不绑定），
    层级会静默丢失——本用例通过真实路由堵住该缺口。
    """
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    client = _endpoint_client(service, _principal())

    response = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/documents",
        files={"file": ("q1.txt", b"endpoint content", "text/plain")},
        data={"folder_id": "", "relative_path": "资料/报告/q1.txt"},
    )
    assert response.status_code == 202
    document = response.json()["document"]
    assert document["name"] == "q1.txt"

    repository = KnowledgeRepository(db)
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}
    assert set(folders) == {"资料", "报告"}
    assert document["folder_id"] == folders["报告"]["id"]


def test_ensure_folders_endpoint_creates_empty_dirs(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    client = _endpoint_client(service, _principal())

    response = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/folders/ensure",
        json={"folder_id": None, "paths": ["资料/报告", "资料/空目录"]},
    )
    assert response.status_code == 200
    assert response.json()["ensured"] == 2
    names = {
        f["name"]
        for f in KnowledgeRepository(db).list_folders(str(base["id"]))
    }
    assert names == {"资料", "报告", "空目录"}
