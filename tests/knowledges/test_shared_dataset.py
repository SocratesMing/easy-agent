"""共享数据集机制单元测试（创建纯本地化 / 个人标签 / 检索过滤 / 状态轮询）。

不依赖真实 Ragflow：用假客户端记录调用，临时 SQLite 主库 + 临时原文目录。
"""

import asyncio
import io

import pytest

from easy_agent.knowledges.api import KnowledgePrincipal
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.models import (
    Evidence,
    KnowledgeBaseVisibility,
    RetrieveResponse,
    SessionKnowledgeScopeResponse,
)
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.core.original_store import LocalOriginalStore
from easy_agent.knowledges.ragflow import RagflowError
from easy_agent.knowledges.service import (
    SHARED_DATASET_NAME,
    KnowledgeService,
    KnowledgeServiceError,
    reset_shared_dataset_cache,
)


class _FakeRagflow:
    """假 Ragflow 客户端：记录全部调用，按需返回既有数据集/文档。"""

    def __init__(self, existing_datasets=None):
        self.existing_datasets = list(existing_datasets or [])
        self.datasets_created: list[dict] = []
        self.dataset_updates: list[dict] = []
        self.listed_datasets_calls: list[dict] = []
        self.uploaded: list[dict] = []
        self.document_updates: list[dict] = []
        self.parsed: list[dict] = []
        self.retrieve_calls: list[dict] = []
        self.retrieve_result: dict = {"chunks": []}
        self.chunk_queries: list[dict] = []
        # document_id -> chunk 列表（list_chunks 时返回，顺序即文档顺序）
        self.chunks_by_document: dict[str, list[dict]] = {}
        self.document_queries: list[dict] = []
        # document_id -> 上游文档行（list_documents(document_id=...) 时返回）
        self.documents_by_id: dict[str, dict] = {}
        self._next_id = 0

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}-{self._next_id}"

    async def list_datasets(self, *, name=None, dataset_id=None, **kwargs):
        self.listed_datasets_calls.append(
            {"name": name, "dataset_id": dataset_id}
        )
        matched = [
            item
            for item in self.existing_datasets
            if (name is None or item.get("name") == name)
            and (dataset_id is None or item.get("id") == dataset_id)
        ]
        return {"datasets": matched}

    async def create_dataset(self, **kwargs):
        self.datasets_created.append(kwargs)
        return {"id": self._new_id("ds-shared")}

    async def update_dataset(self, dataset_id, **kwargs):
        self.dataset_updates.append({"dataset_id": dataset_id, **kwargs})

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
        return self.retrieve_result

    async def list_chunks(self, dataset_id, document_id, **kwargs):
        page_size = int(kwargs.get("page_size") or 30)
        if page_size > 100:
            raise RagflowError("page_size must be less than or equal to 100")
        page = int(kwargs.get("page") or 1)
        self.chunk_queries.append(
            {"dataset_id": dataset_id, "document_id": document_id, **kwargs}
        )
        chunks = list(self.chunks_by_document.get(document_id, []))
        start = (page - 1) * page_size
        return {"chunks": chunks[start : start + page_size], "total": len(chunks)}

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


def _make_service(
    db, ragflow: _FakeRagflow, tmp_path
) -> KnowledgeService:
    return KnowledgeService(
        KnowledgeRepository(db),
        ragflow,
        KnowledgeConfig(
            enabled=True,
            base_url="http://ragflow",
            api_key="rk",
        ),
        original_store=LocalOriginalStore(tmp_path / "originals"),
    )


def _make_service_with_dataset(
    db,
    ragflow: _FakeRagflow,
    tmp_path,
    *,
    dataset_id: str,
) -> KnowledgeService:
    return KnowledgeService(
        KnowledgeRepository(db),
        ragflow,
        KnowledgeConfig(
            enabled=True,
            base_url="http://ragflow",
            api_key="rk",
            shared_dataset_id=dataset_id,
        ),
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
    # 第一次 ensure：list_datasets 用于 find + create 后 _get_dataset 读模型 = 2 次；
    # 第二次 ensure：命中进程级缓存，不再调用 Ragflow
    assert len(ragflow.datasets_created) == 1


async def test_ensure_shared_dataset_reuses_existing_by_name(db, tmp_path):
    ragflow = _FakeRagflow(
        existing_datasets=[{"id": "ds-existing", "name": SHARED_DATASET_NAME}]
    )
    service = _make_service(db, ragflow, tmp_path)

    assert await service._ensure_shared_dataset() == "ds-existing"
    assert ragflow.datasets_created == []


async def test_configured_shared_dataset_id_is_used(db, tmp_path):
    """配置指定共享数据集 ID：校验存在后直接使用，不改库、不建库。"""
    ragflow = _FakeRagflow(
        existing_datasets=[
            {
                "id": "ds-designated",
                "name": "Easy",
                "embedding_model": "",
                "chunk_count": 0,
            }
        ]
    )
    service = KnowledgeService(
        KnowledgeRepository(db),
        ragflow,
        KnowledgeConfig(
            enabled=True,
            base_url="http://ragflow",
            api_key="rk",
            shared_dataset_id="ds-designated",
        ),
        original_store=LocalOriginalStore(tmp_path / "originals"),
    )

    assert await service._ensure_shared_dataset() == "ds-designated"
    assert ragflow.datasets_created == []
    assert ragflow.dataset_updates == []

    # 指定库不依赖进程缓存（复位后依旧指向它）
    reset_shared_dataset_cache()
    assert await service._ensure_shared_dataset() == "ds-designated"

    # 数据集路由同样落到指定库：共享库（remote_dataset_id 为空）→ ds-designated
    base = _create_base_row(db)
    assert await service._dataset_id_for(base) == "ds-designated"


async def test_configured_missing_dataset_raises_actionable_error(db, tmp_path):
    """配置指向不存在的共享数据集 ID：抛出带可操作提示的错误。"""
    ragflow = _FakeRagflow()
    service = _make_service_with_dataset(db, ragflow, tmp_path, dataset_id="ds-missing")

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await service._ensure_shared_dataset()

    assert exc_info.value.code == "KNOWLEDGE_SHARED_DATASET_NOT_FOUND"
    assert "RAGFLOW_BASE_URL" in exc_info.value.message
    assert "RAGFLOW_API_KEY" in exc_info.value.message


async def test_from_mapping_reads_shared_dataset_id(monkeypatch):
    """配置解析：环境变量优先于 YAML；未设置时回退 YAML / 空值。"""

    monkeypatch.delenv("RAGFLOW_SHARED_DATASET_ID", raising=False)
    config = KnowledgeConfig.from_mapping({"shared_dataset_id": "ds-yaml"})
    assert config.shared_dataset_id == "ds-yaml"

    monkeypatch.setenv("RAGFLOW_SHARED_DATASET_ID", "ds-env")
    # 环境变量显式设置时覆盖 YAML 字面值
    assert (
        KnowledgeConfig.from_mapping({"shared_dataset_id": "ds-yaml"}).shared_dataset_id
        == "ds-env"
    )
    assert KnowledgeConfig.from_mapping({}).shared_dataset_id == "ds-env"

    monkeypatch.delenv("RAGFLOW_SHARED_DATASET_ID")
    assert KnowledgeConfig.from_mapping({}).shared_dataset_id == ""


def test_from_mapping_ignores_legacy_embedding_key(monkeypatch):
    """旧配置里的 knowledge.embedding 不再生效，也不会导致加载失败。"""

    config = KnowledgeConfig.from_mapping({"embedding": {"model": "bge-yaml"}})
    assert not hasattr(config, "embedding")

    monkeypatch.setenv("RAGFLOW_EMBEDDING_MODEL", "bge-env")
    assert not hasattr(KnowledgeConfig.from_mapping({}), "embedding")


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
    # 个人库文档打 owner_user_id 标签，并统一带 base_id 元数据
    assert ragflow.document_updates[0]["dataset_id"] == shared_id
    assert ragflow.document_updates[0]["meta_fields"] == {
        "base_id": str(base["id"]),
        "owner_user_id": "u-1",
    }


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
    # 公共库不打个人标签，但仍写入 base_id 元数据（检索范围过滤用）
    assert ragflow.document_updates[0]["meta_fields"] == {"base_id": str(base["id"])}


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


async def test_retrieve_base_with_own_dataset_without_filter(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    repository = KnowledgeRepository(db)
    repository.update_base(base["id"], remote_dataset_id="ds-own")
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
    # 存量自有数据集库：沿用自有数据集，且不打标签过滤（旧文档无标签）
    assert call["dataset_ids"] == ["ds-own"]
    assert call["meta_fields"] is None
    # 未触发共享数据集查找
    assert ragflow.listed_datasets_calls == []


async def test_retrieve_adds_ten_percent_neighbor_overlap(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )
    ragflow.chunks_by_document["rd-1"] = [
        {"id": "c1", "content_with_weight": "A" * 100},
        {"id": "c2", "content_with_weight": "B" * 100},
        {"id": "c3", "content_with_weight": "C" * 100},
    ]
    ragflow.retrieve_result = {
        "chunks": [
            {
                "document_id": "rd-1",
                "chunk_id": "c2",
                "content_with_weight": "命中内容",
                "similarity": 0.9,
            }
        ]
    }

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-neighbor",
    )

    # 命中块前后各拼接相邻块 10%（按字符数）
    assert result.evidence[0].snippet == "A" * 10 + "\n命中内容\n" + "C" * 10
    # 同一文档只拉取一次 chunk 列表
    assert len(ragflow.chunk_queries) == 1


async def test_retrieve_expansion_paginates_chunks_over_100(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )
    ragflow.chunks_by_document["rd-1"] = [
        {"id": f"c{index}", "content_with_weight": f"内容{index}"}
        for index in range(120)
    ]
    ragflow.retrieve_result = {
        "chunks": [
            {
                "document_id": "rd-1",
                "chunk_id": "c100",
                "content_with_weight": "命中",
                "similarity": 0.9,
            }
        ]
    }

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-pagination",
    )

    # 跨页命中：必须翻到第 2 页才能取到 c101 的前 10%
    assert result.evidence[0].snippet == "9\n命中\n内"
    assert [query["page"] for query in ragflow.chunk_queries] == [1, 2]
    assert all(query["page_size"] == 100 for query in ragflow.chunk_queries)


async def test_retrieve_aggregation_question_appends_full_next_chunk(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )
    ragflow.chunks_by_document["rd-1"] = [
        {"id": "c1", "content_with_weight": "A" * 100},
        {"id": "c2", "content_with_weight": "命中内容"},
        {"id": "c3", "content_with_weight": "C" * 200},
    ]
    ragflow.retrieve_result = {
        "chunks": [
            {
                "document_id": "rd-1",
                "chunk_id": "c2",
                "content_with_weight": "命中内容",
                "similarity": 0.9,
            }
        ]
    }

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="本行持有5%以上股份的股东有几个",
        document_ids=None,
        top_n=8,
        request_id="req-aggregation",
    )

    # 计数类问题：完整带出下一个 chunk，避免跨页名单被截断
    assert result.evidence[0].snippet == "命中内容\n" + "C" * 200
    call = ragflow.retrieve_calls[0]
    assert call["page_size"] >= 20
    assert call["similarity_threshold"] == 0.4


async def test_retrieve_percentage_question_does_not_trigger_aggregation(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )

    await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="资本充足率18.72%说明什么",
        document_ids=None,
        top_n=5,
        request_id="req-percent",
    )

    call = ragflow.retrieve_calls[0]
    # 仅含百分比的分析题不再走聚合模式（不降阈值、不扩候选）
    assert call["similarity_threshold"] == 0.5
    # 宽召回：候选条数取 knowledge.retrieval.page_size（默认 30），不再等于 top_n
    assert call["page_size"] >= 5


async def test_retrieve_falls_back_when_primary_threshold_has_no_hits(db, tmp_path):
    """相关问题最高分卡在 0.5 以下时，降级到本地最低证据分再召回一次。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )
    calls: list[dict] = []

    async def retrieve(**kwargs):
        calls.append(kwargs)
        if kwargs["similarity_threshold"] == 0.5:
            return {"chunks": []}
        return {
            "chunks": [
                {
                    "document_id": "rd-1",
                    "chunk_id": "c1",
                    "content_with_weight": "降级命中内容",
                    "similarity": 0.45,
                }
            ]
        }

    ragflow.retrieve = retrieve

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="请分析8月原油进口表现",
        document_ids=None,
        top_n=5,
        request_id="req-threshold-fallback",
    )

    assert [call["similarity_threshold"] for call in calls] == [0.5, 0.4]
    assert result.evidence
    assert result.evidence[0].snippet == "降级命中内容"
    # 降级阈值命中属于弱证据，提示词需要标注「弱相关」
    assert result.evidence[0].metadata["weak"] is True


async def test_retrieve_aggregation_stops_at_next_section(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )
    ragflow.chunks_by_document["rd-1"] = [
        {"id": "c1", "content_with_weight": "3.2.3 股东情况\n1、甲公司\n2、乙公司"},
        {"id": "c2", "content_with_weight": "3、丙公司\n4、丁公司"},
        {"id": "c3", "content_with_weight": "3.2.4 下一节内容\n不应带入"},
        {"id": "c4", "content_with_weight": "更后面的内容"},
    ]
    ragflow.retrieve_result = {
        "chunks": [
            {
                "document_id": "rd-1",
                "chunk_id": "c1",
                "content_with_weight": "3.2.3 股东情况\n1、甲公司\n2、乙公司",
                "similarity": 0.9,
            }
        ]
    }

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="5%以上股东有几个",
        document_ids=None,
        top_n=5,
        request_id="req-section",
    )

    snippet = result.evidence[0].snippet
    assert "丙公司" in snippet and "丁公司" in snippet
    assert "下一节内容" not in snippet


async def test_retrieve_skips_neighbor_fetch_for_low_score(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )
    ragflow.retrieve_result = {
        "chunks": [
            {
                "document_id": "rd-1",
                "chunk_id": "c2",
                "content_with_weight": "弱相关",
                "similarity": 0.1,
            }
        ]
    }

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-low-score",
    )

    assert result.evidence == []
    assert ragflow.chunk_queries == []


async def test_dataset_id_for_prefers_own_dataset(db, tmp_path):
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


async def test_refresh_base_with_own_dataset_keeps_full_list_behavior(db, tmp_path):
    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    # 绑定自有数据集后必须用更新后的行（refresh 按传入的 base 字典路由）
    base = KnowledgeRepository(db).update_base(
        base["id"], remote_dataset_id="ds-own"
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


async def test_list_documents_does_not_wait_for_slow_upstream_refresh(db, tmp_path):
    """列表只读本地投影：单篇文档的上游刷新不能拖住整批文档的状态返回。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    busy = _add_document(
        db, str(base["id"]), name="busy.txt", status="processing",
        remote_document_id="rd-busy",
    )
    # 上游已就绪，但只有后台 worker 主动刷新时才应写回本地
    ragflow.documents_by_id["rd-busy"] = {
        "id": "rd-busy", "run": "DONE", "progress": 1.0,
    }

    response = await service.list_documents(
        base_id=str(base["id"]),
        principal=_principal(),
        page=1,
        page_size=50,
        folder_id=None,
        direct_only=False,
        status=None,
        query=None,
        request_id="req-list",
    )

    assert ragflow.document_queries == []
    assert response.items[0].id == busy["id"]
    assert response.items[0].status == "processing"


async def test_refresh_shared_base_updates_fast_document_before_slow_one(db, tmp_path):
    """单篇慢请求只拖住自己：同批其它文档的状态应先独立写回本地投影。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    fast = _add_document(
        db, str(base["id"]), name="fast.txt", status="processing",
        remote_document_id="rd-fast",
    )
    slow = _add_document(
        db, str(base["id"]), name="slow.txt", status="processing",
        remote_document_id="rd-slow",
    )
    ragflow.documents_by_id["rd-fast"] = {
        "id": "rd-fast", "run": "DONE", "progress": 1.0,
    }
    ragflow.documents_by_id["rd-slow"] = {
        "id": "rd-slow", "run": "DONE", "progress": 1.0,
    }
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    original_list_documents = ragflow.list_documents

    async def list_documents(dataset_id, **kwargs):
        if kwargs.get("document_id") == "rd-slow":
            slow_started.set()
            await release_slow.wait()
        return await original_list_documents(dataset_id, **kwargs)

    ragflow.list_documents = list_documents
    refresh_task = asyncio.create_task(service.refresh_base_documents(base))
    await asyncio.wait_for(slow_started.wait(), timeout=2)

    repository = KnowledgeRepository(db)
    for _ in range(100):
        if repository.get_document(fast["id"])["status"] == "ready":
            break
        await asyncio.sleep(0.01)
    # slow 文档仍被上游阻塞时，fast 文档已独立完成状态更新
    assert repository.get_document(fast["id"])["status"] == "ready"
    assert repository.get_document(slow["id"])["status"] == "processing"

    release_slow.set()
    await asyncio.wait_for(refresh_task, timeout=2)
    assert repository.get_document(slow["id"])["status"] == "ready"


# ---------------------------------------------------------------------------
# 检索增强：base_id 元数据范围 / 同文档去重 / 按库配额 / 重复上传
# ---------------------------------------------------------------------------
async def test_retrieve_uses_base_meta_when_backfilled(db, tmp_path):
    """已回填 base_id 的库按元数据过滤，不再传 document_ids（不受 500 篇上限）。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="team", owner_user_id="u-admin")
    KnowledgeRepository(db).update_base(base["id"], meta_backfilled=1)
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
        request_id="req-meta",
    )

    call = ragflow.retrieve_calls[0]
    assert call["meta_fields"] == {"base_id": str(base["id"])}
    assert "document_ids" not in call


async def test_retrieve_falls_back_to_document_ids_when_meta_scope_misscoped(db, tmp_path):
    """元数据过滤失效（返回其它库片段）时回退 document_ids 范围。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="team", owner_user_id="u-admin")
    KnowledgeRepository(db).update_base(base["id"], meta_backfilled=1)
    _add_document(
        db,
        str(base["id"]),
        name="b.txt",
        status="ready",
        remote_document_id="rd-2",
        created_by="u-admin",
    )
    calls: list[dict] = []

    async def retrieve(**kwargs):
        calls.append(kwargs)
        if "document_ids" not in kwargs:
            # 模拟上游忽略 meta_fields：返回了其它知识库的片段
            return {
                "chunks": [
                    {
                        "document_id": "rd-other",
                        "chunk_id": "c-other",
                        "content_with_weight": "别的库",
                        "similarity": 0.9,
                    }
                ]
            }
        return {
            "chunks": [
                {
                    "document_id": "rd-2",
                    "chunk_id": "c1",
                    "content_with_weight": "回退命中",
                    "similarity": 0.9,
                }
            ]
        }

    ragflow.retrieve = retrieve

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-admin", username="admin"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-meta-fallback",
    )

    assert "document_ids" not in calls[0]
    assert calls[1]["document_ids"] == ["rd-2"]
    assert result.evidence and result.evidence[0].snippet == "回退命中"


async def test_retrieve_dedupes_chunks_of_same_document(db, tmp_path):
    """同一文档多个命中块只保留最高分一条，避免挤占其它文档的证据名额。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    _add_document(
        db, str(base["id"]), name="a.txt", status="ready", remote_document_id="rd-1"
    )
    ragflow.retrieve_result = {
        "chunks": [
            {
                "document_id": "rd-1",
                "chunk_id": "c1",
                "content_with_weight": "高分段",
                "similarity": 0.9,
            },
            {
                "document_id": "rd-1",
                "chunk_id": "c2",
                "content_with_weight": "低分段",
                "similarity": 0.8,
            },
        ]
    }

    result = await service._retrieve_base_evidence(
        base_id=str(base["id"]),
        principal=_principal(user_id="u-1"),
        question="问题",
        document_ids=None,
        top_n=5,
        request_id="req-dedupe",
    )

    assert len(result.evidence) == 1
    assert result.evidence[0].snippet == "高分段"
    assert result.evidence[0].score == 0.9


async def test_retrieve_session_applies_per_base_quota(db, tmp_path, monkeypatch):
    """多库检索时每个库至少保留配额条数，剩余名额再按全局分数补齐。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base_a = _create_base_row(db, space_type="team", owner_user_id="u-admin")
    base_b = _create_base_row(db, space_type="team", owner_user_id="u-admin")

    async def get_scope(*, session_id, principal):
        return SessionKnowledgeScopeResponse(
            session_id=session_id,
            base_ids=[str(base_a["id"]), str(base_b["id"])],
        )

    async def fake_retrieve(
        *, base_id, principal, question, document_ids, top_n, request_id,
        metadata_filter=None,
    ):
        if base_id == str(base_a["id"]):
            evidence = [
                Evidence(
                    document_id=f"a{index}",
                    document_name=f"a{index}.txt",
                    snippet="片段",
                    score=0.9 - index * 0.01,
                )
                for index in range(5)
            ]
        else:
            evidence = [
                Evidence(
                    document_id=f"b{index}",
                    document_name=f"b{index}.txt",
                    snippet="片段",
                    score=0.5 - index * 0.01,
                )
                for index in range(3)
            ]
        return RetrieveResponse(evidence=evidence, request_id=request_id)

    monkeypatch.setattr(service, "get_session_scope", get_scope)
    monkeypatch.setattr(service, "_retrieve_base_evidence", fake_retrieve)

    result = await service.retrieve_session_evidence(
        session_id="session-1",
        principal=_principal(),
        question="问题",
        top_n=8,
        request_id="req-quota",
    )

    names = [item.document_name for item in result.evidence]
    assert len(names) == 8
    # 低分的 B 库也保住配额，不被高分的 A 库完全挤掉
    assert "b0.txt" in names and "b1.txt" in names


async def test_upload_rejects_duplicate_content_in_same_base(db, tmp_path):
    """同库重复内容直接拒绝上传，并提示已存在的文档名。"""

    ragflow = _FakeRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db, space_type="personal", owner_user_id="u-1")
    content = b"duplicate bytes"

    await service.upload_document(
        base_id=str(base["id"]),
        folder_id=None,
        filename="first.txt",
        content_type="text/plain",
        size_bytes=len(content),
        content=io.BytesIO(content),
        principal=_principal(user_id="u-1"),
        request_id="req-dup-1",
    )

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await service.upload_document(
            base_id=str(base["id"]),
            folder_id=None,
            filename="second.txt",
            content_type="text/plain",
            size_bytes=len(content),
            content=io.BytesIO(content),
            principal=_principal(user_id="u-1"),
            request_id="req-dup-2",
        )

    assert exc_info.value.code == "KNOWLEDGE_DUPLICATE_DOCUMENT"
    assert exc_info.value.status_code == 409
    assert "first.txt" in exc_info.value.message
