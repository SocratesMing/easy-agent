"""FastAPI lifecycle adapter for the self-contained knowledge module."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from .config import KnowledgeConfig, KnowledgeConfigError
from .ragflow import RagflowClient
from .storage import create_original_store

logger = logging.getLogger(__name__)


async def startup_knowledge(app: FastAPI, config_path: str | Path) -> None:
    """Load the knowledge section and attach module-owned runtime resources."""

    try:
        config = KnowledgeConfig.load(config_path)
        app.state.knowledge_config = config
        app.state.ragflow_client = RagflowClient(config) if config.enabled else None
        app.state.original_store = create_original_store(config)
        store = app.state.original_store
        if (
            config.original_storage.enabled
            and config.original_storage.health.probe_on_startup
            and (store is None or not store.health())
        ):
            if config.original_storage.health.required_for_readiness:
                raise KnowledgeConfigError("original document storage is not writable")
            logger.warning("原文存储启动探测失败，上传与原文访问可能不可用")
        logger.info(
            "知识库配置加载成功 | status=%s",
            "enabled" if config.enabled else "disabled",
        )
    except KnowledgeConfigError as exc:
        logger.error("知识库配置无效: %s", exc)
        raise RuntimeError("invalid knowledge base configuration") from exc


async def shutdown_knowledge(app: FastAPI) -> None:
    """Close resources owned by the knowledge module."""

    client = getattr(app.state, "ragflow_client", None)
    if client is not None:
        await client.close()
        app.state.ragflow_client = None
