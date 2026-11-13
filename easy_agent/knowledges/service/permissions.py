"""鉴权与授权：知识库角色判定、部门管理员门槛与权限名单管理。"""

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

    async def _has_department_management_grant(
        self, principal: KnowledgePrincipal, department_id: str
    ) -> bool:
        """判断用户是否是指定部门的知识库管理员。"""

        if not department_id:
            return False
        return await self._repo(
            self.repository.is_department_manager, principal.user_id, department_id
        )

    async def _can_create_team_base(self, principal: KnowledgePrincipal) -> bool:
        """判断用户是否可以创建公共空间（TEAM）知识库。

        两种情况返回 True：
        1. 超级管理员（admin / liufengbao，全局直通）
        2. 知识库管理员（在自己所属部门）
        """
        if is_super_admin(principal.username):
            return True
        if principal.department_id and await self._has_department_management_grant(
            principal, principal.department_id
        ):
            return True
        return False

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
        """全量替换可见人员名单并更新勾选名单（受限）标记。

        权限管理新语义：仅支持按人员授权，角色一律为查看者（viewer）；
        restrict_access=True 时该库进入勾选名单模式，公共空间默认的
        同部门可见让位于勾选人员（owner、admin 不受影响）。
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
        return self._permission_list_response(
            rows, restrict_access=restrict_access
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

        与库权限管理目录同一数据源；门槛对齐公共空间创建：需知识库管理员授权，
        避免任意账号拉取全员通讯目录。
        """

        if not await self._can_create_team_base(principal):
            raise KnowledgeServiceError(
                "KNOWLEDGE_TEAM_SPACE_MANAGER_REQUIRED",
                "当前账号未获得公共空间创建与管理权限，请联系 admin 或部门管理员配置",
                status_code=403,
            )
        rows = await self._repo(
            self.repository.list_permission_directory_users,
            exclude_user_id=str(principal.user_id or ""),
        )
        return self._permission_directory_response(rows)
