"""Agent 工具注册表：后端工具元数据的单一来源（D1）。

本模块只负责两件事：

1. **声明**：每个工具的 ``name`` / ``source`` / 是否内置 / 是否写操作 /
   是否 HITL / 审批决策，集中在一处（``TOOL_REGISTRY``）；
2. **装配**：按既有条件与顺序返回会话工具列表（``build_session_tools``），
   使 ``services/agent_manager.py`` 不再逐个工具写 ``if/append``。

约束（与改造前逐项等价）：

- ``hitl.DEFAULT_INTERRUPT_TOOLS`` 由 :func:`default_interrupt_tool_names` 派生，
  内容与顺序保持 ``("save_expert", "create_scheduled_task")``；
- 注入顺序固定为 MCP 基底 → ``access_knowledge_original`` →
  ``create_scheduled_task`` → ``save_expert`` → ``read_uploaded_file`` →
  ``web_search``，条件与原 ``agent_manager`` 中的 ``if`` 一致；
- 带 ``when`` 谓词的条件 HITL（``execute`` / ``access_knowledge_original``）的
  决策与谓词名同样登记在表内，``agent.py`` 按注册表元数据构造 ``interrupt_on``；
  谓词名到函数的绑定留在 ``agent.py``，注册表保持无包内 import 的叶子模块；
- MCP 工具由 ``services/mcp.py`` 运行时加载，本模块不静态枚举；未登记的名字
  一律视为 ``mcp``（见 :func:`resolve_tool_source`）。

设计文档：``docs/design/2026-10-18-d1-tool-registry.md``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping

logger = logging.getLogger("easy-agent.chat_service")

# ── 来源枚举 ─────────────────────────────────────────────────────────────
SOURCE_BUILTIN = "builtin"
SOURCE_KNOWLEDGE = "knowledge"
SOURCE_EXPERT = "expert"
SOURCE_SCHEDULING = "scheduling"
SOURCE_FILE_CONTEXT = "file_context"
SOURCE_WEB_SEARCH = "web_search"
SOURCE_MCP = "mcp"

KNOWN_SOURCES = frozenset(
    {
        SOURCE_BUILTIN,
        SOURCE_KNOWLEDGE,
        SOURCE_EXPERT,
        SOURCE_SCHEDULING,
        SOURCE_FILE_CONTEXT,
        SOURCE_WEB_SEARCH,
        SOURCE_MCP,
    }
)

#: ``interrupt_on`` 允许的决策（与 ``agent.py`` 现有配置一致）。
ALLOWED_INTERRUPT_DECISIONS = ("approve", "reject")


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的元数据声明（不可变，见设计文档 §2）。

    ``builder`` / ``condition`` 用 ``compare=False`` 排除在相等性之外：
    它们只影响装配行为，不影响元数据比较（测试可用 ``dataclasses.replace`` 替换）。
    """

    name: str
    source: str
    description: str = ""
    mutating: bool = False
    is_hitl: bool = False
    interrupt_decisions: tuple[str, ...] = ()
    hitl_conditional: bool = False
    #: ``when`` 谓词的注册名（如 ``is_destructive_command``）。仅条件 HITL 使用；
    #: 由 ``agent.py`` 绑定到实际函数——注册表不 import 谓词实现（保持叶子模块）。
    hitl_predicate: str = ""
    #: HITL 触发条件的可读说明（文档/排查用，不参与判定）。
    hitl_trigger_note: str = ""
    hitl_order: int = 100
    inject_order: int | None = None
    condition_note: str = ""
    builder: Callable[["ToolBuildContext"], Any] | None = field(
        default=None, repr=False, compare=False
    )
    condition: Callable[["ToolBuildContext"], bool] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("ToolSpec.name 不能为空")
        if self.source not in KNOWN_SOURCES:
            raise ValueError(f"未知工具来源: {self.source!r}（工具 {self.name!r}）")
        if self.is_hitl:
            if not self.interrupt_decisions:
                raise ValueError(f"HITL 工具 {self.name!r} 必须声明 interrupt_decisions")
            invalid = [
                d for d in self.interrupt_decisions
                if d not in ALLOWED_INTERRUPT_DECISIONS
            ]
            if invalid:
                raise ValueError(
                    f"HITL 工具 {self.name!r} 含非法决策 {invalid!r}，"
                    f"允许值: {ALLOWED_INTERRUPT_DECISIONS!r}"
                )
        elif self.interrupt_decisions:
            raise ValueError(f"非 HITL 工具 {self.name!r} 不应声明 interrupt_decisions")
        if self.hitl_conditional and not self.is_hitl:
            raise ValueError(f"hitl_conditional 仅对 HITL 工具有意义: {self.name!r}")
        if self.hitl_conditional and not self.hitl_predicate:
            raise ValueError(
                f"条件 HITL 工具 {self.name!r} 必须声明 hitl_predicate（when 谓词名）"
            )
        if self.hitl_predicate and not self.hitl_conditional:
            raise ValueError(
                f"hitl_predicate 仅对条件 HITL 有效: {self.name!r}"
            )
        if self.inject_order is not None and self.builder is None:
            raise ValueError(f"可注入工具 {self.name!r} 缺少 builder")
        if self.builder is not None and self.inject_order is None:
            raise ValueError(
                f"内置/非装配工具 {self.name!r} 不应声明 builder（由框架提供）"
            )

    @property
    def is_builtin(self) -> bool:
        """是否 DeepAgents 内置工具（不参与 ``build_session_tools`` 装配）。"""
        return self.source == SOURCE_BUILTIN

    @property
    def is_injectable(self) -> bool:
        """是否由本注册表装配注入。"""
        return self.inject_order is not None


@dataclass(frozen=True)
class ToolBuildContext:
    """装配会话工具时的只读上下文（字段均来自 ``agent_manager`` 现状）。"""

    config: Any
    username: str = "default"
    session_id: str = ""
    workspace_name: str = ""
    enable_hitl: bool = True
    model_name: str = ""
    creator_requested: bool | None = None
    web_search_tool: Any = None
    edit_targets: Mapping[str, str] | None = None
    # 工具工厂（按工具名索引），由装配根 ``agent_manager._tool_factories()`` 提供。
    # 注册表刻意不 import tools/knowledge 模块，保持包内无 import 边的叶子模块，
    # 避免把 tools.registry 并进既有 knowledge→scheduler→agent_manager SCC。
    factories: Mapping[str, Callable[..., Any]] | None = None

    @property
    def sid(self) -> str:
        """日志用短会话 id（与 agent_manager 现有 ``session_id[-5:]`` 口径一致）。"""
        return (self.session_id or "new")[-5:]


# ── 注入条件（与改造前 agent_manager 中的 if 一一对应） ──────────────────


def _scheduled_task_enabled(ctx: ToolBuildContext) -> bool:
    return bool(ctx.enable_hitl)


def _save_expert_enabled(ctx: ToolBuildContext) -> bool:
    # None = 未传 skill_dirs / HITL 恢复，跟随缓存，保持注入（改造前行为）。
    return ctx.creator_requested is None or bool(ctx.creator_requested)


def _file_context_enabled(ctx: ToolBuildContext) -> bool:
    file_context = getattr(ctx.config, "file_context", None)
    return file_context is not None and bool(getattr(file_context, "enabled", True))


def _web_search_enabled(ctx: ToolBuildContext) -> bool:
    return ctx.web_search_tool is not None


# ── 工具工厂（由装配根注入，注册表保持包内无 import 边） ─────────────────
#
# 这里不 ``import`` 任何 tools/knowledge 模块：``knowledge_impl`` 与
# ``tools.scheduled_task`` 已在既有的 knowledge→scheduler→agent_manager SCC
# （白名单项 knowledge-chat-scheduler-core）里，注册表一旦出现包内 import 边，
# AST 依赖门禁就会把 registry/hitl/agent 并进该 SCC，与白名单精确匹配冲突。
# 因此工具工厂由 ``agent_manager._tool_factories()`` 在装配处提供，
# 注册表只按名字取用（运行期依赖仍在装配根，见设计文档 §2.3 / §4）。


def _get_factory(ctx: ToolBuildContext, name: str) -> Callable[..., Any]:
    factories = ctx.factories or {}
    factory = factories.get(name)
    if factory is None:
        raise LookupError(
            f"工具工厂未提供: {name!r}（应由 agent_manager._tool_factories 注入）"
        )
    return factory


def _build_knowledge_tools(ctx: ToolBuildContext) -> list:
    # 与原实现一致：extend_agent_tools 内部按 KnowledgeConfig.enabled 决定是否
    # 返回工具，并自行捕获异常；这里以空列表为基底，只取它追加的条目。
    extender = _get_factory(ctx, "access_knowledge_original")
    return extender(
        [],
        config=ctx.config,
        username=ctx.username,
        session_id=ctx.session_id,
        workspace_name=ctx.workspace_name,
    )


def _build_scheduled_task_tool(ctx: ToolBuildContext) -> Any:
    factory = _get_factory(ctx, "create_scheduled_task")
    return factory(username=ctx.username, session_id=ctx.session_id or "")


def _build_save_expert_tool(ctx: ToolBuildContext) -> Any:
    factory = _get_factory(ctx, "save_expert")
    tool = factory(
        username=ctx.username,
        session_id=ctx.session_id or "",
        config_model=ctx.model_name or "",
        tag_options=getattr(ctx.config, "expert_tags", None),
        edit_targets=ctx.edit_targets or {},
    )
    logger.info(f"[{ctx.sid}] 🧑‍🔬 已注入 save_expert 工具")
    return tool


def _build_read_uploaded_file_tool(ctx: ToolBuildContext) -> Any:
    factory = _get_factory(ctx, "read_uploaded_file")
    tool = factory(username=ctx.username, session_id=ctx.session_id or "")
    logger.info(f"[{ctx.sid}] 📎 已注入 read_uploaded_file 工具")
    return tool


def _build_web_search_tool(ctx: ToolBuildContext) -> Any:
    if ctx.web_search_tool is None:
        return None
    logger.info(f"[{ctx.sid}] 🌐 联网搜索已开启（注入 web_search 工具）")
    return ctx.web_search_tool


# ── 注册表本体 ───────────────────────────────────────────────────────────
#
# 声明顺序 = 阅读顺序；实际注入顺序由 inject_order 决定，
# HITL 名单顺序由 hitl_order 决定（保证 DEFAULT_INTERRUPT_TOOLS 逐字不变）。

TOOL_REGISTRY: tuple[ToolSpec, ...] = (
    # DeepAgents 内置工具（框架自动提供，inject_order=None）
    ToolSpec(
        name="ls",
        source=SOURCE_BUILTIN,
        description="列出目录内容（DeepAgents 内置）",
    ),
    ToolSpec(
        name="read_file",
        source=SOURCE_BUILTIN,
        description="读取文件（DeepAgents 内置）",
    ),
    ToolSpec(
        name="write_file",
        source=SOURCE_BUILTIN,
        description="写入文件（DeepAgents 内置；是否进 HITL 为独立产品决策）",
        mutating=True,
    ),
    ToolSpec(
        name="edit_file",
        source=SOURCE_BUILTIN,
        description="编辑文件（DeepAgents 内置；是否进 HITL 为独立产品决策）",
        mutating=True,
    ),
    ToolSpec(
        name="glob",
        source=SOURCE_BUILTIN,
        description="按 glob 匹配路径（DeepAgents 内置）",
    ),
    ToolSpec(
        name="grep",
        source=SOURCE_BUILTIN,
        description="按正则检索文件内容（DeepAgents 内置）",
    ),
    ToolSpec(
        name="execute",
        source=SOURCE_BUILTIN,
        description="沙箱内执行 Shell 命令（DeepAgents 内置）",
        mutating=True,
        is_hitl=True,
        interrupt_decisions=("approve", "reject"),
        # 删除类命令才进审批；目录删除直接拒绝，不进入审批。
        hitl_conditional=True,
        hitl_predicate="is_destructive_command",
        hitl_trigger_note=(
            "command 命中文件删除类模式（rm/unlink/shred 等）；目录删除直接拒绝"
        ),
        hitl_order=0,
    ),
    ToolSpec(
        name="write_todos",
        source=SOURCE_BUILTIN,
        description="任务规划清单（DeepAgents 内置）",
    ),
    ToolSpec(
        name="task",
        source=SOURCE_BUILTIN,
        description="派生子代理（DeepAgents 内置）",
    ),
    # 应用注入工具（顺序 10/20/30/40/50，与改造前 if/append 顺序一致）
    ToolSpec(
        name="access_knowledge_original",
        source=SOURCE_KNOWLEDGE,
        description="按权限访问知识库原文（list/metadata/materialize）",
        mutating=True,
        is_hitl=True,
        interrupt_decisions=("approve", "reject"),
        # 仅 action=materialize 触发审批（list/metadata 只读，无需审批）。
        hitl_conditional=True,
        hitl_predicate="is_knowledge_materialize",
        hitl_trigger_note="args.action == 'materialize'",
        hitl_order=30,
        inject_order=10,
        condition_note="总是尝试；KnowledgeConfig.enabled 由 extend_agent_tools 内部判定",
        builder=_build_knowledge_tools,
    ),
    ToolSpec(
        name="create_scheduled_task",
        source=SOURCE_SCHEDULING,
        description="创建 cron 定时任务",
        mutating=True,
        is_hitl=True,
        interrupt_decisions=("approve", "reject"),
        hitl_order=20,
        inject_order=20,
        condition_note="enable_hitl=True",
        condition=_scheduled_task_enabled,
        builder=_build_scheduled_task_tool,
    ),
    ToolSpec(
        name="save_expert",
        source=SOURCE_EXPERT,
        description="在对话内新建/修改专家配置",
        mutating=True,
        is_hitl=True,
        interrupt_decisions=("approve", "reject"),
        hitl_order=10,
        inject_order=30,
        condition_note="skill_dirs 含 expert-creator（None=跟随缓存，保持注入）",
        condition=_save_expert_enabled,
        builder=_build_save_expert_tool,
    ),
    ToolSpec(
        name="read_uploaded_file",
        source=SOURCE_FILE_CONTEXT,
        description=(
            "按需读取/重解析用户上传文件正文（会回填 uploads/ 正文与清单）；"
            "名字含 upload，历史 HITL 名字启发式会将其纳入审批（FW-12 保持等价）"
        ),
        mutating=True,
        inject_order=40,
        condition_note="config.file_context.enabled（缺省开启）",
        condition=_file_context_enabled,
        builder=_build_read_uploaded_file_tool,
    ),
    ToolSpec(
        name="web_search",
        source=SOURCE_WEB_SEARCH,
        description="联网搜索（需配置 api_url/api_key）",
        inject_order=50,
        condition_note="本轮开启且 create_web_search_tool 返回工具",
        condition=_web_search_enabled,
        builder=_build_web_search_tool,
    ),
)


def _validate_registry(specs: Iterable[ToolSpec]) -> None:
    """导入期校验：名字唯一、注入顺序唯一（顺序冲突会导致装配不确定）。"""
    names: set[str] = set()
    inject_orders: dict[int, str] = {}
    for spec in specs:
        if spec.name in names:
            raise ValueError(f"工具注册表名字重复: {spec.name!r}")
        names.add(spec.name)
        if spec.inject_order is not None:
            previous = inject_orders.get(spec.inject_order)
            if previous is not None:
                raise ValueError(
                    f"工具注册表 inject_order 冲突: {previous!r} 与 {spec.name!r} "
                    f"都是 {spec.inject_order}"
                )
            inject_orders[spec.inject_order] = spec.name


_validate_registry(TOOL_REGISTRY)


# ── 查询函数 ─────────────────────────────────────────────────────────────


def iter_tool_specs() -> Iterator[ToolSpec]:
    """按声明顺序迭代全部注册条目。"""
    return iter(TOOL_REGISTRY)


def tool_names() -> tuple[str, ...]:
    """全部已登记工具名（不含动态 MCP 工具）。"""
    return tuple(spec.name for spec in TOOL_REGISTRY)


def get_tool_spec(name: str) -> ToolSpec | None:
    """按名查询；未登记（如 MCP 动态工具）返回 ``None``。"""
    for spec in TOOL_REGISTRY:
        if spec.name == name:
            return spec
    return None


def require_tool_spec(name: str) -> ToolSpec:
    """按名查询；未登记抛 ``KeyError``（需要强校验的调用方用）。"""
    spec = get_tool_spec(name)
    if spec is None:
        raise KeyError(name)
    return spec


def builtin_tool_names() -> tuple[str, ...]:
    """DeepAgents 内置工具名单（框架自动提供，不装配）。"""
    return tuple(spec.name for spec in TOOL_REGISTRY if spec.is_builtin)


def hitl_tool_specs() -> tuple[ToolSpec, ...]:
    """全部 HITL 工具（含条件 HITL），按 ``hitl_order`` 排序。

    该顺序即 ``agent.py`` 构造 ``interrupt_on`` 的顺序：``execute`` →
    ``save_expert`` → ``create_scheduled_task`` → ``access_knowledge_original``。
    """
    return tuple(
        sorted(
            (spec for spec in TOOL_REGISTRY if spec.is_hitl),
            key=lambda spec: (spec.hitl_order, spec.name),
        )
    )


def default_interrupt_tool_names() -> tuple[str, ...]:
    """派生 ``hitl.DEFAULT_INTERRUPT_TOOLS``。

    只包含**无条件** HITL 工具；``execute`` / ``access_knowledge_original`` 带
    ``when`` 谓词，由 ``agent.py`` 按注册表的 ``hitl_predicate`` 元数据构造，
    不在此列表内。
    """
    return tuple(
        spec.name
        for spec in hitl_tool_specs()
        if not spec.hitl_conditional
    )


def hitl_predicate_names() -> tuple[str, ...]:
    """条件 HITL 用到的全部 ``when`` 谓词注册名（去重，保持声明顺序）。

    供 ``agent.py`` 绑定实际函数、测试校验「注册表声明的谓词都已绑定」。
    """
    return tuple(
        dict.fromkeys(spec.hitl_predicate for spec in TOOL_REGISTRY if spec.hitl_predicate)
    )


def is_hitl_tool(name: str) -> bool:
    spec = get_tool_spec(name)
    return bool(spec and spec.is_hitl)


def is_mutating_tool(name: str) -> bool:
    spec = get_tool_spec(name)
    return bool(spec and spec.mutating)


def name_heuristic_exempt_tools() -> frozenset[str]:
    """``looks_like_mutating_tool`` 的豁免集合（名字启发式不适用）。

    与改造前手写名单等价：已单独配置 HITL 的工具（``execute`` / ``save_expert`` /
    ``create_scheduled_task`` / ``access_knowledge_original``）与只读内置工具
    （``ls`` / ``read_file`` / ``glob`` / ``grep`` / ``write_todos`` / ``task``）。
    """
    return frozenset(
        spec.name
        for spec in TOOL_REGISTRY
        if spec.is_hitl or (spec.is_builtin and not spec.mutating)
    )


def resolve_tool_source(name: str) -> str:
    """已登记返回其 ``source``；未登记名字视为 ``mcp``（动态加载，不静态枚举）。"""
    spec = get_tool_spec(name)
    return spec.source if spec is not None else SOURCE_MCP


def ordered_injectable_specs() -> tuple[ToolSpec, ...]:
    """按 ``inject_order`` 升序返回可注入条目。"""
    return tuple(
        sorted(
            (spec for spec in TOOL_REGISTRY if spec.is_injectable),
            key=lambda spec: int(spec.inject_order or 0),
        )
    )


def build_session_tools(
    base_tools: Iterable[Any] | None,
    ctx: ToolBuildContext,
    *,
    specs: Iterable[ToolSpec] | None = None,
) -> list:
    """按注册表装配会话工具列表。

    Args:
        base_tools: 基底工具（当前为 MCP 工具），原样保留在最前。
        ctx: 装配上下文（用户/会话/条件开关）。
        specs: 仅测试用；默认使用 ``TOOL_REGISTRY`` 的可注入条目。

    Returns:
        新列表：``base_tools`` + 满足条件且构造成功的工具（顺序由 ``inject_order`` 决定）。
        单个条目的构造异常只告警并跳过，不影响其余工具。
    """
    tools = list(base_tools or ())
    ordered = ordered_injectable_specs() if specs is None else tuple(
        sorted(
            (spec for spec in specs if spec.is_injectable),
            key=lambda spec: int(spec.inject_order or 0),
        )
    )
    for spec in ordered:
        if spec.condition is not None and not spec.condition(ctx):
            continue
        try:
            built = spec.builder(ctx) if spec.builder is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{ctx.sid}] 注入 {spec.name} 工具失败: {exc}")
            continue
        if built is None:
            continue
        if isinstance(built, (list, tuple)):
            tools.extend(built)
        else:
            tools.append(built)
    return tools


__all__ = [
    "ALLOWED_INTERRUPT_DECISIONS",
    "KNOWN_SOURCES",
    "SOURCE_BUILTIN",
    "SOURCE_EXPERT",
    "SOURCE_FILE_CONTEXT",
    "SOURCE_KNOWLEDGE",
    "SOURCE_MCP",
    "SOURCE_SCHEDULING",
    "SOURCE_WEB_SEARCH",
    "TOOL_REGISTRY",
    "ToolBuildContext",
    "ToolSpec",
    "build_session_tools",
    "builtin_tool_names",
    "default_interrupt_tool_names",
    "get_tool_spec",
    "hitl_predicate_names",
    "hitl_tool_specs",
    "is_hitl_tool",
    "is_mutating_tool",
    "iter_tool_specs",
    "name_heuristic_exempt_tools",
    "ordered_injectable_specs",
    "require_tool_spec",
    "resolve_tool_source",
    "tool_names",
]
