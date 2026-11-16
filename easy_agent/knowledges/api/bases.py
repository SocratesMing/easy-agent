"""知识库生命周期路由：分页、创建、更新与删除。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from easy_agent.knowledges.core.models import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseSummary,
    KnowledgeBaseUpdateRequest,
    KnowledgePrincipal,
)
from easy_agent.knowledges.service import KnowledgeService

from .deps import _call, _request_id, _service, get_knowledge_principal


router = APIRouter()


@router.get("/bases", response_model=KnowledgeBaseListResponse)
async def list_bases(
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> KnowledgeBaseListResponse:
    """分页列出当前用户有权限访问的知识库。"""

    return await _call(service.list_bases, principal, page=page, page_size=page_size)


@router.post(
    "/bases",
    response_model=KnowledgeBaseSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_base(
    payload: KnowledgeBaseCreateRequest,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> KnowledgeBaseSummary:
    """创建知识库（个人空间/公共空间），公共空间需知识库管理员授权。"""

    return await _call(
        service.create_base,
        principal=principal,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility,
        request_id=_request_id(request),
        department_id=payload.department_id,
        restrict_access=payload.restrict_access,
        permission_user_ids=payload.permission_user_ids,
    )


@router.patch("/bases/{base_id}", response_model=KnowledgeBaseSummary)
async def update_base(
    base_id: str,
    payload: KnowledgeBaseUpdateRequest,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> KnowledgeBaseSummary:
    """更新知识库名称与描述。"""

    return await _call(
        service.update_base,
        base_id=base_id,
        principal=principal,
        name=payload.name,
        description=payload.description,
        request_id=_request_id(request),
    )


@router.delete("/bases/{base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_base(
    base_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> Response:
    """删除知识库：立即同步删除 RAGFlow 中的文档，本地保留软删除记录。"""

    await _call(
        service.delete_base,
        base_id=base_id,
        principal=principal,
        request_id=_request_id(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
