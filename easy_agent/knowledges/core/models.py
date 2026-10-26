"""知识库对外 DTO 契约（/agent/knowledge/v1）。

只暴露 EasyAgent 本地标识与稳定领域状态；RAGFlow 的 ID、响应码、解析器
字段与端点不越过 BFF 边界。领域枚举定义于本文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


@dataclass(frozen=True, slots=True)
class KnowledgePrincipal:
    """请求级身份：由 Bearer 令牌解析出的用户、用户名与部门。"""

    user_id: str
    username: str
    department_id: str | None


# ---------------------------------------------------------------------------
# 领域枚举
# ---------------------------------------------------------------------------
class KnowledgeBaseStatus(str, Enum):
    CREATING = "creating"
    ACTIVE = "active"
    OFFLINE = "offline"
    DELETING = "deleting"
    FAILED = "failed"
    DELETED = "deleted"


class KnowledgeBaseVisibility(str, Enum):
    PERSONAL = "personal"
    TEAM = "team"
    SHARED = "shared"


class KnowledgeBaseRole(str, Enum):
    VIEWER = "viewer"
    MAINTAINER = "maintainer"
    MANAGER = "manager"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class OperationType(str, Enum):
    DATASET_CREATE = "dataset_create"
    DOCUMENT_UPLOAD = "document_upload"
    DOCUMENT_PARSE = "document_parse"


class OperationPhase(str, Enum):
    QUEUED = "queued"
    STORING_ORIGINAL = "storing_original"
    UPLOADING = "uploading"
    CONFIGURING = "configuring"
    PARSING = "parsing"
    DELETING = "deleting"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    FAILED = "failed"


class OperationStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class AllowedAction(str, Enum):
    VIEW = "view"
    ASK = "ask"
    DOWNLOAD = "download"
    SELECT_FOR_SESSION = "select_for_session"
    CREATE_FOLDER = "create_folder"
    EDIT_FOLDER = "edit_folder"
    DELETE_FOLDER = "delete_folder"
    UPLOAD = "upload"
    MOVE_DOCUMENT = "move_document"
    DELETE_DOCUMENT = "delete_document"
    RETRY_DOCUMENT = "retry_document"
    EDIT_BASE = "edit_base"
    MANAGE_PERMISSIONS = "manage_permissions"
    DELETE_BASE = "delete_base"


class KnowledgeModuleStatus(str, Enum):
    DISABLED = "disabled"
    CONFIGURED = "configured"
    READY = "ready"
    DEGRADED = "degraded"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 通用结构
# ---------------------------------------------------------------------------
class KnowledgeErrorResponse(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: StrictBool = False
    request_id: str


class PageInfo(ContractModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# 知识库（个人/公共空间）
# ---------------------------------------------------------------------------
class KnowledgeBaseCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=127)
    description: str = Field(default="", max_length=2000)
    visibility: KnowledgeBaseVisibility
    department_id: str | None = Field(default=None, max_length=255)
    # 创建即配置可见性：勾选名单模式开关 + 初始可见人员（仅公共空间库有效）
    restrict_access: StrictBool = False
    permission_user_ids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("department_id")
    @classmethod
    def normalize_department_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("permission_user_ids")
    @classmethod
    def normalize_permission_user_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            user_id = (item or "").strip()
            if user_id and user_id not in seen:
                seen.add(user_id)
                normalized.append(user_id)
        return normalized


class KnowledgeBaseUpdateRequest(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=127)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def optional_name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class KnowledgeBaseSummary(ContractModel):
    id: str
    name: str
    description: str = ""
    visibility: KnowledgeBaseVisibility
    status: KnowledgeBaseStatus
    role: KnowledgeBaseRole
    document_count: int = Field(default=0, ge=0)
    allowed_actions: list[AllowedAction] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TeamSpaceDepartmentSummary(ContractModel):
    id: str
    name: str = ""


class TeamSpaceManagementCapability(ContractModel):
    can_create: StrictBool = False
    can_manage: StrictBool = False
    department_id: str | None = None
    is_admin: StrictBool = False
    departments: list[TeamSpaceDepartmentSummary] = Field(default_factory=list)


class TeamSpaceManagerSummary(ContractModel):
    user_id: str
    username: str
    display_name: str = ""
    department_id: str
    department_name: str = ""
    account_status: Literal["active", "disabled"]
    granted_by: str
    granted_at: datetime
    updated_at: datetime


class TeamSpaceManagerListResponse(ContractModel):
    items: list[TeamSpaceManagerSummary] = Field(default_factory=list)


class TeamSpaceManagerUpdateRequest(ContractModel):
    enabled: StrictBool


class TeamSpaceManagerUpdateResponse(ContractModel):
    user_id: str
    enabled: StrictBool
    manager: TeamSpaceManagerSummary | None = None


class KnowledgeBaseListResponse(ContractModel):
    items: list[KnowledgeBaseSummary]
    page: PageInfo
    team_space_management: TeamSpaceManagementCapability = Field(
        default_factory=TeamSpaceManagementCapability
    )


# ---------------------------------------------------------------------------
# 文件夹
# ---------------------------------------------------------------------------
class FolderCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=127)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class FolderUpdateRequest(FolderCreateRequest):
    pass


class FolderSummary(ContractModel):
    id: str
    base_id: str
    parent_id: str | None = None
    name: str
    document_count: int = Field(default=0, ge=0)
    child_count: int = Field(default=0, ge=0)
    allowed_actions: list[AllowedAction] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class FolderListResponse(ContractModel):
    items: list[FolderSummary]


class FolderEnsureRequest(ContractModel):
    """文件夹上传时批量确保目录树存在（含空目录），路径为相对路径段列表。"""

    paths: list[str] = Field(default_factory=list, max_length=200)
    folder_id: str | None = None

    @field_validator("paths")
    @classmethod
    def paths_not_overlong(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 1024:
                raise ValueError("path too long")
        return value


class FolderEnsureResponse(ContractModel):
    ensured: int = Field(ge=0)


# ---------------------------------------------------------------------------
# 文档与上传
# ---------------------------------------------------------------------------
class DocumentSummary(ContractModel):
    id: str
    base_id: str
    folder_id: str | None = None
    name: str
    content_type: str
    size_bytes: int = Field(ge=0)
    status: DocumentStatus
    original_status: Literal[
        "unmanaged", "staging", "available", "failed", "quarantined", "deleted"
    ] = "unmanaged"
    original_available: StrictBool = False
    original_sha256: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    error: KnowledgeErrorResponse | None = None
    allowed_actions: list[AllowedAction] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    # 业务元数据（文档 SQL 同步写入）
    source: str | None = None
    source_record_id: str | None = None
    batch_id: str | None = None
    report_title: str | None = None
    report_tag: str | None = None
    report_second_source: str | None = None
    publish_date: str | None = None


class DocumentListResponse(ContractModel):
    items: list[DocumentSummary]
    page: PageInfo


class DocumentMoveRequest(ContractModel):
    folder_id: str | None = None


class OperationSummary(ContractModel):
    id: str
    resource_type: str | None = None
    resource_id: str | None = None
    type: OperationType
    phase: OperationPhase
    status: OperationStatus
    current: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    progress: float | None = Field(default=None, ge=0, le=1)
    retryable: StrictBool = False
    error: KnowledgeErrorResponse | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("total")
    @classmethod
    def total_is_not_less_than_current(cls, value: int, info) -> int:
        current = info.data.get("current", 0)
        if value < current:
            raise ValueError("total must be greater than or equal to current")
        return value


class DocumentUploadAccepted(ContractModel):
    document: DocumentSummary
    operation: OperationSummary


class OperationListResponse(ContractModel):
    items: list[OperationSummary]
    page: PageInfo


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------
class PermissionEntry(ContractModel):
    subject_type: Literal["user", "department"]
    subject_id: str = Field(min_length=1, max_length=255)
    role: KnowledgeBaseRole

    @field_validator("subject_id")
    @classmethod
    def subject_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("subject_id must not be blank")
        return value


class PermissionReplaceRequest(ContractModel):
    items: list[PermissionEntry] = Field(default_factory=list, max_length=200)
    # 勾选名单模式：True 时该库仅勾选人员可见（屏蔽公共空间默认同部门可见）
    restrict_access: bool = False


class PermissionListResponse(ContractModel):
    items: list[PermissionEntry]
    restrict_access: bool = False


class PermissionDirectoryUser(ContractModel):
    """权限管理人员目录条目（在职账号，含部门/处室/团队层级字段）。"""

    user_id: str
    username: str = ""
    display_name: str = ""
    department_id: str = ""
    department_name: str = ""
    division_name: str = ""
    team_name: str = ""


class PermissionDirectoryListResponse(ContractModel):
    items: list[PermissionDirectoryUser]


# ---------------------------------------------------------------------------
# 检索与问答
# ---------------------------------------------------------------------------
class Evidence(ContractModel):
    document_id: str
    document_name: str
    snippet: str
    score: float = Field(ge=0)
    page: int | None = Field(default=None, ge=1)
    position: list[int | float | str] | None = None
    preview_locator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentMetadata(ContractModel):
    """文档 SQL 同步写入的业务元数据（全部来自源表）。"""

    source: str = Field(default="source_sync", max_length=127)
    source_record_id: str = Field(min_length=1, max_length=255)
    batch_id: str = Field(default="", max_length=255)
    report_title: str = Field(default="", max_length=255)
    report_tag: str = Field(default="", max_length=127)
    report_second_source: str = Field(default="", max_length=127)
    publish_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class DocumentMetadataFilter(ContractModel):
    """检索元数据过滤器：各字段之间 AND，同字段多值之间 OR。

    publish_date 为闭区间；文档 publish_date 为空时不会命中任何日期条件。
    """

    publish_date_from: str | None = None
    publish_date_to: str | None = None
    report_tags: list[str] = Field(default_factory=list, max_length=20)
    report_second_sources: list[str] = Field(default_factory=list, max_length=20)
    report_title_contains: str | None = Field(default=None, max_length=100)

    @field_validator("publish_date_from", "publish_date_to")
    @classmethod
    def _date_format(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        try:
            return datetime.strptime(str(value).strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("日期必须为 YYYY-MM-DD") from exc

    @field_validator("report_tags", "report_second_sources")
    @classmethod
    def _clean_list(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item and item.strip()))

    @field_validator("report_title_contains")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _range_order(self) -> "DocumentMetadataFilter":
        if (
            self.publish_date_from
            and self.publish_date_to
            and self.publish_date_from > self.publish_date_to
        ):
            raise ValueError("publish_date_from 不能晚于 publish_date_to")
        return self


class RetrieveRequest(ContractModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] | None = None
    top_n: int = Field(default=10, ge=1, le=100)
    metadata_filter: DocumentMetadataFilter | None = None

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value

    @field_validator("document_ids")
    @classmethod
    def document_filter_is_not_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not value:
            raise ValueError("document_ids must be omitted or non-empty")
        return value


class RetrieveResponse(ContractModel):
    evidence: list[Evidence]
    warnings: list[str] = Field(default_factory=list)
    request_id: str


class ConversationTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class AskRequest(RetrieveRequest):
    conversation_context: list[ConversationTurn] = Field(
        default_factory=list, max_length=20
    )


class AskResponse(ContractModel):
    answer: str
    evidence: list[Evidence]
    warnings: list[str] = Field(default_factory=list)
    request_id: str


class SessionKnowledgeScopeRequest(ContractModel):
    base_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("base_ids")
    @classmethod
    def unique_base_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("base_ids cannot contain blank values")
        if len(set(value)) != len(value):
            raise ValueError("base_ids must be unique")
        return value


class SessionKnowledgeScopeResponse(ContractModel):
    session_id: str
    base_ids: list[str]


class KnowledgeStatusResponse(ContractModel):
    enabled: StrictBool
    ready: StrictBool
    status: KnowledgeModuleStatus


class KnowledgeCapabilitiesResponse(ContractModel):
    """知识工程能力声明（前端 KnowledgeWorkbench 启用判断依据）。"""

    enabled: StrictBool
    status: KnowledgeModuleStatus
    upstream_capabilities: list[str] = []
    features: dict[str, bool] = {}


__all__ = [
    "AllowedAction",
    "AskRequest",
    "AskResponse",
    "ConversationTurn",
    "DocumentListResponse",
    "DocumentMoveRequest",
    "DocumentStatus",
    "DocumentSummary",
    "DocumentUploadAccepted",
    "Evidence",
    "FolderCreateRequest",
    "FolderListResponse",
    "FolderSummary",
    "FolderUpdateRequest",
    "KnowledgeBaseCreateRequest",
    "KnowledgeBaseListResponse",
    "KnowledgeBaseRole",
    "KnowledgeBaseStatus",
    "KnowledgeBaseSummary",
    "KnowledgeBaseUpdateRequest",
    "KnowledgeBaseVisibility",
    "KnowledgeErrorResponse",
    "KnowledgeModuleStatus",
    "KnowledgeStatusResponse",
    "OperationListResponse",
    "OperationPhase",
    "OperationStatus",
    "OperationSummary",
    "OperationType",
    "PageInfo",
    "PermissionEntry",
    "PermissionListResponse",
    "PermissionReplaceRequest",
    "PermissionDirectoryListResponse",
    "PermissionDirectoryUser",
    "RetrieveRequest",
    "RetrieveResponse",
    "SessionKnowledgeScopeRequest",
    "SessionKnowledgeScopeResponse",
    "TeamSpaceDepartmentSummary",
    "TeamSpaceManagementCapability",
    "TeamSpaceManagerListResponse",
    "TeamSpaceManagerSummary",
    "TeamSpaceManagerUpdateRequest",
    "TeamSpaceManagerUpdateResponse",
]


class KnowledgeAskCancelResponse(ContractModel):
    status: str
