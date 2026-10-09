"""新版知识库核心业务服务（对照旧版 service.py 精简重写）。

主要裁剪：queue/inline 双模式（新版上传/删除全部同步内联）、原文存储的
NAS 多实现工厂（收敛为本地磁盘存储）、completed_document_probe 完成度
探测（依赖旧版兼容配置）。删除为软删除：本地保留记录与原文，远端
（Ragflow）副本暂存 SOFT_DELETE_RETENTION_DAYS 后由清理 worker
（KnowledgePurgeWorker）到期删除，恢复端点已随前端调用面裁剪移除。

检索走 Ragflow 标准检索接口（retrieve，POST /api/v1/retrieval）。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, Callable

from starlette.concurrency import run_in_threadpool

from .config import (
    KnowledgeConfig,
    UPSTREAM_DOCUMENT_STATUS_MAPPING,
    UploadSecurityConfig,
)
from .file_validation import UploadValidationError, validate_upload
from .models import (
    AllowedAction,
    DocumentListResponse,
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
    PermissionEntry,
    PermissionListResponse,
    PermissionSubject,
    PermissionSubjectListResponse,
    RetrieveResponse,
    SessionKnowledgeScopeResponse,
    TeamSpaceDepartmentSummary,
    TeamSpaceManagementCapability,
)
from .operations_repository import KnowledgeOperationsRepository
from .ragflow import RagflowClient, RagflowError, RagflowNotFoundError
from .repository import KnowledgeRepository

if TYPE_CHECKING:  # 仅供类型注解（api 与 service 相互引用，运行时无循环导入）
    from .api import KnowledgePrincipal

logger = logging.getLogger(__name__)

# 上游解析超过该时长仍未完成时标记失败（与旧版 parsing_poll 默认一致）
PARSE_TIMEOUT_SECONDS = 1800.0
# 软删除保留期（天）：远端副本暂存天数，到期由 KnowledgePurgeWorker 清理
SOFT_DELETE_RETENTION_DAYS = 30
# 证据最低得分阈值（与旧版 quality_baseline 默认一致，0.0 即不过滤）
MINIMUM_EVIDENCE_SCORE = 0.0
# 本地原文根目录（运行期目录，gitignored）
DEFAULT_ORIGINALS_ROOT = Path("data") / "knowledge_originals"

# 数据集创建与解析策略固定参数（v2 扁平配置不再承载，代码内固定）
DATASET_PERMISSION = "me"
CHUNK_METHOD = "naive"
PARSER_CONFIG: dict[str, object] = {
    "chunk_token_num": 512,
    "layout_recognize": "DeepDOC",
    "html4excel": False,
    "delimiter": "\n!?;。；！？",
    "raptor": {"use_raptor": False},
}
# 检索固定参数（与旧版 defaults.retrieval 默认值一致）
RETRIEVAL_SIMILARITY_THRESHOLD = 0.2
RETRIEVAL_VECTOR_SIMILARITY_WEIGHT = 0.3
RETRIEVAL_TOP_K = 1024
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

_ROLE_RANK = {
    KnowledgeBaseRole.VIEWER: 1,
    KnowledgeBaseRole.MAINTAINER: 2,
    KnowledgeBaseRole.MANAGER: 3,
}

_VIEWER_ACTIONS = frozenset(
    {
        AllowedAction.VIEW,
        AllowedAction.ASK,
        AllowedAction.DOWNLOAD,
        AllowedAction.SELECT_FOR_SESSION,
    }
)
_MAINTAINER_ACTIONS = _VIEWER_ACTIONS | frozenset(
    {
        AllowedAction.CREATE_FOLDER,
        AllowedAction.EDIT_FOLDER,
        AllowedAction.DELETE_FOLDER,
        AllowedAction.UPLOAD,
        AllowedAction.MOVE_DOCUMENT,
        AllowedAction.DELETE_DOCUMENT,
        AllowedAction.RETRY_DOCUMENT,
    }
)
_MANAGER_ACTIONS = _MAINTAINER_ACTIONS | frozenset(
    {
        AllowedAction.EDIT_BASE,
        AllowedAction.MANAGE_PERMISSIONS,
        AllowedAction.DELETE_BASE,
    }
)


# ---------------------------------------------------------------------------
# 本地原文存储（旧版 storage/filesystem.py 的精简版）
# ---------------------------------------------------------------------------
class OriginalStorageError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class OriginalReadHandle:
    """本地原文只读句柄：支持分段读取与二进制打开。"""

    def __init__(self, path: Path, size_bytes: int, *, chunk_size: int = 1024 * 1024):
        self.path = path
        self.size_bytes = size_bytes
        self._chunk_size = chunk_size

    def open_binary(self) -> BinaryIO:
        return self.path.open("rb")

    def iter_bytes(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        remaining_end = self.size_bytes - 1 if end is None else min(end, self.size_bytes - 1)
        if start < 0 or remaining_end < start:
            return
        with self.path.open("rb") as source:
            source.seek(start)
            position = start
            while position <= remaining_end:
                chunk = source.read(min(self._chunk_size, remaining_end - position + 1))
                if not chunk:
                    break
                position += len(chunk)
                yield chunk


class LocalOriginalStore:
    """本地磁盘原文存储：原子写入与路径穿越防护。"""

    provider = "filesystem"
    _SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,255}$")
    _SAFE_EXTENSION = re.compile(r"^[A-Za-z0-9]{1,16}$")

    def __init__(self, root: str | Path = DEFAULT_ORIGINALS_ROOT):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _safe_id(cls, value: str, label: str) -> str:
        if not cls._SAFE_ID.fullmatch(value):
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_INVALID_KEY", f"invalid {label}", retryable=False
            )
        return value

    def build_key(self, base_id: str, document_id: str, filename: str) -> str:
        base_id = self._safe_id(base_id, "base id")
        document_id = self._safe_id(document_id, "document id")
        suffix = Path(filename).suffix.lower().lstrip(".")
        extension = suffix if self._SAFE_EXTENSION.fullmatch(suffix) else "bin"
        return str(PurePosixPath("v1", base_id[:2], base_id, document_id, f"original.{extension}"))

    def _path(self, storage_key: str) -> Path:
        pure = PurePosixPath(storage_key)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_INVALID_KEY", "invalid storage key", retryable=False
            )
        candidate = (self.root / Path(*pure.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_INVALID_KEY", "storage key escapes root", retryable=False
            ) from exc
        if candidate.is_symlink() or any(
            (self.root / Path(*pure.parts[: index + 1])).is_symlink()
            for index in range(len(pure.parts) - 1)
        ):
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_UNSAFE_PATH", "symbolic links are not allowed", retryable=False
            )
        return candidate

    def put_atomic(self, storage_key: str, source: BinaryIO, expected_size: int) -> tuple[int, str]:
        """原子写入原文并返回 (字节数, sha256)。"""
        target = self._path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                output.flush()
            if size != expected_size:
                raise OriginalStorageError(
                    "ORIGINAL_STORAGE_SIZE_MISMATCH",
                    "stored original size does not match upload",
                    retryable=False,
                )
            if target.exists():
                # 同一文档重复上传：内容一致则幂等成功，否则拒绝覆盖
                existing = self.open(storage_key)
                existing_digest = hashlib.sha256()
                for chunk in existing.iter_bytes():
                    existing_digest.update(chunk)
                if existing.size_bytes != size or existing_digest.hexdigest() != digest.hexdigest():
                    raise OriginalStorageError(
                        "ORIGINAL_STORAGE_CONFLICT",
                        "original object already exists",
                        retryable=False,
                    )
                temporary.unlink(missing_ok=True)
            else:
                temporary.replace(target)
            return size, digest.hexdigest()
        except OriginalStorageError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_WRITE_FAILED", "原文存储写入失败", retryable=True
            ) from exc

    def open(self, storage_key: str) -> OriginalReadHandle:
        path = self._path(storage_key)
        try:
            stat = path.stat()
        except OSError as exc:
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_NOT_AVAILABLE", "原文暂不可用", retryable=True
            ) from exc
        if not path.is_file() or path.is_symlink():
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_UNSAFE_PATH", "原文存储路径无效", retryable=False
            )
        return OriginalReadHandle(path, stat.st_size)

    def health(self) -> bool:
        try:
            probe = self.root / f".health-{uuid.uuid4().hex}"
            probe.write_bytes(b"ok")
            probe.unlink()
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# 领域辅助
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KnowledgeDocumentBinary:
    size_bytes: int
    content_type: str
    sha256: str | None
    iterator_factory: Callable[[int, int | None], Iterator[bytes]]

    def iter_bytes(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        return self.iterator_factory(start, end)


def _as_role(value: object) -> KnowledgeBaseRole | None:
    try:
        return KnowledgeBaseRole(str(value))
    except ValueError:
        return None


def _matching_permission_roles(
    principal: KnowledgePrincipal,
    permissions: Iterable[Mapping[str, object]],
) -> list[KnowledgeBaseRole]:
    roles: list[KnowledgeBaseRole] = []
    for permission in permissions:
        subject_type = str(permission.get("subject_type", ""))
        subject_id = str(permission.get("subject_id", ""))
        matches_user = subject_type == "user" and subject_id == principal.user_id
        matches_department = (
            subject_type == "department"
            and bool(principal.department_id)
            and subject_id == principal.department_id
        )
        if matches_user or matches_department:
            role = _as_role(permission.get("role"))
            if role is not None:
                roles.append(role)
    return roles


def effective_role(
    base: Mapping[str, object],
    principal: KnowledgePrincipal,
    permissions: Iterable[Mapping[str, object]],
    *,
    team_viewer: bool = False,
) -> KnowledgeBaseRole | None:
    """计算用户在单个知识库上的最强角色。

    公共（team）空间不再默认同部门可见，须通过显式授权或公共空间
    查看/管理白名单获得访问。
    """
    if str(base.get("owner_user_id", "")) == principal.user_id:
        return KnowledgeBaseRole.MANAGER
    visibility = KnowledgeBaseVisibility(str(base["space_type"]))
    if visibility == KnowledgeBaseVisibility.PERSONAL:
        return None
    candidates: list[KnowledgeBaseRole] = []
    if visibility == KnowledgeBaseVisibility.TEAM and team_viewer:
        candidates.append(KnowledgeBaseRole.VIEWER)
    candidates.extend(_matching_permission_roles(principal, permissions))
    if not candidates:
        return None
    return max(candidates, key=_ROLE_RANK.__getitem__)


def allowed_actions(role: KnowledgeBaseRole | None) -> list[AllowedAction]:
    if role == KnowledgeBaseRole.MANAGER:
        actions = _MANAGER_ACTIONS
    elif role == KnowledgeBaseRole.MAINTAINER:
        actions = _MAINTAINER_ACTIONS
    elif role == KnowledgeBaseRole.VIEWER:
        actions = _VIEWER_ACTIONS
    else:
        actions = frozenset()
    return sorted(actions, key=lambda item: item.value)


class KnowledgeServiceError(RuntimeError):
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


# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------
class KnowledgeService:
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
        self._team_viewer_cache: dict[str, bool] = {}
        self.original_store = (
            original_store if original_store is not None else LocalOriginalStore()
        )
        # refresh_base_documents 节流：避免页面反复加载触发全量上游刷新
        self._last_refresh_at: dict[str, float] = {}
        self._refresh_min_interval = 15.0

    async def _repo(self, method, *args, **kwargs):
        return await run_in_threadpool(method, *args, **kwargs)

    @staticmethod
    def _upstream_error(exc: RagflowError) -> KnowledgeServiceError:
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
        """查找或创建共享数据集，返回其 ID（进程级缓存）。

        创建参数与旧版按库建数据集保持一致（embedding 模型取知识库配置，
        缺省用 Ragflow 租户默认模型）。多进程并发创建的竞态由"失败后重查"
        兜底：Ragflow 数据集名称全局唯一，后创建方会收到错误，重查即可复用。
        """
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
        if principal.username == "admin":
            return True
        if principal.user_id not in self._team_manager_cache:
            self._team_manager_cache[principal.user_id] = await self._repo(
                self.repository.is_team_space_manager, principal.user_id
            )
        return self._team_manager_cache[principal.user_id]

    async def _has_team_viewer_grant(self, principal: KnowledgePrincipal) -> bool:
        if principal.username == "admin":
            return True
        if principal.user_id not in self._team_viewer_cache:
            self._team_viewer_cache[principal.user_id] = await self._repo(
                self.repository.is_team_space_viewer, principal.user_id
            )
        return self._team_viewer_cache[principal.user_id]

    async def _effective_role(
        self,
        base: Mapping[str, object],
        principal: KnowledgePrincipal,
        permissions: Iterable[Mapping[str, object]],
    ) -> KnowledgeBaseRole | None:
        """在单库授权之上叠加公共空间管理/查看白名单（均限定本部门）。"""
        permission_rows = list(permissions)
        if str(base.get("space_type")) != KnowledgeBaseVisibility.TEAM.value:
            return effective_role(base, principal, permission_rows)
        if principal.username == "admin":
            return KnowledgeBaseRole.MANAGER
        same_department = bool(principal.department_id) and (
            principal.department_id == str(base.get("department_id") or "").strip()
        )
        has_manager_grant = await self._has_team_management_grant(principal)
        has_viewer_grant = same_department and (
            has_manager_grant or await self._has_team_viewer_grant(principal)
        )
        role = effective_role(base, principal, permission_rows, team_viewer=has_viewer_grant)
        if same_department and has_manager_grant:
            return KnowledgeBaseRole.MANAGER
        if str(base.get("owner_user_id", "")) == principal.user_id:
            explicit_roles = _matching_permission_roles(principal, permission_rows)
            return max([KnowledgeBaseRole.VIEWER, *explicit_roles], key=_ROLE_RANK.__getitem__)
        return role

    # ---------------- 摘要构造 ----------------

    async def _base_summary(
        self, base: Mapping[str, Any], role: KnowledgeBaseRole
    ) -> KnowledgeBaseSummary:
        count = await self._repo(self.repository.count_documents, str(base["id"]))
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
            index_status=DocumentStatus(str(document["status"])),
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
        )

    @staticmethod
    def _operation_summary(
        operation: Mapping[str, Any], request_id: str
    ) -> OperationSummary:
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
            batch_operation_id=None,
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
            include_all_team_spaces=is_admin,
        )
        visible: list[tuple[dict[str, Any], KnowledgeBaseRole]] = []
        for base in candidates:
            permissions = await self._repo(
                self.repository.list_permissions, str(base["id"])
            )
            role = await self._effective_role(base, principal, permissions)
            if role is not None:
                visible.append((base, role))
        start = (page - 1) * page_size
        items = [
            await self._base_summary(base, role)
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
    ) -> KnowledgeBaseSummary:
        del request_id
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
        base = await self._repo(
            self.repository.update_base,
            base["id"],
            status=KnowledgeBaseStatus.ACTIVE.value,
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
        """更新知识库名称与描述。

        上游数据集名称由 base_id 派生（不可变），新版行内 update_dataset
        契约亦不含 description，故本操作仅更新本地投影。
        """
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
        """软删除知识库：本地保留记录，其文档的远端副本暂存保留期后由
        清理 worker 统一删除。"""
        del request_id
        await self._authorized_base(base_id, principal, AllowedAction.DELETE_BASE)
        deleted_at = datetime.now(UTC)
        purge_after = deleted_at + timedelta(days=SOFT_DELETE_RETENTION_DAYS)
        await self._repo(
            self.repository.update_base,
            base_id,
            status=KnowledgeBaseStatus.DELETED.value,
            deleted_at=deleted_at.isoformat(),
            deleted_by=principal.user_id,
            purge_after=purge_after.isoformat(),
        )

    # ---------------- 文件夹 ----------------

    async def list_folders(
        self, base_id: str, principal: KnowledgePrincipal
    ) -> FolderListResponse:
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
        segments = [
            part.strip()
            for part in re.split(r"[\\/]+", relative_path)
            if part.strip()
        ]
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
            segments = [
                part.strip()
                for part in re.split(r"[\\/]+", str(raw_path or ""))
                if part.strip()
            ]
            if not segments:
                continue
            await self._ensure_folder_dirs(base_id, folder_id, segments)
            ensured += 1
        return FolderEnsureResponse(ensured=ensured)

    async def update_folder(
        self, *, folder_id: str, name: str, principal: KnowledgePrincipal
    ) -> FolderSummary:
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
        await self._authorized_base(
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
            ]
        )

    async def replace_permissions(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        items: list[PermissionEntry],
    ) -> PermissionListResponse:
        base, _ = await self._authorized_base(
            base_id, principal, AllowedAction.MANAGE_PERMISSIONS
        )
        if str(base["space_type"]) == KnowledgeBaseVisibility.PERSONAL.value and items:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PERSONAL_NOT_SHAREABLE",
                "个人空间知识库不可授权",
                status_code=409,
            )
        seen: set[tuple[str, str]] = set()
        payload: list[dict[str, str]] = []
        for item in items:
            key = (item.subject_type, item.subject_id)
            if key in seen:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_DUPLICATE_PERMISSION",
                    "授权主体重复",
                    status_code=409,
                )
            if item.subject_type == "user" and item.subject_id == base["owner_user_id"]:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_OWNER_PERMISSION_IMMUTABLE",
                    "所有者权限不可修改",
                    status_code=409,
                )
            seen.add(key)
            payload.append(item.model_dump(mode="json"))
        if (
            str(base["space_type"]) == KnowledgeBaseVisibility.TEAM.value
            and principal.username != "admin"
        ):
            existing_rows = await self._repo(self.repository.list_permissions, base_id)
            existing_elevated = {
                (
                    str(row["subject_type"]),
                    str(row["subject_id"]),
                    str(row["role"]),
                )
                for row in existing_rows
                if str(row["role"]) != KnowledgeBaseRole.VIEWER.value
            }
            requested_elevated = {
                (item["subject_type"], item["subject_id"], item["role"])
                for item in payload
                if item["role"] != KnowledgeBaseRole.VIEWER.value
            }
            if requested_elevated != existing_elevated:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_TEAM_ROLE_ADMIN_REQUIRED",
                    "仅 admin 可授予或变更团队知识库的维护者和管理员角色",
                    status_code=403,
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
            ]
        )

    async def find_permission_subjects(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        subject_type: str,
        query: str,
    ) -> PermissionSubjectListResponse:
        await self._authorized_base(
            base_id, principal, AllowedAction.MANAGE_PERMISSIONS
        )
        rows = await self._repo(
            self.repository.find_permission_subjects,
            subject_type=subject_type,
            query=query,
        )
        return PermissionSubjectListResponse(
            items=[PermissionSubject(subject_type=subject_type, **row) for row in rows]
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
        status = DocumentStatus(_map_upstream_status(upstream_doc.get("run")))
        progress = upstream_doc.get("progress")
        if not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
            progress = None
        # 状态与进度均无变化的文档直接跳过：稳态轮询时避免海量无效
        # DB 查询/写入（每篇文档否则要 4 次串行 DB 往返，几百篇就是分钟级）
        if (
            str(local.get("status")) == status.value
            and local.get("progress") == progress
        ):
            return
        latest_operation: Mapping[str, Any] | None = None
        if status in {DocumentStatus.PENDING, DocumentStatus.PROCESSING}:
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
            if elapsed > PARSE_TIMEOUT_SECONDS:
                timeout_message = (
                    f"解析超过 {int(PARSE_TIMEOUT_SECONDS)} 秒仍未完成，请拆分文件后重试"
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
        values: dict[str, object] = {"status": status.value, "progress": progress}
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
        idempotency_key: str | None = None,
        relative_path: str | None = None,
    ) -> DocumentUploadAccepted:
        """上传文档：校验 → 本地落盘原文 → 上传 RAGFlow → 配置解析器 → 触发解析。

        relative_path 为浏览器文件夹上传的原始相对路径（webkitRelativePath），
        非空时按其目录段逐级 find-or-create 文件夹后落入叶子目录。
        """
        del idempotency_key
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
        try:
            dataset_id = await self._dataset_id_for(base)
            remote_documents = await self.ragflow.upload_documents(
                dataset_id,
                [(safe_name, source.open_binary(), content_type or "application/octet-stream")],
            )
            if not remote_documents or not remote_documents[0].get("id"):
                raise KeyError("id")
            remote_id = str(remote_documents[0]["id"])
            document = await self._repo(
                self.repository.update_document,
                document["id"],
                remote_document_id=remote_id,
                status=DocumentStatus.PENDING.value,
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.CONFIGURING.value,
                progress=0.6,
            )
            # 个人库文档打个人标签（owner_user_id）后再触发解析入库
            await self.ragflow.update_document(
                dataset_id,
                remote_id,
                chunk_method=CHUNK_METHOD,
                parser_config=PARSER_CONFIG,
                meta_fields=self._personal_meta_fields(base),
            )
            try:
                await self.ragflow.parse_documents(
                    dataset_id, [remote_id]
                )
            except RagflowError as exc:
                if not await self._parse_actually_running(base, remote_id):
                    raise
                logger.warning(
                    "RAGFlow 触发解析误报(%s)，文档实际已开始解析，按成功处理",
                    exc,
                )
            document = await self._repo(
                self.repository.update_document,
                document["id"],
                status=DocumentStatus.PROCESSING.value,
                progress=0.0,
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.PARSING.value,
                progress=0.0,
            )
        except (RagflowError, KeyError, OriginalStorageError) as exc:
            code = getattr(exc, "code", "RAGFLOW_CONTRACT_MISMATCH")
            retryable = bool(getattr(exc, "retryable", False))
            document = await self._repo(
                self.repository.update_document,
                document["id"],
                status=DocumentStatus.FAILED.value,
                error_code=code,
                error_message="文档上传或解析启动失败",
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                retryable=retryable,
                error_code=code,
                error_message="文档上传或解析启动失败",
            )
            if isinstance(exc, RagflowError):
                raise self._upstream_error(exc) from exc
            if isinstance(exc, OriginalStorageError):
                raise KnowledgeServiceError(
                    code, "原文读取失败", status_code=503, retryable=retryable
                ) from exc
            raise KnowledgeServiceError(
                "KNOWLEDGE_UPSTREAM_ERROR",
                "知识底座返回了无效文档数据",
                status_code=502,
            ) from exc
        finally:
            source.open_binary().close()
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
                raise KnowledgeServiceError(
                    exc.code, exc.message, status_code=503, retryable=exc.retryable
                ) from exc
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
        """软删除文档：本地保留记录与原文，远端副本暂存保留期后由清理
        worker 删除（purge_expired_documents）。"""
        del request_id
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        if str(document.get("status")) == DocumentStatus.DELETED.value:
            return
        await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.DELETE_DOCUMENT
        )
        deleted_at = datetime.now(UTC)
        purge_after = deleted_at + timedelta(days=SOFT_DELETE_RETENTION_DAYS)
        await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.DELETED.value,
            previous_status=str(document["status"]),
            deleted_at=deleted_at.isoformat(),
            deleted_by=principal.user_id,
            purge_after=purge_after.isoformat(),
            progress=None,
        )

    async def purge_expired_documents(self, *, limit: int = 100) -> int:
        """清理软删除保留期（30 天）已到文档的远端副本，返回本次清理数。

        由后台清理 worker 周期调用；覆盖两类：文档自身软删除到期、所属
        知识库软删除到期（删库时文档未逐个软删除）。远端删除成功（或远端
        已不存在，幂等）后清空本地 remote_document_id 防止死链；本地软
        删除记录与原文保留，误删仍可通过重新上传恢复。单文档失败不阻断
        其余清理，留待下轮重试。
        """
        now_iso = datetime.now(UTC).isoformat()
        rows = await self._repo(
            self.repository.list_purgeable_documents, now_iso, limit=limit
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
            logger.info("软删除保留期到期清理完成 | 清理远端文档数=%s", purged)
        return purged

    async def retry_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> DocumentUploadAccepted:
        """重试失败文档：删除上游旧副本后，用本地原文重新上传并解析。"""
        del idempotency_key
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
                legacy = await self.ragflow.download_document(
                    dataset_id=dataset_id,
                    document_id=str(remote_id),
                )
                source = io.BytesIO(legacy.content)
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
            remote_documents = await self.ragflow.upload_documents(
                dataset_id,
                [(str(document["name"]), source, str(document["content_type"]))],
            )
            if not remote_documents or not remote_documents[0].get("id"):
                raise KeyError("id")
            new_remote_id = str(remote_documents[0]["id"])
            await self._repo(
                self.repository.update_document,
                document_id,
                remote_document_id=new_remote_id,
            )
            # 重试上传同样需要保持个人标签
            await self.ragflow.update_document(
                dataset_id,
                new_remote_id,
                chunk_method=CHUNK_METHOD,
                parser_config=PARSER_CONFIG,
                meta_fields=self._personal_meta_fields(base),
            )
            try:
                await self.ragflow.parse_documents(
                    dataset_id, [new_remote_id]
                )
            except RagflowError as exc:
                if not await self._parse_actually_running(base, new_remote_id):
                    raise
                logger.warning(
                    "RAGFlow 触发解析误报(%s)，文档实际已开始解析，按成功处理",
                    exc,
                )
            document = await self._repo(
                self.repository.update_document,
                document_id,
                status=DocumentStatus.PROCESSING.value,
                progress=0.0,
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.PARSING.value,
                progress=0.0,
            )
        except (RagflowError, KeyError, OriginalStorageError) as exc:
            code = getattr(exc, "code", "RAGFLOW_CONTRACT_MISMATCH")
            retryable = bool(getattr(exc, "retryable", False))
            document = await self._repo(
                self.repository.update_document,
                document_id,
                status=DocumentStatus.FAILED.value,
                error_code=code,
                error_message="文档重试失败",
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                retryable=retryable,
                error_code=code,
                error_message="文档重试失败",
            )
            if isinstance(exc, RagflowError):
                raise self._upstream_error(exc) from exc
            if isinstance(exc, OriginalStorageError):
                raise KnowledgeServiceError(
                    code, "文档原文件不可用", status_code=503, retryable=retryable
                ) from exc
            raise KnowledgeServiceError(
                "KNOWLEDGE_UPSTREAM_ERROR", "知识底座返回无效数据", status_code=502
            ) from exc
        finally:
            if source is not None:
                source.close()
        assert document is not None and operation is not None
        return DocumentUploadAccepted(
            document=self._document_summary(document, role, request_id),
            operation=self._operation_summary(operation, request_id),
        )

    # ---------------- 检索与问答 ----------------

    async def _retrieve_base_evidence(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        question: str,
        document_ids: list[str] | None,
        top_n: int,
        request_id: str,
    ) -> RetrieveResponse:
        """执行检索；无可检索文档时返回空证据与提示，不编造。"""
        base, _ = await self._authorized_base(base_id, principal, AllowedAction.ASK)
        if document_ids is None:
            documents = await self._repo(
                self.repository.list_documents,
                base_id,
                status=DocumentStatus.READY.value,
                limit=self.config.limits.max_documents_per_query,
                offset=0,
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
        documents = [item for item in documents if item.get("remote_document_id")]
        if not documents:
            return RetrieveResponse(
                evidence=[], warnings=["当前范围没有可检索的已解析文档"], request_id=request_id
            )
        try:
            # 个人库走共享数据集时，检索携带个人标签过滤条件
            # （与 document_ids 范围约束形成双重隔离；存量自有数据集库不打
            # 标签、不过滤，保持原行为）
            retrieval_meta: dict[str, Any] | None = None
            if not str(base.get("remote_dataset_id") or "").strip():
                retrieval_meta = self._personal_meta_fields(base)
            result = await self.ragflow.retrieve(
                question=question,
                dataset_ids=[await self._dataset_id_for(base)],
                document_ids=[str(item["remote_document_id"]) for item in documents],
                page_size=top_n,
                similarity_threshold=RETRIEVAL_SIMILARITY_THRESHOLD,
                vector_similarity_weight=RETRIEVAL_VECTOR_SIMILARITY_WEIGHT,
                top_k=RETRIEVAL_TOP_K,
                meta_fields=retrieval_meta,
            )
        except RagflowError as exc:
            raise self._upstream_error(exc) from exc
        by_remote = {str(item["remote_document_id"]): item for item in documents}
        evidence: list[Evidence] = []
        for chunk in result.get("chunks", []):
            if not isinstance(chunk, dict):
                continue
            local = by_remote.get(str(chunk.get("document_id")))
            if local is None or local["status"] != DocumentStatus.READY.value:
                continue
            snippet = str(
                chunk.get("content_with_weight") or chunk.get("content") or ""
            ).strip()
            if not snippet:
                continue
            raw_score = chunk.get("similarity", 0)
            score = max(0.0, float(raw_score)) if isinstance(raw_score, (int, float)) else 0.0
            positions = chunk.get("positions")
            evidence.append(
                Evidence(
                    document_id=str(local["id"]),
                    document_name=str(local["name"]),
                    snippet=snippet,
                    score=score,
                    position=positions[0] if isinstance(positions, list) and positions else None,
                    metadata={
                        "base_id": str(base["id"]),
                        "base_name": str(base["name"]),
                    },
                )
            )
        evidence = [item for item in evidence if item.score >= MINIMUM_EVIDENCE_SCORE]
        return RetrieveResponse(evidence=evidence, request_id=request_id)

    async def retrieve_session_evidence(
        self,
        *,
        session_id: str,
        principal: KnowledgePrincipal,
        question: str,
        top_n: int,
        request_id: str,
    ) -> RetrieveResponse:
        """在会话已选择且仍被授权的知识库范围内检索。"""
        scope = await self.get_session_scope(
            session_id=session_id, principal=principal
        )
        if not scope.base_ids:
            return RetrieveResponse(evidence=[], request_id=request_id)
        evidence: list[Evidence] = []
        warnings: list[str] = []
        for base_id in scope.base_ids:
            result = await self._retrieve_base_evidence(
                base_id=base_id,
                principal=principal,
                question=question,
                document_ids=None,
                top_n=top_n,
                request_id=request_id,
            )
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
    "LocalOriginalStore",
    "OriginalReadHandle",
    "OriginalStorageError",
    "KnowledgeDocumentBinary",
    "allowed_actions",
    "effective_role",
]
