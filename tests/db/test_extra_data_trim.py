"""消息 extra_data 的超限裁剪测试：不超限原样存，超限先截长字段，仍超限只留结构与元信息。"""

import json

from easy_agent.db.repositories import sessions as sessions_repo

LONG = "x" * 1000


def _message():
    return {
        "role": "assistant",
        "content": "回答",
        "timestamp": "t0",
        "thinking": LONG,
        "blocks": [
            {"type": "thinking", "order": 0, "content": LONG},
            {
                "type": "tool_call",
                "order": 1,
                "tool_name": "ls",
                "arguments": {"path": "/w"},
                "result": LONG,
                "success": True,
            },
            {"type": "content", "order": 2, "content": LONG},
        ],
        "tool_calls": [{"tool_name": "ls", "arguments": {"path": "/w"}, "result": LONG}],
    }


def _stored(monkeypatch, limit):
    monkeypatch.setattr(sessions_repo, "_MAX_EXTRA_DATA_CHARS", limit)
    return json.loads(sessions_repo._sanitize_extra_data(None, _message()))


def test_message_without_extra_fields_stores_nothing():
    assert sessions_repo._sanitize_extra_data(None, {"role": "user", "content": "hi"}) is None


def test_under_the_limit_keeps_everything(monkeypatch):
    extra = _stored(monkeypatch, 1_000_000)

    assert extra["thinking"] == LONG
    assert [b.get("content", b.get("result")) for b in extra["blocks"]] == [LONG] * 3
    assert extra["tool_calls"][0]["result"] == LONG


def test_over_the_limit_truncates_long_fields_first(monkeypatch):
    extra = _stored(monkeypatch, 3_000)

    thinking, tool_call, content = extra["blocks"]
    assert len(thinking["content"]) == 200
    assert len(tool_call["result"]) == 200
    assert tool_call["arguments"] == {"path": "/w"}
    assert len(content["content"]) == 500
    assert len(extra["tool_calls"][0]["result"]) == 200
    assert len(extra["thinking"]) == 200


def test_still_over_the_limit_keeps_only_the_structure(monkeypatch):
    extra = _stored(monkeypatch, 100)

    thinking, tool_call, content = extra["blocks"]
    assert thinking["content"] == "x" * 100 + "..."
    assert tool_call["result"] == "x" * 100 + "..."
    assert tool_call["arguments"] == {}
    assert (tool_call["tool_name"], tool_call["success"]) == ("ls", True)
    assert content["content"] == "x" * 200 + "..."
    assert extra["tool_calls"][0] == {
        **extra["tool_calls"][0],
        "arguments": {},
        "result": "x" * 100 + "...",
    }
    # 顶层 thinking 在第一步已截到 200 字，兜底不再动它
    assert len(extra["thinking"]) == 200
