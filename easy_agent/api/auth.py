"""用户管理路由"""

import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import Config
from ..db import Database, get_database
from ..middleware import get_current_username
from ..models.api import (
    UserProfile,
    UpdateUserProfileRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    AuthResponse,
)
from ..services import get_agent_config
from ..utils import create_access_token, hash_password
from ..utils.auth import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)

# 登录时间缓存：username -> 登录成功时刻（本地时间）。用于登出时打印上次登录缓存时间；
# 进程重启后丢失，此时回退到 JWT 的 iat 字段。
_login_time_cache: dict[str, datetime] = {}

# 当前活跃登录 IP 缓存：username -> IP。用于登录时判断是否为异地登录踢人。
# 进程内存，重启丢失（重启后旧 token 仍会因 token_version 不匹配被踢，只是无异地日志）。
_active_login_ip: dict[str, str] = {}


def _get_token_lifetime() -> timedelta:
    """签发 token 的有效期。

    「空闲自动登出」配置（agent.idle_logout_minutes）为 0 表示不登出、一直保持登录，
    此时签发超长有效期 token（365 天），避免 30 分钟 token 过期把用户强制踢下线；
    其余情况保持默认 30 分钟有效期（空闲登出计时器会在更早触发）。
    """
    try:
        _cfg = get_agent_config()
        if _cfg and _cfg.get("config"):
            idle_minutes = _cfg["config"].agent.idle_logout_minutes
            if idle_minutes == 0:
                return timedelta(days=365)
    except Exception as e:
        logger.warning(f"读取 idle_logout_minutes 失败，使用默认 token 有效期: {e}")
    from ..utils.auth import ACCESS_TOKEN_EXPIRE_MINUTES

    return timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)


def _get_max_input_tokens() -> int:
    _cfg = get_agent_config()
    if _cfg and _cfg.get("config"):
        return _cfg["config"].llm.max_input_tokens
    return 200000


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    client_host = request.client.host if request.client else "unknown"
    return client_host


@router.post(
    "/register",
    response_model=AuthResponse,
    summary="注册新用户",
)
async def register(
    request: RegisterRequest,
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
):
    # 注册不再绑定 IP（单点登录：登录不限制 IP，但同账号新登录会踢掉旧登录）
    user = db.register_user(
        username=request.username,
        password=request.password,
        organization_id=request.organization_id,
        email=request.email,
    )

    if not user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 注册即登录：递增 token 版本号并签发带 v 的 token
    new_version = db.increment_user_token_version(user.username)
    access_token = create_access_token(
        data={"sub": user.username, "v": new_version},
        expires_delta=_get_token_lifetime(),
    )

    # 注册即登录，同样缓存登录时间与活跃 IP
    _login_time_cache[user.username] = datetime.now()
    _active_login_ip[user.username] = get_client_ip(http_request)

    try:
        user_workspace = Config.get_user_workspace_dir(user.username)
        user_workspace.mkdir(parents=True, exist_ok=True)
        user_upload = Config.get_user_upload_dir(user.username)
        user_upload.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"[用户] 创建用户workspace | 用户: {user.username} | 路径: {user_workspace}"
        )
    except Exception as e:
        logger.warning(
            f"[用户] 创建用户workspace失败 | 用户: {user.username} | 错误: {e}"
        )

    max_input_tokens = _get_max_input_tokens()

    logger.info(
        f"[用户] 注册成功 | 用户名: {user.username} | 机构ID: {user.organization_id} | 版本: {new_version}"
    )

    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        max_input_tokens=max_input_tokens,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="用户登录",
)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
):
    user = db.get_user_by_username(request.username)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user = db.verify_user_password(request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    client_ip = get_client_ip(http_request)

    # 单点登录：递增 token 版本号，使该用户此前在其他设备/IP 的登录立即失效
    prev_ip = _active_login_ip.get(user.username)
    new_version = db.increment_user_token_version(user.username)
    access_token = create_access_token(
        data={"sub": user.username, "v": new_version},
        expires_delta=_get_token_lifetime(),
    )

    # 缓存登录时间（供登出接口打印）与当前活跃 IP（供下次登录判断异地踢人）
    _login_time_cache[user.username] = datetime.now()
    _active_login_ip[user.username] = client_ip

    max_input_tokens = _get_max_input_tokens()

    if prev_ip and prev_ip != client_ip:
        logger.info(
            f"[用户] 单点登录踢出旧会话 | 用户: {user.username} | 旧IP: {prev_ip} | 新IP: {client_ip}"
        )
    logger.info(
        f"[用户] 登录成功 | 用户名: {user.username} | IP: {client_ip} | 版本: {new_version}"
    )

    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        max_input_tokens=max_input_tokens,
    )


@router.post("/logout", summary="用户登出")
async def logout(
    username: Annotated[str, Depends(get_current_username)],
    http_request: Request,
):
    """记录用户登出信息（用户名、上次登录缓存时间、在线时长）。

    前端手动登出与空闲超时自动登出均会调用。登录时间优先取登录缓存，缓存未命中
    （如后端重启后未重新登录）时回退到 JWT 的 iat（签发时间）。
    """
    now = datetime.now()
    cached_login = _login_time_cache.pop(username, None)
    _active_login_ip.pop(username, None)
    login_time_str = "未知"
    duration_str = "未知"
    if cached_login is not None:
        login_time_str = cached_login.strftime("%Y-%m-%d %H:%M:%S")
        duration_str = str(now - cached_login).split(".")[0]
    else:
        # 缓存未命中：回退到 token 的 iat（签发时间，UTC 时间戳 -> 本地时间）
        auth_header = http_request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            payload = decode_access_token(auth_header[7:])
            if payload and payload.get("iat"):
                iat_local = datetime.fromtimestamp(payload["iat"])
                login_time_str = iat_local.strftime("%Y-%m-%d %H:%M:%S")
                duration_str = str(now - iat_local).split(".")[0]
    logger.info(
        f"[用户] 登出 | 用户名: {username} | 上次登录缓存时间: {login_time_str} | 在线时长: {duration_str}"
    )
    return {"status": "ok"}


@router.get(
    "/profile",
    response_model=UserProfile,
    summary="获取用户资料",
)
async def get_profile(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    user = db.get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return UserProfile(
        user_id=user.user_id,
        username=user.username,
        organization_id=user.organization_id,
        email=user.email,
        bound_ip=user.bound_ip,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.put(
    "/profile",
    response_model=UserProfile,
    summary="更新用户资料",
)
async def update_profile(
    request: UpdateUserProfileRequest,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    user = db.get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 用户名和机构ID注册后不可更改，仅允许更新邮箱
    if request.email is not None:
        user.email = request.email

    user.updated_at = datetime.now().isoformat()
    db.update_user(user)

    logger.info(f"[用户] 更新资料 | 用户名: {user.username} | 仅邮箱可更新")

    return UserProfile(
        user_id=user.user_id,
        username=user.username,
        organization_id=user.organization_id,
        email=user.email,
        bound_ip=user.bound_ip,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.post(
    "/reset-password",
    summary="重置密码",
)
async def reset_password(
    request: ResetPasswordRequest,
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
):
    if request.username == "admin":
        raise HTTPException(status_code=400, detail="admin 用户不支持默认密码重置，请使用正常修改密码流程")
    user = db.get_user_by_username(request.username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 登录不再绑定 IP，密码重置也不再校验 IP（鉴权由单点登录 token 保证）
    new_hash = hash_password(request.new_password)
    success = db.update_user_password(request.username, new_hash)

    if not success:
        raise HTTPException(status_code=500, detail="密码更新失败")

    # 密码重置后递增 token 版本号，使该用户所有已登录设备被迫重新登录
    db.increment_user_token_version(request.username)

    logger.info(f"[用户] 密码重置成功 | 用户名: {request.username}")

    return {"status": "success", "message": "密码已重置"}


@router.get(
    "/config",
    summary="获取当前模型配置信息",
)
async def get_auth_config(
    username: Annotated[str, Depends(get_current_username)],
):
    _cfg = get_agent_config()
    max_input_tokens = 200000
    preset_questions = []
    win = False
    agent_env = ""
    if _cfg and _cfg.get("config"):
        max_input_tokens = _cfg["config"].llm.max_input_tokens
        preset_questions = _cfg["config"].preset_questions or []
        win = bool(_cfg.get("win"))
        agent_env = _cfg.get("agent_env", "") or ""
        idle_logout_minutes = _cfg["config"].agent.idle_logout_minutes
    else:
        idle_logout_minutes = 5
    return {
        "max_input_tokens": max_input_tokens,
        "preset_questions": preset_questions,
        "win": win,
        "agent_env": agent_env,
        "idle_logout_minutes": idle_logout_minutes,
    }
