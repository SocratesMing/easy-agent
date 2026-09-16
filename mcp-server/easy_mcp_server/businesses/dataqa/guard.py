"""问数 SQL 安全网关：把模型生成的 SQL 收敛为"单条只读 SELECT"。

问数场景无法像 market 那样枚举固定语句，必须接受模型生成的 SQL，
所以在执行前统一做四层收敛：

1. 去注释后再判断，避免注释绕过关键字检查
2. 只允许单条语句（禁止 `;` 堆叠）
3. 只允许 SELECT / WITH 开头，屏蔽写操作与系统库
4. 强制返回行数上限（缺省自动补 LIMIT）

已知边界：`;` 若出现在字符串字面量中会误杀（保守拒绝，不冒险放行）。
"""

from __future__ import annotations

import os
import re
from typing import Any

from ...env import load_env

DEFAULT_MAX_ROWS = 500

# 只处理 -- 与 /* */：MySQL 的 # 注释必须独占行首/空白之后，
# 而 `#` 常出现在字符串字面量里，剥离反而会破坏 SQL。
_COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.S)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\s*(?:,\s*(\d+))?\s*$", re.I | re.S)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# 写操作 / 危险函数（按词边界匹配，不会误伤 create_time、update_by 这类列名）
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|replace|merge|drop|alter|create|rename|truncate|"
    r"grant|revoke|commit|rollback|call|load_file|outfile|dumpfile|sleep|benchmark)\b",
    re.I,
)
# 系统库：只拦截 schema 限定访问（information_schema.tables），不拦截同名标识符
_SYSTEM_SCHEMA_RE = re.compile(
    r"\b(information_schema|mysql|performance_schema|sys)\s*\.", re.I
)


def max_rows() -> int:
    """单次查询返回的最大行数（DATAQA_MAX_ROWS，缺省 500）。"""
    load_env()
    raw = os.environ.get("DATAQA_MAX_ROWS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_ROWS


def strip_comments(sql: str) -> str:
    return _COMMENT_RE.sub(" ", sql)


def assert_identifier(name: str) -> str:
    """校验表名/库名等标识符，杜绝反引号逃逸与注入。"""
    cleaned = (name or "").strip().strip("`")
    if not _IDENTIFIER_RE.match(cleaned):
        raise ValueError(f"非法的标识符: {name!r}（只允许字母、数字、下划线且不以数字开头）")
    return cleaned


def _apply_limit(sql: str, limit: int) -> str:
    match = _LIMIT_RE.search(sql)
    if not match:
        return f"{sql} LIMIT {limit}"
    first = int(match.group(1))
    offset_form = match.group(2) is not None
    total = int(match.group(2)) if offset_form else first
    if total <= limit:
        return sql
    if offset_form:
        # 带 OFFSET 的形式不改写语义，交由调用方截断结果
        return sql
    return sql[: match.start()] + f"LIMIT {limit}"


def assert_readonly(sql: str, limit: int | None = None) -> str:
    """校验并归一化 SQL，返回可安全执行的语句；不合法则抛 ValueError。"""
    statement = strip_comments(sql).strip().rstrip(";").strip()
    if not statement:
        raise ValueError("SQL 为空")
    if ";" in statement:
        raise ValueError("只允许单条 SQL（检测到分号）")
    if not re.match(r"^(select|with)\b", statement, re.I):
        raise ValueError("只允许 SELECT / WITH 查询")
    if "@@" in statement:
        raise ValueError("不允许读取服务器变量")

    hit = _FORBIDDEN_RE.search(statement)
    if hit:
        raise ValueError(f"包含不允许的关键字: {hit.group(1)}")
    hit = _SYSTEM_SCHEMA_RE.search(statement)
    if hit:
        raise ValueError(f"不允许访问系统库: {hit.group(1)}")

    return _apply_limit(statement, limit or max_rows())


def json_safe(value: Any) -> Any:
    """把 Decimal / datetime / bytes 等转成可 JSON 序列化的值。"""
    from datetime import date, datetime, time, timedelta
    from decimal import Decimal

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return value
