"""Agent lifecycle management - creation, caching, and cleanup"""

import asyncio
import logging
from easy_agent.bootstrap.env import env_str
import platform
from collections import OrderedDict
from typing import Any, Callable

from easy_agent.agent import EasyAgent
from easy_agent.agent.graph_cache import clear_agent_graph_cache, get_agent_graph_cache
from easy_agent.agent.hitl import hitl_enabled
from easy_agent.bootstrap.config import Config
from easy_agent.db import get_database
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
from easy_agent.services.mcp import get_mcp_tools as load_mcp_tools_for_user
from easy_agent.services.mcp_policy import filter_server_names

logger = logging.getLogger("easy-agent.chat_service")

_session_agents: "OrderedDict[str, EasyAgent]" = OrderedDict()
_AGENT_CACHE_MAX = 100
_session_create_locks: dict[str, asyncio.Lock] = {}
# 会话 → 当前生效的专家 Agent ID（"" 表示通用 Agent）。
# 与 _session_agents 同性质（进程内缓存），用于判断「专家是否变更」——
# 变更时必须驱逐 Agent 缓存，否则会出现「换专家不生效」与「专家人设黏在普通聊天上」。
_session_expert: dict[str, str] = {}
# 会话 → 本会话启用的连接器（mcp.json 的 server 名，由输入框「+」面板的开关写入，
# 见 api/connectors.py 的 /agent/connectors/session）。与 _session_agents 同性质
# （进程内缓存，仅本次会话临时生效，不落库、不改 mcp.json）：
#   * key 不存在 = 尚未设置 → 注入全部启用连接器（新建会话的默认态）；
#   * 空元组     = 显式全部关闭 → 不注入任何连接器的工具；
# 写入路径（set_session_connectors）会同步驱逐该会话的缓存 Agent，否则旧工具集会黏住。
_session_connectors: dict[str, tuple[str, ...]] = {}
_session_stream_tasks: dict[str, asyncio.Task[None]] = {}

# 会话级「待修改专家」绑定表（``{session_id: expert_agent_id}``）：用户在专家面板点
# 「编辑」后由聊天请求的结构化字段 ``pending_expert_id`` 写入（归属校验在接口层做）。
# 修改专家时 save_expert 工具以这里的值为准——**用户消息里不再携带专家 ID**，
# 防止消息体被任意篡改。新绑定覆盖旧绑定，保留至会话结束（remove_session_agent 清除）。
_session_edit_targets: dict[str, str] = {}

# 会话 → 本次生效的技能作用域白名单（``None`` = 全量可见）。
# 与 _session_expert 同性质：进程内缓存，随 Agent 一起创建/清理。
# 作用域**变化必须驱逐 Agent**：技能清单在 Agent 构造时注入，缓存命中会一直用旧作用域。
_session_skill_scope: dict[str, tuple[str, ...] | None] = {}

# 会话 → 本轮用户在输入框选中的技能目录名（``save_expert`` 创建/修改专家时落库为
# 专家的 ``export_required_skills``）。写入方：``api/chat.py``（每轮请求的 skills 字段）；
# 读取方：``tools/expert_writer.py``（同一轮内保存专家）。会话结束 / Agent 驱逐时清理。
_session_turn_skills: dict[str, list[str]] = {}

#: ``get_or_create_agent_for_session(skill_whitelist=...)`` 的「未指定」哨兵：
#: 传 None 表示「显式全量可见」，不传才表示「沿用该会话上次的作用域」（HITL 恢复等
#: 不带本轮选择的路径），两者语义不同，不能用同一个 None 表示。
_SCOPE_UNSPECIFIED = object()

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


def _create_lock(session_id: str) -> asyncio.Lock:
    lock = _session_create_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_create_locks[session_id] = lock
    return lock


def _touch_agent_cache(session_id: str) -> None:
    """命中即移到末尾（LRU）。测试可能把缓存替换成普通 dict，需兼容。"""
    move = getattr(_session_agents, "move_to_end", None)
    if callable(move):
        move(session_id)


def _evict_agent_cache(keep_session_id: str) -> None:
    """按 LRU 淘汰 Agent，并同步清理所有会话级 side dict（避免内存泄漏）。"""
    while len(_session_agents) > _AGENT_CACHE_MAX:
        victim = next((sid for sid in _session_agents if sid != keep_session_id), None)
        if victim is None:
            break
        _session_agents.pop(victim, None)
        # 会话级附属状态与 Agent 同生共死；只清 Agent 会留下永久泄漏的字典项。
        for side in (
            _session_expert,
            _session_connectors,
            _session_edit_targets,
            _session_skill_scope,
            _session_turn_skills,
        ):
            side.pop(victim, None)
        lock = _session_create_locks.get(victim)
        if lock is not None and not lock.locked() and not getattr(lock, "_waiters", None):
            _session_create_locks.pop(victim, None)
        logger.info(f"[{victim[-5:]}] Agent 缓存 LRU 淘汰（含 side dict 清理）")


def _tool_factories() -> dict[str, Callable[..., Any]]:
    """工具工厂表：由装配根提供，注册表只按名字取用。

    为什么放在这里而不是 ``tools/registry.py``：注册表保持「无包内 import 边的叶子
    模块」，工厂经 ``ToolBuildContext.factories`` 注入，测试可直接换成假工厂。最初
    是为了避开当年的 knowledge-chat-scheduler-core SCC；该环已在 A1 拆掉，2026-10-19
    核对过工厂模块都不会 import 回注册表，搬进 ``ToolSpec`` 不会形成环（后续可简化）。

    加载时机与改造前一致：知识库扩展每次装配都会 import（与改造前相同），
    其余三个工厂在对应工具真正被构造时才 import——这样单个工具模块加载失败
    只会让该工具被跳过（注册表逐条目告警），不会拖垮整次 Agent 创建。
    """
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

    return {
        "access_knowledge_original": extend_agent_tools,
        "create_scheduled_task": _scheduled_task,
        "save_expert": _save_expert,
        "read_uploaded_file": _read_uploaded_file,
    }


async def get_or_create_agent_for_session(
    session_id: str, username: str = "default", workspace_name: str = "",
    enable_hitl: bool = True, model_name: str | None = None,
    enable_web_search: bool | None = None,
    system_prompt_extra: str = "",
    expert_agent_id: str | None = None,
    skill_whitelist: list[str] | None | object = _SCOPE_UNSPECIFIED,
) -> EasyAgent:
    """获取（或创建）会话 Agent。

    ``enable_web_search`` 取 True 时注入 ``web_search`` 工具并追加联网搜索提示词，
    取 False/None 时不注入。取 None 表示「跟随缓存」——用于 HITL 审批恢复等
    不带本轮开关的场景：若该会话已有缓存 Agent 则原样复用（不因开关差异驱逐，
    否则会重建 Agent 而丢失 checkpointer 中的中断态导致恢复失败）。

    ``expert_agent_id`` 为当前选中的专家 Agent ID（``None`` 与 ``""`` 等价，都表示
    通用 Agent）。专家提示词由调用方通过 ``system_prompt_extra`` 传入，这里只负责
    「专家变更 -> 驱逐缓存」，确保同一会话在专家之间、专家与通用之间来回切换都能
    立即生效。不传该参数时行为与引入本参数前**逐字一致**。

    ``skill_whitelist`` 为本轮技能作用域（专家技能硬隔离，见 ``services/tool_scope.py``）：
    ``None`` = 显式全量可见；``[]`` = 一个技能都不可用；非空 = 只有这些目录可见。
    **不传**（哨兵 ``_SCOPE_UNSPECIFIED``）表示「沿用该会话上次生效的作用域」——
    HITL 审批恢复这类不带本轮选择的路径靠它避免把专家会话重建回通用 Agent。
    作用域变化会驱逐缓存：技能清单在 Agent 构造时注入，缓存命中会一直用旧清单。

    连接器不再随每轮消息提交：本函数只读 ``_session_connectors`` 里该会话已记录的
    启用集合（写入与「驱逐缓存 Agent」由 ``set_session_connectors`` 负责，见
    ``api/connectors.py`` 的 ``/agent/connectors/session``）。

    ``save_expert``（DA-13）：非专家会话都注入，且不参与技能门控——产品级入口不能依赖
    技能 frontmatter 的写法（见 ``agent/core.py`` 的 ``_ALWAYS_AVAILABLE_TOOLS``）；也不再
    随本轮选中的技能驱逐重建 Agent。专家会话不提供，防止领域专家被借来创建 / 修改别的专家。
    """
    async with _create_lock(session_id):
        return await _get_or_create_agent_unlocked(
            session_id,
            username=username,
            workspace_name=workspace_name,
            enable_hitl=enable_hitl,
            model_name=model_name,
            enable_web_search=enable_web_search,
            system_prompt_extra=system_prompt_extra,
            expert_agent_id=expert_agent_id,
            skill_whitelist=skill_whitelist,
        )


async def _get_or_create_agent_unlocked(
    session_id: str, username: str = "default", workspace_name: str = "",
    enable_hitl: bool = True, model_name: str | None = None,
    enable_web_search: bool | None = None,
    system_prompt_extra: str = "",
    expert_agent_id: str | None = None,
    skill_whitelist: list[str] | None | object = _SCOPE_UNSPECIFIED,
) -> EasyAgent:
    global _session_agents, _session_expert, _session_connectors, _agent_config

    if _agent_config is None:
        raise RuntimeError("Agent 配置未初始化")

    config = _agent_config["config"]
    # config.yaml 的 hitl.enabled=false 时对话也不审批（工具装配和 EasyAgent 都看这个值）
    enable_hitl = hitl_enabled(config, enable_hitl)
    effective_model = model_name or config.active_model
    # None 与 "" 等价：都表示「本次请求不挂专家（通用 Agent）」
    expert_key = expert_agent_id or ""

    # 技能作用域：显式传入的优先并按会话记住；未指定（HITL 恢复等）沿用上次，
    # 都没有则全量可见（= 改造前行为）。
    scope_specified = skill_whitelist is not _SCOPE_UNSPECIFIED
    if scope_specified:
        current_scope = (
            None
            if skill_whitelist is None
            else tuple(
                str(name).strip() for name in skill_whitelist if str(name).strip()
            )
        )
        _session_skill_scope[session_id] = current_scope
    else:
        current_scope = _session_skill_scope.get(session_id)

    # 本会话启用的连接器（mcp.json server 名，由输入框「+」面板的会话级开关写入）。
    # key 不存在 = 尚未设置 → None = 注入全部启用连接器；空元组 = 全部关闭 → []。
    recorded_connectors = _session_connectors.get(session_id)
    if recorded_connectors is None:
        try:
            from easy_agent.services.session_runtime import load_connectors

            recorded_connectors = await run_blocking(load_connectors, get_database(), session_id)
            if recorded_connectors is not None:
                _session_connectors[session_id] = recorded_connectors
        except Exception:
            recorded_connectors = None
    only_connectors = None if recorded_connectors is None else list(recorded_connectors)

    # 联网搜索：仅在「本轮请求开启」且「web_search 已配置 api_url + api_key」时注入。
    # 未配置 api_key（或功能关闭）时静默降级为未开启，不影响其余功能。
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

    # 模型切换 / 联网搜索开关变化 / 专家变更：缓存的 Agent 不匹配，驱逐后重建
    cached = _session_agents.get(session_id)
    if cached is not None:
        cached_web_search = bool(getattr(cached, "enable_web_search", False))
        # enable_web_search=None（如 HITL 恢复）：不比较开关，直接复用缓存 Agent
        search_matches = (
            enable_web_search is None or web_search_enabled == cached_web_search
        )
        # 专家变更同样必须驱逐：否则「换专家不生效」，且专家人设会黏在后续的
        # 普通聊天上（模型与联网开关都没变，缓存会一直命中）。
        # 连接器变更的驱逐不在这里：会话级开关（set_session_connectors）写入时
        # 就已直接驱逐过该会话的缓存 Agent。
        expert_changed = expert_key != _session_expert.get(session_id, "")
        # 技能作用域变化同样必须驱逐：技能清单在 Agent 构造时注入，缓存命中会一直
        # 沿用旧清单（专家 A 的白名单黏在专家 B 上 = 隔离失效）。
        cached_scope = getattr(cached, "skill_whitelist", None)
        cached_scope_key = None if cached_scope is None else tuple(cached_scope)
        scope_changed = cached_scope_key != current_scope
        if (
            effective_model != getattr(cached, "model_name", None)
            or not search_matches
            or expert_changed
            or scope_changed
        ):
            logger.info(
                f"[{session_id[-5:]}] Agent 参数变化 | "
                f"model: {getattr(cached, 'model_name', '?')} -> {effective_model} | "
                f"web_search: {cached_web_search} -> {web_search_enabled} | "
                f"expert: {_session_expert.get(session_id, '') or '(通用)'} -> "
                f"{expert_key or '(通用)'} | "
                f"skill_scope: {cached_scope_key or '全量'} -> {current_scope or '全量'} | "
                f"connectors: {only_connectors if only_connectors is not None else '(全部)'} | "
                f"驱逐旧 Agent 重建 | "
                f"{describe_prompt('本轮 extra', system_prompt_extra)} | "
                f"旧终稿指纹={prompt_fingerprint(getattr(cached, 'system_prompt', ''))}"
            )
            _session_agents.pop(session_id, None)
        else:
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

    # 本会话的连接器集合只读不写：写入由 set_session_connectors 负责（会话级开关）。
    if only_connectors is None:
        logger.info(f"[{session_id[-5:]}] 🔌 未限定连接器，注入全部启用连接器的 MCP 工具")
    elif only_connectors:
        logger.info(
            f"[{session_id[-5:]}] 🔌 按会话开关限定 MCP 工具 | {only_connectors}"
        )
    else:
        logger.info(f"[{session_id[-5:]}] 🔌 本会话连接器已全部关闭，不注入连接器工具")

    # MCP 工具：用户专属 mcp.json 优先，无则按需加载全局 mcp.json；
    # server_names=only_connectors 时只加载这些连接器的工具（None = 全部启用项，[] = 不加载）。
    user_mcp_path = Config.get_user_mcp_path(username, config)
    if user_mcp_path.exists():
        logger.info(f"[{session_id[-5:]}] 加载用户专属 MCP | {user_mcp_path}")
        try:
            # SEC-04：不为普通用户的自定义 stdio 连接器拉起进程
            mcp_tools = await load_mcp_tools_for_user(
                username, server_names=filter_server_names(username, only_connectors)
            )
        except Exception as e:
            logger.warning(
                f"[{session_id[-5:]}] 加载用户 MCP 失败，尝试全局: {e}"
            )
            mcp_tools = await load_mcp_tools_for_user(
                None, server_names=only_connectors
            )
    else:
        # 无用户专属 mcp.json，按需加载全局 mcp.json（缓存命中时零开销）
        logger.info(
            f"[{session_id[-5:]}] 无用户专属 mcp.json，按需加载全局 MCP"
        )
        try:
            mcp_tools = await load_mcp_tools_for_user(
                None, server_names=only_connectors
            )
        except Exception as e:
            logger.warning(
                f"[{session_id[-5:]}] 加载全局 MCP 失败: {e}"
            )
            mcp_tools = []

    # 工具装配：MCP 工具作为基底，其余按工具注册表声明的条件/顺序追加。
    # 工具元数据（是否内置/写操作/HITL/注入顺序）单一来源见 tools/registry.py；
    # 本函数不再逐工具硬编码 if/append，新增工具只需改注册表。
    tools = build_session_tools(
        mcp_tools,
        ToolBuildContext(
            config=config,
            username=username,
            session_id=session_id,
            workspace_name=workspace_name,
            enable_hitl=enable_hitl,
            model_name=model_name or "",
            expert_session=bool(expert_key),
            web_search_tool=web_search_tool,
            edit_targets=_session_edit_targets,
            turn_skills=_session_turn_skills,
            factories=_tool_factories(),
        ),
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
       model_name=model_name,
        enable_web_search=web_search_enabled,
        system_prompt_extra=system_prompt_extra,
        # 技能作用域（专家技能硬隔离）：None = 全量，非 None = 只挂这些技能目录
        skill_whitelist=None if current_scope is None else list(current_scope),
   )
    _session_agents[session_id] = agent
    _touch_agent_cache(session_id)
    _session_expert[session_id] = expert_key
    _evict_agent_cache(session_id)
    logger.info(
        f"[{session_id[-5:]}] Agent 实例创建成功 | workspace: {agent.workspace_dir} | "
        f"model: {agent.model_name} | mcp_tools: {len(tools)} | "
        f"expert: {expert_key or '(通用)'} | "
        f"skill_scope: {list(current_scope) if current_scope is not None else '全量'} | "
        f"{describe_prompt('本轮 extra', system_prompt_extra)} | "
        f"{describe_prompt('终稿 system_prompt', getattr(agent, 'system_prompt', ''))}"
    )
    return agent


def invalidate_user_agents(username: str) -> int:
    """Evict all cached agents belonging to ``username``.

    Called after the user edits their mcp.json (or other per-user config) so
    that the next request rebuilds the agent with fresh settings. Returns the
    number of evicted agents.
    """
    global _session_agents, _session_expert
    if not username:
        return 0
    safe = Config.sanitize_username(username)
    to_remove = [
        sid for sid, a in _session_agents.items()
        if getattr(a, "safe_username", "") == safe
    ]
    for sid in to_remove:
        _session_agents.pop(sid, None)
        _session_expert.pop(sid, None)
        # 会话级连接器开关（_session_connectors）刻意保留：它是用户在输入框里做的
        # 显式选择，不该因为「改了 mcp.json」而静默复位；被卸载/停用的 server 会被
        # MCP 加载层自动跳过并记 warning。
    evicted_graphs = get_agent_graph_cache().invalidate_user(safe)
    logger.info(
        f"用户 {username} 的缓存 Agent 已失效 | 清除 {len(to_remove)} 个 | "
        f"编译图 {evicted_graphs} 张 | sessions={to_remove}"
    )
    return len(to_remove)


def remove_session_agent(session_id: str):
    global _session_agents, _session_expert
    # 专家记录一并清除：否则下次请求会因 expert_key 与残留值相同而误判「未变更」，
    # 跳过本应发生的重建。清除后「未变更」的判定自动退化为「与 ""（通用）相同」。
    _session_expert.pop(session_id, None)
    # 技能作用域 / 本轮技能同生共死：缓存 Agent 没了，旧白名单不能再黏在下一轮请求上
    _session_skill_scope.pop(session_id, None)
    _session_turn_skills.pop(session_id, None)
    # 待修改专家绑定随会话 Agent 一起失效：绑定语义是「本会话内点编辑带来的」，
    # 会话 Agent 没了绑定也没意义；下一次点编辑会重新写入。
    _session_edit_targets.pop(session_id, None)
    # 连接器记录**不在这里清除**：它是用户在「+ → 连接器」面板里显式设置的会话级开关，
    # 而本函数还会被「记忆更新 / 取消生成 / 定时任务结束」等场景调用，清掉会让用户
    # 每次记忆更新后开关悄悄复位。会话真正终结（删除会话）由 clear_session_connectors 负责。
    if session_id in _session_agents:
        del _session_agents[session_id]
        logger.info(f"[{session_id[-5:]}] Agent 缓存已清除")


def set_session_turn_skills(session_id: str, dirs: list[str] | None) -> list[str]:
    """记录「本轮用户在输入框选中的技能目录名」，供同轮 ``save_expert`` 落库。

    创建 / 修改专家走的是对话式流程（expert-creator 技能 + ``save_expert`` 工具），
    用户在输入框选的技能包只进聊天请求的 ``skills`` 字段——工具侧看不到请求体，
    因此由 ``api/chat.py`` 在这里暂存一份（与 ``_session_edit_targets`` 同性质）。

    规则：**非空才覆盖**，空值保留上一次——创建流程可能跨多轮（Agent 先追问再保存），
    用户每轮都会重新带上胶囊，此时不该因为中间某一轮没带胶囊就把选择清空。
    Returns: 本次会话当前生效的技能目录名（便于调用方打日志）。
    """
    cleaned = [str(name).strip() for name in (dirs or []) if str(name).strip()]
    if cleaned and session_id:
        _session_turn_skills[session_id] = cleaned
    return list(_session_turn_skills.get(session_id) or [])


def clear_session_turn_skills(session_id: str) -> None:
    """清空本轮的技能暂存（``save_expert`` 落库后调用，避免黏到下一轮无关的创建）。"""
    _session_turn_skills.pop(session_id, None)


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
    """写进程内绑定表；返回规范化后的专家 ID，无效输入返回空串（不绑定）。"""
    expert_agent_id = str(expert_agent_id or "").strip()
    if not session_id or not expert_agent_id:
        return ""
    if _session_edit_targets.get(session_id) != expert_agent_id:
        logger.info(
            f"[{session_id[-5:]}] ✏️ 已绑定待修改专家 | expert={expert_agent_id}"
        )
    _session_edit_targets[session_id] = expert_agent_id
    return expert_agent_id


def _save_edit_target(session_id: str, expert_agent_id: str) -> None:
    try:
        from easy_agent.services.session_runtime import save_edit_target

        save_edit_target(get_database(), session_id, expert_agent_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 待修改专家落库失败: {exc}")


def get_session_edit_target(session_id: str) -> str:
    """返回会话当前绑定的「待修改专家」ID（"" 表示未绑定）。供测试与排查使用。"""
    return _session_edit_targets.get(session_id or "", "")


def get_session_expert(session_id: str) -> str:
    """返回会话当前生效的专家 Agent ID（"" 表示通用 Agent）。主要供测试与排查使用。"""
    return _session_expert.get(session_id, "")


def get_session_connectors(session_id: str) -> tuple[str, ...] | None:
    """返回会话显式启用的连接器（mcp.json 的 server 名）。

    ``None`` 表示该会话尚未设置过开关 —— 等价于「全部启用连接器」；
    空元组表示用户把开关全部关掉（不注入任何连接器工具）。
    与 ``get_session_expert`` 同理，主要供接口（连接器开关状态查询）与排查使用。
    """
    return _session_connectors.get(session_id)


def set_session_connectors(session_id: str, connector_servers: list[str]) -> int:
    """写入会话级连接器启用集合（server 名，空列表 = 全部关闭），并驱逐缓存 Agent。

    由 ``api/connectors.py`` 的 ``/agent/connectors/session`` 调用：输入框「+」面板里
    的连接器开关切换后，前端把该会话**全部**启用项整体提交过来（全量覆盖，不落库、
    不改 mcp.json，仅本次会话有效）。

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
    _session_connectors[session_id] = normalized
    evicted = 0
    if session_id in _session_agents:
        _session_agents.pop(session_id, None)
        evicted = 1
    logger.info(
        f"[{session_id[-5:]}] 🔌 会话连接器开关已更新 | "
        f"{list(normalized) if normalized else '(全部关闭)'} | 驱逐 Agent: {evicted} 个"
    )
    return normalized, evicted


def _save_connectors(session_id: str, normalized: tuple[str, ...]) -> None:
    try:
        from easy_agent.services.session_runtime import save_connectors

        save_connectors(get_database(), session_id, list(normalized))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 会话连接器落库失败: {exc}")


def clear_session_connectors(session_id: str) -> None:
    """清除会话的连接器开关记录（会话被删除等「会话终结」场景专用）。"""
    global _session_connectors
    if _session_connectors.pop(session_id, None) is not None:
        logger.info(f"[{session_id[-5:]}] 会话连接器开关记录已清除")


def register_stream_task(session_id: str, task: asyncio.Task[None]) -> None:
    """注册会话的流式输出任务，使其可被取消。

    若该会话已存在未完成的流式任务（通常不应发生），先取消旧的，
    避免多个 astream 协程并发写同一会话。
    """
    _acquire_stream_lock(session_id)
    _remember_stream_task(session_id, task)


async def aregister_stream_task(session_id: str, task: asyncio.Task[None]) -> None:
    """:func:`register_stream_task` 的异步版本：获取跨实例流锁的写库放进线程。"""
    await run_blocking(_acquire_stream_lock, session_id)
    _remember_stream_task(session_id, task)


def _acquire_stream_lock(session_id: str) -> None:
    """获取 DB 流锁；会话已被占用时抛 ``SessionBusyError``，其它错误只记日志。"""
    from easy_agent.services.session_runtime import SessionBusyError, acquire_stream

    try:
        acquire_stream(get_database(), session_id)
    except SessionBusyError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 会话流锁获取失败: {exc}")


def _remember_stream_task(session_id: str, task: asyncio.Task[None]) -> None:
    from easy_agent.services.session_runtime import SessionBusyError

    old = _session_stream_tasks.get(session_id)
    if old and not old.done() and old is not task:
        # 旧实现直接 old.cancel()：旧任务的 hub.close() 会把 EOF 发给新订阅者，
        # 造成「新流 0 条内容即结束、旧流被静默取消」。改为显式拒绝并发。
        raise SessionBusyError(
            f"会话 {session_id} 正在生成回复，请等待完成或先取消当前生成"
        )
    _session_stream_tasks[session_id] = task


def is_session_streaming(session_id: str) -> bool:
    """该会话当前是否有本进程内运行中的流式任务（用于并发互斥/409）。"""
    task = _session_stream_tasks.get(session_id)
    return bool(task and not task.done())


def unregister_stream_task(
    session_id: str, task: asyncio.Task[None] | None = None
) -> None:
    """流式任务结束后注销自身。

    传入 task 时仅当注册的仍是该 task 才移除：客户端断开后后台任务会继续运行，
    若用户随后对同一会话发起新流，新任务会先 register；旧任务结束时的 finally
    若无条件 pop 会误删新任务的注册，导致 /cancel 失效。
    """
    _forget_stream_task(session_id, task)
    _release_stream_lock(session_id)


async def aunregister_stream_task(
    session_id: str, task: asyncio.Task[None] | None = None
) -> None:
    """:func:`unregister_stream_task` 的异步版本：释放跨实例流锁的写库放进线程。"""
    _forget_stream_task(session_id, task)
    await run_blocking(_release_stream_lock, session_id)


def _forget_stream_task(session_id: str, task: asyncio.Task[None] | None) -> None:
    if task is None:
        _session_stream_tasks.pop(session_id, None)
    elif _session_stream_tasks.get(session_id) is task:
        _session_stream_tasks.pop(session_id, None)


def _release_stream_lock(session_id: str) -> None:
    try:
        from easy_agent.services.session_runtime import release_stream

        release_stream(get_database(), session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 会话流锁释放失败: {exc}")


async def cancel_stream_task(session_id: str) -> bool:
    """取消会话正在运行的流式任务。

    通过 task.cancel() 向 astream 协程注入 CancelledError，
    streaming.py 的 except CancelledError 分支会保存已生成的部分回复。
    Returns:
        True 表示确实取消了一个正在运行的任务。
    """
    import time as _time

    try:
        from easy_agent.services.session_runtime import request_cancel

        await run_blocking(request_cancel, get_database(), session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 写入跨实例取消标记失败: {exc}")

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


async def drain_stream_tasks(timeout: float = 20.0) -> None:
    """Wait for in-flight SSE/astream tasks on shutdown, then cancel leftovers."""
    tasks = [t for t in list(_session_stream_tasks.values()) if t and not t.done()]
    if not tasks:
        return
    logger.info("关闭前等待 %s 个流式任务结束（最多 %.0fs）", len(tasks), timeout)
    _done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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
