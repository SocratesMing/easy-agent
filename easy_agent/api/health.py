"""健康检查、版本、运行指标与基础配置路由。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from easy_agent.middleware.auth import get_current_username
from easy_agent.models.api import HealthResponse
from easy_agent.services.agents.manager import get_agent_config
from easy_agent.services.health import ping_database, workspace_writable
from easy_agent.services.metrics import snapshot_metrics
from easy_agent.utils.auth import is_super_admin
from easy_agent.utils.version_info import get_version_info

logger = logging.getLogger(__name__)


def build_health_router(
    *,
    get_db: Callable[[], Any],
    get_agent_cfg: Callable[[], Any],
) -> APIRouter:
    """构建应用级健康/指标/配置路由，通过回调读取运行时状态以兼容测试替身。"""
    router = APIRouter()

    @router.get("/agent/live", summary="存活检查（liveness）", response_model=HealthResponse)
    @router.get("/api/live", summary="存活检查（liveness）", response_model=HealthResponse)
    async def liveness():
        return HealthResponse(status="live")

    @router.get("/agent/version", summary="部署版本")
    async def version_info():
        return get_version_info()

    @router.get("/agent/health", summary="健康检查", response_model=HealthResponse)
    async def health_check():
        return await liveness()

    async def _readiness_payload() -> HealthResponse:
        db_instance = get_db()
        agent_cfg = get_agent_cfg()
        db_ok, db_detail = await asyncio.to_thread(ping_database, db_instance)
        workspace_dir = "./workspace"
        try:
            cfg = get_agent_config()
            if cfg and cfg.get("config"):
                workspace_dir = cfg["config"].agent.workspace_dir
        except Exception as exc:  # noqa: BLE001 - 运行期配置缺失时按默认 workspace 目录探测可写性
            logger.debug("readiness 读取 workspace_dir 失败，回退默认目录: %s", exc)
        ws_ok, ws_detail = await asyncio.to_thread(workspace_writable, workspace_dir)
        ready = db_ok and ws_ok and agent_cfg is not None
        detail = ""
        if not db_ok:
            detail = f"database: {db_detail}"
        elif not ws_ok:
            detail = f"workspace: {ws_detail}"
        elif agent_cfg is None:
            detail = "agent not initialized"
        return HealthResponse(
            status="ready" if ready else "not_ready",
            agent_initialized=agent_cfg is not None,
            database_initialized=db_instance is not None,
            database_ready=db_ok,
            workspace_ready=ws_ok,
            detail=detail,
        )

    @router.get("/agent/ready", summary="就绪检查（readiness）", response_model=HealthResponse)
    @router.get("/api/ready", summary="就绪检查（readiness）", response_model=HealthResponse)
    async def readiness():
        payload = await _readiness_payload()
        if payload.status != "ready":
            return JSONResponse(status_code=503, content=payload.model_dump())
        return payload

    @router.get("/agent/metrics", summary="运行指标（RED，仅管理员）")
    async def runtime_metrics(username: str = Depends(get_current_username)):
        """进程内 RED 指标 JSON 快照；仅超级管理员可读。"""
        if not is_super_admin(username):
            raise HTTPException(status_code=403, detail="仅管理员可以查看运行指标")
        return JSONResponse(content=snapshot_metrics())

    @router.get("/agent/config", summary="获取Agent配置")
    async def get_config():
        _cfg = get_agent_config()
        if _cfg:
            return {
                "system_prompt": _cfg["system_prompt"][:100] + "...",
                "provider": _cfg["config"].llm.provider,
                "model": _cfg["config"].llm.model,
            }
        return {"status": "not initialized"}

    @router.get("/api/health", summary="健康检查", response_model=HealthResponse)
    async def api_health_check():
        return await health_check()

    @router.get("/api/config", summary="获取Agent配置")
    async def api_get_config():
        return await get_config()

    return router
