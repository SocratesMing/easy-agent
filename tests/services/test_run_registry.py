"""多副本 Phase 2B：run_registry 租约、启动恢复与周期恢复、异常隔离。"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from easy_agent.services import run_registry
from easy_agent.services.run_registry import (
    RECOVER_ERROR_MESSAGE,
    finish_run,
    new_run_id,
    recover_stale_runs,
    renew_run,
    start_run,
)
from easy_agent.services import session_runtime
from easy_agent.services.session_runtime import instance_owner, is_stream_active
from easy_agent.db.repositories.agent_runs import (
    STATUS_COMPLETED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    get_run,
    start_run as repo_start_run,
)
from easy_agent.db.repositories.stream_events import list_stream_events


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _seed_run(db, run_id: str, session_id: str, *, lease_until: str, owner: str = "pod-a:1"):
    return repo_start_run(
        db,
        run_id,
        session_id,
        "testuser",
        "msg-1",
        "{}",
        owner,
        lease_until,
    )


def _error_event(payload: str) -> dict:
    assert payload.startswith("data: ")
    return json.loads(payload[len("data: ") :].strip())


def test_new_run_id_is_deterministic_and_bounded():
    assert new_run_id("sess", "msg") == "sess:msg"
    assert new_run_id("sess", "") != new_run_id("sess", "")
    assert new_run_id("sess", "").startswith("sess:")
    long_id = new_run_id("s" * 300, "m")
    assert len(long_id) == 255


def test_start_run_registers_running_with_lease(db, monkeypatch):
    monkeypatch.setattr(run_registry, "RUN_LEASE_SECONDS", 60)
    before = datetime.now(UTC)

    assert start_run(
        db,
        "run-1",
        "sess-1",
        username="testuser",
        message_id="msg-1",
        request_json='{"message": "hi"}',
    ) is True

    row = get_run(db, "run-1")
    assert row["status"] == STATUS_RUNNING
    assert row["lease_owner"] == instance_owner()
    assert row["username"] == "testuser"
    assert row["message_id"] == "msg-1"
    assert row["request_json"] == '{"message": "hi"}'

    lease_until = datetime.fromisoformat(row["lease_until"])
    assert before + timedelta(seconds=55) <= lease_until <= before + timedelta(seconds=65)


def test_renew_run_updates_lease_and_rejects_other_owner(db):
    _seed_run(
        db,
        "run-renew",
        "sess-renew",
        lease_until=_iso(datetime.now(UTC) - timedelta(seconds=5)),
    )

    future = datetime.now(UTC) + timedelta(seconds=600)
    assert renew_run(db, "run-renew", owner="pod-a:1", lease_seconds=600) is True
    assert (
        datetime.fromisoformat(get_run(db, "run-renew")["lease_until"])
        >= future - timedelta(seconds=5)
    )

    assert renew_run(
        db, "run-renew", owner="pod-other:9", lease_seconds=600
    ) is False


def test_finish_run_is_idempotent(db):
    start_run(db, "run-finish", "sess-finish")
    assert finish_run(db, "run-finish", STATUS_COMPLETED) is True
    assert get_run(db, "run-finish")["status"] == STATUS_COMPLETED

    # 第二次（或另一 Pod 的恢复）不能覆盖已完成终态
    assert finish_run(db, "run-finish", "failed", "late") is False
    assert get_run(db, "run-finish")["status"] == STATUS_COMPLETED


def test_lease_expiry_then_recover_marks_interrupted_and_writes_error(db, monkeypatch):
    monkeypatch.setattr(run_registry, "RUN_LEASE_SECONDS", 60)
    start_run(
        db,
        "run-stale",
        "sess-stale",
        username="testuser",
        message_id="msg-stale",
    )

    # 租约未过期：不恢复
    assert recover_stale_runs(db, now=datetime.now(UTC)) == 0
    assert get_run(db, "run-stale")["status"] == STATUS_RUNNING
    assert list_stream_events(db, "sess-stale") == []

    # 租约过期：标记 interrupted 并补一条 error 事件
    later = datetime.now(UTC) + timedelta(seconds=120)
    assert recover_stale_runs(db, now=later) == 1

    row = get_run(db, "run-stale")
    assert row["status"] == STATUS_INTERRUPTED
    assert row["error"] == RECOVER_ERROR_MESSAGE
    assert row["lease_until"] == ""

    frames = list_stream_events(db, "sess-stale")
    assert len(frames) == 1
    assert _error_event(frames[0]["payload"]) == {
        "type": "error",
        "content": RECOVER_ERROR_MESSAGE,
    }

    # 再次恢复：已不是 running，不重复写事件
    assert recover_stale_runs(db, now=later) == 0
    assert len(list_stream_events(db, "sess-stale")) == 1


def test_recover_only_touches_expired_running(db):
    future = _iso(datetime.now(UTC) + timedelta(seconds=600))
    past = _iso(datetime.now(UTC) - timedelta(seconds=600))
    _seed_run(db, "run-fresh", "sess-fresh", lease_until=future)
    _seed_run(db, "run-stale", "sess-stale", lease_until=past)
    _seed_run(db, "run-done", "sess-done", lease_until=past)
    finish_run(db, "run-done", STATUS_COMPLETED)

    assert recover_stale_runs(db) == 1
    assert get_run(db, "run-fresh")["status"] == STATUS_RUNNING
    assert get_run(db, "run-stale")["status"] == STATUS_INTERRUPTED
    assert get_run(db, "run-done")["status"] == STATUS_COMPLETED
    # 只给真正被恢复的会话写事件
    assert list_stream_events(db, "sess-fresh") == []
    assert len(list_stream_events(db, "sess-stale")) == 1


def test_recover_competition_writes_event_only_once(db, monkeypatch):
    """同一个 stale run 被两个恢复者看到：只有 mark_interrupted 成功者写事件。"""
    _seed_run(
        db,
        "run-race",
        "sess-race",
        lease_until=_iso(datetime.now(UTC) - timedelta(seconds=5)),
    )
    row = get_run(db, "run-race")
    calls = {"n": 0}
    real_mark = run_registry.agent_runs_repo.mark_interrupted

    def _mark_once(db_arg, run_id, error):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_mark(db_arg, run_id, error)
        return False  # 竞争失败（另一个 Pod 已恢复）

    # 模拟两个恢复者看到同一行：列表里重复一次
    monkeypatch.setattr(
        run_registry.agent_runs_repo,
        "list_stale_running",
        lambda db_arg, now_iso, limit: [row, row],
    )
    monkeypatch.setattr(
        run_registry.agent_runs_repo, "mark_interrupted", _mark_once
    )
    assert recover_stale_runs(db, now=datetime.now(UTC)) == 1
    assert calls["n"] == 2
    assert len(list_stream_events(db, "sess-race")) == 1


def test_service_failures_are_swallowed(db, monkeypatch):
    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("db down")

    monkeypatch.setattr(run_registry.agent_runs_repo, "start_run", _boom)
    assert start_run(db, "run-x", "sess-x") is False

    monkeypatch.setattr(run_registry.agent_runs_repo, "renew_run", _boom)
    assert renew_run(db, "run-x") is False

    monkeypatch.setattr(run_registry.agent_runs_repo, "finish_run", _boom)
    assert finish_run(db, "run-x", STATUS_COMPLETED) is False

    monkeypatch.setattr(run_registry.agent_runs_repo, "list_stale_running", _boom)
    assert recover_stale_runs(db) == 0


def test_recover_truncates_and_keeps_other_runs_on_append_failure(db, monkeypatch):
    """单个 run 的 SSE 写入失败不影响其它 run 的恢复。"""
    past = _iso(datetime.now(UTC) - timedelta(seconds=5))
    _seed_run(db, "run-a", "sess-a", lease_until=past)
    _seed_run(db, "run-b", "sess-b", lease_until=past)

    calls = {"n": 0}
    real_append = run_registry.append_stream_event

    def _append_once(db_arg, session_id, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("write failed")
        return real_append(db_arg, session_id, payload)

    monkeypatch.setattr(run_registry, "append_stream_event", _append_once)
    assert recover_stale_runs(db, now=datetime.now(UTC)) == 2
    assert get_run(db, "run-a")["status"] == STATUS_INTERRUPTED
    assert get_run(db, "run-b")["status"] == STATUS_INTERRUPTED


# ── 崩溃的 Pod 留下的流锁 ──────────────────────────────────────────────


def _hold_stream_lock(db, session_id: str, owner: str, until: str) -> None:
    db.upsert_session_runtime(session_id, stream_owner=owner, stream_until=until)


def test_recover_releases_the_stream_lock_the_crashed_run_left(db, monkeypatch):
    """锁还在 TTL 内、仍归崩溃 run 的实例：照样补 error 事件，并释放锁，会话可以立刻重发。"""
    past = _iso(datetime.now(UTC) - timedelta(seconds=5))
    future = _iso(datetime.now(UTC) + timedelta(minutes=20))
    _seed_run(db, "run-crashed", "sess-crashed", lease_until=past, owner="pod-a:1")
    _hold_stream_lock(db, "sess-crashed", "pod-a:1", future)

    assert recover_stale_runs(db) == 1

    assert get_run(db, "run-crashed")["status"] == STATUS_INTERRUPTED
    frames = list_stream_events(db, "sess-crashed")
    assert [_error_event(f["payload"]) for f in frames] == [
        {"type": "error", "content": RECOVER_ERROR_MESSAGE}
    ]
    assert is_stream_active(db, "sess-crashed") is False
    # 用户照提示重发：别的实例能拿到这个会话的流锁
    monkeypatch.setattr(session_runtime, "instance_owner", lambda: "pod-b:2")
    session_runtime.acquire_stream(db, "sess-crashed")


def test_recover_leaves_a_lock_another_instance_took_over(db):
    past = _iso(datetime.now(UTC) - timedelta(seconds=5))
    future = _iso(datetime.now(UTC) + timedelta(minutes=20))
    _seed_run(db, "run-old", "sess-taken", lease_until=past, owner="pod-a:1")
    _hold_stream_lock(db, "sess-taken", "pod-b:2", future)

    assert recover_stale_runs(db) == 1

    assert get_run(db, "run-old")["status"] == STATUS_INTERRUPTED
    assert list_stream_events(db, "sess-taken") == []
    assert db.get_session_runtime("sess-taken")["stream_owner"] == "pod-b:2"


def test_recover_leaves_a_newer_run_of_the_same_instance_alone(db):
    """同一实例在这个会话上已开了新流（新的 run 租约有效）：锁是新流的，不写事件也不释放。"""
    past = _iso(datetime.now(UTC) - timedelta(seconds=5))
    future = _iso(datetime.now(UTC) + timedelta(minutes=20))
    _seed_run(db, "run-old", "sess-newer", lease_until=past, owner="pod-a:1")
    _seed_run(db, "run-new", "sess-newer", lease_until=future, owner="pod-a:1")
    _hold_stream_lock(db, "sess-newer", "pod-a:1", future)

    assert recover_stale_runs(db) == 1

    assert get_run(db, "run-old")["status"] == STATUS_INTERRUPTED
    assert get_run(db, "run-new")["status"] == STATUS_RUNNING
    assert list_stream_events(db, "sess-newer") == []
    assert db.get_session_runtime("sess-newer")["stream_owner"] == "pod-a:1"


# ── 周期恢复 ────────────────────────────────────────────────────────────


async def test_periodic_recovery_finishes_runs_whose_lease_expires_after_startup(db, monkeypatch):
    """启动那一轮时租约还没过期的 run（新 Pod 起得比租约过期快），由周期任务收尾。"""
    import easy_agent.db as db_package

    monkeypatch.setattr(db_package, "get_database", lambda: db)
    monkeypatch.setattr(run_registry, "_recovery_task", None)
    soon = _iso(datetime.now(UTC) + timedelta(seconds=1))
    _seed_run(db, "run-later", "sess-later", lease_until=soon)
    assert recover_stale_runs(db) == 0

    task = run_registry.start_run_recovery(interval=0.2)
    try:
        for _ in range(60):
            await asyncio.sleep(0.1)
            if get_run(db, "run-later")["status"] == STATUS_INTERRUPTED:
                break
        assert get_run(db, "run-later")["status"] == STATUS_INTERRUPTED
        assert len(list_stream_events(db, "sess-later")) == 1
    finally:
        await run_registry.stop_run_recovery()
    assert task.done()


def _seed_event(db, session_id: str, created_at: str) -> None:
    with db.get_connection() as conn:
        db._execute(
            conn.cursor(),
            "INSERT INTO stream_events (session_id, payload, created_at) VALUES (?, ?, ?)",
            (session_id, "data: {}\n\n", created_at),
        )


def test_purge_removes_only_events_older_than_the_retention(db):
    now = datetime(2026, 10, 27, 12, 0, 0)
    expired = (now - run_registry.STREAM_EVENTS_RETENTION - timedelta(minutes=1)).isoformat()
    recent = (now - timedelta(hours=1)).isoformat()
    for _ in range(3):
        _seed_event(db, "sess-old", expired)
    _seed_event(db, "sess-new", recent)

    # batch=2：3 条过期事件分两批删完，未过期的保留
    assert run_registry.purge_stale_stream_events(db, now, batch=2) == 3
    assert list_stream_events(db, "sess-old") == []
    assert len(list_stream_events(db, "sess-new")) == 1


async def test_periodic_recovery_also_purges_expired_stream_events(db, monkeypatch):
    import easy_agent.db as db_package

    monkeypatch.setattr(db_package, "get_database", lambda: db)
    monkeypatch.setattr(run_registry, "_recovery_task", None)
    expired = (datetime.now() - run_registry.STREAM_EVENTS_RETENTION - timedelta(hours=1)).isoformat()
    _seed_event(db, "sess-stale", expired)

    run_registry.start_run_recovery(interval=0.2)
    try:
        for _ in range(60):
            await asyncio.sleep(0.1)
            if not list_stream_events(db, "sess-stale"):
                break
        assert list_stream_events(db, "sess-stale") == []
    finally:
        await run_registry.stop_run_recovery()


def test_periodic_recovery_is_off_when_the_interval_is_not_positive(monkeypatch):
    monkeypatch.setattr(run_registry, "_recovery_task", None)
    monkeypatch.setenv(run_registry.RUN_RECOVERY_INTERVAL_ENV, "0")

    assert run_registry.start_run_recovery() is None


def test_app_runs_periodic_recovery_while_serving(client):
    task = run_registry._recovery_task
    assert task is not None and not task.done()
    assert task.get_name() == "run-recovery"
