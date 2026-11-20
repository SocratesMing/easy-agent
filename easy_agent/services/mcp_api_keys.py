"""MCP API Key 签发服务。

Key 按 (用户, 业务) 粒度签发：明文只返回一次（写入用户 mcp.json），数据库仅存
sha256 哈希。mcp-server 子项目（独立 uv 项目）直连同一数据库做只读校验，
主应用与子项目只通过 ``mcp_api_keys`` 表结构和 HTTP 协议耦合。
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from easy_agent.db.providers import get_mcp_api_key_repository

logger = logging.getLogger(__name__)

_KEY_PREFIX = "mcp_"  # 便于识别与日志脱敏


def hash_api_key(api_key: str) -> str:
    """与 mcp-server/easy_mcp_server/keys.py 保持同一算法（sha256 十六进制）。"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def issue_api_key(username: str, business: str) -> str:
    """签发（或重签）某用户某业务的 API Key，返回明文（仅此一次，不入库）。

    同一 (用户, 业务) 重复签发会覆盖旧哈希，旧 Key 立即失效。
    """
    api_key = _KEY_PREFIX + secrets.token_urlsafe(32)
    get_mcp_api_key_repository().issue_mcp_api_key(
        username, business, hash_api_key(api_key)
    )
    logger.info(f"签发 MCP API Key | 用户: {username} | 业务: {business}")
    return api_key
