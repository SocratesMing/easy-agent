"""Authentication dependencies for FastAPI"""

import logging
import time
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request

from ..db import Database, get_database
from ..services import get_agent_config
from ..utils.auth import decode_access_token

logger = logging.getLogger(__name__)

# 活跃时间戳的写入节流：username -> 最近一次写入数据库的时间。
# 仅用于减少共享数据库的写压力（滑动续期无需每次请求都落库），
# 权威数据始终以数据库 last_activity_at 为准，因此不跨进程共享也不会
# 造成登录态不一致（读侧永远从共享库读）。
_activity_write_throttle: dict[str, float] = {}
_ACTIVITY_WRITE_INTERVAL = 30.0


def _get_idle_logout_minutes() -> int:
    try:
        agent_config = get_agent_config()
        if agent_config and agent_config.get("config"):
            return agent_config["config"].agent.idle_logout_minutes
    except Exception as exc:
        logger.warning(f"读取 idle_logout_minutes 失败，使用默认 0（永不过期）: {exc}")
    return 0


def touch_user_activity(db: Database, username: str, now: Optional[float] = None) -> None:
    """更新用户最近活跃时间，写入共享数据库（节流）。

    多 worker 下登录请求可能落在 worker A，后续请求落在 worker B；活跃时间
    必须存进共享数据库，否则 worker B 会误判用户「空闲过期」而将其登出。
    """
    if not username or db is None:
        return
    ts = time.time() if now is None else now
    last = _activity_write_throttle.get(username)
    if last is not None and ts - last < _ACTIVITY_WRITE_INTERVAL:
        return
    _activity_write_throttle[username] = ts
    try:
        db.touch_user_activity(username, ts)
    except Exception as exc:
        logger.warning(f"写入用户活跃时间失败 | 用户: {username} | 错误: {exc}")


def clear_user_activity(db: Database, username: str) -> None:
    _activity_write_throttle.pop(username, None)
    if db is None:
        return
    try:
        db.clear_user_activity(username)
    except Exception as exc:
        logger.warning(f"清空用户活跃时间失败 | 用户: {username} | 错误: {exc}")


def get_user_activity_time(db: Database, username: str) -> Optional[float]:
    """从共享数据库读取用户最近活跃时间。"""
    if db is None:
        return None
    try:
        return db.get_user_activity_time(username)
    except Exception as exc:
        logger.warning(f"读取用户活跃时间失败 | 用户: {username} | 错误: {exc}")
        return None


def _user_session_is_active(
    db: Database, username: str, now: Optional[float] = None
) -> bool:
    idle_minutes = _get_idle_logout_minutes()
    if idle_minutes <= 0:
        return True

    last_activity = get_user_activity_time(db, username)
    if last_activity is None:
        return False

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
    if token_v is None or user.token_version != token_v:
        return None
    if not _user_session_is_active(db, username):
        return None
    touch_user_activity(db, username)
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
                    # 单点登录：token 中的 v 必须等于 DB 当前 token_version，
                    # 否则说明该账号已在其他设备/IP 登录，当前 token 立即失效。
                    if token_v is None or user.token_version != token_v:
                        raise HTTPException(
                            status_code=401,
                            detail="您的账号在其他设备登录，您已被迫下线，请重新登录",
                        )
                    if not _user_session_is_active(db, username):
                        raise HTTPException(
                            status_code=401,
                            detail="登录已过期或未登录，请重新登录",
                        )
                    touch_user_activity(db, username)
                    return username

    # 未携带有效凭证（token 过期/未登录/被踢下线）时返回 401。
    # 前端 authFetch 收到 401 后会清除登录态并跳回登录页。
    raise HTTPException(
        status_code=401,
        detail="登录已过期或未登录，请重新登录",
    )
