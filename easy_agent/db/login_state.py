"""登录态：Bearer token 校验（账号停用、单点登录版本、空闲登出）与用户活跃时间读写。

主鉴权依赖（``middleware.auth``）、文件预览 / Web 终端的 query token 校验和知识库鉴权
（``knowledges.api.get_knowledge_principal``）都走 :func:`verify_login_token`，同一个 token
在所有接口上的判定、状态码和提示文案一致。每次拒绝都记一条 WARNING，写明具体原因
（token 解不开、版本不符、空闲超时、库里没有活跃记录……）：前端只看到「登录已过期」时，
按响应里的 request_id 就能在服务端日志里查到是哪一种。

放在数据层：只依赖用户存储端口、JWT 工具和运行期配置，``knowledges``（L4）和
``middleware``（L5）都能导入（分层见 docs/design/2026-10-20-services-subpackages-and-layering.md §5）。
本模块不 import web 框架，拒绝时抛 :class:`LoginRejected`，由调用方转成 HTTP 响应。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Optional

from easy_agent.bootstrap.runtime_config import get_runtime_agent_config
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.models.db import UserModel
from easy_agent.utils.auth import decode_access_token, describe_undecodable_token
from easy_agent.utils.client_ip import get_client_ip

logger = logging.getLogger(__name__)

EXPIRED_DETAIL = "登录已过期或未登录，请重新登录"
# 前端靠「其他设备 / 被迫下线」识别被踢，这类 401 不自动续期（续期会反过来踢掉新登录）
KICKED_DETAIL = "您的账号在其他设备登录，您已被迫下线，请重新登录"
DISABLED_DETAIL = "账号已停用"

# 活跃时间戳的写入节流：username -> 最近一次写入数据库的时间。
# 仅用于减少共享数据库的写压力（滑动续期无需每次请求都落库），
# 权威数据始终以数据库 last_activity_at 为准，因此不跨进程共享也不会
# 造成登录态不一致（读侧永远从共享库读；登录时强制写，见 touch_user_activity）。
_activity_write_throttle: dict[str, float] = {}
_ACTIVITY_WRITE_INTERVAL = 30.0

# 「用户存储端口」：活跃时间读写与登录态校验只用下列同名方法。T3b 起统一
# 收口到 ``UserRepository``（生产路径不再直接依赖 ``Database``）；测试替身只要
# 提供同名方法即可按 duck typing 工作。
UserActivityStore = UserRepository


class LoginRejected(Exception):
    """登录态校验未通过。``status_code`` / ``detail`` 原样返回给前端，``reason`` 只写日志。"""

    def __init__(self, status_code: int, detail: str, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.detail = detail
        self.reason = reason

    @property
    def headers(self) -> Optional[dict[str, str]]:
        # RFC 7235：401 要带认证方式；Bearer 不会触发浏览器的原生登录框
        return {"WWW-Authenticate": "Bearer"} if self.status_code == 401 else None


def _get_idle_logout_minutes() -> int:
    try:
        agent_config = get_runtime_agent_config()
        if agent_config and agent_config.get("config"):
            return agent_config["config"].agent.idle_logout_minutes
    except Exception as exc:
        logger.warning(f"读取 idle_logout_minutes 失败，使用默认 0（永不过期）: {exc}")
    return 0


def touch_user_activity(
    db: UserActivityStore,
    username: str,
    now: Optional[float] = None,
    *,
    force: bool = False,
) -> None:
    """更新用户最近活跃时间，写入共享数据库（节流）。

    多 worker 下登录请求可能落在 worker A，后续请求落在 worker B；活跃时间
    必须存进共享数据库，否则 worker B 会误判用户「空闲过期」而将其登出。

    ``force`` 跳过节流，登录 / 注册时使用：登出会清空库里的活跃时间，但只清得掉处理
    登出的那个进程的节流记录。随后的登录如果落在 30 秒内写过的另一个进程，不强制写
    就会被节流跳过，库里没有活跃记录，新 token 的第一个请求就被判成空闲登出。

    ``db`` 为用户存储端口（生产路径是 ``UserRepository``）。
    """
    if not username or db is None:
        return
    ts = time.time() if now is None else now
    last = _activity_write_throttle.get(username)
    if not force and last is not None and ts - last < _ACTIVITY_WRITE_INTERVAL:
        return
    _activity_write_throttle[username] = ts
    try:
        db.touch_user_activity(username, ts)
    except Exception as exc:
        logger.warning(f"写入用户活跃时间失败 | 用户: {username} | 错误: {exc}")


def clear_user_activity(db: UserActivityStore, username: str) -> None:
    _activity_write_throttle.pop(username, None)
    if db is None:
        return
    try:
        db.clear_user_activity(username)
    except Exception as exc:
        logger.warning(f"清空用户活跃时间失败 | 用户: {username} | 错误: {exc}")


def get_user_activity_time(
    db: UserActivityStore, username: str
) -> Optional[float]:
    """从共享数据库读取用户最近活跃时间。"""
    if db is None:
        return None
    try:
        return db.get_user_activity_time(username)
    except Exception as exc:
        logger.warning(f"读取用户活跃时间失败 | 用户: {username} | 错误: {exc}")
        return None


def _idle_logout_reason(
    db: UserActivityStore, username: str, now: Optional[float] = None
) -> Optional[str]:
    """空闲登出：超时或库里没有活跃记录时返回原因；未开启或未超时返回 None。"""
    idle_minutes = _get_idle_logout_minutes()
    if idle_minutes <= 0:
        return None

    last_activity = get_user_activity_time(db, username)
    if last_activity is None:
        return (
            f"空闲登出已开启（{idle_minutes} 分钟），库里没有该用户的活跃记录"
            "（登录时没有写入，或之后登出过）"
        )

    current_time = time.time() if now is None else now
    idle_seconds = current_time - last_activity
    if idle_seconds < idle_minutes * 60:
        return None
    return f"空闲 {int(idle_seconds)} 秒，超过空闲登出上限 {idle_minutes * 60} 秒"


def bearer_token(request: Any) -> Optional[str]:
    """从 Authorization 头取 Bearer token（scheme 不区分大小写）；没有时返回 None。"""
    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _describe_request(request: Any) -> str:
    """日志里的请求摘要：方法、路径、客户端 IP、request_id。没有请求对象时写 -。"""
    scope = getattr(request, "scope", None)
    if not isinstance(scope, Mapping):
        return "-"
    method = scope.get("method") or ("WS" if scope.get("type") == "websocket" else "-")
    request_id = getattr(getattr(request, "state", None), "request_id", None) or "-"
    return (
        f"{method} {scope.get('path') or '-'} | IP: {get_client_ip(request)}"
        f" | request_id={request_id}"
    )


def _describe_claimed_user(claims: dict) -> str:
    """验签失败时 token 声称的用户与签发时间，标明未验签（客户端可控，用 repr 转义）。"""
    sub = claims.get("sub")
    if not sub:
        return "-"
    claimed = f"{str(sub)[:64]!r}（未验签"
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)):
        return claimed + "）"
    try:
        issued = datetime.fromtimestamp(iat).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return claimed + "）"
    return f"{claimed}，签发于 {issued}）"


def _reject(
    request: Any, status_code: int, detail: str, reason: str, user: str = "-"
) -> LoginRejected:
    logger.warning(
        "[鉴权] 拒绝 %s | 原因: %s | 用户: %s | %s",
        status_code,
        reason,
        user,
        _describe_request(request),
    )
    return LoginRejected(status_code, detail, reason)


def verify_login_token(
    token: Optional[str], db: UserActivityStore, *, request: Any = None
) -> UserModel:
    """校验登录 token：通过时续期活跃时间并返回用户，不通过时记 WARNING 并抛 :class:`LoginRejected`。

    不校验 token 的 ``exp``：登录态由单点登录版本号（登出 / 新登录即失效）和空闲登出
    （库里的最近活跃时间）决定，``exp`` 只是签发时写入的 30 分钟默认值。

    ``request`` 只用于拒绝日志里的方法、路径、IP 和 request_id，可以不传。
    """
    token = (token or "").strip()
    if not token:
        raise _reject(request, 401, EXPIRED_DETAIL, "请求没有携带 Bearer token")

    payload = decode_access_token(token, verify_exp=False)
    if payload is None:
        reason, claims = describe_undecodable_token(token)
        raise _reject(request, 401, EXPIRED_DETAIL, reason, _describe_claimed_user(claims))

    username = payload.get("sub")
    if not username:
        raise _reject(request, 401, EXPIRED_DETAIL, "token 里没有用户名（sub）")

    user = db.get_user_by_username(username)
    if user is None:
        raise _reject(request, 401, EXPIRED_DETAIL, "用户不存在", username)
    if user.account_status == "disabled":
        raise _reject(request, 403, DISABLED_DETAIL, "账号已停用", username)

    # 单点登录：token 中的 v 必须等于 DB 当前 token_version，否则说明该账号
    # 之后又登录过（其他设备 / 标签页 / 免密直登）或已登出，当前 token 立即失效。
    token_version = payload.get("v")
    if token_version is None or token_version != user.token_version:
        raise _reject(
            request,
            401,
            KICKED_DETAIL,
            f"token 版本 {token_version} 与当前版本 {user.token_version} 不符"
            "（之后又登录或登出过）",
            username,
        )

    idle_reason = _idle_logout_reason(db, username)
    if idle_reason:
        raise _reject(request, 401, EXPIRED_DETAIL, idle_reason, username)

    touch_user_activity(db, username)
    return user
