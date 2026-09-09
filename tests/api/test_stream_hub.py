"""会话级流式事件中枢（_StreamHub）与后台流式任务解耦的单元测试。

注意：依赖 TestClient 的接口用例（/stream/status、/stream/live）未放入本文件，
因为当前环境下 app lifespan（调度器初始化）会挂起，属既有环境问题；中枢核心
逻辑已由本文件覆盖。
"""

import pytest

from easy_agent.api.chat import _StreamHub, _detached_event_stream, _session_stream_hubs
from easy_agent.services.streaming import format_sse


def _sse(obj) -> str:
    return format_sse(obj)


@pytest.fixture(autouse=True)
def _clear_hubs():
    _session_stream_hubs.clear()
    yield
    _session_stream_hubs.clear()


async def test_hub_broadcast_replay_and_close():
    hub = _StreamHub("s1")
    q1 = hub.subscribe()
    hub.broadcast(_sse({"type": "start", "session_id": "s1"}))
    hub.broadcast(_sse({"type": "content", "content": "hi"}))

    # 新订阅者先回放历史
    q2 = hub.subscribe()
    assert q2.get_nowait().startswith("data: ")
    assert q2.get_nowait().startswith("data: ")

    hub.close()
    # 两个订阅者都能拿到历史 + None 收尾
    assert q1.get_nowait().startswith("data: ")
    assert q1.get_nowait().startswith("data: ")
    assert q1.get_nowait() is None
    assert q2.get_nowait() is None


async def test_hub_subscribe_after_done_gets_history_and_none():
    hub = _StreamHub("s1")
    hub.broadcast(_sse({"type": "content", "content": "done-text"}))
    hub.close()

    q = hub.subscribe()
    assert q.get_nowait().startswith("data: ")
    assert q.get_nowait() is None


async def test_detached_event_stream_yields_and_publishes_to_hub():
    async def fake_gen():
        yield _sse({"type": "start", "session_id": "sess1"})
        yield _sse({"type": "content", "content": "hello"})
        yield _sse({"type": "done", "session_id": "sess1"})

    chunks = []
    async for chunk in _detached_event_stream(fake_gen(), "sess1", "s1"):
        chunks.append(chunk)

    assert len(chunks) == 3
    assert "hello" in chunks[1]
    hub = _session_stream_hubs.get("sess1")
    assert hub is not None
    assert hub.done is True
    assert hub.history == chunks
