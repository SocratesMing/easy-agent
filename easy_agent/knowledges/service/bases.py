"""知识库生命周期：列表、详情、创建、更新与删除。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from easy_agent.knowledges.core.models import (
    AllowedAction,
    KnowledgeBaseListResponse,
    KnowledgeBaseRole,
    KnowledgeBaseStatus,
    KnowledgeBaseSummary,
    KnowledgeBaseVisibility,
    OperationPhase,
    OperationStatus,
    OperationType,
    PageInfo,
    TeamSpaceDepartmentSummary,
    TeamSpaceManagementCapability,
)
from easy_agent.knowledges.service.contracts import KnowledgeServiceError
from easy_agent.utils.auth import is_super_admin

if TYPE_CHECKING:
    from easy_agent.knowledges.core.models import KnowledgePrincipal


class BaseMixin:
    """知识库生命周期方法；由 KnowledgeService 组合，复用其鉴权与摘要能力。"""

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
