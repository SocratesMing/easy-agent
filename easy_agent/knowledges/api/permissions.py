"""知识库权限路由：单库授权名单、权限目录（管理员在库内「权限管理」中配置）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from easy_agent.knowledges.core.models import (
    KnowledgePrincipal,
    PermissionDirectoryListResponse,
    PermissionListResponse,
    PermissionReplaceRequest,
)
from easy_agent.knowledges.service import KnowledgeService

from .deps import _call, _service, get_knowledge_principal


router = APIRouter()


@router.get("/bases/{base_id}/permissions", response_model=PermissionListResponse)
async def get_permissions(
    base_id: str,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> PermissionListResponse:
    """查询知识库的授权列表（查看者 + 知识库管理员）。"""

    return await _call(service.get_permissions, base_id, principal)


@router.put("/bases/{base_id}/permissions", response_model=PermissionListResponse)
async def replace_permissions(
    base_id: str,
    payload: PermissionReplaceRequest,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> PermissionListResponse:
    """全量替换知识库授权名单并更新受限标记。

    所有者/超级管理员可同时配置知识库管理员（maintainer）与查看者（viewer）；
    被授权的管理员只能调整查看者名单，服务层会保留既有管理员行。
    """

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
    """创建知识库时的可选人员目录（不依赖具体库，仅超级管理员可用）。"""

    return await _call(service.get_creation_permission_directory, principal)
