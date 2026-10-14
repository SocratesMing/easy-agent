"""1 年期国债模型领域层（只读一张历史表，结果存在 JSON 字段里）。

表 ``fmqt_treasury_1y_history`` 每个日期一行，**完整结果在 ``result_json`` 一个
JSON 字段内**（20+ 字段），不是平铺列。口径照
``docs/MCP_docs/1年期国债模型_SQL清单_MCP信息.md``：

- ``yield_pct=1.2147`` 表示 1.2147%（**百分数原值，不能再乘 100**）
- ``prob_up_7d`` / ``prob_down_7d`` / ``yield_percentile_756`` / ``trade_threshold`` 是 0—1 小数
- ``yield_change_2d_bp`` 单位是 bp，**可为 null，不能当 0**
- 信号以**收益率方向**解释：``1`` 上行 / ``-1`` 下行 / ``0`` 观察——
  收益率上行对应债券价格偏空，不能反过来说成"债券价格上涨"
- 最新按 ``signal_date DESC`` 取，**不按 ``updated_at``** 判断
- 缺失键或 JSON null 一律保留为 null，不用 ``COALESCE(..., 0)`` 改成零
- 历史用**每条记录自己的**阈值、信号与建议，不使用最新阈值重算

本模块不做训练、不生成预测、不调用大模型，也不提供任何写入 SQL。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier

TABLES: dict[str, str] = {"main": "fmqt_treasury_1y_history"}

MODEL_NAME = "1年期国债模型"

#: 结果字段的单位说明（原值返回，不换算）
UNITS: dict[str, str] = {
    "yield_pct": "percent",
    "yield_percentile_756": "ratio_0_1",
    "prob_up_7d": "ratio_0_1",
    "prob_down_7d": "ratio_0_1",
    "trade_threshold": "ratio_0_1",
    "yield_change_2d_bp": "bp",
}

#: 历史工具的精简投影字段：不含每期长解释、建议正文与来源文本。
SUMMARY_FIELDS: tuple[str, ...] = (
    "signal_date",
    "strategy_id",
    "yield_pct",
    "yield_percentile_756",
    "prob_up_7d",
    "prob_down_7d",
    "trade_threshold",
    "signal_blocked",
    "production_guard",
    "funding_signal",
    "mt_signal",
    "ht_signal",
    "technical_signal",
    "yield_change_2d_bp",
)

#: 完整结果里不带进历史摘要的长文本字段（单日工具仍返回完整对象）
LONG_TEXT_FIELDS: tuple[str, ...] = (
    "funding_advice",
    "technical_advice",
    "funding_explanation",
    "technical_explanation",
    "funding_source",
    "technical_source",
)

DEFAULT_DATES_LIMIT = 20
DEFAULT_HISTORY_LIMIT = 30
MAX_LIMIT = 250

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def schema_name() -> str:
    load_env()
    return os.environ.get("TREASURY_1Y_SCHEMA", "").strip()


def table_ref(key: str = "main") -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    table = f"`{assert_identifier(TABLES[key])}`"
    schema = schema_name()
    return f"`{assert_identifier(schema)}`.{table}" if schema else table


def default_allowed_tables() -> tuple[str, ...]:
    """结构探索白名单缺省值：本场景唯一一张表。"""
    return tuple(TABLES.values())


def validate_date(value: str | None, name: str = "date") -> str | None:
    """校验 ``YYYY-MM-DD``；留空返回 None（表示取最新）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM-DD 格式（收到 {value!r}）")
    return text


def validate_required_date(value: str | None, name: str = "date") -> str:
    """必填日期：留空即报错。"""
    resolved = validate_date(value, name)
    if resolved is None:
        raise ValueError(f"{name} 不能为空")
    return resolved


def bounded_limit(value: int | None, default: int = DEFAULT_HISTORY_LIMIT) -> int:
    """条数：1~250；留空取调用方指定的缺省值。"""
    requested = int(value) if value else default
    return max(1, min(requested, MAX_LIMIT))


def decode_result(value: Any) -> dict[str, Any]:
    """把 ``result_json`` 还原成对象。

    驱动对 JSON 列的返回形式不一致（pymysql 给 JSON 文本，有的驱动直接给对象），
    两种都兼容；解析不出来时返回空对象，由调用方按"无值"处理。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """历史记录的精简投影：保留原值（含 null），只去掉长文本。"""
    return {field: result.get(field) for field in SUMMARY_FIELDS}


def normalize_blocked(value: Any) -> bool | None:
    """``signal_blocked`` 兼容 0/1 与 false/true；无值返回 None。

    不能把字符串 ``"false"`` 直接当数值——那会被判成真。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no", ""):
        return False
    return None


def display_values(result: dict[str, Any]) -> dict[str, Any]:
    """额外的展示字段（百分比形式）；原字段仍按原值返回。"""
    out: dict[str, Any] = {}
    for field in ("prob_up_7d", "prob_down_7d", "yield_percentile_756", "trade_threshold"):
        raw = result.get(field)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            out[f"{field}_pct"] = round(raw * 100, 2)
    return out


# ── SQL 构造 ────────────────────────────────────────────────────────────


def range_sql() -> tuple[str, tuple[Any, ...]]:
    """2.1：结果起止日期与总记录数（空表也返回一行）。"""
    return (
        f"SELECT COUNT(*) AS total_count, MIN(`signal_date`) AS first_date, "
        f"MAX(`signal_date`) AS last_date FROM {table_ref()}",
        (),
    )


def previous_date_sql(date: str) -> tuple[str, tuple[Any, ...]]:
    """2.2：指定日之前最近的已存结果日（不是简单减一天）。"""
    return (
        f"SELECT MAX(`signal_date`) AS previous_signal_date FROM {table_ref()} "
        "WHERE `signal_date` < %s",
        (validate_required_date(date),),
    )


def dates_sql(limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.3 有限返回版：最近 N 个可用结果日期（倒序），只读主键不加载 JSON。"""
    return (
        f"SELECT `signal_date` FROM {table_ref()} "
        f"ORDER BY `signal_date` DESC LIMIT {bounded_limit(limit, DEFAULT_DATES_LIMIT)}",
        (),
    )


def latest_result_sql() -> tuple[str, tuple[Any, ...]]:
    """2.4：最新一期完整结果。"""
    return (
        f"SELECT `signal_date`, `result_json`, `created_at`, `updated_at` "
        f"FROM {table_ref()} ORDER BY `signal_date` DESC LIMIT 1",
        (),
    )


def result_sql(date: str) -> tuple[str, tuple[Any, ...]]:
    """2.5：指定日期完整结果；该日不存在时返回空，不回退。"""
    return (
        f"SELECT `signal_date`, `result_json`, `created_at`, `updated_at` "
        f"FROM {table_ref()} WHERE `signal_date` = %s "
        "ORDER BY `signal_date` DESC LIMIT 1",
        (validate_required_date(date),),
    )


def _history_filters(
    start_date: str | None, end_date: str | None
) -> tuple[str, list[Any]]:
    """日期闭区间的 WHERE 片段（两端都可空）。"""
    conditions: list[str] = []
    params: list[Any] = []
    start = validate_date(start_date, "start_date")
    end = validate_date(end_date, "end_date")
    if start and end and start > end:
        raise ValueError(f"start_date 不能晚于 end_date（{start} > {end}）")
    if start:
        conditions.append("`signal_date` >= %s")
        params.append(start)
    if end:
        conditions.append("`signal_date` <= %s")
        params.append(end)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def history_count_sql(
    start_date: str | None = None, end_date: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """2.6：范围内总数（不受 limit 影响）。"""
    where, params = _history_filters(start_date, end_date)
    return (f"SELECT COUNT(*) AS matched_count FROM {table_ref()}{where}", tuple(params))


def history_sql(
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """2.6：先过滤日期，再取范围内最近 N 条（SQL 倒序，工具层不再反转）。"""
    where, params = _history_filters(start_date, end_date)
    size = bounded_limit(limit, DEFAULT_HISTORY_LIMIT)
    sql = (
        f"SELECT `signal_date`, `result_json`, `created_at`, `updated_at` "
        f"FROM {table_ref()}{where} ORDER BY `signal_date` DESC LIMIT {size}"
    )
    return sql, tuple(params)


def schema_help() -> dict[str, Any]:
    """数据字典：字段、单位、信号方向与陷阱。"""
    return {
        "model_name": MODEL_NAME,
        "table": TABLES["main"],
        "schema": schema_name() or None,
        "storage": (
            "每个日期一行；完整日结果存放在 result_json 一个 JSON 字段内，"
            "不是平铺的数据库列"
        ),
        "units": dict(UNITS),
        "signal_scale": "1 收益率上行 / -1 收益率下行 / 0 观察或方向不明确",
        "date_rule": "signal_date 为 YYYY-MM-DD；缺省表示最新，指定日无数据不回退",
        "latest_rule": "最新按 signal_date DESC 判断，不按 updated_at",
        "summary_fields": list(SUMMARY_FIELDS),
        "long_text_fields": list(LONG_TEXT_FIELDS),
        "not_available": (
            "本表没有真实未来标签、预测对错、回测 AUC/准确率、入模因子清单或训练审计，"
            "不能查询或虚构这些内容"
        ),
        "pitfalls": [
            "yield_pct 是百分数原值（1.2147 即 1.2147%），不能再乘 100；"
            "概率、分位与交易阈值才是 0—1 小数",
            "信号一律以收益率方向解释：上行对应债券价格偏空，不可反过来说成价格上涨",
            "yield_change_2d_bp 允许为 null，不能当成 0，也不等于当天单日变动",
            "历史记录使用其自身的阈值、信号与建议，不用最新阈值重算",
            "prob_up_7d 与 trade_threshold 的比较不构成交易信号，不得据此覆盖已存信号",
        ],
    }
