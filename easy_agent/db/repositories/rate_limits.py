"""HTTP 限流共享计数 repository（多副本 B3 第二批）。

SQLite / MySQL 共用固定窗口计数表 ``rate_limit_counters``：先尝试
``UPDATE ... SET count=count+1 WHERE count < limit``，rowcount=1 放行；否则尝试
INSERT 当前窗口；INSERT 冲突说明已被并发写满/刚创建，重试一次 UPDATE 处理
「对方 INSERT 成功但尚未计数」的竞态。

SQL 与窗口语义从 ``middleware/rate_limit.py`` 原样搬出（T3c，不改行为），
连接/事务仍由传入的 ``Database`` 提供；中间件只负责选择计数器与降级。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from easy_agent.db.database import Database

#: 固定窗口宽度（秒）由调用方传入；时间格式与表结构保持一致
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _window_start(now: float, window_seconds: float) -> int:
    width = max(1, int(window_seconds))
    return int(now // width) * width


def try_allow(
    db: "Database", key: str, limit: int, window_seconds: float, now: float | None = None
) -> bool:
    """在 ``window_seconds`` 内第 ``limit + 1`` 次调用返回 False（DB 故障时抛异常）。"""
    current = time.time() if now is None else now
    window_start = _window_start(current, window_seconds)
    stamp = time.strftime(_TIME_FORMAT, time.localtime(current))
    with db.get_connection() as conn:
        cursor = conn.cursor()
        # 清理本 bucket 的过期窗口，避免公网大基数下无界增长
        db._execute(
            cursor,
            "DELETE FROM rate_limit_counters WHERE bucket=? AND window_start < ?",
            (key, window_start - max(1, int(window_seconds))),
        )
        db._execute(
            cursor,
            "UPDATE rate_limit_counters SET count=count+1, updated_at=? "
            "WHERE bucket=? AND window_start=? AND count < ?",
            (stamp, key, window_start, limit),
        )
        if cursor.rowcount > 0:
            return True
        try:
            db._execute(
                cursor,
                "INSERT INTO rate_limit_counters "
                "(bucket, window_start, count, updated_at) VALUES (?, ?, 1, ?)",
                (key, window_start, stamp),
            )
            return True
        except Exception:
            # 并发 INSERT 冲突：对方刚创建窗口，重试条件 UPDATE。
            db._execute(
                cursor,
                "UPDATE rate_limit_counters SET count=count+1, updated_at=? "
                "WHERE bucket=? AND window_start=? AND count < ?",
                (stamp, key, window_start, limit),
            )
            return cursor.rowcount > 0


class RateLimitRepository:
    """限流共享计数（``rate_limit_counters`` 表）的领域入口。"""

    def __init__(self, db: "Database"):
        self._db = db

    def try_allow(
        self, key: str, limit: int, window_seconds: float, now: float | None = None
    ) -> bool:
        return try_allow(self._db, key, limit, window_seconds, now=now)


__all__ = ["RateLimitRepository", "try_allow"]