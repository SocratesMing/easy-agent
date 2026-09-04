"""MCP API Key 签发与管理服务。

Key 按 (用户, 业务) 粒度签发：明文只返回一次，数据库仅存 sha256 哈希。
mcp-server 子项目（独立 uv 项目）直连同一数据库做只读校验，
主应用与子项目仅通过 ``mcp_api_keys`` 表结构和 HTTP 协议耦合——
主应用不 import 子项目代码，子项目不 import 主应用代码。
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from ..db import get_database

logger = logging.getLogger(__name__)

# 与 mcp-server/easy_mcp_server/businesses/ 下的业务包一一对应（包名即 URL 后缀）
SUPPORTED_BUSINESSES: tuple[str, ...] = ("market",)

_KEY_PREFIX = "mcp_"  # 便于识别与日志脱敏


def is_supported_business(business: str) -> bool:
    return business in SUPPORTED_BUSINESSES


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
