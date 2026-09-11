"""Optional knowledge tools injected into a regular EasyAgent instance."""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from .config import KnowledgeConfig
from .original_tool import create_knowledge_original_tool

logger = logging.getLogger("easy-agent.chat_service")


def extend_agent_tools(
    tools: list,
    *,
    config: Config,
    username: str,
    session_id: str,
    workspace_name: str,
) -> list:
    """Return tools plus the permission-checked original-document tool."""

    extended = list(tools)
    try:
        knowledge_config = KnowledgeConfig.load()
        safe_username = Config.sanitize_username(username)
        directory_name = workspace_name or session_id
        workspace_dir = (
            Path(config.agent.workspace_dir)
            / safe_username
            / "session"
            / directory_name
        )
        original_tool = create_knowledge_original_tool(
            username=username,
            session_id=session_id,
            workspace_dir=workspace_dir,
            config=knowledge_config,
        )
        if original_tool is not None:
            extended.append(original_tool)
    except Exception as exc:
        sid = session_id[-5:] if session_id else "new"
        logger.warning("[%s] 注入知识库原文工具失败: %s", sid, exc)
    return extended
