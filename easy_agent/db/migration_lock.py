"""跨进程/跨 Pod 的数据库 schema 迁移锁（多副本 Phase 3）。

`init_tables()` 会执行建表/补列/数据自愈；多个 Pod 同时启动时必须有且只有一个
执行迁移，否则 MySQL DDL 并发会产生重复列/锁等待甚至半迁移状态。

- MySQL：使用 `GET_LOCK('easy_agent_schema_migration', timeout)`（连接级锁，
  连接释放自动释放）；
- SQLite：使用数据库旁的 `.migrate.lock` 文件 + `fcntl.flock`（单机多进程）；
- Windows/无 fcntl：降级为进程内 threading 锁并 warning（生产多副本用 MySQL）。

迁移锁超时由 `EASY_MIGRATION_LOCK_TIMEOUT` 控制，默认 120 秒；超时抛
RuntimeError，启动失败而不是带病运行。
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from pathlib import Path

from easy_agent.bootstrap.env import env_int

logger = logging.getLogger("easy_agent.db.migration_lock")

LOCK_NAME = "easy_agent_schema_migration"
DEFAULT_TIMEOUT_SECONDS = 120
_POLL_SECONDS = 0.2

# SQLite/Windows 进程内降级锁：只保护同进程线程；跨进程依赖 flock。
_thread_lock = threading.Lock()


def _timeout() -> int:
    try:
        return max(1, int(env_int("EASY_MIGRATION_LOCK_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


@contextlib.contextmanager
def schema_migration_lock(db, timeout: int | None = None):
    """获取迁移锁；yield 期间保证同库只有一个迁移者。"""
    seconds = _timeout() if timeout is None else max(1, int(timeout))
    if getattr(db, "db_type", "") == "mysql":
        with _mysql_lock(db, seconds):
            yield
        return
    with _sqlite_lock(db, seconds):
        yield


@contextlib.contextmanager
def _mysql_lock(db, timeout: int):
    acquired = False
    # Database.get_connection() 是 contextmanager（MySQL 下自动 commit/rollback，
    # 并把连接归还连接池）。这里必须 with 进入，不能把生成器当连接用。
    with db.get_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT GET_LOCK(%s, %s)", (LOCK_NAME, timeout))
            row = cursor.fetchone()
            value = row[0] if not isinstance(row, dict) else next(iter(row.values()))
            acquired = str(value) == "1"
            if not acquired:
                raise RuntimeError(
                    f"等待数据库迁移锁超时（{timeout}s）：另一个实例正在迁移，"
                    "请检查是否有卡住的 migrate 进程"
                )
            logger.info("已获取 MySQL 迁移锁 | name=%s | timeout=%ss", LOCK_NAME, timeout)
            yield
        finally:
            if acquired:
                try:
                    release_cursor = conn.cursor()
                    release_cursor.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
                    release_cursor.fetchone()
                except Exception as exc:  # noqa: BLE001 - 连接归还/关闭会兜底
                    logger.warning("释放 MySQL 迁移锁失败（连接关闭会兜底）: %s", exc)


@contextlib.contextmanager
def _sqlite_lock(db, timeout: int):
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "当前平台无 fcntl，SQLite 迁移锁降级为进程内锁（仅单进程安全）"
        )
        if not _thread_lock.acquire(timeout=timeout):
            raise RuntimeError(f"等待进程内迁移锁超时（{timeout}s）")
        try:
            yield
        finally:
            _thread_lock.release()
        return

    db_path = Path(getattr(db, "db_path", "./data/easy_agent.db"))
    lock_path = Path(str(db_path) + ".migrate.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("无法创建迁移锁目录，降级为进程内锁: %s", exc)
        with _thread_lock:
            yield
        return

    handle = open(lock_path, "a+", encoding="utf-8")
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"等待 SQLite 迁移锁超时（{timeout}s）：{lock_path}"
                    )
                time.sleep(_POLL_SECONDS)
        logger.info("已获取 SQLite 迁移锁 | path=%s", lock_path)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
