"""知识库模块生命周期：配置加载 fail-fast、运行时资源挂载与回收。

幂等：宿主可能重复调用 startup，以 ``app.state.knowledge_config`` 为守卫；
数据库晚于本函数初始化，worker 通过 ``db_provider`` 惰性获取连接；
原文存储固定为 ``LocalOriginalStore``，无可配置的存储后端。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.config import KnowledgeConfig, KnowledgeConfigError
from easy_agent.knowledges.ragflow import RagflowClient
from easy_agent.knowledges.core.original_store import LocalOriginalStore
from easy_agent.knowledges.runtime.worker import KnowledgeParsePollWorker, KnowledgePurgeWorker

logger = logging.getLogger(__name__)

# 启动探活超时（秒）：只用于给出真实连接状态，避免 RAGFlow 离线时拖慢启动
_RAGFLOW_PROBE_TIMEOUT_SECONDS = 5.0


async def _probe_ragflow(client: RagflowClient, base_url: str) -> bool:
    """启动期短超时探活：结果只用于日志与状态可见性，失败不阻断启动。"""
    try:
        await asyncio.wait_for(
            client.health(), timeout=_RAGFLOW_PROBE_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - 探活失败只记录日志，不阻断启动
        reason = (
            f"探测超时(>{_RAGFLOW_PROBE_TIMEOUT_SECONDS:g}s)"
            if isinstance(exc, asyncio.TimeoutError)
            else (str(exc) or exc.__class__.__name__)
        )
        logger.error(
            "❌ 知识库 RAGFlow 连接失败 | status=enabled | ragflow=unreachable | "
            "base_url=%s | 原因: %s | 服务继续启动，RAGFlow 恢复后知识库自动可用",
            base_url,
            reason,
        )
        return False
    logger.info("✅ 知识库 RAGFlow 连接成功 | ragflow=connected | base_url=%s", base_url)
    return True


async def startup_knowledge(app: FastAPI, config_path: str | Path) -> None:
    """加载知识库配置并挂载模块运行时资源（幂等）。"""

    if getattr(app.state, "knowledge_config", None) is not None:
        return

    try:
        config = KnowledgeConfig.load(config_path)
    except KnowledgeConfigError as exc:
        # fail-fast：enabled 但凭据未解析等配置问题必须阻断启动
        logger.error("知识库配置无效: %s", exc)
        raise RuntimeError("invalid knowledge base configuration") from exc

    app.state.knowledge_config = config
    if not config.enabled:
        # 禁用态零副作用：不建客户端、不建目录、不起 worker
        app.state.ragflow_client = None
        app.state.knowledge_original_store = None
        app.state.knowledge_worker = None
        app.state.knowledge_purge_worker = None
        logger.info("知识库配置加载成功 | status=disabled")
        return

    app.state.ragflow_client = RagflowClient(
        config.base_url,
        config.api_key.get_secret_value(),
    )
    # 配置合法不等于服务可用：短超时探活一次，日志给出真实连接状态
    ragflow_ready = await _probe_ragflow(app.state.ragflow_client, config.base_url)
    store = LocalOriginalStore()
    if not await run_in_threadpool(store.health):
        raise RuntimeError("knowledge original storage is not writable")
    app.state.knowledge_original_store = store

    worker = KnowledgeParsePollWorker(
        config=config,
        ragflow=app.state.ragflow_client,
        original_store=store,
        db_provider=lambda: getattr(app.state, "db", None),
    )
    worker.start()
    app.state.knowledge_worker = worker

    # 删除时已同步清理远端副本；worker 兜底失败项与历史遗留，防止共享数据集膨胀
    purge_worker = KnowledgePurgeWorker(
        config=config,
        ragflow=app.state.ragflow_client,
        db_provider=lambda: getattr(app.state, "db", None),
    )
    purge_worker.start()
    app.state.knowledge_purge_worker = purge_worker
    logger.info(
        "知识库模块启动完成 | status=enabled | ragflow=%s",
        "connected" if ragflow_ready else "unreachable",
    )


async def shutdown_knowledge(app: FastAPI) -> None:
    """停止后台 worker 并关闭模块持有的连接。"""

    purge_worker = getattr(app.state, "knowledge_purge_worker", None)
    if purge_worker is not None:
        await purge_worker.stop()
        app.state.knowledge_purge_worker = None

    worker = getattr(app.state, "knowledge_worker", None)
    if worker is not None:
        await worker.stop()
        app.state.knowledge_worker = None

    client = getattr(app.state, "ragflow_client", None)
    if client is not None:
        await client.close()
        app.state.ragflow_client = None


__all__ = ["shutdown_knowledge", "startup_knowledge"]
