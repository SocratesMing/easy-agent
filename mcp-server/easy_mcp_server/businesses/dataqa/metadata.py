"""问数元数据：表清单与字段字典（供模型"先探索再写 SQL"）。

数据来源是主库的 `information_schema`，列注释直接取自 MySQL 的 COMMENT，
所以业务侧只要维护好表/字段注释，模型就能拿到中文语义，不需要额外的字典表。

可见性控制（不做行级隔离，但做表级收敛）：

- 默认屏蔽 `mcp_api_keys`（存 Key 哈希）与下划线开头的内部表
- `DATAQA_DENY_TABLES`：追加屏蔽表名（逗号分隔）
- `DATAQA_ALLOWED_TABLES`：白名单，配置后只暴露这些表（逗号分隔）
"""

from __future__ import annotations

import os
import re
from typing import Any

from ...db import mysql_config
from ...env import load_env
from .datasource import readonly_query
from .guard import assert_identifier

DEFAULT_DENY_TABLES: tuple[str, ...] = ("mcp_api_keys",)
_ENUM_RE = re.compile(r"^(enum|set)\((.*)\)$", re.I | re.S)
_ENUM_VALUE_RE = re.compile(r"'((?:[^']|'')*)'")


def current_schema() -> str:
    """当前连接的库名（MYSQL_DATABASE）。"""
    return str(mysql_config().get("database") or "")


def allowed_tables() -> list[str] | None:
    """白名单；未配置则返回 None（表示不限制）。"""
    load_env()
    raw = os.environ.get("DATAQA_ALLOWED_TABLES", "").strip()
    tables = [t.strip() for t in raw.split(",") if t.strip()]
    return tables or None


def denied_tables() -> tuple[str, ...]:
    load_env()
    raw = os.environ.get("DATAQA_DENY_TABLES", "").strip()
    extra = tuple(t.strip() for t in raw.split(",") if t.strip())
    return DEFAULT_DENY_TABLES + extra


def is_visible(table: str) -> bool:
    name = assert_identifier(table)
    if name.startswith("_") or name in denied_tables():
        return False
    allow = allowed_tables()
    return allow is None or name in allow


def _enum_values(column_type: str) -> list[str]:
    match = _ENUM_RE.match(column_type or "")
    if not match:
        return []
    return [v.replace("''", "'") for v in _ENUM_VALUE_RE.findall(match.group(2))]


def list_tables() -> list[dict[str, Any]]:
    """列出可查询的表与视图：名称、类型、中文注释、估算行数、更新时间。

    XBOND 行情的长表视图（`v_xbond_depth`）也是 VIEW，所以这里一并纳管，
    否则模型看不到视图就只能去啃 58 列的宽表。
    """
    rows = readonly_query(
        "SELECT TABLE_NAME AS name, TABLE_TYPE AS object_type, "
        "TABLE_COMMENT AS comment, TABLE_ROWS AS approx_rows, "
        "UPDATE_TIME AS updated_at "
        "FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE IN ('BASE TABLE','VIEW') "
        "ORDER BY TABLE_TYPE, TABLE_NAME",
        (current_schema(),),
    )
    tables: list[dict[str, Any]] = []
    for row in rows:
        name = str(row["name"])
        if name.startswith("_") or name in denied_tables():
            continue
        allow = allowed_tables()
        if allow is not None and name not in allow:
            continue
        tables.append(
            {
                "table": name,
                "type": "view" if str(row.get("object_type")) == "VIEW" else "table",
                "comment": str(row.get("comment") or ""),
                "approx_rows": row.get("approx_rows"),
                "updated_at": row.get("updated_at"),
            }
        )
    return tables


def describe_table(table: str) -> dict[str, Any]:
    """返回单表的字段字典：列名、类型、可空、默认值、索引、注释、枚举取值。"""
    if not is_visible(table):
        raise ValueError(f"表不可查询: {table}")

    name = assert_identifier(table)
    rows = readonly_query(
        "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS column_type, "
        "IS_NULLABLE AS nullable, COLUMN_DEFAULT AS default_value, "
        "COLUMN_KEY AS column_key, COLUMN_COMMENT AS comment, ORDINAL_POSITION AS pos "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
        "ORDER BY ORDINAL_POSITION",
        (current_schema(), name),
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
        (current_schema(), name),
    )
    return {
        "table": name,
        "comment": str(comment[0]["comment"]) if comment else "",
        "columns": columns,
    }


def sample_rows(table: str, limit: int = 5) -> list[dict[str, Any]]:
    """采样真实数据行，帮助模型理解字段的实际取值形态。

    表名已通过 :func:`is_visible` 与标识符校验，此处用反引号包裹是安全的；
    行数上限强制收敛到 [1, 20]。
    """
    if not is_visible(table):
        raise ValueError(f"表不可查询: {table}")

    name = assert_identifier(table)
    size = max(1, min(int(limit or 5), 20))
    return readonly_query(f"SELECT * FROM `{name}` LIMIT {size}")
