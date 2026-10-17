"""users 域 repository 对象（C1 剩余域）。

把 ``Database`` 门面上的 users 领域方法收拢为可注入对象，供
``api/auth.py`` 通过 FastAPI ``Depends`` 使用。

边界：

- 本模块不写 SQL，所有方法委托 ``db/repositories/users.py`` 既有函数；
- 不在模块导入期 import FastAPI / middleware，避免 db 层与 Web 层形成回边；
- ``database`` property 仅用于尚未迁移的 middleware 活跃时间函数，API 层
  不应对它调用任何 ``Database`` 业务方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ...models.db import UserModel
from . import users as users_repo

if TYPE_CHECKING:
    from ..database import Database


class UserRepository:
    """注册/登录/资料/管理员用户管理所需的薄委托层。"""

    def __init__(self, db: "Database"):
        self._db = db

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供 middleware 活跃时间包装函数使用。"""
        return self._db

    # ── 查询 ──────────────────────────────────────────────────────────

    def get_user_by_username(self, username: str) -> Optional[UserModel]:
        return users_repo.get_user_by_username(self._db, username)

    def get_user_by_id(self, user_id: str) -> Optional[UserModel]:
        return users_repo.get_user_by_id(self._db, user_id)

    def get_user_by_employee_id(self, employee_id: str) -> Optional[UserModel]:
        return users_repo.get_user_by_employee_id(self._db, employee_id)

    def get_user_by_account(self, account: str) -> Optional[UserModel]:
        return users_repo.get_user_by_account(self._db, account)

    def get_user_token_version(self, username: str) -> int:
        return users_repo.get_user_token_version(self._db, username)

    def list_users(self, limit: int = 50, offset: int = 0) -> list[UserModel]:
        return users_repo.list_users(self._db, limit=limit, offset=offset)

    def count_users(self) -> int:
        return users_repo.count_users(self._db)

    # ── 写入 ──────────────────────────────────────────────────────────

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
        self, username: str, user_id: str = ""
    ) -> tuple[Optional[UserModel], bool]:
        return users_repo.get_or_create_passwordless_user(
            self._db, username, user_id=user_id
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


__all__ = ["UserRepository"]
