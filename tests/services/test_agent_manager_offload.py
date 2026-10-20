"""agent_manager 的异步版本：会话运行期状态（流锁、连接器、待修改专家、取消标记）的写库
放进线程，不占事件循环；进程内的状态仍在事件循环上更新。

同步版本（``register_stream_task`` 等）保持不变，测试与同步调用方照旧使用。
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from easy_agent.services import agent_manager as am
from easy_agent.services import session_runtime


@pytest.fixture
def recorder(monkeypatch):
    """把 session_runtime 的写库函数换成记录调用线程的替身。"""
    loop_thread = threading.get_ident()
    calls: list[tuple[str, bool]] = []

    def fake(name, result=None):
        def _fn(*args, **kwargs):
            calls.append((name, threading.get_ident() == loop_thread))
            return result

        return _fn

    for name in ("acquire_stream", "release_stream", "request_cancel", "save_connectors", "save_edit_target"):
        monkeypatch.setattr(session_runtime, name, fake(name))
    monkeypatch.setattr(am, "get_database", lambda: object())
    return calls


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    monkeypatch.setattr(am, "_session_stream_tasks", {})
    monkeypatch.setattr(am, "_session_agents", OrderedDict())
    monkeypatch.setattr(am, "_session_connectors", {})
    monkeypatch.setattr(am, "_session_edit_targets", {})


async def test_stream_lock_is_written_off_the_event_loop(recorder):
    task = asyncio.create_task(asyncio.sleep(30))
    try:
        await am.aregister_stream_task("s1", task)
        assert am.is_session_streaming("s1")
        await am.aunregister_stream_task("s1", task)
        assert not am.is_session_streaming("s1")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert recorder == [("acquire_stream", False), ("release_stream", False)]


async def test_busy_session_is_still_rejected(recorder, monkeypatch):
    def busy(*args, **kwargs):
        raise session_runtime.SessionBusyError("busy")

    monkeypatch.setattr(session_runtime, "acquire_stream", busy)
    task = asyncio.create_task(asyncio.sleep(30))
    try:
        with pytest.raises(session_runtime.SessionBusyError):
            await am.aregister_stream_task("s1", task)
        assert not am.is_session_streaming("s1")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_connectors_evict_on_the_loop_before_saving_off_it(recorder, monkeypatch):
    am._session_agents["s1"] = SimpleNamespace(safe_username="u")
    seen_during_save: list[bool] = []

    def save(*args, **kwargs):
        seen_during_save.append("s1" in am._session_agents)
        recorder.append(("save_connectors", threading.current_thread() is threading.main_thread()))

    monkeypatch.setattr(session_runtime, "save_connectors", save)

    evicted = await am.aset_session_connectors("s1", ["gaode", "gaode", " "])

    assert evicted == 1
    assert am.get_session_connectors("s1") == ("gaode",)
    # 落库时缓存 Agent 已被驱逐：同一会话的新请求不会再拿到旧工具集
    assert seen_during_save == [False]
    assert recorder == [("save_connectors", False)]


async def test_edit_target_binds_on_the_loop_and_saves_off_it(recorder):
    await am.aset_session_edit_target("s1", " expert_a_1 ")
    assert am.get_session_edit_target("s1") == "expert_a_1"

    await am.aset_session_edit_target("s1", "  ")  # 无效输入：不绑定、不写库
    assert am.get_session_edit_target("s1") == "expert_a_1"
    assert recorder == [("save_edit_target", False)]


async def test_cancel_flag_is_written_off_the_event_loop(recorder):
    task = asyncio.create_task(asyncio.sleep(30))
    am._session_stream_tasks["s1"] = task
    try:
        assert await am.cancel_stream_task("s1") is True
    finally:
        await asyncio.gather(task, return_exceptions=True)

    assert recorder[0] == ("request_cancel", False)
