"""Agent lifecycle management - creation, caching, and cleanup"""

import logging

from ..agent import EasyAgent
from ..config import Config
from ..db import get_database
from ..model import create_model

logger = logging.getLogger("easy_agent.chat_service")

_session_agents: dict[str, EasyAgent] = {}
_agent_config: dict = None
_llm_instance = None
_mcp_tools: list = []  # LangChain BaseTool instances from MCP servers


def init_agent_config(
    config: Config,
    system_prompt: str,
    skills_root: str = "",
    mcp_tools: list | None = None,
):
    global _agent_config, _llm_instance, _mcp_tools
    _agent_config = {
        "config": config,
        "system_prompt": system_prompt,
        "skills_root": skills_root,
    }
    _mcp_tools = mcp_tools or []
    if _mcp_tools:
        logger.info(f"[初始化] MCP 工具已加载: {len(_mcp_tools)} 个工具")
        for t in _mcp_tools:
            logger.info(f"  └─ {t.name}")

    _llm_instance = create_model(config)
    logger.info("[初始化] Agent 配置初始化完成 | LLM 流式已启用")


def set_mcp_tools(tools: list):
    """Set or update MCP tools after startup (e.g., after async connection)."""
    global _mcp_tools
    _mcp_tools = tools or []
    logger.info(f"MCP tools updated: {len(_mcp_tools)} tools")
    for t in _mcp_tools:
        logger.info(f"  └─ {t.name}")


def get_mcp_tools() -> list:
    return _mcp_tools


async def get_or_create_agent_for_session(
    session_id: str, username: str = "default", workspace_name: str = ""
) -> EasyAgent:
    global _session_agents, _agent_config

    if session_id in _session_agents:
        return _session_agents[session_id]

    if _agent_config is None:
        raise RuntimeError("Agent 配置未初始化")

    if not workspace_name:
        try:
            db = get_database()
            session = db.get_session(session_id)
            if session and session.workspace_name:
                workspace_name = session.workspace_name
        except Exception:
            pass

    logger.info(
        f"[{session_id[-5:]}] 为会话创建 Agent 实例 | username={username} | session_id={session_id} | workspace_name={workspace_name}"
    )

    agent = EasyAgent(
        config=_agent_config["config"],
        system_prompt=_agent_config["system_prompt"],
        skills_root=_agent_config.get("skills_root", ""),
        username=username,
        session_id=session_id,
        workspace_name=workspace_name,
        mcp_tools=_mcp_tools,
    )
    _session_agents[session_id] = agent
    logger.info(
        f"[{session_id[-5:]}] Agent 实例创建成功 | workspace: {agent.workspace_dir}"
    )
    return agent


def remove_session_agent(session_id: str):
    global _session_agents
    if session_id in _session_agents:
        del _session_agents[session_id]
        logger.info(f"[{session_id[-5:]}] Agent 缓存已清除")


def get_agent_config() -> dict:
    return _agent_config
