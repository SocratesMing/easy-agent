"""用户管理路由.

提供用户资料的查询、更新和认证 REST API 接口.
"""

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request

from ...config import Config
from ..db import Database, get_database
from ..models import (
    UserProfile,
    UpdateUserProfileRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    AuthResponse,
)
from ..utils.auth import create_access_token, get_username_from_token, hash_password

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


def get_client_ip(request: Request) -> str:
    """从请求中提取客户端真实IP"""
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
    description="注册一个新用户账号。",
)
async def register(
    request: RegisterRequest,
    db: Annotated[Database, Depends(get_database)],
):
    """注册新用户."""
    user = db.register_user(
        username=request.username,
        password=request.password,
        email=request.email,
    )

    if not user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    access_token = create_access_token(data={"sub": user.username})

    # 注册成功后自动创建用户workspace目录
    try:
        user_workspace = Config.get_user_workspace_dir(user.username)
        user_workspace.mkdir(parents=True, exist_ok=True)
        user_upload = Config.get_user_upload_dir(user.username)
        user_upload.mkdir(parents=True, exist_ok=True)
        logger.info(f"[用户] 创建用户workspace | 用户: {user.username} | 路径: {user_workspace}")
    except Exception as e:
        logger.warning(f"[用户] 创建用户workspace失败 | 用户: {user.username} | 错误: {e}")

    logger.info(f"[用户] 注册成功 | 用户名: {user.username}")

    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="用户登录",
    description="使用用户名和密码登录。首次登录会绑定IP地址，后续只能在绑定IP上登录。",
)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
):
    """用户登录（带IP绑定）."""
    user = db.get_user_by_username(request.username)

    if not user:
        raise HTTPException(status_code=404, detail="用户名不存在")

    user = db.verify_user_password(request.username, request.password)

    if not user:
        raise HTTPException(status_code=401, detail="密码错误")

    client_ip = get_client_ip(http_request)
    bound_ip = db.get_user_bound_ip(user.username)

    if bound_ip:
        # IP 已绑定，校验是否一致
        if bound_ip != client_ip:
            logger.warning(f"[用户] IP绑定校验失败 | 用户: {user.username} | 绑定IP: {bound_ip} | 当前IP: {client_ip}")
            raise HTTPException(status_code=403, detail=f"账号已绑定IP {bound_ip}，当前IP {client_ip} 无访问权限")
    else:
        # 首次登录，绑定IP
        db.bind_user_ip(user.username, client_ip)
        logger.info(f"[用户] IP绑定成功 | 用户: {user.username} | IP: {client_ip}")

    # 确保用户workspace和upload目录存在
    try:
        user_workspace = Config.get_user_workspace_dir(user.username)
        user_workspace.mkdir(parents=True, exist_ok=True)
        user_upload = Config.get_user_upload_dir(user.username)
        user_upload.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"[用户] 创建工作区失败 | 用户: {user.username} | 错误: {e}")

    access_token = create_access_token(data={"sub": user.username, "ip": client_ip})

    logger.info(f"[用户] 登录成功 | 用户名: {user.username} | IP: {client_ip}")

    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
    )


@router.post(
    "/reset-password",
    summary="重置密码",
    description="重置用户密码。",
)
async def reset_password(
    request: ResetPasswordRequest,
    db: Annotated[Database, Depends(get_database)],
):
    """重置用户密码."""
    user = db.get_user_by_username(request.username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    new_password_hash = hash_password(request.new_password)
    success = db.update_user_password(request.username, new_password_hash)

    if not success:
        raise HTTPException(status_code=500, detail="密码重置失败")

    logger.info(f"[用户] 密码重置成功 | 用户名: {request.username}")

    return {
        "success": True,
        "message": "密码重置成功",
    }


@router.delete(
    "/unregister",
    summary="注销用户",
    description="注销当前用户账号，删除用户信息和workspace下的相关文件夹。",
)
async def unregister(
    db: Annotated[Database, Depends(get_database)],
    authorization: Annotated[Optional[str], Header()] = None,
):
    """注销用户账号，删除用户信息和workspace下的相关文件夹."""
    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证信息")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    username = get_username_from_token(token)

    if not username:
        raise HTTPException(status_code=401, detail="无效的认证信息")

    user = db.get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    env_workspace = os.environ.get("EASY_WORKSPACE_DIR")
    if env_workspace:
        workspace = Path(env_workspace)
    else:
        project_root = Path(__file__).parent.parent.parent.parent
        workspace = project_root / "workspace"

    safe_username = Config.sanitize_username(username)
    user_workspace = workspace / safe_username

    if user_workspace.exists() and user_workspace.is_dir():
        try:
            shutil.rmtree(user_workspace)
            logger.info(f"[用户] 删除用户workspace | 用户: {username} | 路径: {user_workspace}")
        except Exception as e:
            logger.error(f"[用户] 删除用户workspace失败 | 用户: {username} | 路径: {user_workspace} | 错误: {e}")

    success = db.delete_user(username)

    if not success:
        raise HTTPException(status_code=500, detail="删除用户失败")

    logger.info(f"[用户] 注销成功 | 用户名: {username}")

    return {
        "success": True,
        "message": "用户注销成功",
    }


@router.get(
    "/me",
    response_model=UserProfile,
    summary="获取当前用户资料",
    description="获取当前登录用户的资料信息。",
)
async def get_current_user_profile(
    db: Annotated[Database, Depends(get_database)],
    username: str = None,
):
    """获取当前用户资料（通过 username 参数）."""
    if not username:
        user = db.get_or_create_default_user()
    else:
        user = db.get_user_by_username(username)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

    logger.info(f"[用户] 获取用户资料 | 用户ID: {user.user_id} | 用户名: {user.username}")
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
    description="更新当前用户的资料信息。",
)
async def update_user_profile(
    request: UpdateUserProfileRequest,
    db: Annotated[Database, Depends(get_database)],
    username: str = None,
):
    """更新用户资料."""
    if not username:
        user = db.get_or_create_default_user()
    else:
        user = db.get_user_by_username(username)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

    if request.username is not None:
        existing_user = db.get_user_by_username(request.username)
        if existing_user and existing_user.user_id != user.user_id:
            raise HTTPException(status_code=400, detail="用户名已存在")
        user.username = request.username

    if request.organization_id is not None:
        user.organization_id = request.organization_id

    if request.email is not None:
        user.email = request.email

    user.updated_at = datetime.now().isoformat()
    db.update_user(user)

    logger.info(f"[用户] 更新用户资料 | 用户ID: {user.user_id} | 用户名: {user.username}")

    return UserProfile(
        user_id=user.user_id,
        username=user.username,
        organization_id=user.organization_id,
        email=user.email,
        bound_ip=user.bound_ip,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


profile_router = APIRouter(
    prefix="/api/user",
    tags=["User Profile"],
)


@profile_router.get(
    "/profile",
    response_model=UserProfile,
    summary="获取用户资料",
    description="获取当前登录用户的资料信息。",
)
async def get_user_profile(
    db: Annotated[Database, Depends(get_database)],
    authorization: Annotated[Optional[str], Header()] = None,
):
    """获取用户资料."""
    username = None
    if authorization:
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        username = get_username_from_token(token)

    if not username:
        user = db.get_or_create_default_user()
    else:
        user = db.get_user_by_username(username)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

    logger.info(f"[用户] 获取用户资料 | 用户ID: {user.user_id} | 用户名: {user.username}")
    return UserProfile(
        user_id=user.user_id,
        username=user.username,
        organization_id=user.organization_id,
        email=user.email,
        bound_ip=user.bound_ip,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
