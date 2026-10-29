"""Agent 缓存 LRU 与 side dict 清理测试（PERF-09）。"""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from easy_agent.services import agent_manager as am


@pytest.fixture(autouse=True)
def _restore_state():
    saved = (
        am._session_agents,
        am._session_expert,
        am._session_connectors,
        am._session_edit_targets,
        am._session_create_locks,
        am._session_stream_tasks,
    )
    yield
    (
        am._session_agents,
        am._session_expert,
        am._session_connectors,
        am._session_edit_targets,
        am._session_create_locks,
        am._session_stream_tasks,
    ) = saved


def _seed(count: int):
    am._session_agents = OrderedDict(
        (f"s{i}", SimpleNamespace(model_name="m", safe_username="u"))
        for i in range(count)
    )
    am._session_expert = {f"s{i}": "" for i in range(count)}
    am._session_connectors = {f"s{i}": None for i in range(count)}
    am._session_edit_targets = {f"s{i}": "" for i in range(count)}
    am._session_create_locks = {f"s{i}": asyncio.Lock() for i in range(count)}


def test_evict_keeps_cap_and_cleans_side_dicts():
    _seed(am._AGENT_CACHE_MAX + 5)
    am._evict_agent_cache(f"s{am._AGENT_CACHE_MAX + 4}")
    assert len(am._session_agents) == am._AGENT_CACHE_MAX
    victim = "s0"
    assert victim not in am._session_agents
    assert victim not in am._session_expert
    assert victim not in am._session_connectors
    assert victim not in am._session_edit_targets
    assert victim not in am._session_create_locks


def test_touch_moves_to_end():
    am._session_agents = OrderedDict([("a", object()), ("b", object())])
    am._touch_agent_cache("a")
    assert list(am._session_agents) == ["b", "a"]


def test_touch_is_compatible_with_plain_dict():
    am._session_agents = {"a": object()}
    am._touch_agent_cache("a")  # 不应抛 AttributeError（测试/旧调用可能替换为 dict）


async def test_is_session_streaming_reflects_task_state():
    am._session_stream_tasks.clear()
    try:
        assert am.is_session_streaming("x") is False
        task = asyncio.get_running_loop().create_future()
        am._session_stream_tasks["x"] = task  # future 无 done() 前视为运行中
        assert am.is_session_streaming("x") is True
        task.set_result(None)
        assert am.is_session_streaming("x") is False
    finally:
        am._session_stream_tasks.clear()
