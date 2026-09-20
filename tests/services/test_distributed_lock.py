"""分布式锁（easy_agent/utils/distributed_lock.py）的多实例互斥测试。

用「两个 DistributedLock 实例共享同一个 SQLite 文件」模拟部署在多个 pod 上的
两个进程：数据库是唯一共享状态，锁的互斥语义与 MySQL 部署一致。
"""

import time

import pytest

from easy_agent.config import DistributedLockConfig
from easy_agent.db import Database, init_database
from easy_agent.utils.distributed_lock import DistributedLock, utc_now_iso

LOCK_NAME = "scheduled_task:t-1"


def _config(**overrides) -> DistributedLockConfig:
    params = {"enabled": True, "ttl_seconds": 60, "retry_interval_seconds": 0.05}
    params.update(overrides)
    return DistributedLockConfig(**params)


@pytest.fixture()
def two_pods(tmp_path):
    """(lock_a, lock_b, db)：两个实例共享同一个 SQLite 文件，模拟两个 pod。"""
    path = tmp_path / "shared.db"
    db = init_database({"type": "sqlite", "sqlite": {"path": str(path)}})
    other_db = Database({"type": "sqlite", "sqlite": {"path": str(path)}})
    config = _config()
    return (
        DistributedLock(config=config, db=db, owner="pod-a"),
        DistributedLock(config=config, db=other_db, owner="pod-b"),
        db,
    )


def _force_expire(db, key: str) -> None:
    """把锁行的过期时间改到过去，模拟持有者崩溃后 TTL 到期。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor, "UPDATE distributed_locks SET expires_at=? WHERE lock_key=?",
            ("2000-01-01T00:00:00.000000+00:00", key),
        )


def test_acquire_is_mutually_exclusive(two_pods):
    lock_a, lock_b, _ = two_pods
    handle_a = lock_a.acquire(LOCK_NAME)
    handle_b = lock_b.acquire(LOCK_NAME)

    assert handle_a.acquired is True
    assert handle_b.acquired is False
    assert handle_a.key == "easy_agent:scheduled_task:t-1"
    assert handle_a.owner == "pod-a"


def test_release_only_affects_own_lock(two_pods):
    lock_a, lock_b, _ = two_pods
    lock_a.acquire(LOCK_NAME)
    other = lock_b.acquire(LOCK_NAME)

    assert other.release() is False                     # 未持锁者释放无效
    assert lock_b.acquire(LOCK_NAME).acquired is False  # 锁仍在 A 手里
    assert lock_a.acquire(LOCK_NAME).acquired is False  # 同一实例在释放前不能重入


def test_expired_lock_can_be_taken_over(two_pods):
    lock_a, lock_b, db = two_pods
    handle_a = lock_a.acquire(LOCK_NAME, ttl_seconds=1)
    _force_expire(db, handle_a.key)

    handle_b = lock_b.acquire(LOCK_NAME)

    assert handle_b.acquired is True
    assert handle_a.renew() is False        # 旧持有者续约失败，说明锁已丢失
    assert handle_a.release() is False      # 旧持有者不能删掉新持有者的锁
    assert lock_a.acquire(LOCK_NAME).acquired is False


def test_renew_extends_expiry(two_pods):
    lock_a, _, db = two_pods
    handle = lock_a.acquire(LOCK_NAME, ttl_seconds=30)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor, "SELECT expires_at FROM distributed_locks WHERE lock_key=?",
            (handle.key,),
        )
        before = cursor.fetchone()[0]

    assert handle.renew() is True
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor, "SELECT expires_at FROM distributed_locks WHERE lock_key=?",
            (handle.key,),
        )
        assert cursor.fetchone()[0] > before

    assert handle.release() is True
    assert lock_a.acquire(LOCK_NAME).acquired is True


def test_acquire_waits_until_timeout(two_pods):
    lock_a, lock_b, _ = two_pods
    lock_a.acquire(LOCK_NAME)

    started = time.monotonic()
    handle = lock_b.acquire(LOCK_NAME, wait_seconds=0.2)
    waited = time.monotonic() - started

    assert handle.acquired is False
    assert waited >= 0.2


def test_acquire_with_wait_succeeds_after_release(two_pods):
    lock_a, lock_b, _ = two_pods
    handle_a = lock_a.acquire(LOCK_NAME)
    handle_a.release()

    handle_b = lock_b.acquire(LOCK_NAME, wait_seconds=0.5)

    assert handle_b.acquired is True


def test_locked_context_manager_releases(two_pods):
    lock_a, lock_b, _ = two_pods
    with lock_a.locked(LOCK_NAME) as handle:
        assert handle.acquired is True
        assert lock_b.acquire(LOCK_NAME).acquired is False

    assert lock_b.acquire(LOCK_NAME).acquired is True


def test_cleanup_expired_removes_stale_rows(two_pods):
    lock_a, _, db = two_pods
    handle = lock_a.acquire(LOCK_NAME)
    _force_expire(db, handle.key)

    assert lock_a.cleanup_expired() == 1
    assert lock_a.cleanup_expired() == 0


def test_disabled_lock_is_noop(db):
    lock = DistributedLock(config=_config(enabled=False), db=db, owner="pod-a")

    handle = lock.acquire(LOCK_NAME)

    assert handle.acquired is True
    assert handle.enabled is False
    assert handle.renew() is True
    assert handle.release() is True
    assert lock.cleanup_expired() == 0

    # 关闭时不应产生任何锁行
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT COUNT(*) FROM distributed_locks")
        assert cursor.fetchone()[0] == 0


def test_key_prefix_scopes_lock_keys(db):
    plain = DistributedLock(config=_config(key_prefix=""), db=db, owner="pod-a")
    prefixed = DistributedLock(
        config=_config(key_prefix="other-system"), db=db, owner="pod-b"
    )

    assert plain.acquire(LOCK_NAME).acquired is True
    # 不同前缀是两把互不相干的锁
    assert prefixed.acquire(LOCK_NAME).acquired is True
    assert plain.acquire(LOCK_NAME).acquired is False


def test_utc_now_iso_is_sortable():
    first = utc_now_iso()
    time.sleep(0.001)
    assert utc_now_iso() > first
