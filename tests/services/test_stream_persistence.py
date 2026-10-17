"""C2 persistence 适配器测试：节流、增量/思考/工具结果落库与审批决策。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.services.streaming import persistence
from easy_agent.services.streaming.persistence import (
    IncrementalPersister,
    persist_approval_decisions,
    persist_partial,
    persist_thinking_event,
    persist_todos,
    persist_tool_result_event,
)


class FakeDB:
    def __init__(self, session=None):
        self.session = session
        self.persisted = []
        self.thinking = []
        self.tool_calls = []
        self.generated = []
        self.todos = []

    def update_last_assistant_message(self, session_id, message):
        self.persisted.append(message)

    def record_thinking(self, **kwargs):
        self.thinking.append(kwargs)

    def record_tool_call(self, **kwargs):
        self.tool_calls.append(kwargs)

    def add_generated_file(self, **kwargs):
        self.generated.append(kwargs)

    def update_session_todos(self, session_id, todos):
        self.todos.append(todos)

    def get_session(self, session_id):
        return self.session


class FakeLogger:
    def __init__(self):
        self.tool_calls = []

    def log_tool_call(self, **kwargs):
        self.tool_calls.append(kwargs)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def time(self):
        return self.now


def _proc(response=""):
    return SimpleNamespace(
        accumulated_response=response,
        accumulated_thinking="",
        blocks=[],
        last_usage={"input_tokens": 10, "output_tokens": 5, "reasoning_tokens": 1},
        last_context_tokens=0,
        current_step=1,
    )


def test_incremental_persister_uses_char_and_time_thresholds(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(persistence, "time", clock)
    db = FakeDB()
    proc = _proc("x" * 400)
    persister = IncrementalPersister(
        db=db, session_id="s1", sid="s1", start_time=clock.now
    )

    assert persister.maybe_persist(proc) is False
    assert db.persisted == []

    proc.accumulated_response = "x" * 600
    clock.now = 1000.5
    assert persister.maybe_persist(proc) is False, "未到时间窗口不落库"

    clock.now = 1002.1
    assert persister.maybe_persist(proc) is True
    assert len(db.persisted) == 1
    assert db.persisted[0]["content"] == "x" * 600
    assert persister.last_persisted_len == 600

    assert persister.maybe_persist(proc) is False, "无新增内容不重复落库"

    proc.accumulated_response = "x" * 21000
    clock.now = 1002.2
    assert persister.maybe_persist(proc) is True, "超过最大字符阈值立即落库"
    assert len(db.persisted) == 2
    assert persister.last_persisted_len == 21000


def test_persist_partial_saves_and_can_raise(monkeypatch):
    db = FakeDB()
    proc = _proc("partial")
    persist_partial(proc, db=db, session_id="s1", sid="s1", start_time=0)
    assert db.persisted[0]["content"] == "partial"

    class BrokenDB(FakeDB):
        def update_last_assistant_message(self, session_id, message):
            raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        persist_partial(
            proc,
            db=BrokenDB(),
            session_id="s1",
            sid="s1",
            start_time=0,
            raise_on_error=True,
        )
    # 默认吞异常：持久化失败不能中断流式输出
    persist_partial(proc, db=BrokenDB(), session_id="s1", sid="s1", start_time=0)


def test_persist_thinking_event_records_trimmed_content():
    db = FakeDB()
    proc = _proc()
    proc.blocks = [
        {"type": "thinking", "step": 1, "content": "  why  "},
        {"type": "thinking", "step": 2, "content": "later"},
    ]

    persist_thinking_event(
        {"step": 1, "duration": 0.5},
        proc=proc,
        db=db,
        session_id="s1",
        message_id="m1",
        sid="s1",
    )
    assert db.thinking == [
        {
            "session_id": "s1",
            "message_id": "m1",
            "step": 1,
            "content": "why",
            "duration": 0.5,
        }
    ]

    persist_thinking_event(
        {"step": 2, "duration": 0.1},
        proc=proc,
        db=db,
        session_id="s1",
        message_id="",
        sid="s1",
    )
    assert len(db.thinking) == 1, "message_id 为空时不落库"


def test_persist_tool_result_event_records_call_log_and_generated_file():
    db = FakeDB()
    logger = FakeLogger()
    agent = SimpleNamespace(workspace_dir=Path("/tmp/ws"))

    persist_tool_result_event(
        {
            "tool_name": "execute",
            "tool_call_id": "c1",
            "arguments": {"command": "ls"},
            "result": "ok",
            "success": True,
            "duration": 0.2,
            "step": 1,
        },
        db=db,
        session_id="s1",
        message_id="m1",
        session_logger=logger,
        agent=agent,
        sid="s1",
    )
    assert db.tool_calls[0] == {
        "session_id": "s1",
        "message_id": "m1",
        "tool_name": "execute",
        "tool_call_id": "c1",
        "arguments": {"command": "ls"},
        "result": "ok",
        "success": True,
        "duration": 0.2,
        "step": 1,
    }
    assert logger.tool_calls[0]["tool_call_id"] == "c1"

    persist_tool_result_event(
        {
            "tool_name": "write_file",
            "tool_call_id": "c2",
            "arguments": {"file_path": "out/report.md"},
            "result": "written",
            "success": True,
            "duration": 0.1,
            "step": 2,
        },
        db=db,
        session_id="s1",
        message_id="m1",
        session_logger=None,
        agent=agent,
        sid="s1",
    )
    assert db.generated[0] == {
        "session_id": "s1",
        "message_id": "m1",
        "filename": "report.md",
        "file_path": "/tmp/ws/out/report.md",
        "file_type": "md",
        "size": 0,
    }


def test_persist_approval_decisions_updates_tool_calls_and_blocks():
    session = SimpleNamespace(
        messages=[
            {
                "role": "assistant",
                "tool_calls": [
                    {"tool_call_id": "c1", "approval_status": "pending"}
                ],
                "blocks": [
                    {
                        "type": "tool_call",
                        "tool_call_id": "c1",
                        "approval_status": "pending",
                        "pending_approval": True,
                    }
                ],
            }
        ]
    )
    db = FakeDB(session=session)

    persist_approval_decisions(db, "s1", {"c1": "approved"}, "s1")

    assert db.persisted[0]["tool_calls"][0]["approval_status"] == "approved"
    assert db.persisted[0]["blocks"][0] == {
        "type": "tool_call",
        "tool_call_id": "c1",
        "approval_status": "approved",
    }


def test_persist_todos_swallows_errors():
    class BrokenDB(FakeDB):
        def update_session_todos(self, session_id, todos):
            raise RuntimeError("db down")

    persist_todos(BrokenDB(), "s1", [{"content": "x"}], "s1")
    assert persist_todos is not None
