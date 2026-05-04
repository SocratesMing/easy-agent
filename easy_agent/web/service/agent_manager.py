"""Agent lifecycle management - creation, caching, and cleanup"""

import logging

from ...agent import EasyAgent
from ...config import Config

logger = logging.getLogger("easy_agent.chat_service")

_session_agents: dict[str, EasyAgent] = {}
_agent_config: dict = None
_llm_instance = None


def init_agent_config(config: Config, system_prompt: str, skills_root: str = "", shared_deps_path: str = ""):
    global _agent_config, _llm_instance
    _agent_config = {
        "config": config,
        "system_prompt": system_prompt,
        "skills_root": skills_root,
        "shared_deps_path": shared_deps_path,
    }
    from ...model import create_model
    _llm_instance = create_model(config)
    logger.info("[初始化] Agent 配置初始化完成 | LLM 流式已启用")


async def get_or_create_agent_for_session(session_id: str, username: str = "default", workspace_name: str = "") -> EasyAgent:
    global _session_agents, _agent_config

    if session_id in _session_agents:
        return _session_agents[session_id]

    if _agent_config is None:
        raise RuntimeError("Agent 配置未初始化")

    logger.info(f"[{session_id[-5:]}] 为会话创建 Agent 实例 | username={username} | session_id={session_id}")

    agent = EasyAgent(
        config=_agent_config["config"],
        system_prompt=_agent_config["system_prompt"],
        skills_root=_agent_config.get("skills_root", ""),
        username=username,
        session_id=session_id,
        shared_deps_path=_agent_config.get("shared_deps_path", ""),
        workspace_name=workspace_name,
    )
    _session_agents[session_id] = agent
    logger.info(f"[{session_id[-5:]}] Agent 实例创建成功 | workspace: {agent.workspace_dir}")
    return agent


def remove_session_agent(session_id: str):
    global _session_agents
    if session_id in _session_agents:
        del _session_agents[session_id]
        logger.info(f"[{session_id[-5:]}] Agent 缓存已清除")


def get_agent_config() -> dict:
    return _agent_config
