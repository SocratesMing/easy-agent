"""Independent personnel administration capability for EasyAgent."""

from .api import router
from . import bootstrap as _bootstrap  # noqa: F401  （注册默认管理员策略）
from . import user_fields as _user_fields  # noqa: F401  （注册用户扩展字段读写）

__all__ = ["router"]
