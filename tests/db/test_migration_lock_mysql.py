"""MySQL 迁移锁不能用错 db.get_connection() 的 contextmanager 语义。"""

from contextlib import contextmanager

import pytest

from easy_agent.db.migration_lock import schema_migration_lock


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.executed.append(sql)
        if "GET_LOCK" in sql:
            self.conn.lock_result = self.conn.next_lock_result
        if "RELEASE_LOCK" in sql:
            self.conn.released = True

    def fetchone(self):
        if self.conn.executed[-1].startswith("SELECT GET_LOCK"):
            return (self.conn.lock_result,)
        return (1,)


class _Conn:
    def __init__(self, *, lock_result=1):
        self.executed = []
        self.next_lock_result = lock_result
        self.lock_result = lock_result
        self.released = False

    def cursor(self):
        return _Cursor(self)


class _FakeMySQLDatabase:
    db_type = "mysql"

    def __init__(self, *, lock_result=1):
        self.conn = _Conn(lock_result=lock_result)

    @contextmanager
    def get_connection(self):
        yield self.conn


def test_mysql_lock_uses_contextmanager_and_releases():
    db = _FakeMySQLDatabase(lock_result=1)
    with schema_migration_lock(db, timeout=1):
        assert any("GET_LOCK" in sql for sql in db.conn.executed)
    assert db.conn.released is True


def test_mysql_lock_timeout_raises_and_does_not_leak_context():
    db = _FakeMySQLDatabase(lock_result=0)
    with pytest.raises(RuntimeError, match="迁移锁超时"):
        with schema_migration_lock(db, timeout=1):
            pass
    assert not db.conn.released
