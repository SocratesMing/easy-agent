"""users 域 repository 对象（C1 剩余域 / T3b 收敛）。

把 users 域的全部数据访问收拢为可注入对象：注册/登录/资料/管理员用户管理、
token_version、活跃时间、默认用户/免密用户。T3b 把 ``middleware.auth``、
``services/agents/manager.py`` 等调用方迁到本对象；方法签名全部写全。

边界：

- 本模块不写 SQL，所有方法委托 ``db/repositories/users.py`` 既有函数；
- 不在模块导入期 import FastAPI / middleware，避免 db 层与 Web 层形成回边；
- ``database`` property 仅保留给跨域构造（``api/auth.py`` 的
  ``KnowledgeRepository``），API 层不应对它调用任何 ``Database`` 业务方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from easy_agent.models.db import UserModel
from easy_agent.db.repositories import users as users_repo

if TYPE_CHECKING:
    from easy_agent.db.database import Database


class UserRepository:
    """注册/登录/资料/管理员用户管理所需的领域 repository。"""

    def __init__(self, db: "Database"):
        self._db = db

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供跨域 ``KnowledgeRepository`` 构造使用。"""
        return self._db

    # ── 查询 ──────────────────────────────────────────────────────────

    def get_user_by_username(self, username: str) -> Optional[UserModel]:
        return users_repo.get_user_by_username(self._db, username)

    def get_user_by_id(self, user_id: str) -> Optional[UserModel]:
        return users_repo.get_user_by_id(self._db, user_id)

    def get_user_by_employee_id(self, employee_id: str) -> Optional[UserModel]:
        return users_repo.get_user_by_employee_id(self._db, employee_id)

    def get_user_by_account(self, account: str) -> Optional[UserModel]:
        """按「用户名或工号」定位用户，供登录界面使用。"""
        return users_repo.get_user_by_account(self._db, account)

    def get_user_token_version(self, username: str) -> int:
        return users_repo.get_user_token_version(self._db, username)

    def get_user_bound_ip(self, username: str) -> str:
        return users_repo.get_user_bound_ip(self._db, username)

    def list_users(self, limit: int = 50, offset: int = 0) -> list[UserModel]:
        return users_repo.list_users(self._db, limit=limit, offset=offset)

    def count_users(self) -> int:
        return users_repo.count_users(self._db)

    @staticmethod
    def row_to_user(row) -> UserModel:
        """users 表行 → UserModel（缺列时给默认值，兼容未迁移的旧库）。"""
        return users_repo.row_to_user(row)

    # ── 活跃时间（空闲登出 / SSO token 校验） ────────────────────────

    def touch_user_activity(self, username: str, timestamp: float) -> bool:
        return users_repo.touch_user_activity(self._db, username, timestamp)

    def get_user_activity_time(self, username: str) -> Optional[float]:
        return users_repo.get_user_activity_time(self._db, username)

    def clear_user_activity(self, username: str) -> bool:
        return users_repo.clear_user_activity(self._db, username)

    # ── 写入 ──────────────────────────────────────────────────────────

    def create_user(self, user_data: UserModel) -> UserModel:
        return users_repo.create_user(self._db, user_data)

    def register_user(
        self,
        username: str,
        password: str,
        employee_id: str = "",
        bound_ip: str = "",
    ) -> Optional[UserModel]:
        return users_repo.register_user(
            self._db,
            username,
            password,
            employee_id=employee_id,
            bound_ip=bound_ip,
        )

    def verify_user_password(
        self, account: str, password: str
    ) -> Optional[UserModel]:
        return users_repo.verify_user_password(self._db, account, password)

    def get_or_create_passwordless_user(
        self,
        username: str,
        user_id: str = "",
        profile: Optional[dict] = None,
    ) -> tuple[Optional[UserModel], bool]:
        return users_repo.get_or_create_passwordless_user(
            self._db, username, user_id=user_id, profile=profile
        )

    def get_or_create_default_user(self) -> UserModel:
        return users_repo.get_or_create_default_user(self._db)

    def increment_user_token_version(
        self, username: str, *, require_active: bool = False
    ) -> int:
        return users_repo.increment_user_token_version(
            self._db, username, require_active=require_active
        )

    def update_user(self, user_data: UserModel) -> None:
        return users_repo.update_user(self._db, user_data)

    def update_user_password(self, username: str, new_password_hash: str) -> bool:
        return users_repo.update_user_password(self._db, username, new_password_hash)

    def bind_user_ip(self, username: str, ip: str) -> bool:
        return users_repo.bind_user_ip(self._db, username, ip)

    def delete_user(self, username: str) -> bool:
        return users_repo.delete_user(self._db, username)


__all__ = ["UserRepository"]