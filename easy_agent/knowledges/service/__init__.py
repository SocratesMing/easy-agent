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
import hashlib
import io
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.chunks import (
    chunk_id,
)
from easy_agent.knowledges.core.config import (
    KnowledgeConfig,
    UPSTREAM_DOCUMENT_STATUS_MAPPING,
    UploadSecurityConfig,
)
from easy_agent.knowledges.core.file_validation import (
    UploadValidationError,
    canonical_content_type,
    validate_upload,
)
from easy_agent.knowledges.core.models import (
    AllowedAction,
    DocumentListResponse,
    DocumentMetadata,
    DocumentStatus,
    DocumentSummary,
    DocumentUploadAccepted,
    KnowledgeBaseListResponse,
    KnowledgeBaseRole,
    KnowledgeBaseStatus,
    KnowledgeBaseSummary,
    KnowledgeBaseVisibility,
    OperationListResponse,
    OperationPhase,
    OperationStatus,
    OperationType,
    PageInfo,
    TeamSpaceDepartmentSummary,
    TeamSpaceManagementCapability,
)
from easy_agent.knowledges.core.operations_repository import KnowledgeOperationsRepository
from easy_agent.knowledges.core.original_store import (
    LocalOriginalStore,
    OriginalReadHandle,
    OriginalStorageError,
)
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.service.contracts import (
    KnowledgeDocumentBinary,
    KnowledgeServiceError,
    _not_found,
)
from easy_agent.knowledges.service.folders import FolderMixin, _split_folder_segments
from easy_agent.knowledges.service.permissions import PermissionMixin
from easy_agent.knowledges.service.retrieval import (
    AGGREGATION_SNIPPET_LIMIT,
    RetrievalMixin,
)
from easy_agent.knowledges.service.sessions import SessionScopeMixin
from easy_agent.knowledges.service.summaries import SummaryMixin
from easy_agent.knowledges.ragflow import RagflowClient, RagflowError, RagflowNotFoundError
from easy_agent.utils.auth import is_super_admin

if TYPE_CHECKING:  # 仅供类型注解（api 与 service 相互引用，运行时无循环导入）
    from easy_agent.knowledges.core.models import KnowledgePrincipal

logger = logging.getLogger(__name__)

# 远端删除按批次调用，避免单次请求携带过多 document_id
REMOTE_DELETE_BATCH = 100

# RAGFlow 暂不支持 PowerPoint 解析；即使管理员误把扩展名加回白名单也给出可操作提示
UNSUPPORTED_PRESENTATION_EXTENSIONS = frozenset({"ppt", "pptx"})
UNSUPPORTED_PRESENTATION_MESSAGE = (
    "暂不支持 PPT/PPTX 格式上传，请转换为 PDF 或 DOCX 后再上传"
)

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
# 检索阈值 / 权重 / 候选数 / rerank 已迁移到 knowledge.retrieval
# （KnowledgeConfig.retrieval）；这里只保留与 chunk 扩写相关的固定参数。
# 上传安全校验参数（YAML 不再配置，代码内固定）
_UPLOAD_SECURITY = UploadSecurityConfig()
# 共享库解析状态刷新：并发拉取多篇文档，单篇慢请求只拖住自己，不阻塞同批其它文档。
_REFRESH_DOCUMENT_CONCURRENCY = 5
# 本地解析超时：绝对超时即失败终态；重试/恢复会清空计时重新开始。
_PARSE_TIMEOUT_ERROR_CODE = "KNOWLEDGE_PARSE_TIMEOUT"

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
def _map_upstream_status(run: object) -> str:
    return UPSTREAM_DOCUMENT_STATUS_MAPPING.get(str(run or "")) or DocumentStatus.UNKNOWN.value




# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------
class KnowledgeService(
    FolderMixin, PermissionMixin, RetrievalMixin, SessionScopeMixin, SummaryMixin
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
        return upstream_document_running(data, remote_id)

    @staticmethod
    def _match_upstream_document(
        documents: list[Any], remote_id: str
    ) -> Mapping[str, Any] | None:
        """从上游返回的文档列表中按 ID 精确取出本文档。

        不同 RAGFlow 版本的文档过滤参数名不一致；即使上游忽略过滤返回整页，
        也必须按 ID 匹配，避免同批文档都显示成列表首位（进度最慢那篇）的状态。
        """

        for document in documents:
            if (
                isinstance(document, Mapping)
                and str(document.get("id") or "") == remote_id
            ):
                return document
        return None

    # ---------------- 知识库 ----------------

    async def list_bases(
        self, principal: KnowledgePrincipal, *, page: int, page_size: int
    ) -> KnowledgeBaseListResponse:
        """分页列出用户可见的知识库，附带公共空间管理能力信息。"""

        is_admin = is_super_admin(principal.username)
        has_dept_grant = bool(principal.department_id) and await self._has_department_management_grant(
            principal, principal.department_id
        )
        can_manage_all = is_admin  # 仅超级管理员全局管理
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
        # 超级管理员需要有可选的在职部门才能建公共空间；知识库管理员固定在本部门创建
        can_create_team = has_dept_grant or (is_admin and bool(departments))
        candidates = await self._repo(
            self.repository.list_candidate_bases,
            user_id=principal.user_id,
            department_id=principal.department_id,
            include_all_team_spaces=can_manage_all,
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
                can_create=can_create_team,
                can_manage=can_manage_all,
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
        """创建知识库：公共空间需知识库管理员授权，且只能归属本部门；纯本地建库，走共享数据集。

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
            if not await self._can_create_team_base(principal):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_TEAM_SPACE_MANAGER_REQUIRED",
                    "当前账号未获得公共空间创建与管理权限，请联系 admin 或部门管理员配置",
                    status_code=403,
                )
            target_department_id = await self._resolve_team_department_id(
                principal, requested_department_id
            )
        # create_base 是纯本地操作，不在此处触发 RAGFlow 调用；
        # 知识库表的 embedding_model 字段留空——真实模型由共享数据集统一决定。
        # 如果未来支持"每个知识库独立 RAGFlow 数据集"，再回填此处。
        base = await self._repo(
            self.repository.create_base,
            name=name,
            description=description,
            space_type=visibility.value,
            owner_user_id=principal.user_id,
            department_id=target_department_id,
            embedding_model="",
            # 新库没有历史文档：上传时就会写 base_id 元数据，直接走 meta 检索
            meta_backfilled=1,
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

    async def _require_document(
        self, document_id: str, *, include_deleted: bool = True
    ) -> dict[str, Any]:
        """读取文档记录；不存在（或已软删除且 include_deleted=False）按 404 处理。"""

        document = await self._repo(self.repository.get_document, document_id)
        if document is None or (
            not include_deleted
            and str(document.get("status")) == DocumentStatus.DELETED.value
        ):
            raise _not_found()
        return document

    async def _authorized_document(
        self,
        document_id: str,
        principal: KnowledgePrincipal,
        action: AllowedAction = AllowedAction.VIEW,
        *,
        include_deleted: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any], KnowledgeBaseRole]:
        """读取文档并校验其所属知识库的操作权限，返回 (文档, 库记录, 角色)。"""

        document = await self._require_document(
            document_id, include_deleted=include_deleted
        )
        base, role = await self._authorized_base(
            str(document["base_id"]), principal, action
        )
        return document, base, role

    async def refresh_base_documents(self, base: Mapping[str, Any]) -> None:
        """用上游文档状态刷新本地投影；仅后台解析轮询 worker 调用。

        共享数据集（remote_dataset_id 为空）需先解析共享数据集 ID，存量自有
        数据集直接用自己的 remote_dataset_id；随后逐篇查询本库
        pending/processing 文档。终态文档不再轮询，稳态零上游调用，也不受
        上游列表分页（page_size 上限 100）影响。
        """

        remote_dataset_id = str(base.get("remote_dataset_id") or "").strip()
        if remote_dataset_id:
            await self._refresh_documents_by_id(base, remote_dataset_id)
            return
        try:
            dataset_id = await self._dataset_id_for(base)
        except RagflowError as exc:
            logger.warning(
                "共享数据集解析失败 base=%s: %s", base.get("id"), exc
            )
            return
        await self._refresh_documents_by_id(base, dataset_id)

    async def _refresh_documents_by_id(
        self, base: Mapping[str, Any], dataset_id: str
    ) -> None:
        """逐篇轮询本库 pending/processing 文档的上游状态。"""

        active_docs = await self._repo(
            self.repository.list_refreshable_documents,
            str(base["id"]),
            limit=self.config.limits.max_documents_per_query,
        )
        semaphore = asyncio.Semaphore(_REFRESH_DOCUMENT_CONCURRENCY)

        async def refresh_one(local: Mapping[str, Any]) -> None:
            remote_id = str(local.get("remote_document_id") or "").strip()
            if not remote_id:
                return
            async with semaphore:
                try:
                    upstream = await self.ragflow.list_documents(
                        dataset_id, document_id=remote_id
                    )
                    docs = upstream.get("docs") if isinstance(upstream, dict) else None
                    if not isinstance(docs, list) or not docs:
                        return
                    upstream_doc = self._match_upstream_document(docs, remote_id)
                    if upstream_doc is not None:
                        await self._apply_upstream_doc_status(local, upstream_doc)
                except RagflowError as exc:
                    logger.warning(
                        "刷新文档状态失败 base=%s doc=%s: %s",
                        base.get("id"), local.get("id"), exc,
                    )
                except Exception as exc:  # noqa: BLE001 - 单篇失败不能拖累同批其它文档
                    logger.warning(
                        "刷新文档状态异常 base=%s doc=%s: %s",
                        base.get("id"), local.get("id"), exc,
                    )

        await asyncio.gather(*(refresh_one(local) for local in active_docs))

    async def _apply_upstream_doc_status(
        self, local: Mapping[str, Any], upstream_doc: Mapping[str, Any]
    ) -> None:
        """按上游文档状态更新本地投影（状态/进度/超时判定/操作记录）。"""
        # 本地已暂停的文档不受上游覆盖：stop 后上游 run 回落 UNSTART，
        # 不拦截会被刷新循环拉回 pending，暂停态就丢了
        if str(local.get("status")) == DocumentStatus.PAUSED.value:
            return
        status = DocumentStatus(_map_upstream_status(upstream_doc.get("run")))
        progress = self._normalized_progress(upstream_doc.get("progress"))
        failure_message = str(upstream_doc.get("progress_msg") or "文档解析失败")
        now_iso = datetime.now(UTC).isoformat()
        parse_started_at = local.get("parse_started_at")
        if status == DocumentStatus.PROCESSING and not parse_started_at:
            # 首次观察到上游真正开始解析：超时从此处计时，不含排队时间
            parse_started_at = now_iso
        parse_started_changed = bool(
            parse_started_at and parse_started_at != local.get("parse_started_at")
        )
        # 超时判定先于“无变化跳过”：进度停滞的文档也必须能被发现
        if await self._fail_parse_timeout(local, status, parse_started_at):
            return
        changed = (
            str(local.get("status")) != status.value
            or local.get("progress") != progress
            or parse_started_changed
        )
        # 状态、进度与解析计时均无变化：跳过后续 DB 写入（稳态轮询省往返）
        if not changed:
            return
        await self._repo(
            self.repository.update_document,
            local["id"],
            **self._document_status_values(
                status, progress, parse_started_at, parse_started_changed, failure_message
            ),
        )
        await self._repo(
            self.repository.update_active_document_operations,
            str(local["id"]),
            **self._operation_status_values(status, progress, failure_message),
        )
        if (
            status == DocumentStatus.READY
            and str(local.get("status")) != DocumentStatus.READY.value
        ):
            # 仅在新文档首次解析完成时写入 chunk 关键词；历史已 READY 文档
            # 不会再次触发，批量回填另见 backfill_base_meta 脚本。
            await self._apply_chunk_keywords(local)

    async def _fail_parse_timeout(
        self,
        local: Mapping[str, Any],
        status: DocumentStatus,
        parse_started_at: object,
    ) -> bool:
        """解析超时则写失败终态并返回 True（先于“无变化跳过”判定）。"""

        timeout_seconds = float(self.config.limits.parse_timeout_seconds or 0)
        is_sync_source = str(local.get("source") or "") == "source_sync"
        if not (
            status == DocumentStatus.PROCESSING
            and not is_sync_source
            and timeout_seconds > 0
            and parse_started_at
        ):
            return False
        try:
            started = datetime.fromisoformat(str(parse_started_at))
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            elapsed = (datetime.now(UTC) - started).total_seconds()
        except (TypeError, ValueError):
            elapsed = 0.0
        if elapsed <= timeout_seconds:
            return False
        timeout_message = (
            f"解析超过 {int(timeout_seconds)} 秒仍未完成，请拆分文件后重试"
        )
        await self._repo(
            self.repository.update_document,
            str(local["id"]),
            status=DocumentStatus.FAILED.value,
            progress=None,
            error_code=_PARSE_TIMEOUT_ERROR_CODE,
            error_message=timeout_message,
        )
        await self._repo(
            self.repository.update_active_document_operations,
            str(local["id"]),
            phase=OperationPhase.FAILED.value,
            status=OperationStatus.FAILED.value,
            progress=None,
            retryable=True,
            error_code=_PARSE_TIMEOUT_ERROR_CODE,
            error_message=timeout_message,
        )
        return True

    @staticmethod
    def _document_status_values(
        status: DocumentStatus,
        progress: object,
        parse_started_at: object,
        parse_started_changed: bool,
        failure_message: str,
    ) -> dict[str, object]:
        """文档投影字段：状态/进度/计时 + 各终态的错误码与错误信息。"""

        values: dict[str, object] = {"status": status.value, "progress": progress}
        if parse_started_changed:
            values["parse_started_at"] = parse_started_at
        if status == DocumentStatus.FAILED:
            values.update(
                {
                    "progress": None,
                    "error_code": "KNOWLEDGE_PARSE_FAILED",
                    "error_message": failure_message,
                }
            )
        elif status == DocumentStatus.READY:
            values.update(
                {"progress": 1.0, "error_code": None, "error_message": None}
            )
        elif status == DocumentStatus.CANCELLED:
            values.update(
                {"progress": None, "error_code": None, "error_message": None}
            )
        elif status in {DocumentStatus.PENDING, DocumentStatus.PROCESSING}:
            values.update({"error_code": None, "error_message": None})
        return values

    @staticmethod
    def _operation_status_values(
        status: DocumentStatus, progress: object, failure_message: str
    ) -> dict[str, object]:
        """操作投影字段：与文档终态保持一致的操作阶段与重试标记。"""

        values: dict[str, object] = {"progress": progress}
        if status == DocumentStatus.READY:
            values.update(
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
            values.update(
                {
                    "phase": OperationPhase.FAILED.value,
                    "status": OperationStatus.FAILED.value,
                    "progress": None,
                    "retryable": True,
                    "error_code": "KNOWLEDGE_PARSE_FAILED",
                    "error_message": failure_message,
                }
            )
        elif status == DocumentStatus.CANCELLED:
            values.update(
                {
                    "phase": OperationPhase.COMPLETED.value,
                    "status": OperationStatus.CANCELLED.value,
                    "progress": None,
                    "retryable": True,
                    "error_code": None,
                    "error_message": None,
                }
            )
        return values

    async def _apply_chunk_keywords(self, document: Mapping[str, Any]) -> None:
        """把业务元数据写入该文档各 chunk 的 important_keywords。

        标题/标签进入关键词通道后，检索对专有名词、机构名、报告名的命中率
        更高。失败只记日志，不影响文档解析状态。
        """

        keywords: list[str] = []
        for key in ("report_title", "report_tag", "report_second_source"):
            value = str(document.get(key) or "").strip()
            if value and value not in keywords:
                keywords.append(value[:127])
        keywords = keywords[:8]
        remote_id = str(document.get("remote_document_id") or "").strip()
        if not keywords or not remote_id:
            return
        base = await self._repo(self.repository.get_base, str(document["base_id"]))
        if base is None:
            return
        try:
            dataset_id = await self._dataset_id_for(base)
        except (RagflowError, KnowledgeServiceError) as exc:
            logger.warning(
                "写入 chunk 关键词前解析数据集失败 doc=%s: %s", remote_id, exc
            )
            return
        semaphore = asyncio.Semaphore(_REFRESH_DOCUMENT_CONCURRENCY)

        async def _update(chunk: Mapping[str, Any]) -> None:
            chunk_key = chunk_id(chunk)
            if not chunk_key:
                return
            async with semaphore:
                try:
                    await self.ragflow.update_chunk(
                        dataset_id,
                        remote_id,
                        chunk_key,
                        important_keywords=keywords,
                    )
                except RagflowError as exc:
                    logger.warning(
                        "写入 chunk 关键词失败 doc=%s chunk=%s: %s",
                        remote_id,
                        chunk_key,
                        exc,
                    )

        try:
            page = 1
            while page <= 10:
                payload = await self.ragflow.list_chunks(
                    dataset_id, remote_id, page=page, page_size=100
                )
                batch = payload.get("chunks") if isinstance(payload, dict) else None
                if not isinstance(batch, list) or not batch:
                    return
                await asyncio.gather(
                    *(_update(chunk) for chunk in batch if isinstance(chunk, dict))
                )
                total = payload.get("total")
                if isinstance(total, int) and page * 100 >= total:
                    return
                if len(batch) < 100:
                    return
                page += 1
        except RagflowError as exc:
            logger.warning("写入 chunk 关键词失败 doc=%s: %s", remote_id, exc)

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
        """分页列出知识库文档。

        只读取本地投影，不等待上游全量刷新：解析状态由后台
        ``KnowledgeParsePollWorker`` 按文档逐个拉取并即时落库。这样同一批
        上传的文档不会因为某个慢任务而卡住整页状态更新。

        关键字搜索与状态筛选始终覆盖整个知识库：传了 ``query`` 或 ``status``
        时忽略 ``folder_id`` / ``direct_only``，避免结果被当前目录层级限制。
        """

        query = (query or "").strip() or None
        if query or status:
            folder_id = None
            direct_only = False
        _, role = await self._authorized_base(base_id, principal)
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
        """上传远端 → 配置解析器 → 触发解析，返回 pending 投影行。

        本方法只把文档推进到 ``pending``（已提交上游、等待执行）；真正的
        ``processing`` 由解析轮询在观察到上游 RUNNING 后写入。上传与重试共用
        该流程；失败时统一标记文档/操作为失败并抛出映射后的业务错误。
        调用方负责关闭 source。
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
                progress=None,
                error_code=None,
                error_message=None,
                parse_started_at=None,
            )
            await self._repo(
                self.repository.update_operation,
                operation_id,
                phase=OperationPhase.CONFIGURING.value,
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
            operation = await self._repo(
                self.repository.update_operation,
                operation_id,
                phase=OperationPhase.PARSING.value,
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
            progress=None,
            parse_started_at=None,
            error_code=code,
            error_message=failure_message,
        )
        await self._repo(
            self.repository.update_operation,
            operation_id,
            phase=OperationPhase.FAILED.value,
            status=OperationStatus.FAILED.value,
            progress=None,
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

    @staticmethod
    def _sha256_digest(source: BinaryIO) -> str:
        """流式计算原文 sha256，并把指针复位到开头。"""

        source.seek(0)
        digest = hashlib.sha256()
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
        source.seek(0)
        return digest.hexdigest()

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
        folder_id = await self._resolve_upload_folder(base_id, folder_id, relative_path)
        safe_name = await self._validate_upload_request(base_id, filename, size_bytes)
        content_type = await self._validate_upload_content(
            safe_name, content_type, size_bytes, content
        )
        # 同库重复内容拦截（仅人工上传；文档 SQL 同步有自己的业务幂等键）
        if metadata is None:
            await self._reject_duplicate_content(base_id, content)

        document = await self._repo(
            self.repository.create_document,
            base_id=base_id,
            folder_id=folder_id,
            name=safe_name,
            content_type=content_type,
            size_bytes=size_bytes,
            created_by=principal.user_id,
            status=DocumentStatus.PENDING.value,
            source=metadata.source if metadata else None,
            source_record_id=metadata.source_record_id if metadata else None,
            batch_id=metadata.batch_id if metadata else None,
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
        storage_key = self.original_store.build_key(
            base_id, str(document["id"]), safe_name
        )
        await self._repo(
            self.repository.create_original_object,
            document_id=str(document["id"]),
            provider=self.original_store.provider,
            storage_key=storage_key,
            original_name=safe_name,
            content_type=content_type,
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
                progress=None,
                parse_started_at=None,
                error_code=exc.code,
                error_message="原文存储失败，未提交知识底座",
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                progress=None,
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

    async def _resolve_upload_folder(
        self, base_id: str, folder_id: str | None, relative_path: str | None
    ) -> str | None:
        """校验目标文件夹；文件夹上传时按相对路径逐级 find-or-create。"""

        if folder_id:
            await self._require_folder(base_id, folder_id)
        if relative_path:
            return await self._ensure_folder_dirs(
                base_id, folder_id, _split_folder_segments(relative_path)[:-1]
            )
        return folder_id

    async def _validate_upload_request(
        self, base_id: str, filename: str, size_bytes: int
    ) -> str:
        """文档数量/大小/文件名/后缀校验，返回安全文件名。

        校验顺序即错误码优先级：文档上限 → 文件大小 → 文件名 → 后缀。
        """

        max_per_base = self.config.limits.max_documents_per_base
        if (
            max_per_base > 0
            and await self._repo(self.repository.count_documents, base_id)
            >= max_per_base
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_LIMIT",
                f"知识库文档数量已达上限（{max_per_base}）",
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
        if extension in UNSUPPORTED_PRESENTATION_EXTENSIONS:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TYPE_NOT_ALLOWED",
                UNSUPPORTED_PRESENTATION_MESSAGE,
                status_code=422,
            )
        if extension not in {
            value.lower() for value in self.config.limits.allowed_extensions
        }:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TYPE_NOT_ALLOWED",
                f"不支持该文件类型（后缀 .{extension or '无'}）",
                status_code=422,
            )
        return safe_name

    async def _validate_upload_content(
        self,
        safe_name: str,
        content_type: str,
        size_bytes: int,
        content: BinaryIO,
    ) -> str:
        """运行上传安全校验，并返回按扩展名归一后的内容类型。"""

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
        # 浏览器可能按宿主注册表报出旧版/新版 Office MIME；落库与上游统一用
        # 扩展名对应的标准 MIME，避免 RAGFlow 按声明类型拒收。
        return canonical_content_type(safe_name, content_type)

    async def _reject_duplicate_content(
        self, base_id: str, content: BinaryIO
    ) -> None:
        """同库重复内容拒绝：已存在相同 sha256 的未删除文档时直接 409。

        哈希放线程池，避免大文件阻塞事件循环。
        """

        digest = await asyncio.to_thread(self._sha256_digest, content)
        duplicate = await self._repo(
            self.repository.find_duplicate_document, base_id, digest
        )
        if duplicate is not None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_DUPLICATE_DOCUMENT",
                f"知识库中已存在相同内容的文档：{duplicate['name']}",
                status_code=409,
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

        document, _, role = await self._authorized_document(
            document_id, principal, AllowedAction.MOVE_DOCUMENT, include_deleted=False
        )
        if folder_id:
            await self._require_folder(str(document["base_id"]), folder_id)
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
        document, base, _ = await self._authorized_document(
            document_id, principal, AllowedAction.DOWNLOAD, include_deleted=False
        )
        original = await self._repo(
            self.repository.get_original_object, document_id
        )
        if original is not None and str(original.get("status")) == "available":
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
                # 原文访问一律留痕（不受 audit.enabled 控制，与并表前一致）。
                await self._repo(
                    self.operations_repository.record_audit,
                    request_id=request_id,
                    actor_user_id=principal.user_id,
                    actor_username=principal.username,
                    action="knowledge.original.download",
                    object_type="document",
                    object_id=document_id,
                    base_id=str(document.get("base_id") or "") or None,
                    outcome="opened",
                    details={"session_id": "", "agent_id": "api"},
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
        document = await self._require_document(document_id)
        if str(document.get("status")) == DocumentStatus.DELETED.value:
            return
        base, _role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.DELETE_DOCUMENT
        )
        remote_id = str(document.get("remote_document_id") or "").strip()
        if remote_id:
            await self._delete_remote_documents(
                await self._dataset_id_for(base), [remote_id]
            )
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
            error = await self._delete_remote_documents_quietly(
                dataset_id, [str(row["remote_document_id"])]
            )
            if error is not None:
                logger.warning(
                    "清理到期文档远端副本失败（下轮重试） | doc=%s: %s",
                    row["document_id"],
                    error,
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
        document, base, _role = await self._authorized_document(
            document_id, principal, AllowedAction.RETRY_DOCUMENT
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
            parse_started_at=None,
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
        document, base, _role = await self._authorized_document(
            document_id, principal, AllowedAction.RETRY_DOCUMENT
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
            parse_started_at=None,
            error_code=None,
            error_message=None,
        )

    async def retry_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> DocumentUploadAccepted:
        """重试失败文档：删除上游旧副本后，用本地原文重新上传并解析。"""
        document, base, role = await self._authorized_document(
            document_id, principal, AllowedAction.RETRY_DOCUMENT
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
        failure_message = "文档重试失败"
        original_error_message = "文档原文件不可用"
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
            # 注意：不在这里删上游旧副本，也不清 remote_document_id。
            # 必须等 _submit_to_ragflow 成功后再做，否则一旦中途抛异常，
            # 旧副本已被删除、remote_id 也丢了，下次重试既没有本地原文
            # 又无法从 RAGFlow 下载，只能报"文档原文件不可用" (bug 根因)。
        except (RagflowError, KeyError, OriginalStorageError) as exc:
            await self._fail_submit(
                document_id=document_id,
                operation_id=str(operation["id"]),
                exc=exc,
                failure_message=failure_message,
                original_error_message=original_error_message,
            )
        try:
            document, operation = await self._submit_to_ragflow(
                base=base,
                document=document,
                dataset_id=dataset_id,
                source=source,
                operation_id=str(operation["id"]),
                failure_message=failure_message,
                original_error_message=original_error_message,
            )
        finally:
            if source is not None:
                source.close()
        assert document is not None and operation is not None
        # 新文档已提交成功，再尽力清理旧副本。删除失败不影响新文档，
        # 只记 warning；后台 purge_deleted_documents 兜底。
        if remote_id and str(document.get("remote_document_id") or "") != str(remote_id):
            error = await self._delete_remote_documents_quietly(
                dataset_id, [str(remote_id)]
            )
            if error is not None:
                logger.warning(
                    "重试后清理 RAGFlow 旧副本失败（不影响新文档） | doc=%s "
                    "dataset=%s old_remote=%s code=%s",
                    document_id,
                    dataset_id,
                    remote_id,
                    getattr(error, "code", type(error).__name__),
                )
        return DocumentUploadAccepted(
            document=self._document_summary(document, role, request_id),
            operation=self._operation_summary(operation, request_id),
        )

    # ---------------- 检索与问答 ----------------

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



def upstream_document_running(payload: Any, remote_id: str) -> bool:
    """上游文档响应中，指定文档的解析是否已实际启动（RUNNING/DONE）。

    ``payload`` 兼容 ``{"docs": [...]}`` 与直接列表；优先按 ID 精确匹配，
    上游忽略过滤只返回一条时退化为该条。供解析触发竞态兜底与迁移脚本共用。
    """

    docs = payload.get("docs") if isinstance(payload, dict) else payload
    if not isinstance(docs, list) or not docs:
        return False
    matched = KnowledgeService._match_upstream_document(docs, remote_id)
    if matched is None and len(docs) == 1:
        matched = docs[0]
    if not isinstance(matched, Mapping):
        return False
    return str(matched.get("run", "")).upper() in {"RUNNING", "DONE"}


__all__ = [
    "AGGREGATION_SNIPPET_LIMIT",
    "KnowledgeService",
    "KnowledgeServiceError",
    "KnowledgeDocumentBinary",
    "upstream_document_running",
]
