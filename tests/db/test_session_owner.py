"""轻量归属查询与 legacy messages 回退测试（PERF-11/PERF-15）。"""

import json

from easy_agent.db.database import Database
from easy_agent.models.db import SessionModel


def _db(tmp_path) -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "owner.db")}})
    db.init_tables()
    return db


def _set_legacy_messages(db, session_id, messages):
    """模拟 A2 之前的存量会话：JSON 正文直接写在 sessions.messages。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE sessions SET messages=? WHERE session_id=?",
            (json.dumps(messages, ensure_ascii=False), session_id),
        )


def _session(messages):
    return SessionModel(
        session_id="sess-1",
        title="t",
        messages=messages,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        username="alice",
        workspace_name="ws",
    )


def test_get_session_owner_light_fields(tmp_path):
    db = _db(tmp_path)
    db.create_session(_session([]))
    db.update_session_workspace_name("sess-1", "ws")
    owner = db.get_session_owner("sess-1")
    assert owner["username"] == "alice"
    assert owner["workspace_name"] == "ws"
    assert db.get_session_owner("missing") is None


def test_legacy_messages_fallback_and_meta_update(tmp_path):
    db = _db(tmp_path)
    # A2 起 create_session 不再写 JSON 正文；直接构造存量会话的 legacy 列。
    db.create_session(_session([]))
    _set_legacy_messages(
        db, "sess-1", [{"role": "user", "content": "legacy-only", "timestamp": "t"}]
    )
    # 行表为空 -> 回退读取 legacy JSON
    assert [m["content"] for m in db.get_session("sess-1").messages] == ["legacy-only"]

    # 仅更新元信息不应清空/重写 legacy 消息
    db.update_session_meta("sess-1", title="new-title", updated_at="2026-02-01T00:00:00")
    got = db.get_session("sess-1")
    assert got.title == "new-title"
    assert [m["content"] for m in got.messages] == ["legacy-only"]
