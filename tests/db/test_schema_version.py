"""init_tables() 的 schema 版本快速路径与数据自愈行为。

背景：引入分布式锁后启动要连 MySQL，每个 pod 都跑完整建表/补列/建索引流程，
在 MySQL 上是 40+ 次网络往返，导致启动慢。这里的用例锁定优化后的行为：
结构无变化时只发少量探测 SQL，结构变更/缺表时仍能正确迁移。
"""

import json
from contextlib import contextmanager

import pytest

from easy_agent.db import database as dbmod
from easy_agent.db.database import Database, SCHEMA_VERSION, EXPECTED_TABLES


class _CountingCursor:
    """包一层真实 cursor，统计底层 execute 次数（≈ 数据库往返数）。"""

    def __init__(self, cursor, counter):
        self._cursor = cursor
        self._counter = counter

    def execute(self, sql, params=None):
        self._counter["n"] += 1
        if params is None:
            return self._cursor.execute(sql)
        return self._cursor.execute(sql, params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        return self._cursor.close()


class _Conn:
    def __init__(self, conn, counter):
        self._conn = conn
        self._counter = counter

    def cursor(self):
        return _CountingCursor(self._conn.cursor(), self._counter)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()


@pytest.fixture()
def sqlite_path(tmp_path):
    return tmp_path / "schema.db"


def _init_with_count(path):
    """初始化数据库并返回 (db, 底层 execute 次数)。"""
    counter = {"n": 0}
    db = Database({"type": "sqlite", "sqlite": {"path": str(path)}})
    real_get = db.get_connection

    @contextmanager
    def counting_get():
        with real_get() as conn:
            yield _Conn(conn, counter)

    db.get_connection = counting_get
    db.init_tables()
    return db, counter["n"]


def test_fast_path_skips_ddl_when_schema_unchanged(sqlite_path):
    """第二次启动（结构已是最新）应显著少于首次的 SQL 条数。"""
    _, first = _init_with_count(sqlite_path)
    _, second = _init_with_count(sqlite_path)
    assert second < first
    # 快速路径只做：建 meta 表 + 读版本 + 校验表 + 数据自愈比对
    assert second <= 8


def test_schema_version_recorded(sqlite_path):
    db, _ = _init_with_count(sqlite_path)
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT meta_value FROM schema_meta WHERE meta_key=?",
            (dbmod.SCHEMA_VERSION_KEY,),
        )
        row = cur.fetchone()
    assert row is not None
    assert int(row[0]) == SCHEMA_VERSION


def test_present_tables_detected(sqlite_path):
    db, _ = _init_with_count(sqlite_path)
    with db.get_connection() as conn:
        assert db._schema_tables_present(conn.cursor()) is True


def test_missing_table_forces_full_init(sqlite_path):
    db, _ = _init_with_count(sqlite_path)
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE distributed_locks")
        conn.commit()
        assert db._schema_tables_present(cur) is False

    # 重新初始化应补回缺失的表
    db2, _ = _init_with_count(sqlite_path)
    with db2.get_connection() as conn:
        assert db2._schema_tables_present(conn.cursor()) is True


def test_repair_rebuilds_mismatched_rows_on_fast_path(sqlite_path):
    """结构无变化时，仍应自愈 JSON 条数与 session_messages 行数不一致的会话。"""
    db, _ = _init_with_count(sqlite_path)
    messages = [
        {"role": "user", "content": "hi", "timestamp": "t1"},
        {"role": "assistant", "content": "yo", "timestamp": "t2"},
        {"role": "user", "content": "more", "timestamp": "t3"},
    ]
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions(session_id,title,messages,created_at,updated_at,username)"
            " VALUES (?,?,?,?,?,?)",
            ("s1", "t", json.dumps(messages), "t", "t", "u"),
        )
        cur.execute(
            "INSERT INTO session_messages(session_id,role,content,extra_data,created_at)"
            " VALUES (?,?,?,?,?)",
            ("s1", "user", "hi", None, "t1"),
        )

    db2, _ = _init_with_count(sqlite_path)
    with db2.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id=?", ("s1",)
        )
        assert cur.fetchone()[0] == len(messages)


def test_expected_tables_match_created_schema(sqlite_path):
    db, _ = _init_with_count(sqlite_path)
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r[0] for r in cur.fetchall()}
    assert set(EXPECTED_TABLES).issubset(names)
