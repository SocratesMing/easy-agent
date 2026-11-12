"""T5：连接归还由 Database 负责，清理失败不得覆盖原异常。"""

import logging

import pytest

from easy_agent.db.database import Database
from easy_agent.db.repositories.token_usage import TokenUsageRepository


class PoolConnection:
    def __init__(self, *, commit_error=None, rollback_error=None, close_error=None):
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.calls = []

    def commit(self):
        self.calls.append("commit")
        if self.commit_error:
            raise self.commit_error

    def rollback(self):
        self.calls.append("rollback")
        if self.rollback_error:
            raise self.rollback_error

    def close(self):
        self.calls.append("close")
        if self.close_error:
            raise self.close_error


def mysql_database(monkeypatch, connection):
    db = Database()
    db.db_type = "mysql"
    monkeypatch.setattr(db, "_get_connection", lambda: connection)
    return db


@pytest.mark.parametrize("failure", [None, "query", "commit", "cancel"])
def test_mysql_returns_connection_once_after_transaction(monkeypatch, failure):
    error = KeyboardInterrupt("cancelled") if failure == "cancel" else RuntimeError("failed")
    conn = PoolConnection(commit_error=error if failure == "commit" else None)
    db = mysql_database(monkeypatch, conn)

    def transact():
        with db.get_connection() as borrowed:
            assert borrowed is conn
            if failure in ("query", "cancel"):
                raise error

    if failure:
        with pytest.raises(type(error)) as caught:
            transact()
        assert caught.value is error
    else:
        transact()
    assert conn.calls == (["commit", "close"] if failure is None else
                          ["commit", "rollback", "close"] if failure == "commit" else
                          ["rollback", "close"])


@pytest.mark.parametrize("rollback_fails,close_fails", [(True, False), (False, True), (True, True)])
def test_mysql_cleanup_logs_failures_and_preserves_query_error(
    monkeypatch, caplog, rollback_fails, close_fails,
):
    original = ValueError("query error")
    conn = PoolConnection(
        rollback_error=RuntimeError("rollback error") if rollback_fails else None,
        close_error=RuntimeError("close error") if close_fails else None,
    )
    db = mysql_database(monkeypatch, conn)
    with caplog.at_level(logging.ERROR, logger="easy_agent.db.database"):
        with pytest.raises(ValueError) as caught:
            with db.get_connection():
                raise original
    assert caught.value is original
    assert conn.calls == ["rollback", "close"]
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == int(rollback_fails) + int(close_fails)
    assert all(record.exc_info for record in errors)


def test_mysql_close_failure_after_success_is_visible(monkeypatch):
    error = RuntimeError("close error")
    conn = PoolConnection(close_error=error)
    db = mysql_database(monkeypatch, conn)
    with pytest.raises(RuntimeError) as caught:
        with db.get_connection():
            pass
    assert caught.value is error
    assert conn.calls == ["commit", "close"]


def test_sqlite_connection_is_shared_and_survives_rollback(db):
    with db.get_connection() as first:
        first.execute("CREATE TABLE lifecycle_test (value INTEGER)")
        first.execute("INSERT INTO lifecycle_test VALUES (1)")
    with pytest.raises(ValueError):
        with db.get_connection() as second:
            assert second is first
            second.execute("INSERT INTO lifecycle_test VALUES (2)")
            raise ValueError("abort")
    with db.get_connection() as third:
        assert third is first
        assert [row[0] for row in third.execute("SELECT value FROM lifecycle_test")] == [1]


def test_usage_repository_returns_mysql_connection_once(monkeypatch):
    conn = PoolConnection()
    db = mysql_database(monkeypatch, conn)
    cursor = type("Cursor", (), {"close": lambda self: None})()
    monkeypatch.setattr(conn, "cursor", lambda: cursor, raising=False)
    monkeypatch.setattr(db, "_execute", lambda *args: None)
    TokenUsageRepository(db).record_usage(
        event_id="event", user_id="user", session_id=None, model_key="model",
        model_name="model", completed_at_ms=1, input_tokens=2, output_tokens=3,
    )
    assert conn.calls == ["commit", "close"]
