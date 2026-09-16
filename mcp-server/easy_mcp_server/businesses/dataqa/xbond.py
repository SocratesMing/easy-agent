"""XBOND 本币债券行情领域层。

基表 `fmut_mkt_bonddpanyhis` 是 5 档宽表：BID / OFFER × 5 档 × (清算速度, 量, 净价,
到期收益率)，共 58 列。直接把这张表丢给模型写 SQL 有三个问题：

1. 列名高度重复（`BID_PRICE1..5` / `OFFER_YIELD1..5`），模型极易选错档位
2. 一行 58 列，取回来的结果又宽又大，很快撞上返回上限
3. 时间字段是 `bigint(14)` 的 `YYYYMMDDHHMMSS`，模型容易写错过滤条件

所以这里做两件事：

- **长表形态**：把 5 档 UNPIVOT 成行，一行 = 一只债券 × 一个时刻 × 一档的双边报价
- **领域查询构造**：时间窗 + 债券 + 档位 + 粒度（最新 / 每日 / 明细）

关于视图 `v_xbond_depth`：MySQL 对含 `UNION ALL` 的视图强制使用 TEMPTABLE 算法，
**外层 WHERE 不会下推到基表索引**，等价于先物化整张历史表再过滤，月度查询会全表扫。
因此：

- 视图只用于探索（`describe_table` / `sample_rows` / 小范围即席 `query`）
- `get_bond_quotes` / `list_bonds` 走 `long_format_sql()`，把时间与债券条件
  下推到每一档分支，命中基表索引 `IDX_FMUT_MKT_BONDDPANYHIS(UPDATE_TIME2, ...)`
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Iterable

from ...env import load_env
from .guard import assert_identifier, max_rows

DEFAULT_BASE_TABLE = "fmut_mkt_bonddpanyhis"
DEFAULT_DEPTH_VIEW = "v_xbond_depth"

MAX_LEVEL = 5
DEFAULT_DAYS = 30
DEFAULT_ROW_LIMIT = 500
GRANULARITIES = ("latest", "daily", "raw")

# 长表形态的列（顺序即返回顺序）
VIEW_COLUMNS = (
    "update_time",
    "update_date",
    "update_time_str",
    "feed_code",
    "bond_code",
    "bond_name",
    "bond_type",
    "term",
    "market_depth",
    "price_id",
    "transact_time",
    "level_no",
    "bid_settletype",
    "bid_qty",
    "bid_price",
    "bid_yield",
    "offer_settletype",
    "offer_qty",
    "offer_price",
    "offer_yield",
)


def base_table() -> str:
    load_env()
    # 空值回退默认：把变量留空是常见操作，不能让空串变成表名
    return os.environ.get("XBOND_TABLE", "").strip() or DEFAULT_BASE_TABLE


def depth_view() -> str:
    load_env()
    return os.environ.get("XBOND_VIEW", "").strip() or DEFAULT_DEPTH_VIEW


def default_days() -> int:
    load_env()
    raw = os.environ.get("XBOND_DEFAULT_DAYS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_DAYS


# ── 长表形态 ────────────────────────────────────────────────────────────


def _level_select(level: int, table: str, extra_where: str = "") -> str:
    """生成单个档位的长表 SELECT；extra_where 会被下推到本分支（可用上索引）。"""
    # 第 1 档的买入清算速度列名没有数字后缀（基表就是这样定义的）
    bid_settletype = "BID_SETTLTYPE" if level == 1 else f"BID_SETTLTYPE{level}"
    tail = f" AND {extra_where}" if extra_where else ""
    return f"""SELECT
        UPDATE_TIME2 AS update_time,
        UPDATE_DATE AS update_date,
        UPDATE_TIME AS update_time_str,
        FEED_CODE AS feed_code,
        BOND_CODE AS bond_code,
        BOND_NAME AS bond_name,
        BOND_TYPE AS bond_type,
        TERM_MONTHLY_RATE AS term,
        MARKET_DEPTH AS market_depth,
        PRICE_ID AS price_id,
        TRAN_TRANSACT_TIME AS transact_time,
        {level} AS level_no,
        {bid_settletype} AS bid_settletype,
        BID_ORDER_QTY{level} AS bid_qty,
        BID_PRICE{level} AS bid_price,
        BID_YIELD{level} AS bid_yield,
        OFFER_SETTLTYPE{level} AS offer_settletype,
        OFFER_ORDER_QTY{level} AS offer_qty,
        OFFER_PRICE{level} AS offer_price,
        OFFER_YIELD{level} AS offer_yield
    FROM {table}
    WHERE (BID_PRICE{level} IS NOT NULL OR OFFER_PRICE{level} IS NOT NULL){tail}"""


def create_view_sql(table: str | None = None, view: str | None = None) -> str:
    """生成长表视图 DDL（幂等，CREATE OR REPLACE）。

    仅用于探索与小范围即席查询；大范围取数请用 :func:`long_format_sql`，
    它把过滤条件下推到每个分支，避免 UNION ALL 视图被 TEMPTABLE 物化。
    """
    table_name = assert_identifier(table or base_table())
    view_name = assert_identifier(view or depth_view())
    parts = [_level_select(level, table_name) for level in range(1, MAX_LEVEL + 1)]
    return f"CREATE OR REPLACE VIEW {view_name} AS\n" + "\nUNION ALL\n".join(parts)


def _branch_conditions(days: int | None, bond_codes: Iterable[str] | None):
    """分支内条件下推：时间走 UPDATE_TIME2（有索引），债券走 BOND_CODE。

    库里的债券代码带市场后缀（如 `2105005.IB`），但用户和模型常常只给裸代码
    （`2105005`）。若一律精确匹配，裸代码会静默返回空——比报错更难排查。
    因此：不带 "." 的代码同时按"精确等于"和"代码 + 后缀"匹配；
    带后缀的仍走精确匹配，避免前缀匹配引入歧义。
    """
    conditions = ["UPDATE_TIME2 >= %s"]
    params: list[Any] = [cutoff_time(days)]
    codes = [str(c).strip() for c in (bond_codes or []) if str(c).strip()]
    if codes:
        parts: list[str] = []
        for code in codes:
            if "." in code:
                parts.append("BOND_CODE = %s")
                params.append(code)
            else:
                parts.append("(BOND_CODE = %s OR BOND_CODE LIKE %s)")
                params.extend([code, f"{code}.%"])
        conditions.append("(" + " OR ".join(parts) + ")")
    return conditions, params


def long_format_sql(
    bond_codes: Iterable[str] | None = None,
    days: int | None = None,
    level: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """生成 5 档展开的长表 SQL，并把过滤条件下推到每个分支。

    指定 level 时只生成该档位的分支（少扫 4/5 的数据），否则生成全部 5 档。
    """
    table_name = assert_identifier(base_table())
    level_no = _validate_level(level)
    levels = [level_no] if level_no else list(range(1, MAX_LEVEL + 1))

    conditions, params = _branch_conditions(days, bond_codes)
    branch_where = " AND ".join(conditions)
    branches = [_level_select(lv, table_name, branch_where) for lv in levels]
    return "\nUNION ALL\n".join(branches), tuple(params * len(levels))


# ── 时间 ────────────────────────────────────────────────────────────────


def cutoff_time(days: int | None = None) -> int:
    """近 N 天的起始时刻，返回与 `UPDATE_TIME2` 同型的 `YYYYMMDDHHMMSS` 整数。"""
    window = int(days) if days and int(days) > 0 else default_days()
    window = min(window, 3650)
    start = datetime.now() - timedelta(days=window)
    return int(start.strftime("%Y%m%d") + "000000")


def format_update_time(value: Any) -> str:
    """把 20260908143000 格式化成 2026-09-08 14:30:00，便于阅读。"""
    text = str(value or "")
    if len(text) != 14 or not text.isdigit():
        return text
    return (
        f"{text[0:4]}-{text[4:6]}-{text[6:8]} "
        f"{text[8:10]}:{text[10:12]}:{text[12:14]}"
    )


# ── 查询构造 ────────────────────────────────────────────────────────────


def _normalize_codes(bond_codes: Iterable[str] | None) -> list[str]:
    return [str(code).strip() for code in (bond_codes or []) if str(code).strip()]


def _validate_level(level: int | None) -> int | None:
    if level is None:
        return None
    value = int(level)
    if not 1 <= value <= MAX_LEVEL:
        raise ValueError(f"档位只能是 1~{MAX_LEVEL}，收到 {value}")
    return value


def validate_granularity(granularity: str) -> str:
    value = (granularity or "daily").strip().lower()
    if value not in GRANULARITIES:
        raise ValueError(f"粒度只能是 {GRANULARITIES}，收到 {granularity!r}")
    return value


def row_limit(limit: int | None = None) -> int:
    return max(1, min(int(limit) if limit else DEFAULT_ROW_LIMIT, max_rows()))


def list_bonds_sql(
    keyword: str | None = None,
    days: int | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """近 N 天出现过的债券清单（代码 / 名称 / 类型 / 待偿期 / 最近更新时间）。"""
    inner, inner_params = long_format_sql(days=days)
    conditions: list[str] = []
    params: list[Any] = []
    keyword = (keyword or "").strip()
    if keyword:
        conditions.append("(bond_code LIKE %s OR bond_name LIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        "SELECT bond_code, MAX(bond_name) AS bond_name, MAX(bond_type) AS bond_type, "
        "MAX(term) AS term, COUNT(*) AS quote_rows, MAX(update_time) AS last_update "
        f"FROM (\n{inner}\n) u{where} "
        f"GROUP BY bond_code ORDER BY last_update DESC LIMIT {row_limit(limit)}"
    )
    return sql, tuple(list(inner_params) + params)


def depth_sql(
    bond_codes: Iterable[str] | None = None,
    days: int | None = None,
    level: int | None = None,
    granularity: str = "daily",
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """近 N 天的 XBOND 双边行情。

    - latest：每只债券取最新一条
    - daily ：每只债券每天取当天最后一条（"近一个月行情"的默认形态）
    - raw   ：明细，按时间倒序

    时间窗与债券条件在每个分支内过滤（命中 UPDATE_TIME2 索引）；
    daily / latest 需要在长表之上再做一次聚合，因此长表 SQL 会出现两次，参数也传两遍。
    """
    granularity = validate_granularity(granularity)
    limit_n = row_limit(limit)
    inner, inner_params = long_format_sql(bond_codes, days, level)

    if granularity == "raw":
        sql = (
            f"SELECT * FROM (\n{inner}\n) u "
            f"ORDER BY update_time DESC, bond_code, level_no LIMIT {limit_n}"
        )
        return sql, inner_params

    if granularity == "latest":
        group = f"SELECT bond_code, MAX(update_time) AS mt FROM (\n{inner}\n) u GROUP BY bond_code"
        join_on = "t.bond_code = d.bond_code AND t.mt = d.update_time"
        order_by = "d.bond_code, d.level_no"
    else:
        group = (
            f"SELECT bond_code, update_date, MAX(update_time) AS mt "
            f"FROM (\n{inner}\n) u GROUP BY bond_code, update_date"
        )
        # update_date 可能为 NULL，用 <=> 做空安全比较
        join_on = (
            "t.bond_code = d.bond_code AND t.update_date <=> d.update_date "
            "AND t.mt = d.update_time"
        )
        order_by = "d.update_time DESC, d.bond_code, d.level_no"

    sql = (
        f"SELECT d.* FROM (\n{inner}\n) d "
        f"JOIN (\n{group}\n) t ON {join_on} "
        f"ORDER BY {order_by} LIMIT {limit_n}"
    )
    return sql, tuple(inner_params * 2)


# ── 结果整理 ────────────────────────────────────────────────────────────


def format_quotes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按长表列序输出，补可读时间，并对聚合 join 可能产生的重复行去重。"""
    formatted: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for row in rows:
        key = (
            row.get("bond_code"),
            row.get("update_time"),
            row.get("level_no"),
            row.get("price_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        item = {column: row.get(column) for column in VIEW_COLUMNS}
        item["update_time_readable"] = format_update_time(row.get("update_time"))
        formatted.append(item)
    return formatted


def summarize_quotes(
    rows: list[dict[str, Any]], days: int | None, granularity: str
) -> str:
    if not rows:
        return f"近 {days or default_days()} 天没有匹配的 XBOND 行情数据。"
    bonds = {row.get("bond_code") for row in rows}
    times = sorted(str(row.get("update_time") or "") for row in rows)
    return (
        f"近 {days or default_days()} 天（{granularity}）共 {len(rows)} 行，"
        f"覆盖 {len(bonds)} 只债券，时间范围 "
        f"{format_update_time(times[0])} ~ {format_update_time(times[-1])}。"
    )


def schema_help() -> dict[str, Any]:
    """给模型看的口径说明：表、视图、档位、时间字段与典型问法。"""
    return {
        "base_table": base_table(),
        "base_table_note": "5 档宽表（58 列），列名为 BID_*/OFFER_* 加档位后缀，不要直接查",
        "view": depth_view(),
        "view_note": "长表视图，一行 = 一只债券 × 一个时刻 × 一档双边报价。"
        "注意：UNION ALL 视图在 MySQL 中是 TEMPTABLE 算法，外层时间过滤不下推索引，"
        "只适合探索与小范围即席查询；大范围取数用 get_bond_quotes",
        "columns": {
            "bond_code": "债券代码，带市场后缀（如 2105005.IB）；传裸代码（2105005）也能匹配",
            "bond_name": "债券名称",
            "bond_type": "债券类型",
            "term": "待偿期（源库 varchar）",
            "level_no": "报价档位，1 为最优档（默认只取 1）",
            "bid_price": "买入净价",
            "bid_yield": "买入到期收益率",
            "bid_qty": "买入报价量",
            "bid_settletype": "买入清算速度",
            "offer_price": "卖出净价",
            "offer_yield": "卖出到期收益率",
            "offer_qty": "卖出报价量",
            "offer_settletype": "卖出清算速度",
            "update_time": "bigint YYYYMMDDHHMMSS，过滤时间一律用它（有索引）",
            "update_date": "源库的更新日期（varchar，可能与 update_time 不一致）",
            "update_time_str": "源库的更新时间（varchar）",
            "price_id": "价格编号，同一时刻可能存在多个报价来源",
        },
        "time_filter": "WHERE update_time >= <YYYYMMDD000000 整数>，例如近一个月用 "
        f"{cutoff_time(30)}；不要对 update_time 做字符串比较",
        "typical_questions": {
            "近一个月各债券行情": "get_bond_quotes(days=30, granularity='daily', level=1)",
            "某只债券最新报价": "get_bond_quotes(bond_codes=['<代码>'], granularity='latest')",
            "有哪些债券": "list_bonds(days=30)",
            "非标准口径": "query(sql)，对视图写只读 SELECT",
        },
    }
