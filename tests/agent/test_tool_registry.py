"""D1 工具注册表：元数据单一来源与装配等价性（不依赖 LLM/网络/DB）。"""

from types import SimpleNamespace

import pytest

from easy_agent.hitl import DEFAULT_INTERRUPT_TOOLS, looks_like_mutating_tool
from easy_agent.tools import registry
from easy_agent.tools.expert_writer import create_save_expert_tool
from easy_agent.tools.registry import (
    SOURCE_BUILTIN,
    SOURCE_EXPERT,
    SOURCE_MCP,
    TOOL_REGISTRY,
    ToolBuildContext,
    ToolSpec,
    build_session_tools,
)
from easy_agent.tools.scheduled_task import CreateScheduledTaskTool
from easy_agent.tools.session_file_reader import ReadUploadedFileTool


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _factories():
    def fake_knowledge(tools, *, config, username, session_id, workspace_name):
        return [*tools, _FakeTool("access_knowledge_original")]

    return {
        "access_knowledge_original": fake_knowledge,
        "create_scheduled_task": CreateScheduledTaskTool,
        "save_expert": create_save_expert_tool,
        "read_uploaded_file": ReadUploadedFileTool,
    }


def _ctx(**overrides) -> ToolBuildContext:
    base = {
        "config": SimpleNamespace(file_context=SimpleNamespace(enabled=True)),
        "username": "u",
        "session_id": "session-12345",
        "workspace_name": "ws",
        "enable_hitl": True,
        "model_name": "m",
        "creator_requested": None,
        "web_search_tool": None,
        "edit_targets": {},
        "factories": _factories(),
    }
    base.update(overrides)
    return ToolBuildContext(**base)


def _fake_spec(name, order, builder, condition=None) -> ToolSpec:
    return ToolSpec(
        name=name,
        source=SOURCE_EXPERT,
        inject_order=order,
        builder=builder,
        condition=condition,
    )


# ── 元数据校验 ───────────────────────────────────────────────────────────


def test_entries_have_required_fields_and_unique_names():
    names = [spec.name for spec in TOOL_REGISTRY]
    assert len(names) == len(set(names))
    for spec in TOOL_REGISTRY:
        assert spec.name and spec.name == spec.name.strip()
        assert spec.source in registry.KNOWN_SOURCES
        assert isinstance(spec.description, str)
        if spec.is_hitl:
            assert spec.interrupt_decisions
            assert set(spec.interrupt_decisions) <= {"approve", "reject"}
        else:
            assert spec.interrupt_decisions == ()


def test_builtin_tools_cover_deepagents_set():
    assert set(registry.builtin_tool_names()) == {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "write_todos",
        "task",
    }
    for spec in TOOL_REGISTRY:
        assert spec.is_builtin is (spec.source == SOURCE_BUILTIN)
        assert spec.is_injectable is (spec.inject_order is not None)


def test_spec_validation_rejects_invalid_entries():
    with pytest.raises(ValueError):
        ToolSpec(name="", source=SOURCE_BUILTIN)
    with pytest.raises(ValueError):
        ToolSpec(name="x", source="unknown-source")
    with pytest.raises(ValueError):
        ToolSpec(name="x", source=SOURCE_EXPERT, is_hitl=True)
    with pytest.raises(ValueError):
        ToolSpec(name="x", source=SOURCE_EXPERT, interrupt_decisions=("approve",))
    with pytest.raises(ValueError):
        ToolSpec(name="x", source=SOURCE_EXPERT, hitl_conditional=True)
    with pytest.raises(ValueError):
        ToolSpec(name="x", source=SOURCE_EXPERT, inject_order=10)
    with pytest.raises(ValueError):
        ToolSpec(name="x", source=SOURCE_EXPERT, builder=lambda ctx: None)
    with pytest.raises(ValueError):
        ToolSpec(
            name="x",
            source=SOURCE_EXPERT,
            is_hitl=True,
            interrupt_decisions=("maybe",),
        )


# ── HITL 名单 ────────────────────────────────────────────────────────────


def test_default_interrupt_list_matches_hitl_module():
    assert DEFAULT_INTERRUPT_TOOLS == registry.default_interrupt_tool_names()
    assert DEFAULT_INTERRUPT_TOOLS == ("save_expert", "create_scheduled_task")
    for name in DEFAULT_INTERRUPT_TOOLS:
        spec = registry.require_tool_spec(name)
        assert spec.is_hitl
        assert spec.interrupt_decisions == ("approve", "reject")
        assert not spec.hitl_conditional


def test_conditional_hitl_tools_are_not_in_default_list():
    conditional = {spec.name for spec in TOOL_REGISTRY if spec.hitl_conditional}
    assert conditional == {"execute", "access_knowledge_original"}
    assert not conditional & set(registry.default_interrupt_tool_names())


def test_name_heuristic_exemptions_match_registry():
    exempt = registry.name_heuristic_exempt_tools()
    legacy = {
        "execute",
        "save_expert",
        "create_scheduled_task",
        "access_knowledge_original",
        "write_todos",
        "read_file",
        "ls",
        "glob",
        "grep",
    }
    assert legacy <= exempt
    for name in exempt:
        assert looks_like_mutating_tool(name) is False
    assert looks_like_mutating_tool("mysql_execute_sql") is True
    assert looks_like_mutating_tool("") is False


# ── 注入顺序与条件 ───────────────────────────────────────────────────────


def test_inject_order_matches_legacy_sequence():
    specs = registry.ordered_injectable_specs()
    assert [spec.name for spec in specs] == [
        "access_knowledge_original",
        "create_scheduled_task",
        "save_expert",
        "read_uploaded_file",
        "web_search",
    ]
    orders = [int(spec.inject_order or 0) for spec in specs]
    assert orders == sorted(orders)


def test_injection_conditions_follow_legacy_if_statements():
    specs = {spec.name: spec for spec in registry.ordered_injectable_specs()}

    scheduled = specs["create_scheduled_task"].condition
    assert scheduled(_ctx(enable_hitl=True)) is True
    assert scheduled(_ctx(enable_hitl=False)) is False

    save_expert = specs["save_expert"].condition
    assert save_expert(_ctx(creator_requested=None)) is True
    assert save_expert(_ctx(creator_requested=True)) is True
    assert save_expert(_ctx(creator_requested=False)) is False

    read_uploaded = specs["read_uploaded_file"].condition
    assert read_uploaded(_ctx()) is True
    assert read_uploaded(
        _ctx(config=SimpleNamespace(file_context=SimpleNamespace(enabled=False)))
    ) is False
    assert read_uploaded(_ctx(config=SimpleNamespace())) is False

    web_search = specs["web_search"].condition
    assert web_search(_ctx()) is False
    assert web_search(_ctx(web_search_tool=_FakeTool("web_search"))) is True


# ── 装配行为 ─────────────────────────────────────────────────────────────


def test_build_session_tools_keeps_base_and_applies_order_and_shapes():
    specs = [
        _fake_spec("late", 20, lambda ctx: _FakeTool("late")),
        _fake_spec("early", 10, lambda ctx: [_FakeTool("early-1"), _FakeTool("early-2")]),
        _fake_spec("none", 30, lambda ctx: None),
    ]
    tools = build_session_tools([_FakeTool("mcp")], _ctx(), specs=specs)
    assert [tool.name for tool in tools] == ["mcp", "early-1", "early-2", "late"]


def test_build_session_tools_skips_false_conditions_and_builder_errors():
    def _boom(ctx):
        raise RuntimeError("builder failed")

    specs = [
        _fake_spec("skip", 10, lambda ctx: _FakeTool("skip"), condition=lambda ctx: False),
        _fake_spec("boom", 20, _boom),
        _fake_spec("ok", 30, lambda ctx: _FakeTool("ok")),
    ]
    tools = build_session_tools([], _ctx(), specs=specs)
    assert [tool.name for tool in tools] == ["ok"]


def test_real_builders_produce_expected_tool_names():
    tools = build_session_tools(
        [_FakeTool("mcp_a")],
        _ctx(web_search_tool=_FakeTool("web_search")),
    )
    assert [tool.name for tool in tools] == [
        "mcp_a",
        "access_knowledge_original",
        "create_scheduled_task",
        "save_expert",
        "read_uploaded_file",
        "web_search",
    ]


def test_optional_tools_absent_when_conditions_are_off():
    ctx = _ctx(
        enable_hitl=False,
        creator_requested=False,
        config=SimpleNamespace(file_context=SimpleNamespace(enabled=False)),
        factories={
            **_factories(),
            "access_knowledge_original": lambda tools, **kwargs: list(tools),
        },
    )
    tools = build_session_tools([_FakeTool("mcp")], ctx)
    assert [tool.name for tool in tools] == ["mcp"]


# ── 查询函数 ─────────────────────────────────────────────────────────────


def test_source_resolution_and_queries():
    assert registry.resolve_tool_source("ls") == SOURCE_BUILTIN
    assert registry.resolve_tool_source("save_expert") == "expert"
    assert registry.resolve_tool_source("not_registered_mcp") == SOURCE_MCP

    assert registry.get_tool_spec("not_registered_mcp") is None
    with pytest.raises(KeyError):
        registry.require_tool_spec("not_registered_mcp")

    assert registry.is_hitl_tool("execute") is True
    assert registry.is_hitl_tool("ls") is False
    assert registry.is_mutating_tool("write_file") is True
    assert registry.is_mutating_tool("read_file") is False

    assert list(registry.iter_tool_specs()) == list(TOOL_REGISTRY)
    assert registry.tool_names() == tuple(spec.name for spec in TOOL_REGISTRY)
