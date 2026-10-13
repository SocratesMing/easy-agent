"""国内宏观经济指标预测领域层：报告、指标、模型预测与归因查询。"""

from __future__ import annotations

import os
import re
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier, env_list, max_rows

TABLES: dict[str, str] = {
    "report": "fmqt_forecast_report_meta",
    "indicator": "fmqt_forecast_indicator",
    "prediction": "fmqt_forecast_prediction",
    "contribution": "fmqt_forecast_feature_contribution",
    "selected_feature": "fmqt_forecast_selected_feature",
    "analysis": "fmqt_forecast_analysis",
}
GROUPS: tuple[str, ...] = ("增长", "通胀", "信用", "外贸", "其他")
SCENARIOS: tuple[str, ...] = ("SC_GLOBAL", "SC_FEATURE", "SC_INDICATOR")
DEFAULT_ROW_LIMIT = 500
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def schema_name() -> str:
    load_env()
    return os.environ.get("FORECAST_SCHEMA", "").strip()


def table_ref(key: str) -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    table = f"`{assert_identifier(TABLES[key])}`"
    schema = schema_name()
    return f"`{assert_identifier(schema)}`.{table}" if schema else table


def row_limit(limit: int | None = None, default: int = DEFAULT_ROW_LIMIT) -> int:
    return max(1, min(int(limit) if limit else default, max_rows()))


def validate_month(value: str) -> str:
    text = str(value or "").strip()
    if not _MONTH_RE.fullmatch(text):
        raise ValueError(f"forecast_month 必须是 YYYY-MM 格式（收到 {value!r}）")
    return text


def validate_id(value: int | str, name: str = "id") -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是正整数（收到 {value!r}）") from None
    if result < 1:
        raise ValueError(f"{name} 必须是正整数（收到 {value!r}）")
    return result


def report_list_sql(limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS report_id, `forecast_month`, `title`, `indicator_count`, "
        "`data_cutoff`, `generated_at`, `the_status` AS status "
        f"FROM {table_ref('report')} ORDER BY `forecast_month` DESC, `id` DESC "
        f"LIMIT {row_limit(limit)}"
    )
    return sql, ()


def report_by_month_sql(forecast_month: str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS report_id, `forecast_month`, `title`, `data_cutoff`, "
        "`generated_at`, `indicator_count`, `model_pool`, `the_status` AS status "
        f"FROM {table_ref('report')} WHERE `forecast_month` = %s "
        "ORDER BY `id` DESC LIMIT 1"
    )
    return sql, (validate_month(forecast_month),)


def report_meta_sql(report_id: int | str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS report_id, `title`, `forecast_month`, `data_cutoff`, "
        "`generated_at`, `indicator_count`, `model_pool`, `narrative_summary`, "
        "`the_status` AS status "
        f"FROM {table_ref('report')} WHERE `id` = %s LIMIT 1"
    )
    return sql, (validate_id(report_id, "report_id"),)


_INDICATOR_FIELDS = (
    "`id` AS indicator_id, `report_id`, `target_name` AS name, `group_cat`, "
    "CAST(`market_pred` AS DOUBLE) AS market_pred, "
    "CAST(`the_prior` AS DOUBLE) AS prior, CAST(`pred` AS DOUBLE) AS pred, "
    "CAST(`r2` AS DOUBLE) AS r2, CAST(`zhangyu` AS DOUBLE) AS zhangyu"
)
_GROUP_ORDER = (
    "CASE `group_cat` WHEN '增长' THEN 1 WHEN '通胀' THEN 2 "
    "WHEN '信用' THEN 3 WHEN '外贸' THEN 4 ELSE 5 END"
)


def overview_indicators_sql(
    report_id: int | str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT {_INDICATOR_FIELDS} FROM {table_ref('indicator')} "
        "WHERE `report_id` = %s AND `is_active` = 1 "
        f"ORDER BY {_GROUP_ORDER}, `id` LIMIT {row_limit(limit)}"
    )
    return sql, (validate_id(report_id, "report_id"),)


def report_summary_sql(report_id: int | str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT `group_cat`, COUNT(*) AS cnt FROM {table_ref('indicator')} "
        "WHERE `report_id` = %s AND `is_active` = 1 "
        f"GROUP BY `group_cat` ORDER BY {_GROUP_ORDER}"
    )
    return sql, (validate_id(report_id, "report_id"),)


def indicators_by_group_sql(
    report_id: int | str, group_cat: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    group = str(group_cat or "").strip()
    if group not in GROUPS:
        raise ValueError(f"group_cat 只支持 {', '.join(GROUPS)}（收到 {group_cat!r}）")
    sql = (
        f"SELECT {_INDICATOR_FIELDS} FROM {table_ref('indicator')} "
        "WHERE `report_id` = %s AND `group_cat` = %s AND `is_active` = 1 "
        f"ORDER BY `id` LIMIT {row_limit(limit)}"
    )
    return sql, (validate_id(report_id, "report_id"), group)


def search_indicator_sql(
    keyword: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    text = str(keyword or "").strip()
    if not text:
        raise ValueError("keyword 必填")
    sql = (
        "SELECT i.`id` AS indicator_id, i.`report_id`, m.`forecast_month`, "
        "i.`target_name` AS name, i.`group_cat`, "
        "CAST(i.`market_pred` AS DOUBLE) AS market_pred, "
        "CAST(i.`the_prior` AS DOUBLE) AS prior, CAST(i.`pred` AS DOUBLE) AS pred, "
        "CAST(i.`r2` AS DOUBLE) AS r2, CAST(i.`zhangyu` AS DOUBLE) AS zhangyu "
        f"FROM {table_ref('indicator')} i "
        f"JOIN {table_ref('report')} m ON m.`id` = i.`report_id` "
        "WHERE i.`target_name` LIKE %s AND i.`is_active` = 1 "
        f"ORDER BY m.`forecast_month` DESC, i.`id` LIMIT {row_limit(limit)}"
    )
    return sql, (f"%{text}%",)


def indicator_snapshot_sql(indicator_id: int | str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS indicator_id, `report_id`, `target_name` AS name, `group_cat`, "
        "CAST(`market_pred` AS DOUBLE) AS market_pred, "
        "CAST(`the_prior` AS DOUBLE) AS prior, CAST(`pred` AS DOUBLE) AS pred, "
        "CAST(`r2` AS DOUBLE) AS r2, CAST(`zhangyu` AS DOUBLE) AS zhangyu, `is_active` "
        f"FROM {table_ref('indicator')} WHERE `id` = %s LIMIT 1"
    )
    return sql, (validate_id(indicator_id, "indicator_id"),)


def model_predictions_sql(
    indicator_id: int | str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `model_rank`, `model`, CAST(`pred` AS DOUBLE) AS pred, "
        "CAST(`the_prior` AS DOUBLE) AS prior, CAST(`delta` AS DOUBLE) AS delta "
        f"FROM {table_ref('prediction')} WHERE `indicator_id` = %s "
        f"ORDER BY `model_rank` LIMIT {row_limit(limit)}"
    )
    return sql, (validate_id(indicator_id, "indicator_id"),)


def feature_contributions_sql(
    indicator_id: int | str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `model_rank`, `model`, `feature_rank`, `feature`, "
        "CAST(`shap` AS DOUBLE) AS shap, CAST(`share` AS DOUBLE) AS share "
        f"FROM {table_ref('contribution')} WHERE `indicator_id` = %s "
        f"ORDER BY `model_rank`, `feature_rank` LIMIT {row_limit(limit)}"
    )
    return sql, (validate_id(indicator_id, "indicator_id"),)


def selected_features_sql(
    indicator_id: int | str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT `feature` FROM {table_ref('selected_feature')} "
        f"WHERE `indicator_id` = %s ORDER BY `feature` LIMIT {row_limit(limit)}"
    )
    return sql, (validate_id(indicator_id, "indicator_id"),)


def top_shap_features_sql(
    indicator_id: int | str, limit: int = 5
) -> tuple[str, tuple[Any, ...]]:
    size = max(1, min(int(limit) if limit else 5, 50, max_rows()))
    sql = (
        "SELECT `feature`, `model`, `model_rank`, `feature_rank`, "
        "CAST(`shap` AS DOUBLE) AS shap, CAST(`share` AS DOUBLE) AS share "
        f"FROM {table_ref('contribution')} WHERE `indicator_id` = %s "
        f"ORDER BY ABS(`shap`) DESC LIMIT {size}"
    )
    return sql, (validate_id(indicator_id, "indicator_id"),)


def compare_indicator_months_sql(
    target_name: str, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    name = str(target_name or "").strip()
    if not name:
        raise ValueError("target_name 必填；建议先通过 search_indicator_by_name 确认全名")
    sql = (
        "SELECT m.`forecast_month`, i.`id` AS indicator_id, i.`target_name` AS name, "
        "CAST(i.`pred` AS DOUBLE) AS pred, CAST(i.`the_prior` AS DOUBLE) AS prior, "
        "CAST(i.`market_pred` AS DOUBLE) AS market_pred, "
        "CAST(i.`zhangyu` AS DOUBLE) AS zhangyu, CAST(i.`r2` AS DOUBLE) AS r2 "
        f"FROM {table_ref('indicator')} i "
        f"JOIN {table_ref('report')} m ON m.`id` = i.`report_id` "
        "WHERE i.`target_name` = %s AND i.`is_active` = 1 "
        f"ORDER BY m.`forecast_month` ASC LIMIT {row_limit(limit)}"
    )
    return sql, (name,)


def llm_analysis_sql(
    report_id: int | str,
    scenario: str,
    indicator_id: int | str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    scene = str(scenario or "").strip().upper()
    if scene not in SCENARIOS:
        raise ValueError(f"scenario 只支持 {', '.join(SCENARIOS)}（收到 {scenario!r}）")
    params: list[Any] = [validate_id(report_id, "report_id"), scene]
    conditions = ["`report_id` = %s", "`scenario` = %s"]
    if scene == "SC_GLOBAL":
        conditions.append("`indicator_id` IS NULL")
    else:
        if indicator_id is None:
            raise ValueError(f"scenario={scene} 时 indicator_id 必填")
        conditions.append("`indicator_id` = %s")
        params.append(validate_id(indicator_id, "indicator_id"))
    sql = (
        "SELECT `id`, `report_id`, `scenario`, `indicator_id`, `title`, `content`, "
        "`the_status` AS status, `err_msg`, `created_at` "
        f"FROM {table_ref('analysis')} WHERE {' AND '.join(conditions)} "
        "ORDER BY `id` DESC LIMIT 1"
    )
    return sql, tuple(params)


def default_allowed_tables() -> tuple[str, ...]:
    return env_list("FORECAST_ALLOWED_TABLES") or tuple(TABLES.values())


def schema_help() -> dict[str, Any]:
    return {
        "tables": {key: table_ref(key) for key in TABLES},
        "groups": list(GROUPS),
        "analysis_scenarios": list(SCENARIOS),
        "month_format": "YYYY-MM",
        "value_semantics": {
            "pred": "模型预测值",
            "prior": "上一期实际值",
            "market_pred": "市场一致预期，可为 NULL",
            "zhangyu": "张瑜团队预测值，可为 NULL",
            "r2": "Top1 模型的 R²",
        },
        "analysis_status": {
            "pending": "生成中",
            "done": "已完成，仅此状态的 content 可作为结论引用",
            "error": "生成失败，应查看 err_msg",
        },
        "pitfalls": [
            "只有 the_status='done' 的报告数据完整",
            "market_pred 与 zhangyu 的 NULL 表示无数据，不应当作 0",
            "板块顺序固定为 增长→通胀→信用→外贸→其他",
            "SC_GLOBAL 是报告综览；SC_FEATURE 与 SC_INDICATOR 需要 indicator_id",
        ],
        "visible_tables": list(default_allowed_tables()),
    }
