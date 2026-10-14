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


async def test_replay_is_complete_when_history_exceeds_queue_slack():
    """回归：长流程刷新后「只有思考、工具卡片消失」。

    长任务（多轮工具 + 大量 thinking 增量，每个增量都是一个 SSE 事件）会让中枢
    历史超过 1000 条。旧实现回放用固定 maxsize=1000 的队列且满即 break，导致
    刷新后重连的客户端只拿到最早 1000 条，中间（含 tool_call）被静默丢弃。
    """
    hub = _StreamHub("s1")
    total = 1500
    for i in range(total):
        if i == 1000:
            # 工具事件落在历史中段——正是旧实现被丢弃的区间
            hub.broadcast(
                _sse(
                    {
                        "type": "tool_call",
                        "tool_name": "execute",
                        "tool_call_id": "call-mid",
                        "arguments": {"command": "ls -la"},
                        "step": 1,
                    }
                )
            )
        hub.broadcast(_sse({"type": "content", "content": f"chunk-{i}"}))

    q = hub.subscribe()
    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait())

    assert len(replayed) == total + 1, f"回放应完整，实际只回放 {len(replayed)}/{total + 1} 条"
    assert any("tool_call" in item for item in replayed), "中段的 tool_call 事件必须出现在回放里"
    assert "chunk-0" in replayed[0]
    assert "chunk-1499" in replayed[-1]


async def test_slow_consumer_gets_eof_instead_of_hanging():
    """慢消费者被断开时必须收到收尾信号，不能永远 await 在空队列上。"""
    hub = _StreamHub("s1")
    q = hub.subscribe()
    capacity = q.maxsize

    # 广播超过队列容量，触发慢消费者断开分支
    for i in range(capacity + 50):
        hub.broadcast(_sse({"type": "content", "content": f"c{i}"}))

    items = []
    while not q.empty():
        items.append(q.get_nowait())

    assert items[-1] is None, "断开的订阅者应先收到 EOF（None），否则 SSE 连接会永久挂起"
