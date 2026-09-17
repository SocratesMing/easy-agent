"""API Key 校验：Bearer key -> username。

只做只读校验，签发由主应用负责（表结构同样由主应用维护）。
校验结果做短 TTL 缓存，避免每次工具调用都查库。
"""

from __future__ import annotations

import hashlib
import logging
import time

from .auth import ApiKeyVerifier
from .db import fetch_one

logger = logging.getLogger("easy-mcp-server")

# 缓存 (business, key_hash) -> (username, expire_at)
_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
DEFAULT_TTL_SECONDS = 60.0


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _lookup(business: str, key_hash: str) -> str | None:
    try:
        row = fetch_one(
            "SELECT username FROM mcp_api_keys "
            "WHERE business=%s AND key_hash=%s AND revoked=0",
            (business, key_hash),
        )
    except Exception as e:
        # 数据库不可用时拒绝所有请求，而不是放行
        logger.error(f"查询 mcp_api_keys 失败 | business={business} | {e}")
        return None
    return str(row["username"]) if row else None


def create_mysql_verifier(ttl_seconds: float = DEFAULT_TTL_SECONDS) -> ApiKeyVerifier:
    """返回基于 MySQL 的校验器（带 TTL 缓存）。

    注意：吊销后最多 ttl_seconds 内旧 key 仍可能通过缓存校验。
    """

    def verify(business: str, api_key: str) -> str | None:
        key_hash = hash_api_key(api_key)
        cache_key = (business, key_hash)
        now = time.monotonic()

        hit = _CACHE.get(cache_key)
        if hit and hit[1] > now:
            return hit[0]

        username = _lookup(business, key_hash)
        if username:
            _CACHE[cache_key] = (username, now + ttl_seconds)
        else:
            _CACHE.pop(cache_key, None)
        return username

    return verify


def clear_cache() -> None:
    _CACHE.clear()
