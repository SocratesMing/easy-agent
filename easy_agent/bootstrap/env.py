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

# 三份 .env.*.example 模板的敏感项统一写成 ``EASY_AGENT_SET_ME__<变量名>``：
# 既满足「每个变量显式非空」，又保证照抄模板时占位值不会被当作真实密钥/密码/域名。
PLACEHOLDER_PREFIX = "EASY_AGENT_SET_ME__"
_LEGACY_PLACEHOLDERS = frozenset({
    "change_me", "changeme", "replace_me", "replaceme", "set_me", "<set-me>",
})


def is_unset_placeholder(value: str | None) -> bool:
    """判断值是否为空或「待替换占位」（哨兵），供配置展开与安全项共用。

    只做纯字符串判定，不返回 strip 后的值：调用方据此回退默认值或生成安全
    默认值，非占位值必须原样保留（含密钥前后空格）。
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if lowered.startswith(PLACEHOLDER_PREFIX.lower()):
        return True
    return lowered in _LEGACY_PLACEHOLDERS


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
