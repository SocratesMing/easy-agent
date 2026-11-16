"""知识库文件夹路由：列表、创建、批量确保、重命名与删除。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from easy_agent.knowledges.core.models import (
    FolderCreateRequest,
    FolderEnsureRequest,
    FolderEnsureResponse,
    FolderListResponse,
    FolderSummary,
    FolderUpdateRequest,
    KnowledgePrincipal,
)
from easy_agent.knowledges.service import KnowledgeService

from .deps import _call, _service, get_knowledge_principal


router = APIRouter()


@router.get("/bases/{base_id}/folders", response_model=FolderListResponse)
async def list_folders(
    base_id: str,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> FolderListResponse:
    """列出知识库内的全部文件夹。"""

    return await _call(service.list_folders, base_id, principal)


@router.post(
    "/bases/{base_id}/folders",
    response_model=FolderSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    base_id: str,
    payload: FolderCreateRequest,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> FolderSummary:
    """在知识库内创建文件夹（层级最多 5 层）。"""

    return await _call(
        service.create_folder,
        base_id=base_id,
        name=payload.name,
        parent_id=payload.parent_id,
        principal=principal,
    )


@router.post("/bases/{base_id}/folders/ensure", response_model=FolderEnsureResponse)
async def ensure_folders(
    base_id: str,
    payload: FolderEnsureRequest,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> FolderEnsureResponse:
    """文件夹上传时批量确保目录树存在（空目录也会创建），幂等。"""
    return await _call(
        service.ensure_folders,
        base_id=base_id,
        folder_id=payload.folder_id,
        paths=payload.paths,
        principal=principal,
    )


@router.patch("/folders/{folder_id}", response_model=FolderSummary)
async def update_folder(
    folder_id: str,
    payload: FolderUpdateRequest,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> FolderSummary:
    """重命名文件夹。"""

    return await _call(
        service.update_folder,
        folder_id=folder_id, name=payload.name, principal=principal
    )


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> Response:
    """删除文件夹及其全部内容（含子文件夹与文档）。"""

    await _call(service.delete_folder, folder_id=folder_id, principal=principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
