"""agent_manager 工具装配：注册表接入后的工具集合/顺序与改造前等价。

用 spy 替代 ``EasyAgent``、mock MCP/联网工具，不触发 LLM/DB/网络。
"""

from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.bootstrap.config import Config
from easy_agent.services import agent_manager as am
from easy_agent.tools import registry
from easy_agent.tools.registry import ToolSpec


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _SpyAgent:
    """替代 EasyAgent：记录构造参数，并补齐 agent_manager 会读取的属性。"""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = dict(kwargs)
        self.model_name = kwargs.get("model_name") or kwargs["config"].active_model
        self.workspace_dir = "/tmp/ws"
        self.system_prompt = kwargs.get("system_prompt", "")
        self.safe_username = Config.sanitize_username(kwargs.get("username", ""))
        self.enable_web_search = bool(kwargs.get("enable_web_search", False))


def _fake_extender(tools, *, config, username, session_id, workspace_name):
    return [*tools, _FakeTool("access_knowledge_original")]


def _fake_factories():
    return {
        "access_knowledge_original": _fake_extender,
        "create_scheduled_task": lambda **kwargs: _FakeTool("create_scheduled_task"),
        "save_expert": lambda **kwargs: _FakeTool("save_expert"),
        "read_uploaded_file": lambda **kwargs: _FakeTool("read_uploaded_file"),
    }


async def _fake_load_mcp(*args, **kwargs):
    return [_FakeTool("mcp_a")]


def _injected_names() -> list[str]:
    return [tool.name for tool in _SpyAgent.last_kwargs["mcp_tools"]]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    saved = (
        am._session_agents,
        am._session_expert,
        am._session_connectors,
        am._session_edit_targets,
        am._session_create_locks,
    )
    am._session_agents = OrderedDict()
    am._session_expert = {}
    am._session_connectors = {}
    am._session_edit_targets = {}
    am._session_create_locks = {}
    _SpyAgent.last_kwargs = {}

    config = SimpleNamespace(
        active_model="model-x",
        agent=SimpleNamespace(workspace_dir="/tmp/ws", memories_dir="/tmp/mem"),
        expert_tags=None,
        file_context=SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(
        am,
        "_agent_config",
        {
            "config": config,
            "system_prompt": "base prompt",
            "skills_root": "",
            "agent_env": "test",
            "win": False,
        },
    )
    monkeypatch.setattr(am, "EasyAgent", _SpyAgent)
    monkeypatch.setattr(
        am,
        "get_database",
        lambda: (_ for _ in ()).throw(RuntimeError("db disabled in test")),
    )
    monkeypatch.setattr(
        Config,
        "get_user_mcp_path",
        classmethod(lambda cls, username, config=None: Path("/nonexistent/mcp.json")),
    )
    monkeypatch.setattr(am, "_tool_factories", _fake_factories)
    monkeypatch.setattr(am, "load_mcp_tools_for_user", _fake_load_mcp)
    yield config

    (
        am._session_agents,
        am._session_expert,
        am._session_connectors,
        am._session_edit_targets,
        am._session_create_locks,
    ) = saved


async def test_hitl_disabled_in_config_turns_approvals_off(monkeypatch, _isolate):
    """config.yaml 的 hitl.enabled=false：对话和定时任务一样不审批，也不提供建定时任务的工具。"""
    _isolate.hitl = SimpleNamespace(enabled=False, tools=None)
    monkeypatch.setattr(am, "create_web_search_tool", lambda *a, **k: None)

    await am._get_or_create_agent_unlocked(
        "session-hitl0",
        username="u",
        workspace_name="ws",
        enable_hitl=True,
        enable_web_search=False,
    )

    assert _SpyAgent.last_kwargs["enable_hitl"] is False
    assert "create_scheduled_task" not in _injected_names()


async def test_tool_set_matches_legacy_when_hitl_and_file_context_on(monkeypatch):
    monkeypatch.setattr(am, "create_web_search_tool", lambda *a, **k: None)

    agent = await am._get_or_create_agent_unlocked(
        "session-abcde",
        username="u",
        workspace_name="ws",
        enable_hitl=True,
        enable_web_search=False,
    )

    assert _injected_names() == [
        "mcp_a",
        "access_knowledge_original",
        "create_scheduled_task",
        "save_expert",
        "read_uploaded_file",
    ]
    assert agent is am._session_agents["session-abcde"]


async def test_expert_session_gets_no_save_expert(monkeypatch):
    """DA-13：专家会话不提供 save_expert，避免领域专家被借来创建 / 修改别的专家。"""
    monkeypatch.setattr(am, "create_web_search_tool", lambda *a, **k: None)

    await am._get_or_create_agent_unlocked(
        "session-expert",
        username="u",
        workspace_name="ws",
        enable_hitl=True,
        enable_web_search=False,
        expert_agent_id="expert_a_1",
    )

    assert "save_expert" not in _injected_names()


async def test_tool_set_matches_legacy_with_web_search_and_no_hitl(_isolate, monkeypatch):
    _isolate.file_context = SimpleNamespace(enabled=False)
    monkeypatch.setattr(am, "create_web_search_tool", lambda *a, **k: _FakeTool("web_search"))

    await am._get_or_create_agent_unlocked(
        "session-2",
        username="u",
        workspace_name="ws",
        enable_hitl=False,
        enable_web_search=True,
    )

    # save_expert 不再依赖本轮选中 expert-creator：非专家会话都注入，由技能带出（DA-13）
    assert _injected_names() == [
        "mcp_a",
        "access_knowledge_original",
        "save_expert",
        "web_search",
    ]
    assert _SpyAgent.last_kwargs["enable_web_search"] is True


async def test_agent_manager_follows_registry_for_new_tools(monkeypatch):
    """新增工具只改注册表即可注入：agent_manager 主流程不再识别具体工具名。"""
    monkeypatch.setattr(am, "create_web_search_tool", lambda *a, **k: None)
    extra = ToolSpec(
        name="demo_tool",
        source="expert",
        inject_order=99,
        builder=lambda ctx: _FakeTool("demo_tool"),
    )
    monkeypatch.setattr(registry, "TOOL_REGISTRY", registry.TOOL_REGISTRY + (extra,))

    await am._get_or_create_agent_unlocked(
        "session-3",
        username="u",
        workspace_name="ws",
        enable_hitl=False,
        enable_web_search=False,
    )

    assert "demo_tool" in _injected_names()
    assert _injected_names()[-1] == "demo_tool"


def test_every_injectable_spec_has_a_factory():
    """需要构造的工具必须在装配根工厂表里有实现（web_search 由 ctx 直接传入）。"""
    factories = am._tool_factories()
    assert set(factories) == {
        "access_knowledge_original",
        "create_scheduled_task",
        "save_expert",
        "read_uploaded_file",
    }
    injectable = {spec.name for spec in registry.ordered_injectable_specs()}
    assert set(factories) <= injectable


async def test_session_connectors_are_loaded_off_the_event_loop(monkeypatch):
    """建 Agent 时从 DB 读会话连接器：放进线程，不占事件循环。"""
    import threading

    from easy_agent.services import session_runtime

    monkeypatch.setattr(am, "create_web_search_tool", lambda *a, **k: None)
    monkeypatch.setattr(am, "get_database", lambda: object())
    loop_thread = threading.get_ident()
    off_loop: list[bool] = []

    def fake_load(db, session_id):
        off_loop.append(threading.get_ident() != loop_thread)
        return ("srv_a",)

    monkeypatch.setattr(session_runtime, "load_connectors", fake_load)

    await am._get_or_create_agent_unlocked(
        "session-conn",
        username="u",
        workspace_name="ws",
        enable_hitl=False,
        enable_web_search=False,
    )

    assert off_loop == [True]
    assert am._session_connectors["session-conn"] == ("srv_a",)
