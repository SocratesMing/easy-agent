"""V3StreamRunner._translate 的最小单测：把 v3 内容块增量翻成 StreamProcessor 事件。"""

from langchain_core.messages import AIMessageChunk

from easy_agent.services.stream_processor import StreamProcessor
from easy_agent.services.stream_v3 import V3StreamRunner


def _proc():
    return StreamProcessor(sid="t", session_id="s", current_step=0, blocks=[])


def _types(events):
    return [e["type"] for e in events]


def test_text_delta_becomes_content_event():
    runner = V3StreamRunner(_proc())
    events = runner._translate(
        {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "hi"}}
    )
    assert _types(events) == ["content"]
    assert events[0]["content"] == "hi"


def test_reasoning_delta_becomes_thinking_event():
    runner = V3StreamRunner(_proc())
    events = runner._translate(
        {
            "event": "content-block-delta",
            "delta": {"type": "reasoning-delta", "reasoning": "想一下"},
        }
    )
    # 首次进入思考：thinking_start + thinking
    assert "thinking" in _types(events)
    assert any(e.get("content") == "想一下" for e in events if e["type"] == "thinking")


def test_legacy_content_block_reasoning_supported():
    runner = V3StreamRunner(_proc())
    events = runner._translate(
        {"event": "content-block-delta", "content_block": {"type": "reasoning", "reasoning": "r"}}
    )
    assert "thinking" in _types(events)


def test_empty_and_non_delta_ignored():
    runner = V3StreamRunner(_proc())
    assert runner._translate({"event": "message-start"}) == []
    assert runner._translate(
        {"event": "content-block-delta", "delta": {"type": "text-delta", "text": ""}}
    ) == []
