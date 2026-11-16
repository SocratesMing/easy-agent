"""Preserve administrator bootstrap policy across host database upgrades."""

from typing import Any

import logging
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from easy_agent.bootstrap.env import env_str, is_unset_placeholder
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.knowledges.personnel.models import ADMIN_DEPARTMENT_ID
from easy_agent.models.db import UserModel
from easy_agent.utils.auth import hash_password, verify_password

logger = logging.getLogger(__name__)

_WEAK_PASSWORDS = frozenset({"admin", "123456", "password", "changeme", "easyagent"})
_ADMIN_PASSWORD_FILE = Path("./data/.admin_bootstrap_password")


def _is_strong_password(password: str) -> bool:
    """长度 >= 12、非弱口令、至少 4 种不同字符。"""
    return (
        len(password) >= 12
        and password.casefold() not in _WEAK_PASSWORDS
        and len(set(password)) >= 4
    )


def _generated_admin_password() -> str:
    """dev/test 首次建库时生成强随机 admin 密码，0600 持久化（日志不打印密码）。"""
    try:
        if _ADMIN_PASSWORD_FILE.exists():
            stored = _ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
            if _is_strong_password(stored):
                return stored
        password = secrets.token_urlsafe(24)
        _ADMIN_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ADMIN_PASSWORD_FILE.write_text(password, encoding="utf-8")
        try:
            os.chmod(_ADMIN_PASSWORD_FILE, 0o600)
        except OSError:
            pass
        logger.info("dev/test 已生成 admin 初始密码 | 文件: %s", _ADMIN_PASSWORD_FILE)
        return password
    except OSError as exc:
        logger.warning("无法持久化 admin 初始密码，改用进程内随机值 | 原因: %s", exc)
        return secrets.token_urlsafe(24)


def bootstrap_password():
    password = os.environ.get("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", "")
    if _is_strong_password(password) and not is_unset_placeholder(password):
        return password
    if env_str("AGENT_ENV", "dev").strip().lower() in {"prod", "production"}:
        raise RuntimeError("生产环境需要通过 EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD 注入长度不少于 12 位的强随机管理员密码")
    return _generated_admin_password()


def _sync_admin_department(db, user):
    """把 admin 固定到系统管理员专职部门，作为公共空间建库的兜底部门。

    老库在下次启动时也会被补齐；组织ID与部门ID必须同时写入，知识库鉴权只读
    ``organization_id``，只写 ``department_id`` 仍会被判定为"没有部门"。
    """
    if user.organization_id == ADMIN_DEPARTMENT_ID and user.department_id == ADMIN_DEPARTMENT_ID:
        return user
    user.organization_id = ADMIN_DEPARTMENT_ID
    user.department_id = ADMIN_DEPARTMENT_ID
    if not user.department_name:
        user.department_name = ADMIN_DEPARTMENT_ID
    UserRepository(db).update_user(user)
    return db.get_user_by_username("admin") or user


def ensure_default_admin(db) -> Any:
    user = db.get_user_by_username("admin")
    if user:
        production = env_str("AGENT_ENV").casefold() in {"prod", "production"}
        weak = bool(user.password_hash) and any(verify_password(p, user.password_hash) for p in ("admin", "123456"))
        if not user.password_hash or (production and weak):
            db.update_user_password("admin", hash_password(bootstrap_password()))
            user = db.get_user_by_username("admin")
        return _sync_admin_department(db, user)
    now = datetime.now().isoformat()
    return db.create_user(UserModel(
        user_id=str(uuid.uuid4()),
        username="admin",
        password_hash=hash_password(bootstrap_password()),
        organization_id=ADMIN_DEPARTMENT_ID,
        department_id=ADMIN_DEPARTMENT_ID,
        department_name=ADMIN_DEPARTMENT_ID,
        created_at=now,
        updated_at=now,
    ))


# 注册默认管理员策略：db 层通过注册表调用，避免静态 import personnel。
from easy_agent.db.user_fields_registry import register_default_admin  # noqa: E402

register_default_admin(ensure_default_admin)
