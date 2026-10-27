"""旧 ``sessions.messages`` JSON 的启动修复与一次性清空（``db/legacy_messages.py``）。

回归：A2（10-18）起正文只写行表，旧会话的 JSON 不再更新。原启动修复在条数不等时删掉
整个会话的行、按旧 JSON 重建，继续对话的旧会话每次重启都会丢掉 A2 之后的消息。
现在只补不删；行表已覆盖 JSON 的会话由迁移 6 把 JSON 清空。
"""

import json
import logging

from easy_agent.db import migrations as mig
from easy_agent.db.database import Database


def _make_db(path) -> Database:
    """建库 / 模拟一次重启：``init_tables`` 会跑启动修复和还没执行过的迁移。"""
    db = Database({"type": "sqlite", "sqlite": {"path": str(path)}})
    db.init_tables()
    return db


def _old_db(path, monkeypatch) -> Database:
    """改造前创建的库：迁移账本里还没有 6，下一次启动才会执行它。"""
    with monkeypatch.context() as m:
        m.setattr(mig, "MIGRATIONS", [])
        return _make_db(path)


def _msg(role, content, ts="2026-07-21T10:00:00"):
    return {"role": role, "content": content, "timestamp": ts}


def _legacy_session(db, sid, blob, rows=None):
    """A2 之前的会话：JSON 列存 ``blob``（字符串原样写入），行表存 ``rows``（默认与 JSON 相同）。"""
    rows = blob if rows is None else rows
    raw = blob if isinstance(blob, str) else json.dumps(blob, ensure_ascii=False)
    with db.get_connection() as conn:
        cur = conn.cursor()
        db._execute(
            cur,
            "INSERT INTO sessions (session_id, title, messages, created_at, updated_at, username) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "t", raw, "t0", "t0", "u"),
        )
        for m in rows:
            db._execute(
                cur,
                "INSERT INTO session_messages (session_id, role, content, extra_data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, m["role"], m["content"], None, m["timestamp"]),
            )


def _contents(db, sid):
    return [m["content"] for m in db.get_messages(sid)]


def _raw_blob(db, sid):
    with db.get_connection() as conn:
        cur = conn.cursor()
        db._execute(cur, "SELECT messages FROM sessions WHERE session_id=?", (sid,))
        return cur.fetchone()[0]


OLD = [_msg("user", "旧问题"), _msg("assistant", "旧回答")]


def test_restart_keeps_messages_added_after_a2(tmp_path, monkeypatch):
    """回归：旧会话在 A2 之后继续对话，重启不能按旧 JSON 把新消息删掉。"""
    path = tmp_path / "legacy.db"
    db = _old_db(path, monkeypatch)
    _legacy_session(db, "s1", OLD)
    db.add_message("s1", _msg("user", "新问题"))
    db.add_message("s1", _msg("assistant", "新回答"))

    restarted = _make_db(path)
    assert _contents(restarted, "s1") == ["旧问题", "旧回答", "新问题", "新回答"]
    # 行表已覆盖旧 JSON：迁移 6 把它清空，之后的启动扫描不再读它
    assert _raw_blob(restarted, "s1") == ""

    # 再重启一次（迁移不再执行）消息也不变
    assert _contents(_make_db(path), "s1") == ["旧问题", "旧回答", "新问题", "新回答"]


def test_restart_fills_rows_for_a_legacy_session_without_rows(tmp_path, monkeypatch):
    path = tmp_path / "no-rows.db"
    db = _old_db(path, monkeypatch)
    blob = [*OLD, _msg("user", "第三条")]
    _legacy_session(db, "s1", blob, rows=[])

    restarted = _make_db(path)
    assert _contents(restarted, "s1") == ["旧问题", "旧回答", "第三条"]
    assert _raw_blob(restarted, "s1") == ""


def test_restart_appends_the_missing_tail_in_order(tmp_path, monkeypatch):
    """行表是 JSON 的前缀（尾部缺失）：按原顺序补上缺的那几条。"""
    path = tmp_path / "tail.db"
    db = _old_db(path, monkeypatch)
    blob = [*OLD, _msg("user", "第三条")]
    _legacy_session(db, "s1", blob, rows=blob[:1])

    restarted = _make_db(path)
    assert _contents(restarted, "s1") == ["旧问题", "旧回答", "第三条"]


def test_diverged_sessions_are_left_untouched(tmp_path, monkeypatch, caplog):
    """行表与 JSON 对不上：两边都不改动，只告警，留给人工核对。"""
    caplog.set_level(logging.WARNING, logger="easy_agent.db.legacy_messages")
    path = tmp_path / "diverged.db"
    db = _old_db(path, monkeypatch)
    blob = [*OLD, _msg("user", "第三条")]
    _legacy_session(db, "s1", blob, rows=[OLD[0], _msg("assistant", "改过的回答")])

    restarted = _make_db(path)
    assert _contents(restarted, "s1") == ["旧问题", "改过的回答"]
    assert json.loads(_raw_blob(restarted, "s1")) == blob
    assert "对不上" in caplog.text


def test_migration_clears_only_blobs_covered_by_rows(tmp_path, monkeypatch):
    path = tmp_path / "mixed.db"
    db = _old_db(path, monkeypatch)
    _legacy_session(db, "covered", OLD)
    _legacy_session(db, "continued", OLD, rows=[*OLD, _msg("user", "新问题")])
    _legacy_session(db, "diverged", OLD, rows=[OLD[0], _msg("assistant", "改过的回答")])
    _legacy_session(db, "broken", '[{"role": "user"', rows=[OLD[0]])
    _legacy_session(db, "empty-array", "[]", rows=[OLD[0]])

    restarted = _make_db(path)

    assert _raw_blob(restarted, "covered") == ""
    assert _raw_blob(restarted, "continued") == ""
    assert _raw_blob(restarted, "empty-array") == ""
    # 对不上 / 不是合法 JSON：无法确认行表完整，保留原样
    assert json.loads(_raw_blob(restarted, "diverged")) == OLD
    assert _raw_blob(restarted, "broken") == '[{"role": "user"'
    # 只动 JSON 列，消息行一条不少
    assert _contents(restarted, "continued") == ["旧问题", "旧回答", "新问题"]
    assert _contents(restarted, "diverged") == ["旧问题", "改过的回答"]


def test_migration_is_recorded_and_runs_once(tmp_path, monkeypatch):
    path = tmp_path / "once.db"
    _old_db(path, monkeypatch)
    _make_db(path)
    db = _make_db(path)

    with db.get_connection() as conn:
        versions = mig.applied_versions(db, conn.cursor())
    assert versions.count(6) == 1
