"""Authentication dependencies for FastAPI"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ..db import Database, get_database
from ..utils.auth import get_username_from_token

logger = logging.getLogger(__name__)


async def get_current_username(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> str:
    auth_header = http_request.headers.get("Authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        username_from_token = get_username_from_token(token)
        if username_from_token:
            user = db.get_user_by_username(username_from_token)
            if user:
                return username_from_token

    username_header = http_request.headers.get("X-Username")
    if username_header:
        user = db.get_user_by_username(username_header)
        if user:
            return username_header

    # 不再静默回退到 admin：未携带有效凭证（token 过期/未登录）时直接返回 401，
    # 避免把请求（如发送消息）误记到 admin 账户下。前端 authFetch 收到 401 后
    # 会清除登录态并跳回登录页。
    raise HTTPException(
        status_code=401,
        detail="登录已过期或未登录，请重新登录",
    )
