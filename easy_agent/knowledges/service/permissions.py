"""鉴权与授权：知识库角色判定、单库管理员授权与查看名单管理。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from easy_agent.knowledges.core.models import (
    AllowedAction,
    KnowledgeBaseRole,
    KnowledgeBaseStatus,
    KnowledgeBaseVisibility,
    PermissionDirectoryListResponse,
    PermissionDirectoryUser,
    PermissionEntry,
    PermissionListResponse,
)
from easy_agent.knowledges.core.permissions import (
    _base_access_restricted,
    allowed_actions,
    resolve_role,
)
from easy_agent.knowledges.service.contracts import KnowledgeServiceError, _not_found
from easy_agent.utils.auth import is_super_admin

if TYPE_CHECKING:
    from easy_agent.knowledges.core.models import KnowledgePrincipal


class PermissionMixin:
    """鉴权与授权方法；由 KnowledgeService 组合，复用其仓储读取能力。"""

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
            raise _not_found()
        permissions = await self._repo(self.repository.list_permissions, base_id)
        role = await self._effective_role(base, principal, permissions)
        if role is None:
            raise _not_found()
        if action not in allowed_actions(role):
            raise KnowledgeServiceError(
                "KNOWLEDGE_FORBIDDEN", "当前用户无权执行该操作", status_code=403
            )
        if str(base.get("status")) == KnowledgeBaseStatus.DELETED.value:
            raise _not_found()
        return base, role

    async def _can_create_team_base(self, principal: KnowledgePrincipal) -> bool:
        """判断用户是否可以创建公共空间（TEAM）知识库。

        知识库管理员改为「按库授权」后不再具备建库权限：公共空间只能由
        超级管理员创建；被授权的管理员仅在指定知识库上维护文档与查看权限。
        """

        return is_super_admin(principal.username)

    async def _resolve_team_department_id(
        self, principal: KnowledgePrincipal, requested_department_id: str | None
    ) -> str:
        """确定公共空间知识库的归属部门：超级管理员可选，其余账号固定本部门。"""

        if is_super_admin(principal.username):
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
            return target_department_id
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
        return principal.department_id

    async def _effective_role(
        self,
        base: Mapping[str, object],
        principal: KnowledgePrincipal,
        permissions: Iterable[Mapping[str, object]],
    ) -> KnowledgeBaseRole | None:
        """在单库授权之上叠加超级管理员全局权限。

        规则统一由 ``core.permissions.resolve_role`` 提供，与 Agent 原文工具
        共用同一口径：超级管理员（admin / liufengbao）对所有公共库直通
        MANAGER；owner 由 effective_role 直接判 MANAGER（owner 权限不落
        knowledge_permissions）；其余账号维持本部门默认只读、勾选名单与
        单库授权逻辑。
        """
        return resolve_role(
            base,
            principal,
            permissions,
            super_admin=is_super_admin(principal.username),
        )

    @staticmethod
    def _permission_list_response(
        rows: Iterable[Mapping[str, Any]], *, restrict_access: bool
    ) -> PermissionListResponse:
        """把权限行转换为对外响应（角色与主体原样回显）。"""

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

    async def get_permissions(
        self, base_id: str, principal: KnowledgePrincipal
    ) -> PermissionListResponse:
        """查询知识库授权列表与受限标记（需管理权限）。"""

        base, _ = await self._authorized_base(
            base_id, principal, AllowedAction.MANAGE_PERMISSIONS
        )
        rows = await self._repo(self.repository.list_permissions, base_id)
        return self._permission_list_response(
            rows, restrict_access=_base_access_restricted(base)
        )

    async def replace_permissions(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        items: list[PermissionEntry],
        restrict_access: bool = False,
    ) -> PermissionListResponse:
        """全量替换单库授权名单并更新勾选名单（受限）标记。

        知识库管理员改为按库授权（role=maintainer），由所有者或超级管理员在
        该库的「权限管理」中指定；被授权的管理员只能维护文档与查看者名单，
        不能创建/删除知识库，也不能再授权其他管理员。
        """

        base, role = await self._authorized_base(
            base_id, principal, AllowedAction.MANAGE_PERMISSIONS
        )
        if str(base["space_type"]) == KnowledgeBaseVisibility.PERSONAL.value and items:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PERSONAL_NOT_SHAREABLE",
                "个人空间知识库不可授权",
                status_code=409,
            )
        is_manager = role == KnowledgeBaseRole.MANAGER
        seen: set[str] = set()
        payload: list[dict[str, str]] = []
        for item in items:
            if item.subject_type != "user":
                raise KnowledgeServiceError(
                    "KNOWLEDGE_PERMISSION_SUBJECT_UNSUPPORTED",
                    "权限管理仅支持按人员授权",
                    status_code=409,
                )
            if item.role not in (
                KnowledgeBaseRole.VIEWER,
                KnowledgeBaseRole.MAINTAINER,
            ):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_PERMISSION_ROLE_UNSUPPORTED",
                    "权限管理仅支持查看者或知识库管理员",
                    status_code=409,
                )
            if item.role == KnowledgeBaseRole.MAINTAINER and not is_manager:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_PERMISSION_MANAGER_REQUIRED",
                    "只有知识库所有者或系统管理员可以配置知识库管理员",
                    status_code=403,
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
                    "role": item.role.value,
                }
            )
        # 知识库管理员保存查看名单时保留既有管理员行，避免越权撤销管理员。
        if not is_manager:
            existing = await self._repo(self.repository.list_permissions, base_id)
            preserved = [
                {
                    "subject_type": "user",
                    "subject_id": str(row["subject_id"]),
                    "role": KnowledgeBaseRole.MAINTAINER.value,
                }
                for row in existing
                if row["subject_type"] == "user"
                and row["role"] == KnowledgeBaseRole.MAINTAINER.value
            ]
            preserved_ids = {row["subject_id"] for row in preserved}
            payload = preserved + [
                row for row in payload if row["subject_id"] not in preserved_ids
            ]
        # 勾选了管理员/查看者即进入受限模式（与创建时的名单语义一致）。
        effective_restricted = bool(restrict_access or payload)
        # 先落受限标记再替换名单（fail-closed）：名单替换失败时最多是
        # 「可见面更窄」，不会出现已声明受限但仍对同部门开放的窗口。
        await self._repo(
            self.repository.update_base,
            base_id,
            access_restricted=1 if effective_restricted else 0,
        )
        rows = await self._repo(
            self.repository.replace_permissions,
            base_id=base_id,
            permissions=payload,
            created_by=principal.user_id,
        )
        return self._permission_list_response(
            rows, restrict_access=effective_restricted
        )

    @staticmethod
    def _permission_directory_response(
        rows: Iterable[Mapping[str, Any]],
    ) -> PermissionDirectoryListResponse:
        return PermissionDirectoryListResponse(
            items=[PermissionDirectoryUser(**row) for row in rows]
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
        return self._permission_directory_response(rows)

    async def get_creation_permission_directory(
        self, principal: KnowledgePrincipal
    ) -> PermissionDirectoryListResponse:
        """创建知识库时的可选人员目录（在职账号，剔除本人与 admin）。

        与库权限管理目录同一数据源；仅超级管理员可创建公共空间知识库，
        避免任意账号拉取全员通讯目录。
        """

        if not await self._can_create_team_base(principal):
            raise KnowledgeServiceError(
                "KNOWLEDGE_TEAM_SPACE_MANAGER_REQUIRED",
                "当前账号无权创建公共空间知识库，请联系系统管理员",
                status_code=403,
            )
        rows = await self._repo(
            self.repository.list_permission_directory_users,
            exclude_user_id=str(principal.user_id or ""),
        )
        return self._permission_directory_response(rows)
