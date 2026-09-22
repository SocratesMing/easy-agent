"""表/字段探索：让模型"先看清楚再写 SQL"。

数据来源是 `information_schema`，列注释直接取自 MySQL 的 COMMENT，
所以业务侧只要维护好表/字段注释，模型就能拿到中文语义，不需要额外的字典表。

可见性由 :class:`ExplorePolicy` 控制（表级收敛，不做行级隔离）：

- 默认屏蔽 ``mcp_api_keys``（存 Key 哈希）与下划线开头的内部表
- 业务可用 ``{前缀}ALLOWED_TABLES`` / ``{前缀}DENY_TABLES`` 定制，
  通过 :func:`policy_from_env` 构造策略
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..db import mysql_config
from .config import DEFAULT_DENY_TABLES, env_list
from .guard import assert_identifier
from .pool import readonly_query

_ENUM_RE = re.compile(r"^(enum|set)\((.*)\)$", re.I | re.S)
_ENUM_VALUE_RE = re.compile(r"'((?:[^']|'')*)'")


@dataclass(frozen=True)
class ExplorePolicy:
    """表级可见性策略。

    Args:
        allowed: 白名单；None 表示不限制（仍受 denied 约束）
        denied: 黑名单，优先于白名单
        schema: 目标库；None 表示当前连接的库
    """

    allowed: tuple[str, ...] | None = None
    denied: tuple[str, ...] = field(default=DEFAULT_DENY_TABLES)
    schema: str | None = None

    def is_visible(self, table: str) -> bool:
        name = assert_identifier(table)
        if name.startswith("_") or name in self.denied:
            return False
        return self.allowed is None or name in self.allowed

    def target_schema(self) -> str:
        return self.schema or str(mysql_config().get("database") or "")


def policy_from_env(prefix: str = "QUERYKIT_") -> ExplorePolicy:
    """从环境变量构造策略：``{prefix}ALLOWED_TABLES`` / ``{prefix}DENY_TABLES``。

    业务包通常传自己的前缀，例如 ``policy_from_env("STRATEGY_")``。
    """
    allowed = env_list(f"{prefix}ALLOWED_TABLES") or None
    denied = DEFAULT_DENY_TABLES + env_list(f"{prefix}DENY_TABLES")
    return ExplorePolicy(allowed=allowed, denied=denied)


def _enum_values(column_type: str) -> list[str]:
    match = _ENUM_RE.match(column_type or "")
    if not match:
        return []
    return [v.replace("''", "'") for v in _ENUM_VALUE_RE.findall(match.group(2))]


def list_tables(policy: ExplorePolicy | None = None) -> list[dict[str, Any]]:
    """列出可查询的表与视图：名称、类型、中文注释、估算行数、更新时间。"""
    policy = policy or ExplorePolicy()
    rows = readonly_query(
        "SELECT TABLE_NAME AS name, TABLE_TYPE AS object_type, "
        "TABLE_COMMENT AS comment, TABLE_ROWS AS approx_rows, "
        "UPDATE_TIME AS updated_at "
        "FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE IN ('BASE TABLE','VIEW') "
        "ORDER BY TABLE_TYPE, TABLE_NAME",
        (policy.target_schema(),),
    )
    return [
        {
            "table": str(row["name"]),
            "type": "view" if str(row.get("object_type")) == "VIEW" else "table",
            "comment": str(row.get("comment") or ""),
            "approx_rows": row.get("approx_rows"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
        if policy.is_visible(str(row["name"]))
    ]


def describe_table(table: str, policy: ExplorePolicy | None = None) -> dict[str, Any]:
    """返回单表的字段字典：列名、类型、可空、默认值、索引、注释、枚举取值。"""
    policy = policy or ExplorePolicy()
    if not policy.is_visible(table):
        raise ValueError(f"表不可查询: {table}")

    name = assert_identifier(table)
    rows = readonly_query(
        "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS column_type, "
        "IS_NULLABLE AS nullable, COLUMN_DEFAULT AS default_value, "
        "COLUMN_KEY AS column_key, COLUMN_COMMENT AS comment, ORDINAL_POSITION AS pos "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
        "ORDER BY ORDINAL_POSITION",
        (policy.target_schema(), name),
    )
    if not rows:
        raise ValueError(f"表不存在或不可见: {name}")

    columns = [
        {
            "name": str(row["name"]),
            "type": str(row["column_type"]),
            "nullable": str(row["nullable"]) == "YES",
            "default": row.get("default_value"),
            "key": str(row.get("column_key") or ""),
            "comment": str(row.get("comment") or ""),
            "enum_values": _enum_values(str(row["column_type"])),
        }
        for row in rows
    ]
    comment = readonly_query(
        "SELECT TABLE_COMMENT AS comment FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (policy.target_schema(), name),
    )
    return {
        "table": name,
        "comment": str(comment[0]["comment"]) if comment else "",
        "columns": columns,
    }


def sample_rows(
    table: str, limit: int = 5, policy: ExplorePolicy | None = None
) -> list[dict[str, Any]]:
    """采样真实数据行，帮助模型理解字段的实际取值形态。

    表名已通过 :meth:`ExplorePolicy.is_visible` 与标识符校验，
    此处用反引号包裹是安全的；行数上限强制收敛到 [1, 20]。
    """
    policy = policy or ExplorePolicy()
    if not policy.is_visible(table):
        raise ValueError(f"表不可查询: {table}")

    name = assert_identifier(table)
    size = max(1, min(int(limit or 5), 20))
    return readonly_query(f"SELECT * FROM `{name}` LIMIT {size}")
