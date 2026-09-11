"""Stable state vocabularies shared by API, services, and adapters."""

from enum import Enum


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
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class OperationType(str, Enum):
    DATASET_CREATE = "dataset_create"
    DATASET_DELETE = "dataset_delete"
    DATASET_RESTORE = "dataset_restore"
    DOCUMENT_UPLOAD = "document_upload"
    DOCUMENT_PARSE = "document_parse"
    DOCUMENT_DELETE = "document_delete"
    SOURCE_SYNC = "source_sync"


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
    RESTORE_DOCUMENT = "restore_document"
    EDIT_BASE = "edit_base"
    MANAGE_PERMISSIONS = "manage_permissions"
    DELETE_BASE = "delete_base"
    RESTORE_BASE = "restore_base"


__all__ = [
    "AllowedAction",
    "DocumentStatus",
    "KnowledgeBaseRole",
    "KnowledgeBaseStatus",
    "KnowledgeBaseVisibility",
    "OperationPhase",
    "OperationStatus",
    "OperationType",
]
