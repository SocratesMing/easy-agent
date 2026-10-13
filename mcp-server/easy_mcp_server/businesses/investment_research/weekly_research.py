"""研报观点矩阵与周度宏观预测领域层。"""

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
        raise ValueError(f"week 包含无效日期（收到 {value!r}）") from None
    if start > end:
        raise ValueError("week 起始日期不能晚于结束日期")
    return text


def _week_param(week: str) -> str:
    return validate_week(week)


def list_opinion_weeks_sql(limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT `week` FROM {table_ref('opinions')} WHERE `week` <> 'test' "
        f"ORDER BY `week` DESC LIMIT {row_limit(limit)}"
    )
    return sql, ()


def opinion_summary_sql(week: str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT w.`week`, JSON_LENGTH(JSON_EXTRACT(w.`data_json`, '$.views')) AS institutions, "
        "(SELECT SUM(JSON_LENGTH(JSON_EXTRACT(o.opinions, '$'))) "
        " FROM JSON_TABLE(JSON_EXTRACT(w.`data_json`, '$.views'), '$[*]' "
        "      COLUMNS(opinions JSON PATH '$.opinions')) o) AS opinions "
        f"FROM {table_ref('opinions')} w WHERE w.`week` = %s LIMIT 1"
    )
    return sql, (_week_param(week),)


def _opinion_detail_query(
    select: str,
    week: str,
    institution: str | None = None,
    category: str | None = None,
    macro_only: bool = False,
    order_by: str = "jt.institution, op.asset",
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    conditions = ["w.`week` = %s"]
    params: list[Any] = [_week_param(week)]
    if institution and str(institution).strip():
        conditions.append("jt.institution = %s")
        params.append(str(institution).strip())
    if category and str(category).strip():
        conditions.append("op.category = %s")
        params.append(str(category).strip())
    if macro_only:
        conditions.append("op.category IN ('宏观指标', '宏观')")
    sql = (
        f"SELECT {select} FROM {table_ref('opinions')} w, "
        "JSON_TABLE(JSON_EXTRACT(w.`data_json`, '$.views'), '$[*]' COLUMNS("
        "institution VARCHAR(64) PATH '$.institution', "
        "opinions JSON PATH '$.opinions')) jt, "
        "JSON_TABLE(jt.opinions, '$[*]' COLUMNS("
        "category VARCHAR(32) PATH '$.category', "
        "asset VARCHAR(128) PATH '$.asset', "
        "direction VARCHAR(16) PATH '$.direction')) op "
        f"WHERE {' AND '.join(conditions)} ORDER BY {order_by} "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def opinion_details_sql(
    week: str,
    institution: str | None = None,
    category: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    return _opinion_detail_query(
        "w.`week`, jt.institution, op.category, op.asset, op.direction",
        week,
        institution=institution,
        category=category,
        limit=limit,
    )


def macro_opinions_sql(
    week: str, institution: str | None = None, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    return _opinion_detail_query(
        "w.`week`, jt.institution, op.asset, op.direction",
        week,
        institution=institution,
        macro_only=True,
        limit=limit,
    )


def opinion_direction_counts_sql(
    week: str, institution: str | None = None, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    conditions = ["w.`week` = %s"]
    params: list[Any] = [_week_param(week)]
    if institution and str(institution).strip():
        conditions.append("jt.institution = %s")
        params.append(str(institution).strip())
    sql = (
        "SELECT jt.institution, op.direction, COUNT(*) AS n "
        f"FROM {table_ref('opinions')} w, "
        "JSON_TABLE(JSON_EXTRACT(w.`data_json`, '$.views'), '$[*]' COLUMNS("
        "institution VARCHAR(64) PATH '$.institution', "
        "opinions JSON PATH '$.opinions')) jt, "
        "JSON_TABLE(jt.opinions, '$[*]' COLUMNS(direction VARCHAR(16) PATH '$.direction')) op "
        f"WHERE {' AND '.join(conditions)} "
        f"GROUP BY jt.institution, op.direction ORDER BY jt.institution, op.direction "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def asset_consensus_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT op.asset, SUM(op.direction='看涨') AS bull, "
        "SUM(op.direction='看跌') AS bear, SUM(op.direction='中性') AS neutral "
        f"FROM {table_ref('opinions')} w, "
        "JSON_TABLE(JSON_EXTRACT(w.`data_json`, '$.views'), '$[*]' "
        "COLUMNS(opinions JSON PATH '$.opinions')) jt, "
        "JSON_TABLE(jt.opinions, '$[*]' COLUMNS("
        "asset VARCHAR(128) PATH '$.asset', direction VARCHAR(16) PATH '$.direction')) op "
        "WHERE w.`week` = %s GROUP BY op.asset ORDER BY (bull + bear) DESC "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, (_week_param(week),)


def opinion_sources_sql(
    week: str, asset: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    name = str(asset or "").strip()
    if not name:
        raise ValueError("asset 必填")
    sql = (
        "SELECT jt.institution, op.asset, op.direction, s.report, s.source_date, "
        "s.passage, s.rationale "
        f"FROM {table_ref('opinions')} w, "
        "JSON_TABLE(JSON_EXTRACT(w.`data_json`, '$.views'), '$[*]' COLUMNS("
        "institution VARCHAR(64) PATH '$.institution', opinions JSON PATH '$.opinions')) jt, "
        "JSON_TABLE(jt.opinions, '$[*]' COLUMNS("
        "asset VARCHAR(128) PATH '$.asset', direction VARCHAR(16) PATH '$.direction', "
        "sources JSON PATH '$.sources')) op, "
        "JSON_TABLE(op.sources, '$[*]' COLUMNS("
        "report VARCHAR(256) PATH '$.report', source_date VARCHAR(24) PATH '$.date', "
        "passage VARCHAR(2048) PATH '$.passage', rationale VARCHAR(1024) PATH '$.rationale')) s "
        "WHERE w.`week` = %s AND op.asset = %s "
        "ORDER BY jt.institution, s.source_date DESC "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, (_week_param(week), name)


def asset_opinion_trend_sql(
    asset: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    name = str(asset or "").strip()
    if not name:
        raise ValueError("asset 必填")
    sql = (
        "SELECT DISTINCT w.`week`, op.direction, op.price, op.price_date "
        f"FROM {table_ref('opinions')} w, "
        "JSON_TABLE(JSON_EXTRACT(w.`data_json`, '$.views'), '$[*]' "
        "COLUMNS(opinions JSON PATH '$.opinions')) jt, "
        "JSON_TABLE(jt.opinions, '$[*]' COLUMNS("
        "asset VARCHAR(128) PATH '$.asset', direction VARCHAR(16) PATH '$.direction', "
        "price VARCHAR(32) PATH '$.price', price_date VARCHAR(24) PATH '$.price_date')) op "
        "WHERE op.asset = %s AND w.`week` <> 'test' "
        f"ORDER BY w.`week` DESC LIMIT {row_limit(limit)}"
    )
    return sql, (name,)


def market_overview_sql(week: str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT `week`, `market_overview` FROM {table_ref('opinions')} "
        "WHERE `week` = %s LIMIT 1"
    )
    return sql, (_week_param(week),)


def list_macro_weeks_sql(limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT `week`, `as_of`, `indicator_count` FROM {table_ref('macro')} "
        f"ORDER BY `week` DESC LIMIT {row_limit(limit)}"
    )
    return sql, ()


def macro_indicators_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT m.`week`, jt.indicator, jt.category, jt.release_date, jt.period, "
        "jt.prior, jt.consensus, jt.actual, jt.unit "
        f"FROM {table_ref('macro')} m, "
        "JSON_TABLE(m.`macro_json`, '$.items[*]' COLUMNS("
        "indicator VARCHAR(128) PATH '$.indicator', category VARCHAR(32) PATH '$.category', "
        "release_date VARCHAR(32) PATH '$.release_date', period VARCHAR(16) PATH '$.period', "
        "prior VARCHAR(32) PATH '$.prior', consensus VARCHAR(32) PATH '$.consensus', "
        "actual VARCHAR(32) PATH '$.actual', unit VARCHAR(16) PATH '$.unit')) jt "
        f"WHERE m.`week` = %s ORDER BY jt.indicator LIMIT {row_limit(limit)}"
    )
    return sql, (_week_param(week),)


def macro_forecasts_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT m.`week`, jt.indicator, kj.inst AS institution, "
        "JSON_UNQUOTE(JSON_EXTRACT(jt.forecasts_json, CONCAT('$.\"', kj.inst, '\"'))) AS val "
        f"FROM {table_ref('macro')} m, "
        "JSON_TABLE(m.`macro_json`, '$.items[*]' COLUMNS("
        "indicator VARCHAR(128) PATH '$.indicator', forecasts_json JSON PATH '$.forecasts')) jt, "
        "JSON_TABLE(JSON_KEYS(jt.forecasts_json), '$[*]' COLUMNS(inst VARCHAR(128) PATH '$')) kj "
        f"WHERE m.`week` = %s ORDER BY jt.indicator, kj.inst LIMIT {row_limit(limit)}"
    )
    return sql, (_week_param(week),)


def macro_indicator_trend_sql(
    indicator: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    name = str(indicator or "").strip()
    if not name:
        raise ValueError("indicator 必填")
    sql = (
        "SELECT m.`week`, jt.period, jt.consensus, jt.actual "
        f"FROM {table_ref('macro')} m, "
        "JSON_TABLE(m.`macro_json`, '$.items[*]' COLUMNS("
        "indicator VARCHAR(128) PATH '$.indicator', period VARCHAR(16) PATH '$.period', "
        "consensus VARCHAR(32) PATH '$.consensus', actual VARCHAR(32) PATH '$.actual')) jt "
        "WHERE jt.indicator = %s "
        f"ORDER BY m.`week` ASC LIMIT {row_limit(limit)}"
    )
    return sql, (name,)


def macro_coverage_sql(week: str, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT jt.indicator, COALESCE(JSON_LENGTH(jt.forecasts_json), 0) AS filled, "
        "jt.consensus, jt.actual "
        f"FROM {table_ref('macro')} m, "
        "JSON_TABLE(m.`macro_json`, '$.items[*]' COLUMNS("
        "indicator VARCHAR(128) PATH '$.indicator', consensus VARCHAR(32) PATH '$.consensus', "
        "actual VARCHAR(32) PATH '$.actual', forecasts_json JSON PATH '$.forecasts')) jt "
        f"WHERE m.`week` = %s ORDER BY filled DESC, jt.indicator LIMIT {row_limit(limit)}"
    )
    return sql, (_week_param(week),)


def macro_numeric_average_sql(
    week: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT jt.indicator, ROUND(AVG(CAST(JSON_UNQUOTE(JSON_EXTRACT("
        "jt.forecasts_json, CONCAT('$.\"', kj.inst, '\"'))) AS DECIMAL(14,4))), 2) AS bank_avg_num, "
        "COUNT(*) AS n_num, jt.consensus, jt.actual "
        f"FROM {table_ref('macro')} m, "
        "JSON_TABLE(m.`macro_json`, '$.items[*]' COLUMNS("
        "indicator VARCHAR(128) PATH '$.indicator', consensus VARCHAR(32) PATH '$.consensus', "
        "actual VARCHAR(32) PATH '$.actual', forecasts_json JSON PATH '$.forecasts')) jt, "
        "JSON_TABLE(JSON_KEYS(jt.forecasts_json), '$[*]' COLUMNS(inst VARCHAR(128) PATH '$')) kj "
        "WHERE m.`week` = %s AND JSON_TYPE(JSON_EXTRACT(jt.forecasts_json, "
        "CONCAT('$.\"', kj.inst, '\"'))) IN ('INTEGER', 'DOUBLE') "
        "GROUP BY jt.indicator, jt.consensus, jt.actual ORDER BY n_num DESC, jt.indicator "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, (_week_param(week),)


def macro_text_mode_sql(
    week: str, indicator: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    name = str(indicator or "").strip()
    if not name:
        raise ValueError("indicator 必填")
    sql = (
        "SELECT jt.indicator, JSON_UNQUOTE(JSON_EXTRACT(jt.forecasts_json, "
        "CONCAT('$.\"', kj.inst, '\"'))) AS val, COUNT(*) AS n "
        f"FROM {table_ref('macro')} m, "
        "JSON_TABLE(m.`macro_json`, '$.items[*]' COLUMNS("
        "indicator VARCHAR(128) PATH '$.indicator', forecasts_json JSON PATH '$.forecasts')) jt, "
        "JSON_TABLE(JSON_KEYS(jt.forecasts_json), '$[*]' COLUMNS(inst VARCHAR(128) PATH '$')) kj "
        "WHERE m.`week` = %s AND jt.indicator = %s AND JSON_TYPE(JSON_EXTRACT("
        "jt.forecasts_json, CONCAT('$.\"', kj.inst, '\"'))) = 'STRING' "
        "GROUP BY jt.indicator, val ORDER BY n DESC, val "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, (_week_param(week), name)


def default_allowed_tables() -> tuple[str, ...]:
    return env_list("WEEKLY_RESEARCH_ALLOWED_TABLES") or tuple(TABLES.values())


def schema_help() -> dict[str, Any]:
    return {
        "tables": {key: table_ref(key) for key in TABLES},
        "week_format": "YYYYMMDD-YYYYMMDD",
        "opinion_directions": ["看涨", "看跌", "中性"],
        "opinion_categories": ["境内利率", "境外利率", "外汇", "全球股指", "大宗", "宏观指标"],
        "macro_values": "prior/consensus/actual 与机构预测可能是数值或文字，按字符串保留原始显示口径",
        "units": "宏观单位由每条 macro_json.items[].unit 提供",
        "pitfalls": [
            "观点 JSON 中的宏观指标观点与 weekly_macro_data 的数值预测是两个独立场景",
            "同一观点可以有多个 sources，出处查询会展开为多行",
            "资产 price 是观点生成时的价格锚点，不能据此计算期间涨跌幅",
            "机构预测可能包含文字值；数值均值只统计 JSON INTEGER/DOUBLE，文字口径单独取众数",
        ],
        "visible_tables": list(default_allowed_tables()),
    }
