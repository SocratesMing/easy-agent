"""用户管理路由"""

import logging
from datetime import datetime
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

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


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
    # 注册时与用户IP强绑定
    client_ip = get_client_ip(http_request)

    user = db.register_user(
        username=request.username,
        password=request.password,
        organization_id=request.organization_id,
        email=request.email,
        bound_ip=client_ip,
    )

    if not user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    access_token = create_access_token(data={"sub": user.username})

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
        f"[用户] 注册成功 | 用户名: {user.username} | 机构ID: {user.organization_id} | 绑定IP: {client_ip}"
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

    if user.bound_ip and user.bound_ip != client_ip:
        logger.warning(
            f"[用户] IP不匹配 | 用户: {user.username} | 绑定IP: {user.bound_ip} | 请求IP: {client_ip}"
        )
        raise HTTPException(
            status_code=403,
            detail=f"账号已绑定IP: {user.bound_ip}，当前IP: {client_ip} 不允许登录",
        )

    if not user.bound_ip:
        db.bind_user_ip(user.username, client_ip)
        logger.info(f"[用户] 首次登录绑定IP | 用户: {user.username} | IP: {client_ip}")

    access_token = create_access_token(data={"sub": user.username})

    max_input_tokens = _get_max_input_tokens()

    logger.info(f"[用户] 登录成功 | 用户名: {user.username} | IP: {client_ip}")

    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        max_input_tokens=max_input_tokens,
    )


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
    db: Annotated[Database, Depends(get_database)],
):
    user = db.get_user_by_username(request.username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    new_hash = hash_password(request.new_password)
    success = db.update_user_password(request.username, new_hash)

    if not success:
        raise HTTPException(status_code=500, detail="密码更新失败")

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
    return {
        "max_input_tokens": max_input_tokens,
        "preset_questions": preset_questions,
        "win": win,
        "agent_env": agent_env,
    }
