"""知识库模块生命周期：配置加载 fail-fast、运行时资源挂载与回收。

与旧版的差异：
- 幂等：宿主可能重复调用 startup，以 ``app.state.knowledge_config`` 为守卫；
- 数据库晚于本函数初始化，worker 通过 ``db_provider`` 惰性获取连接；
- 原文存储固定为 ``LocalOriginalStore``，不再有可配置的存储后端。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from .config import KnowledgeConfig, KnowledgeConfigError
from .ragflow import RagflowClient
from .service import LocalOriginalStore
from .worker import KnowledgeParsePollWorker

logger = logging.getLogger(__name__)


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
        logger.info("知识库配置加载成功 | status=disabled")
        return

    app.state.ragflow_client = RagflowClient(
        config.base_url,
        config.api_key.get_secret_value(),
    )
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
    logger.info("知识库配置加载成功 | status=enabled")


async def shutdown_knowledge(app: FastAPI) -> None:
    """停止轮询 worker 并关闭模块持有的连接。"""

    worker = getattr(app.state, "knowledge_worker", None)
    if worker is not None:
        await worker.stop()
        app.state.knowledge_worker = None

    client = getattr(app.state, "ragflow_client", None)
    if client is not None:
        await client.close()
        app.state.ragflow_client = None


__all__ = ["shutdown_knowledge", "startup_knowledge"]
