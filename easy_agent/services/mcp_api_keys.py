"""MCP API Key 签发与管理服务。

Key 按 (用户, 业务) 粒度签发：明文只返回一次，数据库仅存 sha256 哈希。
mcp-server 子项目（独立 uv 项目）直连同一数据库做只读校验，
主应用与子项目仅通过 ``mcp_api_keys`` / ``mcp_businesses`` 两张表结构耦合——
主应用不 import 子项目代码，子项目不 import 主应用代码。

业务清单的所有权在 mcp-server（``easy_mcp_server/contract.py``）：它启动时把
``businesses/`` 下的包名写进 ``mcp_businesses``，本模块只读取。因此**新增业务
只改 mcp-server**，主应用无需改动。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time

from ..db import get_database

logger = logging.getLogger(__name__)

SUPPORTED_BUSINESSES: tuple[str, ...] = ("strategyqa",)
"""离线兜底清单。仅在共享库 ``mcp_businesses`` 不可读或为空时使用（见下）。

它不代表"当前可用业务"，只保证 mcp-server 尚未部署/首次启动前设置页仍可用。
正常情况下业务清单来自 mcp-server 的登记，不需要维护这里。
"""

_BUSINESS_CACHE_TTL_SECONDS = 60.0
_business_cache: tuple[float, tuple[str, ...]] | None = None

_KEY_PREFIX = "mcp_"  # 便于识别与日志脱敏（契约：contract.API_KEY_PREFIX）


def supported_businesses() -> tuple[str, ...]:
    """返回当前可签发 Key 的业务清单。

    权威来源是 mcp-server 登记的 ``mcp_businesses`` 表（它才是业务清单的所有者）。
    表不可读或为空时回退 :data:`SUPPORTED_BUSINESSES`，避免因为一个"发现通道"
    故障就让设置页整体不可用。

    结果做 60s 进程内缓存：签发/展示是低频动作，但调用点可能在循环里。
    """
    global _business_cache
    now = time.monotonic()
    if _business_cache and _business_cache[0] > now:
        return _business_cache[1]

    try:
        names = tuple(get_database().list_mcp_businesses())
    except Exception as e:
        logger.warning(f"读取 MCP 业务清单失败，回退内置清单: {e}")
        names = ()

    resolved = names or SUPPORTED_BUSINESSES
    if not names:
        logger.debug("mcp_businesses 为空，使用内置兜底业务清单")
    _business_cache = (now + _BUSINESS_CACHE_TTL_SECONDS, resolved)
    return resolved


def invalidate_business_cache() -> None:
    """清空业务清单缓存（mcp-server 刚上线/改清单后想立刻生效时调用）。"""
    global _business_cache
    _business_cache = None


def is_supported_business(business: str) -> bool:
    return business in supported_businesses()


def issue_api_key(username: str, business: str) -> str:
    """签发（或重签）API Key，返回明文（仅此一次）。重签后旧 key 失效。"""
    if not is_supported_business(business):
        raise ValueError(f"不支持的 MCP 业务: {business}")
    api_key = _KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = hash_api_key(api_key)
    db = get_database()
    db.issue_mcp_api_key(username, business, key_hash)
    logger.info(f"签发 MCP API Key | 用户: {username} | 业务: {business}")
    return api_key


def revoke_api_key(username: str, business: str) -> bool:
    if not is_supported_business(business):
        raise ValueError(f"不支持的 MCP 业务: {business}")
    revoked = get_database().revoke_mcp_api_key(username, business)
    logger.info(f"吊销 MCP API Key | 用户: {username} | 业务: {business} | 存在: {revoked}")
    return revoked


def list_key_status(username: str) -> list[dict]:
    """返回用户所有业务的 key 状态（仅元数据，不含任何密钥信息）。"""
    rows = get_database().get_mcp_api_keys(username)
    return [
        {
            "business": row["business"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revoked": bool(row["revoked"]),
        }
        for row in rows
    ]


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
