"""querykit 的配置读取。

统一用 ``QUERYKIT_`` 前缀的环境变量，避免与具体业务的环境变量混淆：

- ``QUERYKIT_MAX_ROWS``   单次查询返回行数上限（缺省 500）
- ``QUERYKIT_TIMEOUT_MS`` 单次查询超时（缺省 10000）
- ``QUERYKIT_POOL_SIZE``  MySQL 连接池上限/预热数量（缺省 5）

业务包如需自己的白/黑名单，用 :func:`env_list` 读自己的前缀
（例如 ``STRATEGY_ALLOWED_TABLES``），再传给 :class:`ExplorePolicy`。
"""

from __future__ import annotations

import os

from ..env import load_env

DEFAULT_MAX_ROWS = 500
DEFAULT_TIMEOUT_MS = 10_000
DEFAULT_POOL_SIZE = 5

# 默认屏蔽的表：存 API Key 哈希，任何问数场景都不应暴露
DEFAULT_DENY_TABLES: tuple[str, ...] = ("mcp_api_keys",)


def env_int(name: str, default: int) -> int:
    """读正整数环境变量；缺失、非数字或非正数时返回缺省值。"""
    load_env()
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def env_list(name: str) -> tuple[str, ...]:
    """读逗号分隔的环境变量，返回去空后的元组。"""
    load_env()
    raw = os.environ.get(name, "").strip()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def max_rows() -> int:
    """单次查询返回的行数上限（QUERYKIT_MAX_ROWS）。"""
    return env_int("QUERYKIT_MAX_ROWS", DEFAULT_MAX_ROWS)


def query_timeout_ms() -> int:
    """单次查询超时毫秒数（QUERYKIT_TIMEOUT_MS）。"""
    return env_int("QUERYKIT_TIMEOUT_MS", DEFAULT_TIMEOUT_MS)


def pool_size() -> int:
    """连接池上限，同时也是预热数量（QUERYKIT_POOL_SIZE）。"""
    return env_int("QUERYKIT_POOL_SIZE", DEFAULT_POOL_SIZE)
