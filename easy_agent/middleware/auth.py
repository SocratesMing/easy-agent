"""FastAPI 鉴权依赖：将 ``db.login_state`` 的登录态判定转换为 HTTP 响应。"""

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.requests import HTTPConnection

from easy_agent.db import Database, get_database
from easy_agent.db.login_state import (
    LoginRejected,
    UserActivityStore,
    bearer_token,
    verify_login_token,
)
from easy_agent.db.repositories.user_repository import UserRepository


def verify_token_sso(
    token: str, db: UserActivityStore, request: Optional[HTTPConnection] = None
) -> Optional[str]:
    """校验 token（含单点登录 token_version），成功返回 username，失败返回 None。"""
    try:
        return verify_login_token(token, db, request=request).username
    except LoginRejected:
        return None


def resolve_username_from_request(
    request: HTTPConnection,
    db: UserActivityStore,
    token: Optional[str] = None,
) -> Optional[str]:
    """优先从 Authorization Bearer Header、其次从 query ``token`` 解析并校验用户名。"""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        username = verify_token_sso(auth_header[7:], db, request)
        if username:
            return username
    if token:
        return verify_token_sso(token, db, request)
    return None


def get_user_repository(
    db: Annotated[Database, Depends(get_database)],
) -> UserRepository:
    """FastAPI 依赖：提供 users 域 repository。"""
    return UserRepository(db)


def get_current_username(
    http_request: Request,
    repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> str:
    """校验 Bearer token 并返回用户名（同步依赖，由 FastAPI 自动放入线程池执行查库）。"""
    try:
        user = verify_login_token(bearer_token(http_request), repo, request=http_request)
    except LoginRejected as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        ) from None
    return user.username
