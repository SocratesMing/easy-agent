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
from easy_agent.knowledges.service.shared_dataset import (
    SHARED_DATASET_NAME,
    SharedDatasetMixin,
    reset_shared_dataset_cache,
)
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
    SharedDatasetMixin,
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
    "CHUNK_METHOD",
    "DATASET_PERMISSION",
    "KnowledgeService",
    "KnowledgeServiceError",
    "KnowledgeDocumentBinary",
    "PARSER_CONFIG",
    "SHARED_DATASET_NAME",
    "reset_shared_dataset_cache",
    "upstream_document_running",
]
