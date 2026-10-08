"""共享数据集机制单元测试（创建纯本地化 / 个人标签 / 检索过滤 / 状态轮询）。

不依赖真实 Ragflow：用假客户端记录调用，临时 SQLite 主库 + 临时原文目录。
"""

import io

import pytest

from easy_agent.knowledges.api import KnowledgePrincipal
from easy_agent.knowledges.config import KnowledgeConfig
from easy_agent.knowledges.models import KnowledgeBaseVisibility
from easy_agent.knowledges.repository import KnowledgeRepository
from easy_agent.knowledges.service import (
    SHARED_DATASET_NAME,
    KnowledgeService,
    LocalOriginalStore,
    reset_shared_dataset_cache,
)


class _FakeRagflow:
    """假 Ragflow 客户端：记录全部调用，按需返回既有数据集/文档。"""

    def __init__(self, existing_datasets=None):
        self.existing_datasets = list(existing_datasets or [])
        self.datasets_created: list[dict] = []
        self.listed_datasets_calls: list[dict] = []
        self.uploaded: list[dict] = []
        self.document_updates: list[dict] = []
        self.parsed: list[dict] = []
        self.retrieve_calls: list[dict] = []
        self.document_queries: list[dict] = []
        # document_id -> 上游文档行（list_documents(document_id=...) 时返回）
        self.documents_by_id: dict[str, dict] = {}
        self._next_id = 0

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}-{self._next_id}"

    async def list_datasets(self, *, name=None, **kwargs):
        self.listed_datasets_calls.append({"name": name})
        matched = [
            item
            for item in self.existing_datasets
            if name is None or item.get("name") == name
        ]
        return {"datasets": matched}

    async def create_dataset(self, **kwargs):
        self.datasets_created.append(kwargs)
        return {"id": self._new_id("ds-shared")}

    async def upload_documents(self, dataset_id, files):
        doc_id = self._new_id("remote-doc")
        self.uploaded.append(
            {"dataset_id": dataset_id, "names": [item[0] for item in files]}
        )
        return [{"id": doc_id}]

    async def update_document(self, dataset_id, document_id, **kwargs):
        self.document_updates.append(
            {"dataset_id": dataset_id, "document_id": document_id, **kwargs}
        )

    async def parse_documents(self, dataset_id, document_ids):
        self.parsed.append({"dataset_id": dataset_id, "document_ids": document_ids})

    async def retrieve(self, **kwargs):
        self.retrieve_calls.append(kwargs)
        return {"chunks": []}

    async def list_documents(self, dataset_id, **kwargs):
        self.document_queries.append({"dataset_id": dataset_id, **kwargs})
        document_id = kwargs.get("document_id")
        if document_id and document_id in self.documents_by_id:
            return {"docs": [self.documents_by_id[document_id]], "total": 1}
        return {"docs": [], "total": 0}


def _principal(
    user_id: str = "u-1", username: str = "alice", department_id: str | None = None
) -> KnowledgePrincipal:
    return KnowledgePrincipal(
        user_id=user_id, username=username, department_id=department_id
    )


def _make_service(db, ragflow: _FakeRagflow, tmp_path) -> KnowledgeService:
    return KnowledgeService(
        KnowledgeRepository(db),
        ragflow,
        KnowledgeConfig(enabled=True, base_url="http://ragflow", api_key="rk"),
        original_store=LocalOriginalStore(tmp_path / "originals"),
    )


def _create_base_row(
    db, *, space_type: str = "personal", owner_user_id: str = "u-1"
) -> dict:
    repository = KnowledgeRepository(db)
    base = repository.create_base(
        name=f"base-{space_type}-{owner_user_id}",
        description="",
        space_type=space_type,
        owner_user_id=owner_user_id,
        department_id=None,
        embedding_model="",
    )
    return repository.update_base(base["id"], status="active")


def _add_document(
    db,
    base_id: str,
    *,
    name: str,
    status: str,
    remote_document_id: str | None = None,
    created_by: str = "u-1",
) -> dict:
    repository = KnowledgeRepository(db)
    document = repository.create_document(
        base_id=base_id,
        folder_id=None,
        name=name,
        content_type="text/plain",
        size_bytes=16,
        created_by=created_by,
        status=status,
    )
    if remote_document_id:
        document = repository.update_document(
            document["id"], remote_document_id=remote_document_id
        )
    return document


@pytest.fixture(autouse=True)
def _isolate_shared_dataset_cache():
    """共享数据集 ID 是进程级缓存，必须在每个用例前后复位。"""
    reset_shared_dataset_cache()
    yield
    reset_shared_dataset_cache()


# ---------------------------------------------------------------------------
# 共享数据集 find-or-create
# ---------------------------------------------------------------------------
async def test_ensure_shared_dataset_creates_when_missing_and_caches(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)

    shared_id = await service._ensure_shared_dataset()
    assert shared_id.startswith("ds-shared-")
    assert len(ragflow.datasets_created) == 1
    assert ragflow.datasets_created[0]["name"] == SHARED_DATASET_NAME

    # 进程级缓存：新的 service 实例不重复查找/创建
    another = _make_service(db, ragflow, tmp_path)
    assert await another._ensure_shared_dataset() == shared_id
    assert len(ragflow.listed_datasets_calls) == 1
    assert len(ragflow.datasets_created) == 1


async def test_ensure_shared_dataset_reuses_existing_by_name(db, tmp_path):
    ragflow = _FakeRagflow(
        existing_datasets=[{"id": "ds-existing", "name": SHARED_DATASET_NAME}]
    )
    service = _make_service(db, ragflow, tmp_path)

    assert await service._ensure_shared_dataset() == "ds-existing"
    assert ragflow.datasets_created == []


async def test_find_shared_dataset_does_not_use_name_filter(db, tmp_path):
    """回归：定制版 Ragflow 的 name 过滤对不存在的名称也报 code=102
    权限错误，查找必须全量列表后本地匹配，不得依赖 name 查询参数。"""
    ragflow = _FakeRagflow(
        existing_datasets=[
            {"id": "ds-other", "name": "别的库"},
            {"id": "ds-existing", "name": SHARED_DATASET_NAME},
        ]
    )
    service = _make_service(db, ragflow, tmp_path)

    assert await service._find_shared_dataset() == "ds-existing"
    assert ragflow.listed_datasets_calls, "应发起列表查询"
    assert all(call["name"] is None for call in ragflow.listed_datasets_calls)


# ---------------------------------------------------------------------------
# 创建知识库：纯本地化
# ---------------------------------------------------------------------------
async def test_create_base_is_purely_local(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)

    summary = await service.create_base(
        principal=_principal(),
        name="我的个人库",
        description="",
        visibility=KnowledgeBaseVisibility.PERSONAL,
        request_id="req-1",
    )

    # 不触碰 Ragflow：既不建数据集也不查询
    assert ragflow.datasets_created == []
    assert ragflow.listed_datasets_calls == []

    repository = KnowledgeRepository(db)
    base = repository.get_base(summary.id)
    assert base["status"] == "active"
    # remote_dataset_id 为空 → 共享数据集模式
    assert not str(base["remote_dataset_id"] or "").strip()


# ---------------------------------------------------------------------------
# 上传：路由到共享数据集 + 个人库打标签
# ---------------------------------------------------------------------------
async def test_upload_personal_base_routes_to_shared_and_tags_owner(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")

    await service.upload_document(
        base_id=str(base["id"]),
        folder_id=None,
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=len(b"hello shared dataset"),
        content=io.BytesIO(b"hello shared dataset"),
        principal=_principal(user_id="u-1"),
        request_id="req-2",
    )

    # 上传与解析均落在共享数据集
    shared_id = await service._ensure_shared_dataset()
    assert ragflow.uploaded[0]["dataset_id"] == shared_id
    assert ragflow.parsed[0]["dataset_id"] == shared_id
    assert ragflow.parsed[0]["document_ids"] == [
        ragflow.document_updates[0]["document_id"]
    ]
    # 个人库文档打 owner_user_id 标签
    assert ragflow.document_updates[0]["dataset_id"] == shared_id
    assert ragflow.document_updates[0]["meta_fields"] == {"owner_user_id": "u-1"}


async def test_upload_public_shared_base_has_no_tag(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    # 公共库（team）直接落库，owner 为 admin
    base = _create_base_row(db, space_type="team", owner_user_id="u-admin")

    await service.upload_document(
        base_id=str(base["id"]),
        folder_id=None,
        filename="report.txt",
        content_type="text/plain",
        size_bytes=len(b"public content"),
        content=io.BytesIO(b"public content"),
        principal=_principal(user_id="u-admin", username="admin"),
        request_id="req-3",
    )

    shared_id = await service._ensure_shared_dataset()
    assert ragflow.uploaded[0]["dataset_id"] == shared_id
    # 公共库不打个人标签
    assert ragflow.document_updates[0]["meta_fields"] is None


# ---------------------------------------------------------------------------
# 检索：数据集路由 + 个人标签过滤
# ---------------------------------------------------------------------------
async def test_retrieve_personal_shared_base_filters_by_owner(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )

    await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-4",
    )

    shared_id = await service._ensure_shared_dataset()
    call = ragflow.retrieve_calls[0]
    assert call["dataset_ids"] == [shared_id]
    assert call["document_ids"] == ["rd-1"]
    # 个人库共享模式：检索携带个人标签过滤
    assert call["meta_fields"] == {"owner_user_id": "u-1"}


async def test_retrieve_public_shared_base_has_no_filter(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="team", owner_user_id="u-admin")
    _add_document(
        db,
        str(base["id"]),
        name="b.txt",
        status="ready",
        remote_document_id="rd-2",
        created_by="u-admin",
    )

    await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-admin", username="admin"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-5",
    )

    assert ragflow.retrieve_calls[0]["meta_fields"] is None


async def test_retrieve_legacy_base_uses_own_dataset_without_filter(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    repository = KnowledgeRepository(db)
    repository.update_base(base["id"], remote_dataset_id="ds-legacy")
    _add_document(
        db, str(base["id"]), name="c.txt", status="ready", remote_document_id="rd-3"
    )

    await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-6",
    )

    call = ragflow.retrieve_calls[0]
    # 存量自有数据集库：沿用旧数据集，且不打标签过滤（旧文档无标签）
    assert call["dataset_ids"] == ["ds-legacy"]
    assert call["meta_fields"] is None
    # 未触发共享数据集查找
    assert ragflow.listed_datasets_calls == []


async def test_dataset_id_for_prefers_legacy_dataset(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)

    assert await service._dataset_id_for({"remote_dataset_id": "ds-old"}) == "ds-old"
    assert ragflow.listed_datasets_calls == []


# ---------------------------------------------------------------------------
# 共享库状态刷新：逐文档轮询，稳态零调用
# ---------------------------------------------------------------------------
async def test_refresh_shared_base_polls_only_active_documents(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    # ready 文档不轮询；processing 文档逐个查；无远端 ID 的 pending 文档跳过
    _add_document(
        db, str(base["id"]), name="done.txt", status="ready",
        remote_document_id="rd-done",
    )
    busy = _add_document(
        db, str(base["id"]), name="busy.txt", status="processing",
        remote_document_id="rd-busy",
    )
    _add_document(db, str(base["id"]), name="wait.txt", status="pending")
    ragflow.documents_by_id["rd-busy"] = {
        "id": "rd-busy", "run": "DONE", "progress": 1.0,
    }

    await service.refresh_base_documents(base)

    queried = [query.get("document_id") for query in ragflow.document_queries]
    assert queried == ["rd-busy"]
    # 上游 DONE → 本地投影更新为 ready
    assert KnowledgeRepository(db).get_document(busy["id"])["status"] == "ready"

    # 稳态（全部 ready）：新实例再刷新一次，零 API 调用
    steady = _make_service(db, ragflow, tmp_path)
    await steady.refresh_base_documents(base)
    assert len(ragflow.document_queries) == 1


async def test_refresh_legacy_base_keeps_full_list_behavior(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    # 绑定自有数据集后必须用更新后的行（refresh 按传入的 base 字典路由）
    base = KnowledgeRepository(db).update_base(
        base["id"], remote_dataset_id="ds-legacy"
    )
    _add_document(
        db, str(base["id"]), name="x.txt", status="ready",
        remote_document_id="rd-x",
    )

    await service.refresh_base_documents(base)

    # 存量库走全量列表（page/page_size），不做逐文档查询
    assert len(ragflow.document_queries) == 1
    assert ragflow.document_queries[0].get("document_id") is None
    assert ragflow.document_queries[0].get("page") == 1
