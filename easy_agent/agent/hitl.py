"""HITL 人工审批配置：破坏性命令识别、按会话注入工具动态构造 interrupt_on 及 execute 说明注册。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping

from deepagents.middleware.filesystem import EXECUTE_TOOL_DESCRIPTION
from langchain.agents.middleware import InterruptOnConfig, ToolCallRequest

from easy_agent.sandboxing.command_policy import is_deletion_command, is_directory_deletion
from easy_agent.tools.registry import (
    DESTRUCTIVE_INTERRUPT_DECISIONS,
    MCP_INTERRUPT_DECISIONS,
    ToolSpec,
    approval_tool_specs,
    builtin_tool_names,
    default_interrupt_tool_names,
    get_tool_spec,
    hitl_tool_specs,
    name_heuristic_exempt_tools,
)

logger = logging.getLogger("easy-agent.hitl")

# 无条件审批工具兼容出口；实际 interrupt_on 由 build_interrupt_config 按会话工具集动态构造。
DEFAULT_INTERRUPT_TOOLS = default_interrupt_tool_names()

# config.hitl.tools 未配置时的默认安全审批名单（不含产品交互中断 ask_user）。
DEFAULT_APPROVAL_TOOLS = tuple(spec.name for spec in approval_tool_specs())

# 用户级审批模式（设置 → 权限设置）。
# manual：名单里的工具都要用户批准。
# auto：摘掉 AUTO_APPROVED_APPROVAL_TOOLS，其余保留。
# auto 不是 enable_hitl=False。总开关关掉后，定时任务注入、提示词段落和恢复语义都会变。
# 名单摘空后 DeepAgents 不安装审批中间件，enable_hitl 仍为 True。
APPROVAL_MODE_MANUAL = "manual"
APPROVAL_MODE_AUTO = "auto"
APPROVAL_MODES: tuple[str, ...] = (APPROVAL_MODE_MANUAL, APPROVAL_MODE_AUTO)

#: auto 模式下不再审批的工具，覆盖默认安全审批名单，因此 interrupt_on 为空。
#: ask_user 不在这里，规划确认仍然要用户响应。
#: delete 含递归删目录；rm -r / rmdir 仍由 backends/shell.py 拒绝。
#: save_expert 只覆盖自己的专家，他人专家由文件系统 deny 拦住。
AUTO_APPROVED_APPROVAL_TOOLS: frozenset[str] = frozenset(
    {
        "execute",
        "delete",
        "save_expert",
        "create_scheduled_task",
        "access_knowledge_original",
    }
)

#: auto 模式下不对 MCP / 动态注入工具做名字启发式或 annotations 审批。
#: manual 仍走这两条兜底。当前接入的 MCP 只有查询能力。
AUTO_APPROVES_MCP_TOOLS = True

# 名字启发式的豁免集合同样由注册表派生：已单独配置 HITL 的工具与只读内置工具
# 不参与「名字含 write/delete/... 即视为写操作」的判定。
_NAME_HEURISTIC_EXEMPT = name_heuristic_exempt_tools()

_MUTATING_TOOL_RE = re.compile(
    r"(write|delete|update|insert|drop|create|put|remove|save|upload|execute_sql)",
    re.I,
)

#: 删除类工具名（MCP / 动态工具）：审批只给「批准 / 拒绝」，不提供编辑参数。
#: ``rm`` 用词边界，避免 form/perform 这类词误命中。
_DELETE_TOOL_RE = re.compile(
    r"(delete|destroy|drop|erase|purge|remove|truncate|unlink)|\brm\b",
    re.I,
)

#: 开启 HITL 时由 ``prompt_guard.assemble_system_prompt`` 接在系统提示词末尾，告诉模型哪些操作
#: 要等用户批准。沙箱实现（bwrap / Landlock、fail-closed）模型用不上，写在 execute 的工具描述里。
HITL_SYSTEM_NOTE = (
    "## 人工审批\n\n"
    "以下操作会先暂停，等用户在界面上批准后才执行：文件删除类 execute 命令、"
    "删除文件或目录的 delete、save_expert、create_scheduled_task、"
    "access_knowledge_original 的 materialize，以及服务端声明为写操作、"
    "或名称含 write/delete/update/insert 等写操作的 MCP 工具。"
    "收到审批结果前不要假设操作已完成；用户拒绝后按其意见调整方案，不要原样重试。"
)

#: 用户开启「允许自动审批」时的替代文案：告诉模型哪几类操作已被放行、哪几类仍要等批准。
#: 工具描述是**按模型全局注册**的（``HarnessProfile``），没法按用户改，
#: 所以 execute / delete / save_expert 描述里「会先暂停等批准」的旧说法只能由本段提示词覆盖。
HITL_AUTO_SYSTEM_NOTE = (
    "## 人工审批（已全部放行）\n\n"
    "用户已开启「允许自动审批」：下列操作都会**直接执行**，不再暂停等用户批准。"
    "相关工具描述里「会先暂停、等你批准后再执行」的说法在当前设置下不适用，不必为此征询同意：\n"
    "- delete：删除文件或目录（含递归删除整个目录）；\n"
    "- execute：沙箱内的全部 Shell 命令，包括 rm / del / unlink 等删除类命令；\n"
    "- save_expert：在对话内新建 / 修改自己的专家；\n"
    "- create_scheduled_task：创建定时任务；\n"
    "- access_knowledge_original：把知识库原文落盘到工作区（materialize）；\n"
    "- 全部 MCP 工具（当前接入的 MCP 服务只提供查询能力）。\n\n"
    "策略层与权限层的硬限制不因本设置改变：execute 里的 rm -r / rmdir 等目录删除仍会被直接拒绝"
    "（删目录请用 delete 工具）；修改他人的专家、他人创建的数据与公共技能仍会被拒绝，"
    "遇到拒绝不要换路径重试。\n"
    "删除与覆盖都不可撤销：执行前确认路径正确，不要用通配符批量删除，也不要覆盖用户已有文件。"
)

# ── 审批日志（两种模式必须一眼可分） ─────────────────────────────────────
#
# 排查时最常被问的两件事：「我开了自动审批，日志里怎么没有任何痕迹」和
# 「这个删除到底是谁批的」。因此两条路径各有一句固定文案：
#
# * ``auto``：``interrupt_on`` 被摘空 → 框架不装 ``HumanInTheLoopMiddleware``，
#   工具链路上没有任何审批节点。由 ``ApprovalAuditMiddleware`` 在工具**执行前**
#   补 :data:`AUTO_APPROVAL_SKIPPED_NOTE`；
# * ``manual``：恢复阶段由 ``orchestrator`` 打 :data:`MANUAL_APPROVAL_DONE_NOTE`
#   （带审批结论），工具执行时不再重复记录。
AUTO_APPROVAL_SKIPPED_NOTE = "用户已选择自动审批，跳过人工审批"
MANUAL_APPROVAL_DONE_NOTE = "用户已人工审批"


def brief_args(args: object, limit: int = 300) -> str:
    """工具参数的单行摘要（审批日志用）：超长截断，取不到时返回 ``-``。"""
    if not isinstance(args, Mapping) or not args:
        return "-"
    text = str(dict(args)).replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"


EXECUTE_HITL_TOOL_DESCRIPTION: str = (
    "⚠️⚠️⚠️ 人工审批工具 (HUMAN-IN-THE-LOOP / HITL) ⚠️⚠️⚠️\n\n"
    "本工具在沙箱内执行 Shell 命令（Linux 优先 bwrap，其次 Landlock；"
    "二者都不可用时 fail-closed，命令会被拒绝，不会落到宿主机）。\n"
    "当命令属于「文件删除」类（如 rm / unlink / shred 删除文件）时，工具调用会触发【人工审批中断】：\n"
    "  • 调用会立即暂停，等待用户在对话界面上「批准 / 拒绝」；\n"
    "  • 在你收到用户的审批结果之前，命令【尚未真正执行】，不要假设它已成功；\n"
    "  • 若用户「拒绝」，请根据拒绝原因调整方案，不要原样重复发起被拒绝的删除命令；\n"
    "  • 目录删除（rm -r / rmdir 等）会被直接拒绝，不会进入审批流程；需要删除目录时改用 delete 工具（会请用户审批）。\n"
    "普通命令（查看文件、列出目录、编译、运行测试、安装依赖等）通常无需审批即可直接执行。\n\n"
    "—— 以下为工具的常规使用说明 ——\n\n"
    + EXECUTE_TOOL_DESCRIPTION
)

#: ``delete`` 的工具描述。目录删除只走这个工具，由用户审批把关（2026-11-07 确认）；
#: ``execute`` 里的 ``rm -r`` / ``rmdir`` 仍由 ``backends/shell.py`` 直接拒绝。框架默认描述
#: 没提审批，也没说 execute 删不了目录，这里补上（与 execute 的审批说明一致）。
DELETE_HITL_TOOL_DESCRIPTION: str = (
    "删除文件或目录，删除后无法恢复；删除目录会连同其中的全部内容一起删除。\n\n"
    "用法：\n"
    "- 传入要删除的绝对路径，只删除确定不再需要的文件或目录。\n"
    "- 开启人工审批时，调用会先暂停，等待用户在对话界面上「批准 / 拒绝」；"
    "收到结果之前尚未删除，不要假设已经成功。\n"
    "- 用户拒绝后根据原因调整方案，不要原样重复发起。\n"
    "- 删除目录时一次删掉整个目录，不要逐个删除其中的文件（每次调用都要审批一次）。\n"
    "- 删除目录只能用本工具：execute 里的 rm -r、rmdir 等目录删除命令会被系统直接拒绝。"
)


# execute / delete 的工具说明经 HarnessProfile 注入，不改 FilesystemMiddleware。
#
# DeepAgents 0.7（0.6.8 起）：
#   HarnessProfile(tool_description_overrides={"execute": ..., "delete": ...})
#   register_harness_profile("provider:model", profile)
# create_deep_agent 传入模型实例时，按 ls_provider + 模型 identifier 取 profile。

#: profile 中被替换描述的工具名（``tool_description_overrides`` 的键）。
EXECUTE_TOOL_NAME = "execute"
DELETE_TOOL_NAME = "delete"

#: 配置里的 ``protocol`` → 模型实例的 LangChain ``ls_provider``。
#: 拼 profile 查找键时用的是后者，不是配置里的网关名（如 deepseek/minimax）。
_PROTOCOL_TO_LS_PROVIDER = {"openai": "openai"}

#: 已成功注册 execute 描述的 profile 键（进程级：用于自检与幂等判断）。
_EXECUTE_DESC_PROFILE_KEYS: set[str] = set()

_DEPRECATED_WARNED = False


def _mcp_original_name(tool: object) -> str:
    """MCP 工具（可能被加了 server 前缀）的原始名；非 MCP 工具返回空串。"""
    metadata = getattr(tool, "metadata", None)
    if isinstance(metadata, Mapping):
        raw = metadata.get("mcp_original_name")
        if isinstance(raw, str):
            return raw
    return ""


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


def mcp_interrupt_decisions(tool_name: str, tool: object | None = None) -> list[str]:
    """MCP / 动态工具的决策集：删除类操作只给「批准 / 拒绝」。

    破坏性取两处证据：服务端 ``destructiveHint=True``，或（去掉 server 前缀后的）
    工具名命中删除类词根。任一成立即不提供编辑参数——改参数正是绕过删除策略的入口
    （例如把 rm 目标改成通配、把路径改到别的目录）。
    """
    metadata = getattr(tool, "metadata", None)
    destructive = (
        isinstance(metadata, Mapping) and metadata.get("destructiveHint") is True
    )
    if destructive or _DELETE_TOOL_RE.search(tool_name or ""):
        return list(DESTRUCTIVE_INTERRUPT_DECISIONS)
    return list(MCP_INTERRUPT_DECISIONS)


def _is_knowledge_materialize(request: ToolCallRequest) -> bool:
    tool_call = request.tool_call if hasattr(request, "tool_call") else {}
    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
    return str(args.get("action", "")).lower() == "materialize"


def _model_profile_key(llm_config) -> str | None:
    """把一份模型配置映射成 HarnessProfile 查找键 ``provider:model``。

    模型实例存在两种 provider 口径：配置里的 ``provider`` 是网关名
    （deepseek / minimax / ...），而 deepagents 查找 profile 用的是 LangChain
    的 ``ls_provider``（ChatOpenAI 固定 ``openai``）。因此这里按 ``protocol``
    推导后半段的 provider。
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

    ``config is None`` 时退化为受支持协议的 provider 级键，仅用于兼容
    旧的无参调用；正常路径总是传入 ``Config``（``models`` + active ``llm``）。
    """
    if config is None:
        return tuple(_PROTOCOL_TO_LS_PROVIDER.values())

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
    """为配置中的每个模型注册 execute / delete 描述的 ``HarnessProfile``（幂等）。

    用 HarnessProfile 注入 execute / delete 说明，不再改
    ``FilesystemMiddleware.__init__``。注册是加性的：
    同一进程内重复调用只对尚未注册的键生效。

    Args:
        config: ``Config``（读取 ``models`` 与 active ``llm``）。``None`` 仅用于
            旧的无参调用，退化为 provider 级（openai）注册。

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
        tool_description_overrides={
            EXECUTE_TOOL_NAME: EXECUTE_HITL_TOOL_DESCRIPTION,
            DELETE_TOOL_NAME: DELETE_HITL_TOOL_DESCRIPTION,
        }
    )
    for key in pending:
        try:
            register_harness_profile(key, profile)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "注册 execute / delete 描述 HarnessProfile 失败 | key=%s | %s", key, exc
            )
            continue
        _EXECUTE_DESC_PROFILE_KEYS.add(key)

    registered = tuple(sorted(_EXECUTE_DESC_PROFILE_KEYS))
    logger.info(
        "execute / delete 描述 HarnessProfile 已注册 | keys=%s | deepagents beta API",
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
    if not is_deletion_command(command):
        return False
    if is_directory_deletion(command):
        return False
    return True


# interrupt_on 只按本会话实际注入的工具名构造。
# HumanInTheLoopMiddleware 对不在工具表里的名字静默跳过，静态预置名单会看起来覆盖了、实际不生效。

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


def hitl_enabled(config, requested: bool = True) -> bool:
    """本次运行是否人工审批：调用方要（定时任务传 False），且 config.yaml 的 ``hitl.enabled`` 没关。"""
    return bool(requested) and bool(getattr(getattr(config, "hitl", None), "enabled", True))


def normalize_approval_mode(mode: object) -> str:
    """把任意输入收敛成合法审批模式；非法值一律回退 ``manual``（安全方向默认值）。"""
    value = str(mode or "").strip().lower()
    return APPROVAL_MODE_AUTO if value == APPROVAL_MODE_AUTO else APPROVAL_MODE_MANUAL


def is_auto_approval(approval_mode: object) -> bool:
    """当前是否「允许自动审批」（只放行 :data:`AUTO_APPROVED_APPROVAL_TOOLS`）。"""
    return normalize_approval_mode(approval_mode) == APPROVAL_MODE_AUTO


def auto_approved_tool_names(approval_tools: Iterable[str] | None = None) -> list[str]:
    """自动审批实际放行的审批项（按审批名单顺序；名单里没有的不返回）。

    供设置页展示「放行哪些」，保证文案与后端实际行为同源。
    """
    names = list(DEFAULT_APPROVAL_TOOLS if approval_tools is None else approval_tools)
    return [name for name in names if name in AUTO_APPROVED_APPROVAL_TOOLS]


def resolve_approval_tools(
    approval_tools: Iterable[str] | None, approval_mode: object
) -> list[str] | None:
    """按审批模式收敛出本轮审批名单。

    ``manual``：原样返回（``None`` 时交给 :func:`build_interrupt_config` 用默认名单）；
    ``auto``：从名单里摘掉 :data:`AUTO_APPROVED_APPROVAL_TOOLS`（execute / delete /
    save_expert / create_scheduled_task / access_knowledge_original），其余顺序不变。
    默认名单已被全部覆盖，所以正常配置下 ``auto`` 返回**空列表**——本轮没有任何审批项。
    """
    if not is_auto_approval(approval_mode):
        return None if approval_tools is None else [str(name) for name in approval_tools]
    names = list(DEFAULT_APPROVAL_TOOLS if approval_tools is None else approval_tools)
    return [str(name) for name in names if str(name).strip() not in AUTO_APPROVED_APPROVAL_TOOLS]


def auto_approves_mcp(approval_mode: object) -> bool:
    """auto 模式下 MCP / 动态注入工具是否需要审批（False = 直接执行）。"""
    return is_auto_approval(approval_mode) and AUTO_APPROVES_MCP_TOOLS


def _interrupt_options(
    name: str,
    spec: ToolSpec | None,
    tool: object | None,
    predicate_map: Mapping[str, Callable[[ToolCallRequest], bool]],
    sid: str,
) -> InterruptOnConfig:
    """名单里一个工具的审批选项：注册表 HITL 工具沿用它的决策和条件谓词，其余按名字决定能否改参数。"""
    if spec is None:
        return InterruptOnConfig(
            allowed_decisions=mcp_interrupt_decisions(_mcp_original_name(tool) or name, tool)
        )
    options: dict[str, object] = {"allowed_decisions": list(spec.interrupt_decisions)}
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
    return InterruptOnConfig(**options)


def build_interrupt_config(
    tool_names: Iterable[object],
    *,
    enable_hitl: bool = True,
    approval_tools: Iterable[str] | None = None,
    specs: Iterable[ToolSpec] | None = None,
    predicates: Mapping[str, Callable[[ToolCallRequest], bool]] | None = None,
    sid: str = "",
    approval_mode: str = APPROVAL_MODE_MANUAL,
) -> dict[str, bool | InterruptOnConfig] | None:
    """按审批名单和本会话实际注入的工具构造 ``interrupt_on``。

    ``ask_user`` 只要注入就进入配置，``enable_hitl=False`` 关不掉它。
    ``approval_tools`` 为 config.yaml 的 ``hitl.tools``，缺省用
    :data:`DEFAULT_APPROVAL_TOOLS`；名单里本会话没有的工具跳过。
    注册表工具沿用其决策和 ``when`` 谓词，谓词缺失时改为无条件审批。
    名单外的 MCP / 动态工具先看 annotations，再按名字启发式；
    ``approval_mode="auto"`` 时跳过这一步，并摘掉
    :data:`AUTO_APPROVED_APPROVAL_TOOLS`。
    没有任何可中断工具时返回 ``None``。

    Args:
        tool_names: 本会话注入的非内置工具（对象或名字）。
        enable_hitl: 是否启用安全审批。
        approval_tools: 审批名单；``None`` 用默认名单。
        specs: 测试用，默认 ``hitl_tool_specs()``。
        predicates: 测试用，默认 :data:`HITL_WHEN_PREDICATES`。
        sid: 日志用短会话 id。
        approval_mode: ``manual`` 或 ``auto``，非法值按 ``manual``。
    """
    tools = list(tool_names or ())
    ordered_names = [name for name in map(_tool_name, tools) if name]
    tool_objects = {_tool_name(tool): tool for tool in tools if not isinstance(tool, str)}
    builtin_names = set(builtin_tool_names())
    predicate_map = HITL_WHEN_PREDICATES if predicates is None else predicates
    all_specs = tuple(hitl_tool_specs() if specs is None else specs)
    interaction_specs = tuple(spec for spec in all_specs if spec.interaction_only)
    approval_specs = {spec.name: spec for spec in all_specs if not spec.interaction_only}
    wanted_source = (
        list(approval_specs)
        if approval_tools is None
        else [str(name).strip() for name in approval_tools if str(name).strip()]
    )
    wanted = resolve_approval_tools(wanted_source, approval_mode) or []
    # auto 模式：MCP / 应用注入（注册表里没有的工具）不进审批，只留注册表里的 HITL 工具
    auto_mcp = auto_approves_mcp(approval_mode)
    if auto_mcp:
        dropped = [name for name in wanted if name not in approval_specs]
        wanted = [name for name in wanted if name in approval_specs]
        if dropped:
            logger.info(
                "[%s] 允许自动审批：MCP / 应用工具不进审批 | %s", sid or "-", dropped
            )
    # 名单按注入名匹配，MCP 工具也认去掉 server 前缀的原始名
    injected: dict[str, list[str]] = {}
    for name in ordered_names:
        injected.setdefault(name, []).append(name)
        original = _mcp_original_name(tool_objects.get(name))
        if original and original != name:
            injected.setdefault(original, []).append(name)

    config: dict[str, bool | InterruptOnConfig] = {}

    # 产品交互（ask_user）与安全审批分开：即使 hitl.enabled=false，规划确认仍可用。
    for spec in interaction_specs:
        for name in injected.get(spec.name, []):
            config.setdefault(
                name,
                _interrupt_options(
                    name,
                    spec,
                    tool_objects.get(name),
                    predicate_map,
                    sid,
                ),
            )

    if not enable_hitl:
        return config or None

    for wanted_name in wanted:
        matches = [wanted_name] if wanted_name in builtin_names else injected.get(wanted_name, [])
        if not matches:
            logger.debug(
                "[%s] 审批名单里的工具 %s 未实际注入，跳过 interrupt_on",
                sid or "-",
                wanted_name,
            )
        for name in matches:
            if name not in config:
                config[name] = _interrupt_options(
                    name,
                    approval_specs.get(wanted_name),
                    tool_objects.get(name),
                    predicate_map,
                    sid,
                )

    # 名单外的 MCP / 动态工具兜底（DA-09）：服务端 annotations 优先，没有可用标注再看名字。
    # D4：MCP 工具可能带 server 前缀，名字启发式按原始名判断，避免前缀干扰。
    # auto 模式整段跳过：当前接入的 MCP 只提供查询能力，不再给它们加审批。
    fallback_names = () if auto_mcp else ordered_names
    for name in fallback_names:
        if name in config or name in builtin_names or get_tool_spec(name) is not None:
            continue
        tool_obj = tool_objects.get(name)
        heuristic_name = _mcp_original_name(tool_obj) or name
        by_name = looks_like_mutating_tool(heuristic_name)
        decision = mcp_annotation_decision(tool_obj)
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
                InterruptOnConfig(
                    allowed_decisions=mcp_interrupt_decisions(heuristic_name, tool_obj)
                ),
            )
    if auto_mcp and not config:
        # 全部审批项已放行：空配置 = 不安装 HumanInTheLoopMiddleware（enable_hitl 仍为 True）
        logger.info(
            "[%s] 允许自动审批：本轮没有需要审批的工具，不安装审批中间件", sid or "-"
        )
    return config
