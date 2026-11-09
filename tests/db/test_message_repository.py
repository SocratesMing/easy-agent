"""T3 消息域 repository：``session_messages`` 行表读写与轮次标注。

A2（消息只写行表）语义不变：``MessageRepository.add_message`` 只写
``session_messages``，legacy ``sessions.messages`` 列保持空串。
"""

from __future__ import annotations

from easy_agent.db.database import Database
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.models.db import SessionModel


def _make_db(tmp_path) -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "messages.db")}})
    db.init_tables()
    return db


def _session(session_id: str = "sess-msg") -> SessionModel:
    return SessionModel(
        session_id=session_id,
        title="消息域",
        messages=[],
        created_at="2026-10-18T00:00:00",
        updated_at="2026-10-18T00:00:00",
        username="alice",
        workspace_name="ws",
    )


def test_add_message_writes_rows_not_legacy_blob(tmp_path):
    db = _make_db(tmp_path)
    SessionRepository(db).create_session(_session())
    repo = MessageRepository(db)

    repo.add_message("sess-msg", {"role": "user", "content": "你好", "timestamp": "t1"})
    repo.add_message(
        "sess-msg", {"role": "assistant", "content": "收到", "timestamp": "t2"}
    )

    assert [m["content"] for m in repo.get_messages("sess-msg")] == ["你好", "收到"]
    # A2：正文只在行表；legacy 列仍是空串。
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT messages FROM sessions WHERE session_id=?", ("sess-msg",))
        row = cursor.fetchone()
    assert (row["messages"] if isinstance(row, dict) else row[0]) == ""


def test_update_last_assistant_message_overwrites_the_tail(tmp_path):
    db = _make_db(tmp_path)
    SessionRepository(db).create_session(_session())
    repo = MessageRepository(db)
    repo.add_message("sess-msg", {"role": "user", "content": "问题", "timestamp": "t1"})
    repo.add_message("sess-msg", {"role": "assistant", "content": "半截", "timestamp": "t2"})

    repo.update_last_assistant_message(
        "sess-msg", {"role": "assistant", "content": "完整回复", "timestamp": "t3"}
    )

    assert [m["content"] for m in repo.get_messages("sess-msg")] == ["问题", "完整回复"]


def test_annotate_user_turn_by_turn_id(tmp_path):
    db = _make_db(tmp_path)
    SessionRepository(db).create_session(_session())
    repo = MessageRepository(db)
    repo.add_message(
        "sess-msg",
        {"role": "user", "content": "问题", "timestamp": "t1", "turn_id": "m1"},
    )

    assert repo.annotate_user_turn("sess-msg", "m1", {"turn_checkpoint_id": "cp-1"}) is True
    assert repo.annotate_user_turn("sess-msg", "missing", {"x": 1}) is False

    [message] = repo.get_messages("sess-msg")
    assert message["turn_checkpoint_id"] == "cp-1"