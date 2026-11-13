"""知识库权限路由：授权名单、权限目录与部门管理员配置。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from starlette.concurrency import run_in_threadpool

from easy_agent.db import Database, get_database
from easy_agent.knowledges.core.models import (
    DepartmentManagerGrantRequest,
    DepartmentManagerGrantResponse,
    DepartmentManagerListResponse,
    DepartmentManagerRevokeResponse,
    DepartmentManagerSummary,
    KnowledgePrincipal,
    PermissionDirectoryListResponse,
    PermissionListResponse,
    PermissionReplaceRequest,
)
from easy_agent.knowledges.repository import KnowledgeRepository
from easy_agent.knowledges.service import KnowledgeService
from easy_agent.utils.auth import is_super_admin

from .deps import _call, _record_manager_audit, _service, get_knowledge_principal


router = APIRouter()


@router.get("/bases/{base_id}/permissions", response_model=PermissionListResponse)
async def get_permissions(
    base_id: str,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> PermissionListResponse:
    """查询知识库的授权列表。"""

    return await _call(service.get_permissions, base_id, principal)


@router.put("/bases/{base_id}/permissions", response_model=PermissionListResponse)
async def replace_permissions(
    base_id: str,
    payload: PermissionReplaceRequest,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> PermissionListResponse:
    """全量替换知识库可见人员名单并更新受限标记。"""

    return await _call(
        service.replace_permissions,
        base_id=base_id,
        principal=principal,
        items=payload.items,
        restrict_access=payload.restrict_access,
    )


@router.get(
    "/bases/{base_id}/permission-directory",
    response_model=PermissionDirectoryListResponse,
)
async def get_permission_directory(
    base_id: str,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> PermissionDirectoryListResponse:
    """查询权限管理可选人员目录（按部门/处室/团队分组展示的前置数据）。"""

    return await _call(service.get_permission_directory, base_id, principal)


@router.get(
    "/bases/permission-directory",
    response_model=PermissionDirectoryListResponse,
)
async def get_creation_permission_directory(
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> PermissionDirectoryListResponse:
    """创建知识库时的可选人员目录（不依赖具体库，需知识库管理员授权）。"""

    return await _call(service.get_creation_permission_directory, principal)


# ---------------------------------------------------------------------------
# 管理员守卫
# ---------------------------------------------------------------------------
def _admin(
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
):
    """仅超级管理员账号（admin / liufengbao）可访问。"""

    if not is_super_admin(principal.username):
        raise HTTPException(status_code=403, detail="仅超级管理员可访问")
    return principal


# ---------------------------------------------------------------------------
# 知识库管理员（admin 可配置）
# ---------------------------------------------------------------------------
def _dept_manager_summary(item: dict) -> DepartmentManagerSummary:
    """将数据库行转换为知识库管理员摘要模型。"""

    return DepartmentManagerSummary(
        id=str(item["id"]),
        user_id=str(item["user_id"]),
        username=str(item.get("username") or ""),
        display_name=str(item.get("display_name") or ""),
        department_id=str(item.get("department_id") or ""),
        department_name=str(item.get("department_name") or ""),
        account_status=str(item.get("account_status") or "active"),
        granted_by=str(item["granted_by"]),
        granted_at=item["granted_at"],
        updated_at=item["updated_at"],
    )


@router.get(
    "/department-managers",
    response_model=DepartmentManagerListResponse,
    summary="查询知识库管理员列表",
)
async def list_department_managers(
    _: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
    department_id: str = Query(default="", max_length=255),
) -> DepartmentManagerListResponse:
    """查询知识库管理员列表（可选按部门过滤）。"""

    dept_filter = department_id.strip() or None
    rows = await run_in_threadpool(
        KnowledgeRepository(db).list_department_managers, dept_filter
    )
    return DepartmentManagerListResponse(
        items=[_dept_manager_summary(item) for item in rows]
    )


@router.post(
    "/department-managers",
    response_model=DepartmentManagerGrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="授予知识库管理员权限",
)
async def grant_department_manager(
    request: Request,
    principal: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
    payload: DepartmentManagerGrantRequest,
    department_id: str = Query(..., min_length=1, max_length=255),
) -> DepartmentManagerGrantResponse:
    """授予指定用户在指定部门的知识库管理员权限。"""

    repository = KnowledgeRepository(db)
    try:
        row = await run_in_threadpool(
            repository.grant_department_manager,
            user_id=payload.user_id,
            department_id=department_id,
            granted_by=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _record_manager_audit(
        request,
        db,
        principal,
        action="department_manager.grant",
        object_id=f"{payload.user_id}@{department_id}",
        department_id=department_id,
    )
    return DepartmentManagerGrantResponse(manager=_dept_manager_summary(row))


@router.delete(
    "/department-managers/{user_id}",
    response_model=DepartmentManagerRevokeResponse,
    summary="撤销知识库管理员权限",
)
async def revoke_department_manager(
    user_id: str,
    request: Request,
    principal: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
    department_id: str = Query(..., min_length=1, max_length=255),
) -> DepartmentManagerRevokeResponse:
    """撤销指定用户在指定部门的知识库管理员权限。"""

    ok = await run_in_threadpool(
        KnowledgeRepository(db).revoke_department_manager,
        user_id=user_id,
        department_id=department_id,
    )

    await _record_manager_audit(
        request,
        db,
        principal,
        action="department_manager.revoke",
        object_id=f"{user_id}@{department_id}",
        department_id=department_id,
    )
    return DepartmentManagerRevokeResponse(
        user_id=user_id, department_id=department_id, revoked=ok
    )
