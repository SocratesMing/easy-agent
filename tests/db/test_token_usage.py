"""真实 SQLite 账本聚合、窗口边界、升级和跨连接快照。"""

import sqlite3
import os
import uuid
from datetime import datetime

import pytest

from easy_agent.db.database import Database
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.db.schema import EXPECTED_TABLES, SCHEMA_VERSION
from easy_agent.models.db import SessionModel

AS_OF = int(datetime.fromisoformat("2026-10-30T04:00:00+00:00").timestamp() * 1000)


def stamp(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def record(
    repo,
    event="event",
    user="alice-id",
    at=AS_OF - 1,
    input=10,
    output=2,
    key="default",
    name="deepseek-flash",
    session=None,
):
    repo.record_usage(
        event_id=event,
        user_id=user,
        session_id=session,
        model_key=key,
        model_name=name,
        completed_at_ms=at,
        input_tokens=input,
        output_tokens=output,
    )


def fixed(repo):
    entries = [
        ("10-30", "alice-id", "default", 1000, 200),
        ("10-29", "alice-id", "secondary", 2000, 300),
        ("10-24", "alice-id", "default", 3000, 500),
        ("10-23", "alice-id", "default", 4000, 600),
        ("10-01", "alice-id", "secondary", 5000, 700),
        ("09-30", "alice-id", "default", 6000, 800),
        ("08-31", "alice-id", "default", 7000, 900),
        ("10-30", "bob-id", "default", 90000, 10000),
    ]
    for index, (date, user, key, incoming, outgoing) in enumerate(entries):
        at = stamp(f"2026-{date}T{'11' if date == '10-30' else '12'}:00:00+08:00")
        record(
            repo,
            str(index),
            user,
            at,
            incoming,
            outgoing,
            key,
            "model-b" if key == "secondary" else "deepseek-flash",
        )


@pytest.mark.parametrize("days,model_totals", [(7, [4700, 2300]), (30, [9300, 8000])])
def test_fixed_data_and_user_isolation(db, days, model_totals):
    repo = TokenUsageRepository(db)
    fixed(repo)
    result = repo.query("alice-id", days, AS_OF)
    assert result["summary"] == dict(
        today=1200, last_7_days=7000, last_30_days=17300, all_time=32000
    )
    assert result["as_of"] == "2026-10-30T04:00:00.000Z"
    assert result["timezone"] == "Asia/Shanghai"
    daily = result["daily"]
    assert len(daily["dates"]) == days
    assert daily["dates"][0] == ("2026-10-24" if days == 7 else "2026-10-01")
    assert daily["dates"][-1] == "2026-10-30"
    assert [sum(s["values"]) for s in daily["series"]] == model_totals
    assert all(
        total == sum(s["values"][i] for s in daily["series"])
        for i, total in enumerate(daily["totals"])
    )
    assert sum(daily["totals"]) == result["summary"][f"last_{days}_days"]
    if days == 7:
        assert daily["totals"] == [3500, 0, 0, 0, 0, 2300, 1200]
    assert set(repo.query("bob-id", days, AS_OF)["summary"].values()) == {100000}


def test_zero_empty_and_days_validation(db):
    repo = TokenUsageRepository(db)
    record(repo, input=0, output=0)
    result = repo.query("alice-id", 30, AS_OF)
    assert set(result["summary"].values()) == {0}
    assert result["daily"]["series"] == []
    assert result["daily"]["totals"] == [0] * 30
    assert repo.query("missing", 7, AS_OF)["daily"]["totals"] == [0] * 7
    with pytest.raises(ValueError):
        repo.query("alice-id", 1)


@pytest.mark.parametrize("bad", [True, None, -1, 1.2, "1"])
def test_invalid_counts_are_rejected(db, bad):
    with pytest.raises(ValueError):
        record(TokenUsageRepository(db), input=bad)


def test_midnight_inclusive_as_of_exclusive_and_recorded_completion_date(db):
    repo = TokenUsageRepository(db)
    midnight = stamp("2026-10-30T00:00:00+08:00")
    for event, at in (
        ("before", midnight - 1),
        ("midnight", midnight),
        ("asof", AS_OF),
        ("future", AS_OF + 1),
    ):
        record(repo, event, at=at)
    result = repo.query("alice-id", 7, AS_OF)
    assert result["summary"] == dict(today=12, last_7_days=24, last_30_days=24, all_time=24)
    assert result["daily"]["totals"][-2:] == [12, 12]
    with db.get_connection() as conn:
        row = conn.execute("SELECT usage_date FROM token_usage WHERE event_id='before'").fetchone()
        assert row[0] == "2026-10-29"


def test_exact_seven_and_thirty_day_start_boundaries(db):
    repo = TokenUsageRepository(db)
    for event, at in (
        ("seven-before", "2026-10-23T23:59:59.999+08:00"),
        ("seven", "2026-10-24T00:00:00+08:00"),
        ("thirty-before", "2026-09-30T23:59:59.999+08:00"),
        ("thirty", "2026-10-01T00:00:00+08:00"),
    ):
        record(repo, event, at=stamp(at))
    result = repo.query("alice-id", 30, AS_OF)
    assert result["summary"] == dict(today=0, last_7_days=12, last_30_days=36, all_time=48)


def test_idempotency_case_sensitive_identity_and_series_order(db):
    repo = TokenUsageRepository(db)
    record(repo, "lc:A", key="A", name="Model", input=7, output=3)
    record(repo, "lc:A", key="changed", input=999)
    record(repo, "lc:a", key="a", name="Model", input=7, output=3)
    record(repo, "lc:b", key="A", name="model", input=7, output=3)
    result = repo.query("alice-id", 7, AS_OF)
    assert result["summary"]["all_time"] == 30
    assert [(s["model_key"], s["model_name"]) for s in result["daily"]["series"]] == [
        ("A", "Model"),
        ("A", "model"),
        ("a", "Model"),
    ]


def test_sqlite_read_snapshot_excludes_write_between_queries(db, monkeypatch):
    repo = TokenUsageRepository(db)
    record(repo, "old")
    execute = db._execute
    inserted = False

    def interleave(cursor, sql, params=None):
        nonlocal inserted
        if "GROUP BY usage_date" in sql and not inserted:
            with sqlite3.connect(db.db_path) as other:
                other.execute(
                    "INSERT INTO token_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "concurrent",
                        "alice-id",
                        None,
                        "default",
                        "deepseek-flash",
                        AS_OF - 2,
                        "2026-10-30",
                        20,
                        3,
                    ),
                )
            inserted = True
        return execute(cursor, sql, params)

    monkeypatch.setattr(db, "_execute", interleave)
    first = repo.query("alice-id", 7, AS_OF)
    assert first["summary"]["today"] == sum(first["daily"]["totals"]) == 12
    second = repo.query("alice-id", 7, AS_OF)
    assert second["summary"]["today"] == sum(second["daily"]["totals"]) == 35


def test_upgrade_restart_and_session_deletion_preserve_ledger(tmp_path):
    path = str(tmp_path / "usage.db")
    config = {"type": "sqlite", "sqlite": {"path": path}}
    db = Database(config)
    db.init_tables()
    assert SCHEMA_VERSION >= 17 and "token_usage" in EXPECTED_TABLES
    sessions = SessionRepository(db)
    sessions.create_session(
        SessionModel(session_id="deleted-session", title="Temporary", username="alice")
    )
    record(TokenUsageRepository(db), session="deleted-session")
    sessions.set_session_archived("deleted-session", "alice", True)
    assert TokenUsageRepository(db).query("alice-id", 7, AS_OF)["summary"]["all_time"] == 12
    assert sessions.delete_session("deleted-session")
    assert sessions.get_session("deleted-session") is None
    with db.get_connection() as conn:
        conn.execute("UPDATE schema_meta SET meta_value='16' WHERE meta_key='schema_version'")
    db.close()
    restarted = Database(config)
    restarted.init_tables()
    try:
        assert (
            TokenUsageRepository(restarted).query("alice-id", 7, AS_OF)["summary"]["all_time"] == 12
        )
        with restarted.get_connection() as conn:
            assert (
                int(
                    conn.execute(
                        "SELECT meta_value FROM schema_meta WHERE meta_key='schema_version'"
                    ).fetchone()[0]
                )
                == SCHEMA_VERSION
            )
            assert {row[1] for row in conn.execute("PRAGMA index_list(token_usage)")} >= {
                "idx_token_usage_user_completed",
                "idx_token_usage_user_date",
            }
    finally:
        restarted.close()


@pytest.fixture
def mysql_usage_db(monkeypatch):
    """与现有 MySQL 集成测试共用显式环境开关，始终使用独立临时库。"""
    host = os.getenv("EASY_TEST_MYSQL_HOST")
    if not host:
        pytest.skip("EASY_TEST_MYSQL_HOST is not configured; real MySQL not verified")
    import pymysql

    settings = dict(
        host=host,
        port=int(os.getenv("EASY_TEST_MYSQL_PORT", "3306")),
        user=os.getenv("EASY_TEST_MYSQL_USER", "root"),
        password=os.getenv("EASY_TEST_MYSQL_PASSWORD", ""),
        charset="utf8mb4",
    )
    admin = pymysql.connect(**settings, autocommit=True)
    name = "test_token_usage_" + uuid.uuid4().hex
    db = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        db = Database({"type": "mysql", "mysql": {**settings, "database": name}})
        assert db.db_type == "mysql", "MySQL test must never silently fall back to SQLite"
        db.init_tables()
        original_get = db._get_mysql_connection

        def read_committed_connection():
            conn = original_get()
            with conn.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
            return conn

        monkeypatch.setattr(db, "_get_mysql_connection", read_committed_connection)
        yield db
    finally:
        if db is not None:
            db.close()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()


def test_real_mysql_fixed_data_case_identity_and_read_committed_snapshot(
    mysql_usage_db, monkeypatch
):
    db = mysql_usage_db
    repo = TokenUsageRepository(db)
    fixed(repo)
    for days in (7, 30):
        result = repo.query("alice-id", days, AS_OF)
        assert result["summary"] == dict(
            today=1200, last_7_days=7000, last_30_days=17300, all_time=32000
        )
        assert len(result["daily"]["dates"]) == days
        assert sum(result["daily"]["totals"]) == result["summary"][f"last_{days}_days"]
    record(repo, "lc:Case", user="case-user", key="A", name="Model")
    record(repo, "lc:Case", user="case-user", input=999)
    record(repo, "lc:case", user="case-user", key="a", name="Model")
    record(repo, "lc:name", user="case-user", key="A", name="model")
    cases = repo.query("case-user", 7, AS_OF)
    assert cases["summary"]["today"] == 36
    assert [(s["model_key"], s["model_name"]) for s in cases["daily"]["series"]] == [
        ("A", "Model"),
        ("A", "model"),
        ("a", "Model"),
    ]

    execute = db._execute
    inserted = False

    def interleave(cursor, sql, params=None):
        nonlocal inserted
        if "GROUP BY usage_date" in sql and not inserted:
            inserted = True
            record(repo, "between-queries", input=20, output=3)
        return execute(cursor, sql, params)

    monkeypatch.setattr(db, "_execute", interleave)
    result = repo.query("alice-id", 7, AS_OF)
    assert result["summary"]["today"] == 1200
    assert sum(result["daily"]["totals"]) == 7000
    result = repo.query("alice-id", 7, AS_OF)
    assert result["summary"]["today"] == 1223
    assert sum(result["daily"]["totals"]) == 7023

    conn = db._get_mysql_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='token_usage'"
            )
            assert cursor.fetchone()["ENGINE"] == "InnoDB"
    finally:
        conn.close()


def test_failed_read_rolls_back_snapshot_and_allows_next_query(db, monkeypatch):
    repo = TokenUsageRepository(db)
    record(repo)
    execute = db._execute

    def fail_daily(cursor, sql, params=None):
        if "GROUP BY usage_date" in sql:
            raise sqlite3.OperationalError("simulated read failure")
        return execute(cursor, sql, params)

    monkeypatch.setattr(db, "_execute", fail_daily)
    with pytest.raises(sqlite3.OperationalError):
        repo.query("alice-id", 7, AS_OF)
    assert not db._connection.in_transaction
    monkeypatch.setattr(db, "_execute", execute)
    assert repo.query("alice-id", 7, AS_OF)["summary"]["today"] == 12
