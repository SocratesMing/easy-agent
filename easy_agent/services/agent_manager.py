"""Agent lifecycle management - creation, caching, and cleanup"""

import asyncio
import logging
import os
import platform

from ..agent import EasyAgent
from ..config import Config
from ..db import get_database
from ..model import create_model
from .mcp import get_mcp_tools as load_mcp_tools_for_user

logger = logging.getLogger("easy-agent.chat_service")

_session_agents: dict[str, EasyAgent] = {}
_session_stream_tasks: dict[str, asyncio.Task[None]] = {}
_agent_config: dict = None
_llm_instance = None


def _is_windows() -> bool:
    """Detect whether the server process runs on Windows.

    Auto-detects via ``platform.system()`` but can be overridden with the
    ``AGENT_WIN`` environment variable (``1/true/yes/on`` -> True,
    ``0/false/no/off`` -> False).
    """
    override = os.environ.get("AGENT_WIN", "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return True
    if override in ("0", "false", "no", "off"):
        return False
    return platform.system().lower().startswith("win")


def init_agent_config(
    config: Config,
    system_prompt: str,
    skills_root: str = "",
    agent_env: str = "",
):
    """初始化 Agent 全局配置。

    MCP 工具不再在启动时全局预加载，改为按用户配置动态加载
    （见 get_or_create_agent_for_session）。

    ``agent_env`` 为运行环境标识（dev/test/prod），``win`` 表示后端是否运行在
    Windows 系统上，二者均存入全局配置供接口与前端读取。
    """
    global _agent_config, _llm_instance
    _agent_config = {
        "config": config,
        "system_prompt": system_prompt,
        "skills_root": skills_root,
        "agent_env": agent_env or "",
        "win": _is_windows(),
    }

    _llm_instance = create_model(config)
    logger.info(
        f"[初始化] Agent 配置初始化完成 | 环境: {agent_env or '(未设置)'} | "
        f"Windows: {_is_windows()} | LLM 流式已启用 | MCP 按需动态加载"
    )


async def get_or_create_agent_for_session(
    session_id: str, username: str = "default", workspace_name: str = "",
   enable_hitl: bool = True, model_name: str | None = None,
    system_prompt_extra: str = "",
) -> EasyAgent:
    global _session_agents, _agent_config

    if _agent_config is None:
        raise RuntimeError("Agent 配置未初始化")

    config = _agent_config["config"]
    effective_model = model_name or config.active_model

    # 模型切换：若缓存的 Agent 使用了不同模型，驱逐后重建
    cached = _session_agents.get(session_id)
    if cached is not None:
        if effective_model != getattr(cached, "model_name", None):
            logger.info(
                f"[{session_id[-5:]}] 模型切换 | {getattr(cached, 'model_name', '?')} -> {effective_model} | 驱逐旧 Agent 重建"
            )
            _session_agents.pop(session_id, None)
        else:
            return cached

    if not workspace_name:
        try:
            db = get_database()
            session = db.get_session(session_id)
            if session and session.workspace_name:
                workspace_name = session.workspace_name
        except Exception:
            pass

    effective_model = model_name or config.active_model
    logger.info(
        f"[{session_id[-5:]}] 为会话创建 Agent 实例 | username={username} | session_id={session_id} | "
        f"workspace_name={workspace_name} | model={effective_model}"
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

    # MCP 工具：用户专属 mcp.json 优先，无则按需加载全局 mcp.json
    user_mcp_path = Config.get_user_mcp_path(username, config)
    if user_mcp_path.exists():
        logger.info(f"[{session_id[-5:]}] 加载用户专属 MCP | {user_mcp_path}")
        try:
            mcp_tools = await load_mcp_tools_for_user(username)
        except Exception as e:
            logger.warning(
                f"[{session_id[-5:]}] 加载用户 MCP 失败，尝试全局: {e}"
            )
            mcp_tools = await load_mcp_tools_for_user(None)
    else:
        # 无用户专属 mcp.json，按需加载全局 mcp.json（缓存命中时零开销）
        logger.info(
            f"[{session_id[-5:]}] 无用户专属 mcp.json，按需加载全局 MCP"
        )
        try:
            mcp_tools = await load_mcp_tools_for_user(None)
        except Exception as e:
            logger.warning(
                f"[{session_id[-5:]}] 加载全局 MCP 失败: {e}"
            )
            mcp_tools = []

    # 构建 tools 列表：MCP 工具 + 定时任务工具（仅 HITL 模式下注入）
    tools = list(mcp_tools)
    if enable_hitl:
        try:
            from ..tools.scheduled_task import CreateScheduledTaskTool
            tools.append(CreateScheduledTaskTool(username=username, session_id=session_id or ""))
        except Exception as e:
            logger.warning(f"[{session_id[-5:]}] 注入定时任务工具失败: {e}")

    agent = EasyAgent(
        config=config,
        system_prompt=_agent_config["system_prompt"],
        skills_root=_agent_config.get("skills_root", ""),
        username=username,
        session_id=session_id,
        workspace_name=workspace_name,
        mcp_tools=tools,
        organization_id=organization_id,
       enable_hitl=enable_hitl,
       model_name=model_name,
        system_prompt_extra=system_prompt_extra,
   )
    _session_agents[session_id] = agent
    logger.info(
        f"[{session_id[-5:]}] Agent 实例创建成功 | workspace: {agent.workspace_dir} | "
        f"model: {agent.model_name} | mcp_tools: {len(tools)}"
    )
    return agent


def invalidate_user_agents(username: str) -> int:
    """Evict all cached agents belonging to ``username``.

    Called after the user edits their mcp.json (or other per-user config) so
    that the next request rebuilds the agent with fresh settings. Returns the
    number of evicted agents.
    """
    global _session_agents
    if not username:
        return 0
    safe = Config.sanitize_username(username)
    to_remove = [
        sid for sid, a in _session_agents.items()
        if getattr(a, "safe_username", "") == safe
    ]
    for sid in to_remove:
        _session_agents.pop(sid, None)
    logger.info(
        f"用户 {username} 的缓存 Agent 已失效 | 清除 {len(to_remove)} 个 | sessions={to_remove}"
    )
    return len(to_remove)


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


def unregister_stream_task(
    session_id: str, task: asyncio.Task[None] | None = None
) -> None:
    """流式任务结束后注销自身。

    传入 task 时仅当注册的仍是该 task 才移除：客户端断开后后台任务会继续运行，
    若用户随后对同一会话发起新流，新任务会先 register；旧任务结束时的 finally
    若无条件 pop 会误删新任务的注册，导致 /cancel 失效。
    """
    if task is None:
        _session_stream_tasks.pop(session_id, None)
    elif _session_stream_tasks.get(session_id) is task:
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
