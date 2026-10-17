"""会话流锁原子性测试（T2 并发问题 / CONC-01）。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from easy_agent.db.database import Database
from easy_agent.services.session_runtime import (
    SessionBusyError,
    acquire_stream,
    release_stream,
)


def _db(tmp_path) -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "stream.db")}})
    db.init_tables()
    return db


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _window(minutes: int = 5):
    now = datetime.now(timezone.utc)
    return _iso(now), _iso(now + timedelta(minutes=minutes))


def test_atomic_acquire_and_busy(tmp_path):
    db = _db(tmp_path)
    now, until = _window()
    assert db.try_acquire_session_stream("s1", "A", until, now)[0] is True

    ok, row = db.try_acquire_session_stream("s1", "B", until, now)
    assert ok is False
    assert row["stream_owner"] == "A"


def test_expired_lock_can_be_taken(tmp_path):
    db = _db(tmp_path)
    now, _ = _window()
    # 手动写入一个已过期锁
    db.upsert_session_runtime(
        "s2", stream_owner="old", stream_until="2000-01-01T00:00:00+00:00"
    )
    until = _iso(datetime.now(timezone.utc) + timedelta(minutes=5))
    ok, _row = db.try_acquire_session_stream("s2", "new", until, now)
    assert ok is True
    assert db.get_session_runtime("s2")["stream_owner"] == "new"


def test_concurrent_acquire_has_single_winner(tmp_path):
    db = _db(tmp_path)
    now, until = _window()

    def worker(i: int) -> bool:
        return db.try_acquire_session_stream("s3", f"owner-{i}", until, now)[0]

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(worker, range(20)))
    assert sum(1 for item in results if item) == 1


def test_acquire_stream_raises_for_other_instance(tmp_path):
    db = _db(tmp_path)
    _, until = _window()
    db.upsert_session_runtime("s4", stream_owner="other-host:123", stream_until=until)
    with pytest.raises(SessionBusyError):
        acquire_stream(db, "s4")


def test_release_allows_reacquire(tmp_path):
    db = _db(tmp_path)
    owner = acquire_stream(db, "s5")
    assert owner
    release_stream(db, "s5")
    acquire_stream(db, "s5")


def test_renew_only_by_current_owner(tmp_path):
    db = _db(tmp_path)
    now, until = _window()
    assert db.try_acquire_session_stream("s6", "A", until, now)[0] is True
    later = _iso(datetime.now(timezone.utc) + timedelta(minutes=10))
    assert db.renew_session_stream("s6", "A", later, now) is True
    assert db.get_session_runtime("s6")["stream_until"] == later
    assert db.renew_session_stream("s6", "B", later, now) is False


def test_session_runtime_renew_fails_after_takeover(tmp_path):
    from easy_agent.services.session_runtime import renew_stream

    db = _db(tmp_path)
    acquire_stream(db, "s7")
    assert renew_stream(db, "s7") is True

    # 模拟被其它实例接管
    _, until = _window()
    db.upsert_session_runtime("s7", stream_owner="other-host:1", stream_until=until)
    assert renew_stream(db, "s7") is False
