"""B3 第二批：共享限流计数（DB）测试。"""

import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from easy_agent.db.database import Database
from easy_agent.middleware import rate_limit as rl


def _db(tmp_path) -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "rl.db")}})
    db.init_tables()
    return db


def _counter(db):
    return rl.DBRateLimitCounter(db_provider=lambda: db)


def test_db_counter_enforces_limit_and_resets_window(tmp_path, monkeypatch):
    db = _db(tmp_path)
    counter = _counter(db)
    now = [1_000_000.0]
    monkeypatch.setattr(rl.time, "time", lambda: now[0])
    key = "login:1.2.3.4"
    assert counter.allow(key, 2, 60.0) is True
    assert counter.allow(key, 2, 60.0) is True
    assert counter.allow(key, 2, 60.0) is False
    now[0] += 60.0
    assert counter.allow(key, 2, 60.0) is True


def test_db_counter_isolated_by_key(tmp_path):
    db = _db(tmp_path)
    counter = _counter(db)
    assert counter.allow("a", 1, 60.0) is True
    assert counter.allow("b", 1, 60.0) is True
    assert counter.allow("a", 1, 60.0) is False


def test_db_counter_falls_back_when_db_unavailable(tmp_path):
    def broken():
        raise RuntimeError("db down")

    counter = rl.DBRateLimitCounter(db_provider=broken)
    assert counter.allow("k", 2, 60.0) is True
    assert counter.allow("k", 2, 60.0) is True
    assert counter.allow("k", 2, 60.0) is False


def test_db_counter_is_exact_under_concurrent_threads(tmp_path):
    """中间件在线程里调用共享计数器：并发线程下也不能多放行。"""
    counter = _counter(_db(tmp_path))
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: counter.allow("k", 5, 60.0), range(20)))
    assert results.count(True) == 5


def test_fallback_counter_is_exact_under_concurrent_threads():
    """库不可用时降级到进程内计数，同样在线程里调用：检查与记录要一起完成。"""

    class _SlowLenDeque(deque):
        def __len__(self):
            size = super().__len__()
            time.sleep(0.001)  # 读到长度后让出 GIL：放大「检查」到「记录」之间的竞争窗口
            return size

    def broken():
        raise RuntimeError("db down")

    counter = rl.DBRateLimitCounter(db_provider=broken)
    counter._fallback._hits = defaultdict(_SlowLenDeque)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: counter.allow("k", 3, 60.0), range(16)))
    assert results.count(True) == 3


def test_schema_has_rate_limit_table(tmp_path):
    db = _db(tmp_path)
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rate_limit_counters")
        assert cur.fetchone()[0] == 0


def test_build_counter_selection(monkeypatch):
    monkeypatch.setenv("EASY_RATE_LIMIT_BACKEND", "memory")
    assert isinstance(rl.build_rate_limit_counter(), rl.InMemorySlidingWindowCounter)

    monkeypatch.setenv("EASY_RATE_LIMIT_BACKEND", "db")
    assert isinstance(rl.build_rate_limit_counter(), rl.DBRateLimitCounter)

    monkeypatch.delenv("EASY_RATE_LIMIT_BACKEND")
    monkeypatch.setattr(rl, "detect_worker_count", lambda: 1)
    assert isinstance(rl.build_rate_limit_counter(), rl.InMemorySlidingWindowCounter)
    monkeypatch.setattr(rl, "detect_worker_count", lambda: 3)
    assert isinstance(rl.build_rate_limit_counter(), rl.DBRateLimitCounter)
