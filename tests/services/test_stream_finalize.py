"""C2 收尾抽取：``finalize_stream`` 的 done 载荷与副作用顺序 golden。

直接驱动 ``orchestrator.finalize_stream``，用 spy 记录「最终消息 → 副作用 →
会话日志」的调用顺序，并锁住 chat / resume 的 done 字段差异：

- chat：done 无 ``blocks``，usage 无 ``elapsed_time``，写快照 + 会话日志；
- resume：done 带权威 ``blocks``，usage 含 ``elapsed_time``，不写快照/会话日志。

公开生成器（chat / resume）的完整事件序列由 ``test_stream_split_golden.py`` 覆盖。
"""

import json
import time
from types import SimpleNamespace

import pytest

import easy_agent.services.streaming as streaming
from easy_agent.services.streaming import orchestrator


def _events(frames):
    return [json.loads(frame.removeprefix("data: ").strip()) for frame in frames]


class _SessionLogger:
    def __init__(self, sink):
        self.sink = sink

    def log_assistant_response(self, **kwargs):
        self.sink.append(("log_assistant_response", (), kwargs))


def _proc(**overrides):
    proc = SimpleNamespace(
        accumulated_response="hello",
        accumulated_thinking="why",
        blocks=[
            {"type": "thinking", "content": "why", "step": 1, "duration": 0.5},
            {"type": "content", "content": "hello", "step": 1},
            {
                "type": "tool_call",
                "tool_name": "execute",
                "tool_call_id": "c1",
                "arguments": {"command": "ls"},
                "result": "ok",
                "success": True,
                "duration": 0.2,
                "step": 1,
                "approval_status": "approved",
            },
        ],
        current_step=2,
        last_context_tokens=42,
        last_usage={"input_tokens": 12, "output_tokens": 7, "reasoning_tokens": 3},
    )
    for key, value in overrides.items():
        setattr(proc, key, value)
    proc._sse_blocks = lambda: proc.blocks
    return proc


def _spy(monkeypatch):
    calls = []

    def _record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _fn

    def _record_async(name):
        async def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _fn

    for name in (
        "persist_final_message",
        "write_session_context_snapshot",
        "submit_memory_updates",
    ):
        monkeypatch.setattr(orchestrator, name, _record(name))
    for name in ("maybe_rename_workspace", "register_session_skills"):
        monkeypatch.setattr(orchestrator, name, _record_async(name))
    return calls, SimpleNamespace(name="agent"), SimpleNamespace(name="db")


async def _finalize(proc, *, agent, db, session_logger=None, **kwargs):
    return [
        frame
        async for frame in orchestrator.finalize_stream(
            agent=agent,
            db=db,
            session_id="s1",
            sid="s1",
            message_id="m1",
            proc=proc,
            start_time=time.time() - 1.0,
            context_length=None,
            auto_compress_tokens=170000,
            session_logger=session_logger,
            **kwargs,
        )
    ]


async def test_finalize_chat_done_without_blocks_and_side_effect_order(monkeypatch):
    calls, agent, db = _spy(monkeypatch)
    proc = _proc()
    frames = await _finalize(
        proc,
        agent=agent,
        db=db,
        session_logger=_SessionLogger(calls),
        tool_call_records=[("execute", "c1", {"command": "ls"}, "ok", True, 0.2, 1)],
        raw_user_msg="current question",
        message_content="[workspace: /ws] current question",
    )

    assert len(frames) == 1
    done = _events(frames)[0]
    assert done["type"] == "done"
    assert set(done) == {"type", "session_id", "elapsed_time", "usage"}
    assert "blocks" not in done
    assert done["session_id"] == "s1"
    assert done["elapsed_time"] >= 1.0
    assert done["usage"] == {
        "input_tokens": 12,
        "output_tokens": 7,
        "reasoning_tokens": 3,
        "context_length": None,
        "auto_compress_tokens": 170000,
        "context_tokens": 42,
        "step_count": 2,
    }

    # 顺序：最终消息 → 快照 → rename → 技能入库 → 记忆更新 → 会话日志 → done
    assert [call[0] for call in calls] == [
        "persist_final_message",
        "write_session_context_snapshot",
        "maybe_rename_workspace",
        "register_session_skills",
        "submit_memory_updates",
        "log_assistant_response",
    ]
    final_message = calls[0][1][2]
    assert final_message["content"] == "hello"
    assert final_message["thinking_duration"] == pytest.approx(0.5)
    assert final_message["tool_calls"][0]["approval_status"] is None
    assert calls[1][2]["message_content"] == "[workspace: /ws] current question"
    # 记忆更新使用当前用户消息（chat 的 raw_user_msg）
    assert calls[4][1][3] == "current question"
    assert calls[5][2]["content"] == "hello"


async def test_finalize_resume_done_with_authoritative_blocks_and_elapsed_usage(
    monkeypatch,
):
    calls, agent, db = _spy(monkeypatch)
    proc = _proc()
    tool_records = [
        ("execute", "c1", {"command": "ls"}, "ok", True, 0.2, 1, "approved")
    ]
    frames = await _finalize(
        proc,
        agent=agent,
        db=db,
        session_logger=_SessionLogger(calls),
        tool_call_records=tool_records,
        raw_user_msg="original question",
        mode="resume",
    )

    assert len(frames) == 1
    done = _events(frames)[0]
    assert done["type"] == "done"
    assert set(done) == {"type", "session_id", "elapsed_time", "usage", "blocks"}
    assert done["blocks"] == proc.blocks
    assert done["usage"]["elapsed_time"] == done["elapsed_time"]
    assert done["usage"]["step_count"] == 2

    # resume 不写 context 快照、不记会话日志；其余副作用顺序与 chat 一致
    assert [call[0] for call in calls] == [
        "persist_final_message",
        "maybe_rename_workspace",
        "register_session_skills",
        "submit_memory_updates",
    ]
    final_message = calls[0][1][2]
    assert final_message["thinking_duration"] is None
    assert final_message["tool_calls"][0]["approval_status"] == "approved"
    # 记忆更新使用恢复流捕获的原始用户消息
    assert calls[3][1][3] == "original question"


def test_finalize_stream_reexported_from_package_facade():
    assert streaming.finalize_stream is orchestrator.finalize_stream
