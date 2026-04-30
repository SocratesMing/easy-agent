"""认证依赖模块.

提供 FastAPI 依赖项用于从请求头提取用户信息.
"""

import logging
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request

from .db import Database, get_database
from .utils.auth import decode_access_token, get_username_from_token

logger = logging.getLogger(__name__)


async def get_current_username(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> str:
    """从请求头提取当前用户名.

    优先级:
    1. Authorization: Bearer <token> 头中的 JWT token
    2. X-Username 请求头（用于测试/开发）
    3. 默认用户 "default"

    Args:
        http_request: HTTP 请求对象
        db: 数据库实例

    Returns:
        用户名
    """
    auth_header = http_request.headers.get("Authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        username_from_token = get_username_from_token(token)
        if username_from_token:
            user = db.get_user_by_username(username_from_token)
            if user:
                logger.debug(f"[认证] 从 JWT token 提取用户: {username_from_token}")
                return username_from_token

    x_username = http_request.headers.get("X-Username")
    if x_username:
        logger.debug(f"[认证] 从 X-Username 头提取用户: {x_username}")
        return x_username

    logger.debug("[认证] 未提供认证信息，使用默认用户")
    return "default"


async def get_optional_username(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> Optional[str]:
    """可选的用户名获取（不会自动降级到默认用户）.

    Args:
        http_request: HTTP 请求对象
        db: 数据库实例

    Returns:
        用户名，如果未提供则返回 None
    """
    auth_header = http_request.headers.get("Authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        username_from_token = get_username_from_token(token)
        if username_from_token:
            user = db.get_user_by_username(username_from_token)
            if user:
                return username_from_token

    x_username = http_request.headers.get("X-Username")
    if x_username:
        return x_username

    return None


def set_current_username(username: str):
    """设置当前用户名（用于登录后设置）"""
    pass
