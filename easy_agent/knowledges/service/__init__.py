"""知识库核心业务服务。

上传/删除全部同步内联；原文存储为本地磁盘存储（``LocalOriginalStore``）。
文档状态链：上传成功只落 ``pending``；解析轮询在观察到上游 RUNNING 后写入
``processing``（并开始超时计时），上游终态或本地超时落 ``ready/failed``；
暂停/恢复/重试都会清空旧计时后重新排队。
删除为软删除：本地保留记录与原文，远端（Ragflow）副本在删除时立即同步
删除；清理 worker（KnowledgePurgeWorker）仅兜底同步失败与历史遗留副本。

检索走 Ragflow 标准检索接口（retrieve，POST /api/v1/retrieval）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.models import (
    KnowledgeBaseVisibility,
    OperationListResponse,
    PageInfo,
)
from easy_agent.knowledges.core.operations_repository import KnowledgeOperationsRepository
from easy_agent.knowledges.core.original_store import LocalOriginalStore
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.service.contracts import (
    KnowledgeDocumentBinary,
    KnowledgeServiceError,
)
from easy_agent.knowledges.service.bases import BaseMixin
from easy_agent.knowledges.service.document_status import (
    DocumentStatusMixin,
    upstream_document_running,
)
from easy_agent.knowledges.service.documents import DocumentMixin
from easy_agent.knowledges.service.folders import FolderMixin
from easy_agent.knowledges.service.permissions import PermissionMixin
from easy_agent.knowledges.service.retrieval import (
    AGGREGATION_SNIPPET_LIMIT,
    RetrievalMixin,
)
from easy_agent.knowledges.service.sessions import SessionScopeMixin
from easy_agent.knowledges.service.summaries import SummaryMixin
from easy_agent.knowledges.service.uploads import (
    CHUNK_METHOD,
    DATASET_PERMISSION,
    PARSER_CONFIG,
    UploadMixin,
)
from easy_agent.knowledges.ragflow import RagflowClient, RagflowError, RagflowNotFoundError

if TYPE_CHECKING:  # 仅供类型注解（api 与 service 相互引用，运行时无循环导入）
    from easy_agent.knowledges.core.models import KnowledgePrincipal

logger = logging.getLogger(__name__)

# 远端删除按批次调用，避免单次请求携带过多 document_id
REMOTE_DELETE_BATCH = 100


# 检索阈值 / 权重 / 候选数 / rerank 已迁移到 knowledge.retrieval
# （KnowledgeConfig.retrieval）；这里只保留与 chunk 扩写相关的固定参数。
# 上传安全校验参数（YAML 不再配置，代码内固定）

# 共享数据集：所有走共享模式的个人/公共知识库共用同一个 Ragflow 数据集。
# 个人库文档通过 meta_fields={"owner_user_id": ...} 打个人标签，检索时
# 携带同名过滤条件实现个人隔离（与 document_ids 范围约束形成双重隔离）。
SHARED_DATASET_NAME = "EA_SHARED"
# 进程级缓存（KnowledgeService 每请求实例化，共享数据集 ID 必须跨实例复用）
_shared_dataset_id: str | None = None
_shared_dataset_lock: asyncio.Lock | None = None
# 进程级缓存：共享数据集绑定的实际 embedding 模型。由 RAGFlow 数据集直接
# 解析得到，不再从配置显式指定。空字符串极端情况表示上游数据集尚未绑定模型。
_effective_embedding_model: str | None = None


def _get_shared_dataset_lock() -> asyncio.Lock:
    """惰性创建模块级锁（避免 import 时绑定事件循环的兼容问题）。"""
    global _shared_dataset_lock
    if _shared_dataset_lock is None:
        _shared_dataset_lock = asyncio.Lock()
    return _shared_dataset_lock


def reset_shared_dataset_cache() -> None:
    """清空共享数据集 ID / embedding 模型进程级缓存（测试用）。"""
    global _shared_dataset_id, _effective_embedding_model
    _shared_dataset_id = None
    _effective_embedding_model = None


# ---------------------------------------------------------------------------
# 领域辅助
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------
class KnowledgeService(
    BaseMixin,
    DocumentMixin,
    DocumentStatusMixin,
    FolderMixin,
    PermissionMixin,
    RetrievalMixin,
    SessionScopeMixin,
    SummaryMixin,
    UploadMixin,
):
    """知识模块核心服务：组合本地仓储投影与 Ragflow 上游操作。"""

    def __init__(
        self,
        repository: KnowledgeRepository,
        ragflow: RagflowClient,
        config: KnowledgeConfig,
        *,
        original_store: LocalOriginalStore | None = None,
    ):
        self.repository = repository
        self.ragflow = ragflow
        self.config = config
        self.operations_repository = KnowledgeOperationsRepository(repository.db)
        self.original_store = (
            original_store if original_store is not None else LocalOriginalStore()
        )

    async def _repo(self, method, *args, **kwargs):
        """将同步仓储调用放入线程池执行，避免阻塞事件循环。"""

        return await run_in_threadpool(method, *args, **kwargs)

    @staticmethod
    def _upstream_error(exc: RagflowError) -> KnowledgeServiceError:
        """将 Ragflow 上游异常统一转换为服务层业务错误（可重试→503）。"""

        status_code = 503 if exc.retryable else 502
        logger.warning("知识底座上游错误: %s", exc)
        prefix = "知识底座暂时不可用" if exc.retryable else "知识底座请求失败"
        return KnowledgeServiceError(
            "KNOWLEDGE_UPSTREAM_UNAVAILABLE"
            if exc.retryable
            else "KNOWLEDGE_UPSTREAM_ERROR",
            f"{prefix}: {exc}",
            status_code=status_code,
            retryable=exc.retryable,
        )

    async def _ensure_shared_dataset(self) -> str:
        """返回共享数据集 ID，并顺带解析出实际生效的 embedding 模型。

        规则（模型一律从上游 RAGFlow 数据集解析，不再通过配置显式指定）：

        1. 配了 ``RAGFLOW_SHARED_DATASET_ID`` → 校验存在，直接用其
           ``embedding_model``；
        2. 未配置 → 查找/创建 ``EA_SHARED`` 共享数据集，从拿到的
           数据集读取 ``embedding_model``；创建时不指定模型，让 RAGFlow
           用租户默认（数据集创建好后自然会带上模型）。

        模型缓存于模块级 ``_effective_embedding_model``，同一进程内
        后续调用不再重复查数据集。
        """
        global _shared_dataset_id, _effective_embedding_model

        # 1) 显式指定数据集 ID：直接用
        configured = self.config.shared_dataset_id.strip()
        if configured:
            if _shared_dataset_id == configured and _effective_embedding_model is not None:
                return configured
            dataset = await self._get_dataset(configured)
            if dataset is None:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_SHARED_DATASET_NOT_FOUND",
                    f"共享数据集 {configured} 不存在或当前 API Key 无权访问；"
                    "请确认 RAGFLOW_BASE_URL / RAGFLOW_API_KEY / "
                    "RAGFLOW_SHARED_DATASET_ID 指向同一套 RAGFlow",
                    status_code=409,
                )
            _shared_dataset_id = configured
            _effective_embedding_model = str(
                dataset.get("embedding_model") or ""
            ).strip()
            logger.info(
                "共享数据集就绪（显式指定） | id=%s model=%s",
                configured,
                _effective_embedding_model,
            )
            return configured

        # 2) 未指定：按名称查找 / 创建 EA_SHARED（进程级缓存）
        if _shared_dataset_id is not None and _effective_embedding_model is not None:
            return _shared_dataset_id
        async with _get_shared_dataset_lock():
            if _shared_dataset_id is not None and _effective_embedding_model is not None:
                return _shared_dataset_id

            found = await self._find_shared_dataset()
            if found is None:
                _shared_dataset_id = await self._create_shared_dataset()
            else:
                _shared_dataset_id = found

            # 从最终确定的数据集读模型
            dataset = await self._get_dataset(_shared_dataset_id)
            _effective_embedding_model = str(
                (dataset or {}).get("embedding_model") or ""
            ).strip()
            logger.info(
                "共享数据集就绪 | id=%s model=%s",
                _shared_dataset_id,
                _effective_embedding_model,
            )
            return _shared_dataset_id

    async def _create_shared_dataset(self) -> str:
        """创建共享数据集：不指定 embedding_model，让 RAGFlow 用租户默认。"""
        try:
            remote = await self.ragflow.create_dataset(
                name=SHARED_DATASET_NAME,
                description="EasyAgent 共享知识库（个人/公共库共用）",
                # 不指定模型，RAGFlow 会用租户默认
                permission=DATASET_PERMISSION,
                chunk_method=CHUNK_METHOD,
                parser_config=PARSER_CONFIG,
            )
            return str(remote["id"])
        except RagflowError:
            # 并发竞态：其他进程/实例刚创建了同名数据集 → 重查复用
            found = await self._find_shared_dataset(name=SHARED_DATASET_NAME)
            if found is None:
                raise
            return found

    async def _get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        """按 ID 查询上游数据集详情；不存在返回 None。"""

        try:
            result = await self.ragflow.list_datasets(
                dataset_id=dataset_id, page=1, page_size=1
            )
        except RagflowError as exc:
            raise self._upstream_error(exc) from exc
        datasets = result.get("datasets") if isinstance(result, dict) else None
        for item in datasets or []:
            if isinstance(item, dict) and str(item.get("id")) == dataset_id:
                return item
        return None

    async def _find_shared_dataset(
        self, *, name: str = SHARED_DATASET_NAME
    ) -> str | None:
        """分页拉取数据集列表，客户端按名称精确匹配查找共享数据集。

        注意：不能用 ``name`` 查询参数过滤——定制版 Ragflow 对不存在的
        名称也返回 code=102 权限错误（实测），只能全量列表后本地匹配。
        未找到返回 None；Ragflow 异常同样返回 None（由调用方兜底）。
        """
        try:
            page = 1
            while page <= 20:  # 安全上限：2000 个数据集
                result = await self.ragflow.list_datasets(page=page, page_size=100)
                datasets = result.get("datasets") if isinstance(result, dict) else None
                items = datasets or []
                for item in items:
                    if (
                        isinstance(item, dict)
                        and str(item.get("name")) == name
                    ):
                        return str(item["id"])
                # 客户端列表分支返回的 total 为本页长度：不满一页即到末尾
                if len(items) < 100:
                    return None
                page += 1
        except RagflowError:
            return None
        return None

    async def _dataset_id_for(self, base: Mapping[str, Any]) -> str:
        """数据集路由：存量库沿用自有数据集，共享库（remote_dataset_id 为空）
        统一落到共享数据集。"""
        own = str(base.get("remote_dataset_id") or "").strip()
        if own:
            return own
        return await self._ensure_shared_dataset()

    @staticmethod
    def _personal_meta_fields(base: Mapping[str, Any]) -> dict[str, Any] | None:
        """个人库文档标签：以库主用户 ID 标识归属，检索时按此过滤。"""
        if str(base.get("space_type")) == KnowledgeBaseVisibility.PERSONAL.value:
            return {"owner_user_id": str(base["owner_user_id"])}
        return None

    @staticmethod
    def _document_meta_fields(
        base: Mapping[str, Any], document: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """文档级 meta_fields：知识库归属标签 + 个人标签 + 业务元数据（RAGFlow 副本）。

        ``base_id`` 让检索可以按知识库元数据过滤，不再把所有文档 ID 塞进
        document_ids（超过 500 篇也不会截断）；业务元数据以本地 SQL 为权威。
        """
        fields: dict[str, Any] = {"base_id": str(base["id"])}
        fields.update(KnowledgeService._personal_meta_fields(base) or {})
        for key in (
            "source",
            "source_record_id",
            "batch_id",
            "report_title",
            "report_tag",
            "report_second_source",
            "publish_date",
        ):
            value = document.get(key)
            if value not in (None, ""):
                fields[key] = str(value)
        return fields or None

    # ---------------- 远端副本清理 ----------------

    async def _delete_remote_documents_quietly(
        self, dataset_id: str, document_ids: list[str]
    ) -> RagflowError | None:
        """删除远端文档副本；远端已不存在视为成功。

        不吞掉真实的上游异常，而是返回给调用方决定策略：删除主路径映射为
        业务错误抛出，兜底清理场景只记日志后继续。
        """

        try:
            await self.ragflow.delete_documents(
                dataset_id=dataset_id, document_ids=document_ids
            )
        except RagflowNotFoundError:
            return None
        except RagflowError as exc:
            return exc
        return None

    async def _delete_remote_documents(
        self, dataset_id: str, document_ids: list[str]
    ) -> None:
        """删除远端文档副本；远端已不存在按成功处理（幂等）。"""

        error = await self._delete_remote_documents_quietly(
            dataset_id, document_ids
        )
        if error is not None:
            raise self._upstream_error(error) from error

    async def _delete_remote_in_batches(
        self, dataset_id: str, remote_ids: list[str]
    ) -> None:
        """按 REMOTE_DELETE_BATCH 分批删除远端副本（远端缺失视为已删）。"""

        for start in range(0, len(remote_ids), REMOTE_DELETE_BATCH):
            await self._delete_remote_documents(
                dataset_id, remote_ids[start : start + REMOTE_DELETE_BATCH]
            )

    async def _delete_base_remote_documents(self, base: Mapping[str, Any]) -> None:
        """同步删除知识库下全部远端文档（分批调用，远端缺失视为已删除）。

        先分页收集远端 ID 再统一删除：更新文档会刷新 updated_at，边查边改
        会导致 offset 分页错位。删完后清空本地 remote_document_id 防死链。
        """
        base_id = str(base["id"])
        pending: list[tuple[str, str]] = []
        offset = 0
        while True:
            rows = await self._repo(
                self.repository.list_documents, base_id, limit=200, offset=offset
            )
            if not rows:
                break
            for row in rows:
                remote_id = str(row.get("remote_document_id") or "").strip()
                if remote_id:
                    pending.append((str(row["id"]), remote_id))
            offset += len(rows)
        if not pending:
            return
        # 仅在有远端文档时解析数据集，避免删除空库时触发共享数据集创建
        dataset_id = await self._dataset_id_for(base)
        for start in range(0, len(pending), REMOTE_DELETE_BATCH):
            chunk = pending[start : start + REMOTE_DELETE_BATCH]
            await self._delete_remote_documents(
                dataset_id, [remote_id for _, remote_id in chunk]
            )
            for document_id, _remote_id in chunk:
                await self._repo(
                    self.repository.update_document,
                    document_id,
                    remote_document_id=None,
                )

    # ---------------- 文档 ----------------

    # ---------------- 操作记录 ----------------

    async def list_operations(
        self,
        *,
        principal: KnowledgePrincipal,
        page: int,
        page_size: int,
        request_id: str,
    ) -> OperationListResponse:
        """分页查询当前用户发起的操作记录。"""

        rows = await self._repo(
            self.repository.list_operations,
            created_by=principal.user_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await self._repo(
            self.repository.count_operations, created_by=principal.user_id
        )
        return OperationListResponse(
            items=[self._operation_summary(row, request_id) for row in rows],
            page=PageInfo(page=page, page_size=page_size, total=total),
        )



__all__ = [
    "AGGREGATION_SNIPPET_LIMIT",
    "KnowledgeService",
    "KnowledgeServiceError",
    "KnowledgeDocumentBinary",
    "upstream_document_running",
]
