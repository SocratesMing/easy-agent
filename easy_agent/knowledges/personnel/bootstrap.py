"""Preserve administrator bootstrap policy across host database upgrades."""

import os
from easy_agent.bootstrap.env import env_str
import uuid
from datetime import datetime

from easy_agent.models.db import UserModel
from easy_agent.utils.auth import hash_password, verify_password


def bootstrap_password():
    password = os.environ.get("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", "")
    if len(password) < 12 or password.casefold() in {"admin", "123456", "password", "changeme", "easyagent"} or len(set(password)) < 4:
        raise RuntimeError("首次初始化需要通过 EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD 注入长度不少于 12 位的强随机管理员密码")
    return password


def ensure_default_admin(db):
    user = db.get_user_by_username("admin")
    if user:
        production = env_str("AGENT_ENV").casefold() in {"prod", "production"}
        weak = bool(user.password_hash) and any(verify_password(p, user.password_hash) for p in ("admin", "123456"))
        if not user.password_hash or (production and weak):
            db.update_user_password("admin", hash_password(bootstrap_password()))
            return db.get_user_by_username("admin")
        return user
    now = datetime.now().isoformat()
    return db.create_user(UserModel(user_id=str(uuid.uuid4()), username="admin",
        password_hash=hash_password(bootstrap_password()), created_at=now, updated_at=now))


# 注册默认管理员策略：db 层通过注册表调用，避免静态 import personnel。
from easy_agent.db.user_fields_registry import register_default_admin  # noqa: E402

register_default_admin(ensure_default_admin)
