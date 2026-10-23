"""知识库核心业务服务。

上传/删除全部同步内联；原文存储为本地磁盘存储（``LocalOriginalStore``）。
删除为软删除：本地保留记录与原文，远端（Ragflow）副本在删除时立即同步
删除；清理 worker（KnowledgePurgeWorker）仅兜底同步失败与历史遗留副本。

检索走 Ragflow 标准检索接口（retrieve，POST /api/v1/retrieval）。
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Callable

from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.config import (
    KnowledgeConfig,
    UPSTREAM_DOCUMENT_STATUS_MAPPING,
    UploadSecurityConfig,
)
from easy_agent.knowledges.core.file_validation import UploadValidationError, validate_upload
from easy_agent.knowledges.core.models import (
    AllowedAction,
    DocumentListResponse,
    DocumentMetadata,
    DocumentMetadataFilter,
    DocumentStatus,
    DocumentSummary,
    DocumentUploadAccepted,
    Evidence,
    FolderEnsureResponse,
    FolderListResponse,
    FolderSummary,
    KnowledgeBaseListResponse,
    KnowledgeBaseRole,
    KnowledgeBaseStatus,
    KnowledgeBaseSummary,
    KnowledgeBaseVisibility,
    KnowledgeErrorResponse,
    OperationListResponse,
    OperationPhase,
    OperationStatus,
    OperationSummary,
    OperationType,
    PageInfo,
    PermissionDirectoryListResponse,
    PermissionDirectoryUser,
    PermissionEntry,
    PermissionListResponse,
    RetrieveResponse,
    SessionKnowledgeScopeResponse,
    TeamSpaceDepartmentSummary,
    TeamSpaceManagementCapability,
)
from easy_agent.knowledges.core.operations_repository import KnowledgeOperationsRepository
from easy_agent.knowledges.core.original_store import (
    LocalOriginalStore,
    OriginalReadHandle,
    OriginalStorageError,
)
from easy_agent.knowledges.core.permissions import (
    _ROLE_RANK,
    _base_access_restricted,
    _matching_permission_roles,
    allowed_actions,
    effective_role,
)
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.ragflow import RagflowClient, RagflowError, RagflowNotFoundError

if TYPE_CHECKING:  # 仅供类型注解（api 与 service 相互引用，运行时无循环导入）
    from easy_agent.knowledges.core.models import KnowledgePrincipal

logger = logging.getLogger(__name__)

# 证据最低得分阈值：低于该分的 chunk 不进证据列表，作为本地兜底过滤
MINIMUM_EVIDENCE_SCORE = 0.4

# 数据集创建与解析策略固定参数（扁平配置不承载，代码内固定）
DATASET_PERMISSION = "me"
CHUNK_METHOD = "naive"
PARSER_CONFIG: dict[str, object] = {
    "chunk_token_num": 512,
    "layout_recognize": "DeepDOC",
    "html4excel": False,
    "delimiter": "\n!?;。；！？",
    "raptor": {"use_raptor": False},
}
# 检索固定参数
# 阈值 0.5：召回阶段即过滤无关 chunk（无关问题的混合分多在 0.3~0.5），
# 让与知识库无关的问题返回空证据，而不是凑满 top_n 条弱相关依据
RETRIEVAL_SIMILARITY_THRESHOLD = 0.5
# 向量权重 0.5：向量/关键词各占一半，提升语义相关性、降低表面用词匹配的干扰
RETRIEVAL_VECTOR_SIMILARITY_WEIGHT = 0.5
RETRIEVAL_TOP_K = 1024
# 相邻 chunk 重叠：命中块前后各拼接相邻块 10% 的文本（仅扩展检索证据，不改存储）
RETRIEVAL_NEIGHBOR_OVERLAP_RATIO = 0.1
# 计数/名单/汇总类问题：命中块额外带出完整后续 chunk；普通比例分析题不触发
AGGREGATION_QUESTION_RE = re.compile(r"(几个|多少|几家|几户|哪些|数量|合计|总计|总共|前十|排名)")
RETRIEVAL_AGGREGATION_THRESHOLD = 0.4
# 聚合证据在问答上下文中的最大字符数（普通证据仍为 2400）
AGGREGATION_SNIPPET_LIMIT = 6000
# 后续 chunk 遇到新章节标题时停止，避免把下一节内容当作名单补进来
_SECTION_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:\d{1,2}(?:\.\d{1,2}){1,3}\s+[\u4e00-\u9fa5]|第[一二三四五六七八九十百]+[章节])"
)
# 上传安全校验参数（YAML 不再配置，代码内固定）
_UPLOAD_SECURITY = UploadSecurityConfig()

# 共享数据集：所有走共享模式的个人/公共知识库共用同一个 Ragflow 数据集。
# 个人库文档通过 meta_fields={"owner_user_id": ...} 打个人标签，检索时
# 携带同名过滤条件实现个人隔离（与 document_ids 范围约束形成双重隔离）。
SHARED_DATASET_NAME = "EA_SHARED"
# 进程级缓存（KnowledgeService 每请求实例化，共享数据集 ID 必须跨实例复用）
_shared_dataset_id: str | None = None
_shared_dataset_lock: asyncio.Lock | None = None


def _get_shared_dataset_lock() -> asyncio.Lock:
    """惰性创建模块级锁（避免 import 时绑定事件循环的兼容问题）。"""
    global _shared_dataset_lock
    if _shared_dataset_lock is None:
        _shared_dataset_lock = asyncio.Lock()
    return _shared_dataset_lock


def reset_shared_dataset_cache() -> None:
    """清空共享数据集 ID 进程级缓存（测试用）。"""
    global _shared_dataset_id
    _shared_dataset_id = None


# ---------------------------------------------------------------------------
# 领域辅助
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KnowledgeDocumentBinary:
    """文档二进制内容封装：供响应层按区间分段读取。"""

    size_bytes: int
    content_type: str
    sha256: str | None
    iterator_factory: Callable[[int, int | None], Iterator[bytes]]

    def iter_bytes(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        return self.iterator_factory(start, end)


class KnowledgeServiceError(RuntimeError):
    """知识服务业务异常：携带错误码、HTTP 状态码与可重试标记。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def _map_upstream_status(run: object) -> str:
    return UPSTREAM_DOCUMENT_STATUS_MAPPING.get(str(run or "")) or DocumentStatus.UNKNOWN.value


def _split_folder_segments(path: str) -> list[str]:
    """按 / 或 \\ 拆分相对路径段，过滤空白段。"""

    return [part.strip() for part in re.split(r"[\\/]+", path) if part.strip()]


# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------
class KnowledgeService:
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
        self._team_manager_cache: dict[str, bool] = {}
        self.original_store = (
            original_store if original_store is not None else LocalOriginalStore()
        )
        # refresh_base_documents 节流：避免页面反复加载触发全量上游刷新
        self._last_refresh_at: dict[str, float] = {}
        self._refresh_min_interval = 15.0

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
        """返回共享数据集 ID：配置指定库时直接透传，否则查找/创建（进程级缓存）。

        配置了 shared_dataset_id 时不查找、不创建——适合共享/托管 RAGFlow
        环境（仅授权使用某个既有数据集，不允许随意建库）；ID 是否真实存在
        由后续 Ragflow 调用自然校验。未配置时按名称查找共享数据集，没有则创建。
        创建参数：embedding 模型取知识库配置，缺省用 Ragflow 租户默认模型。
        多进程并发创建的竞态由"失败后重查"兜底：Ragflow 数据集名称全局唯一，
        后创建方会收到错误，重查即可复用。
        """
        configured = self.config.shared_dataset_id.strip()
        if configured:
            return configured
        global _shared_dataset_id
        if _shared_dataset_id:
            return _shared_dataset_id
        async with _get_shared_dataset_lock():
            if _shared_dataset_id:
                return _shared_dataset_id
            found = await self._find_shared_dataset()
            if found is None:
                try:
                    remote = await self.ragflow.create_dataset(
                        name=SHARED_DATASET_NAME,
                        description="EasyAgent 共享知识库（个人/公共库共用）",
                        embedding_model=self.config.embedding.model or None,
                        permission=DATASET_PERMISSION,
                        chunk_method=CHUNK_METHOD,
                        parser_config=PARSER_CONFIG,
                    )
                    _shared_dataset_id = str(remote["id"])
                except RagflowError:
                    # 并发竞态：其他进程/实例刚创建了同名数据集 → 重查复用
                    found = await self._find_shared_dataset()
                    if found is None:
                        raise
                    _shared_dataset_id = found
            else:
                _shared_dataset_id = found
            logger.info("共享数据集就绪 | id=%s", _shared_dataset_id)
            return _shared_dataset_id

    async def _find_shared_dataset(self) -> str | None:
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
                        and str(item.get("name")) == SHARED_DATASET_NAME
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
        """文档级 meta_fields：个人库归属标签 + 业务元数据（RAGFlow 副本）。

        业务元数据以本地 SQL 为权威；这里写入 RAGFlow 仅用于后台追溯与外部
        系统直连时的可见性，不作为检索过滤器。
        """
        fields = dict(KnowledgeService._personal_meta_fields(base) or {})
        for key in (
            "source",
            "source_record_id",
            "report_title",
            "report_tag",
            "report_second_source",
            "publish_date",
        ):
            value = document.get(key)
            if value not in (None, ""):
                fields[key] = str(value)
        return fields or None

    async def _parse_actually_running(self, base: dict, remote_id: str) -> bool:
        """核查远端文档解析是否实际已启动。

        Ragflow 触发解析存在竞态误报：任务已入队（文档 run=RUNNING）但响应
        返回 code=100 IndexError。此时应按成功处理，避免误标失败、留下孤儿文档。
        """
        try:
            data = await self.ragflow.list_documents(
                await self._dataset_id_for(base), document_id=remote_id
            )
        except RagflowError:
            return False
        docs = data.get("docs") if isinstance(data, dict) else data
        if isinstance(docs, list) and docs:
            run = str(docs[0].get("run", "")).upper()
            return run in {"RUNNING", "DONE"}
        return False

    async def _authorized_base(
        self,
        base_id: str,
        principal: KnowledgePrincipal,
        action: AllowedAction = AllowedAction.VIEW,
    ) -> tuple[dict[str, Any], KnowledgeBaseRole]:
        """校验用户对知识库的操作权限，返回 (库记录, 有效角色)。

        无权限或已软删除按 404 处理（不暴露资源存在性），操作越权按 403。
        """

        base = await self._repo(self.repository.get_base, base_id)
        if base is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        permissions = await self._repo(self.repository.list_permissions, base_id)
        role = await self._effective_role(base, principal, permissions)
        if role is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        if action not in allowed_actions(role):
            raise KnowledgeServiceError(
                "KNOWLEDGE_FORBIDDEN", "当前用户无权执行该操作", status_code=403
            )
        if str(base.get("status")) == KnowledgeBaseStatus.DELETED.value:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        return base, role

    async def _has_team_management_grant(self, principal: KnowledgePrincipal) -> bool:
        """判断用户是否在公共空间管理白名单（admin 直通，结果按用户缓存）。"""

        if principal.username == "admin":
            return True
        if principal.user_id not in self._team_manager_cache:
            self._team_manager_cache[principal.user_id] = await self._repo(
                self.repository.is_team_space_manager, principal.user_id
            )
        return self._team_manager_cache[principal.user_id]

    async def _effective_role(
        self,
        base: Mapping[str, object],
        principal: KnowledgePrincipal,
        permissions: Iterable[Mapping[str, object]],
    ) -> KnowledgeBaseRole | None:
        """在单库授权之上叠加公共空间管理白名单。

        admin 与公共空间管理员（白名单账号）对所有公共库直通 MANAGER（同权）；
        其余账号维持本部门默认只读、勾选名单与单库授权逻辑。
        """
        permission_rows = list(permissions)
        if str(base.get("space_type")) != KnowledgeBaseVisibility.TEAM.value:
            return effective_role(base, principal, permission_rows)
        if principal.username == "admin":
            return KnowledgeBaseRole.MANAGER
        if await self._has_team_management_grant(principal):
            return KnowledgeBaseRole.MANAGER
        same_department = bool(principal.department_id) and (
            principal.department_id == str(base.get("department_id") or "").strip()
        )
        # 公共空间默认对本部门账号只读可见；勾选名单（access_restricted）
        # 模式下默认可见让位于勾选人员。
        team_viewer = same_department and not _base_access_restricted(base)
        role = effective_role(base, principal, permission_rows, team_viewer=team_viewer)
        if str(base.get("owner_user_id", "")) == principal.user_id:
            explicit_roles = _matching_permission_roles(principal, permission_rows)
            return max([KnowledgeBaseRole.VIEWER, *explicit_roles], key=_ROLE_RANK.__getitem__)
        return role

    # ---------------- 摘要构造 ----------------

    async def _base_summary(
        self,
        base: Mapping[str, Any],
        role: KnowledgeBaseRole,
        *,
        document_count: int | None = None,
    ) -> KnowledgeBaseSummary:
        """组装知识库摘要：含文档数与当前角色可用操作。

        document_count 由列表查询批量带出时传入，避免逐库 COUNT 查询。
        """

        count = (
            document_count
            if document_count is not None
            else await self._repo(self.repository.count_documents, str(base["id"]))
        )
        return KnowledgeBaseSummary(
            id=str(base["id"]),
            name=str(base["name"]),
            description=str(base.get("description") or ""),
            visibility=KnowledgeBaseVisibility(str(base["space_type"])),
            status=KnowledgeBaseStatus(str(base["status"])),
            role=role,
            document_count=count,
            allowed_actions=allowed_actions(role),
            created_at=base["created_at"],
            updated_at=base["updated_at"],
        )

    @staticmethod
    def _document_summary(
        document: Mapping[str, Any],
        role: KnowledgeBaseRole,
        request_id: str,
    ) -> DocumentSummary:
        """将文档行转换为摘要模型，并归一化错误与进度字段。"""

        error = None
        if document.get("error_code") or document.get("error_message"):
            error = KnowledgeErrorResponse(
                code=str(document.get("error_code") or "KNOWLEDGE_DOCUMENT_ERROR"),
                message=str(document.get("error_message") or "文档处理失败"),
                request_id=request_id,
                retryable=str(document.get("status")) == DocumentStatus.FAILED.value,
            )
        progress = document.get("progress")
        if not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
            progress = None
        return DocumentSummary(
            id=str(document["id"]),
            base_id=str(document["base_id"]),
            folder_id=document.get("folder_id"),
            name=str(document["name"]),
            content_type=str(document["content_type"]),
            size_bytes=int(document["size_bytes"]),
            status=DocumentStatus(str(document["status"])),
            original_status=str(document.get("original_status") or "unmanaged"),
            original_available=str(document.get("original_status") or "") == "available",
            original_sha256=(
                str(document["original_sha256"]) if document.get("original_sha256") else None
            ),
            progress=progress,
            error=error,
            allowed_actions=allowed_actions(role),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
            source=document.get("source"),
            source_record_id=document.get("source_record_id"),
            report_title=document.get("report_title"),
            report_tag=document.get("report_tag"),
            report_second_source=document.get("report_second_source"),
            publish_date=document.get("publish_date"),
        )

    @staticmethod
    def _operation_summary(
        operation: Mapping[str, Any], request_id: str
    ) -> OperationSummary:
        """将操作记录行转换为摘要模型，并归一化错误与进度字段。"""

        error = None
        if operation.get("error_code") or operation.get("error_message"):
            error = KnowledgeErrorResponse(
                code=str(operation.get("error_code") or "KNOWLEDGE_OPERATION_ERROR"),
                message=str(operation.get("error_message") or "知识任务失败"),
                request_id=request_id,
                retryable=bool(operation.get("retryable")),
            )
        progress = operation.get("progress")
        if not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
            progress = None
        return OperationSummary(
            id=str(operation["id"]),
            resource_type=str(operation.get("resource_type") or "") or None,
            resource_id=str(operation.get("resource_id") or "") or None,
            type=OperationType(str(operation["type"])),
            phase=OperationPhase(str(operation["phase"])),
            status=OperationStatus(str(operation["status"])),
            current=int(operation.get("current_count") or 0),
            total=int(operation.get("total_count") or 0),
            progress=progress,
            retryable=bool(operation.get("retryable")),
            error=error,
            created_at=operation["created_at"],
            updated_at=operation["updated_at"],
        )

    # ---------------- 知识库 ----------------

    async def list_bases(
        self, principal: KnowledgePrincipal, *, page: int, page_size: int
    ) -> KnowledgeBaseListResponse:
        """分页列出用户可见的知识库，附带公共空间管理能力信息。"""

        is_admin = principal.username == "admin"
        has_team_management_grant = await self._has_team_management_grant(principal)
        departments: list[TeamSpaceDepartmentSummary] = []
        if is_admin:
            department_rows = await self._repo(self.repository.list_active_departments)
            departments = [
                TeamSpaceDepartmentSummary(
                    id=str(row["department_id"]),
                    name=str(row.get("department_name") or row["department_id"]),
                )
                for row in department_rows
                if str(row.get("department_id") or "").strip()
            ]
        candidates = await self._repo(
            self.repository.list_candidate_bases,
            user_id=principal.user_id,
            department_id=principal.department_id,
            include_all_team_spaces=has_team_management_grant,
        )
        # 权限行一次批量取回，避免逐库查询（列表页 N+1）
        permissions_by_base: dict[str, list[dict[str, Any]]] = {}
        permission_rows = await self._repo(
            self.repository.list_permissions_for_bases,
            [str(base["id"]) for base in candidates],
        )
        for row in permission_rows:
            permissions_by_base.setdefault(str(row["base_id"]), []).append(row)
        visible: list[tuple[dict[str, Any], KnowledgeBaseRole]] = []
        for base in candidates:
            role = await self._effective_role(
                base, principal, permissions_by_base.get(str(base["id"]), [])
            )
            if role is not None:
                visible.append((base, role))
        start = (page - 1) * page_size
        items = [
            await self._base_summary(
                base,
                role,
                document_count=int(base.get("document_count") or 0),
            )
            for base, role in visible[start : start + page_size]
        ]
        return KnowledgeBaseListResponse(
            items=items,
            page=PageInfo(page=page, page_size=page_size, total=len(visible)),
            team_space_management=TeamSpaceManagementCapability(
                can_create=(
                    bool(departments)
                    if is_admin
                    else (bool(principal.department_id) and has_team_management_grant)
                ),
                can_manage=has_team_management_grant,
                department_id=principal.department_id,
                is_admin=is_admin,
                departments=departments,
            ),
        )

    async def get_base(
        self, base_id: str, principal: KnowledgePrincipal
    ) -> KnowledgeBaseSummary:
        """查询单个知识库详情（含角色与可用操作）。"""

        base, role = await self._authorized_base(base_id, principal)
        return await self._base_summary(base, role)

    async def create_base(
        self,
        *,
        principal: KnowledgePrincipal,
        name: str,
        description: str,
        visibility: KnowledgeBaseVisibility,
        request_id: str,
        department_id: str | None = None,
        restrict_access: bool = False,
        permission_user_ids: list[str] | None = None,
    ) -> KnowledgeBaseSummary:
        """创建知识库：公共空间需管理白名单且归属本部门；纯本地建库，走共享数据集。

        创建即配置可见性（仅公共空间）：勾选名单模式下仅勾选人员可见，
        语义与创建后的权限管理（replace_permissions）完全一致。
        """

        del request_id
        permission_ids: list[str] = []
        for raw in permission_user_ids or []:
            user_id = str(raw or "").strip()
            if user_id and user_id != principal.user_id and user_id not in permission_ids:
                permission_ids.append(user_id)
        if visibility == KnowledgeBaseVisibility.PERSONAL and (restrict_access or permission_ids):
            raise KnowledgeServiceError(
                "KNOWLEDGE_PERSONAL_NOT_SHAREABLE",
                "个人空间知识库不可授权",
                status_code=409,
            )
        # 勾选了初始名单即视为进入受限模式（同权限管理语义：名单决定可见面）
        effective_restricted = bool(restrict_access or permission_ids)
        target_department_id: str | None = None
        requested_department_id = (department_id or "").strip() or None
        if visibility == KnowledgeBaseVisibility.TEAM:
            if not await self._has_team_management_grant(principal):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_TEAM_SPACE_MANAGER_REQUIRED",
                    "当前账号未获得公共空间创建与管理权限，请联系 admin 配置",
                    status_code=403,
                )
            if principal.username == "admin":
                target_department_id = requested_department_id or principal.department_id
                if not target_department_id:
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_DEPARTMENT_REQUIRED",
                        "请选择公共空间所属部门",
                        status_code=422,
                    )
                active_departments = await self._repo(
                    self.repository.list_active_departments
                )
                active_department_ids = {
                    str(row.get("department_id") or "").strip()
                    for row in active_departments
                }
                if target_department_id not in active_department_ids:
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_DEPARTMENT_INVALID",
                        "所选部门不存在或没有在职成员",
                        status_code=422,
                    )
            else:
                if not principal.department_id:
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_DEPARTMENT_REQUIRED",
                        "创建公共空间知识库需要有效部门",
                        status_code=422,
                    )
                if requested_department_id and requested_department_id != principal.department_id:
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_DEPARTMENT_FORBIDDEN",
                        "只能在当前账号所属部门创建公共空间",
                        status_code=403,
                    )
                target_department_id = principal.department_id
        base = await self._repo(
            self.repository.create_base,
            name=name,
            description=description,
            space_type=visibility.value,
            owner_user_id=principal.user_id,
            department_id=target_department_id,
            embedding_model=self.config.embedding.model,
        )
        # 纯本地创建（同建文件夹）：不再调用 Ragflow 建数据集，
        # remote_dataset_id 保持 NULL 表示走共享数据集。
        # 勾选名单模式（仅公共空间）：受限标记与激活一并落库（fail-closed）。
        restricted_values = {"access_restricted": 1} if effective_restricted else {}
        base = await self._repo(
            self.repository.update_base,
            base["id"],
            status=KnowledgeBaseStatus.ACTIVE.value,
            **restricted_values,
        )
        if permission_ids:
            await self._repo(
                self.repository.replace_permissions,
                base_id=base["id"],
                permissions=[
                    {
                        "subject_type": "user",
                        "subject_id": user_id,
                        "role": KnowledgeBaseRole.VIEWER.value,
                    }
                    for user_id in permission_ids
                ],
                created_by=principal.user_id,
            )
        # 操作记录直接落终态，保留审计轨迹
        await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DATASET_CREATE.value,
            resource_type="base",
            resource_id=base["id"],
            phase=OperationPhase.COMPLETED.value,
            status=OperationStatus.SUCCEEDED.value,
            created_by=principal.user_id,
        )
        assert base is not None
        return await self._base_summary(base, KnowledgeBaseRole.MANAGER)

    async def update_base(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        name: str | None,
        description: str | None,
        request_id: str,
    ) -> KnowledgeBaseSummary:
        """更新知识库名称与描述；知识库为纯本地投影，无需同步上游数据集。"""
        del request_id
        base, role = await self._authorized_base(
            base_id, principal, AllowedAction.EDIT_BASE
        )
        local_values: dict[str, object] = {}
        if name is not None:
            local_values["name"] = name
        if description is not None:
            local_values["description"] = description
        if local_values:
            base = await self._repo(
                self.repository.update_base, base_id, **local_values
            )
        assert base is not None
        return await self._base_summary(base, role)

    async def delete_base(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> None:
        """删除知识库：先同步删除其全部远端（Ragflow）文档，再标记本地软删除。

        远端删除失败时直接抛错、本地保持原状，用户重试即可（远端已删除的
        文档按 404 幂等跳过）；本地记录与原文仍保留软删除标记（界面不可见）。
        """
        del request_id
        base, _role = await self._authorized_base(
            base_id, principal, AllowedAction.DELETE_BASE
        )
        await self._delete_base_remote_documents(base)
        await self._repo(
            self.repository.update_base,
            base_id,
            status=KnowledgeBaseStatus.DELETED.value,
            deleted_at=datetime.now(UTC).isoformat(),
            deleted_by=principal.user_id,
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
        for start in range(0, len(pending), 100):
            chunk = pending[start : start + 100]
            try:
                await self.ragflow.delete_documents(
                    dataset_id=dataset_id,
                    document_ids=[remote_id for _, remote_id in chunk],
                )
            except RagflowNotFoundError:
                pass  # 远端已不存在：视为已删除（幂等）
            except RagflowError as exc:
                raise self._upstream_error(exc) from exc
            for document_id, _remote_id in chunk:
                await self._repo(
                    self.repository.update_document,
                    document_id,
                    remote_document_id=None,
                )

    # ---------------- 文件夹 ----------------

    async def list_folders(
        self, base_id: str, principal: KnowledgePrincipal
    ) -> FolderListResponse:
        """列出知识库内全部文件夹（含文档与子文件夹计数）。"""

        _, role = await self._authorized_base(base_id, principal)
        rows = await self._repo(self.repository.list_folders, base_id)
        return FolderListResponse(
            items=[
                FolderSummary(
                    id=row["id"],
                    base_id=row["base_id"],
                    parent_id=row.get("parent_id"),
                    name=row["name"],
                    document_count=int(row.get("document_count") or 0),
                    child_count=int(row.get("child_count") or 0),
                    allowed_actions=allowed_actions(role),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]
        )

    async def create_folder(
        self,
        *,
        base_id: str,
        name: str,
        parent_id: str | None,
        principal: KnowledgePrincipal,
    ) -> FolderSummary:
        """创建文件夹：校验层级深度（最多 5 层）与同级重名。"""

        _, role = await self._authorized_base(
            base_id, principal, AllowedAction.CREATE_FOLDER
        )
        if parent_id:
            parent = await self._repo(self.repository.get_folder, parent_id)
            if parent is None or str(parent["base_id"]) != base_id:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_NOT_FOUND", "上级文件夹不存在", status_code=404
                )
            depth = 1
            cursor = parent
            while cursor.get("parent_id"):
                depth += 1
                if depth >= 5:
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_FOLDER_DEPTH_LIMIT",
                        "文件夹最多支持 5 层",
                        status_code=409,
                    )
                cursor = await self._repo(
                    self.repository.get_folder, str(cursor["parent_id"])
                )
                if cursor is None:
                    break
        try:
            existing = await self._repo(
                self.repository.find_folder_by_name, base_id, name, parent_id
            )
            if existing is not None:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_EXISTS",
                    "同名文件夹已存在",
                    status_code=409,
                )
            row = await self._repo(
                self.repository.create_folder,
                base_id=base_id,
                name=name,
                parent_id=parent_id,
            )
        except KnowledgeServiceError:
            raise
        except Exception as exc:
            if "UNIQUE" in str(exc).upper() or "DUPLICATE" in str(exc).upper():
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_EXISTS",
                    "同名文件夹已存在",
                    status_code=409,
                ) from exc
            raise
        return FolderSummary(
            id=row["id"],
            base_id=row["base_id"],
            parent_id=row.get("parent_id"),
            name=row["name"],
            document_count=0,
            child_count=0,
            allowed_actions=allowed_actions(role),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def _folder_chain_depth(
        self, base_id: str, folder_id: str | None
    ) -> int:
        """文件夹层级深度：自身记 1，逐级向上到库根；库根（无文件夹）为 0。"""
        depth = 0
        current = folder_id
        seen: set[str] = set()
        while current:
            if current in seen:  # 脏数据环保护
                break
            seen.add(current)
            folder = await self._repo(self.repository.get_folder, current)
            if folder is None or str(folder["base_id"]) != base_id:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_NOT_FOUND", "文件夹不存在", status_code=404
                )
            depth += 1
            current = str(folder.get("parent_id") or "") or None
        return depth

    async def _ensure_folder_chain(
        self, base_id: str, folder_id: str | None, relative_path: str
    ) -> str | None:
        """按上传相对路径逐级 find-or-create 文件夹，返回叶子文件夹 ID。

        relative_path 形如「资料/报告/2026/q1.pdf」（浏览器 webkitRelativePath），
        最后一段视为文件名，其余段自 folder_id（或库根）起逐级创建，保留上传
        时的目录层级。
        """
        segments = _split_folder_segments(relative_path)
        dirs = segments[:-1] if len(segments) > 1 else []
        return await self._ensure_folder_dirs(base_id, folder_id, dirs)

    async def _ensure_folder_dirs(
        self, base_id: str, folder_id: str | None, dirs: list[str]
    ) -> str | None:
        """逐级 find-or-create 目录段，返回叶子文件夹 ID；空段列表原样返回。

        同级同名文件夹已存在则直接复用，重复上传不产生重复目录。段名拒绝
        「.」「..」与超长名，深度沿用 create_folder 的 5 层上限。
        """
        for segment in dirs:
            if segment in {".", ".."} or len(segment) > 127:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_INVALID_UPLOAD_PATH",
                    "上传路径包含非法目录名",
                    status_code=422,
                )
        current = folder_id
        depth = await self._folder_chain_depth(base_id, current)
        for segment in dirs:
            existing = await self._repo(
                self.repository.find_folder_by_name, base_id, segment, current
            )
            if existing is None and depth >= 5:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_DEPTH_LIMIT",
                    "文件夹最多支持 5 层",
                    status_code=409,
                )
            if existing is None:
                try:
                    row = await self._repo(
                        self.repository.create_folder,
                        base_id=base_id,
                        name=segment,
                        parent_id=current,
                    )
                    current = str(row["id"])
                except Exception as exc:
                    # 并发下撞唯一约束：回读已创建的同名文件夹复用
                    if "UNIQUE" not in str(exc).upper() and "DUPLICATE" not in str(
                        exc
                    ).upper():
                        raise
                    existing = await self._repo(
                        self.repository.find_folder_by_name,
                        base_id,
                        segment,
                        current,
                    )
                    if existing is None:
                        raise
                    current = str(existing["id"])
            else:
                current = str(existing["id"])
            depth += 1
        return current

    async def ensure_folders(
        self,
        *,
        base_id: str,
        folder_id: str | None,
        paths: list[str],
        principal: KnowledgePrincipal,
    ) -> FolderEnsureResponse:
        """批量确保相对目录树存在（文件夹上传的空目录补齐），幂等。

        paths 为纯目录相对路径（如「资料/报告」），自 folder_id（或库根）起
        逐级 find-or-create；返回实际处理的路径数。权限门槛与上传一致
        （UPLOAD），因为它是文件夹上传流程的一部分。
        """
        await self._authorized_base(base_id, principal, AllowedAction.UPLOAD)
        if folder_id:
            folder = await self._repo(self.repository.get_folder, folder_id)
            if folder is None or str(folder["base_id"]) != base_id:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_NOT_FOUND", "文件夹不存在", status_code=404
                )
        ensured = 0
        for raw_path in paths:
            segments = _split_folder_segments(str(raw_path or ""))
            if not segments:
                continue
            await self._ensure_folder_dirs(base_id, folder_id, segments)
            ensured += 1
        return FolderEnsureResponse(ensured=ensured)

    async def update_folder(
        self, *, folder_id: str, name: str, principal: KnowledgePrincipal
    ) -> FolderSummary:
        """重命名文件夹，同级重名时冲突报错。"""

        folder = await self._repo(self.repository.get_folder, folder_id)
        if folder is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        _, role = await self._authorized_base(
            str(folder["base_id"]), principal, AllowedAction.EDIT_FOLDER
        )
        try:
            if str(folder["name"]) != name:
                existing = await self._repo(
                    self.repository.find_folder_by_name,
                    str(folder["base_id"]),
                    name,
                    folder.get("parent_id"),
                )
                if existing is not None:
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_FOLDER_EXISTS",
                        "同名文件夹已存在",
                        status_code=409,
                    )
            row = await self._repo(
                self.repository.update_folder, folder_id, name=name
            )
        except KnowledgeServiceError:
            raise
        except Exception as exc:
            if "UNIQUE" in str(exc).upper() or "DUPLICATE" in str(exc).upper():
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_EXISTS",
                    "同名文件夹已存在",
                    status_code=409,
                ) from exc
            raise
        assert row is not None
        return FolderSummary(
            id=row["id"],
            base_id=row["base_id"],
            parent_id=row.get("parent_id"),
            name=row["name"],
            document_count=0,
            child_count=0,
            allowed_actions=allowed_actions(role),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def delete_folder(
        self, *, folder_id: str, principal: KnowledgePrincipal
    ) -> None:
        """删除空文件夹；非空文件夹拒绝删除。"""

        folder = await self._repo(self.repository.get_folder, folder_id)
        if folder is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        await self._authorized_base(
            str(folder["base_id"]), principal, AllowedAction.DELETE_FOLDER
        )
        deleted = await self._repo(self.repository.delete_empty_folder, folder_id)
        if not deleted:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FOLDER_NOT_EMPTY",
                "文件夹非空，无法删除",
                status_code=409,
            )

    # ---------------- 权限 ----------------

    async def get_permissions(
        self, base_id: str, principal: KnowledgePrincipal
    ) -> PermissionListResponse:
        """查询知识库授权列表与受限标记（需管理权限）。"""

        base, _ = await self._authorized_base(
            base_id, principal, AllowedAction.MANAGE_PERMISSIONS
        )
        rows = await self._repo(self.repository.list_permissions, base_id)
        return PermissionListResponse(
            items=[
                PermissionEntry(
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                    role=row["role"],
                )
                for row in rows
            ],
            restrict_access=_base_access_restricted(base),
        )

    async def replace_permissions(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        items: list[PermissionEntry],
        restrict_access: bool = False,
    ) -> PermissionListResponse:
        """全量替换可见人员名单并更新勾选名单（受限）标记。

        权限管理新语义：仅支持按人员授权，角色一律为查看者（viewer）；
        restrict_access=True 时该库进入勾选名单模式，公共空间默认的
        同部门可见让位于勾选人员（owner、admin、部门管理白名单不受影响）。
        """

        base, _ = await self._authorized_base(
            base_id, principal, AllowedAction.MANAGE_PERMISSIONS
        )
        if str(base["space_type"]) == KnowledgeBaseVisibility.PERSONAL.value and items:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PERSONAL_NOT_SHAREABLE",
                "个人空间知识库不可授权",
                status_code=409,
            )
        seen: set[str] = set()
        payload: list[dict[str, str]] = []
        for item in items:
            if item.subject_type != "user":
                raise KnowledgeServiceError(
                    "KNOWLEDGE_PERMISSION_SUBJECT_UNSUPPORTED",
                    "权限管理仅支持按人员授权",
                    status_code=409,
                )
            subject_id = item.subject_id.strip()
            if subject_id in seen:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_DUPLICATE_PERMISSION",
                    "授权主体重复",
                    status_code=409,
                )
            if subject_id == str(base["owner_user_id"] or ""):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_OWNER_PERMISSION_IMMUTABLE",
                    "所有者权限不可修改",
                    status_code=409,
                )
            seen.add(subject_id)
            payload.append(
                {
                    "subject_type": "user",
                    "subject_id": subject_id,
                    "role": KnowledgeBaseRole.VIEWER.value,
                }
            )
        # 先落受限标记再替换名单（fail-closed）：名单替换失败时最多是
        # 「可见面更窄」，不会出现已声明受限但仍对同部门开放的窗口。
        await self._repo(
            self.repository.update_base,
            base_id,
            access_restricted=1 if restrict_access else 0,
        )
        rows = await self._repo(
            self.repository.replace_permissions,
            base_id=base_id,
            permissions=payload,
            created_by=principal.user_id,
        )
        return PermissionListResponse(
            items=[
                PermissionEntry(
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                    role=row["role"],
                )
                for row in rows
            ],
            restrict_access=restrict_access,
        )

    async def get_permission_directory(
        self, base_id: str, principal: KnowledgePrincipal
    ) -> PermissionDirectoryListResponse:
        """查询权限管理可选人员目录（在职账号，剔除 owner 与 admin）。"""

        base, _ = await self._authorized_base(
            base_id, principal, AllowedAction.MANAGE_PERMISSIONS
        )
        rows = await self._repo(
            self.repository.list_permission_directory_users,
            exclude_user_id=str(base.get("owner_user_id") or ""),
        )
        return PermissionDirectoryListResponse(
            items=[PermissionDirectoryUser(**row) for row in rows]
        )

    async def get_creation_permission_directory(
        self, principal: KnowledgePrincipal
    ) -> PermissionDirectoryListResponse:
        """创建知识库时的可选人员目录（在职账号，剔除本人与 admin）。

        与库权限管理目录同一数据源；门槛对齐公共空间创建：需管理白名单授权，
        避免任意账号拉取全员通讯目录。
        """

        if not await self._has_team_management_grant(principal):
            raise KnowledgeServiceError(
                "KNOWLEDGE_TEAM_SPACE_MANAGER_REQUIRED",
                "当前账号未获得公共空间创建与管理权限，请联系 admin 配置",
                status_code=403,
            )
        rows = await self._repo(
            self.repository.list_permission_directory_users,
            exclude_user_id=str(principal.user_id or ""),
        )
        return PermissionDirectoryListResponse(
            items=[PermissionDirectoryUser(**row) for row in rows]
        )

    # ---------------- 文档 ----------------

    async def refresh_base_documents(
        self, base: Mapping[str, Any], *, request_id: str = ""
    ) -> None:
        """用上游文档状态刷新本地投影；供 list_documents 与后台轮询共用。

        存量自有数据集库：全量列表刷新（原行为不变）。
        共享数据集库（remote_dataset_id 为空）：仅对本库 pending/processing
        的文档逐个查询——共享数据集文档量会超过 list_documents 的
        page_size 上限（100），全量列表既低效又会漏同步；逐文档轮询在
        稳态（全部 ready）时零 API 调用。
        """
        del request_id
        remote_dataset_id = str(base.get("remote_dataset_id") or "").strip()
        # 节流：15 秒内同一知识库不重复刷新（worker 与页面加载共用）
        now = time.monotonic()
        last = self._last_refresh_at.get(str(base["id"]), 0.0)
        if now - last < self._refresh_min_interval:
            return
        self._last_refresh_at[str(base["id"])] = now
        if not remote_dataset_id:
            await self._refresh_shared_base_documents(base)
            return
        try:
            upstream = await self.ragflow.list_documents(
                remote_dataset_id,
                page=1,
                page_size=self.config.limits.max_documents_per_query,
            )
        except RagflowError as exc:
            # 轮询失败只跳过本轮，但必须留痕，否则状态不回传时无从排查
            logger.warning(
                "刷新上游文档状态失败 base=%s dataset=%s: %s",
                base.get("id"), remote_dataset_id, exc,
            )
            return
        docs = upstream.get("docs", []) if isinstance(upstream, dict) else []
        remote_ids = [str(item.get("id")) for item in docs if item.get("id")]
        local_docs = await self._repo(
            self.repository.get_documents_by_remote_ids,
            str(base["id"]),
            remote_ids,
        )
        by_remote = {str(item["remote_document_id"]): item for item in local_docs}
        for upstream_doc in docs:
            if not isinstance(upstream_doc, dict):
                continue
            local = by_remote.get(str(upstream_doc.get("id")))
            if local is None:
                continue
            await self._apply_upstream_doc_status(local, upstream_doc)

    async def _refresh_shared_base_documents(self, base: Mapping[str, Any]) -> None:
        """共享数据集库的状态刷新：逐个轮询本库解析中的文档。"""
        try:
            dataset_id = await self._dataset_id_for(base)
        except RagflowError as exc:
            logger.warning(
                "共享数据集解析失败 base=%s: %s", base.get("id"), exc
            )
            return
        limit = self.config.limits.max_documents_per_query
        active_docs: list[dict[str, Any]] = []
        for status_value in ("pending", "processing"):
            active_docs.extend(
                await self._repo(
                    self.repository.list_documents,
                    str(base["id"]),
                    status=status_value,
                    limit=limit,
                    offset=0,
                )
            )
        for local in active_docs:
            remote_id = str(local.get("remote_document_id") or "").strip()
            if not remote_id:
                continue
            try:
                upstream = await self.ragflow.list_documents(
                    dataset_id, document_id=remote_id
                )
            except RagflowError as exc:
                logger.warning(
                    "刷新共享库文档状态失败 base=%s doc=%s: %s",
                    base.get("id"), local.get("id"), exc,
                )
                continue
            docs = upstream.get("docs") if isinstance(upstream, dict) else None
            if not isinstance(docs, list) or not docs:
                continue
            upstream_doc = docs[0]
            if isinstance(upstream_doc, dict):
                await self._apply_upstream_doc_status(local, upstream_doc)

    async def _apply_upstream_doc_status(
        self, local: Mapping[str, Any], upstream_doc: Mapping[str, Any]
    ) -> None:
        """按上游文档状态更新本地投影（状态/进度/超时判定/操作记录）。"""
        # 本地已暂停的文档不受上游覆盖：stop 后上游 run 回落 UNSTART，
        # 不拦截会被刷新循环拉回 pending，暂停态就丢了
        if str(local.get("status")) == DocumentStatus.PAUSED.value:
            return
        status = DocumentStatus(_map_upstream_status(upstream_doc.get("run")))
        progress = upstream_doc.get("progress")
        if not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
            progress = None
        changed = (
            str(local.get("status")) != status.value
            or local.get("progress") != progress
        )
        now_iso = datetime.now(UTC).isoformat()
        parse_started_at = local.get("parse_started_at")
        if status == DocumentStatus.PROCESSING and not parse_started_at:
            # 首次真正开始解析：超时从此处计时，不含上游排队时间
            parse_started_at = now_iso
        # 超时判定先于“无变化跳过”：进度停滞的文档也必须能被发现
        timeout_seconds = float(self.config.limits.parse_timeout_seconds or 0)
        is_sync_source = str(local.get("source") or "") == "source_sync"
        if (
            not is_sync_source
            and timeout_seconds > 0
            and (
                status == DocumentStatus.PROCESSING
                or str(local.get("status")) == DocumentStatus.PROCESSING.value
            )
        ):
            started_at = parse_started_at
            if not started_at:
                active_operation = await self._repo(
                    self.repository.get_active_document_operation,
                    str(local["id"]),
                )
                latest_operation = active_operation or await self._repo(
                    self.repository.get_latest_document_operation, str(local["id"])
                )
                started_at = (
                    latest_operation.get("created_at") if latest_operation else None
                )
            try:
                started = datetime.fromisoformat(str(started_at))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                elapsed = (datetime.now(UTC) - started).total_seconds()
            except (TypeError, ValueError):
                elapsed = 0.0
            # 之前判定超时的文档保持失败，除非上游已给出终态
            was_timed_out = (
                str(local.get("error_code") or "") == "KNOWLEDGE_PARSE_TIMEOUT"
            )
            if was_timed_out:
                return
            if elapsed > timeout_seconds:
                timeout_message = (
                    f"解析超过 {int(timeout_seconds)} 秒仍未完成，请拆分文件后重试"
                )
                await self._repo(
                    self.repository.update_document,
                    str(local["id"]),
                    status=DocumentStatus.FAILED.value,
                    progress=None,
                    error_code="KNOWLEDGE_PARSE_TIMEOUT",
                    error_message=timeout_message,
                )
                await self._repo(
                    self.repository.update_active_document_operations,
                    str(local["id"]),
                    phase=OperationPhase.FAILED.value,
                    status=OperationStatus.FAILED.value,
                    progress=None,
                    retryable=True,
                    error_code="KNOWLEDGE_PARSE_TIMEOUT",
                    error_message=timeout_message,
                )
                return
        # 状态与进度均无变化：跳过后续 DB 写入（稳态轮询省往返）
        if not changed:
            return
        values: dict[str, object] = {"status": status.value, "progress": progress}
        if parse_started_at and parse_started_at != local.get("parse_started_at"):
            values["parse_started_at"] = parse_started_at
        if status == DocumentStatus.FAILED:
            values.update(
                {
                    "error_code": "KNOWLEDGE_PARSE_FAILED",
                    "error_message": str(
                        upstream_doc.get("progress_msg") or "文档解析失败"
                    ),
                }
            )
        elif status == DocumentStatus.READY:
            values.update({"error_code": None, "error_message": None})
        await self._repo(self.repository.update_document, local["id"], **values)
        operation_values: dict[str, object] = {"progress": progress}
        if status == DocumentStatus.READY:
            operation_values.update(
                {
                    "phase": OperationPhase.COMPLETED.value,
                    "status": OperationStatus.SUCCEEDED.value,
                    "current_count": 1,
                    "progress": 1.0,
                    "retryable": False,
                    "error_code": None,
                    "error_message": None,
                }
            )
        elif status == DocumentStatus.FAILED:
            operation_values.update(
                {
                    "phase": OperationPhase.FAILED.value,
                    "status": OperationStatus.FAILED.value,
                    "retryable": True,
                    "error_code": "KNOWLEDGE_PARSE_FAILED",
                    "error_message": str(
                        upstream_doc.get("progress_msg") or "文档解析失败"
                    ),
                }
            )
        updated_operation_count = await self._repo(
            self.repository.update_active_document_operations,
            str(local["id"]),
            **operation_values,
        )
        if (
            status == DocumentStatus.READY
            and not updated_operation_count
            and latest_operation is not None
            and str(latest_operation.get("error_code") or "")
            == "KNOWLEDGE_PARSE_TIMEOUT"
        ):
            await self._repo(
                self.repository.update_operation,
                str(latest_operation["id"]),
                **operation_values,
            )

    async def list_documents(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        page: int,
        page_size: int,
        folder_id: str | None,
        direct_only: bool,
        status: str | None,
        query: str | None,
        request_id: str,
    ) -> DocumentListResponse:
        """分页列出知识库文档；返回前先刷新上游解析状态。"""

        base, role = await self._authorized_base(base_id, principal)
        await self.refresh_base_documents(base, request_id=request_id)
        rows = await self._repo(
            self.repository.list_documents,
            base_id,
            folder_id=folder_id,
            direct_only=direct_only,
            status=status,
            query=query,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await self._repo(
            self.repository.count_documents_filtered,
            base_id,
            folder_id=folder_id,
            direct_only=direct_only,
            status=status,
            query=query,
        )
        return DocumentListResponse(
            items=[self._document_summary(row, role, request_id) for row in rows],
            page=PageInfo(page=page, page_size=page_size, total=total),
        )

    async def _submit_to_ragflow(
        self,
        *,
        base: Mapping[str, Any],
        document: Mapping[str, Any],
        dataset_id: str,
        source: BinaryIO,
        operation_id: str,
        failure_message: str,
        original_error_message: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """上传远端 → 配置解析器 → 触发解析 → 落 processing，返回最新投影行。

        上传与重试共用该段流程；失败时统一标记文档/操作为失败并抛出映射后的
        业务错误。调用方负责关闭 source。
        """

        try:
            remote_documents = await self.ragflow.upload_documents(
                dataset_id,
                [(str(document["name"]), source, str(document["content_type"]))],
            )
            if not remote_documents or not remote_documents[0].get("id"):
                raise KeyError("id")
            remote_id = str(remote_documents[0]["id"])
            document = await self._repo(
                self.repository.update_document,
                str(document["id"]),
                remote_document_id=remote_id,
                status=DocumentStatus.PENDING.value,
            )
            await self._repo(
                self.repository.update_operation,
                operation_id,
                phase=OperationPhase.CONFIGURING.value,
                progress=0.6,
            )
            # 个人库打 owner_user_id 标签；业务文档同时写 metadata 副本
            await self.ragflow.update_document(
                dataset_id,
                remote_id,
                chunk_method=CHUNK_METHOD,
                parser_config=PARSER_CONFIG,
                meta_fields=self._document_meta_fields(base, document),
            )
            try:
                await self.ragflow.parse_documents(dataset_id, [remote_id])
            except RagflowError as exc:
                if not await self._parse_actually_running(base, remote_id):
                    raise
                logger.warning(
                    "RAGFlow 触发解析误报(%s)，文档实际已开始解析，按成功处理",
                    exc,
                )
            document = await self._repo(
                self.repository.update_document,
                str(document["id"]),
                status=DocumentStatus.PROCESSING.value,
                progress=0.0,
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation_id,
                phase=OperationPhase.PARSING.value,
                progress=0.0,
            )
        except (RagflowError, KeyError, OriginalStorageError) as exc:
            await self._fail_submit(
                document_id=str(document["id"]),
                operation_id=operation_id,
                exc=exc,
                failure_message=failure_message,
                original_error_message=original_error_message,
            )
        return document, operation

    async def _fail_submit(
        self,
        *,
        document_id: str,
        operation_id: str,
        exc: BaseException,
        failure_message: str,
        original_error_message: str,
    ) -> None:
        """标记文档与操作为失败，并抛出映射后的业务错误（始终抛出）。"""

        code = getattr(exc, "code", "RAGFLOW_CONTRACT_MISMATCH")
        retryable = bool(getattr(exc, "retryable", False))
        await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.FAILED.value,
            error_code=code,
            error_message=failure_message,
        )
        await self._repo(
            self.repository.update_operation,
            operation_id,
            phase=OperationPhase.FAILED.value,
            status=OperationStatus.FAILED.value,
            retryable=retryable,
            error_code=code,
            error_message=failure_message,
        )
        if isinstance(exc, RagflowError):
            raise self._upstream_error(exc) from exc
        if isinstance(exc, OriginalStorageError):
            raise KnowledgeServiceError(
                code, original_error_message, status_code=503, retryable=retryable
            ) from exc
        raise KnowledgeServiceError(
            "KNOWLEDGE_UPSTREAM_ERROR",
            "知识底座返回了无效文档数据",
            status_code=502,
        )

    async def upload_document(
        self,
        *,
        base_id: str,
        folder_id: str | None,
        filename: str,
        content_type: str,
        size_bytes: int,
        content: BinaryIO,
        principal: KnowledgePrincipal,
        request_id: str,
        relative_path: str | None = None,
        metadata: DocumentMetadata | None = None,
    ) -> DocumentUploadAccepted:
        """上传文档：校验 → 本地落盘原文 → 上传 RAGFlow → 配置解析器 → 触发解析。

        relative_path 为浏览器文件夹上传的原始相对路径（webkitRelativePath），
        非空时按其目录段逐级 find-or-create 文件夹后落入叶子目录。
        metadata 为文档 SQL 同步附带的业务元数据（人工上传时为 None）。
        """
        base, role = await self._authorized_base(
            base_id, principal, AllowedAction.UPLOAD
        )
        if folder_id:
            folder = await self._repo(self.repository.get_folder, folder_id)
            if folder is None or str(folder["base_id"]) != base_id:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_NOT_FOUND",
                    "文件夹不存在",
                    status_code=404,
                )
        if relative_path:
            folder_id = await self._ensure_folder_chain(
                base_id, folder_id, relative_path
            )
        if (
            await self._repo(self.repository.count_documents, base_id)
            >= self.config.limits.max_documents_per_query
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_LIMIT",
                "知识库文档数量已达到第一期上限",
                status_code=409,
            )
        if size_bytes > self.config.limits.max_file_size_mb * 1024 * 1024:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TOO_LARGE", "文件超过大小限制", status_code=413
            )
        safe_name = Path(filename).name
        if (
            not safe_name
            or len(safe_name.encode("utf-8")) > self.config.limits.max_filename_utf8_bytes
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_INVALID_FILENAME", "文件名无效或过长", status_code=422
            )
        extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if extension not in {
            value.lower() for value in self.config.limits.allowed_extensions
        }:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TYPE_NOT_ALLOWED",
                f"不支持该文件类型（后缀 .{extension or '无'}）",
                status_code=422,
            )
        try:
            await self._repo(
                validate_upload,
                content,
                filename=safe_name,
                content_type=content_type or "application/octet-stream",
                size_bytes=size_bytes,
                config=_UPLOAD_SECURITY,
            )
        except UploadValidationError as exc:
            raise KnowledgeServiceError(
                exc.code,
                exc.message,
                status_code=503 if exc.retryable else 422,
                retryable=exc.retryable,
            ) from exc

        document = await self._repo(
            self.repository.create_document,
            base_id=base_id,
            folder_id=folder_id,
            name=safe_name,
            content_type=content_type or "application/octet-stream",
            size_bytes=size_bytes,
            created_by=principal.user_id,
            status=DocumentStatus.PENDING.value,
            source=metadata.source if metadata else None,
            source_record_id=metadata.source_record_id if metadata else None,
            report_title=metadata.report_title if metadata else None,
            report_tag=metadata.report_tag if metadata else None,
            report_second_source=metadata.report_second_source if metadata else None,
            publish_date=metadata.publish_date if metadata else None,
        )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DOCUMENT_UPLOAD.value,
            resource_type="document",
            resource_id=document["id"],
            phase=OperationPhase.STORING_ORIGINAL.value,
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
            retryable=True,
        )
        if self.original_store is None:
            raise KnowledgeServiceError(
                "ORIGINAL_STORAGE_NOT_READY",
                "原文存储尚未就绪",
                status_code=503,
                retryable=True,
            )
        storage_key = self.original_store.build_key(
            base_id, str(document["id"]), safe_name
        )
        await self._repo(
            self.repository.create_original_object,
            document_id=str(document["id"]),
            provider=self.original_store.provider,
            storage_key=storage_key,
            original_name=safe_name,
            content_type=content_type or "application/octet-stream",
            size_bytes=size_bytes,
            created_by=principal.user_id,
        )
        # ---- 本地落盘原文 ----
        try:
            stored_size, stored_sha = await self._repo(
                self.original_store.put_atomic, storage_key, content, size_bytes
            )
            await self._repo(
                self.repository.update_original_object,
                str(document["id"]),
                status="available",
                size_bytes=stored_size,
                sha256=stored_sha,
                stored_at=datetime.now(UTC).isoformat(),
                error_code=None,
                error_message=None,
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.UPLOADING.value,
                progress=0.35,
            )
        except OriginalStorageError as exc:
            await self._repo(
                self.repository.update_original_object,
                str(document["id"]),
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
            )
            document = await self._repo(
                self.repository.update_document,
                str(document["id"]),
                status=DocumentStatus.FAILED.value,
                error_code=exc.code,
                error_message="原文存储失败，未提交知识底座",
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="原文存储失败，未提交知识底座",
            )
            raise KnowledgeServiceError(
                exc.code,
                "原文存储暂时不可用",
                status_code=503 if exc.retryable else 422,
                retryable=exc.retryable,
            ) from exc
        # ---- 上传 RAGFlow 并触发解析 ----
        source = self.original_store.open(storage_key)
        binary = source.open_binary()
        try:
            document, operation = await self._submit_to_ragflow(
                base=base,
                document=document,
                dataset_id=await self._dataset_id_for(base),
                source=binary,
                operation_id=str(operation["id"]),
                failure_message="文档上传或解析启动失败",
                original_error_message="原文读取失败",
            )
        finally:
            binary.close()
        assert document is not None and operation is not None
        return DocumentUploadAccepted(
            document=self._document_summary(document, role, request_id),
            operation=self._operation_summary(operation, request_id),
        )

    async def move_document(
        self,
        *,
        document_id: str,
        folder_id: str | None,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> DocumentSummary:
        """移动文档到目标文件夹（目标须属于同一知识库）。"""

        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        if str(document.get("status")) == DocumentStatus.DELETED.value:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        _, role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.MOVE_DOCUMENT
        )
        if folder_id:
            folder = await self._repo(self.repository.get_folder, folder_id)
            if folder is None or str(folder["base_id"]) != str(document["base_id"]):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_FOLDER_NOT_FOUND", "文件夹不存在", status_code=404
                )
        document = await self._repo(
            self.repository.update_document, document_id, folder_id=folder_id
        )
        assert document is not None
        return self._document_summary(document, role, request_id)

    async def download_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> tuple[dict[str, Any], KnowledgeDocumentBinary]:
        """优先返回本地原文；不可用时回退下载上游副本。"""
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        if str(document.get("status")) == DocumentStatus.DELETED.value:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        base, _ = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.DOWNLOAD
        )
        original = await self._repo(
            self.repository.get_original_object, document_id
        )
        if (
            original is not None
            and str(original.get("status")) == "available"
            and self.original_store is not None
        ):
            try:
                handle: OriginalReadHandle = await self._repo(
                    self.original_store.open, str(original["storage_key"])
                )
            except OriginalStorageError as exc:
                # 本地原文记录仍在但磁盘文件丢失/不可读：不再直接 503，
                # 降级为上游（RAGFlow）副本下载，保证预览与下载可用。
                logger.warning(
                    "本地原文不可用，回退上游副本 | document_id=%s code=%s",
                    document_id,
                    exc.code,
                )
            else:
                await self._repo(
                    self.repository.create_original_access_audit,
                    user_id=principal.user_id,
                    session_id="",
                    agent_id="api",
                    document_id=document_id,
                    action="download",
                    result="opened",
                    request_id=request_id,
                )
                return document, KnowledgeDocumentBinary(
                    size_bytes=handle.size_bytes,
                    content_type=str(original.get("content_type") or document["content_type"]),
                    sha256=str(original.get("sha256")) if original.get("sha256") else None,
                    iterator_factory=handle.iter_bytes,
                )
        if not document.get("remote_document_id"):
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档原文件暂不可用", status_code=409
            )
        try:
            binary = await self.ragflow.download_document(
                dataset_id=await self._dataset_id_for(base),
                document_id=str(document["remote_document_id"]),
            )
        except RagflowError as exc:
            raise self._upstream_error(exc) from exc
        content_bytes = binary.content
        return document, KnowledgeDocumentBinary(
            size_bytes=len(content_bytes),
            content_type=binary.content_type,
            sha256=None,
            iterator_factory=lambda start=0, end=None: iter(
                [content_bytes[start : (len(content_bytes) if end is None else end + 1)]]
            ),
        )

    async def delete_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> None:
        """删除文档：立即同步删除远端（Ragflow）副本，本地保留软删除记录与原文。

        远端删除失败时直接抛错、本地保持原状，用户重试即可；远端已不存在
        按 404 幂等处理。
        """
        del request_id
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        if str(document.get("status")) == DocumentStatus.DELETED.value:
            return
        base, _role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.DELETE_DOCUMENT
        )
        remote_id = str(document.get("remote_document_id") or "").strip()
        if remote_id:
            try:
                await self.ragflow.delete_documents(
                    dataset_id=await self._dataset_id_for(base),
                    document_ids=[remote_id],
                )
            except RagflowNotFoundError:
                pass  # 远端已不存在：视为已删除（幂等）
            except RagflowError as exc:
                raise self._upstream_error(exc) from exc
        await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.DELETED.value,
            deleted_at=datetime.now(UTC).isoformat(),
            deleted_by=principal.user_id,
            remote_document_id=None,
            progress=None,
        )

    async def purge_deleted_documents(self, *, limit: int = 100) -> int:
        """兜底清理软删除文档的远端副本，返回本次清理数。

        正常删除路径已同步删除远端副本；本方法由后台清理 worker 周期调用，
        兜底同步删除失败与历史遗留（仍持有远端副本的软删除文档/已删除库）。
        远端删除成功（或远端已不存在，幂等）后清空本地 remote_document_id；
        本地软删除记录与原文保留。单文档失败不阻断其余清理，留待下轮重试。
        """
        rows = await self._repo(
            self.repository.list_purgeable_documents, limit=limit
        )
        if not rows:
            return 0
        purged = 0
        for row in rows:
            dataset_id = str(row.get("base_remote_dataset_id") or "").strip()
            if not dataset_id:
                # 共享数据集模式：路由到 EA_SHARED（含进程级缓存）
                dataset_id = await self._ensure_shared_dataset()
            try:
                await self.ragflow.delete_documents(
                    dataset_id=dataset_id,
                    document_ids=[str(row["remote_document_id"])],
                )
            except RagflowNotFoundError:
                pass  # 远端已不存在：视为已清理，落本地防重扫
            except RagflowError as exc:
                logger.warning(
                    "清理到期文档远端副本失败（下轮重试） | doc=%s: %s",
                    row["document_id"],
                    exc,
                )
                continue
            await self._repo(
                self.repository.update_document,
                str(row["document_id"]),
                remote_document_id=None,
            )
            purged += 1
        if purged:
            logger.info("兜底清理软删除文档远端副本完成 | 清理远端文档数=%s", purged)
        return purged

    async def pause_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> Mapping[str, Any]:
        """暂停未就绪文档的解析任务：退出解析队列，保留原文可随时恢复。"""
        del request_id
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        base, _role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.RETRY_DOCUMENT
        )
        if str(document["status"]) not in {
            DocumentStatus.PENDING.value,
            DocumentStatus.PROCESSING.value,
        }:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PAUSE_NOT_ALLOWED",
                "仅未就绪（等待解析/解析中）的文档支持暂停",
                status_code=409,
            )
        remote_id = str(document.get("remote_document_id") or "").strip()
        if not remote_id:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PAUSE_NO_REMOTE",
                "文档尚未同步到解析服务，无法暂停",
                status_code=409,
            )
        dataset_id = await self._dataset_id_for(base)
        try:
            await self.ragflow.stop_parsing(dataset_id, [remote_id])
        except RagflowError as exc:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PAUSE_FAILED", f"暂停解析失败: {exc}", status_code=502
            ) from exc
        return await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.PAUSED.value,
            progress=None,
        )

    async def resume_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> Mapping[str, Any]:
        """恢复已暂停文档：重新提交解析队列，回到待解析状态。"""
        del request_id
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        base, _role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.RETRY_DOCUMENT
        )
        if str(document["status"]) != DocumentStatus.PAUSED.value:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RESUME_NOT_ALLOWED",
                "仅已暂停的文档支持恢复",
                status_code=409,
            )
        remote_id = str(document.get("remote_document_id") or "").strip()
        if not remote_id:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RESUME_NO_REMOTE",
                "文档尚未同步到解析服务，无法恢复",
                status_code=409,
            )
        dataset_id = await self._dataset_id_for(base)
        try:
            await self.ragflow.parse_documents(dataset_id, [remote_id])
        except RagflowError as exc:
            if not await self._parse_actually_running(base, remote_id):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_RESUME_FAILED",
                    f"恢复解析失败: {exc}",
                    status_code=502,
                ) from exc
        return await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.PENDING.value,
            progress=None,
        )

    async def retry_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> DocumentUploadAccepted:
        """重试失败文档：删除上游旧副本后，用本地原文重新上传并解析。"""
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        base, role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.RETRY_DOCUMENT
        )
        if str(document["status"]) not in {
            DocumentStatus.FAILED.value,
            DocumentStatus.CANCELLED.value,
            DocumentStatus.UNKNOWN.value,
        }:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RETRY_NOT_ALLOWED",
                "当前文档状态不允许重试",
                status_code=409,
            )
        remote_id = document.get("remote_document_id")
        original_record = await self._repo(
            self.repository.get_original_object, document_id
        )
        has_local_original = (
            original_record is not None
            and str(original_record.get("status")) == "available"
            and self.original_store is not None
        )
        if not remote_id and not has_local_original:
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档原文件不可用", status_code=409
            )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DOCUMENT_PARSE.value,
            resource_type="document",
            resource_id=document_id,
            phase=OperationPhase.UPLOADING.value,
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
            retryable=True,
        )
        source: BinaryIO | None = None
        try:
            dataset_id = await self._dataset_id_for(base)
            if has_local_original:
                handle = await self._repo(
                    self.original_store.open, str(original_record["storage_key"])
                )
                source = handle.open_binary()
            else:
                downloaded = await self.ragflow.download_document(
                    dataset_id=dataset_id,
                    document_id=str(remote_id),
                )
                source = io.BytesIO(downloaded.content)
            if remote_id:
                await self.ragflow.delete_documents(
                    dataset_id=dataset_id,
                    document_ids=[str(remote_id)],
                )
            await self._repo(
                self.repository.update_document,
                document_id,
                remote_document_id=None,
                status=DocumentStatus.PENDING.value,
                error_code=None,
                error_message=None,
            )
        except (RagflowError, KeyError, OriginalStorageError) as exc:
            await self._fail_submit(
                document_id=document_id,
                operation_id=str(operation["id"]),
                exc=exc,
                failure_message="文档重试失败",
                original_error_message="文档原文件不可用",
            )
        try:
            document, operation = await self._submit_to_ragflow(
                base=base,
                document=document,
                dataset_id=dataset_id,
                source=source,
                operation_id=str(operation["id"]),
                failure_message="文档重试失败",
                original_error_message="文档原文件不可用",
            )
        finally:
            if source is not None:
                source.close()
        assert document is not None and operation is not None
        return DocumentUploadAccepted(
            document=self._document_summary(document, role, request_id),
            operation=self._operation_summary(operation, request_id),
        )

    # ---------------- 检索与问答 ----------------

    @staticmethod
    def _metadata_filter_kwargs(
        metadata_filter: DocumentMetadataFilter | None,
    ) -> dict[str, Any]:
        """把过滤器转成 repository._document_filters 的关键字参数。"""
        if metadata_filter is None:
            return {}
        return {
            "publish_date_from": metadata_filter.publish_date_from,
            "publish_date_to": metadata_filter.publish_date_to,
            "report_tags": metadata_filter.report_tags or None,
            "report_second_sources": metadata_filter.report_second_sources or None,
            "report_title_contains": metadata_filter.report_title_contains,
        }

    @staticmethod
    def _matches_metadata_filter(
        document: Mapping[str, Any], metadata_filter: DocumentMetadataFilter | None
    ) -> bool:
        """本地兜底校验：与 SQL 过滤保持同一语义（AND / 多值 OR）。"""
        if metadata_filter is None:
            return True
        publish_date = str(document.get("publish_date") or "")
        if metadata_filter.publish_date_from and (
            not publish_date or publish_date < metadata_filter.publish_date_from
        ):
            return False
        if metadata_filter.publish_date_to and (
            not publish_date or publish_date > metadata_filter.publish_date_to
        ):
            return False
        if metadata_filter.report_tags and str(
            document.get("report_tag") or ""
        ) not in set(metadata_filter.report_tags):
            return False
        if metadata_filter.report_second_sources and str(
            document.get("report_second_source") or ""
        ) not in set(metadata_filter.report_second_sources):
            return False
        if metadata_filter.report_title_contains:
            haystack = str(
                document.get("report_title") or document.get("name") or ""
            ).lower()
            if metadata_filter.report_title_contains.lower() not in haystack:
                return False
        return True

    async def _retrieve_base_evidence(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        question: str,
        document_ids: list[str] | None,
        top_n: int,
        request_id: str,
        metadata_filter: DocumentMetadataFilter | None = None,
    ) -> RetrieveResponse:
        """执行检索；无可检索文档时返回空证据与提示，不编造。

        metadata_filter 为硬过滤：先缩小本地文档集，再只把这些文档的
        remote_document_id 交给 RAGFlow，保证区间外文档不会被召回。
        """
        base, _ = await self._authorized_base(base_id, principal, AllowedAction.ASK)
        filter_kwargs = self._metadata_filter_kwargs(metadata_filter)
        if document_ids is None:
            documents = await self._repo(
                self.repository.list_documents,
                base_id,
                status=DocumentStatus.READY.value,
                limit=self.config.limits.max_documents_per_query,
                offset=0,
                **filter_kwargs,
            )
        else:
            documents = await self._repo(
                self.repository.get_documents, base_id, document_ids
            )
            if len(documents) != len(set(document_ids)):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
                )
            documents = [
                item for item in documents if item["status"] == DocumentStatus.READY.value
            ]
        documents = [
            item
            for item in documents
            if item.get("remote_document_id")
            and self._matches_metadata_filter(item, metadata_filter)
        ]
        if not documents:
            return RetrieveResponse(
                evidence=[], warnings=["当前范围没有可检索的已解析文档"], request_id=request_id
            )
        dataset_id = await self._dataset_id_for(base)
        aggregation = bool(AGGREGATION_QUESTION_RE.search(question))
        try:
            # 个人库走共享数据集时，检索携带个人标签过滤条件
            # （与 document_ids 范围约束形成双重隔离；存量自有数据集库不打
            # 标签、不过滤，保持原行为）
            retrieval_meta: dict[str, Any] | None = None
            if not str(base.get("remote_dataset_id") or "").strip():
                retrieval_meta = self._personal_meta_fields(base)
            result = await self.ragflow.retrieve(
                question=question,
                dataset_ids=[dataset_id],
                document_ids=[str(item["remote_document_id"]) for item in documents],
                page_size=min(max(top_n * 2, 20), 50) if aggregation else top_n,
                similarity_threshold=(
                    RETRIEVAL_AGGREGATION_THRESHOLD
                    if aggregation
                    else RETRIEVAL_SIMILARITY_THRESHOLD
                ),
                vector_similarity_weight=RETRIEVAL_VECTOR_SIMILARITY_WEIGHT,
                top_k=RETRIEVAL_TOP_K,
                meta_fields=retrieval_meta,
            )
        except RagflowError as exc:
            raise self._upstream_error(exc) from exc
        by_remote = {str(item["remote_document_id"]): item for item in documents}
        candidates: list[dict[str, Any]] = []
        for chunk in result.get("chunks", []):
            if not isinstance(chunk, dict):
                continue
            local = by_remote.get(str(chunk.get("document_id")))
            if local is None or local["status"] != DocumentStatus.READY.value:
                continue
            # 纵深防御：上游若意外返回范围外文档，这里再挡一次
            if not self._matches_metadata_filter(local, metadata_filter):
                continue
            snippet = str(
                chunk.get("content_with_weight") or chunk.get("content") or ""
            ).strip()
            if not snippet:
                continue
            raw_score = chunk.get("similarity", 0)
            score = max(0.0, float(raw_score)) if isinstance(raw_score, (int, float)) else 0.0
            if score < MINIMUM_EVIDENCE_SCORE:
                continue
            positions = chunk.get("positions")
            candidates.append(
                {
                    "document": local,
                    "chunk_id": str(chunk.get("chunk_id") or chunk.get("id") or ""),
                    "snippet": snippet,
                    "score": score,
                    "position": positions[0] if isinstance(positions, list) and positions else None,
                }
            )
        await self._expand_neighbor_overlap(
            dataset_id=dataset_id,
            candidates=candidates,
            aggregation=aggregation,
        )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return RetrieveResponse(
            evidence=[
                Evidence(
                    document_id=str(item["document"]["id"]),
                    document_name=str(item["document"]["name"]),
                    snippet=item["snippet"],
                    score=item["score"],
                    position=item["position"],
                    metadata={
                        "base_id": str(base["id"]),
                        "base_name": str(base["name"]),
                        "aggregation": aggregation,
                        "source_record_id": item["document"].get("source_record_id"),
                        "report_title": item["document"].get("report_title"),
                        "report_tag": item["document"].get("report_tag"),
                        "report_second_source": item["document"].get(
                            "report_second_source"
                        ),
                        "publish_date": item["document"].get("publish_date"),
                    },
                )
                for item in candidates[:top_n]
            ],
            request_id=request_id,
        )

    async def _expand_neighbor_overlap(
        self,
        *,
        dataset_id: str,
        candidates: list[dict[str, Any]],
        aggregation: bool = False,
    ) -> None:
        """为命中 chunk 补相邻块内容，模拟 chunk 搭接。

        普通问题：命中块前后各拼接相邻块 10% 的文本；
        计数/汇总类问题：额外拼接完整的下一个 chunk，避免跨页表格或名单被截断。
        只扩展本次检索的证据文本，不修改 RAGFlow 存储；按文档拉取一次 chunk
        顺序，拉取失败时保持原证据不变。
        """

        by_document: dict[str, list[dict[str, Any]]] = {}
        for item in candidates:
            remote_doc_id = str(item["document"]["remote_document_id"])
            by_document.setdefault(remote_doc_id, []).append(item)
        for remote_doc_id, items in by_document.items():
            chunks: list[dict[str, Any]] = []
            try:
                # RAGFlow list_chunks 单页上限 100，按页拉全文档后再定位相邻块
                page = 1
                while page <= 10:
                    payload = await self.ragflow.list_chunks(
                        dataset_id, remote_doc_id, page=page, page_size=100
                    )
                    batch = payload.get("chunks") if isinstance(payload, dict) else None
                    if not isinstance(batch, list) or not batch:
                        break
                    chunks.extend(chunk for chunk in batch if isinstance(chunk, dict))
                    total = payload.get("total")
                    if isinstance(total, int) and len(chunks) >= total:
                        break
                    if len(batch) < 100:
                        break
                    page += 1
            except RagflowError as exc:
                logger.warning(
                    "检索相邻 chunk 拉取失败 doc=%s: %s", remote_doc_id, exc
                )
                continue
            if not chunks:
                continue
            ordered = self._ordered_chunks(chunks)
            index_by_id = {
                self._chunk_id(chunk): index
                for index, chunk in enumerate(ordered)
                if self._chunk_id(chunk)
            }
            # 聚合模式只对得分最高的 2 条做整块扩展，其余仍按 10% 邻接，控制上下文
            items = sorted(items, key=lambda item: item["score"], reverse=True)
            expanded = 0
            for item in items:
                index = index_by_id.get(item["chunk_id"])
                if index is None:
                    continue
                if aggregation and expanded < 2:
                    expanded += 1
                    # 跨页表格/名单可能连续占多个 chunk：最多再带 5 个，遇到下一节即停止
                    for offset in range(1, 6):
                        if index + offset >= len(ordered):
                            break
                        following = self._chunk_text(ordered[index + offset])
                        if not following:
                            continue
                        if _SECTION_HEADING_RE.search(following[:80]):
                            break
                        if len(item["snippet"]) + len(following) > AGGREGATION_SNIPPET_LIMIT:
                            break
                        item["snippet"] = f'{item["snippet"]}\n{following}'
                    continue
                parts: list[str] = []
                if index > 0:
                    previous = self._chunk_text(ordered[index - 1])
                    if previous:
                        parts.append(previous[-self._overlap_chars(previous) :])
                parts.append(item["snippet"])
                if index + 1 < len(ordered):
                    following = self._chunk_text(ordered[index + 1])
                    if following:
                        parts.append(following[: self._overlap_chars(following)])
                item["snippet"] = "\n".join(parts)

    @staticmethod
    def _chunk_id(chunk: Mapping[str, Any]) -> str:
        return str(chunk.get("chunk_id") or chunk.get("id") or "")

    @staticmethod
    def _chunk_text(chunk: Mapping[str, Any]) -> str:
        return str(
            chunk.get("content_with_weight") or chunk.get("content") or ""
        ).strip()

    @staticmethod
    def _overlap_chars(text: str) -> int:
        return max(1, int(len(text) * RETRIEVAL_NEIGHBOR_OVERLAP_RATIO))

    @staticmethod
    def _ordered_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """优先按 chunk_order_int/order_int 排序，字段缺失时保留接口顺序。"""

        key = next(
            (
                name
                for name in ("chunk_order_int", "order_int")
                if chunks
                and all(isinstance(item.get(name), (int, float)) for item in chunks)
            ),
            None,
        )
        return sorted(chunks, key=lambda item: item[key]) if key else chunks

    async def retrieve_session_evidence(
        self,
        *,
        session_id: str,
        principal: KnowledgePrincipal,
        question: str,
        top_n: int,
        request_id: str,
        metadata_filter: DocumentMetadataFilter | None = None,
    ) -> RetrieveResponse:
        """在会话已选择且仍被授权的知识库范围内检索。"""
        scope = await self.get_session_scope(
            session_id=session_id, principal=principal
        )
        if not scope.base_ids:
            return RetrieveResponse(evidence=[], request_id=request_id)
        results = await asyncio.gather(
            *(
                self._retrieve_base_evidence(
                    base_id=base_id,
                    principal=principal,
                    question=question,
                    document_ids=None,
                    top_n=top_n,
                    request_id=request_id,
                    metadata_filter=metadata_filter,
                )
                for base_id in scope.base_ids
            )
        )
        evidence: list[Evidence] = []
        warnings: list[str] = []
        for result in results:
            evidence.extend(result.evidence)
            warnings.extend(result.warnings)
        evidence.sort(key=lambda item: item.score, reverse=True)
        return RetrieveResponse(
            evidence=evidence[:top_n],
            warnings=list(dict.fromkeys(warnings)),
            request_id=request_id,
        )

    async def get_session_document_catalog(
        self,
        *,
        session_id: str,
        principal: KnowledgePrincipal,
    ) -> list[dict[str, Any]]:
        """返回所选知识库的安全文档清单（供聊天模型回答范围类问题）。"""
        scope = await self.get_session_scope(
            session_id=session_id, principal=principal
        )
        catalog: list[dict[str, Any]] = []
        for base_id in scope.base_ids:
            remaining = self.config.limits.max_documents_per_query - len(catalog)
            if remaining <= 0:
                break
            base, _ = await self._authorized_base(
                base_id, principal, AllowedAction.SELECT_FOR_SESSION
            )
            documents = await self._repo(
                self.repository.list_documents,
                base_id,
                limit=remaining,
                offset=0,
            )
            for document in documents:
                progress = document.get("progress")
                catalog.append(
                    {
                        "base_id": str(base["id"]),
                        "base_name": str(base["name"]),
                        "document_id": str(document["id"]),
                        "document_name": str(document["name"]),
                        "status": str(document["status"]),
                        "progress": (
                            float(progress)
                            if isinstance(progress, (int, float)) and 0 <= progress <= 1
                            else None
                        ),
                    }
                )
        return catalog

    # ---------------- 操作记录与会话范围 ----------------

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

    async def get_session_scope(
        self, *, session_id: str, principal: KnowledgePrincipal
    ) -> SessionKnowledgeScopeResponse:
        """读取会话知识范围，并剔除已失去权限的知识库。"""

        session = await self._repo(self.repository.db.get_session, session_id)
        if session is None or session.username != principal.username:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "会话不存在", status_code=404
            )
        base_ids = await self._repo(
            self.repository.get_session_scope,
            session_id=session_id,
            user_id=principal.user_id,
        )
        visible: list[str] = []
        for base_id in base_ids:
            try:
                await self._authorized_base(
                    base_id, principal, AllowedAction.SELECT_FOR_SESSION
                )
            except KnowledgeServiceError:
                continue
            visible.append(base_id)
        if visible != base_ids:
            await self._repo(
                self.repository.replace_session_scope,
                session_id=session_id,
                user_id=principal.user_id,
                base_ids=visible,
            )
        return SessionKnowledgeScopeResponse(session_id=session_id, base_ids=visible)

    async def replace_session_scope(
        self,
        *,
        session_id: str,
        principal: KnowledgePrincipal,
        base_ids: list[str],
    ) -> SessionKnowledgeScopeResponse:
        """替换会话知识范围：要求所选知识库向量模型一致。"""

        session = await self._repo(self.repository.db.get_session, session_id)
        if session is None or session.username != principal.username:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "会话不存在", status_code=404
            )
        embedding_models: set[str] = set()
        for base_id in base_ids:
            base, _ = await self._authorized_base(
                base_id, principal, AllowedAction.SELECT_FOR_SESSION
            )
            embedding_models.add(str(base["embedding_model"]))
        if len(embedding_models) > 1:
            raise KnowledgeServiceError(
                "KNOWLEDGE_EMBEDDING_MODEL_MISMATCH",
                "所选知识库的向量模型不一致",
                status_code=409,
            )
        saved = await self._repo(
            self.repository.replace_session_scope,
            session_id=session_id,
            user_id=principal.user_id,
            base_ids=base_ids,
        )
        return SessionKnowledgeScopeResponse(session_id=session_id, base_ids=saved)


__all__ = [
    "KnowledgeService",
    "KnowledgeServiceError",
    "KnowledgeDocumentBinary",
]
