"""A2：会话消息单一写路径（sessions.messages 不写正文）。

覆盖：
- ``create_session`` 只写元数据，messages 列为空串（不是 JSON 正文）；
- ``add_message`` / ``update_last_assistant_message`` 只写 session_messages 行表；
- 旧会话（JSON 非空 + 行表空）仍可回退读取；
- 重启期数据自愈不会把新会话的行表消息按空 JSON 重建掉。
"""

import json

from easy_agent.db.database import Database
from easy_agent.db.repositories import sessions as sessions_repo
from easy_agent.models.db import SessionModel


def _make_db(path) -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(path)}})
    db.init_tables()
    return db


def _session(session_id="sess-a2", messages=None):
    return SessionModel(
        session_id=session_id,
        title="t",
        messages=list(messages or []),
        created_at="2026-10-18T00:00:00",
        updated_at="2026-10-18T00:00:00",
        username="alice",
        workspace_name="ws",
    )


def _raw_messages(db, session_id):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT messages FROM sessions WHERE session_id=?",
            (session_id,),
        )
        row = cursor.fetchone()
    return row["messages"] if isinstance(row, dict) else row[0]


def _set_legacy_messages(db, session_id, messages):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE sessions SET messages=? WHERE session_id=?",
            (json.dumps(messages, ensure_ascii=False), session_id),
        )


def test_create_session_only_writes_metadata(tmp_path):
    db = _make_db(tmp_path / "create.db")
    incoming = [{"role": "user", "content": "不要落 JSON", "timestamp": "t1"}]
    created = db.create_session(_session(messages=incoming))

    # 数据库行没有 JSON 正文（schema NOT NULL -> 空串占位）
    raw = _raw_messages(db, created.session_id)
    assert raw == ""
    assert "不要落 JSON" not in raw

    # API 返回对象仍保留传入的 messages（内存语义不变）
    assert created.messages == incoming
    # 新会话行表为空 -> 读为空，且不回退解析 legacy JSON
    assert db.get_messages(created.session_id) == []


def test_add_message_reads_from_rows_and_never_rewrites_json(tmp_path):
    db = _make_db(tmp_path / "add.db")
    sid = db.create_session(_session()).session_id

    db.add_message(sid, {"role": "user", "content": "hello", "timestamp": "t1"})
    db.update_last_assistant_message(
        sid, {"role": "assistant", "content": "hi", "timestamp": "t2"}
    )

    assert [m["content"] for m in db.get_messages(sid)] == ["hello", "hi"]
    assert _raw_messages(db, sid) == ""


def test_legacy_json_fallback_kept_for_old_sessions(tmp_path):
    db = _make_db(tmp_path / "legacy.db")
    sid = db.create_session(_session()).session_id
    legacy = [{"role": "user", "content": "legacy-only", "timestamp": "t"}]
    _set_legacy_messages(db, sid, legacy)

    before = sessions_repo._LEGACY_FALLBACK_COUNT
    # 行表为空 -> 回退读 legacy JSON（存量兼容）
    assert [m["content"] for m in db.get_messages(sid)] == ["legacy-only"]
    assert sessions_repo._LEGACY_FALLBACK_COUNT == before + 1

    # 行表有消息后行表优先，不再读 legacy JSON
    db.add_message(sid, {"role": "user", "content": "row", "timestamp": "t2"})
    assert [m["content"] for m in db.get_messages(sid)] == ["row"]
    assert sessions_repo._LEGACY_FALLBACK_COUNT == before + 1


def test_update_session_does_not_rewrite_message_json(tmp_path):
    db = _make_db(tmp_path / "update.db")
    sid = db.create_session(_session()).session_id
    db.add_message(sid, {"role": "user", "content": "keep", "timestamp": "t1"})

    session = db.get_session(sid)
    session.title = "new-title"
    db.update_session(session)

    assert _raw_messages(db, sid) == ""
    assert [m["content"] for m in db.get_messages(sid)] == ["keep"]
    assert db.get_session(sid).title == "new-title"


def test_restart_repair_keeps_new_session_rows(tmp_path):
    """回归：messages 若写 '[]'，启动自愈会按空 JSON 删除全部行表消息。"""
    path = tmp_path / "restart.db"
    db = _make_db(path)
    sid = db.create_session(_session()).session_id
    db.add_message(sid, {"role": "user", "content": "must-survive", "timestamp": "t1"})

    restarted = _make_db(path)
    assert [m["content"] for m in restarted.get_messages(sid)] == ["must-survive"]
    assert _raw_messages(restarted, sid) == ""
