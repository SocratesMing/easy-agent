"""知识工程 BFF 端点（/agent/knowledge/v1，按前端实际调用面裁剪）。

各业务端点按职责拆到同级子模块（bases/folders/documents/permissions/chat），
本文件只做路由组合与模块能力/状态探测；原文存储固定为本地落盘
（``app.state.knowledge_original_store``）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.models import (
    KnowledgeCapabilitiesResponse,
    KnowledgeModuleStatus,
    KnowledgePrincipal,
    KnowledgeStatusResponse,
)
from easy_agent.knowledges.ragflow import RagflowError

from .bases import router as bases_router
from .chat import router as chat_router
from .deps import _get_knowledge_config, get_knowledge_principal
from .documents import router as documents_router
from .folders import router as folders_router
from .permissions import router as permissions_router


router = APIRouter(prefix="/agent/knowledge/v1", tags=["knowledge-engineering"])
router.include_router(bases_router)
router.include_router(folders_router)
router.include_router(documents_router)
router.include_router(permissions_router)
router.include_router(chat_router)


# ---------------------------------------------------------------------------
# 模块状态
# ---------------------------------------------------------------------------
@router.get("/capabilities", response_model=KnowledgeCapabilitiesResponse)
async def get_capabilities(
    request: Request,
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> KnowledgeCapabilitiesResponse:
    """查询知识模块能力开关与特性清单。"""

    del principal
    config = _get_knowledge_config(request)
    if not config.enabled:
        return KnowledgeCapabilitiesResponse(
            enabled=False, status=KnowledgeModuleStatus.DISABLED
        )
    return KnowledgeCapabilitiesResponse(
        enabled=True,
        status=KnowledgeModuleStatus.CONFIGURED,
        features={
            "document_retry": True,
            "document_preview": True,
            "original_document_storage": True,
            "agent_original_access": True,
            "user_department_permissions": True,
            "session_knowledge_scope": True,
        },
    )


@router.get("/status", response_model=KnowledgeStatusResponse)
async def get_status(
    request: Request,
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> KnowledgeStatusResponse:
    """查询知识模块整体状态：配置、Ragflow 与原文存储逐项探测。"""

    del principal
    config = _get_knowledge_config(request)
    if not config.enabled:
        return KnowledgeStatusResponse(
            enabled=False, ready=False, status=KnowledgeModuleStatus.DISABLED
        )
    ragflow = getattr(request.app.state, "ragflow_client", None)
    if ragflow is None:
        return KnowledgeStatusResponse(
            enabled=True, ready=False, status=KnowledgeModuleStatus.DEGRADED
        )
    try:
        await ragflow.health()
    except RagflowError:
        return KnowledgeStatusResponse(
            enabled=True, ready=False, status=KnowledgeModuleStatus.DEGRADED
        )
    original_store = getattr(request.app.state, "knowledge_original_store", None)
    if original_store is None or not await run_in_threadpool(original_store.health):
        return KnowledgeStatusResponse(
            enabled=True, ready=False, status=KnowledgeModuleStatus.DEGRADED
        )
    return KnowledgeStatusResponse(
        enabled=True, ready=True, status=KnowledgeModuleStatus.READY
    )


__all__ = ["router"]
