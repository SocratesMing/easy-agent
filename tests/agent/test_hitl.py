"""FW-12：动态 HITL 工具名单（按实际注入工具构造 interrupt_on）。"""

from types import SimpleNamespace

from easy_agent.agent import EasyAgent
from easy_agent.agent.hitl import (
    AUTO_APPROVED_APPROVAL_TOOLS,
    DEFAULT_APPROVAL_TOOLS,
    EXECUTE_HITL_TOOL_DESCRIPTION,
    HITL_SYSTEM_NOTE,
    HITL_WHEN_PREDICATES,
    _is_destructive_command,
    _is_knowledge_materialize,
    build_interrupt_config,
    hitl_enabled,
    resolve_approval_tools,
)
from easy_agent.tools import registry
from easy_agent.tools.registry import SOURCE_EXPERT, ToolSpec


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _agent(mcp_tools=(), *, enable_hitl: bool = True) -> EasyAgent:
    """不跑 __init__ 的轻量实例：只验证 interrupt_on 构造。"""
    agent = EasyAgent.__new__(EasyAgent)
    agent.enable_hitl = enable_hitl
    agent.sid = "fw12-1"
    agent.mcp_tools = list(mcp_tools)
    return agent


def test_execute_description_mentions_sandbox():
    assert "沙箱" in EXECUTE_HITL_TOOL_DESCRIPTION
    assert "没有沙箱隔离" not in EXECUTE_HITL_TOOL_DESCRIPTION


def test_destructive_rm_detected():
    request = SimpleNamespace(tool_call={"args": {"command": "rm /workspace/a.txt"}})
    assert _is_destructive_command(request) is True


def test_hitl_note_and_delete_description_match_the_approval_policy():
    """目录删除只走 delete、由用户审批（审批说明要写到它）；execute 里的目录删除命令仍直接拒绝。"""
    from easy_agent.agent.hitl import (
        DELETE_HITL_TOOL_DESCRIPTION,
        EXECUTE_HITL_TOOL_DESCRIPTION,
        HITL_SYSTEM_NOTE,
    )

    assert HITL_SYSTEM_NOTE.startswith("## 人工审批")
    assert "删除文件或目录的 delete" in HITL_SYSTEM_NOTE
    assert "不要原样重试" in HITL_SYSTEM_NOTE
    assert "bwrap" not in HITL_SYSTEM_NOTE  # 沙箱实现写在 execute 描述里
    assert "删除文件或目录" in DELETE_HITL_TOOL_DESCRIPTION
    assert "「批准 / 拒绝」" in DELETE_HITL_TOOL_DESCRIPTION
    assert "需要删除目录时改用 delete 工具" in EXECUTE_HITL_TOOL_DESCRIPTION


def test_execute_directory_deletion_is_rejected_and_points_to_delete(tmp_path):
    """execute 删目录仍直接拒绝（不进审批），但要告诉模型改用 delete，而不是「目录受保护、删不了」。"""
    from easy_agent.backends.shell import PathTranslatingShell

    (tmp_path / "sub").mkdir()
    shell = PathTranslatingShell({"/workspace": str(tmp_path)}, root_dir=tmp_path, sandbox_enabled=False)

    result = shell.execute("rm -r /workspace/sub")

    assert result.exit_code == 1
    assert "delete 工具" in result.output
    assert (tmp_path / "sub").is_dir()


def test_register_execute_description_profiles_is_idempotent():
    from easy_agent.agent.hitl import (
        execute_description_installed,
        execute_description_profile_keys,
        register_execute_description_profiles,
    )

    config = SimpleNamespace(
        models={
            "hitl-agent": SimpleNamespace(
                protocol="openai", model="fw04-hitl-agent-model"
            )
        },
        llm=SimpleNamespace(protocol="openai", model="fw04-hitl-agent-model"),
    )

    first = register_execute_description_profiles(config)
    second = register_execute_description_profiles(config)

    assert first == second
    assert "openai:fw04-hitl-agent-model" in first
    assert execute_description_installed() is True
    assert "openai:fw04-hitl-agent-model" in execute_description_profile_keys()


# ── FW-12：纯函数 build_interrupt_config ─────────────────────────────────


def test_build_interrupt_config_only_adds_injected_registry_tools():
    """知识库/专家工具未注入时不得再出现在 interrupt_on（静默无效项）。"""
    cfg = build_interrupt_config(["mysql_execute_sql"])

    assert set(cfg) == {"execute", "delete", "mysql_execute_sql"}
    assert "mysql_execute_sql" not in build_interrupt_config([])
    assert "save_expert" not in cfg
    assert "create_scheduled_task" not in cfg
    assert "access_knowledge_original" not in cfg
    assert cfg["execute"]["when"] is _is_destructive_command
    # 删除类操作不给「编辑参数」，只留批准/拒绝
    assert cfg["execute"]["allowed_decisions"] == ["approve", "reject"]
    assert cfg["delete"]["allowed_decisions"] == ["approve", "reject"]
    assert cfg["mysql_execute_sql"] == {"allowed_decisions": ["approve", "edit", "reject"]}


def test_build_interrupt_config_includes_every_injected_hitl_tool():
    cfg = build_interrupt_config(
        [
            _tool("access_knowledge_original"),
            _tool("save_expert"),
            _tool("create_scheduled_task"),
        ]
    )

    assert set(cfg) == {
        "execute",
        "delete",
        "access_knowledge_original",
        "save_expert",
        "create_scheduled_task",
    }
    assert cfg["access_knowledge_original"]["when"] is _is_knowledge_materialize
    assert cfg["save_expert"]["allowed_decisions"] == ["approve", "edit", "reject"]
    assert cfg["create_scheduled_task"]["allowed_decisions"] == ["approve", "edit", "reject"]


def test_build_interrupt_config_preserves_registry_hitl_order():
    cfg = build_interrupt_config(
        [
            "access_knowledge_original",
            "create_scheduled_task",
            "save_expert",
            "mysql_execute_sql",
        ]
    )

    assert list(cfg) == [
        "execute",
        "delete",
        "save_expert",
        "create_scheduled_task",
        "access_knowledge_original",
        "mysql_execute_sql",
    ]


def test_build_interrupt_config_ignores_non_mutating_unknown_tools():
    cfg = build_interrupt_config(["weather_lookup", "read_file"])
    assert set(cfg) == {"execute", "delete"}


def test_read_uploaded_file_needs_approval_only_when_listed():
    """应用工具只看审批名单：read_uploaded_file 名字含 upload，以前被名字启发式误拦。"""
    assert "read_uploaded_file" not in build_interrupt_config(["read_uploaded_file"])
    cfg = build_interrupt_config(["read_uploaded_file"], approval_tools=["read_uploaded_file"])
    assert cfg == {"read_uploaded_file": {"allowed_decisions": ["approve", "edit", "reject"]}}


def test_build_interrupt_config_disabled_returns_none():
    assert build_interrupt_config(["save_expert"], enable_hitl=False) is None


def test_build_interrupt_config_unknown_predicate_falls_back_to_unconditional():
    spec = ToolSpec(
        name="odd_tool",
        source=SOURCE_EXPERT,
        is_hitl=True,
        interrupt_decisions=("approve",),
        hitl_conditional=True,
        hitl_predicate="missing_predicate",
    )

    cfg = build_interrupt_config(["odd_tool"], specs=[spec])

    assert cfg["odd_tool"]["allowed_decisions"] == ["approve"]
    assert "when" not in cfg["odd_tool"]


def test_hitl_when_predicates_cover_registry():
    assert set(HITL_WHEN_PREDICATES) == set(registry.hitl_predicate_names())


# ── EasyAgent 接入：注入集合变化 → 名单变化 ──────────────────────────────


def test_agent_interrupt_config_uses_injected_tool_objects():
    agent = _agent([_tool("save_expert"), _tool("mysql_execute_sql")])
    assert set(agent._build_interrupt_config()) == {
        "execute",
        "delete",
        "save_expert",
        "mysql_execute_sql",
    }


def test_agent_interrupt_config_empty_tool_set_only_execute():
    assert set(_agent()._build_interrupt_config()) == {"execute", "delete"}


def test_agent_interrupt_config_respects_enable_hitl():
    assert _agent(enable_hitl=False)._build_interrupt_config() is None


def test_agent_interrupt_config_changes_with_tool_set():
    base = set(_agent()._build_interrupt_config())
    with_creator = set(
        _agent([_tool("save_expert"), _tool("read_uploaded_file")])
        ._build_interrupt_config()
    )

    assert base == {"execute", "delete"}
    assert with_creator == {"execute", "delete", "save_expert"}


# ── config.yaml 的 hitl 段：总开关 + 审批名单 ───────────────────────────


def test_default_approval_list_is_every_registry_hitl_tool():
    assert DEFAULT_APPROVAL_TOOLS == (
        "execute",
        "delete",
        "save_expert",
        "create_scheduled_task",
        "access_knowledge_original",
    )


def test_configured_list_replaces_the_default():
    cfg = build_interrupt_config(
        [_tool("save_expert"), _tool("create_scheduled_task")], approval_tools=["save_expert"]
    )
    assert list(cfg) == ["save_expert"]


def test_listed_tools_keep_their_registry_conditions():
    cfg = build_interrupt_config(
        [_tool("access_knowledge_original")],
        approval_tools=["access_knowledge_original", "execute"],
    )
    assert list(cfg) == ["access_knowledge_original", "execute"]
    assert cfg["execute"]["when"] is _is_destructive_command
    assert cfg["access_knowledge_original"]["when"] is _is_knowledge_materialize


def test_listed_builtin_without_a_registry_rule_is_always_approved():
    cfg = build_interrupt_config([], approval_tools=["write_file", "delete"])
    assert cfg == {
        "write_file": {"allowed_decisions": ["approve", "edit", "reject"]},
        "delete": {"allowed_decisions": ["approve", "reject"]},
    }


def test_listed_names_that_are_not_injected_are_skipped():
    assert build_interrupt_config([], approval_tools=["save_expert", "no_such_tool"]) == {}


def test_agent_uses_the_configured_approval_list():
    agent = _agent([_tool("save_expert"), _tool("create_scheduled_task")])
    agent.config = SimpleNamespace(hitl=SimpleNamespace(tools=["create_scheduled_task"]))
    assert list(agent._build_interrupt_config()) == ["create_scheduled_task"]


def test_hitl_enabled_needs_both_the_caller_and_the_config():
    on = SimpleNamespace(hitl=SimpleNamespace(enabled=True))
    off = SimpleNamespace(hitl=SimpleNamespace(enabled=False))
    assert hitl_enabled(on) is True
    assert hitl_enabled(on, False) is False  # 定时任务
    assert hitl_enabled(off) is False
    assert hitl_enabled(SimpleNamespace()) is True  # 没有 hitl 段


# ── DA-09：MCP annotations 决定是否审批，名字启发式只做兜底 ──────────────


def _mcp_tool(name: str, **hints):
    """模拟 langchain-mcp-adapters 的工具：annotations 平铺在 metadata 里。"""
    from langchain_core.tools import StructuredTool

    annotations = {"title": None, "readOnlyHint": None, "destructiveHint": None,
                   "idempotentHint": None, "openWorldHint": None, **hints}
    return StructuredTool.from_function(
        func=lambda: "ok", name=name, description=f"{name} tool", metadata=annotations
    )


def test_read_only_annotation_skips_approval_despite_mutating_name():
    cfg = build_interrupt_config([_mcp_tool("update_cache_stats", readOnlyHint=True)])
    assert "update_cache_stats" not in cfg


def test_write_annotations_require_approval_despite_innocent_name():
    cfg = build_interrupt_config(
        [
            _mcp_tool("run_query", readOnlyHint=False),
            _mcp_tool("cleanup", destructiveHint=True),
            _mcp_tool("sync", readOnlyHint=True, destructiveHint=True),  # 自相矛盾时宁可多问
        ]
    )
    assert {"run_query", "cleanup", "sync"} <= set(cfg)


def test_unannotated_tools_fall_back_to_the_name_heuristic():
    cfg = build_interrupt_config([_mcp_tool("update_record"), _mcp_tool("list_records")])
    assert "update_record" in cfg
    assert "list_records" not in cfg


def test_delete_like_mcp_tools_have_no_edit_decision():
    """删除类工具（名字命中或 destructiveHint）只给批准/拒绝，不给改参数。"""
    cfg = build_interrupt_config(
        [
            _mcp_tool("delete_note"),
            _mcp_tool("drop_table"),
            _mcp_tool("cleanup", destructiveHint=True),
            _mcp_tool("update_record"),
        ]
    )

    assert cfg["delete_note"]["allowed_decisions"] == ["approve", "reject"]
    assert cfg["drop_table"]["allowed_decisions"] == ["approve", "reject"]
    assert cfg["cleanup"]["allowed_decisions"] == ["approve", "reject"]
    # 非删除类写操作仍可改参数
    assert cfg["update_record"]["allowed_decisions"] == ["approve", "edit", "reject"]


def test_delete_decision_check_ignores_innocent_words():
    """``\\brm\\b`` 带词边界，form / performance 这类词不应被判成删除工具。"""
    cfg = build_interrupt_config(
        [_mcp_tool("update_form"), _mcp_tool("update_performance")]
    )

    assert cfg["update_form"]["allowed_decisions"] == ["approve", "edit", "reject"]
    assert cfg["update_performance"]["allowed_decisions"] == [
        "approve",
        "edit",
        "reject",
    ]


def test_annotations_survive_the_mcp_evidence_wrapper():
    from easy_agent.services.mcp.evidence import wrap_mcp_tool

    wrapped = wrap_mcp_tool(_mcp_tool("delete_note", readOnlyHint=True), server="notes")
    assert "delete_note" not in build_interrupt_config([wrapped])


def test_listed_mcp_tool_matches_its_name_without_the_server_prefix():
    tool = _mcp_tool("crm__close_ticket", readOnlyHint=True)
    tool.metadata["mcp_original_name"] = "close_ticket"
    cfg = build_interrupt_config([tool], approval_tools=["close_ticket"])
    # 名单优先于服务端的只读标注
    assert cfg == {"crm__close_ticket": {"allowed_decisions": ["approve", "edit", "reject"]}}


def test_unlisted_mcp_tools_still_use_annotations_and_names():
    cfg = build_interrupt_config(
        [_mcp_tool("delete_note"), _mcp_tool("list_notes"), _tool("read_uploaded_file")],
        approval_tools=[],
    )
    assert list(cfg) == ["delete_note"]


# ── 用户级审批模式（设置 → 权限设置） ───────────────────────────────────


def test_auto_mode_approves_every_approval_item():
    """auto 把 5 个审批项全部放行（含知识库原文落盘），MCP 写工具也不进审批。"""
    agent = _agent(
        [
            _tool("save_expert"),
            _tool("create_scheduled_task"),
            _tool("access_knowledge_original"),
            _mcp_tool("mysql_execute_sql"),
        ]
    )

    agent.approval_mode = "manual"
    assert set(agent._build_interrupt_config()) == {
        "execute",
        "delete",
        "save_expert",
        "create_scheduled_task",
        "access_knowledge_original",
        "mysql_execute_sql",
    }

    agent.approval_mode = "auto"
    # 空字典 = 不安装 HumanInTheLoopMiddleware，本轮没有任何审批节点
    assert agent._build_interrupt_config() == {}


def test_auto_mode_skips_the_mcp_fallback_entirely(monkeypatch):
    """auto 模式连 MCP 兜底都不走：名字含 execute_sql / destructiveHint 也不进审批。"""
    agent = _agent(
        [
            _mcp_tool("mysql_execute_sql"),
            _mcp_tool("drop_table", destructiveHint=True),
            _mcp_tool("delete_row"),
        ]
    )
    agent.approval_mode = "auto"
    assert agent._build_interrupt_config() == {}

    # 回滚开关：把 AUTO_APPROVES_MCP_TOOLS 改回 False 即恢复 MCP 审批
    from easy_agent.agent import hitl

    monkeypatch.setattr(hitl, "AUTO_APPROVES_MCP_TOOLS", False)
    assert set(agent._build_interrupt_config()) == {
        "mysql_execute_sql",
        "drop_table",
        "delete_row",
    }


def test_manual_mode_keeps_the_mcp_fallback():
    """manual 是安全默认值：MCP 写工具照旧按 annotations / 名字进审批。"""
    agent = _agent([_mcp_tool("mysql_execute_sql"), _mcp_tool("list_tables", readOnlyHint=True)])
    agent.approval_mode = "manual"
    assert set(agent._build_interrupt_config()) == {
        "execute",
        "delete",
        "mysql_execute_sql",
    }


def test_resolve_approval_tools_falls_back_to_manual_on_illegal_value():
    assert resolve_approval_tools(None, "manual") is None
    assert resolve_approval_tools(None, "yolo") is None
    assert resolve_approval_tools(["delete", "execute"], "manual") == ["delete", "execute"]


def test_resolve_approval_tools_removes_exactly_the_auto_approved_names():
    auto = resolve_approval_tools(None, "auto")
    assert set(DEFAULT_APPROVAL_TOOLS) - set(auto) == AUTO_APPROVED_APPROVAL_TOOLS
    # 默认名单已被全部覆盖：auto 不剩任何审批项
    assert auto == []
    assert set(DEFAULT_APPROVAL_TOOLS) <= AUTO_APPROVED_APPROVAL_TOOLS


def test_auto_approved_names_are_all_real_approval_items():
    """放行名单里写错名字会静默失效（摘不到就永远不减），这里把拼写钉死。"""
    for name in AUTO_APPROVED_APPROVAL_TOOLS:
        assert name in DEFAULT_APPROVAL_TOOLS, f"{name} 不在审批名单里，放行它是空操作"


def test_auto_mode_swaps_the_hitl_system_note():
    """工具描述是按模型全局注册的，auto 下只能靠提示词覆盖「会先暂停等批准」的说法。"""
    agent = _agent()
    agent.approval_mode = "manual"
    assert agent._hitl_system_note() == HITL_SYSTEM_NOTE

    agent.approval_mode = "auto"
    note = agent._hitl_system_note()
    assert note != HITL_SYSTEM_NOTE
    assert "自动审批" in note and "直接执行" in note

    agent.enable_hitl = False
    assert agent._hitl_system_note() == ""
