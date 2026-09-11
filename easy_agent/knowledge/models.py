"""Public DTO contract for ``/api/knowledge/v1``.

These models intentionally expose only EasyAgent-local identifiers and stable
domain states. RAGFlow IDs, response codes, parser fields, and endpoints never
cross the BFF boundary.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .domain import (
    AllowedAction,
    DocumentStatus,
    KnowledgeBaseRole,
    KnowledgeBaseStatus,
    KnowledgeBaseVisibility,
    OperationPhase,
    OperationStatus,
    OperationType,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeModuleStatus(str, Enum):
    DISABLED = "disabled"
    CONFIGURED = "configured"
    READY = "ready"
    DEGRADED = "degraded"


class KnowledgeCapabilitiesResponse(ContractModel):
    schema_version: Literal[1] = 1
    api_version: Literal["v1"] = "v1"
    enabled: StrictBool
    status: KnowledgeModuleStatus
    upstream_capabilities: list[str] = Field(default_factory=list)
    features: dict[str, StrictBool] = Field(default_factory=dict)


class KnowledgeErrorResponse(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: StrictBool = False
    request_id: str


class CursorPage(ContractModel):
    next_cursor: str | None = None
    has_more: StrictBool = False


class PageInfo(ContractModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class KnowledgeBaseCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=127)
    description: str = Field(default="", max_length=2000)
    visibility: KnowledgeBaseVisibility
    department_id: str | None = Field(default=None, max_length=255)

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


class DocumentSummary(ContractModel):
    id: str
    base_id: str
    folder_id: str | None = None
    name: str
    content_type: str
    size_bytes: int = Field(ge=0)
    status: DocumentStatus
    index_status: DocumentStatus | None = None
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


class DocumentListResponse(ContractModel):
    items: list[DocumentSummary]
    page: PageInfo


class DocumentMoveRequest(ContractModel):
    folder_id: str | None = None


class OperationSummary(ContractModel):
    id: str
    batch_operation_id: str | None = None
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
    allowed_actions: list[AllowedAction] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("total")
    @classmethod
    def total_is_not_less_than_current(cls, value: int, info) -> int:
        current = info.data.get("current", 0)
        if value < current:
            raise ValueError("total must be greater than or equal to current")
        return value


class UploadItemAccepted(ContractModel):
    client_file_id: str
    document_id: str | None = None
    operation_id: str
    status: OperationStatus = OperationStatus.ACCEPTED
    error: KnowledgeErrorResponse | None = None


class UploadBatchAccepted(ContractModel):
    batch_operation_id: str
    items: list[UploadItemAccepted]
    poll_after_seconds: float = Field(default=2.0, gt=0)

    @field_validator("items")
    @classmethod
    def batch_has_items(cls, value: list[UploadItemAccepted]) -> list[UploadItemAccepted]:
        if not value:
            raise ValueError("upload batch must contain at least one item")
        return value


class DocumentUploadAccepted(ContractModel):
    document: DocumentSummary
    operation: OperationSummary
    poll_after_seconds: float = Field(default=3.0, gt=0)


class OperationListResponse(ContractModel):
    items: list[OperationSummary]
    page: PageInfo


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


class PermissionListResponse(ContractModel):
    items: list[PermissionEntry]


class PermissionSubject(ContractModel):
    subject_type: Literal["user", "department"]
    subject_id: str
    label: str
    secondary: str = ""


class PermissionSubjectListResponse(ContractModel):
    items: list[PermissionSubject]


class Evidence(ContractModel):
    document_id: str
    document_name: str
    snippet: str
    score: float = Field(ge=0)
    page: int | None = Field(default=None, ge=1)
    position: list[int | float | str] | None = None
    preview_locator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrieveRequest(ContractModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] | None = None
    top_n: int = Field(default=10, ge=1, le=100)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value

    @field_validator("document_ids")
    @classmethod
    def document_filter_is_not_empty(
        cls, value: list[str] | None
    ) -> list[str] | None:
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


class AskMetadataEvent(ContractModel):
    event: Literal["metadata"] = "metadata"
    request_id: str


class AskAnswerDeltaEvent(ContractModel):
    event: Literal["answer.delta"] = "answer.delta"
    delta: str


class AskEvidenceEvent(ContractModel):
    event: Literal["evidence"] = "evidence"
    evidence: list[Evidence]


class AskWarningEvent(ContractModel):
    event: Literal["warning"] = "warning"
    warning: str


class AskDoneEvent(ContractModel):
    event: Literal["done"] = "done"


class AskErrorEvent(ContractModel):
    event: Literal["error"] = "error"
    error: KnowledgeErrorResponse


__all__ = [
    "AskAnswerDeltaEvent",
    "AskDoneEvent",
    "AskErrorEvent",
    "AskEvidenceEvent",
    "AskMetadataEvent",
    "AskRequest",
    "AskResponse",
    "AskWarningEvent",
    "ConversationTurn",
    "CursorPage",
    "DocumentListResponse",
    "DocumentMoveRequest",
    "DocumentSummary",
    "DocumentUploadAccepted",
    "Evidence",
    "FolderCreateRequest",
    "FolderListResponse",
    "FolderSummary",
    "FolderUpdateRequest",
    "KnowledgeBaseCreateRequest",
    "KnowledgeBaseListResponse",
    "KnowledgeBaseSummary",
    "KnowledgeBaseUpdateRequest",
    "KnowledgeCapabilitiesResponse",
    "KnowledgeErrorResponse",
    "KnowledgeModuleStatus",
    "KnowledgeStatusResponse",
    "OperationListResponse",
    "OperationSummary",
    "PageInfo",
    "PermissionEntry",
    "PermissionListResponse",
    "PermissionReplaceRequest",
    "PermissionSubject",
    "PermissionSubjectListResponse",
    "RetrieveRequest",
    "RetrieveResponse",
    "SessionKnowledgeScopeRequest",
    "SessionKnowledgeScopeResponse",
    "UploadBatchAccepted",
    "UploadItemAccepted",
]
