"""Independent personnel administration capability for EasyAgent."""

from easy_agent.personnel.api import router
from easy_agent.personnel import bootstrap as _bootstrap  # noqa: F401  （注册默认管理员策略）
from easy_agent.personnel import user_fields as _user_fields  # noqa: F401  （注册用户扩展字段读写）

__all__ = ["router"]
