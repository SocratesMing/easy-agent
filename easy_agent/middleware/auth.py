"""Authentication dependencies for FastAPI"""

import logging
import time
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request

from ..db import Database, get_database
from ..services import get_agent_config
from ..utils.auth import decode_access_token

logger = logging.getLogger(__name__)

_user_activity_cache: dict[str, float] = {}


def _get_idle_logout_minutes() -> int:
    try:
        agent_config = get_agent_config()
        if agent_config and agent_config.get("config"):
            return agent_config["config"].agent.idle_logout_minutes
    except Exception as exc:
        logger.warning(f"读取 idle_logout_minutes 失败，使用默认 5 分钟: {exc}")
    return 5


def touch_user_activity(username: str, now: Optional[float] = None) -> None:
    if not username:
        return
    _user_activity_cache[username] = time.time() if now is None else now


def clear_user_activity(username: str) -> None:
    _user_activity_cache.pop(username, None)


def get_user_activity_time(username: str) -> Optional[float]:
    return _user_activity_cache.get(username)


def _user_session_is_active(
    username: str,
    now: Optional[float] = None,
    *,
    token_issued_at: object = None,
) -> bool:
    idle_minutes = _get_idle_logout_minutes()
    if idle_minutes <= 0:
        return True

    last_activity = _user_activity_cache.get(username)
    if last_activity is None:
        # The idle cache is deliberately process-local. After a clean service
        # restart, conservatively fall back to the signed JWT issue time so a
        # recently active session can resume, while an old token cannot reset
        # its idle window merely by hitting the restarted server.
        if not isinstance(token_issued_at, (int, float)):
            return False
        last_activity = float(token_issued_at)

    current_time = time.time() if now is None else now
    return current_time - last_activity < idle_minutes * 60


def verify_token_sso(token: str, db: Database) -> Optional[str]:
    """校验 token（含单点登录 token_version 校验），成功返回 username，失败返回 None。

    供 files.py 等无法走 Depends(get_current_username) 的场景（如 iframe 预览的
    query token）复用，确保被踢下线的旧 token 同样无法访问文件预览。
    """
    payload = decode_access_token(token, verify_exp=False)
    if not payload:
        return None
    username = payload.get("sub")
    token_v = payload.get("v")
    if not username:
        return None
    user = db.get_user_by_username(username)
    if not user:
        return None
    if getattr(user, "account_status", "active") == "disabled":
        return None
    if token_v is None or user.token_version != token_v:
        return None
    if not _user_session_is_active(
        username, token_issued_at=payload.get("iat")
    ):
        return None
    touch_user_activity(username)
    return username


async def get_current_username(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> str:
    auth_header = http_request.headers.get("Authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = decode_access_token(token, verify_exp=False)
        if payload:
            username = payload.get("sub")
            token_v = payload.get("v")
            if username:
                user = db.get_user_by_username(username)
                if user:
                    if getattr(user, "account_status", "active") == "disabled":
                        raise HTTPException(
                            status_code=403,
                            detail="账号已停用，请联系管理员",
                        )
                    # 单点登录：token 中的 v 必须等于 DB 当前 token_version，
                    # 否则说明该账号已在其他设备/IP 登录，当前 token 立即失效。
                    if token_v is None or user.token_version != token_v:
                        raise HTTPException(
                            status_code=401,
                            detail="您的账号在其他设备登录，您已被迫下线，请重新登录",
                        )
                    if not _user_session_is_active(
                        username, token_issued_at=payload.get("iat")
                    ):
                        raise HTTPException(
                            status_code=401,
                            detail="登录已过期或未登录，请重新登录",
                        )
                    touch_user_activity(username)
                    return username

    # 未携带有效凭证（token 过期/未登录/被踢下线）时返回 401。
    # 前端 authFetch 收到 401 后会清除登录态并跳回登录页。
    raise HTTPException(
        status_code=401,
        detail="登录已过期或未登录，请重新登录",
    )
