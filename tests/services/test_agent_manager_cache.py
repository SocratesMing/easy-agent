"""Agent 缓存 LRU 与 SessionState 会话状态清理测试（PERF-09 / T6）。"""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from easy_agent.services.agents import manager as am
from easy_agent.services.agents.manager import SessionState


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """隔离会话状态注册表：每个用例一份新的 OrderedDict，结束后恢复原对象。"""
    monkeypatch.setattr(am, "_sessions", OrderedDict())


def _seed(count: int) -> None:
    for index in range(count):
        am._sessions[f"s{index}"] = SessionState(
            agent=SimpleNamespace(model_name="m", safe_username="u"),
            expert_key="expert_a",
            connectors=("srv",),
            edit_target="expert_a",
            skill_scope=("docx",),
            turn_skills=["docx"],
            create_lock=asyncio.Lock(),
        )


def _cached_agents() -> int:
    return sum(1 for state in am._sessions.values() if state.agent is not None)


def test_evict_keeps_cap_and_cleans_session_state():
    _seed(am._AGENT_CACHE_MAX + 5)
    keep = f"s{am._AGENT_CACHE_MAX + 4}"

    am._evict_agent_cache(keep)

    assert _cached_agents() == am._AGENT_CACHE_MAX
    # 淘汰最久未用的 s0：随 Agent 的 side 状态一并清掉（锁空闲、无流任务 → 整条移除）
    assert "s0" not in am._sessions
    assert am.get_session_agent(keep) is not None


def test_evict_ignores_sessions_without_agent():
    """无缓存 Agent 的会话不参与 LRU 计数，也不会被淘汰。"""
    _seed(am._AGENT_CACHE_MAX)
    am._sessions["aux"] = SessionState(connectors=("srv",))
    am._sessions["new"] = SessionState(agent=SimpleNamespace(model_name="m"))

    am._evict_agent_cache("new")

    assert _cached_agents() == am._AGENT_CACHE_MAX
    assert am.get_session_connectors("aux") == ("srv",)
    assert am.get_session_agent("new") is not None


def test_touch_moves_to_end():
    am._sessions["a"] = SessionState(agent=object())
    am._sessions["b"] = SessionState(agent=object())
    am._touch_agent_cache("a")
    assert list(am._sessions) == ["b", "a"]


def test_get_session_agent_returns_none_when_not_cached():
    assert am.get_session_agent("no-such-session") is None
    agent = object()
    am._sessions["s"] = SessionState(agent=agent)
    assert am.get_session_agent("s") is agent


async def test_is_session_streaming_reflects_task_state():
    assert am.is_session_streaming("x") is False
    task = asyncio.get_running_loop().create_future()
    am._sessions["x"] = SessionState(stream_task=task)
    assert am.is_session_streaming("x") is True
    task.set_result(None)
    assert am.is_session_streaming("x") is False
