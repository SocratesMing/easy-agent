"""多实例（多 pod / 多 worker）部署下的数据库分布式锁。

为什么需要：服务部署到多个实例时，每个实例都会独立跑一份调度/维护逻辑。
例如每个 pod 启动时都会把同一批定时任务注册进 APScheduler，同一个 cron
到点后会在所有 pod 上各触发一次，任务被重复执行。把互斥判断放进共享数据库，
只有抢到锁的实例真正执行，即可避免重复处理。

实现（``distributed_locks`` 表，``lock_key`` 为主键）：

1. 插入锁行（已存在则忽略）—— 插入成功即获锁；
2. 锁行已存在时尝试「抢占过期锁」，即 ``expires_at < now`` 的原子 UPDATE。

两步都是单条 SQL，SQLite 与 MySQL(InnoDB) 下均为原子操作，不依赖
``INSERT ... ON DUPLICATE KEY UPDATE`` 的影响行数语义，两套方言行为一致。

锁带 TTL：持有者崩溃后，其它实例最多等一个 TTL 即可接管；执行时间可能超过
TTL 的长任务需周期性 ``LockHandle.renew()`` 续约，续约失败说明锁已被他人抢占，
调用方应中止处理。要求各实例系统时钟同步（NTP）。

锁不可重入：同一实例在释放前重复 ``acquire`` 同一个 key 会返回未获锁；
互斥按 owner 判定，释放与续约都校验 owner，不会误删其它实例持有的锁。

开关：``config.yaml`` 的 ``distributed_lock.enabled``。未开启时 ``acquire`` 直接
返回「已获锁」的空操作句柄，调用方无需为开关分支，也不产生任何数据库访问。
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from ..config import Config, DistributedLockConfig
from ..db import get_database

logger = logging.getLogger("easy_agent.distributed_lock")

TABLE_NAME = "distributed_locks"


def utc_now_iso() -> str:
    """UTC 时间戳（固定微秒精度）：字符串字典序即时间先后，可直接在 SQL 中比较。"""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _utc_after(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(
        timespec="microseconds"
    )


class LockHandle:
    """一次抢锁的结果。

    ``acquired=False`` 表示锁正被其它实例持有，调用方应跳过本次处理。
    未启用分布式锁时 ``acquired=True`` 且 ``enabled=False``，``renew`` / ``release``
    都是空操作，调用方无需为开关写分支。
    """

    def __init__(self, lock, key, owner, acquired, enabled, ttl_seconds):
        self._lock = lock
        self.key = key
        self.owner = owner
        self.acquired = acquired
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds

    @property
    def renew_interval_seconds(self) -> float:
        """续约间隔：取配置值与 ttl/3 的较小者，保证一个 TTL 内至少续约两次。"""
        configured = float(self._lock.config.renew_interval_seconds)
        return max(1.0, min(configured, self.ttl_seconds / 3.0))

    def renew(self) -> bool:
        """续约一个 TTL；返回 False 表示锁已丢失（已过期或被其它实例抢占）。"""
        if not self.acquired:
            return False
        if not self.enabled:
            return True
        return self._lock.renew_key(self.key, self.owner, self.ttl_seconds)

    def release(self) -> bool:
        """释放锁；仅当锁仍属于自己时才删除，避免误删其它实例持有的锁。"""
        if not self.acquired:
            return False
        self.acquired = False
        if not self.enabled:
            return True
        return self._lock.release_key(self.key, self.owner)

    def __repr__(self) -> str:
        return (
            f"LockHandle(key={self.key!r}, acquired={self.acquired}, "
            f"enabled={self.enabled}, owner={self.owner!r})"
        )


class DistributedLock:
    """数据库锁表实现的分布式锁。

    ``owner`` 标识当前实例（主机名-进程号-随机串），续约与释放都会校验 owner，
    避免把其它实例持有的锁删掉。
    """

    def __init__(
        self,
        config: DistributedLockConfig | None = None,
        db=None,
        owner: str | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self.owner = owner or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )

    @property
    def config(self) -> DistributedLockConfig:
        if self._config is None:
            self._config = load_distributed_lock_config()
        return self._config

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    @property
    def db(self):
        if self._db is None:
            self._db = get_database()
        return self._db

    def scoped_key(self, name: str) -> str:
        """拼上配置里的 ``key_prefix``：多套系统共用一个库时避免键冲突。"""
        prefix = (self.config.key_prefix or "").strip(": ")
        return f"{prefix}:{name}" if prefix else name

    def acquire(
        self, name: str, ttl_seconds: float | None = None, wait_seconds: float = 0.0
    ) -> LockHandle:
        """尝试获取名为 ``name`` 的锁。

        ``wait_seconds`` > 0 时按 ``retry_interval_seconds`` 轮询等待，直到超时或
        抢到锁；默认 0 表示只抢一次，抢不到立即返回（定时任务用这种语义）。
        """
        ttl = float(ttl_seconds or self.config.ttl_seconds)
        key = self.scoped_key(name)
        if not self.enabled:
            return LockHandle(
                self, key=key, owner=self.owner, acquired=True, enabled=False,
                ttl_seconds=ttl,
            )

        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        while True:
            acquired = self._try_acquire(key, ttl)
            if acquired or time.monotonic() >= deadline:
                return LockHandle(
                    self, key=key, owner=self.owner, acquired=acquired, enabled=True,
                    ttl_seconds=ttl,
                )
            time.sleep(max(0.05, float(self.config.retry_interval_seconds)))

    @contextmanager
    def locked(self, name: str, ttl_seconds: float | None = None, wait_seconds: float = 0.0):
        """``with lock.locked("xxx") as handle:`` 简写，退出时自动释放。"""
        handle = self.acquire(name, ttl_seconds=ttl_seconds, wait_seconds=wait_seconds)
        try:
            yield handle
        finally:
            handle.release()

    def renew_key(self, key: str, owner: str, ttl_seconds: float) -> bool:
        """把锁的过期时间后推一个 TTL（仅限自己仍持有且未过期时）。"""
        now = utc_now_iso()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                UPDATE {TABLE_NAME}
                SET expires_at=?, updated_at=?
                WHERE lock_key=? AND owner=? AND expires_at > ?
                """,
                (_utc_after(ttl_seconds), now, key, owner, now),
            )
            return cursor.rowcount == 1

    def release_key(self, key: str, owner: str) -> bool:
        """删除自己持有的锁行；锁已被他人接管时不做任何事。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"DELETE FROM {TABLE_NAME} WHERE lock_key=? AND owner=?",
                (key, owner),
            )
            return cursor.rowcount > 0

    def cleanup_expired(self) -> int:
        """清理过期的锁行（启动时调用一次即可，避免一次性 key 长期堆积）。"""
        if not self.enabled:
            return 0
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, f"DELETE FROM {TABLE_NAME} WHERE expires_at < ?",
                (utc_now_iso(),),
            )
            removed = cursor.rowcount
        if removed:
            logger.info(f"清理过期分布式锁 {removed} 条")
        return removed

    def _try_acquire(self, key: str, ttl_seconds: float) -> bool:
        now = utc_now_iso()
        expires_at = _utc_after(ttl_seconds)
        insert = (
            f"INSERT OR IGNORE INTO {TABLE_NAME}"
            if self.db.db_type == "sqlite"
            else f"INSERT IGNORE INTO {TABLE_NAME}"
        )
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                {insert} (lock_key, owner, acquired_at, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key, self.owner, now, expires_at, now),
            )
            if cursor.rowcount == 1:
                return True

            # 锁行已存在：只有「已过期」才能抢占。UPDATE 的 WHERE 条件与行锁
            # 保证多个实例同时抢占时只有一个能把 owner 改写成自己。
            self.db._execute(
                cursor,
                f"""
                UPDATE {TABLE_NAME}
                SET owner=?, acquired_at=?, expires_at=?, updated_at=?
                WHERE lock_key=? AND expires_at < ?
                """,
                (self.owner, now, expires_at, now, key, now),
            )
            return cursor.rowcount == 1


_lock_instance: DistributedLock | None = None


def load_distributed_lock_config() -> DistributedLockConfig:
    """读取 ``distributed_lock`` 配置段；读取失败按「未启用」降级，不阻断主流程。"""
    try:
        return Config.load().distributed_lock
    except Exception as e:
        logger.warning(f"读取 distributed_lock 配置失败，按未启用处理: {e}")
        return DistributedLockConfig()


def get_distributed_lock() -> DistributedLock:
    """进程内共享的分布式锁实例（配置与数据库连接都按需懒加载）。"""
    global _lock_instance
    if _lock_instance is None:
        _lock_instance = DistributedLock()
    return _lock_instance


def reset_distributed_lock() -> None:
    """重置单例（测试用）。"""
    global _lock_instance
    _lock_instance = None
