"""MySQL 启动初始化：库已存在时不再执行建库 DDL。

背景：``CREATE DATABASE IF NOT EXISTS`` 是 DDL，需要 schema 级元数据锁。库已存在
且库内有未提交事务 / 其它实例正在建表时，它会等到 ``lock_wait_timeout`` 后报
1205（Lock wait timeout exceeded），表现为「启动时连接 MySQL 失败」。
这里断言：存在性探测通过后跳过 DDL，且锁冲突只告警、不阻断启动。
"""

import pytest

pymysql = pytest.importorskip("pymysql")

from easy_agent.db import database as db_mod  # noqa: E402


class _FakeCursor:
    def __init__(self, state, raise_on_execute=False):
        self.state = state
        self.raise_on_execute = raise_on_execute

    def execute(self, sql, args=None):
        if self.raise_on_execute:
            raise pymysql.err.OperationalError(
                1205, "Lock wait timeout exceeded; try restarting transaction"
            )
        self.state["sql"].append(sql)

    def fetchone(self):
        last = self.state["sql"][-1] if self.state["sql"] else ""
        if "information_schema.SCHEMATA" in last:
            return ("easy_agent",) if self.state["exists"] else None
        return {"VERSION()": "8.0.36"}

    def close(self):
        pass


class _FakeConn:
    def __init__(self, state, raise_on_execute=False):
        self.state = state
        self.raise_on_execute = raise_on_execute

    def cursor(self):
        return _FakeCursor(self.state, self.raise_on_execute)

    def close(self):
        pass


class _FakePool:
    def __init__(self, state, **kwargs):
        self.state = state

    def connection(self):
        return _FakeConn(self.state)


def _install(monkeypatch, *, exists=True, raise_1205=False):
    state = {"exists": exists, "sql": []}
    # 只有建库那条连接会抛 1205；连接池里的 SELECT VERSION() 正常
    monkeypatch.setattr(
        db_mod.pymysql, "connect", lambda **kw: _FakeConn(state, raise_1205)
    )
    monkeypatch.setattr(db_mod, "PooledDB", lambda **kw: _FakePool(state, **kw))
    return state


def _make_db():
    return db_mod.Database(
        {
            "type": "mysql",
            "mysql": {
                "database": "easy_agent",
                "host": "127.0.0.1",
                "port": 3306,
                "user": "u",
                "password": "p",
            },
        }
    )


def test_existing_database_skips_create_ddl(monkeypatch):
    state = _install(monkeypatch, exists=True)

    db = _make_db()

    assert db.db_type == "mysql"
    assert not any("CREATE DATABASE" in sql for sql in state["sql"])
    assert any("information_schema.SCHEMATA" in sql for sql in state["sql"])
    assert any("lock_wait_timeout" in sql for sql in state["sql"])


def test_missing_database_is_created(monkeypatch):
    state = _install(monkeypatch, exists=False)

    db = _make_db()

    assert db.db_type == "mysql"
    assert any("CREATE DATABASE" in sql for sql in state["sql"])


def test_lock_wait_timeout_does_not_block_startup(monkeypatch, caplog):
    state = _install(monkeypatch, raise_1205=True)

    db = _make_db()

    # 建库阶段 1205 只告警：连接池仍建立，服务继续以 MySQL 启动
    assert db.db_type == "mysql"
    assert db._pool is not None
    assert "1205" in caplog.text or "Lock wait timeout" in caplog.text
