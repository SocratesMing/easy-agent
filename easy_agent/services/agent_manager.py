"""Agent lifecycle management - creation, caching, and cleanup"""

import asyncio
import logging

from ..agent import EasyAgent
from ..config import Config
from ..db import get_database
from ..model import create_model

logger = logging.getLogger("easy_agent.chat_service")

_session_agents: dict[str, EasyAgent] = {}
_session_stream_tasks: dict[str, asyncio.Task[None]] = {}
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

    # 查询用户机构ID，注入系统提示词
    organization_id = ""
    try:
        db = get_database()
        user = db.get_user_by_username(username)
        if user:
            organization_id = user.organization_id or ""
    except Exception as e:
        logger.warning(f"[{session_id[-5:]}] 获取用户机构ID失败: {e}")

    agent = EasyAgent(
        config=_agent_config["config"],
        system_prompt=_agent_config["system_prompt"],
        skills_root=_agent_config.get("skills_root", ""),
        username=username,
        session_id=session_id,
        workspace_name=workspace_name,
        mcp_tools=_mcp_tools,
        organization_id=organization_id,
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


def register_stream_task(session_id: str, task: asyncio.Task[None]) -> None:
    """注册会话的流式输出任务，使其可被取消。

    若该会话已存在未完成的流式任务（通常不应发生），先取消旧的，
    避免多个 astream 协程并发写同一会话。
    """
    old = _session_stream_tasks.get(session_id)
    if old and not old.done():
        old.cancel()
    _session_stream_tasks[session_id] = task


def unregister_stream_task(session_id: str) -> None:
    """流式任务结束后注销自身。"""
    _session_stream_tasks.pop(session_id, None)


async def cancel_stream_task(session_id: str) -> bool:
    """取消会话正在运行的流式任务。

    通过 task.cancel() 向 astream 协程注入 CancelledError，
    streaming.py 的 except CancelledError 分支会保存已生成的部分回复。
    Returns:
        True 表示确实取消了一个正在运行的任务。
    """
    import time as _time

    task = _session_stream_tasks.pop(session_id, None)
    if not task:
        logger.warning(
            f"[{session_id[-5:]}] 取消流式任务 | 未找到注册的任务（可能已结束或未注册）"
        )
        return False
    if task.done():
        logger.info(
            f"[{session_id[-5:]}] 取消流式任务 | 任务已完成（done=True），无需取消"
        )
        return False

    task_name = task.get_name() if hasattr(task, "get_name") else "?"
    logger.info(
        f"[{session_id[-5:]}] 取消流式任务 | 正在取消 task={task_name} | "
        f"cancelled={task.cancelled()} | "
        f"当前事件循环任务数={len(asyncio.all_tasks())}"
    )

    # 注入 CancelledError
    cancelled_flag = task.cancel()
    logger.info(
        f"[{session_id[-5:]}] 取消流式任务 | task.cancel() 返回 {cancelled_flag}（True=成功请求取消）"
    )

    # 等待任务真正终止，以便 CancelledError 分支完成持久化
    t0 = _time.time()
    try:
        await asyncio.wait_for(task, timeout=3.0)
        logger.info(
            f"[{session_id[-5:]}] 取消流式任务 | 任务已终止 | 耗时 {_time.time() - t0:.2f}s"
        )
    except asyncio.CancelledError:
        logger.info(
            f"[{session_id[-5:]}] 取消流式任务 | 任务收到 CancelledError 已终止 | "
            f"耗时 {_time.time() - t0:.2f}s"
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[{session_id[-5:]}] 取消流式任务 | 等待任务终止超时（3s），任务可能仍在后台运行"
        )
    except Exception as e:
        logger.warning(
            f"[{session_id[-5:]}] 取消流式任务 | 任务终止时抛出异常: {type(e).__name__}: {e}"
        )
    return True


def get_agent_config() -> dict:
    return _agent_config
