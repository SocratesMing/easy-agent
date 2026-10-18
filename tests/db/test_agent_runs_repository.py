"""多副本 Phase 2B：``agent_runs`` repository 的租约/终态/竞争语义。"""

from types import SimpleNamespace

from easy_agent.db import schema as schema_module
from easy_agent.db.repositories.agent_runs import (
    STATUS_COMPLETED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    finish_run,
    get_run,
    list_stale_running,
    mark_interrupted,
    renew_run,
    start_run,
)
from easy_agent.db.schema import EXPECTED_TABLES, SCHEMA_VERSION

RUN_ID = "sess-1:msg-1"
PAST = "2020-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"
NOW = "2026-10-18T00:00:00+00:00"


def _start(db, run_id: str = RUN_ID, lease_until: str = FUTURE, **overrides):
    values = {
        "session_id": "sess-1",
        "username": "testuser",
        "message_id": "msg-1",
        "request_json": '{"message": "hi"}',
        "owner": "pod-a:1",
        "lease_until": lease_until,
    }
    values.update(overrides)
    return start_run(db, run_id, **values)


def test_schema_declares_agent_runs_table():
    assert SCHEMA_VERSION >= 9
    assert "agent_runs" in EXPECTED_TABLES


def test_start_run_inserts_running_row(db):
    assert _start(db) is True

    row = get_run(db, RUN_ID)
    assert row is not None
    assert row["run_id"] == RUN_ID
    assert row["session_id"] == "sess-1"
    assert row["username"] == "testuser"
    assert row["message_id"] == "msg-1"
    assert row["status"] == STATUS_RUNNING
    assert row["request_json"] == '{"message": "hi"}'
    assert row["lease_owner"] == "pod-a:1"
    assert row["lease_until"] == FUTURE
    assert row["error"] in (None, "")
    assert row["created_at"] and row["updated_at"]


def test_start_run_is_idempotent_and_does_not_overwrite(db):
    assert _start(db) is True
    finish_run(db, RUN_ID, STATUS_COMPLETED)

    # 同 run_id 重复登记：不插入、不把 completed 改回 running
    assert _start(db, lease_until=PAST) is False
    row = get_run(db, RUN_ID)
    assert row["status"] == STATUS_COMPLETED
    assert row["lease_until"] == ""


def test_renew_run_requires_owner_and_running(db):
    _start(db, lease_until=PAST)

    assert renew_run(db, RUN_ID, "pod-a:1", FUTURE) is True
    assert get_run(db, RUN_ID)["lease_until"] == FUTURE

    # 其它 Pod 不能续约
    assert renew_run(db, RUN_ID, "pod-b:2", PAST) is False
    assert get_run(db, RUN_ID)["lease_until"] == FUTURE

    # 已落终态的 run 不再续约
    finish_run(db, RUN_ID, STATUS_COMPLETED)
    assert renew_run(db, RUN_ID, "pod-a:1", FUTURE) is False


def test_finish_run_is_atomic_and_idempotent(db):
    _start(db)

    assert finish_run(db, RUN_ID, STATUS_COMPLETED) is True
    row = get_run(db, RUN_ID)
    assert row["status"] == STATUS_COMPLETED
    assert row["lease_until"] == ""
    assert row["error"] in (None, "")

    # 第二次 finish 不覆盖既有终态
    assert finish_run(db, RUN_ID, "failed", "late failure") is False
    row = get_run(db, RUN_ID)
    assert row["status"] == STATUS_COMPLETED
    assert row["error"] in (None, "")

    # 不存在的 run
    assert finish_run(db, "missing", STATUS_COMPLETED) is False


def test_finish_run_stores_failed_error(db):
    _start(db)
    assert finish_run(db, RUN_ID, "failed", "RuntimeError: boom") is True
    row = get_run(db, RUN_ID)
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError: boom"


def test_list_stale_running_filters_and_orders(db):
    _start(db, "run-expired-2", lease_until="2026-10-17T00:00:00+00:00")
    _start(db, "run-expired-1", lease_until="2026-10-16T00:00:00+00:00")
    _start(db, "run-fresh", lease_until=FUTURE)
    finish_run(db, "run-expired-1", STATUS_COMPLETED)
    _start(db, "run-other-session", lease_until="2026-10-15T00:00:00+00:00")

    rows = list_stale_running(db, NOW)
    ids = [row["run_id"] for row in rows]
    # 只含 running 且 lease_until < now；按 lease_until 升序
    assert ids == ["run-other-session", "run-expired-2"]

    limited = list_stale_running(db, NOW, limit=1)
    assert [row["run_id"] for row in limited] == ["run-other-session"]
    assert list_stale_running(db, NOW, limit=0) == []
    assert list_stale_running(db, "1999-01-01T00:00:00+00:00") == []


def test_mark_interrupted_only_one_winner(db):
    _start(db, lease_until=PAST)

    assert mark_interrupted(db, RUN_ID, "instance gone") is True
    # 第二个 Pod（同一 stale run）竞争失败，不能重复写终态/事件
    assert mark_interrupted(db, RUN_ID, "instance gone") is False

    row = get_run(db, RUN_ID)
    assert row["status"] == STATUS_INTERRUPTED
    assert row["error"] == "instance gone"
    assert row["lease_until"] == ""
    # interrupted 是终态：finish 不能再覆盖
    assert finish_run(db, RUN_ID, STATUS_COMPLETED) is False


def test_database_delegations_are_wired(db):
    assert db.start_run(
        "run-via-db", "sess-db", "u", "m", "{}", "pod-a:1", FUTURE
    ) is True
    assert db.renew_run("run-via-db", "pod-a:1", FUTURE) is True
    assert db.get_run("run-via-db")["status"] == STATUS_RUNNING
    assert [r["run_id"] for r in db.list_stale_running(NOW)] == []
    assert db.finish_run("run-via-db", STATUS_COMPLETED) is True
    assert db.mark_interrupted("run-via-db", "x") is False


def test_mysql_ddl_uses_longtext_and_both_indexes():
    """双栈建表口径：MySQL 必须 LONGTEXT，索引 (session_id, created_at) /
    (status, lease_until) 都要建。"""
    executed: list[str] = []

    class _Cursor:
        def execute(self, sql, params=None):  # noqa: ANN001
            executed.append(sql)

    fake_db = SimpleNamespace(db_type="mysql")
    schema_module._create_agent_runs_table(fake_db, _Cursor())

    ddl = "\n".join(executed)
    assert "CREATE TABLE IF NOT EXISTS agent_runs" in ddl
    assert "request_json LONGTEXT" in ddl
    assert "CREATE INDEX idx_agent_runs_session ON agent_runs(session_id, created_at)" in ddl
    assert "CREATE INDEX idx_agent_runs_status ON agent_runs(status, lease_until)" in ddl
