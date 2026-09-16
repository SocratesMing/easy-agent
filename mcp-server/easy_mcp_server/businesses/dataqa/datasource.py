"""问数只读数据源：连接池 + 会话只读加固。

**为什么要连接池**：MySQL 侧 `skip_name_resolve=OFF` 时，每接受一个新连接都要
对客户端 IP 做反向 DNS 解析。本地实测单次新建连接约 130ms，而查询本身不到 1ms。
连接池把每次调用的固定开销从 ~130ms 降到 ~1ms。

**加固为什么放在 creator 里**：会话级只读（`TRANSACTION READ ONLY`）与执行超时
只需要在"连接建立"时设置一次。把它写进连接工厂，则池内新建与断线重连都会带上
加固状态，复用连接时零额外开销。注意 guard.py 的 SQL 校验仍是主要防线，加固是兜底。

连接池是**进程内**的：多进程部署时每个进程各持有一个池，实际连接数 =
进程数 × `MYSQL_POOL_SIZE`，需确保小于 MySQL 的 `max_connections`。
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from dbutils.pooled_db import PooledDB

from ...db import mysql_config
from ...env import load_env

logger = logging.getLogger("easy-mcp-server")

DEFAULT_TIMEOUT_MS = 10_000
DEFAULT_POOL_SIZE = 5

_pool_lock = threading.Lock()
_pool: PooledDB | None = None


def query_timeout_ms() -> int:
    """单次查询超时毫秒数（DATAQA_QUERY_TIMEOUT_MS，缺省 10000）。"""
    load_env()
    raw = os.environ.get("DATAQA_QUERY_TIMEOUT_MS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_TIMEOUT_MS


def pool_size() -> int:
    """连接池上限（MYSQL_POOL_SIZE，缺省 10）。"""
    load_env()
    raw = os.environ.get("MYSQL_POOL_SIZE", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_POOL_SIZE


def _harden(conn: Any) -> None:
    """对新建连接做会话级加固；低版本/无权限时只告警，不阻断。"""
    with conn.cursor() as cursor:
        for statement, label in (
            ("SET SESSION TRANSACTION READ ONLY", "只读事务"),
            (f"SET SESSION MAX_EXECUTION_TIME={query_timeout_ms()}", "执行超时"),
        ):
            try:
                cursor.execute(statement)
            except Exception as e:  # pragma: no cover - 依赖数据库版本/权限
                logger.warning(f"[dataqa] 会话加固失败（{label}）: {e}")


def _create_connection() -> Any:
    """连接工厂：新建连接（含池内断线重连）时顺带完成会话加固。"""
    conn = pymysql.connect(**mysql_config())
    _harden(conn)
    return conn


def get_pool() -> PooledDB:
    """懒创建进程内连接池（首次调用时才建，避免服务启动即依赖数据库）。"""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            size = pool_size()
            _pool = PooledDB(
                creator=_create_connection,
                maxconnections=size,
                # 预热到上限：新建连接要付反向 DNS 的 130ms，而 DBUtils 建连接是在锁内
                # 串行的——池懒增长时"首批并发"会各付一次（实测 5 并发 503ms）。
                # 在这里一次性建好，之后任何并发都不再等待。
                mincached=size,
                maxcached=size,
                blocking=True,
                ping=1,  # 取出时检查可用性，断线自动重连
                reset=True,  # 归还时回滚未提交事务
            )
            logger.info(f"[dataqa] MySQL 连接池已创建（预热 {size} 个连接）")
    return _pool


def reset_pool() -> None:
    """关闭并清空连接池（配置变更或测试用）。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception as e:  # pragma: no cover
                logger.warning(f"[dataqa] 关闭连接池失败: {e}")
            _pool = None


@contextmanager
def readonly_connection() -> Iterator[Any]:
    """从池中取出一个（已加固的）连接，用完归还。"""
    conn = get_pool().connection()
    try:
        yield conn
    finally:
        conn.close()  # 归还到池，并非真正关闭


def readonly_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """执行只读查询，返回字典列表。"""
    with readonly_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())
