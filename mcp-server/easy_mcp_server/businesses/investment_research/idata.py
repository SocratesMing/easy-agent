"""iData 数据分析看板领域层（四张表）。

| key | 表 | 页面 |
| --- | --- | --- |
| ``daily`` | `fmqt_bond_exec_part_data` | 机构当日（现券成交分机构统计） |
| ``bond`` | `fmqt_bond_trade_counterpart_analysis` | 个券机构（个债交易对手分析） |
| ``lending`` | `fmqt_bond_lending_analysis` | 债券借贷（个债债券借贷分析） |
| ``llm`` | `fmqt_idata_llm_analysis` | 大模型点评 |

**统一口径**（原始 SQL 里的差异在这里收敛，工具层不再各写一遍）：

1. 日期一律 ``YYYYMMDD`` 字符串；``date`` 留空 → 由工具层先查最新交易日
2. 金额**统一换算成亿元**（原文档里"机构当日=亿元、另两表=元"的混用已消除）
3. ``SIDE='1'`` 买入、``SIDE='2'`` 卖出，净买入 = 买 − 卖（正=净买入）
4. 集中度 / 占比单位是 **%**（``24.53`` 表示 24.53%）
5. **机构当日表的合计行陷阱**：该表同时存明细行与合计行，SQL 必须显式区分
   （``include_total`` 参数），混用会让金额翻倍

**合并来源**：本模块把原始文档的 29 条 SQL 收敛为少量参数化查询，其中

- 交易日类（原 1.1-1.4 / 1.13 / 2.1 / 2.2 / 3.1-3.3，共 10 条）→ :func:`trade_dates_sql`
- 机构流向类（原 1.5-1.12 / 1.14，共 9 条）→ :func:`institution_flow_sql` + 维度白名单
- 个券机构 / 借贷 / 点评 → 各自的参数化查询

设计说明见 ``mcp-server/iData-MCP工具设计.md``。
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Sequence

from ...env import load_env
from ...querykit import assert_identifier, env_list, max_rows

# ── 表与日期列 ──────────────────────────────────────────────────────────

TABLES: dict[str, str] = {
    "daily": "fmqt_bond_exec_part_data",
    "bond": "fmqt_bond_trade_counterpart_analysis",
    "lending": "fmqt_bond_lending_analysis",
    "llm": "fmqt_idata_llm_analysis",
}

DATE_COLUMNS: dict[str, str] = {
    "daily": "STATISTIC_REPORT_RANGE",
    "bond": "TRANSACT_DATE",
    "lending": "TRANSACT_DATE",
}

SOURCES: tuple[str, ...] = ("daily", "bond", "lending")

DEFAULT_ROW_LIMIT = 500
DEFAULT_DATE_LIMIT = 250
MAX_DATES = 60  # 单次查询允许的日期个数上限，避免模型一次传几百个日期

_DATE_RE = re.compile(r"^\d{8}$")


# ── 配置 ────────────────────────────────────────────────────────────────


def schema_name() -> str:
    """表所在库；留空表示当前连接指向的库（内网与其他环境常不同，用 IDATA_SCHEMA 指定）。"""
    load_env()
    return os.environ.get("IDATA_SCHEMA", "").strip()


def table_ref(key: str) -> str:
    """SQL 表引用（含可选库名前缀），标识符全部经校验。"""
    if key not in TABLES:
        raise ValueError(f"未知数据源: {key!r}（可选 {', '.join(TABLES)}）")
    table = assert_identifier(TABLES[key])
    schema = schema_name()
    if schema:
        return f"`{assert_identifier(schema)}`.`{table}`"
    return f"`{table}`"


def row_limit(limit: int | None = None, default: int = DEFAULT_ROW_LIMIT) -> int:
    return max(1, min(int(limit) if limit else default, max_rows()))


# ── 输入校验 ────────────────────────────────────────────────────────────


def validate_date(value: str) -> str:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        raise ValueError(f"日期必须是 YYYYMMDD 格式（收到 {value!r}）")
    return text


def normalize_dates(dates: Sequence[str] | None, single: str | None = None) -> list[str]:
    """把 ``dates`` 列表 / ``date`` 单值统一成去重后的日期列表（保持原顺序）。"""
    raw: list[str] = []
    if dates:
        raw.extend(str(d) for d in dates)
    if single:
        raw.append(str(single))

    result: list[str] = []
    for item in raw:
        text = validate_date(item)
        if text not in result:
            result.append(text)
    if len(result) > MAX_DATES:
        raise ValueError(f"一次最多查询 {MAX_DATES} 个交易日（收到 {len(result)} 个）")
    return result


def validate_source(source: str) -> str:
    text = str(source or "").strip().lower() or "daily"
    if text not in SOURCES:
        raise ValueError(f"source 只支持 {', '.join(SOURCES)}（收到 {source!r}）")
    return text


def _like_params(keyword: str) -> tuple[str, list[str]]:
    clause = "(STATISTIC_DETAIL_ID LIKE %s OR STATISTIC_DETAIL_NAME LIKE %s)"
    value = f"%{keyword}%"
    return clause, [value, value]


# ── 可复用 SQL 片段（消除逐字重复） ─────────────────────────────────────

# 机构当日表的金额表达式：源表是 varchar，必须 CAST 后聚合。
# 列名统一加反引号：SIDE / OPEN / CLOSE / TIME 这类词在部分引擎里是关键字。
_AMT = "CAST(`TRADE_CASHAMT` AS DECIMAL(20,2))"
_NET = (
    f"(SUM(CASE WHEN `SIDE`='1' THEN {_AMT} ELSE 0 END)"
    f" - SUM(CASE WHEN `SIDE`='2' THEN {_AMT} ELSE 0 END))"
)
BUY_YI = f"ROUND(SUM(CASE WHEN `SIDE`='1' THEN {_AMT} ELSE 0 END) / 100000000, 2)"
SELL_YI = f"ROUND(SUM(CASE WHEN `SIDE`='2' THEN {_AMT} ELSE 0 END) / 100000000, 2)"
NET_YI = f"ROUND({_NET} / 100000000, 2)"

# 券种归一化（原文档 1.6 / 1.8 里逐字重复了两次）
BOND_TYPE_CASE = """CASE
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` = '合计' THEN '合计'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` IN ('国债-新债','国债-老债') THEN '国债'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` IN ('政策性金融债-新债','政策性金融债-老债') THEN '政金债'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` = '中期票据' THEN '中票'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` = '短期/超短期融资券' THEN '短融超短融'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` = '企业债' THEN '企业债'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` = '地方政府债' THEN '地方债'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` = '同业存单' THEN '同业存单'
    WHEN `RELATED_STATISTIC_SUBDETAIL_NAME` = '资产支持证券' THEN 'ABS'
    ELSE '其他' END"""

# 机构当日表的期限分桶（10 年以上合并为一档）
TERM_BUCKET_CASE = """CASE
    WHEN `STATISTIC_SUBDETAIL_NAME` IN ('1年及1年以下','1-3年','3-5年','5-7年','7-10年')
      THEN `STATISTIC_SUBDETAIL_NAME`
    ELSE '大于10年' END"""

# 借贷余额口径：金额类型 19 = 借贷余额，方向 1 = 融入（不可放松）
LENDING_BALANCE = (
    "SUM(CASE WHEN `STATISTIC_AMT_TYPE` = '19' AND `STATISTIC_AMT_SIDE` = '1'"
    " THEN CAST(`STATISTIC_AMT_VALUE` AS DECIMAL(20,2)) ELSE 0 END)"
)
LENDING_CONCENTRATION = "MAX(CAST(`BOND_CONCENTRATION` AS DECIMAL(20,6)))"


# ── 维度白名单（GROUP BY 不能用占位符，只能映射） ──────────────────────

DIMENSIONS: dict[str, str] = {
    "date": f"`{DATE_COLUMNS['daily']}`",
    "institution": "`STATISTIC_DETAIL_NAME`",
    "term": "`STATISTIC_SUBDETAIL_NAME`",
    "bond_type": BOND_TYPE_CASE,
}

# 券种过滤条件（原文档 1.11 的 bond_filter 取值表）
BOND_FILTERS: dict[str, str] = {
    "国债": "`RELATED_STATISTIC_SUBDETAIL_NAME` IN ('国债-新债','国债-老债')",
    "政金债": "`RELATED_STATISTIC_SUBDETAIL_NAME` IN ('政策性金融债-新债','政策性金融债-老债')",
    "信用债": (
        "`RELATED_STATISTIC_SUBDETAIL_NAME` IN "
        "('中期票据','短期/超短期融资券','企业债','资产支持证券')"
    ),
    "地方政府债": "`RELATED_STATISTIC_SUBDETAIL_NAME` = '地方政府债'",
    "同业存单": "`RELATED_STATISTIC_SUBDETAIL_NAME` = '同业存单'",
    "全部": "`RELATED_STATISTIC_SUBDETAIL_NAME` != '合计'",
    "合计": "`RELATED_STATISTIC_SUBDETAIL_NAME` = '合计'",
}

# 券种中文名 → 归一化名（应用层用，与 BOND_TYPE_CASE 保持一致）
_BOND_TYPE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("国债-新债", "国债-老债"), "国债"),
    (("政策性金融债-新债", "政策性金融债-老债"), "政金债"),
    (("中期票据",), "中票"),
    (("短期/超短期融资券",), "短融超短融"),
    (("企业债",), "企业债"),
    (("地方政府债",), "地方债"),
    (("同业存单",), "同业存单"),
    (("资产支持证券",), "ABS"),
)

SCENES: tuple[str, ...] = ("institution_daily", "bond_institution", "bond_lending")
LLM_SCENE_LABELS: dict[str, str] = {
    "institution_daily": "机构当日",
    "bond_institution": "个券机构",
    "bond_lending": "债券借贷",
}


# ── 交易日查询（原 1.1-1.4 / 1.13 / 2.1 / 2.2 / 3.1-3.3 共 10 条） ────────


def _date_conditions(
    column: str,
    before: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[list[str], list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if start and end:
        conditions.append(f"`{column}` BETWEEN %s AND %s")
        params.extend([validate_date(start), validate_date(end)])
    else:
        if before:
            conditions.append(f"`{column}` < %s")
            params.append(validate_date(before))
        if start:
            conditions.append(f"`{column}` >= %s")
            params.append(validate_date(start))
        if end:
            conditions.append(f"`{column}` <= %s")
            params.append(validate_date(end))
    return conditions, params


def trade_dates_sql(
    source: str = "daily",
    limit: int | None = None,
    before: str | None = None,
    start: str | None = None,
    end: str | None = None,
    ascending: bool = False,
) -> tuple[str, tuple[Any, ...]]:
    """交易日查询：最新 / 上一交易日 / 最近 N 个 / 区间内，一个入口全覆盖。

    - ``limit`` 缺省 1（即"最新交易日"）
    - ``before`` + ``limit=1`` 即"上一交易日"
    - ``start`` / ``end`` 走区间（含端点）
    """
    key = validate_source(source)
    column = DATE_COLUMNS[key]
    conditions, params = _date_conditions(column, before=before, start=start, end=end)

    size = max(1, min(int(limit) if limit else 1, DEFAULT_DATE_LIMIT))
    direction = "ASC" if ascending else "DESC"
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        f"SELECT DISTINCT `{column}` AS d FROM {table_ref(key)}{where} "
        f"ORDER BY d {direction} LIMIT {size}"
    )
    return sql, tuple(params)


# ── 机构当日（原 1.5-1.12 / 1.14 共 9 条 → 一个参数化查询） ─────────────


def institution_flow_sql(
    dates: Sequence[str] | None = None,
    group_by: Sequence[str] | None = None,
    include_total: bool = True,
    bond_type: str | None = None,
    institution: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """机构流向：单日矩阵 / 多日趋势 / 期限堆叠，靠 ``group_by`` 维度组合切换。

    Args:
        dates: 交易日列表（必填，工具层负责把"缺省取最新"解析成具体日期）
        group_by: 维度组合，取值见 :data:`DIMENSIONS`；默认 ``["institution"]``
        include_total: True 用合计行（单日汇总口径），False 用明细行；
            **两者不可混用，混用会让金额翻倍**
        bond_type: 券种过滤（:data:`BOND_FILTERS` 的键）；None 表示不额外过滤
        institution: 只看某机构（期限堆叠场景常用）
    """
    keys = [str(k).strip() for k in (group_by or ["institution"]) if str(k).strip()]
    if not keys:
        keys = ["institution"]
    unknown = [k for k in keys if k not in DIMENSIONS]
    if unknown:
        # 不静默回退：静默回退会让模型拿到错误维度的数据却以为是对的
        raise ValueError(
            f"不支持的 group_by 维度 {unknown}；可选 {', '.join(DIMENSIONS)}"
        )

    date_list = normalize_dates(dates)
    if not date_list:
        raise ValueError("dates 至少需要一个交易日")

    select_parts = [f"{DIMENSIONS[k]} AS {k}" for k in keys]
    group_parts = [DIMENSIONS[k] for k in keys]

    conditions = ["`STATISTIC_REPORT_RANGE` IN (" + ", ".join(["%s"] * len(date_list)) + ")"]
    params: list[Any] = list(date_list)

    if include_total:
        conditions.append("`STATISTIC_SUBDETAIL_NAME` = '合计'")
        conditions.append("`RELATED_STATISTIC_SUBDETAIL_NAME` = '合计'")
    else:
        conditions.append("`STATISTIC_SUBDETAIL_NAME` != '合计'")
        # 券种过滤表达式自带 RELATED 条件时，不再叠加 != '合计'，否则会互相打架
        if not bond_type:
            conditions.append("`RELATED_STATISTIC_SUBDETAIL_NAME` != '合计'")

    if bond_type:
        text = str(bond_type).strip()
        clause = BOND_FILTERS.get(text)
        if clause is None:
            raise ValueError(
                f"不支持的 bond_type: {bond_type!r}；可选 {', '.join(BOND_FILTERS)}"
            )
        conditions.append(clause)

    if institution and str(institution).strip():
        conditions.append("`STATISTIC_DETAIL_NAME` = %s")
        params.append(str(institution).strip())

    sql = (
        f"SELECT {', '.join(select_parts)}, {BUY_YI} AS buy_yi, {SELL_YI} AS sell_yi, "
        f"{NET_YI} AS net_yi FROM {table_ref('daily')} "
        f"WHERE {' AND '.join(conditions)} "
        f"GROUP BY {', '.join(group_parts)} "
        f"ORDER BY {group_parts[0]}, net_yi DESC LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def institution_term_stack_sql(
    dates: Sequence[str],
    institution: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """机构期限堆叠（原 1.14）：期限归到 6 档，明细行（不含合计）。"""
    date_list = normalize_dates(dates)
    if not date_list:
        raise ValueError("dates 至少需要一个交易日")

    conditions = [
        "`STATISTIC_REPORT_RANGE` IN (" + ", ".join(["%s"] * len(date_list)) + ")",
        "`RELATED_STATISTIC_SUBDETAIL_NAME` = '合计'",
        "`STATISTIC_SUBDETAIL_NAME` != '合计'",
    ]
    params: list[Any] = list(date_list)
    if institution and str(institution).strip():
        conditions.append("`STATISTIC_DETAIL_NAME` = %s")
        params.append(str(institution).strip())

    sql = (
        f"SELECT `STATISTIC_REPORT_RANGE` AS date, {TERM_BUCKET_CASE} AS term, "
        f"{NET_YI} AS net_yi FROM {table_ref('daily')} "
        f"WHERE {' AND '.join(conditions)} "
        f"GROUP BY `STATISTIC_REPORT_RANGE`, term "
        f"ORDER BY date, term LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def institution_change_basis_sql(
    dates: Sequence[str],
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """机构合计净买入（原 1.12）：给定日期 → 各机构合计行净买入，用于环比。"""
    return institution_flow_sql(
        dates=dates, group_by=["date", "institution"], include_total=True, limit=limit
    )


# ── 个券机构（原 2.3-2.8） ──────────────────────────────────────────────


def bond_list_sql(
    date: str,
    keyword: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """某交易日的个券列表（代码 + 简称），原 2.3 + 2.4。"""
    conditions = ["`TRANSACT_DATE` = %s"]
    params: list[Any] = [validate_date(date)]
    if keyword and keyword.strip():
        clause, likes = _like_params(keyword.strip())
        conditions.append(clause)
        params.extend(likes)

    sql = (
        "SELECT DISTINCT `STATISTIC_DETAIL_ID` AS bond_id, `STATISTIC_DETAIL_NAME` AS bond_name "
        f"FROM {table_ref('bond')} WHERE {' AND '.join(conditions)} "
        f"ORDER BY bond_id LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def bond_counterparts_sql(
    bond_id: str,
    dates: Sequence[str],
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """某券各机构净买卖 + 成交量占比，原 2.5 + 2.8（同一条 SQL 的两个字段）。"""
    code = str(bond_id or "").strip()
    if not code:
        raise ValueError("bond_id 必填")
    date_list = normalize_dates(dates)
    if not date_list:
        raise ValueError("dates 至少需要一个交易日")

    conditions = [
        "`STATISTIC_DETAIL_ID` = %s",
        "`TRANSACT_DATE` IN (" + ", ".join(["%s"] * len(date_list)) + ")",
        "`RELATED_STATISTIC_SUBDETAIL_NAME` = '全部'",
    ]
    params: list[Any] = [code, *date_list]

    sql = (
        "SELECT `TRANSACT_DATE` AS date, `STATISTIC_SUBDETAIL_NAME` AS institution, "
        "`BID_SIZE` AS net, `TRADE_QTY_RATIO` AS ratio "
        f"FROM {table_ref('bond')} WHERE {' AND '.join(conditions)} "
        f"ORDER BY date, net DESC LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def bond_trend_sql(
    bond_id: str,
    dates: Sequence[str],
    institution: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """某券多日的机构 × 日期趋势，原 2.6。"""
    code = str(bond_id or "").strip()
    if not code:
        raise ValueError("bond_id 必填")
    date_list = normalize_dates(dates)
    if not date_list:
        raise ValueError("dates 至少需要一个交易日")

    conditions = [
        "`STATISTIC_DETAIL_ID` = %s",
        "`TRANSACT_DATE` IN (" + ", ".join(["%s"] * len(date_list)) + ")",
        "`RELATED_STATISTIC_SUBDETAIL_NAME` = '全部'",
    ]
    params: list[Any] = [code, *date_list]
    if institution and str(institution).strip():
        conditions.append("`STATISTIC_SUBDETAIL_NAME` = %s")
        params.append(str(institution).strip())

    sql = (
        "SELECT `TRANSACT_DATE` AS date, `STATISTIC_SUBDETAIL_NAME` AS institution, "
        "`BID_SIZE` AS net "
        f"FROM {table_ref('bond')} WHERE {' AND '.join(conditions)} "
        f"ORDER BY date ASC, net DESC LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def top_active_bonds_sql(date: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """某日全部券 × 机构明细（原 2.7）；活跃度排序在应用层做（见 :func:`rank_active_bonds`）。"""
    conditions = [
        "`TRANSACT_DATE` = %s",
        "`RELATED_STATISTIC_SUBDETAIL_NAME` = '全部'",
    ]
    sql = (
        "SELECT `STATISTIC_DETAIL_ID` AS bond_id, `STATISTIC_DETAIL_NAME` AS bond_name, "
        "`STATISTIC_SUBDETAIL_NAME` AS institution, `BID_SIZE` AS net "
        f"FROM {table_ref('bond')} WHERE {' AND '.join(conditions)} "
        f"LIMIT {row_limit(limit, default=5000)}"
    )
    return sql, (validate_date(date),)


# ── 债券借贷（原 3.4-3.6） ──────────────────────────────────────────────


def lending_snapshot_sql(date: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """某日全部券的借贷余额与集中度快照，原 3.4。

    余额口径固定为 ``STATISTIC_AMT_TYPE='19' AND STATISTIC_AMT_SIDE='1'``，
    参数不可放松——口径错了结论就完全错。
    """
    sql = (
        "SELECT `STATISTIC_DETAIL_ID` AS bond_id, `STATISTIC_DETAIL_NAME` AS bond_name, "
        f"{LENDING_BALANCE} AS balance, {LENDING_CONCENTRATION} AS concentration "
        f"FROM {table_ref('lending')} WHERE `TRANSACT_DATE` = %s "
        f"GROUP BY `STATISTIC_DETAIL_ID`, `STATISTIC_DETAIL_NAME` "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, (validate_date(date),)


def lending_bond_days_sql(limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """每个券出现的交易日数，原 3.5（用于筛"持续更新"的核心跟踪券）。"""
    sql = (
        "SELECT `STATISTIC_DETAIL_ID` AS bond_id, COUNT(DISTINCT `TRANSACT_DATE`) AS days "
        f"FROM {table_ref('lending')} GROUP BY `STATISTIC_DETAIL_ID` "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, ()


def lending_trend_sql(
    dates: Sequence[str],
    codes: Sequence[str] | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """某券（或全部券）的余额与集中度走势，原 3.6。"""
    date_list = normalize_dates(dates)
    if not date_list:
        raise ValueError("dates 至少需要一个交易日")

    conditions = ["`TRANSACT_DATE` IN (" + ", ".join(["%s"] * len(date_list)) + ")"]
    params: list[Any] = list(date_list)

    normalized = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if normalized:
        conditions.append(
            "`STATISTIC_DETAIL_ID` IN (" + ", ".join(["%s"] * len(normalized)) + ")"
        )
        params.extend(normalized)

    sql = (
        "SELECT `TRANSACT_DATE` AS date, `STATISTIC_DETAIL_ID` AS bond_id, "
        f"{LENDING_BALANCE} AS balance, {LENDING_CONCENTRATION} AS concentration "
        f"FROM {table_ref('lending')} WHERE {' AND '.join(conditions)} "
        f"GROUP BY `TRANSACT_DATE`, `STATISTIC_DETAIL_ID` "
        f"ORDER BY date ASC LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


# ── 大模型点评（原第四节） ──────────────────────────────────────────────


def llm_comment_sql(scene: str, date: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """取某场景最新一条成功点评；给定日期则取该日的。"""
    text = str(scene or "").strip()
    if text not in SCENES:
        raise ValueError(f"scene 只支持 {', '.join(SCENES)}（收到 {scene!r}）")

    conditions = ["`SCENE` = %s", "`STATUS` = 'success'"]
    params: list[Any] = [text]
    if date:
        conditions.append("`ANALYSIS_DATE` = %s")
        params.append(validate_date(date))

    sql = (
        "SELECT `SCENE`, `SCENE_NAME`, `ANALYSIS_DATE`, `CONTENT`, `MODEL`, `STATUS` "
        f"FROM {table_ref('llm')} WHERE {' AND '.join(conditions)} "
        "ORDER BY `ANALYSIS_DATE` DESC LIMIT 1"
    )
    return sql, tuple(params)


# ── 结果整形（单位换算与口径归一都在这里，工具层不重复写） ──────────────


def to_yi(value: Any) -> float | None:
    """元 → 亿元（保留 2 位）。None 与非法值按 None 返回。"""
    if value is None:
        return None
    try:
        return round(float(value) / 100000000, 2)
    except (TypeError, ValueError):
        return None


def to_float(value: Any, default: float = 0.0) -> float:
    """安全转 float：``BOND_CONCENTRATION`` 实测有 NULL，直接 float() 会抛异常。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_bond_type(raw: str | None) -> str:
    """券种名称归一化（新债/老债合并），与 SQL 里的 :data:`BOND_TYPE_CASE` 保持一致。"""
    text = str(raw or "").strip()
    if not text:
        return "其他"
    if text == "合计":
        return "合计"
    for names, label in _BOND_TYPE_RULES:
        if text in names:
            return label
    return "其他"


def decorate_flow(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """机构流向结果整形：补齐数值类型（金额已在 SQL 里换算成亿元）。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("buy_yi", "sell_yi", "net_yi"):
            if key in item:
                item[key] = to_float(item[key])
        items.append(item)
    return items


def decorate_bond_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """个券机构结果整形：``net``（元）→ ``net_yi``（亿元），占比转 float。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if "net" in item:
            item["net_yi"] = to_yi(item.pop("net"))
        if "ratio" in item:
            item["ratio"] = to_float(item["ratio"])
        items.append(item)
    return items


def decorate_lending_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """借贷结果整形：余额 → 亿元；集中度 NULL → 0.0。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if "balance" in item:
            item["balance_yi"] = to_yi(item.pop("balance"))
        item["concentration"] = to_float(item.get("concentration"))
        items.append(item)
    return items


def pick_extremes(
    rows: Sequence[dict[str, Any]], key: str = "net_yi"
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """按 ``key`` 取最大 / 最小两行（主力净买入 / 主力净卖出）。"""
    valid = [r for r in rows if r.get(key) is not None]
    if not valid:
        return None, None
    ordered = sorted(valid, key=lambda r: r[key])
    return ordered[-1], ordered[0]


def rank_active_bonds(
    rows: Sequence[dict[str, Any]], top: int = 8
) -> list[dict[str, Any]]:
    """把"某日全部券 × 机构"明细按活跃度（Σ|净买卖|）排序取前 N（原 2.7 应用层逻辑）。"""
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("bond_id") or "")
        if not code:
            continue
        bucket = buckets.setdefault(
            code,
            {
                "bond_id": code,
                "bond_name": row.get("bond_name") or "",
                "activity_yi": 0.0,
                "rows": [],
            },
        )
        yi = to_yi(row.get("net"))
        bucket["rows"].append(
            {"institution": row.get("institution"), "net_yi": yi}
        )
        bucket["activity_yi"] = round(
            bucket["activity_yi"] + abs(yi or 0.0), 2
        )

    ranked = sorted(buckets.values(), key=lambda b: b["activity_yi"], reverse=True)[:top]
    for bucket in ranked:
        buyer, seller = pick_extremes(bucket["rows"])
        bucket["top_buyer"] = buyer
        bucket["top_seller"] = seller
    return ranked


def with_change(
    today: Sequence[dict[str, Any]],
    prev: Sequence[dict[str, Any]],
    key: str = "balance_yi",
    id_key: str = "bond_id",
) -> list[dict[str, Any]]:
    """按 ``id_key`` 对比当日与前一日，补 ``change_yi``；缺失的用 0 参与计算。"""
    prev_map = {str(r.get(id_key)): to_float(r.get(key)) for r in prev}
    result: list[dict[str, Any]] = []
    for row in today:
        item = dict(row)
        before = prev_map.get(str(item.get(id_key)), 0.0)
        after = to_float(item.get(key))
        item["prev_yi"] = before
        item["change_yi"] = round(after - before, 2)
        result.append(item)
    return result


def filter_core_bonds(
    snapshot: Sequence[dict[str, Any]],
    days: Sequence[dict[str, Any]],
    top: int = 7,
) -> list[dict[str, Any]]:
    """核心跟踪券：出现天数 == 最大天数的券，按余额降序取前 ``top`` 只（原 3.5）。"""
    if not days:
        return []
    max_days = max(int(d.get("days") or 0) for d in days)
    tracked = {str(d.get("bond_id")) for d in days if int(d.get("days") or 0) == max_days}
    candidates = [r for r in snapshot if str(r.get("bond_id")) in tracked]
    candidates.sort(key=lambda r: to_float(r.get("balance_yi")), reverse=True)
    return candidates[: max(1, int(top))]


# ── 口径字典（给模型看） ────────────────────────────────────────────────


def schema_help() -> dict[str, Any]:
    """给模型看的口径说明：表、字段、枚举、单位与陷阱。"""
    return {
        "tables": {
            "daily": {
                "table": table_ref("daily"),
                "note": "现券成交分机构统计：机构 × 期限 × 券种 × 买卖方向",
                "date_column": DATE_COLUMNS["daily"],
            },
            "bond": {
                "table": table_ref("bond"),
                "note": "个债交易对手分析：单只债券 × 交易对手机构",
                "date_column": DATE_COLUMNS["bond"],
            },
            "lending": {
                "table": table_ref("lending"),
                "note": "个债债券借贷分析：单只债券的借贷余额与集中度",
                "date_column": DATE_COLUMNS["lending"],
            },
            "llm": {
                "table": table_ref("llm"),
                "note": "大模型点评结果（按场景 + 日期）",
            },
        },
        "units": {
            "金额": "**统一亿元**，字段名带 _yi（如 net_yi / balance_yi）",
            "集中度/占比": "% 数值（24.53 表示 24.53%）",
        },
        "enums": {
            "source": list(SOURCES),
            "group_by": list(DIMENSIONS),
            "bond_type": list(BOND_FILTERS),
            "scene": list(SCENES),
        },
        "date_rule": "所有工具的 date 留空 → 自动取该表最新交易日；格式 YYYYMMDD",
        "visible_tables": list(default_allowed_tables()),
        "pitfalls": [
            "机构当日表同时有明细行与合计行，混用会让金额翻倍——工具已用 include_total 隔离",
            "借贷余额口径固定为 STATISTIC_AMT_TYPE='19' AND STATISTIC_AMT_SIDE='1'，不是成交量",
            "BOND_CONCENTRATION 可能为 NULL，工具层已按 0 兜底",
            "债券借贷单查询不要跨多月（12 个月约 7 秒会超时），按月分片",
        ],
        "typical_questions": {
            "今天各机构买了多少": "get_institution_flow(date='最新')",
            "大型银行的期限分布": "get_institution_flow(institution='大型银行', group_by=['date','term'])",
            "260205 今天谁在买": "get_bond_counterparts(bond_id='260205')",
            "最近哪几只在加杠杆": "get_lending_snapshot(include_change=True)",
        },
    }


def default_allowed_tables() -> tuple[str, ...]:
    """探索白名单缺省值：四张业务表（用 IDATA_ALLOWED_TABLES 可覆盖）。"""
    return env_list("IDATA_ALLOWED_TABLES") or tuple(TABLES.values())
