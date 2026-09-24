"""管理员运维端点（/agent/knowledge/v1/admin）。

旧版 9 个端点裁剪为 7 个：告警（alerts）与对账（reconciliation/runs、
reconciliation/issues）依赖的表与仓库在新版基础层中不存在，一并移除；
公共空间管理员/查看白名单、健康、指标、审计保留。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from ..db import Database, get_database
from ..services import get_agent_config
from .auth import KnowledgePrincipal, get_knowledge_principal
from .models import (
    TeamSpaceManagerListResponse,
    TeamSpaceManagerSummary,
    TeamSpaceManagerUpdateRequest,
    TeamSpaceManagerUpdateResponse,
    TeamSpaceViewerListResponse,
    TeamSpaceViewerSummary,
    TeamSpaceViewerUpdateRequest,
    TeamSpaceViewerUpdateResponse,
)
from .observability import metrics
from .operations_repository import KnowledgeOperationsRepository
from .ragflow import RagflowError
from .repository import KnowledgeRepository


router = APIRouter(prefix="/agent/knowledge/v1/admin", tags=["knowledge-operations"])


def _admin(principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)]):
    if principal.username != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可查看运维信息")
    return principal


def _team_manager_summary(item: dict) -> TeamSpaceManagerSummary:
    return TeamSpaceManagerSummary(
        user_id=str(item["user_id"]),
        username=str(item["username"]),
        display_name=str(item.get("display_name") or ""),
        department_id=str(item.get("department_id") or ""),
        department_name=str(item.get("department_name") or ""),
        account_status=str(item.get("account_status") or "active"),
        granted_by=str(item["granted_by"]),
        granted_at=item["granted_at"],
        updated_at=item["updated_at"],
    )


@router.get(
    "/team-space-managers",
    response_model=TeamSpaceManagerListResponse,
    summary="查询公共空间管理员白名单",
)
async def list_team_space_managers(
    _: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
) -> TeamSpaceManagerListResponse:
    rows = await run_in_threadpool(KnowledgeRepository(db).list_team_space_managers)
    return TeamSpaceManagerListResponse(
        items=[_team_manager_summary(item) for item in rows]
    )


@router.put(
    "/team-space-managers/{user_id}",
    response_model=TeamSpaceManagerUpdateResponse,
    summary="设置单个账号的公共空间管理权限",
)
async def set_team_space_manager(
    user_id: str,
    payload: TeamSpaceManagerUpdateRequest,
    request: Request,
    principal: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
) -> TeamSpaceManagerUpdateResponse:
    repository = KnowledgeRepository(db)
    manager = None
    try:
        if payload.enabled:
            row = await run_in_threadpool(
                repository.grant_team_space_manager,
                user_id=user_id,
                granted_by=principal.user_id,
            )
            manager = _team_manager_summary(row)
        else:
            target = await run_in_threadpool(db.get_user_by_id, user_id)
            if target is None:
                raise LookupError("用户不存在")
            if target.username == "admin":
                raise ValueError("admin 的全局权限不可取消")
            await run_in_threadpool(repository.revoke_team_space_manager, user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    config = getattr(request.app.state, "knowledge_config", None)
    if config is None or config.audit.enabled:
        await run_in_threadpool(
            KnowledgeOperationsRepository(db).record_audit,
            request_id=str(getattr(request.state, "request_id", "unknown")),
            actor_user_id=principal.user_id,
            actor_username=principal.username,
            action=(
                "team_space_manager.grant"
                if payload.enabled
                else "team_space_manager.revoke"
            ),
            object_type="team_space_manager",
            object_id=user_id,
            details={"enabled": payload.enabled},
            max_details_bytes=getattr(
                getattr(config, "audit", None), "max_details_bytes", 4096
            ),
        )
    return TeamSpaceManagerUpdateResponse(
        user_id=user_id,
        enabled=payload.enabled,
        manager=manager,
    )


def _team_viewer_summary(item: dict) -> TeamSpaceViewerSummary:
    return TeamSpaceViewerSummary(
        user_id=str(item["user_id"]),
        username=str(item["username"]),
        display_name=str(item.get("display_name") or ""),
        department_id=str(item.get("department_id") or ""),
        department_name=str(item.get("department_name") or ""),
        account_status=str(item.get("account_status") or "active"),
        granted_by=str(item["granted_by"]),
        granted_at=item["granted_at"],
        updated_at=item["updated_at"],
    )


@router.get(
    "/team-space-viewers",
    response_model=TeamSpaceViewerListResponse,
    summary="查询公共空间可查看权限白名单",
)
async def list_team_space_viewers(
    _: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
) -> TeamSpaceViewerListResponse:
    rows = await run_in_threadpool(KnowledgeRepository(db).list_team_space_viewers)
    return TeamSpaceViewerListResponse(
        items=[_team_viewer_summary(item) for item in rows]
    )


@router.put(
    "/team-space-viewers/{user_id}",
    response_model=TeamSpaceViewerUpdateResponse,
    summary="设置单个账号的公共空间查看权限",
)
async def set_team_space_viewer(
    user_id: str,
    payload: TeamSpaceViewerUpdateRequest,
    request: Request,
    principal: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
) -> TeamSpaceViewerUpdateResponse:
    repository = KnowledgeRepository(db)
    viewer = None
    try:
        if payload.enabled:
            row = await run_in_threadpool(
                repository.grant_team_space_viewer,
                user_id=user_id,
                granted_by=principal.user_id,
            )
            viewer = _team_viewer_summary(row)
        else:
            target = await run_in_threadpool(db.get_user_by_id, user_id)
            if target is None:
                raise LookupError("用户不存在")
            if target.username == "admin":
                raise ValueError("admin 的全局权限不可取消")
            await run_in_threadpool(repository.revoke_team_space_viewer, user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    config = getattr(request.app.state, "knowledge_config", None)
    if config is None or config.audit.enabled:
        await run_in_threadpool(
            KnowledgeOperationsRepository(db).record_audit,
            request_id=str(getattr(request.state, "request_id", "unknown")),
            actor_user_id=principal.user_id,
            actor_username=principal.username,
            action=(
                "team_space_viewer.grant"
                if payload.enabled
                else "team_space_viewer.revoke"
            ),
            object_type="team_space_viewer",
            object_id=user_id,
            details={"enabled": payload.enabled},
            max_details_bytes=getattr(
                getattr(config, "audit", None), "max_details_bytes", 4096
            ),
        )
    return TeamSpaceViewerUpdateResponse(
        user_id=user_id,
        enabled=payload.enabled,
        viewer=viewer,
    )


@router.get("/health")
async def operational_health(
    request: Request,
    _: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
):
    operations = KnowledgeOperationsRepository(db)
    components: dict[str, str] = {}
    try:

        def probe_db():
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()

        await run_in_threadpool(probe_db)
        components["database"] = "ready"
    except Exception:
        components["database"] = "unavailable"
    store = getattr(request.app.state, "knowledge_original_store", None)
    components["original_storage"] = (
        "ready"
        if store is not None and await run_in_threadpool(store.health)
        else "unavailable"
    )
    ragflow = getattr(request.app.state, "ragflow_client", None)
    try:
        if ragflow is None:
            raise RuntimeError
        await ragflow.health()
        components["ragflow"] = "ready"
    except (RagflowError, RuntimeError):
        components["ragflow"] = "unavailable"
    # 新版无心跳表：以轮询任务是否存活判定 worker 状态
    worker = getattr(request.app.state, "knowledge_worker", None)
    components["worker"] = (
        "ready" if worker is not None and worker.is_running else "unavailable"
    )
    counts = await run_in_threadpool(operations.task_counts)
    runtime = get_agent_config()
    components["model"] = (
        "ready" if runtime and runtime.get("config") is not None else "unavailable"
    )
    status_value = (
        "ready" if all(value == "ready" for value in components.values()) else "degraded"
    )
    return {
        "status": status_value,
        "components": components,
        "task_counts": counts,
        "request_id": getattr(request.state, "request_id", ""),
    }


@router.get("/metrics", response_class=PlainTextResponse)
async def operational_metrics(
    _: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
):
    counts = await run_in_threadpool(KnowledgeOperationsRepository(db).task_counts)
    return metrics.prometheus(counts)


@router.get("/audits")
async def list_audits(
    _: Annotated[KnowledgePrincipal, Depends(_admin)],
    db: Annotated[Database, Depends(get_database)],
    limit: int = 100,
):
    items = await run_in_threadpool(
        KnowledgeOperationsRepository(db).list_audits, limit=min(max(limit, 1), 500)
    )
    return {"items": items}


__all__ = ["router"]
