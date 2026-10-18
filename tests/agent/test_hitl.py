"""FW-12：动态 HITL 工具名单（按实际注入工具构造 interrupt_on）。"""

from types import SimpleNamespace

from easy_agent.agent import EasyAgent
from easy_agent.hitl import (
    EXECUTE_HITL_TOOL_DESCRIPTION,
    HITL_WHEN_PREDICATES,
    _is_destructive_command,
    _is_knowledge_materialize,
    build_interrupt_config,
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


def test_register_execute_description_profiles_is_idempotent():
    from easy_agent.hitl import (
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

    assert set(cfg) == {"execute", "mysql_execute_sql"}
    assert "mysql_execute_sql" not in build_interrupt_config([])
    assert "save_expert" not in cfg
    assert "create_scheduled_task" not in cfg
    assert "access_knowledge_original" not in cfg
    assert cfg["execute"]["when"] is _is_destructive_command
    assert cfg["execute"]["allowed_decisions"] == ["approve", "reject"]
    assert cfg["mysql_execute_sql"] == {"allowed_decisions": ["approve", "reject"]}


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
        "access_knowledge_original",
        "save_expert",
        "create_scheduled_task",
    }
    assert cfg["access_knowledge_original"]["when"] is _is_knowledge_materialize
    assert cfg["save_expert"]["allowed_decisions"] == ["approve", "reject"]
    assert cfg["create_scheduled_task"]["allowed_decisions"] == ["approve", "reject"]


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
        "save_expert",
        "create_scheduled_task",
        "access_knowledge_original",
        "mysql_execute_sql",
    ]


def test_build_interrupt_config_ignores_non_mutating_unknown_tools():
    cfg = build_interrupt_config(["weather_lookup", "read_file"])
    assert set(cfg) == {"execute"}


def test_build_interrupt_config_keeps_legacy_read_uploaded_file_heuristic():
    """行为等价：read_uploaded_file 名字含 upload，改造前已被启发式纳入审批。"""
    cfg = build_interrupt_config(["read_uploaded_file"])
    assert cfg["read_uploaded_file"]["allowed_decisions"] == ["approve", "reject"]


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
        "save_expert",
        "mysql_execute_sql",
    }


def test_agent_interrupt_config_empty_tool_set_only_execute():
    assert set(_agent()._build_interrupt_config()) == {"execute"}


def test_agent_interrupt_config_respects_enable_hitl():
    assert _agent(enable_hitl=False)._build_interrupt_config() is None


def test_agent_interrupt_config_changes_with_tool_set():
    base = set(_agent()._build_interrupt_config())
    with_creator = set(
        _agent([_tool("save_expert"), _tool("read_uploaded_file")])
        ._build_interrupt_config()
    )

    assert base == {"execute"}
    assert with_creator == {"execute", "save_expert", "read_uploaded_file"}
