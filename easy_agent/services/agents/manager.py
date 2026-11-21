"""Agent lifecycle management - creation, caching, and cleanup"""

import logging
from easy_agent.bootstrap.env import env_str
import platform
from typing import Any, Callable

from easy_agent.agent import EasyAgent
from easy_agent.agent.graph_cache import clear_agent_graph_cache, get_agent_graph_cache
from easy_agent.agent.hitl import hitl_enabled
from easy_agent.bootstrap.config import Config
from easy_agent.db.providers import (
    get_session_repository,
    get_session_runtime_repository,
    get_user_repository,
)
from easy_agent.bootstrap.runtime_config import (
    get_runtime_agent_config,
    set_agent_invalidator,
    set_runtime_agent_config,
)
from easy_agent.agent.model import create_model
from easy_agent.tools.registry import ToolBuildContext, build_session_tools
from easy_agent.tools.web_search import create_web_search_tool
from easy_agent.utils.prompt_probe import describe_prompt, prompt_fingerprint
from easy_agent.utils.blocking import run_blocking
from easy_agent.services.mcp.tools import get_mcp_tools as load_mcp_tools_for_user
from easy_agent.services.mcp.policy import filter_server_names
from easy_agent.services.approval_settings import read_approval_mode
from easy_agent.services.agents.state import (  # noqa: F401 - 公开与测试兼容 re-export
    DropScope,
    SessionState,
    _EDIT_TARGETS_VIEW,
    _SCOPE_UNSPECIFIED,
    _SessionFieldView,
    _TURN_SKILLS_VIEW,
    _cached_agent_count,
    _clear_agent_fields,
    _create_lock,
    _prune_if_empty,
    _reset_session_state,
    _sessions,
    _state_for,
    _touch_agent_cache,
    bind_sessions_getter,
    drop,
)
bind_sessions_getter(lambda: _sessions)
from easy_agent.services.agents.stream_tasks import (  # noqa: F401 - 公开与测试兼容 re-export
    _acquire_stream_lock,
    _forget_stream_task,
    _release_stream_lock,
    _remember_stream_task,
    aregister_stream_task,
    aunregister_stream_task,
    cancel_stream_task,
    drain_stream_tasks,
    is_session_streaming,
    register_stream_task,
    unregister_stream_task,
)

logger = logging.getLogger("easy-agent.chat_service")

# 一个 session_id 对应一个 SessionState。
# 各类状态的生命周期不同，清理统一走 ``drop(session_id, scope)``：
# 随 Agent 的状态、随会话的状态、session_runtime 落库缓存各自有清理范围。
# 注册表是 OrderedDict，只把真正缓存了 Agent 的会话计入 LRU（上限 _AGENT_CACHE_MAX）。

#: ``approval_mode`` 的「未指定」哨兵：
#: * 不传 → 每轮读一次用户设置（设置页保存后下一条消息生效）；
#: * 传 ``None`` → 沿用缓存的 Agent（HITL 审批恢复 / 定时任务：此时用户可能刚改过设置，
#:   若按新值重建 Agent 会丢掉 checkpointer 里的中断态，恢复直接 409）。
#: 与 ``enable_web_search=None``「跟随缓存」的约定同构。
_APPROVAL_UNSPECIFIED = object()
_AGENT_CACHE_MAX = 100


def _evict_agent_cache(keep_session_id: str) -> None:
    """按 LRU 淘汰 Agent，并同步清理被淘汰会话的附属状态（避免内存泄漏）。

    - 只按「有缓存 Agent 的会话」计数与淘汰，无 Agent 的会话不参与；
    - 连接器是 session_runtime 表的缓存，清掉后下次建 Agent 会从库重新读回；
    - 流任务可能仍在运行，保留引用（由 unregister / drain 负责清理）；
    - 创建锁空闲且无等待者时一并释放。
    """
    while _cached_agent_count() > _AGENT_CACHE_MAX:
        victim = next(
            (
                sid
                for sid, state in _sessions.items()
                if sid != keep_session_id and state.agent is not None
            ),
            None,
        )
        if victim is None:
            break
        state = _sessions[victim]
        _clear_agent_fields(state)
        state.connectors = None
        lock = state.create_lock
        if lock is not None and not lock.locked() and not getattr(lock, "_waiters", None):
            state.create_lock = None
        _prune_if_empty(victim, state)
        logger.info(f"[{victim[-5:]}] Agent 缓存 LRU 淘汰（含会话状态清理）")


_agent_config: dict = None
_llm_instance = None


def _is_windows() -> bool:
    """Detect whether the server process runs on Windows.

    Auto-detects via ``platform.system()`` but can be overridden with the
    ``AGENT_WIN`` environment variable (``1/true/yes/on`` -> True,
    ``0/false/no/off`` -> False).
    """
    override = env_str("AGENT_WIN").lower()
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
    # 供 config/utils 等基础层读取，避免它们反向 import 本模块（ARCH-03）。
    set_runtime_agent_config(_agent_config)
    # D2：配置重载（启动/测试/热更新）后，旧编译图的模型/工具/提示词都可能已变，
    # 直接清空进程内图缓存，避免复用过期图。
    clear_agent_graph_cache()
    # 下层工具（expert_writer）需要驱逐缓存时走这里，不再 import 本模块。
    set_agent_invalidator(invalidate_user_agents)

    _llm_instance = create_model(config)
    logger.info(
        f"[初始化] Agent 配置初始化完成 | 环境: {agent_env or '(未设置)'} | "
        f"Windows: {_is_windows()} | LLM 流式已启用 | MCP 按需动态加载"
    )


def _tool_factories() -> dict[str, Callable[..., Any]]:
    """按工具名延迟构造工具实例，单个工具加载失败时仅跳过该工具。"""
    from easy_agent.knowledges.facade import extend_agent_tools

    def _scheduled_task(**kwargs: Any) -> Any:
        from easy_agent.tools.scheduled_task import CreateScheduledTaskTool
        return CreateScheduledTaskTool(**kwargs)

    def _save_expert(**kwargs: Any) -> Any:
        from easy_agent.tools.expert_writer import create_save_expert_tool
        return create_save_expert_tool(**kwargs)

    def _read_uploaded_file(**kwargs: Any) -> Any:
        from easy_agent.tools.session_file_reader import ReadUploadedFileTool
        return ReadUploadedFileTool(**kwargs)

    def _ask_user() -> Any:
        from easy_agent.tools.user_interaction import create_ask_user_tool
        return create_ask_user_tool()

    return {
        "access_knowledge_original": extend_agent_tools,
        "ask_user": _ask_user,
        "create_scheduled_task": _scheduled_task,
        "save_expert": _save_expert,
        "read_uploaded_file": _read_uploaded_file,
    }


async def get_or_create_agent_for_session(
    session_id: str, username: str = "default", workspace_name: str = "",
    enable_hitl: bool = True, model_name: str | None = None,
    enable_user_interaction: bool = True,
    enable_web_search: bool | None = None,
    system_prompt_extra: str = "",
    expert_agent_id: str | None = None,
    skill_whitelist: list[str] | None | object = _SCOPE_UNSPECIFIED,
    approval_mode: str | None | object = _APPROVAL_UNSPECIFIED,
) -> EasyAgent:
    """获取（或创建）会话 Agent。

    ``enable_web_search`` 取 True 时注入 ``web_search`` 工具并追加联网搜索提示词，
    取 False/None 时不注入。取 None 表示「跟随缓存」——用于 HITL 审批恢复等
    不带本轮开关的场景：若该会话已有缓存 Agent 则原样复用（不因开关差异驱逐，
    否则会重建 Agent 而丢失 checkpointer 中的中断态导致恢复失败）。

    ``enable_user_interaction=False`` 供定时任务等无人值守运行使用：不注入
    ``ask_user``，避免任务停在无法完成的交互中断上。交互式聊天保持默认 True；
    该开关变化会驱逐缓存 Agent，防止调度会话后续在 UI 中继续时沿用错误工具集。

    ``expert_agent_id`` 为当前选中的专家 Agent ID（``None`` 与 ``""`` 等价，都表示
    通用 Agent）。专家提示词由调用方通过 ``system_prompt_extra`` 传入，这里只负责
    「专家变更 -> 驱逐缓存」，确保同一会话在专家之间、专家与通用之间来回切换都能
    立即生效。不传该参数时行为与引入本参数前**逐字一致**。

    ``skill_whitelist`` 为本轮技能作用域（专家技能硬隔离，见 ``services/agents/tool_scope.py``）：
    ``None`` = 显式全量可见；``[]`` = 一个技能都不可用；非空 = 只有这些目录可见。
    **不传**（哨兵 ``_SCOPE_UNSPECIFIED``）表示「沿用该会话上次生效的作用域」——
    HITL 审批恢复这类不带本轮选择的路径靠它避免把专家会话重建回通用 Agent。
    作用域变化会驱逐缓存：技能清单在 Agent 构造时注入，缓存命中会一直用旧清单。

    连接器不再随每轮消息提交：本函数只读该会话 SessionState 里已记录的启用集合
    （写入与「驱逐缓存 Agent」由 ``set_session_connectors`` 负责，见
    ``api/connectors.py`` 的 ``/agent/connectors/session``）。

    ``save_expert``（DA-13）：非专家会话都注入，且不参与技能门控——产品级入口不能依赖
    技能 frontmatter 的写法（见 ``agent/core.py`` 的 ``_ALWAYS_AVAILABLE_TOOLS``）；也不再
    随本轮选中的技能驱逐重建 Agent。专家会话不提供，防止领域专家被借来创建 / 修改别的专家。

    ``approval_mode`` 为用户级审批模式（设置 → 权限设置，存
    ``{workspace_dir}/.settings/{user}.json``）：
    **不传**（哨兵）表示每轮读一次用户设置——这是「保存后下一条消息生效」的唯一来源，
    也是多副本部署里跨 Pod 自愈的方式（不依赖 ``invalidate_user_agents`` 那种只清本进程）；
    传 ``None`` 表示「沿用缓存的 Agent」，供 HITL 审批恢复使用：此时若按最新设置重建 Agent，
    ``interrupt_on`` 变化会让图重建、checkpointer 里的中断态丢失，恢复直接 409。
    """
    async with _create_lock(session_id):
        return await _get_or_create_agent_unlocked(
            session_id,
            username=username,
            workspace_name=workspace_name,
            enable_hitl=enable_hitl,
            enable_user_interaction=enable_user_interaction,
            model_name=model_name,
            enable_web_search=enable_web_search,
            system_prompt_extra=system_prompt_extra,
            expert_agent_id=expert_agent_id,
            skill_whitelist=skill_whitelist,
            approval_mode=approval_mode,
        )


def _resolve_skill_scope(state, skill_whitelist) -> tuple[bool, tuple[str, ...] | None]:
    """技能作用域：显式传入的优先并按会话记住；未指定沿用上次，都没有则全量可见。"""
    scope_specified = skill_whitelist is not _SCOPE_UNSPECIFIED
    if scope_specified:
        current_scope = (
            None
            if skill_whitelist is None
            else tuple(
                str(name).strip() for name in skill_whitelist if str(name).strip()
            )
        )
        state.skill_scope = current_scope
    else:
        saved_scope = state.skill_scope
        current_scope = None if saved_scope is _SCOPE_UNSPECIFIED else saved_scope
    return scope_specified, current_scope


async def _resolve_session_connectors(state, session_id: str) -> list[str] | None:
    """本会话启用的连接器；进程内没有缓存时回读 session_runtime 表（落库缓存）。"""
    # None = 尚未设置 → 注入全部启用连接器；空元组 = 全部关闭 → []。
    recorded_connectors = state.connectors
    if recorded_connectors is None:
        try:
            from easy_agent.services.sessions.runtime import load_connectors

            recorded_connectors = await run_blocking(
                load_connectors, get_session_runtime_repository(), session_id
            )
            if recorded_connectors is not None:
                state.connectors = recorded_connectors
        except Exception:
            recorded_connectors = None
    return None if recorded_connectors is None else list(recorded_connectors)


def _resolve_web_search_tool(
    config, username: str, session_id: str, enable_web_search: bool | None
) -> tuple[object | None, bool]:
    """联网搜索工具：仅在本轮请求开启且 web_search 已配置时注入；否则静默降级并告警。"""
    web_search_tool = (
        create_web_search_tool(config, user_id=Config.sanitize_username(username))
        if enable_web_search
        else None
    )
    web_search_enabled = web_search_tool is not None
    if enable_web_search and not web_search_enabled:
        logger.warning(
            f"[{session_id[-5:]}] 已请求联网搜索，但 web_search 未配置 api_url/api_key，本轮按未开启处理"
        )
    return web_search_tool, web_search_enabled


def _try_reuse_cached_agent(
    state,
    session_id: str,
    *,
    cached,
    effective_model: str,
    enable_user_interaction: bool,
    web_search_enabled: bool,
    enable_web_search: bool | None,
    expert_key: str,
    current_scope: tuple[str, ...] | None,
    only_connectors: list[str] | None,
    system_prompt_extra: str,
    approval_mode: str | None = None,
):
    """缓存命中判定：参数一致则返回缓存 Agent；参数变化则驱逐并返回 None。

    ``approval_mode=None`` 表示「沿用缓存」（HITL 审批恢复）：不比较审批模式，
    否则用户在审批卡片挂着的时候切了设置，这一轮就会重建 Agent 并丢掉中断态。
    """
    if cached is None:
        return None
    cached_web_search = bool(getattr(cached, "enable_web_search", False))
    # enable_web_search=None（如 HITL 恢复）：不比较开关，直接复用缓存 Agent
    search_matches = (
        enable_web_search is None or web_search_enabled == cached_web_search
    )
    # 审批模式变化同样必须驱逐：否则「设置了自动审批但下一轮还在弹审批卡片」，
    # 跨 Pod 时尤其明显（invalidate_user_agents 只清本进程）。
    cached_approval_mode = getattr(cached, "approval_mode", None)
    approval_changed = (
        approval_mode is not None and approval_mode != cached_approval_mode
    )
    # 专家变更同样必须驱逐：否则「换专家不生效」，且专家人设会黏在后续的
    # 普通聊天上（模型与联网开关都没变，缓存会一直命中）。
    # 连接器变更的驱逐不在这里：会话级开关（set_session_connectors）写入时
    # 就已直接驱逐过该会话的缓存 Agent。
    expert_changed = expert_key != state.expert_key
    # 技能作用域变化同样必须驱逐：技能清单在 Agent 构造时注入，缓存命中会一直
    # 沿用旧清单（专家 A 的白名单黏在专家 B 上 = 隔离失效）。
    cached_scope = getattr(cached, "skill_whitelist", None)
    cached_scope_key = None if cached_scope is None else tuple(cached_scope)
    scope_changed = cached_scope_key != current_scope
    cached_interaction = bool(getattr(cached, "enable_user_interaction", True))
    interaction_changed = cached_interaction != enable_user_interaction
    # 提示词补充（专家人设 / 技能公告 / 上传提示）变化时同样驱逐缓存：
    # 避免多副本或热更新专家后静默沿用旧提示词；HITL 恢复（approval_mode=None）跳过校验以保留中断态。
    extra_fp = prompt_fingerprint(system_prompt_extra)
    cached_extra_fp = getattr(state, "system_prompt_extra_fp", "")
    prompt_extra_changed = (
        approval_mode is not None
        and bool(cached_extra_fp)
        and extra_fp != cached_extra_fp
    )
    if (
        effective_model != getattr(cached, "model_name", None)
        or not search_matches
        or expert_changed
        or scope_changed
        or interaction_changed
        or approval_changed
        or prompt_extra_changed
    ):
        logger.info(
            f"[{session_id[-5:]}] Agent 参数变化 | "
            f"model: {getattr(cached, 'model_name', '?')} -> {effective_model} | "
            f"web_search: {cached_web_search} -> {web_search_enabled} | "
            f"expert: {state.expert_key or '(通用)'} -> "
            f"{expert_key or '(通用)'} | "
            f"user_interaction: {cached_interaction} -> {enable_user_interaction} | "
            f"skill_scope: {cached_scope_key or '全量'} -> {current_scope or '全量'} | "
            f"approval_mode: {cached_approval_mode or '?'} -> {approval_mode or '(沿用缓存)'} | "
            f"connectors: {only_connectors if only_connectors is not None else '(全部)'} | "
            f"驱逐旧 Agent 重建 | "
            f"{describe_prompt('本轮 extra', system_prompt_extra)} | "
            f"旧终稿指纹={prompt_fingerprint(getattr(cached, 'system_prompt', ''))}"
        )
        state.agent = None
        return None
    # 缓存命中：本轮传入的 extra 会被**忽略**（复用旧 Agent 的提示词）。
    # 因此这里同时打印「本轮 extra」与「缓存 Agent 终稿」两组指纹：一致说明
    # 复用的确实是本轮该用的提示词；若不一致，说明本轮内容被缓存吞掉了
    # （专家内容变更时应由 /agent/update -> invalidate_user_agents 驱逐，
    #  这两组指纹不一致即为「驱逐漏了」的告警信号）。
    logger.info(
        f"[{session_id[-5:]}] ♻️ Agent 缓存命中 | expert: {expert_key or '(通用)'} | "
        f"{describe_prompt('本轮 extra', system_prompt_extra)} | "
        f"{describe_prompt('缓存终稿', getattr(cached, 'system_prompt', ''))}"
    )
    _touch_agent_cache(session_id)
    return cached


async def _resolve_workspace_name(session_id: str, workspace_name: str) -> str:
    """未显式传 workspace_name 时回读会话记录；读不到按空值继续建 Agent。"""
    if workspace_name:
        return workspace_name
    try:
        session_repo = get_session_repository()
        session = await run_blocking(session_repo.get_session, session_id)
        if session and session.workspace_name:
            return session.workspace_name
    except Exception as exc:  # noqa: BLE001 - 读不到就按空 workspace_name 继续建 Agent
        logger.debug(f"[{session_id[-5:]}] 读取会话 workspace_name 失败: {exc}")
    return workspace_name


async def _resolve_organization_id(session_id: str, username: str) -> str:
    """查询用户机构 ID（注入系统提示词）；失败按空值继续。"""
    try:
        user_repo = get_user_repository()
        user = await run_blocking(user_repo.get_user_by_username, username)
        if user:
            return user.organization_id or ""
    except Exception as e:
        logger.warning(f"[{session_id[-5:]}] 获取用户机构ID失败: {e}")
    return ""


def _log_connector_scope(session_id: str, only_connectors: list[str] | None) -> None:
    """本会话的连接器集合只读不写：写入由 set_session_connectors 负责（会话级开关）。"""
    if only_connectors is None:
        logger.info(f"[{session_id[-5:]}] 🔌 未限定连接器，注入全部启用连接器的 MCP 工具")
    elif only_connectors:
        logger.info(
            f"[{session_id[-5:]}] 🔌 按会话开关限定 MCP 工具 | {only_connectors}"
        )
    else:
        logger.info(f"[{session_id[-5:]}] 🔌 本会话连接器已全部关闭，不注入连接器工具")


async def _load_mcp_tools(session_id: str, username: str, config, only_connectors: list[str] | None) -> list:
    """MCP 工具：用户专属 mcp.json 优先，无则按需加载全局 mcp.json。

    三条路径**统一**先走 filter_server_names（SEC-04 stdio 策略 + 违规 server 过滤）：
    不管配置来源是用户专属还是全局，普通用户都不能拉自定义 stdio、不能绕过策略。
    """
    # SEC-04：先算好过滤后的 server_names，三条路径共用同一份结果
    server_names = filter_server_names(username, only_connectors)

    user_mcp_path = Config.get_user_mcp_path(username, config)
    if user_mcp_path.exists():
        logger.info(f"[{session_id[-5:]}] 加载用户专属 MCP | {user_mcp_path}")
        try:
            return await load_mcp_tools_for_user(
                username, server_names=server_names
            )
        except Exception as e:
            logger.warning(
                f"[{session_id[-5:]}] 加载用户 MCP 失败，尝试全局: {e}"
            )
            return await load_mcp_tools_for_user(
                None, server_names=server_names
            )
    # 无用户专属 mcp.json，按需加载全局 mcp.json（缓存命中时零开销）
    logger.info(
        f"[{session_id[-5:]}] 无用户专属 mcp.json，按需加载全局 MCP"
    )
    try:
        return await load_mcp_tools_for_user(
            None, server_names=server_names
        )
    except Exception as e:
        logger.warning(
            f"[{session_id[-5:]}] 加载全局 MCP 失败: {e}"
        )
        return []


def _build_session_tools_for_agent(
    config,
    *,
    username: str,
    session_id: str,
    workspace_name: str,
    enable_hitl: bool,
    enable_user_interaction: bool,
    model_name: str | None,
    expert_key: str,
    web_search_tool,
    mcp_tools: list,
) -> list:
    """工具装配：MCP 工具作为基底，其余按工具注册表声明的条件/顺序追加。"""
    # 工具元数据（是否内置/写操作/HITL/注入顺序）单一来源见 tools/registry.py；
    # 本函数不再逐工具硬编码 if/append，新增工具只需改注册表。
    return build_session_tools(
        mcp_tools,
        ToolBuildContext(
            config=config,
            username=username,
            session_id=session_id,
            workspace_name=workspace_name,
            enable_hitl=enable_hitl,
            enable_user_interaction=enable_user_interaction,
            model_name=model_name or "",
            expert_session=bool(expert_key),
            web_search_tool=web_search_tool,
            edit_targets=_EDIT_TARGETS_VIEW,
            turn_skills=_TURN_SKILLS_VIEW,
            factories=_tool_factories(),
        ),
    )


def _register_created_agent(
    state,
    session_id: str,
    agent,
    *,
    expert_key: str,
    current_scope: tuple[str, ...] | None,
    system_prompt_extra: str,
    tools: list,
) -> None:
    """登记缓存并打创建成功日志（LRU 淘汰在登记后立即执行）。"""
    state.agent = agent
    _touch_agent_cache(session_id)
    state.expert_key = expert_key
    state.system_prompt_extra_fp = prompt_fingerprint(system_prompt_extra)
    _evict_agent_cache(session_id)
    logger.info(
        f"[{session_id[-5:]}] Agent 实例创建成功 | workspace: {agent.workspace_dir} | "
        f"model: {agent.model_name} | mcp_tools: {len(tools)} | "
        f"expert: {expert_key or '(通用)'} | "
        f"skill_scope: {list(current_scope) if current_scope is not None else '全量'} | "
        f"{describe_prompt('本轮 extra', system_prompt_extra)} | "
        f"{describe_prompt('终稿 system_prompt', getattr(agent, 'system_prompt', ''))}"
    )

async def _get_or_create_agent_unlocked(
    session_id: str, username: str = "default", workspace_name: str = "",
    enable_hitl: bool = True, model_name: str | None = None,
    enable_user_interaction: bool = True,
    enable_web_search: bool | None = None,
    system_prompt_extra: str = "",
    expert_agent_id: str | None = None,
    skill_whitelist: list[str] | None | object = _SCOPE_UNSPECIFIED,
    approval_mode: str | None | object = _APPROVAL_UNSPECIFIED,
) -> EasyAgent:
    if _agent_config is None:
        raise RuntimeError("Agent 配置未初始化")

    state = _state_for(session_id)
    config = _agent_config["config"]
    # config.yaml 的 hitl.enabled=false 时对话也不审批（工具装配和 EasyAgent 都看这个值）
    enable_hitl = hitl_enabled(config, enable_hitl)
    effective_model = model_name or config.active_model
    # None 与 "" 等价：都表示「本次请求不挂专家（通用 Agent）」
    expert_key = expert_agent_id or ""

    # 技能作用域（显式传入优先并按会话记住）、会话级连接器（落库缓存回读）、联网搜索工具
    _scope_specified, current_scope = _resolve_skill_scope(state, skill_whitelist)
    only_connectors = await _resolve_session_connectors(state, session_id)
    web_search_tool, web_search_enabled = _resolve_web_search_tool(
        config, username, session_id, enable_web_search
    )
    # 用户级审批模式：未指定时每轮读一次文件（约 200 字节）；None = 沿用缓存（审批恢复）
    if approval_mode is _APPROVAL_UNSPECIFIED:
        approval_mode = await run_blocking(read_approval_mode, username, config)

    # 模型切换 / 联网搜索开关变化 / 专家变更：缓存的 Agent 不匹配，驱逐后重建
    cached = _try_reuse_cached_agent(
        state,
        session_id,
        cached=state.agent,
        effective_model=effective_model,
        enable_user_interaction=enable_user_interaction,
        web_search_enabled=web_search_enabled,
        enable_web_search=enable_web_search,
        expert_key=expert_key,
        current_scope=current_scope,
        only_connectors=only_connectors,
        system_prompt_extra=system_prompt_extra,
        approval_mode=approval_mode,
    )
    if cached is not None:
        return cached

    workspace_name = await _resolve_workspace_name(session_id, workspace_name)
    logger.info(
        f"[{session_id[-5:]}] 为会话创建 Agent 实例 | username={username} | session_id={session_id} | "
        f"workspace_name={workspace_name} | model={effective_model}"
    )

    # 查询用户机构ID，注入系统提示词
    organization_id = await _resolve_organization_id(session_id, username)

    _log_connector_scope(session_id, only_connectors)
    mcp_tools = await _load_mcp_tools(session_id, username, config, only_connectors)
    tools = _build_session_tools_for_agent(
        config,
        username=username,
        session_id=session_id,
        workspace_name=workspace_name,
        enable_hitl=enable_hitl,
        enable_user_interaction=enable_user_interaction,
        model_name=model_name,
        expert_key=expert_key,
        web_search_tool=web_search_tool,
        mcp_tools=mcp_tools,
    )

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
        enable_user_interaction=enable_user_interaction,
        # approval_mode=None（沿用缓存）但缓存已被 LRU 淘汰时没有中断态可保，
        # 退回读文件按最新设置重建
        approval_mode=(
            approval_mode
            if approval_mode is not None
            else await run_blocking(read_approval_mode, username, config)
        ),
        model_name=model_name,
        enable_web_search=web_search_enabled,
        system_prompt_extra=system_prompt_extra,
        # 技能作用域（专家技能硬隔离）：None = 全量，非 None = 只挂这些技能目录
        skill_whitelist=None if current_scope is None else list(current_scope),
    )
    _register_created_agent(
        state,
        session_id,
        agent,
        expert_key=expert_key,
        current_scope=current_scope,
        system_prompt_extra=system_prompt_extra,
        tools=tools,
    )
    return agent


def invalidate_user_agents(username: str) -> int:
    """Evict all cached agents belonging to ``username``.

    Called after the user edits their mcp.json (or other per-user config) so
    that the next request rebuilds the agent with fresh settings. Returns the
    number of evicted agents.
    """
    if not username:
        return 0
    safe = Config.sanitize_username(username)
    to_remove = [
        sid
        for sid, state in _sessions.items()
        if state.agent is not None
        and getattr(state.agent, "safe_username", "") == safe
    ]
    for sid in to_remove:
        # 用户级失效：清 Agent 与专家，保留会话级连接器开关（用户显式选择，不该因为
        # 「改了 mcp.json」而静默复位；被卸载/停用的 server 由 MCP 加载层跳过并告警）。
        drop(sid, DropScope.USER)
    evicted_graphs = get_agent_graph_cache().invalidate_user(safe)
    logger.info(
        f"用户 {username} 的缓存 Agent 已失效 | 清除 {len(to_remove)} 个 | "
        f"编译图 {evicted_graphs} 张 | sessions={to_remove}"
    )
    return len(to_remove)


def remove_session_agent(session_id: str) -> None:
    """只丢 Agent：清「随 Agent」的状态，保留连接器 / 流任务 / 创建锁。

    记忆更新、取消生成、定时任务结束、上传文件等场景都会调用本函数，会话仍然存在，
    因此连接器开关（用户显式设置）和正在运行的流任务都不能清；会话真正终结时走
    ``clear_session_connectors`` 或 ``drop(session_id, DropScope.SESSION)``。
    """
    state = _sessions.get(session_id)
    if state is None:
        return
    had_agent = state.agent is not None
    # 专家 / 技能作用域 / 本轮技能 / 待修改专家绑定都随 Agent 一起失效：否则
    # expert_key 残留会让下次请求误判「未变更」，旧白名单或旧绑定也会黏在下一轮上。
    drop(session_id, DropScope.AGENT)
    if had_agent:
        logger.info(f"[{session_id[-5:]}] Agent 缓存已清除")


def set_session_turn_skills(session_id: str, dirs: list[str] | None) -> list[str]:
    """记录「本轮用户在输入框选中的技能目录名」，供同轮 ``save_expert`` 落库。

    创建 / 修改专家走的是对话式流程（expert-creator 技能 + ``save_expert`` 工具），
    用户在输入框选的技能包只进聊天请求的 ``skills`` 字段——工具侧看不到请求体，
    因此由 ``api/chat.py`` 在这里暂存一份（存进该会话的 SessionState）。

    规则：**非空才覆盖**，空值保留上一次——创建流程可能跨多轮（Agent 先追问再保存），
    用户每轮都会重新带上胶囊，此时不该因为中间某一轮没带胶囊就把选择清空。
    Returns: 本次会话当前生效的技能目录名（便于调用方打日志）。
    """
    cleaned = [str(name).strip() for name in (dirs or []) if str(name).strip()]
    if cleaned and session_id:
        _state_for(session_id).turn_skills = cleaned
    state = _sessions.get(session_id)
    return list(state.turn_skills) if state is not None else []


def set_session_edit_target(session_id: str, expert_agent_id: str) -> None:
    """绑定 / 覆盖会话级「待修改专家」（归属校验由接口层完成后再调用）。

    新绑定覆盖旧绑定（同一会话连续编辑多个专家时取最后一次）；保留至会话结束，
    中间的普通聊天轮次不会清除——用户可能隔几轮才提交修改内容。
    """
    expert_agent_id = _bind_edit_target(session_id, expert_agent_id)
    if expert_agent_id:
        _save_edit_target(session_id, expert_agent_id)


async def aset_session_edit_target(session_id: str, expert_agent_id: str) -> None:
    """:func:`set_session_edit_target` 的异步版本：落库放进线程，不占事件循环。"""
    expert_agent_id = _bind_edit_target(session_id, expert_agent_id)
    if expert_agent_id:
        await run_blocking(_save_edit_target, session_id, expert_agent_id)


def _bind_edit_target(session_id: str, expert_agent_id: str) -> str:
    """写会话状态里的待修改专家绑定；返回规范化后的专家 ID，无效输入返回空串（不绑定）。"""
    expert_agent_id = str(expert_agent_id or "").strip()
    if not session_id or not expert_agent_id:
        return ""
    state = _state_for(session_id)
    if state.edit_target != expert_agent_id:
        logger.info(
            f"[{session_id[-5:]}] ✏️ 已绑定待修改专家 | expert={expert_agent_id}"
        )
    state.edit_target = expert_agent_id
    return expert_agent_id


def _save_edit_target(session_id: str, expert_agent_id: str) -> None:
    try:
        from easy_agent.services.sessions.runtime import save_edit_target
        save_edit_target(
            get_session_runtime_repository(), session_id, expert_agent_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 待修改专家落库失败: {exc}")


def get_session_edit_target(session_id: str) -> str:
    """返回会话当前绑定的「待修改专家」ID（"" 表示未绑定）。供测试与排查使用。"""
    state = _sessions.get(session_id or "")
    return state.edit_target if state is not None else ""


def get_session_expert(session_id: str) -> str:
    """返回会话当前生效的专家 Agent ID（"" 表示通用 Agent）。主要供测试与排查使用。"""
    state = _sessions.get(session_id)
    return state.expert_key if state is not None else ""


def get_session_agent(session_id: str) -> EasyAgent | None:
    """返回会话当前缓存的 Agent 实例（None 表示未缓存）。主要供测试与排查使用。"""
    state = _sessions.get(session_id)
    return state.agent if state is not None else None


def get_session_connectors(session_id: str) -> tuple[str, ...] | None:
    """返回会话显式启用的连接器（mcp.json 的 server 名）。

    ``None`` 表示该会话尚未设置过开关 —— 等价于「全部启用连接器」；
    空元组表示用户把开关全部关掉（不注入任何连接器工具）。
    与 ``get_session_expert`` 同理，主要供接口（连接器开关状态查询）与排查使用。
    """
    state = _sessions.get(session_id)
    return state.connectors if state is not None else None


def set_session_connectors(session_id: str, connector_servers: list[str]) -> int:
    """写入会话级连接器启用集合（server 名，空列表 = 全部关闭），并驱逐缓存 Agent。

    由 ``api/connectors.py`` 的 ``/agent/connectors/session`` 调用：输入框「+」面板里
    的连接器开关切换后，前端把该会话**全部**启用项整体提交过来（全量覆盖，不改
    mcp.json；集合落库缓存到 session_runtime 表，多副本 / 重启后仍能读回）。

    集合变了必须驱逐该会话的缓存 Agent，否则旧工具集会黏住（下次请求会按新集合重建）。
    返回被驱逐的 Agent 数，供接口回执与日志。
    """
    if not session_id:
        return 0
    normalized, evicted = _apply_session_connectors(session_id, connector_servers)
    _save_connectors(session_id, normalized)
    return evicted


async def aset_session_connectors(session_id: str, connector_servers: list[str]) -> int:
    """:func:`set_session_connectors` 的异步版本。

    先在事件循环上更新集合并驱逐缓存 Agent，落库放进线程：落库期间同一会话的新请求
    看到的已经是驱逐后的状态，不会再拿到旧工具集。
    """
    if not session_id:
        return 0
    normalized, evicted = _apply_session_connectors(session_id, connector_servers)
    await run_blocking(_save_connectors, session_id, normalized)
    return evicted


def _apply_session_connectors(
    session_id: str, connector_servers: list[str]
) -> tuple[tuple[str, ...], int]:
    """更新进程内的连接器集合并驱逐该会话的缓存 Agent；返回（规范化集合，驱逐数）。"""
    normalized = tuple(
        dict.fromkeys(str(n).strip() for n in connector_servers if str(n).strip())
    )
    state = _state_for(session_id)
    state.connectors = normalized
    evicted = 0
    if state.agent is not None:
        state.agent = None
        evicted = 1
    logger.info(
        f"[{session_id[-5:]}] 🔌 会话连接器开关已更新 | "
        f"{list(normalized) if normalized else '(全部关闭)'} | 驱逐 Agent: {evicted} 个"
    )
    return normalized, evicted


def _save_connectors(session_id: str, normalized: tuple[str, ...]) -> None:
    try:
        from easy_agent.services.sessions.runtime import save_connectors

        save_connectors(
            get_session_runtime_repository(), session_id, list(normalized)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 会话连接器落库失败: {exc}")


def clear_session_connectors(session_id: str) -> None:
    """清除会话的连接器开关记录（会话被删除等「会话终结」场景专用）。"""
    state = _sessions.get(session_id)
    if state is None or state.connectors is None:
        return
    state.connectors = None
    _prune_if_empty(session_id, state)
    logger.info(f"[{session_id[-5:]}] 会话连接器开关记录已清除")


def get_agent_config() -> dict:
    # 以 runtime_config 为准：测试/重载可直接 set_runtime_agent_config 生效，
    # 同时保留模块级 _agent_config 变量供既有测试 monkeypatch。
    return _agent_config or get_runtime_agent_config()


# 模块导入即注册：expert_writer 等下层通过 runtime_config 调用，无需等待 init。
set_agent_invalidator(invalidate_user_agents)


# 断 scheduler/agent_manager 循环依赖：scheduler 通过端口取 agent 生命周期。
from easy_agent.services.scheduler import set_agent_lifecycle as _set_scheduler_agent_lifecycle  # noqa: E402

_set_scheduler_agent_lifecycle(
    lambda: (get_or_create_agent_for_session, remove_session_agent)
)
