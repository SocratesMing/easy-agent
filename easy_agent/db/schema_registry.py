"""可插拔的 schema 初始化注册表（打破 db <-> 领域模块 的循环依赖）。

`Database.init_tables()` 只负责核心表；领域模块（如 knowledges）在 import 时
把自己的幂等建表函数注册进来，由 Database 统一调用。依赖方向：

    db.database -> db.schema_registry <- knowledges.core.schema

无环，也不需要在 db 里 import 具体领域模块。
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

_SCHEMA_INITIALIZERS: list[Callable] = []


def register_schema_initializer(fn: Callable) -> None:
    """注册一个 `(db, cursor) -> None` 的幂等建表函数（重复注册去重）。"""
    if fn not in _SCHEMA_INITIALIZERS:
        _SCHEMA_INITIALIZERS.append(fn)


def run_schema_initializers(db, cursor) -> None:
    """依次执行已注册的建表函数；单个失败只告警，不阻断启动。"""
    for fn in list(_SCHEMA_INITIALIZERS):
        try:
            fn(db, cursor)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "扩展 schema 初始化失败（功能可能不可用，服务继续启动）| %s | %s",
                getattr(fn, "__module__", "?"),
                exc,
            )


def registered_count() -> int:
    return len(_SCHEMA_INITIALIZERS)
