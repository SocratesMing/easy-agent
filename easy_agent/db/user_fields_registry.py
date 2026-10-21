"""Personnel 扩展字段/默认管理员的注册表（解除 db → personnel 反向依赖）。

方向：personnel 领域在 import 时把实现注册进来；db 层只调用注册表。
    knowledges.personnel.user_fields -> db.user_fields_registry <- db.schema / db.repositories.users
实际依赖：knowledges.personnel → registry；db.schema/repositories → registry（无环）。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger("easy_agent.db.user_fields")

_SaveFn = Callable[[object, object, object], None]
_ReadFn = Callable[[object], dict]
_EnsureFn = Callable[[object, object], None]
_DefaultAdminFn = Callable[[object], object]

_save: Optional[_SaveFn] = None
_read: Optional[_ReadFn] = None
_ensure: Optional[_EnsureFn] = None
_default_admin: Optional[_DefaultAdminFn] = None


def register_user_fields(
    *,
    save: _SaveFn,
    read: _ReadFn,
    ensure: _EnsureFn,
) -> None:
    global _save, _read, _ensure
    _save, _read, _ensure = save, read, ensure


def register_default_admin(fn: _DefaultAdminFn) -> None:
    global _default_admin
    _default_admin = fn


def ensure_user_fields(db, cursor) -> None:
    if _ensure is not None:
        _ensure(db, cursor)


def save_user_fields(db, cursor, user) -> None:
    if _save is not None:
        _save(db, cursor, user)


def read_user_fields(row) -> dict:
    if _read is None:
        return {}
    return _read(row)


def get_or_create_default_admin(db):
    """创建/修复默认管理员；personnel 未注册时给出明确错误。"""
    if _default_admin is None:
        raise RuntimeError(
            "默认管理员策略未注册：请先 import easy_agent.knowledges.personnel（应用启动路径会自动完成）"
        )
    return _default_admin(db)
