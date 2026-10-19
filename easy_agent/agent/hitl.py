"""HITL helpers: destructive shell detection, interrupt config and execute copy.

FW-04：execute 的人工审批说明不再 monkeypatch ``FilesystemMiddleware.__init__``，
改为在启动期/首次建图前通过 DeepAgents 官方（beta）能力
``HarnessProfile.tool_description_overrides`` 注册
（:func:`register_execute_description_profiles`）。旧入口
:func:`install_execute_tool_description` 保留为 deprecated 兼容包装，不再改写框架类。

FW-12：``interrupt_on`` 不再静态预置全部 HITL 工具，而由
:func:`build_interrupt_config` 按「本会话实际注入的工具」动态构造；
未注入的注册表 HITL 工具只记 debug 日志，不进入配置（DeepAgents 对
``interrupt_on`` 里不存在的工具名静默无效，见 T1-14）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping

from deepagents.middleware.filesystem import EXECUTE_TOOL_DESCRIPTION
from langchain.agents.middleware import InterruptOnConfig, ToolCallRequest

from easy_agent.tools.registry import (
    ToolSpec,
    builtin_tool_names,
    default_interrupt_tool_names,
    hitl_tool_specs,
    name_heuristic_exempt_tools,
)

logger = logging.getLogger("easy-agent.hitl")

_DESTRUCTIVE_CMD_PATTERNS = [
    r"\brm\b",
    r"\brmdir\b",
    r"\bunlink\b",
    r"\bshred\b",
    r"\bfind\s+.*\s-delete\b",
    r"\bdel\b",
    r"\berase\b",
    r"\brd\b",
    r"\bRemove-Item\b",
    r"sudo\s+rm\b",
    r"/\s*bin\s*/\s*rm\b",
]

_DIR_DELETION_PATTERNS = [
    r"\brmdir\b",
    r"\brm\b\s+(-\w*[rRd])",
    r"\bfind\s+.*\s-delete\b",
    r"\bshred\b\s+(-\w*[rR])",
    r"\brd\b(?:\s+-\w*s)?",
    r"\bdel\b(?:\s+[^&|;]*)\s/-s\b",
    r"\bRemove-Item\b[^;|&]*-Recurse\b",
]

# 默认人工审批名单：由工具注册表派生（无条件 HITL 工具），
# 内容与顺序保持改造前逐字一致（save_expert -> create_scheduled_task）。
# FW-12 起 agent.py 不再直接消费本常量：它只作为注册表派生结果的兼容出口，
# 实际 interrupt_on 由 build_interrupt_config() 按会话实际工具集过滤。
DEFAULT_INTERRUPT_TOOLS = default_interrupt_tool_names()

# 名字启发式的豁免集合同样由注册表派生：已单独配置 HITL 的工具与只读内置工具
# 不参与「名字含 write/delete/... 即视为写操作」的判定。
_NAME_HEURISTIC_EXEMPT = name_heuristic_exempt_tools()

_MUTATING_TOOL_RE = re.compile(
    r"(write|delete|update|insert|drop|create|put|remove|save|upload|execute_sql)",
    re.I,
)

HITL_SYSTEM_NOTE = (
    "敏感工具会触发人工审批后再执行：沙箱内的文件删除类 execute、"
    "save_expert、create_scheduled_task、access_knowledge_original 的 materialize，"
    "以及服务端声明为写操作、或名称含 write/delete/update/insert 等写操作的 MCP 工具。"
    "execute 走 bwrap/Landlock，二者都不可用时 fail-closed，不会落到宿主机。"
)

EXECUTE_HITL_TOOL_DESCRIPTION: str = (
    "⚠️⚠️⚠️ 人工审批工具 (HUMAN-IN-THE-LOOP / HITL) ⚠️⚠️⚠️\n\n"
    "本工具在沙箱内执行 Shell 命令（Linux 优先 bwrap，其次 Landlock；"
    "二者都不可用时 fail-closed，命令会被拒绝，不会落到宿主机）。\n"
    "当命令属于「文件删除」类（如 rm / unlink / shred 删除文件）时，工具调用会触发【人工审批中断】：\n"
    "  • 调用会立即暂停，等待用户在对话界面上「批准 / 拒绝」；\n"
    "  • 在你收到用户的审批结果之前，命令【尚未真正执行】，不要假设它已成功；\n"
    "  • 若用户「拒绝」，请根据拒绝原因调整方案，不要原样重复发起被拒绝的删除命令；\n"
    "  • 目录删除（rm -r / rmdir 等）会被直接拒绝，不会进入审批流程。\n"
    "普通命令（查看文件、列出目录、编译、运行测试、安装依赖等）通常无需审批即可直接执行。\n\n"
    "—— 以下为工具的常规使用说明 ——\n\n"
    + EXECUTE_TOOL_DESCRIPTION
)


# ── FW-04：execute 描述通过官方 HarnessProfile 注入 ──────────────────────
#
# DeepAgents 0.7 的公开 beta API（0.6.8 起已有）：
#   HarnessProfile(tool_description_overrides={"execute": ...})
#   register_harness_profile("provider:model", profile)
# ``create_deep_agent`` 在模型已构建（本项目总是传模型实例）时，按
# ``_get_ls_params()["ls_provider"]`` + 模型 identifier 解析 profile，并把
# ``tool_description_overrides`` 传给真实 ``FilesystemMiddleware`` 实例。

#: profile 中被替换描述的工具名（``tool_description_overrides`` 的键）。
EXECUTE_TOOL_NAME = "execute"

#: 配置里的 ``protocol`` → 模型实例的 LangChain ``ls_provider``。
#: 拼 profile 查找键时用的是后者，不是配置里的网关名（如 deepseek/minimax）。
_PROTOCOL_TO_LS_PROVIDER = {"openai": "openai", "anthropic": "anthropic"}

#: 已成功注册 execute 描述的 profile 键（进程级：用于自检与幂等判断）。
_EXECUTE_DESC_PROFILE_KEYS: set[str] = set()

_DEPRECATED_WARNED = False


def _is_directory_deletion(command: str) -> bool:
    """命令是否命中目录删除模式（策略层直接拒绝，见 ``_is_destructive_command``）。"""
    if not command:
        return False
    return any(re.search(pat, command) for pat in _DIR_DELETION_PATTERNS)


def looks_like_mutating_tool(name: str) -> bool:
    """Heuristic for MCP / extra tools that should pause for HITL."""
    if not name or name in _NAME_HEURISTIC_EXEMPT:
        return False
    return bool(_MUTATING_TOOL_RE.search(name))


def mcp_annotation_decision(tool: object) -> bool | None:
    """按 MCP 服务端声明的 ToolAnnotations 判定是否审批（DA-09 第一步）。

    ``langchain-mcp-adapters`` 把 annotations 平铺进 ``tool.metadata``（证据包装会保留）。
    只认**显式**取值（未声明的字段是 ``None``，不套用规范里的默认值，否则每个工具都要审批）：

    - ``destructiveHint=True`` → 审批（宁可多问）；
    - ``readOnlyHint=True`` → 免审批；
    - ``readOnlyHint=False`` → 会改动外部状态 → 审批；
    - 都没有 → ``None``，由调用方回退名字启发式。

    注意：annotations 是服务端的自我声明；连接哪个 MCP 服务由用户 / 管理员决定。
    """
    metadata = getattr(tool, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    if metadata.get("destructiveHint") is True:
        return True
    read_only = metadata.get("readOnlyHint")
    if read_only is True:
        return False
    if read_only is False:
        return True
    return None


def _is_knowledge_materialize(request: ToolCallRequest) -> bool:
    tool_call = request.tool_call if hasattr(request, "tool_call") else {}
    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
    return str(args.get("action", "")).lower() == "materialize"


def _model_profile_key(llm_config) -> str | None:
    """把一份模型配置映射成 HarnessProfile 查找键 ``provider:model``。

    模型实例存在两种 provider 口径：配置里的 ``provider`` 是网关名
    （deepseek / minimax / ...），而 deepagents 查找 profile 用的是 LangChain
    的 ``ls_provider``（ChatOpenAI 固定 ``openai``、ChatAnthropic 固定
    ``anthropic``）。因此这里按 ``protocol`` 推导后半段的 provider。
    """
    # 与 model.resolve_llm_config 对齐：protocol 为空时按 openai 建模型。
    protocol = (
        str(getattr(llm_config, "protocol", "") or "").strip().lower() or "openai"
    )
    provider = _PROTOCOL_TO_LS_PROVIDER.get(protocol, protocol)
    identifier = str(getattr(llm_config, "model", "") or "").strip()
    if not provider or not identifier:
        return None
    if ":" in identifier:
        # deepagents 对含冒号的 identifier 只做 identifier 查询，避免双冒号键。
        return identifier
    return f"{provider}:{identifier}"


def _configured_model_profile_keys(config) -> tuple[str, ...]:
    """列出配置中每个模型对应的 profile 键（去重，保持配置顺序）。

    ``config is None`` 时退化为两个受支持协议的 provider 级键，仅用于兼容
    旧的无参调用；正常路径总是传入 ``Config``（``models`` + active ``llm``）。
    """
    if config is None:
        return tuple(
            _PROTOCOL_TO_LS_PROVIDER[protocol] for protocol in ("openai", "anthropic")
        )

    keys: dict[str, None] = {}
    models = getattr(config, "models", None)
    if isinstance(models, Mapping):
        for llm_config in models.values():
            key = _model_profile_key(llm_config)
            if key:
                keys[key] = None
    key = _model_profile_key(getattr(config, "llm", None))
    if key:
        keys[key] = None
    return tuple(keys)


def register_execute_description_profiles(config=None) -> tuple[str, ...]:
    """为配置中的每个模型注册 execute 描述的 ``HarnessProfile``（幂等）。

    这是 FW-04 的主入口：用官方 beta API 替代对
    ``FilesystemMiddleware.__init__`` 的 monkeypatch。注册是加性的：
    同一进程内重复调用只对尚未注册的键生效。

    Args:
        config: ``Config``（读取 ``models`` 与 active ``llm``）。``None`` 仅用于
            旧的无参调用，退化为 provider 级（openai/anthropic）注册。

    Returns:
        当前进程内已注册的 profile 键（排序后）；失败项不在结果内。
    """
    keys = _configured_model_profile_keys(config)
    if not keys:
        logger.error("execute 描述 HarnessProfile 未注册：配置中没有任何模型")
        return ()

    pending = [key for key in keys if key not in _EXECUTE_DESC_PROFILE_KEYS]
    if not pending:
        return tuple(sorted(_EXECUTE_DESC_PROFILE_KEYS))

    try:
        from deepagents import HarnessProfile, register_harness_profile
    except Exception as exc:  # noqa: BLE001 - beta API 可能被移除/改名
        logger.error(
            "deepagents HarnessProfile 不可用（%s: %s），execute 描述无法注入；"
            "请对照 deepagents 版本修正 hitl.py",
            type(exc).__name__,
            exc,
        )
        return tuple(sorted(_EXECUTE_DESC_PROFILE_KEYS))

    profile = HarnessProfile(
        tool_description_overrides={EXECUTE_TOOL_NAME: EXECUTE_HITL_TOOL_DESCRIPTION}
    )
    for key in pending:
        try:
            register_harness_profile(key, profile)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "注册 execute 描述 HarnessProfile 失败 | key=%s | %s", key, exc
            )
            continue
        _EXECUTE_DESC_PROFILE_KEYS.add(key)

    registered = tuple(sorted(_EXECUTE_DESC_PROFILE_KEYS))
    logger.info(
        "execute 描述 HarnessProfile 已注册 | keys=%s | deepagents beta API",
        list(registered),
    )
    return registered


def execute_description_profile_keys() -> tuple[str, ...]:
    """当前进程内已注册 execute 描述的 profile 键（排序）。"""
    return tuple(sorted(_EXECUTE_DESC_PROFILE_KEYS))


def install_execute_tool_description(config=None) -> bool:
    """Deprecated：改为注册官方 ``HarnessProfile``，不再 monkeypatch 框架类。

    保留函数名仅为兼容既有调用方（含测试导入）。新代码请直接使用
    :func:`register_execute_description_profiles`。
    """
    global _DEPRECATED_WARNED
    if not _DEPRECATED_WARNED:
        _DEPRECATED_WARNED = True
        logger.warning(
            "install_execute_tool_description() 已废弃：execute 描述改由 "
            "HarnessProfile.tool_description_overrides 注入，本函数不再修改 "
            "FilesystemMiddleware"
        )
    return bool(register_execute_description_profiles(config))


def execute_description_installed() -> bool:
    """execute 描述是否已通过 HarnessProfile 注册（启动期自检用）。"""
    return bool(_EXECUTE_DESC_PROFILE_KEYS)


def _is_destructive_command(request: ToolCallRequest) -> bool:
    """``execute`` 的 HITL 条件：文件删除类命令才需要审批。

    目录删除（``rm -r`` / ``rmdir`` / ``find -delete`` 等）返回 ``False``：
    它不进入 HITL，而由 ``backends/shell.py`` 在执行前直接拒绝（策略层）。
    这是有意分层，不是漏配——用户无法通过「批准」执行目录删除。
    """
    tool_call = request.tool_call if hasattr(request, "tool_call") else {}
    command = (tool_call.get("args", {}) if isinstance(tool_call, dict) else {}).get(
        "command", ""
    )
    if not command:
        return False
    if not any(re.search(pat, command) for pat in _DESTRUCTIVE_CMD_PATTERNS):
        return False
    if _is_directory_deletion(command):
        return False
    return True


# ── FW-12：按实际注入的工具动态构造 interrupt_on ─────────────────────────
#
# DeepAgents 的 HumanInTheLoopMiddleware 只按工具名匹配 interrupt_on；
# 名字不在本会话工具表里时**静默跳过**（不建立中断、也不报错）。因此静态
# 预置 execute / DEFAULT_INTERRUPT_TOOLS / access_knowledge_original 会让
# 「知识库未启用」「expert-creator 未激活」等会话的配置看起来覆盖了工具、
# 实际却不生效（T1-14）。本模块改为只消费实际工具集合。

#: 注册表 ``hitl_predicate`` 名称 → 实际 ``when`` 谓词。
#: 注册表是叶子模块（只登记名字），谓词实现与绑定都留在本模块。
HITL_WHEN_PREDICATES: Mapping[str, Callable[[ToolCallRequest], bool]] = {
    "is_destructive_command": _is_destructive_command,
    "is_knowledge_materialize": _is_knowledge_materialize,
}


def _tool_name(tool: object) -> str:
    """从工具对象或字符串取出工具名；空值/无名对象返回 ``""``。"""
    if isinstance(tool, str):
        return tool
    return str(getattr(tool, "name", "") or "")


def build_interrupt_config(
    tool_names: Iterable[object],
    *,
    enable_hitl: bool = True,
    specs: Iterable[ToolSpec] | None = None,
    predicates: Mapping[str, Callable[[ToolCallRequest], bool]] | None = None,
    sid: str = "",
) -> dict[str, bool | InterruptOnConfig] | None:
    """按「实际注入到本会话的工具」构造 ``interrupt_on``。

    规则（顺序与改造前一致）：

    1. ``enable_hitl=False`` → ``None``（不启用 HITL，行为不变）；
    2. 注册表声明的 HITL 工具按 ``hitl_order`` 排序，**只保留本会话实际
       存在的工具**；未注入的条目记 debug 日志并跳过。``execute`` 由注册表的
       builtin 集合视为始终可用，因此总在配置里；若未来框架移除/改名它，
       需要同步注册表（否则仍会静默无效，见设计文档风险）；
    3. 条件 HITL（``execute`` / ``access_knowledge_original``）沿用注册表的
       ``hitl_predicate`` 绑定 ``when``；谓词缺失时退化为无条件审批
       （宁可多问，不可漏问）；
    4. 其余**实际注入**的非内置工具：先看 MCP annotations
       （:func:`mcp_annotation_decision`），没有可用标注再按名字启发式判定，
       未登记名字一律视为 MCP（与 ``resolve_tool_source`` 口径一致）；
    5. 未知/未注入工具不进入配置。

    Args:
        tool_names: 本会话实际注入的非内置工具（工具对象或工具名）。
            ``EasyAgent.mcp_tools`` 实际承载的是「MCP + 应用注入工具」全集。
        enable_hitl: 是否启用人工审批（与 ``EasyAgent.enable_hitl`` 一致）。
        specs: 仅测试用；默认使用注册表的 ``hitl_tool_specs()``。
        predicates: 仅测试/扩展用；默认使用本模块的
            :data:`HITL_WHEN_PREDICATES`。
        sid: 日志用短会话 id。

    Returns:
        工具名 → ``InterruptOnConfig`` 的字典；未启用 HITL 时为 ``None``。
    """
    if not enable_hitl:
        return None

    tools = list(tool_names or ())
    ordered_names = [name for name in map(_tool_name, tools) if name]
    tool_objects = {_tool_name(tool): tool for tool in tools if not isinstance(tool, str)}
    builtin_names = set(builtin_tool_names())
    available = set(ordered_names) | builtin_names
    predicate_map = HITL_WHEN_PREDICATES if predicates is None else predicates
    resolved_specs = tuple(hitl_tool_specs() if specs is None else specs)

    config: dict[str, bool | InterruptOnConfig] = {}
    for spec in resolved_specs:
        if spec.name not in available:
            logger.debug(
                "[%s] HITL 工具 %s 未实际注入，跳过 interrupt_on",
                sid or "-",
                spec.name,
            )
            continue
        options: dict[str, object] = {
            "allowed_decisions": list(spec.interrupt_decisions)
        }
        if spec.hitl_predicate:
            predicate = predicate_map.get(spec.hitl_predicate)
            if predicate is None:
                logger.error(
                    "[%s] 未知 HITL 条件谓词 %r（工具 %r），退化为无条件审批",
                    sid or "-",
                    spec.hitl_predicate,
                    spec.name,
                )
            else:
                options["when"] = predicate
        config.setdefault(spec.name, InterruptOnConfig(**options))

    # MCP / 动态工具：只对**实际注入**且未进声明名单的工具走名字启发式。
    # 内置工具（write_file/edit_file 等）不参与：它们由框架提供，改造前也不在
    # EasyAgent.mcp_tools 里。read_uploaded_file 名字含 upload，历史上会被该
    # 启发式加入审批；FW-12 保持这一现状（行为等价），是否改为 is_hitl 由后续
    # 产品决策处理（见设计文档「未覆盖工具」）。
    # DA-09：MCP 工具优先看服务端声明的 annotations，没有可用标注才回退名字启发式。
    for name in ordered_names:
        if name in builtin_names or name in config:
            continue
        by_name = looks_like_mutating_tool(name)
        decision = None
        if name not in _NAME_HEURISTIC_EXEMPT:
            decision = mcp_annotation_decision(tool_objects.get(name))
        if decision is None:
            decision = by_name
        elif decision != by_name:
            logger.info(
                "[%s] 工具 %s 按 MCP annotations %s（名字启发式结论相反）",
                sid or "-",
                name,
                "需要审批" if decision else "免审批",
            )
        if decision:
            config.setdefault(
                name,
                InterruptOnConfig(allowed_decisions=["approve", "reject"]),
            )
    return config
