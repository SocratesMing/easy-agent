"""Authentication dependencies for FastAPI

登录态的判定（账号停用、单点登录版本、空闲登出）与活跃时间读写在
``easy_agent.db.login_state``，知识库鉴权也用它；这里只把结果转成 HTTP 响应。
"""

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
    """校验 token（含单点登录 token_version 校验），成功返回 username，失败返回 None。

    供 files.py 等无法走 Depends(get_current_username) 的场景（如 iframe 预览的
    query token）复用，确保被踢下线的旧 token 同样无法访问文件预览。

    ``db`` 为用户存储端口（由 API 层注入 ``UserRepository``）；``request`` 只用于
    拒绝日志里的路径和 IP。
    """
    try:
        return verify_login_token(token, db, request=request).username
    except LoginRejected:
        return None


def get_user_repository(
    db: Annotated[Database, Depends(get_database)],
) -> UserRepository:
    """FastAPI 依赖：把进程级 ``Database`` 包装成 users 域 repository。

    ``get_database`` 只出现在本 provider 内；``api.auth`` / ``api.files`` /
    ``api.scheduled_tasks`` 的路由函数只声明 ``Depends(get_user_repository)``，
    因此活跃时间与 SSO token 校验无需在 API 层触碰 ``Database`` 门面。
    """
    return UserRepository(db)


def get_current_username(
    http_request: Request,
    repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> str:
    """校验 Bearer token 并返回用户名（每个需要登录的接口都经过这里）。

    刻意写成同步函数：FastAPI 会把同步依赖放进线程池执行。这里每个请求都要查库（用户状态、
    单点登录的 ``token_version``、空闲登出的最近活动时间），写成 ``async def`` 会在事件循环上
    同步查库，业务库是 MySQL 时每次往返都会卡住所有并发的 SSE 流。
    T3b 起依赖 ``UserRepository``，不再直接使用 ``Database`` 门面。
    """
    try:
        user = verify_login_token(bearer_token(http_request), repo, request=http_request)
    except LoginRejected as exc:
        # 前端 authFetch 收到 401 后会清除登录态并跳回登录页（被迫下线的文案不续期）；
        # 具体原因已由 verify_login_token 写进服务端日志。
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        ) from None
    return user.username
