"""同会话并发流互斥测试（PERF-02）。

旧实现直接 cancel 旧任务，旧任务 hub.close() 的 EOF 会发给新订阅者，
导致新流 0 条内容即结束。现在第二个并发流必须被显式拒绝（SessionBusyError），
上层 API 返回 409。
"""

import asyncio

import pytest

from easy_agent.services import agent_manager as am
from easy_agent.services import session_runtime


@pytest.mark.asyncio
async def test_register_stream_task_rejects_second_active_stream(monkeypatch):
    monkeypatch.setattr(session_runtime, "acquire_stream", lambda *a, **k: "owner")
    monkeypatch.setattr(am, "get_database", lambda: None)
    am._session_stream_tasks.clear()
    t1 = asyncio.create_task(asyncio.sleep(30))
    t2 = asyncio.create_task(asyncio.sleep(30))
    try:
        am.register_stream_task("session-x", t1)
        assert am.is_session_streaming("session-x") is True
        with pytest.raises(session_runtime.SessionBusyError):
            am.register_stream_task("session-x", t2)
        # 原任务仍注册在案，未被 cancel/覆盖
        assert am._session_stream_tasks["session-x"] is t1
        assert not t1.cancelled()
    finally:
        t1.cancel()
        t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        am._session_stream_tasks.pop("session-x", None)


@pytest.mark.asyncio
async def test_register_stream_task_allows_after_finish(monkeypatch):
    monkeypatch.setattr(session_runtime, "acquire_stream", lambda *a, **k: "owner")
    monkeypatch.setattr(am, "get_database", lambda: None)
    am._session_stream_tasks.clear()
    t1 = asyncio.create_task(asyncio.sleep(0))
    await asyncio.gather(t1, return_exceptions=True)
    t2 = asyncio.create_task(asyncio.sleep(30))
    try:
        am.register_stream_task("session-y", t1)  # done task 允许登记
        am.register_stream_task("session-y", t2)
        assert am._session_stream_tasks["session-y"] is t2
    finally:
        t2.cancel()
        await asyncio.gather(t2, return_exceptions=True)
        am._session_stream_tasks.pop("session-y", None)
