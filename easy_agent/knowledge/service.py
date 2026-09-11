"""Minimal knowledge business rules and request-driven MVP use cases."""

from __future__ import annotations

import io
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from starlette.concurrency import run_in_threadpool

from .repository import KnowledgeRepository
from .operations_repository import KnowledgeOperationsRepository
from .file_validation import UploadValidationError, validate_upload
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
from .ragflow import RagflowBinary, RagflowClient, RagflowError
from .config import KnowledgeConfig
from .auth import KnowledgePrincipal
from .models import (
    DocumentListResponse,
    DocumentSummary,
    DocumentUploadAccepted,
    Evidence,
    FolderListResponse,
    FolderSummary,
    KnowledgeBaseListResponse,
    KnowledgeBaseSummary,
    KnowledgeErrorResponse,
    OperationListResponse,
    OperationSummary,
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
from .storage import (
    OriginalReadHandle,
    OriginalStorageError,
    create_original_store,
)
from .storage.base import OriginalDocumentStore


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
        AllowedAction.RESTORE_DOCUMENT,
    }
)
_MANAGER_ACTIONS = _MAINTAINER_ACTIONS | frozenset(
    {
        AllowedAction.EDIT_BASE,
        AllowedAction.MANAGE_PERMISSIONS,
        AllowedAction.DELETE_BASE,
        AllowedAction.RESTORE_BASE,
    }
)


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
) -> KnowledgeBaseRole | None:
    """Return the strongest allowed role for one user and one-level department."""

    if str(base.get("owner_user_id", "")) == principal.user_id:
        return KnowledgeBaseRole.MANAGER

    visibility = KnowledgeBaseVisibility(str(base["space_type"]))
    if visibility == KnowledgeBaseVisibility.PERSONAL:
        return None

    candidates: list[KnowledgeBaseRole] = []
    base_department = str(base.get("department_id") or "").strip()
    if (
        visibility == KnowledgeBaseVisibility.TEAM
        and principal.department_id
        and principal.department_id == base_department
    ):
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


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _upstream_dataset_name(base_id: object, max_bytes: int) -> str:
    """Build the ASCII-only unique name required by the bank dataset API."""

    safe_id = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in str(base_id)
    ).strip("_")
    return _truncate_utf8(f"EA_{safe_id}", max_bytes)


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        ragflow: RagflowClient,
        config: KnowledgeConfig,
        *,
        original_store: OriginalDocumentStore | None = None,
    ):
        self.repository = repository
        self.ragflow = ragflow
        self.config = config
        self.operations_repository = KnowledgeOperationsRepository(repository.db)
        self._team_manager_cache: dict[str, bool] = {}
        self.original_store = (
            original_store
            if original_store is not None
            else create_original_store(config)
        )

    async def _repo(self, method, *args, **kwargs):
        return await run_in_threadpool(method, *args, **kwargs)

    @staticmethod
    def _upstream_error(exc: RagflowError) -> KnowledgeServiceError:
        status_code = 503 if exc.retryable else 502
        return KnowledgeServiceError(
            "KNOWLEDGE_UPSTREAM_UNAVAILABLE"
            if exc.retryable
            else "KNOWLEDGE_UPSTREAM_ERROR",
            "知识底座暂时不可用" if exc.retryable else "知识底座请求失败",
            status_code=status_code,
            retryable=exc.retryable,
        )

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
        if (
            str(base.get("status")) == KnowledgeBaseStatus.DELETED.value
            and action != AllowedAction.RESTORE_BASE
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        return base, role

    async def _has_team_management_grant(
        self, principal: KnowledgePrincipal
    ) -> bool:
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
        """Apply the global team-manager allowlist after normal base grants.

        The global allowlist controls department-wide management and creation.
        Per-base roles are a separate scope: admin may grant maintainer or
        manager on one team knowledge base without granting creation rights for
        other team spaces.
        """

        permission_rows = list(permissions)
        role = effective_role(base, principal, permission_rows)
        if str(base.get("space_type")) != KnowledgeBaseVisibility.TEAM.value:
            return role
        if principal.username == "admin":
            return KnowledgeBaseRole.MANAGER
        same_department = bool(principal.department_id) and (
            principal.department_id == str(base.get("department_id") or "").strip()
        )
        if same_department and await self._has_team_management_grant(principal):
            return KnowledgeBaseRole.MANAGER
        if str(base.get("owner_user_id", "")) == principal.user_id:
            explicit_roles = _matching_permission_roles(
                principal, permission_rows
            )
            return max(
                [KnowledgeBaseRole.VIEWER, *explicit_roles],
                key=_ROLE_RANK.__getitem__,
            )
        return role

    async def _retire_original(self, document_id: str) -> None:
        """Apply the configured original-retention policy after logical deletion."""

        original = await self._repo(
            self.repository.get_original_object, document_id
        )
        if original is None or str(original.get("status")) in {
            "deleted",
            "quarantined",
        }:
            return
        if self.original_store is None:
            return
        policy = self.config.original_storage.deletion.policy
        timestamp = datetime.now(UTC).isoformat()
        storage_key = str(original["storage_key"])
        try:
            if policy == "purge":
                await self._repo(self.original_store.purge, storage_key)
                await self._repo(
                    self.repository.update_original_object,
                    document_id,
                    status="deleted",
                    deleted_at=timestamp,
                    error_code=None,
                    error_message=None,
                )
            elif policy == "quarantine":
                quarantine_key = await self._repo(
                    self.original_store.quarantine, storage_key
                )
                await self._repo(
                    self.repository.update_original_object,
                    document_id,
                    storage_key=quarantine_key,
                    status="quarantined",
                    quarantined_at=timestamp,
                    error_code=None,
                    error_message=None,
                )
            else:
                await self._repo(
                    self.repository.update_original_object,
                    document_id,
                    status="quarantined",
                    quarantined_at=timestamp,
                    error_code=None,
                    error_message=None,
                )
        except OriginalStorageError as exc:
            await self._repo(
                self.repository.update_original_object,
                document_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise

    async def _base_summary(
        self,
        base: Mapping[str, Any],
        role: KnowledgeBaseRole,
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
            original_available=(
                str(document.get("original_status") or "") == "available"
            ),
            original_sha256=(
                str(document["original_sha256"])
                if document.get("original_sha256") else None
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
            batch_operation_id=(
                str(operation["batch_operation_id"])
                if operation.get("batch_operation_id") else None
            ),
            resource_type=(
                str(operation["resource_type"])
                if operation.get("resource_type") else None
            ),
            resource_id=(
                str(operation["resource_id"])
                if operation.get("resource_id") else None
            ),
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

    async def list_bases(
        self, principal: KnowledgePrincipal, *, page: int, page_size: int
    ) -> KnowledgeBaseListResponse:
        is_admin = principal.username == "admin"
        has_team_management_grant = await self._has_team_management_grant(principal)
        departments: list[TeamSpaceDepartmentSummary] = []
        if is_admin:
            department_rows = await self._repo(
                self.repository.list_active_departments
            )
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
                can_create=(bool(departments) if is_admin else (
                    bool(principal.department_id) and has_team_management_grant
                )),
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
        target_department_id: str | None = None
        requested_department_id = (department_id or "").strip() or None
        if visibility == KnowledgeBaseVisibility.TEAM:
            if not await self._has_team_management_grant(principal):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_TEAM_SPACE_MANAGER_REQUIRED",
                    "当前账号未获得团队空间创建与管理权限，请联系 admin 配置",
                    status_code=403,
                )
            if principal.username == "admin":
                target_department_id = (
                    requested_department_id or principal.department_id
                )
                if not target_department_id:
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_DEPARTMENT_REQUIRED",
                        "请选择团队空间所属部门",
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
                        "创建团队空间知识库需要有效部门",
                        status_code=422,
                    )
                if (
                    requested_department_id
                    and requested_department_id != principal.department_id
                ):
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_DEPARTMENT_FORBIDDEN",
                        "只能在当前账号所属部门创建团队空间",
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
            embedding_model=self.config.defaults.dataset.embedding_model,
        )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DATASET_CREATE.value,
            resource_type="base",
            resource_id=base["id"],
            phase=OperationPhase.CONFIGURING.value,
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
        )
        remote_name = _upstream_dataset_name(
            base["id"], self.config.limits.max_dataset_name_utf8_bytes
        )
        try:
            remote = await self.ragflow.create_dataset(
                name=remote_name, description=description, request_id=request_id
            )
            base = await self._repo(
                self.repository.update_base,
                base["id"],
                remote_dataset_id=str(remote["id"]),
                status=KnowledgeBaseStatus.ACTIVE.value,
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.COMPLETED.value,
                status=OperationStatus.SUCCEEDED.value,
                current_count=1,
                progress=1.0,
            )
        except RagflowError as exc:
            await self._repo(
                self.repository.update_base,
                base["id"],
                status=KnowledgeBaseStatus.FAILED.value,
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="知识底座创建失败",
            )
            raise self._upstream_error(exc) from exc
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
        base, role = await self._authorized_base(
            base_id, principal, AllowedAction.EDIT_BASE
        )
        values: dict[str, object] = {}
        local_values: dict[str, object] = {}
        if name is not None:
            values["name"] = _upstream_dataset_name(
                base_id, self.config.limits.max_dataset_name_utf8_bytes
            )
            local_values["name"] = name
        if description is not None:
            values["description"] = description
            local_values["description"] = description
        if values:
            try:
                await self.ragflow.update_dataset(
                    str(base["remote_dataset_id"]), values, request_id=request_id
                )
            except RagflowError as exc:
                raise self._upstream_error(exc) from exc
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
        base, _ = await self._authorized_base(
            base_id, principal, AllowedAction.DELETE_BASE
        )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DATASET_DELETE.value,
            resource_type="base",
            resource_id=base_id,
            phase=OperationPhase.DELETING.value,
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
        )
        if self.config.operations.worker.mode == "queue":
            deleted_at = datetime.now(UTC)
            purge_after = deleted_at + timedelta(
                days=self.config.original_storage.deletion.retention_days
            )
            await self._repo(
                self.repository.update_base,
                base_id,
                status=KnowledgeBaseStatus.DELETED.value,
                deleted_at=deleted_at.isoformat(),
                deleted_by=principal.user_id,
                purge_after=purge_after.isoformat(),
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.QUEUED.value,
                status=OperationStatus.ACCEPTED.value,
            )
            task_key = hashlib.sha256(
                f"delete-base:{principal.user_id}:{base_id}:{request_id}".encode()
            ).hexdigest()
            await self._repo(
                self.operations_repository.create_task,
                operation_id=str(operation["id"]),
                task_type="dataset_delete",
                resource_type="base",
                resource_id=base_id,
                idempotency_key=task_key,
                payload={},
                request_id=request_id,
                created_by=principal.user_id,
                max_attempts=self.config.operations.worker.max_attempts,
            )
            return
        await self._repo(
            self.repository.update_base,
            base_id,
            status=KnowledgeBaseStatus.DELETING.value,
        )
        try:
            await self.ragflow.delete_datasets(
                [str(base["remote_dataset_id"])], request_id=request_id
            )
        except RagflowError as exc:
            await self._repo(
                self.repository.update_base,
                base_id,
                status=KnowledgeBaseStatus.FAILED.value,
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="知识库删除失败",
            )
            raise self._upstream_error(exc) from exc
        documents = await self._repo(
            self.repository.list_documents,
            base_id,
            limit=1_000_000,
            offset=0,
        )
        try:
            for document in documents:
                await self._retire_original(str(document["id"]))
        except OriginalStorageError as exc:
            await self._repo(
                self.repository.update_base,
                base_id,
                status=KnowledgeBaseStatus.FAILED.value,
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.PARTIAL_FAILED.value,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="知识库已从底座删除，原文待清理",
            )
            raise KnowledgeServiceError(
                exc.code,
                "知识库已从底座删除，原文清理失败",
                status_code=503,
                retryable=exc.retryable,
            ) from exc
        await self._repo(self.repository.delete_base, base_id)
        await self._repo(
            self.repository.update_operation,
            operation["id"],
            phase=OperationPhase.COMPLETED.value,
            status=OperationStatus.SUCCEEDED.value,
            current_count=1,
            progress=1.0,
        )

    async def restore_base(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> KnowledgeBaseSummary:
        base, role = await self._authorized_base(
            base_id, principal, AllowedAction.RESTORE_BASE
        )
        if str(base.get("status")) != KnowledgeBaseStatus.DELETED.value:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RESTORE_NOT_ALLOWED",
                "当前知识库不需要恢复",
                status_code=409,
            )
        purge_after = base.get("purge_after")
        if purge_after:
            try:
                if datetime.fromisoformat(str(purge_after)) <= datetime.now(UTC):
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_RESTORE_EXPIRED",
                        "知识库已超过恢复期",
                        status_code=409,
                    )
            except ValueError as exc:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_RESTORE_NOT_ALLOWED",
                    "知识库恢复信息无效",
                    status_code=409,
                ) from exc
        await self._repo(
            self.operations_repository.cancel_pending_tasks,
            resource_id=base_id,
            task_type="dataset_delete",
        )
        base = await self._repo(
            self.repository.update_base,
            base_id,
            status=KnowledgeBaseStatus.ACTIVE.value,
            deleted_at=None,
            deleted_by=None,
            purge_after=None,
        )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DATASET_RESTORE.value,
            resource_type="base",
            resource_id=base_id,
            phase=OperationPhase.COMPLETED.value,
            status=OperationStatus.SUCCEEDED.value,
            created_by=principal.user_id,
            retryable=False,
        )
        operation = await self._repo(
            self.repository.update_operation,
            operation["id"],
            current_count=1,
            progress=1.0,
        )
        assert base is not None and operation is not None
        return await self._base_summary(base, role)

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
            row = await self._repo(
                self.repository.create_folder,
                base_id=base_id,
                name=name,
                parent_id=parent_id,
            )
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
            row = await self._repo(
                self.repository.update_folder, folder_id, name=name
            )
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
            existing_rows = await self._repo(
                self.repository.list_permissions, base_id
            )
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
            items=[
                PermissionSubject(subject_type=subject_type, **row) for row in rows
            ]
        )

    async def _refresh_documents(self, base: Mapping[str, Any], request_id: str) -> None:
        remote_dataset_id = base.get("remote_dataset_id")
        if not remote_dataset_id:
            return
        try:
            upstream = await self.ragflow.list_documents(
                dataset_id=str(remote_dataset_id),
                page=1,
                page_size=self.config.limits.max_documents_per_query,
                request_id=request_id,
            )
        except RagflowError:
            return
        docs = upstream.get("docs", [])
        remote_ids = [str(item.get("id")) for item in docs if item.get("id")]
        local_docs = await self._repo(
            self.repository.get_documents_by_remote_ids,
            str(base["id"]),
            remote_ids,
        )
        by_remote = {str(item["remote_document_id"]): item for item in local_docs}
        for upstream_doc in docs:
            local = by_remote.get(str(upstream_doc.get("id")))
            if local is None:
                continue
            status = self.config.compatibility.map_document_status(
                upstream_doc.get("run")
            )
            progress = upstream_doc.get("progress")
            if not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
                progress = None
            latest_operation: Mapping[str, Any] | None = None

            # RAGFlow may leave a task in RUNNING indefinitely after a parser or
            # embedding worker stalls. Honour the configured deadline so the UI
            # can show an actionable failure instead of an ever-growing timer.
            if status in {DocumentStatus.PENDING, DocumentStatus.PROCESSING}:
                active_operation = await self._repo(
                    self.repository.get_active_document_operation,
                    str(local["id"]),
                )
                latest_operation = active_operation
                if latest_operation is None:
                    latest_operation = await self._repo(
                        self.repository.get_latest_document_operation,
                        str(local["id"]),
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
                probe = self.config.compatibility.completed_document_probe
                chunk_count = upstream_doc.get("chunk_count", 0)
                token_count = upstream_doc.get("token_count", 0)
                counters_complete = (
                    isinstance(chunk_count, (int, float))
                    and isinstance(token_count, (int, float))
                    and chunk_count >= probe.min_chunk_count
                    and token_count >= probe.min_token_count
                )
                was_timed_out = (
                    str(local.get("error_code") or "") == "KNOWLEDGE_PARSE_TIMEOUT"
                )
                if (
                    probe.enabled
                    and (elapsed >= probe.grace_seconds or was_timed_out)
                    and counters_complete
                ):
                    try:
                        probe_result = await self.ragflow.retrieve(
                            question=probe.probe_question,
                            dataset_ids=[str(remote_dataset_id)],
                            document_ids=[str(upstream_doc.get("id"))],
                            top_n=1,
                            request_id=request_id,
                        )
                    except RagflowError:
                        probe_result = {}
                    exact_remote_id = str(upstream_doc.get("id"))
                    for chunk in probe_result.get("chunks", []):
                        if not isinstance(chunk, dict):
                            continue
                        snippet = str(
                            chunk.get("content_with_weight")
                            or chunk.get("content")
                            or ""
                        ).strip()
                        if str(chunk.get("document_id")) == exact_remote_id and snippet:
                            status = DocumentStatus.READY
                            progress = 1.0
                            break

                # A previous timeout may have been a false negative caused by
                # the upstream document/task projection split.  Keep it failed
                # unless the exact-document probe above proves the index ready.
                if (
                    status in {DocumentStatus.PENDING, DocumentStatus.PROCESSING}
                    and str(local.get("error_code") or "")
                    == "KNOWLEDGE_PARSE_TIMEOUT"
                ):
                    continue
                timeout = self.config.operations.parsing_poll.max_duration_seconds
                if status in {DocumentStatus.PENDING, DocumentStatus.PROCESSING} and elapsed > timeout:
                    timeout_message = (
                        f"解析超过 {int(timeout)} 秒仍未完成，请拆分文件后重试"
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
                    continue
            values: dict[str, object] = {
                "status": status.value,
                "progress": progress,
            }
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
            await self._repo(
                self.repository.update_document, local["id"], **values
            )
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
        await self._refresh_documents(base, request_id)
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
    ) -> DocumentUploadAccepted:
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
        if await self._repo(self.repository.count_documents, base_id) >= self.config.limits.max_documents_per_query:
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
        if not safe_name or len(safe_name.encode("utf-8")) > self.config.limits.max_filename_utf8_bytes:
            raise KnowledgeServiceError(
                "KNOWLEDGE_INVALID_FILENAME", "文件名无效或过长", status_code=422
            )
        extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if extension not in {value.lower() for value in self.config.limits.allowed_extensions}:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TYPE_NOT_ALLOWED",
                "不支持该文件类型",
                status_code=422,
            )
        try:
            await self._repo(
                validate_upload,
                content,
                filename=safe_name,
                content_type=content_type or "application/octet-stream",
                size_bytes=size_bytes,
                config=self.config.upload_security,
            )
        except UploadValidationError as exc:
            raise KnowledgeServiceError(
                exc.code,
                exc.message,
                status_code=503 if exc.retryable else 422,
                retryable=exc.retryable,
            ) from exc

        task_key = hashlib.sha256(
            f"upload:{principal.user_id}:{base_id}:{idempotency_key or request_id}".encode()
        ).hexdigest()
        if self.config.operations.worker.mode == "queue":
            existing_task = await self._repo(
                self.operations_repository.get_task_by_idempotency, task_key
            )
            if existing_task:
                existing_document = await self._repo(
                    self.repository.get_document, str(existing_task["resource_id"])
                )
                existing_operation = await self._repo(
                    self.repository.get_operation, str(existing_task["operation_id"])
                )
                if existing_document and existing_operation:
                    return DocumentUploadAccepted(
                        document=self._document_summary(existing_document, role, request_id),
                        operation=self._operation_summary(existing_operation, request_id),
                    )
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
            phase=(
                OperationPhase.STORING_ORIGINAL.value
                if self.config.original_storage.enabled
                else OperationPhase.UPLOADING.value
            ),
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
            retryable=True,
        )
        if self.config.original_storage.enabled:
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
            try:
                stored = await self._repo(
                    self.original_store.put_atomic,
                    storage_key,
                    content,
                    size_bytes,
                )
                await self._repo(
                    self.repository.update_original_object,
                    str(document["id"]),
                    status="available",
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    stored_at=datetime.now(UTC).isoformat(),
                    error_code=None,
                    error_message=None,
                )
                content.seek(0)
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
                operation = await self._repo(
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
        if self.config.operations.worker.mode == "queue":
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.QUEUED.value,
                status=OperationStatus.ACCEPTED.value,
                progress=0.35,
            )
            await self._repo(
                self.operations_repository.create_task,
                operation_id=str(operation["id"]),
                task_type="document_upload",
                resource_type="document",
                resource_id=str(document["id"]),
                idempotency_key=task_key,
                payload={"base_id": base_id},
                request_id=request_id,
                created_by=principal.user_id,
                max_attempts=self.config.operations.worker.max_attempts,
            )
            document = await self._repo(self.repository.get_document, str(document["id"]))
            assert document is not None and operation is not None
            return DocumentUploadAccepted(
                document=self._document_summary(document, role, request_id),
                operation=self._operation_summary(operation, request_id),
                poll_after_seconds=self.config.operations.worker.poll_interval_seconds,
            )
        try:
            remote = await self.ragflow.upload_document(
                dataset_id=str(base["remote_dataset_id"]),
                filename=safe_name,
                content=content,
                content_type=content_type or "application/octet-stream",
                request_id=request_id,
            )
            remote_id = str(remote["id"])
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
            await self.ragflow.configure_document(
                dataset_id=str(base["remote_dataset_id"]),
                document_id=remote_id,
                filename=safe_name,
                request_id=request_id,
            )
            await self.ragflow.start_parsing(
                dataset_id=str(base["remote_dataset_id"]),
                document_ids=[remote_id],
                request_id=request_id,
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
        except (RagflowError, KeyError) as exc:
            code = exc.code if isinstance(exc, RagflowError) else "RAGFLOW_CONTRACT_MISMATCH"
            retryable = exc.retryable if isinstance(exc, RagflowError) else False
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
            raise KnowledgeServiceError(
                "KNOWLEDGE_UPSTREAM_ERROR",
                "知识底座返回了无效文档数据",
                status_code=502,
            ) from exc
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
                    exc.code, exc.message, status_code=503,
                    retryable=exc.retryable,
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
        if (
            not self.config.original_storage.reads.legacy_fallback_to_ragflow
            or not document.get("remote_document_id")
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档原文件暂不可用", status_code=409
            )
        try:
            binary = await self.ragflow.download_document(
                dataset_id=str(base["remote_dataset_id"]),
                document_id=str(document["remote_document_id"]),
                request_id=request_id,
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
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        if str(document.get("status")) == DocumentStatus.DELETED.value:
            return
        base, _ = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.DELETE_DOCUMENT
        )
        original = await self._repo(
            self.repository.get_original_object, document_id
        )
        if not document.get("remote_document_id") and original is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档映射不存在", status_code=409
            )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DOCUMENT_DELETE.value,
            resource_type="document",
            resource_id=document_id,
            phase=OperationPhase.DELETING.value,
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
            retryable=True,
        )
        if self.config.operations.worker.mode == "queue":
            deleted_at = datetime.now(UTC)
            purge_after = deleted_at + timedelta(
                days=self.config.original_storage.deletion.retention_days
            )
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
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.QUEUED.value,
                status=OperationStatus.ACCEPTED.value,
            )
            task_key = hashlib.sha256(
                f"delete:{principal.user_id}:{document_id}:{request_id}".encode()
            ).hexdigest()
            await self._repo(
                self.operations_repository.create_task,
                operation_id=str(operation["id"]),
                task_type="document_delete",
                resource_type="document",
                resource_id=document_id,
                idempotency_key=task_key,
                payload={"base_id": str(base["id"])},
                request_id=request_id,
                created_by=principal.user_id,
                max_attempts=self.config.operations.worker.max_attempts,
            )
            return
        await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.DELETING.value,
        )
        try:
            if document.get("remote_document_id"):
                await self.ragflow.delete_documents(
                    dataset_id=str(base["remote_dataset_id"]),
                    document_ids=[str(document["remote_document_id"])],
                    request_id=request_id,
                )
        except RagflowError as exc:
            await self._repo(
                self.repository.update_document,
                document_id,
                status=DocumentStatus.FAILED.value,
                error_code=exc.code,
                error_message="文档删除失败",
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="文档删除失败",
            )
            raise self._upstream_error(exc) from exc
        try:
            await self._retire_original(document_id)
        except OriginalStorageError as exc:
            await self._repo(
                self.repository.update_document,
                document_id,
                remote_document_id=None,
                status=DocumentStatus.DELETED.value,
                progress=None,
                error_code=exc.code,
                error_message="文档已删除，原文待清理",
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.PARTIAL_FAILED.value,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="文档已删除，原文待清理",
            )
            raise KnowledgeServiceError(
                exc.code,
                "文档已删除，原文清理失败",
                status_code=503,
                retryable=exc.retryable,
            ) from exc
        await self._repo(
            self.repository.update_document,
            document_id,
            remote_document_id=None,
            status=DocumentStatus.DELETED.value,
            progress=None,
        )
        await self._repo(
            self.repository.update_operation,
            operation["id"],
            phase=OperationPhase.COMPLETED.value,
            status=OperationStatus.SUCCEEDED.value,
            current_count=1,
            progress=1.0,
        )

    async def retry_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> DocumentUploadAccepted:
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
        if not remote_id and not (
            original_record is not None
            and str(original_record.get("status")) == "available"
            and self.original_store is not None
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档原文件不可用", status_code=409
            )
        task_key = hashlib.sha256(
            f"retry:{principal.user_id}:{document_id}:{idempotency_key or request_id}".encode()
        ).hexdigest()
        if self.config.operations.worker.mode == "queue":
            existing_task = await self._repo(
                self.operations_repository.get_task_by_idempotency, task_key
            )
            if existing_task:
                existing_operation = await self._repo(
                    self.repository.get_operation, str(existing_task["operation_id"])
                )
                if existing_operation:
                    return DocumentUploadAccepted(
                        document=self._document_summary(document, role, request_id),
                        operation=self._operation_summary(existing_operation, request_id),
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
        if self.config.operations.worker.mode == "queue":
            await self._repo(
                self.repository.update_document,
                document_id,
                status=DocumentStatus.PENDING.value,
                error_code=None,
                error_message=None,
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.QUEUED.value,
                status=OperationStatus.ACCEPTED.value,
                progress=0.0,
            )
            await self._repo(
                self.operations_repository.create_task,
                operation_id=str(operation["id"]),
                task_type="document_retry",
                resource_type="document",
                resource_id=document_id,
                idempotency_key=task_key,
                payload={"base_id": str(base["id"])},
                request_id=request_id,
                created_by=principal.user_id,
                max_attempts=self.config.operations.worker.max_attempts,
            )
            document = await self._repo(self.repository.get_document, document_id)
            assert document is not None and operation is not None
            return DocumentUploadAccepted(
                document=self._document_summary(document, role, request_id),
                operation=self._operation_summary(operation, request_id),
                poll_after_seconds=self.config.operations.worker.poll_interval_seconds,
            )
        source: BinaryIO | None = None
        try:
            if (
                original_record is not None
                and str(original_record.get("status")) == "available"
                and self.original_store is not None
            ):
                handle = await self._repo(
                    self.original_store.open,
                    str(original_record["storage_key"]),
                )
                source = handle.open_binary()
            elif remote_id and self.config.original_storage.reads.legacy_fallback_to_ragflow:
                legacy = await self.ragflow.download_document(
                    dataset_id=str(base["remote_dataset_id"]),
                    document_id=str(remote_id),
                    request_id=request_id,
                )
                source = io.BytesIO(legacy.content)
            else:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE",
                    "文档原文件不可用",
                    status_code=409,
                )
            if remote_id:
                await self.ragflow.delete_documents(
                    dataset_id=str(base["remote_dataset_id"]),
                    document_ids=[str(remote_id)],
                    request_id=request_id,
                )
            await self._repo(
                self.repository.update_document,
                document_id,
                remote_document_id=None,
                status=DocumentStatus.PENDING.value,
                error_code=None,
                error_message=None,
            )
            remote = await self.ragflow.upload_document(
                dataset_id=str(base["remote_dataset_id"]),
                filename=str(document["name"]),
                content=source,
                content_type=str(document["content_type"]),
                request_id=request_id,
            )
            new_remote_id = str(remote["id"])
            await self._repo(
                self.repository.update_document,
                document_id,
                remote_document_id=new_remote_id,
            )
            await self.ragflow.configure_document(
                dataset_id=str(base["remote_dataset_id"]),
                document_id=new_remote_id,
                filename=str(document["name"]),
                request_id=request_id,
            )
            await self.ragflow.start_parsing(
                dataset_id=str(base["remote_dataset_id"]),
                document_ids=[new_remote_id],
                request_id=request_id,
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
        except OriginalStorageError as exc:
            document = await self._repo(
                self.repository.update_document,
                document_id,
                status=DocumentStatus.FAILED.value,
                error_code=exc.code,
                error_message="文档原文件不可用",
            )
            operation = await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="文档重试失败",
            )
            raise KnowledgeServiceError(
                exc.code, exc.message, status_code=503, retryable=exc.retryable
            ) from exc
        except (RagflowError, KeyError) as exc:
            code = exc.code if isinstance(exc, RagflowError) else "RAGFLOW_CONTRACT_MISMATCH"
            retryable = exc.retryable if isinstance(exc, RagflowError) else False
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

    async def restore_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> DocumentUploadAccepted:
        document = await self._repo(self.repository.get_document, document_id)
        if document is None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识资源不存在", status_code=404
            )
        _, role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.RESTORE_DOCUMENT
        )
        if str(document["status"]) != DocumentStatus.DELETED.value:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RESTORE_NOT_ALLOWED", "当前文档不需要恢复", status_code=409
            )
        purge_after = document.get("purge_after")
        if purge_after:
            try:
                if datetime.fromisoformat(str(purge_after)) <= datetime.now(UTC):
                    raise KnowledgeServiceError(
                        "KNOWLEDGE_RESTORE_EXPIRED", "文档已超过恢复期", status_code=409
                    )
            except ValueError:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_RESTORE_NOT_ALLOWED", "文档恢复信息无效", status_code=409
                )
        original = await self._repo(self.repository.get_original_object, document_id)
        if original is None or str(original.get("status")) == "deleted":
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档原文已不可用", status_code=409
            )
        if str(original.get("status")) == "quarantined":
            if self.original_store is None:
                raise KnowledgeServiceError(
                    "ORIGINAL_STORAGE_NOT_READY", "原文存储未就绪", status_code=503,
                    retryable=True,
                )
            storage_key = str(original["storage_key"])
            target_key = storage_key.removeprefix("quarantine/")
            try:
                await self._repo(self.original_store.restore, storage_key, target_key)
                original = await self._repo(
                    self.repository.update_original_object, document_id,
                    storage_key=target_key, status="available", quarantined_at=None,
                    error_code=None, error_message=None,
                )
            except OriginalStorageError as exc:
                raise KnowledgeServiceError(
                    exc.code, "原文恢复失败", status_code=503,
                    retryable=exc.retryable,
                ) from exc
        await self._repo(
            self.operations_repository.cancel_pending_tasks,
            resource_id=document_id, task_type="document_delete",
        )
        await self._repo(
            self.repository.update_document, document_id,
            status=DocumentStatus.FAILED.value, deleted_at=None, deleted_by=None,
            purge_after=None, error_code=None, error_message=None,
        )
        if self.config.operations.worker.mode == "inline":
            return await self.retry_document(
                document_id=document_id, principal=principal, request_id=request_id
            )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DOCUMENT_PARSE.value,
            resource_type="document", resource_id=document_id,
            phase=OperationPhase.QUEUED.value, status=OperationStatus.ACCEPTED.value,
            created_by=principal.user_id, retryable=True,
        )
        task_key = hashlib.sha256(
            f"restore:{principal.user_id}:{document_id}:{request_id}".encode()
        ).hexdigest()
        await self._repo(
            self.operations_repository.create_task,
            operation_id=str(operation["id"]), task_type="document_retry",
            resource_type="document", resource_id=document_id,
            idempotency_key=task_key, payload={"restore": True}, request_id=request_id,
            created_by=principal.user_id,
            max_attempts=self.config.operations.worker.max_attempts,
        )
        document = await self._repo(
            self.repository.update_document, document_id, status=DocumentStatus.PENDING.value
        )
        assert document is not None
        return DocumentUploadAccepted(
            document=self._document_summary(document, role, request_id),
            operation=self._operation_summary(operation, request_id),
            poll_after_seconds=self.config.operations.worker.poll_interval_seconds,
        )

    async def retrieve(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        question: str,
        document_ids: list[str] | None,
        top_n: int,
        request_id: str,
    ) -> RetrieveResponse:
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
                item
                for item in documents
                if item["status"] == DocumentStatus.READY.value
            ]
        documents = [item for item in documents if item.get("remote_document_id")]
        if not documents:
            return RetrieveResponse(
                evidence=[], warnings=["当前范围没有可检索的已解析文档"], request_id=request_id
            )
        try:
            result = await self.ragflow.retrieve(
                question=question,
                dataset_ids=[str(base["remote_dataset_id"])],
                document_ids=[str(item["remote_document_id"]) for item in documents],
                top_n=top_n,
                request_id=request_id,
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
        minimum_score = self.config.quality_baseline.minimum_evidence_score
        evidence = [item for item in evidence if item.score >= minimum_score]
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
        """Retrieve across the currently selected, still-authorized bases."""

        scope = await self.get_session_scope(
            session_id=session_id, principal=principal
        )
        if not scope.base_ids:
            return RetrieveResponse(evidence=[], request_id=request_id)

        evidence: list[Evidence] = []
        warnings: list[str] = []
        for base_id in scope.base_ids:
            result = await self.retrieve(
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
        """Return a safe document inventory for the selected, authorized bases.

        Retrieval results are ranked chunks and may represent only a subset of
        documents. The chat model needs this separate inventory to answer scope
        questions without mistaking chunk hits for the complete library.
        """

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
                            if isinstance(progress, (int, float))
                            and 0 <= progress <= 1
                            else None
                        ),
                    }
                )
        return catalog

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

    async def get_operation(
        self,
        *,
        operation_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> OperationSummary:
        row = await self._repo(self.repository.get_operation, operation_id)
        if row is None or str(row["created_by"]) != principal.user_id:
            raise KnowledgeServiceError(
                "KNOWLEDGE_NOT_FOUND", "知识任务不存在", status_code=404
            )
        return self._operation_summary(row, request_id)

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
        return SessionKnowledgeScopeResponse(
            session_id=session_id, base_ids=visible
        )

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
    "allowed_actions",
    "effective_role",
]
