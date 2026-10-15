"""美国 CPI 预测领域层（四张表，三个预测数组存在 LONGTEXT 列里，只读）。

口径照 ``docs/MCP_docs/美国CPI预测_SQL清单_MCP信息.md``：

- 报告主表 ``fmqt_forecast_us_cpi_prediction_result`` 一行 = 一个「三文件组合版本」，
  ``groups_json`` / ``leaves_json`` / ``daily_json`` 三个列是 **LONGTEXT 文本**，
  工具层解析成数组再筛选排序（不用 ``JSON_TABLE``）
- 除官方历史外，**先定位 ``status='ready'`` 的报告**：给了 ``report_id`` 就只查它，
  否则按 ``generated_at DESC, imported_at DESC, report_id DESC`` 取最新。
  **一次工具调用固定同一个 report_id**，不能在多个子查询之间重新选最新版本
- ``yoy`` / ``nsa_mom`` / ``sa_mom`` / ``weight`` 都是**百分数**（``0.4`` 表示 0.4%），
  不是小数比率；``weight`` 的 100 代表百分之百
- ``gap``（模型减市场）与 ``daily.correction`` 单位是**百分点**
- ``NSA`` 指数保持原基期，不做换算
- 组名/分项名按**已存原文**匹配（例如"核心服务"不能改成别的名字），
  不存在的组名返回空并在 warnings 说明，不改名、不补造
- 数值 ``NULL`` 保留 null，真实 ``0`` 保留 0
- 官方历史**独立于报告**，不接受 ``report_id``

本模块不做训练、不生成预测、不重算中位数、不生成新策略，也不提供任何写入 SQL。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier

TABLES: dict[str, str] = {
    "prediction": "fmqt_forecast_us_cpi_prediction_result",
    "market": "fmqt_forecast_us_cpi_market_expectation",
    "strategy": "fmqt_forecast_us_cpi_strategy_advice",
    "official": "fmqt_forecast_us_cpi_official_history",
}

MODEL_NAME = "美国CPI预测"

#: 报告状态：只有 ready 才对外可读
READY_STATUS = "ready"

#: 组级预测 record_type
RECORD_MAIN = "main"
RECORD_FAR = "far"
RECORD_TYPES: tuple[str, ...] = (RECORD_MAIN, RECORD_FAR)

#: 逐日预测里常见的组别（仅作说明，实际按已存原文匹配）
DAILY_GROUPS: tuple[str, ...] = ("总体", "核心", "食品", "能源")

#: 场景 5 里模型侧要取的两个组名
MODEL_COMPARE_GROUPS: tuple[str, ...] = ("总体", "核心")

#: 百分数口径的字段（0.4 表示 0.4%）
PERCENT_FIELDS: tuple[str, ...] = ("yoy", "nsa_mom", "sa_mom", "weight")

#: groups_json 的元素字段
GROUP_FIELDS: tuple[str, ...] = (
    "record_type",
    "forecast_month",
    "group_name",
    "weight",
    "yoy",
    "nsa_mom",
    "sa_mom",
)

#: leaves_json 的元素字段
LEAF_FIELDS: tuple[str, ...] = (
    "forecast_month",
    "item_name",
    "group_name",
    "weight",
    "method",
    "yoy",
    "nsa_mom",
    "sa_mom",
)

#: daily_json 的元素字段
DAILY_FIELDS: tuple[str, ...] = (
    "target_month",
    "observation_date",
    "group_name",
    "yoy",
    "baseline",
    "correction",
    "event_sources",
    "is_future_display",
)

DEFAULT_REPORT_LIMIT = 1
MAX_REPORT_LIMIT = 50
DEFAULT_OFFICIAL_LIMIT = 1000
MAX_OFFICIAL_LIMIT = 5000
#: 开始月份未给时，从结束月向前回溯的自然月数
OFFICIAL_DEFAULT_MONTHS = 12

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def schema_name() -> str:
    load_env()
    return os.environ.get("US_CPI_SCHEMA", "").strip()


def table_ref(key: str) -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    if schema_name():
        return f"`{assert_identifier(schema_name())}`.`{assert_identifier(TABLES[key])}`"
    return f"`{assert_identifier(TABLES[key])}`"


def prediction_ref() -> str:
    return table_ref("prediction")


def market_ref() -> str:
    return table_ref("market")


def strategy_ref() -> str:
    return table_ref("strategy")


def official_ref() -> str:
    return table_ref("official")


def default_allowed_tables() -> tuple[str, ...]:
    """结构探索白名单缺省值：本场景四张表。"""
    return tuple(TABLES.values())


# ── 参数校验 ────────────────────────────────────────────────────────────


def validate_month(value: str | None, name: str = "month") -> str | None:
    """校验 ``YYYY-MM``（数组内的月份是 ``YYYY-MM-DD``，由调用方自行区分）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if not _MONTH_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM 格式且月份在 01~12（收到 {value!r}）")
    return text


def validate_date(value: str | None, name: str = "date") -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM-DD 格式（收到 {value!r}）")
    return text


def validate_report_id(value: Any) -> int | None:
    """报告 ID：留空返回 None（表示取最新 ready 报告）。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"report_id 必须是正整数（收到 {value!r}）") from None
    if resolved <= 0:
        raise ValueError(f"report_id 必须是正整数（收到 {value!r}）")
    return resolved


def validate_scope(value: str | None) -> str:
    """组级预测范围：main / far / all。"""
    text = str(value or "").strip().lower()
    if not text:
        return RECORD_MAIN
    if text not in (*RECORD_TYPES, "all"):
        raise ValueError(f"scope 只支持 main / far / all（收到 {value!r}）")
    return text


def bounded_limit(
    value: int | None, default: int, maximum: int
) -> int:
    requested = int(value) if value is not None else default
    return max(1, min(requested, maximum))


def month_start(month: str) -> str:
    """``YYYY-MM`` → 该月首日 ``YYYY-MM-01``。"""
    resolved = validate_month(month, "month")
    if not resolved:
        raise ValueError("month 不能为空")
    return f"{resolved}-01"


def month_end(month: str) -> str:
    """``YYYY-MM`` → 该月末日。"""
    resolved = validate_month(month, "month")
    if not resolved:
        raise ValueError("month 不能为空")
    year, mon = (int(part) for part in resolved.split("-"))
    if mon == 12:
        last_year, last_mon = year + 1, 1
    else:
        last_year, last_mon = year, mon + 1
    # 下月首日减一天
    import datetime as _dt

    last_day = (_dt.date(last_year, last_mon, 1) - _dt.timedelta(days=1)).day
    return f"{resolved}-{last_day:02d}"


def shift_months(month: str, delta: int) -> str:
    """按自然月平移（用于开始月份未给时向前回溯）。"""
    resolved = validate_month(month, "month")
    if not resolved:
        raise ValueError("month 不能为空")
    year, mon = (int(part) for part in resolved.split("-"))
    total = year * 12 + (mon - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


# ── 文本列解析 ──────────────────────────────────────────────────────────


def parse_json_text(value: Any) -> list[dict[str, Any]]:
    """把 LONGTEXT 列解析成数组；解不出来或非数组返回空列表。

    驱动返回值可能是 str / bytes / 已解析对象，三种都兼容。
    """
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    return []


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """按字段表投影，数值列转 float（null 保持 null）。"""
    out: dict[str, Any] = {}
    for field in fields:
        value = item.get(field)
        if field in PERCENT_FIELDS:
            out[field] = _as_float(value)
        elif field == "is_future_display":
            out[field] = _as_int(value)
        elif field in ("forecast_month", "target_month", "observation_date"):
            out[field] = str(value) if value is not None else None
        else:
            out[field] = value
    return out


def group_rows(item: dict[str, Any]) -> dict[str, Any]:
    return _normalize(item, GROUP_FIELDS)


def leaf_rows(item: dict[str, Any]) -> dict[str, Any]:
    return _normalize(item, LEAF_FIELDS)


def daily_rows(item: dict[str, Any]) -> dict[str, Any]:
    return _normalize(item, DAILY_FIELDS)


def official_row(row: dict[str, Any]) -> dict[str, Any]:
    """官方历史行：指数与环比保持原值，同比是 SQL 算出来的。"""
    return {
        "observation_month": (
            str(row["observation_month"]) if row.get("observation_month") else None
        ),
        "indicator_name": row.get("indicator_name"),
        "nsa_series_id": row.get("nsa_series_id"),
        "sa_series_id": row.get("sa_series_id"),
        "nsa_index": _as_float(row.get("nsa_index")),
        "sa_mom": _as_float(row.get("sa_mom")),
        "nsa_yoy": _as_float(row.get("nsa_yoy")),
        "nsa_source_file": row.get("nsa_source_file"),
        "sa_source_file": row.get("sa_source_file"),
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
    }


def schema_help() -> dict[str, Any]:
    """数据字典：表关系、单位口径、陷阱。"""
    return {
        "model_name": MODEL_NAME,
        "tables": dict(TABLES),
        "schema": schema_name() or None,
        "structure": (
            "报告主表（三个 LONGTEXT 预测数组）+ 机构预测表 + 策略建议表 + "
            "官方历史表"
        ),
        "report_rule": (
            "除官方历史外先定位 status='ready' 的报告：给了 report_id 只查它，"
            "否则按 generated_at DESC, imported_at DESC, report_id DESC 取最新；"
            "一次工具调用固定同一个 report_id"
        ),
        "units": {
            "yoy / nsa_mom / sa_mom": "百分数（0.4 表示 0.4%），不是小数比率",
            "weight": "百分数（100 代表百分之百）",
            "gap（模型减市场）": "百分点",
            "correction（逐日）": "百分点（相对月初基线的累计修正）",
            "nsa_index": "指数，保持原基期，不换算",
            "baseline（逐日）": "仅用于逐日预测的月初同比惯性基线，"
            "不是顶部的 SA 环比基线",
        },
        "record_types": {
            RECORD_MAIN: "main 组级预测",
            RECORD_FAR: "far 远期预测",
        },
        "not_available": (
            "不重算市场中位数、不生成新策略、不把历史评价文字提取成模型可信度；"
            "官方历史不接受 report_id"
        ),
        "pitfalls": [
            "yoy/nsa_mom/sa_mom/weight 是百分数，不是 0—1 小数，不要乘 100",
            "组名与分项名按已存原文精确匹配，不存在的返回空，不改名（如「核心服务」）",
            "三个 JSON 列是 LONGTEXT，必须先解析再筛选，不能用 JSON_TABLE",
            "同一份报告内 main 与 far 是不同记录，不能混成一条",
            "daily.baseline 只属于逐日场景，不是顶部 SA 环比基线",
            "event_sources 同一天在不同组重复保存，不能按组累计事件次数",
            "model_generated_at 是模型生成时间，不是策略撰写时间",
        ],
    }


# ── SQL 构造 ────────────────────────────────────────────────────────────


def resolve_report_sql(report_id: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """定位报告：给了 id 只查它，否则取最新 ready 报告。"""
    if report_id is not None:
        return (
            f"SELECT report_id, forecast_month, official_latest_month, market_month, "
            f"strategy_month, generated_at, imported_at, model_sha256, market_sha256, "
            f"strategy_sha256 FROM {prediction_ref()} "
            f"WHERE status = %s AND report_id = %s",
            (READY_STATUS, report_id),
        )
    return (
        f"SELECT report_id, forecast_month, official_latest_month, market_month, "
        f"strategy_month, generated_at, imported_at, model_sha256, market_sha256, "
        f"strategy_sha256 FROM {prediction_ref()} WHERE status = %s "
        f"ORDER BY generated_at DESC, imported_at DESC, report_id DESC LIMIT 1",
        (READY_STATUS,),
    )


def reports_sql(
    report_id: int | None = None, forecast_month: str | None = None, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """场景 1：报告版本与各来源月份。"""
    size = bounded_limit(limit, DEFAULT_REPORT_LIMIT, MAX_REPORT_LIMIT)
    conditions = ["status = %s"]
    params: list[Any] = [READY_STATUS]
    if report_id is not None:
        conditions.append("report_id = %s")
        params.append(report_id)
    month = validate_month(forecast_month, "forecast_month")
    if month:
        conditions.append("forecast_month = %s")
        params.append(month)
    return (
        f"SELECT report_id, forecast_month, official_latest_month, market_month, "
        f"strategy_month, generated_at, imported_at, model_sha256, market_sha256, "
        f"strategy_sha256 FROM {prediction_ref()} "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY generated_at DESC, imported_at DESC, report_id DESC LIMIT {size}",
        tuple(params),
    )


def prediction_arrays_sql(report_id: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """场景 2/3/4 共用：取三个 LONGTEXT 数组（按已解析的报告 id）。"""
    if report_id is None:
        # 未给 id 时直接取最新 ready 报告，避免在子查询里二次选版本
        return (
            f"SELECT p.report_id, p.forecast_month, p.groups_json, p.leaves_json, "
            f"p.daily_json FROM {prediction_ref()} AS p "
            f"WHERE p.status = %s "
            f"ORDER BY p.generated_at DESC, p.imported_at DESC, p.report_id DESC LIMIT 1",
            (READY_STATUS,),
        )
    return (
        f"SELECT report_id, forecast_month, groups_json, leaves_json, daily_json "
        f"FROM {prediction_ref()} WHERE report_id = %s AND status = %s",
        (report_id, READY_STATUS),
    )


def market_sql(
    report_id: int | None,
    effective_month: str | None = None,
    median_only: bool = False,
    institution: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """场景 5：机构预测（report_id 为 None 时取最新 ready 报告）。"""
    conditions: list[str] = []
    params: list[Any] = []
    if report_id is None:
        conditions.append(
            f"report_id = (SELECT report_id FROM {prediction_ref()} "
            f"WHERE status = %s ORDER BY generated_at DESC, imported_at DESC, "
            f"report_id DESC LIMIT 1)"
        )
        params.append(READY_STATUS)
    else:
        conditions.append("report_id = %s")
        params.append(report_id)

    month = validate_month(effective_month, "forecast_month")
    if month:
        conditions.append("forecast_month = %s")
        params.append(month)
    if median_only:
        conditions.append("is_median = 1")
    text = str(institution or "").strip()
    if text:
        conditions.append("institution_name = %s")
        params.append(text)

    return (
        f"SELECT report_id, forecast_month, institution_name, is_median, "
        f"headline_yoy, headline_mom, core_yoy, core_mom FROM {market_ref()} "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY is_median DESC, institution_name",
        tuple(params),
    )


def strategy_sql(
    report_id: int | None = None, effective_month: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """场景 6：策略建议原文（JOIN 报告表校验 ready）。"""
    conditions = ["r.status = %s"]
    params: list[Any] = [READY_STATUS]
    if report_id is None:
        conditions.append(
            f"a.report_id = (SELECT report_id FROM {prediction_ref()} "
            f"WHERE status = %s ORDER BY generated_at DESC, imported_at DESC, "
            f"report_id DESC LIMIT 1)"
        )
        params.append(READY_STATUS)
    else:
        conditions.append("a.report_id = %s")
        params.append(report_id)
    month = validate_month(effective_month, "forecast_month")
    if month:
        conditions.append("a.forecast_month = %s")
        params.append(month)
    return (
        f"SELECT a.report_id, a.forecast_month, a.content, r.strategy_sha256, "
        f"r.generated_at AS model_generated_at, r.imported_at "
        f"FROM {strategy_ref()} AS a "
        f"JOIN {prediction_ref()} AS r ON r.report_id = a.report_id "
        f"WHERE {' AND '.join(conditions)} ORDER BY a.forecast_month DESC",
        tuple(params),
    )


def official_latest_month_sql(indicator: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """场景 7 辅助：所选指标最新有值月份（指标留空时取官方表最新月）。"""
    text = str(indicator or "").strip()
    if text:
        return (
            f"SELECT MAX(observation_month) AS latest_month FROM {official_ref()} "
            f"WHERE indicator_name = %s",
            (text,)
        )
    return (
        f"SELECT MAX(observation_month) AS latest_month FROM {official_ref()}",
        (),
    )


def official_history_sql(
    indicator: str | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """场景 7：官方历史 SA 环比与 NSA 指数（同比用同表去年同期算）。

    ``limit`` 之外多取一行用于判断截断。
    """
    size = bounded_limit(limit, DEFAULT_OFFICIAL_LIMIT, MAX_OFFICIAL_LIMIT)
    conditions: list[str] = []
    params: list[Any] = []
    text = str(indicator or "").strip()
    if text:
        conditions.append("h.indicator_name = %s")
        params.append(text)
    start = validate_month(start_month, "start_month")
    end = validate_month(end_month, "end_month")
    if start:
        conditions.append("h.observation_month >= %s")
        params.append(start)
    if end:
        conditions.append("h.observation_month <= %s")
        params.append(end)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return (
        f"SELECT h.observation_month, h.indicator_name, h.nsa_series_id, "
        f"h.sa_series_id, h.nsa_index, h.sa_mom, "
        f"100 * (h.nsa_index / NULLIF(y.nsa_index, 0) - 1) AS nsa_yoy, "
        f"h.nsa_source_file, h.sa_source_file, h.updated_at "
        f"FROM {official_ref()} AS h "
        f"LEFT JOIN {official_ref()} AS y ON y.indicator_name = h.indicator_name "
        f"AND y.observation_month = DATE_FORMAT("
        f"DATE_SUB(STR_TO_DATE(CONCAT(h.observation_month, '-01'), '%Y-%m-%d'), "
        f"INTERVAL 1 YEAR), '%Y-%m') {where} "
        f"ORDER BY h.observation_month DESC LIMIT {size + 1}",
        tuple(params),
    )
