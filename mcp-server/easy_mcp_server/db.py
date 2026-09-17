"""MySQL 访问（连接配置与查询助手）。

与主应用共用同一个数据库：鉴权只读 `mcp_api_keys`，业务数据由各自业务包按需读取。
连接配置优先读环境变量（可由 mcp-server/.env 提供）。
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
import pymysql.cursors

from .env import load_env

logger = logging.getLogger("easy-mcp-server")


def mysql_config() -> dict[str, Any]:
    load_env()
    config: dict[str, Any] = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE", "agent"),
        "charset": os.environ.get("MYSQL_CHARSET", "utf8mb4"),
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }
    timeout = os.environ.get("MYSQL_CONNECT_TIMEOUT")
    if timeout:
        config["connect_timeout"] = int(timeout)
    return config


@contextmanager
def connection() -> Iterator[pymysql.connections.Connection]:
    conn = pymysql.connect(**mysql_config())
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()
