"""D2：agent_manager 两个会话共享同一张编译图（EasyAgent 仍按会话保留）。"""

from __future__ import annotations

from collections import OrderedDict
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver

import easy_agent.agent.core as agent_mod
from easy_agent.agent.graph_cache import get_agent_graph_cache, reset_agent_graph_cache
from easy_agent.bootstrap.config import Config
from easy_agent.services import agent_manager as am


@pytest.fixture(autouse=True)
def _restore_state(monkeypatch):
    saved = (
        OrderedDict(am._session_agents),
        dict(am._session_expert),
        dict(am._session_creator),
        dict(am._session_connectors),
        dict(am._session_edit_targets),
        dict(am._session_create_locks),
    )
    am._session_agents = OrderedDict()
    am._session_expert = {}
    am._session_creator = {}
    am._session_connectors = {}
    am._session_edit_targets = {}
    am._session_create_locks = {}
    reset_agent_graph_cache()
    monkeypatch.setenv("EASY_AGENT_GRAPH_CACHE", "1")
    yield
    (
        am._session_agents,
        am._session_expert,
        am._session_creator,
        am._session_connectors,
        am._session_edit_targets,
        am._session_create_locks,
    ) = saved
    reset_agent_graph_cache()


@pytest.fixture
def config(tmp_path):
    cfg = Config.load()
    cfg.agent.workspace_dir = str(tmp_path / "workspace")
    cfg.agent.memories_dir = str(tmp_path / "memories")
    am.init_agent_config(cfg, "SYS", "", "test")
    return cfg


@pytest.fixture
def compiled(monkeypatch):
    saver = MemorySaver()
    monkeypatch.setattr(agent_mod, "_shared_graph_checkpointer", lambda: saver)
    calls: list[dict] = []

    def fake_create_deep_agent(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(kind="compiled-graph", kwargs=kwargs)

    monkeypatch.setattr(agent_mod, "create_deep_agent", fake_create_deep_agent)

    async def _no_mcp(username=None, server_names=None):
        return []

    monkeypatch.setattr(am, "load_mcp_tools_for_user", _no_mcp)
    monkeypatch.setattr(
        am,
        "get_database",
        lambda: SimpleNamespace(
            get_user_by_username=lambda username: None,
            get_session=lambda session_id: None,
        ),
    )
    am._session_connectors = {"s1": (), "s2": ()}
    return calls


async def test_two_sessions_share_one_compiled_graph(config, compiled):
    first = await am.get_or_create_agent_for_session(
        "s1", "u1", "w1", enable_hitl=True, enable_web_search=False
    )
    second = await am.get_or_create_agent_for_session(
        "s2", "u1", "w2", enable_hitl=True, enable_web_search=False
    )

    assert len(compiled) == 1, "两个会话应只编译一次（工具由 context 注入）"
    assert first is not second, "per-session EasyAgent 仍然保留"
    assert first.agent is second.agent
    assert first.agent_graph_key == second.agent_graph_key


async def test_same_session_cached_easyagent_does_not_recompile(config, compiled):
    first = await am.get_or_create_agent_for_session(
        "s1", "u1", "w1", enable_hitl=True, enable_web_search=False
    )
    second = await am.get_or_create_agent_for_session(
        "s1", "u1", "w1", enable_hitl=True, enable_web_search=False
    )
    assert second is first
    assert len(compiled) == 1


async def test_expert_prompt_change_compiles_new_graph(config, compiled):
    first = await am.get_or_create_agent_for_session(
        "s1",
        "u1",
        "w1",
        enable_hitl=True,
        enable_web_search=False,
        system_prompt_extra="EXPERT A",
    )
    second = await am.get_or_create_agent_for_session(
        "s2",
        "u1",
        "w2",
        enable_hitl=True,
        enable_web_search=False,
        system_prompt_extra="EXPERT B",
    )
    assert len(compiled) == 2
    assert first.agent is not second.agent
    assert first.agent_graph_key != second.agent_graph_key


async def test_invalidate_user_agents_drops_graph_cache(config, compiled):
    await am.get_or_create_agent_for_session(
        "s1", "u1", "w1", enable_hitl=True, enable_web_search=False
    )
    cache = get_agent_graph_cache()
    assert cache.stats()["entries"] == 1

    am.invalidate_user_agents("u1")
    assert cache.stats()["entries"] == 0
    assert "s1" not in am._session_agents
