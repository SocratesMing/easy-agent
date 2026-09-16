"""hbaseqa 安全网关：表名/限定符/值 白名单校验、行数上限与 scan 参数收敛。

与 `dataqa.guard` 的定位一致，但这里面对的不是 SQL 而是 Thrift scan：
需要防的是三件事——

1. **打错/打不到隔壁业务表**：表清单来自 Thrift `tables()`，但仍要过
   白名单 + 黑名单 + 前缀过滤，`system:` 命名空间永不放行
2. **一次 scan 拖垮集群**：Thrift scan 没有 SQL 的 `LIMIT` 可用，只能在应用层
   兜底——时间窗缺失时用默认窗口、扫描行数设硬上限、单次返回行数由 `max_rows` 裁剪
3. **过滤器字符串注入**：HBase 的 Filter Language 是一段文本 DSL，
   直接把外部字符串拼进去等价于注入，所以所有进入 DSL 的字面量都要过字符集白名单
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Iterable

from . import config

# HBase 表名允许 `[A-Za-z0-9_.-]` 且可带 `namespace:` 前缀；这里禁掉以点、连字符开头
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*[A-Za-z0-9_](:[A-Za-z_][A-Za-z0-9_\-]*)?$")
# 进入 Filter Language 的限定符 `CF:TIME`
_QUALIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 进入 Filter Language 的字面量：禁掉引号、括号、逗号等 DSL 元字符
_LITERAL_RE = re.compile(r"^[A-Za-z0-9_\-.:+*]+$")
# HBase 内部命名空间
_SYSTEM_NAMESPACES = ("system", "hbase", "default")


def assert_table_name(name: str) -> str:
    """校验表名，杜绝 `namespace:` 逃逸与非法字符。"""
    cleaned = (name or "").strip()
    if not _TABLE_RE.match(cleaned):
        raise ValueError(
            f"非法的表名: {name!r}（只允许字母数字下划线与可选的 namespace: 前缀）"
        )
    if cleaned.split(":", 1)[0].lower() in _SYSTEM_NAMESPACES and ":" in cleaned:
        raise ValueError(f"不允许访问系统命名空间: {cleaned}")
    return cleaned


def assert_family(family: str) -> str:
    cleaned = (family or "").strip()
    if not _QUALIFIER_RE.match(cleaned):
        raise ValueError(f"非法的列族名: {family!r}")
    return cleaned


def assert_literal(value: Any, label: str = "值") -> str:
    """校验拼进 Filter Language 的字面量（不通过即拒绝，不尝试转义）。"""
    text = str(value)
    if not text or not _LITERAL_RE.match(text):
        raise ValueError(f"{label} 含不允许的字符，已拒绝: {value!r}")
    return text


def is_visible(table: str) -> bool:
    """表级可见性：前缀过滤 + 白名单 + 黑名单 + 系统命名空间。"""
    try:
        name = assert_table_name(table)
    except ValueError:
        return False

    allow = config.allowed_tables()
    if allow is not None and name not in allow:
        return False
    if name in config.denied_tables():
        return False
    if not config.list_all_tables():
        prefix = config.table_prefix()
        # 前缀判断看 qualifier：带命名空间的表是 `ns:HSDC_...`，整串比会全部漏掉
        qualifier = name.split(":", 1)[1] if ":" in name else name
        if prefix and not qualifier.startswith(prefix):
            return False
    return True


def filter_visible(tables: Iterable[str]) -> list[str]:
    return sorted(t for t in tables if is_visible(t))


def max_rows() -> int:
    return config.max_rows()


def row_limit(limit: int | None = None) -> int:
    """最终返回行数：用户给的 limit 不能超过配置上限。"""
    cap = max_rows()
    if limit is None:
        return cap
    try:
        asked = int(limit)
    except (TypeError, ValueError):
        return cap
    return max(1, min(asked, cap))


def row_ranges(start_ms: int, end_ms: int) -> list[tuple[str, str]]:
    """把毫秒区间翻译成 rowkey 扫描范围（**可能多段**：每个 region 一段）。

    真实 rowkey 是 `region(2) + time(13) + ...`，时间戳**不在开头**，所以范围必须
    拼上 region 前缀：`[f"{region}{start}", f"{region}{end + 1}")`。
    直接拿 `str(毫秒)` 当 startRow 会一条都扫不到——而且是**静默返回空**，
    这是本业务最容易踩的坑。

    毫秒等宽（13 位）且 region 定长（2 位），所以字典序 == 数值序；HBase 的
    `row_stop` 是开区间，故上界 +1。

    返回空列表表示"无法用 rowkey 范围过滤"，此时上层会退回 `CF:TIME` 列过滤。
    """
    layout = config.rowkey_layout()
    if layout == "region_ts":
        return [
            (f"{region}{start_ms}", f"{region}{end_ms + 1}")
            for region in config.region_prefixes()
        ]
    if layout == "ts":
        return [(str(start_ms), str(end_ms + 1))]
    return []


def use_row_bounds() -> bool:
    """是否能用 rowkey 范围过滤（`region_ts` / `ts` 都可以）。"""
    return config.rowkey_layout() in ("region_ts", "ts")


def build_time_filter(start_ms: int, end_ms: int) -> str | None:
    """构造 `CF:TIME` 的区间过滤（rowkey 不可用时才用）。

    `filterIfMissing=true` 保证缺列的行被丢掉，`latestVersionOnly=false`
    让比较基于最新版本。比较对象是 `binary:` 前缀的字节串。
    """
    if not (config.use_time_column_filter() or not use_row_bounds()):
        return None

    family = assert_family(config.column_family())
    parts: list[str] = []
    if start_ms is not None:
        parts.append(_single_column_filter(family, config.TIME_FIELDS[0], ">=", start_ms))
    if end_ms is not None:
        parts.append(_single_column_filter(family, config.TIME_FIELDS[0], "<=", end_ms))
    return " AND ".join(parts) if parts else None


def _single_column_filter(family: str, qualifier: str, operator: str, value: Any) -> str:
    safe_family = assert_family(family)
    safe_qualifier = assert_literal(qualifier, "限定符")
    safe_value = assert_literal(value, "时间边界")
    if operator not in (">=", "<=", "=", ">", "<"):
        raise ValueError(f"不支持的比较符: {operator}")
    return (
        f"SingleColumnValueFilter('{safe_family}', '{safe_qualifier}', "
        f"{operator}, 'binary:{safe_value}', true, false)"
    )


def as_text(value: Any) -> str:
    """把 bytes / 任意对象安全地转成 str。

    `None` 必须映射成空串而不是 `"None"`：Thrift 的结构体里可选字段没设置时读出来
    就是 None，`str(None)` 会把表名拼成 `None:HSDC_...` 这种不存在的名字
    （真机上表现为 TableNotFoundException，排查起来很费劲）。
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    try:
        return str(value)
    except Exception:
        return repr(value)


def exception_text(exc: BaseException) -> str:
    """异常的"安全字符串化"。

    必须走这里，不能直接 f-string 插值。thriftpy2 的 `TApplicationException` /
    `TProtocolException` 把 `__str__` 实现成"直接返回 message"，而 message 是
    HBase 服务端回传的 bytes，于是 `str(exc)` 抛
    `TypeError: __str__ returned non-string (type bytes)` —— 真实错误被这句
    莫名其妙的 TypeError 盖掉，排查时完全看不到服务端说了什么。

    这里按"最贴近人话"的顺序取信息：`__str__` → `.message` → `.args`，
    兜底再拼类型名，保证任何异常都能拿到一行可读文本。
    """
    try:
        text = str(exc)
        if text:
            return text
    except Exception:
        pass

    parts: list[str] = [type(exc).__name__]
    message = getattr(exc, "message", None)
    if message:
        parts.append(as_text(message))
    args = getattr(exc, "args", ())
    if args:
        parts.append(", ".join(as_text(arg) for arg in args))
    return ": ".join(parts)


def json_safe(value: Any) -> Any:
    """把 bytes / Decimal / datetime 转成可 JSON 序列化的值。

    Thrift 返回的行字典 key 与 value 都是 bytes，这是唯一必须转的一步。
    """
    if isinstance(value, dict):
        return {_decode_key(k): json_safe(v) for k, v in value.items()}
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


def _decode_key(key: Any) -> str:
    if isinstance(key, (bytes, bytearray)):
        return bytes(key).decode("utf-8", "replace")
    return str(key)
