"""统一环境变量读取（ARCH-09：减少 30+ 处手写 os.getenv 语义漂移）。

约定：
- 布尔值统一 ``1/true/yes/on`` 为真（大小写不敏感）；
- 整数解析失败回退默认值，不抛异常；
- 列表用逗号分隔并去空；
- 普通字符串默认 strip（密钥类需要保留原样时传 ``strip=False``）。
"""

from __future__ import annotations

import os
from typing import Iterable

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def env_str(name: str, default: str = "", *, strip: bool = True) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip() if strip else raw


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name).lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


def env_int(name: str, default: int = 0) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_list(name: str, default: Iterable[str] = (), *, sep: str = ",") -> tuple[str, ...]:
    raw = env_str(name)
    if not raw:
        return tuple(default)
    return tuple(item.strip() for item in raw.split(sep) if item.strip())
