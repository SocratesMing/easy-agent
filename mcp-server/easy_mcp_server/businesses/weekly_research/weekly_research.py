"""研报观点矩阵与美国宏观资产研判领域层（两张表，JSON 数组展开，只读）。

口径照 ``docs/MCP_docs/研报观点矩阵与宏观_SQL清单_MCP信息.md``：

- 观点矩阵 = ``weekly_data``（一周一行，``week`` 为唯一键）
- 美国宏观资产研判 = ``weekly_macro_data``（一周一行，``week`` 为唯一键）
- 两表**互不关联**，跨表 JOIN 要注意排序规则（可能报 1267）

本模块的三条硬约束：

1. **只用 MySQL 5.7 已有的语法**：不写 ``JSON_TABLE``（8.0 才有，5.7 上直接 1064
   语法报错）、不写递归 CTE、窗口函数、``LATERAL``。数组展开统一用
   **序号表 seq + ``CONCAT`` 拼下标**（见 :data:`SEQ_LIMIT` 与各 ``_*_block`` 常量）。
2. **JSON 里的 ``null`` 取出来是字符串 ``'null'``**（``JSON_UNQUOTE`` 不解成 SQL
   NULL），所以可空字段统一套 ``NULLIF(..., 'null')``。
3. **观点矩阵只含资产类观点**：``category`` 为 ``宏观指标``（历史还有 ``宏观``）的
   一律排除——宏观类观点仍在库里，只是本模块不返回。

派生表上的 ``LIMIT 1``（单周）/ ``DISTINCT``（跨周）是**强制物化**开关：
先把子数组抽进派生表，整份 JSON 才只解析一次，删掉会让跨周查询慢两个数量级。

序号表上限 40 是硬上限：数组比 40 长时**不报错、只静默少行**，数据有新增周后要跑
一次文档 §0.6 的自检。
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier, env_list, max_rows

TABLES: dict[str, str] = {
    "opinions": "weekly_data",
    "macro": "weekly_macro_data",
}

DEFAULT_ROW_LIMIT = 500

#: 序号表上限（0–39）。数组更长时不会报错、只会少行，需同步调大
SEQ_LIMIT = 40

#: 观点矩阵要排除的宏观类 category（历史数据里还有旧的「宏观」）
MACRO_CATEGORIES: tuple[str, ...] = ("宏观指标", "宏观")

_WEEK_RE = re.compile(r"^(\d{8})-(\d{8})$")


def schema_name() -> str:
    load_env()
    return os.environ.get("WEEKLY_RESEARCH_SCHEMA", "").strip()


def table_ref(key: str) -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    table = f"`{assert_identifier(TABLES[key])}`"
    schema = schema_name()
    return f"`{assert_identifier(schema)}`.{table}" if schema else table


def row_limit(limit: int | None = None, default: int = DEFAULT_ROW_LIMIT) -> int:
    return max(1, min(int(limit) if limit else default, max_rows()))


def validate_week(value: str) -> str:
    text = str(value or "").strip()
    match = _WEEK_RE.fullmatch(text)
    if not match:
        raise ValueError(f"week 必须是 YYYYMMDD-YYYYMMDD 格式（收到 {value!r}）")
    try:
        start = date.fromisoformat(
            f"{match.group(1)[:4]}-{match.group(1)[4:6]}-{match.group(1)[6:]}"
        )
        end = date.fromisoformat(
            f"{match.group(2)[:4]}-{match.group(2)[4:6]}-{match.group(2)[6:]}"
        )
    except ValueError:
        raise ValueError("week 包含无效日期（收到 {value!r}）") from None
    if start > end:
        raise ValueError("week 起始日期不能晚于结束日期")
    return text


def _week_param(week: str) -> str:
    return validate_week(week)


# ── 共享块（对应文档 §零）──────────────────────────────────────────────


def _seq() -> str:
    """序号表片段：0..SEQ_LIMIT-1 的下标。派生表片段，用时自带括号。"""
    digits = " UNION ".join(f"SELECT {i} i" for i in range(10))
    return (
        f"(SELECT a.i + b.i*10 AS i FROM ({digits}) a, ({digits}) b "
        f"WHERE a.i + b.i*10 < {SEQ_LIMIT})"
    )


def _views_single() -> str:
    """单周 views 派生表（``LIMIT 1`` 强制物化，``week`` 走 %s）。"""
    return (
        f"(SELECT week, JSON_EXTRACT(data_json, '$.views') AS views_json "
        f"FROM {table_ref('opinions')} WHERE week = %s LIMIT 1)"
    )


def _views_all() -> str:
    """跨周 views 派生表（``DISTINCT`` 强制物化，无 week 参数）。"""
    return (
        f"(SELECT DISTINCT week, JSON_EXTRACT(data_json, '$.views') AS views_json "
        f"FROM {table_ref('opinions')})"
    )


#: {{V}}：一行一个观点，已排除宏观类。参数：week（一个 %s）
VIEWS_EXPAND = (
    "SELECT v.week, v.institution, "
    "JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].category')))  AS category, "
    "JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].asset')))     AS asset, "
    "JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].direction'))) AS direction, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].price'))), 'null')      AS price, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].price_date'))), 'null') AS price_date "
    "FROM ("
    "  SELECT w.week, "
    "         JSON_UNQUOTE(JSON_EXTRACT(w.views_json, CONCAT('$[', sv.i, '].institution'))) AS institution, "
    "         JSON_EXTRACT(w.views_json, CONCAT('$[', sv.i, '].opinions'))                  AS ops_json "
    f"  FROM {_views_single()} w, {_seq()} sv "
    "  WHERE sv.i < JSON_LENGTH(w.views_json) "
    ") v, " + _seq() + " so "
    "WHERE so.i < JSON_LENGTH(v.ops_json) "
    "  AND JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].category'))) "
    "      NOT IN ('宏观指标', '宏观')"
)

#: {{V_ALLWK}}：跨周版观点展开（无 week 参数，不能再按 week 过滤）
VIEWS_EXPAND_ALL_WEEKS = VIEWS_EXPAND.replace(_views_single(), _views_all(), 1)

#: {{M}}：一行一个宏观指标。参数：week（一个 %s）
MACRO_ITEMS_EXPAND = (
    "SELECT m.week, "
    "JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].indicator')))    AS indicator, "
    "JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].category')))     AS category, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].release_date'))), 'null') AS release_date, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].period'))), 'null')       AS period, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].prior'))), 'null')        AS prior, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].consensus'))), 'null')    AS consensus, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].actual'))), 'null')       AS actual, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].unit'))), 'null')         AS unit, "
    "JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].forecasts'))                  AS forecasts "
    f"FROM (SELECT week, JSON_EXTRACT(macro_json, '$.items') AS items_json "
    f"      FROM {table_ref('macro')} WHERE week = %s LIMIT 1) m, {_seq()} si "
    "WHERE si.i < JSON_LENGTH(m.items_json)"
)

#: {{M_ALLWK}}：跨周版宏观指标展开（无 week 参数）
_MACRO_ITEMS_ALL = (
    f"(SELECT DISTINCT week, JSON_EXTRACT(macro_json, '$.items') AS items_json "
    f"FROM {table_ref('macro')})"
)
MACRO_ITEMS_EXPAND_ALL_WEEKS = MACRO_ITEMS_EXPAND.replace(
    f"(SELECT week, JSON_EXTRACT(macro_json, '$.items') AS items_json "
    f"      FROM {table_ref('macro')} WHERE week = %s LIMIT 1)",
    _MACRO_ITEMS_ALL,
    1,
)

#: {{B}}：一行一个机构预测。参数：week（一个 %s）
MACRO_BANK_EXPAND = (
    "SELECT b.week, b.indicator, b.consensus, b.actual, b.inst, "
    "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(b.forecasts, CONCAT('$.\"', b.inst, '\"'))), 'null') AS val, "
    "JSON_TYPE(JSON_EXTRACT(b.forecasts, CONCAT('$.\"', b.inst, '\"')))                    AS jtype "
    "FROM ("
    "  SELECT m.week, "
    "         JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].indicator'))) AS indicator, "
    "         NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].consensus'))), 'null') AS consensus, "
    "         NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].actual'))), 'null')    AS actual, "
    "         JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].forecasts'))               AS forecasts, "
    "         JSON_UNQUOTE(JSON_EXTRACT(JSON_KEYS(JSON_EXTRACT(m.items_json, CONCAT('$[', si.i, '].forecasts'))), "
    "                                   CONCAT('$[', sk.i, ']')))                         AS inst "
    f"  FROM (SELECT week, JSON_EXTRACT(macro_json, '$.items') AS items_json "
    f"        FROM {table_ref('macro')} WHERE week = %s LIMIT 1) m, "
    f"{_seq()} si, {_seq()} sk "
    "  WHERE si.i < JSON_LENGTH(m.items_json) "
    "    AND sk.i < COALESCE(JSON_LENGTH(JSON_EXTRACT(m.items_json, "
    "                           CONCAT('$[', si.i, '].forecasts'))), 0) "
    ") b"
)


# ── 观点矩阵 ────────────────────────────────────────────────────────────


def list_opinion_weeks_sql(limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.1 全部周（倒序）。"""
    return (
        f"SELECT `week` FROM {table_ref('opinions')} WHERE `week` <> 'test' "
        f"ORDER BY `week` DESC LIMIT {row_limit(limit)}",
        (),
    )


def opinion_summary_sql(week: str) -> tuple[str, tuple[Any, ...]]:
    """1.2 某周机构数 / 观点数（⚠️ 不含宏观类）。"""
    return (
        "SELECT x.week, COUNT(DISTINCT x.institution) AS institutions, "
        f"COUNT(*) AS opinions FROM ({VIEWS_EXPAND}) x GROUP BY x.week",
        (_week_param(week),),
    )


def opinion_details_sql(
    week: str,
    institution: str | None = None,
    category: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """1.3 展开成一行一个观点（已排除宏观类）。"""
    conditions: list[str] = []
    params: list[Any] = [_week_param(week)]
    if institution and str(institution).strip():
        conditions.append("x.institution = %s")
        params.append(str(institution).strip())
    if category and str(category).strip():
        conditions.append("x.category = %s")
        params.append(str(category).strip())
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return (
        "SELECT x.week, x.institution, x.category, x.asset, x.direction, "
        f"x.price, x.price_date FROM ({VIEWS_EXPAND}) x {where} "
        f"ORDER BY x.institution, x.asset LIMIT {row_limit(limit)}",
        tuple(params),
    )


def opinion_direction_counts_sql(
    week: str, institution: str | None = None, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.4 某周 机构 × 方向 计数（已排除宏观类）。"""
    conditions: list[str] = []
    params: list[Any] = [_week_param(week)]
    if institution and str(institution).strip():
        conditions.append("x.institution = %s")
        params.append(str(institution).strip())
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return (
        "SELECT x.institution, x.direction, COUNT(*) AS n "
        f"FROM ({VIEWS_EXPAND}) x {where} "
        "GROUP BY x.institution, x.direction "
        f"ORDER BY x.institution, x.direction LIMIT {row_limit(limit)}",
        tuple(params),
    )


def asset_consensus_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.5 某周 资产 × 方向分布（看共识 / 分歧，已排除宏观类）。"""
    return (
        "SELECT x.asset, SUM(x.direction = '看涨') AS bull, "
        "SUM(x.direction = '看跌') AS bear, SUM(x.direction = '中性') AS neutral "
        f"FROM ({VIEWS_EXPAND}) x GROUP BY x.asset "
        f"ORDER BY (bull + bear) DESC, x.asset LIMIT {row_limit(limit)}",
        (_week_param(week),),
    )


def opinion_sources_sql(
    week: str, asset: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.6 观点原文出处（views → opinions → sources 三层展开）。

    ``sources`` 是一对多：一条观点有 N 个出处就展开成 N 行，
    **展开行数 ≠ 观点数**。
    """
    name = str(asset or "").strip()
    if not name:
        raise ValueError("asset 必填")
    return (
        "SELECT y.asset, y.direction, y.report, LEFT(y.passage, 120) AS passage_head "
        "FROM ("
        "  SELECT "
        "  JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].category')))  AS category, "
        "  JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].asset')))     AS asset, "
        "  JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].direction'))) AS direction, "
        "  JSON_UNQUOTE(JSON_EXTRACT(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].sources')), "
        "                             CONCAT('$[', s.i, '].report')))  AS report, "
        "  JSON_UNQUOTE(JSON_EXTRACT(JSON_EXTRACT(v.ops_json, CONCAT('$[', so.i, '].sources')), "
        "                             CONCAT('$[', s.i, '].passage'))) AS passage "
        "  FROM ("
        "    SELECT w.week, JSON_EXTRACT(w.views_json, CONCAT('$[', sv.i, '].opinions')) AS ops_json "
        f"    FROM {_views_single()} w, {_seq()} sv "
        "    WHERE sv.i < JSON_LENGTH(w.views_json) "
        "  ) v, " + _seq() + " so, " + _seq() + " s "
        "  WHERE so.i < JSON_LENGTH(v.ops_json) "
        "    AND s.i < COALESCE(JSON_LENGTH(JSON_EXTRACT(v.ops_json, "
        "                           CONCAT('$[', so.i, '].sources'))), 0) "
        ") y "
        "WHERE y.category NOT IN ('宏观指标', '宏观') AND y.asset = %s "
        "ORDER BY y.report "
        f"LIMIT {row_limit(limit)}",
        (_week_param(week), name),
    )


def asset_opinion_trend_sql(
    asset: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.7 某资产跨周方向 + 价格锚点（跨周版展开）。"""
    name = str(asset or "").strip()
    if not name:
        raise ValueError("asset 必填")
    return (
        "SELECT DISTINCT x.week, x.direction, x.price, x.price_date "
        f"FROM ({VIEWS_EXPAND_ALL_WEEKS}) x "
        "WHERE x.asset = %s ORDER BY x.week DESC "
        f"LIMIT {row_limit(limit)}",
        (name,),
    )


def market_overview_sql(week: str) -> tuple[str, tuple[Any, ...]]:
    """1.8 周度综述正文。"""
    return (
        f"SELECT `week`, `market_overview` FROM {table_ref('opinions')} "
        "WHERE `week` = %s LIMIT 1",
        (_week_param(week),),
    )


# ── 美国宏观资产研判 ────────────────────────────────────────────────────


def list_macro_weeks_sql(limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.1 有宏观数据的周（倒序）。"""
    return (
        f"SELECT `week`, `as_of`, `indicator_count` FROM {table_ref('macro')} "
        f"ORDER BY `week` DESC LIMIT {row_limit(limit)}",
        (),
    )


def macro_indicators_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.2 展开成一行一个指标。"""
    return (
        f"{MACRO_ITEMS_EXPAND} ORDER BY indicator LIMIT {row_limit(limit)}",
        (_week_param(week),),
    )


def macro_forecasts_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.3 展开成一行一个机构预测（机构 × 指标）。"""
    return (
        f"{MACRO_BANK_EXPAND} ORDER BY b.indicator, b.inst LIMIT {row_limit(limit)}",
        (_week_param(week),),
    )


def macro_indicator_trend_sql(
    indicator: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """2.4 某指标跨周走势（跨周版展开）。"""
    name = str(indicator or "").strip()
    if not name:
        raise ValueError("indicator 必填")
    return (
        "SELECT x.week, x.period, x.consensus, x.actual "
        f"FROM ({MACRO_ITEMS_EXPAND_ALL_WEEKS}) x "
        "WHERE x.indicator = %s ORDER BY x.week "
        f"LIMIT {row_limit(limit)}",
        (name,),
    )


def macro_coverage_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.5 某周每个指标的机构覆盖数。"""
    return (
        "SELECT x.indicator, COALESCE(JSON_LENGTH(x.forecasts), 0) AS filled, "
        "x.consensus, x.actual "
        f"FROM ({MACRO_ITEMS_EXPAND}) x "
        f"ORDER BY filled DESC, x.indicator LIMIT {row_limit(limit)}",
        (_week_param(week),),
    )


def macro_numeric_average_sql(
    week: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """2.6 数值行：机构算术平均（⚠️ 只算 JSON INTEGER/DOUBLE）。

    ``jtype IN ('INTEGER','DOUBLE')`` 是必需条件：机构值里混有文字，
    不过滤会把文字当数字算，``CAST`` 后得到 0 或直接报错。
    """
    return (
        "SELECT x.indicator, ROUND(AVG(CAST(x.val AS DECIMAL(14,4))), 2) AS bank_avg_num, "
        "COUNT(*) AS n_num, x.consensus, x.actual "
        f"FROM ({MACRO_BANK_EXPAND}) x "
        "WHERE x.jtype IN ('INTEGER', 'DOUBLE') "
        "GROUP BY x.indicator, x.consensus, x.actual "
        f"ORDER BY n_num DESC, x.indicator LIMIT {row_limit(limit)}",
        (_week_param(week),),
    )


def macro_text_mode_sql(
    week: str, indicator: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """2.7 文字行：机构口径取众数（如「12月」/「10月」/「不动」）。"""
    name = str(indicator or "").strip()
    if not name:
        raise ValueError("indicator 必填")
    return (
        "SELECT x.indicator, x.val, COUNT(*) AS n "
        f"FROM ({MACRO_BANK_EXPAND}) x "
        "WHERE x.indicator = %s AND x.jtype = 'STRING' "
        "GROUP BY x.indicator, x.val ORDER BY n DESC, x.val "
        f"LIMIT {row_limit(limit)}",
        (_week_param(week), name),
    )


def default_allowed_tables() -> tuple[str, ...]:
    return env_list("WEEKLY_RESEARCH_ALLOWED_TABLES") or tuple(TABLES.values())


def schema_help() -> dict[str, Any]:
    return {
        "model_name": "研报观点矩阵与美国宏观资产研判",
        "tables": {key: table_ref(key) for key in TABLES},
        "week_format": "YYYYMMDD-YYYYMMDD",
        "modules": {
            "opinions": "观点矩阵（weekly_data，只含资产类观点）",
            "macro": "美国宏观资产研判（weekly_macro_data）",
        },
        "sql_dialect": (
            "只用 MySQL 5.7 已有语法：JSON_EXTRACT / JSON_UNQUOTE / JSON_KEYS / "
            "JSON_LENGTH / JSON_TYPE / NULLIF / 派生表；"
            "不用 JSON_TABLE、递归 CTE、窗口函数、LATERAL"
        ),
        "opinion_scope": (
            "观点矩阵只含资产类观点：category 为 境内利率/境外利率/外汇/全球股指/大宗；"
            "宏观指标（历史还有 宏观）一律排除——宏观类观点仍在库里，只是本模块不返回"
        ),
        "opinion_directions": ["看涨", "看跌", "中性"],
        "opinion_categories": ["境内利率", "境外利率", "外汇", "全球股指", "大宗"],
        "macro_values": (
            "prior/consensus/actual 与机构预测可能是数值或文字，按字符串保留原始口径；"
            "要比较大小先 CAST(... AS DECIMAL(14,4))"
        ),
        "units": "宏观单位由每条 items[].unit 提供（% / 千人 / 次 / bp / 时点）",
        "json_null": (
            "JSON 里的 null 经 JSON_UNQUOTE 解出来是字符串 'null'（不是 SQL NULL），"
            "可空字段统一套 NULLIF(..., 'null')"
        ),
        "seq_limit": SEQ_LIMIT,
        "not_available": (
            "取宏观一律用 weekly_macro_data；weekly_data.data_json 里的 "
            "macro_forecasts 是历史兼容字段，不读它"
        ),
        "pitfalls": [
            "MySQL 5.7 没有 JSON_TABLE，数组展开用序号表 seq + CONCAT 拼下标",
            "JSON null 解出来是字符串 'null'，可空字段必须 NULLIF(..., 'null')",
            "派生表上的 LIMIT 1 / DISTINCT 是强制物化开关，删掉会让跨周查询慢两个数量级",
            "序号表上限 40 是硬上限，超长数组不报错只少行，需定期跑文档 §0.6 自检",
            "观点矩阵不含宏观类观点（category IN ('宏观指标','宏观') 一律排除）",
            "sources 是一对多，展开行数 ≠ 观点数；返回时只取首条 + 计数",
            "price 只是价格锚点，不可与 direction 推导涨跌幅",
            "两个模块的 category 含义不同：观点里是资产分类，宏观里是指标分类",
            "宏观周数少于观点周数，不要假设每周都有宏观；没有就返回 0 行",
            "机构预测可能含文字值；数值均值只统计 JSON INTEGER/DOUBLE，文字口径单独取众数",
            "最早几周 data_json 顶层是数组（没有 views 键），第一章查询在这些周返回 0 行",
            "跨表 JOIN 可能报 Illegal mix of collations (1267)，需显式 COLLATE",
        ],
        "visible_tables": list(default_allowed_tables()),
    }
