"""Authentication dependencies for FastAPI"""

import logging
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request

from ..db import Database, get_database
from ..utils.auth import decode_access_token

logger = logging.getLogger(__name__)


def verify_token_sso(token: str, db: Database) -> Optional[str]:
    """校验 token（含单点登录 token_version 校验），成功返回 username，失败返回 None。

    供 files.py 等无法走 Depends(get_current_username) 的场景（如 iframe 预览的
    query token）复用，确保被踢下线的旧 token 同样无法访问文件预览。
    """
    payload = decode_access_token(token)
    if not payload:
        return None
    username = payload.get("sub")
    token_v = payload.get("v")
    if not username:
        return None
    user = db.get_user_by_username(username)
    if not user:
        return None
    if token_v is None or user.token_version != token_v:
        return None
    return username


async def get_current_username(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> str:
    auth_header = http_request.headers.get("Authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = decode_access_token(token)
        if payload:
            username = payload.get("sub")
            token_v = payload.get("v")
            if username:
                user = db.get_user_by_username(username)
                if user:
                    # 单点登录：token 中的 v 必须等于 DB 当前 token_version，
                    # 否则说明该账号已在其他设备/IP 登录，当前 token 立即失效。
                    if token_v is None or user.token_version != token_v:
                        raise HTTPException(
                            status_code=401,
                            detail="您的账号在其他设备登录，您已被迫下线，请重新登录",
                        )
                    return username

    # 未携带有效凭证（token 过期/未登录/被踢下线）时返回 401。
    # 前端 authFetch 收到 401 后会清除登录态并跳回登录页。
    raise HTTPException(
        status_code=401,
        detail="登录已过期或未登录，请重新登录",
    )
