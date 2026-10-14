"""10年期国债趋势判断领域层：只读取已保存的预测与运行审计。"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier, env_list, max_rows

TABLES: dict[str, str] = {
    "prediction": "fmqt_predictions_rates",
    "run": "fmqt_prediction_runs_rates",
}
DEFAULT_ROW_LIMIT = 250
PREDICTION_COLUMNS = (
    "`id`, `signal_date`, `run_id`, `probability`, `prediction`, `threshold`, "
    "`strong_signal`, `signal_label`, `validation_status`, `actual`, `is_correct`, "
    "`factor_version`, `model_version`, `created_at`, `updated_at`"
)
TREND_COLUMNS = (
    "`signal_date`, `probability`, `prediction`, `threshold`, `signal_label`, "
    "`validation_status`, `actual`, `is_correct`, `run_id`"
)
RUN_STATUS_COLUMNS = (
    "`id` AS run_id, `run_type`, `status`, `stage`, `error_message`, "
    "`source_file_name`, `latest_data_date`, `row_count`, `column_count`, "
    "`cycle_anchor`, `mature_screening_count`, `train_end_date`, "
    "`selected_factor_count`, `explanation_metadata`, `created_at`, "
    "`started_at`, `finished_at`"
)


def schema_name() -> str:
    load_env()
    return os.environ.get("RATES_SCHEMA", "").strip()


def table_ref(key: str) -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    table = f"`{assert_identifier(TABLES[key])}`"
    schema = schema_name()
    return f"`{assert_identifier(schema)}`.{table}" if schema else table


def _bounded_limit(limit: int | None, default: int, ceiling: int) -> int:
    requested = int(limit) if limit else default
    return max(1, min(requested, ceiling, max_rows()))


def validate_date(value: str, name: str = "date") -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{name} 必须是有效的 YYYY-MM-DD 日期（收到 {value!r}）") from None
    if parsed.isoformat() != text:
        raise ValueError(f"{name} 必须是 YYYY-MM-DD 格式（收到 {value!r}）")
    return text


def validate_run_id(value: int | str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"run_id 必须是正整数（收到 {value!r}）") from None
    if result < 1:
        raise ValueError(f"run_id 必须是正整数（收到 {value!r}）")
    return result


def latest_signal_date_sql() -> tuple[str, tuple[Any, ...]]:
    return f"SELECT MAX(`signal_date`) AS latest_signal_date FROM {table_ref('prediction')}", ()


def previous_signal_date_sql(signal_date: str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT MAX(`signal_date`) AS previous_signal_date FROM {table_ref('prediction')} "
        "WHERE `signal_date` < %s"
    )
    return sql, (validate_date(signal_date, "date"),)


def recent_signal_dates_sql(limit: int = 20) -> tuple[str, tuple[Any, ...]]:
    size = _bounded_limit(limit, default=20, ceiling=250)
    sql = (
        f"SELECT `signal_date` FROM {table_ref('prediction')} "
        f"ORDER BY `signal_date` DESC, `id` DESC LIMIT {size}"
    )
    return sql, ()


def prediction_sql(signal_date: str | None = None) -> tuple[str, tuple[Any, ...]]:
    params: tuple[Any, ...] = ()
    where = ""
    if signal_date is not None and str(signal_date).strip():
        where = " WHERE `signal_date` = %s"
        params = (validate_date(signal_date, "date"),)
    sql = (
        f"SELECT {PREDICTION_COLUMNS} FROM {table_ref('prediction')}{where} "
        "ORDER BY `signal_date` DESC, `id` DESC LIMIT 1"
    )
    return sql, params


def _date_range_conditions(
    start_date: str | None, end_date: str | None
) -> tuple[list[str], list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    start = validate_date(start_date, "start_date") if start_date else None
    end = validate_date(end_date, "end_date") if end_date else None
    if start and end and start > end:
        raise ValueError("start_date 不能晚于 end_date")
    if start:
        conditions.append("`signal_date` >= %s")
        params.append(start)
    if end:
        conditions.append("`signal_date` <= %s")
        params.append(end)
    return conditions, params


def prediction_page_settings(page: int, page_size: int) -> tuple[int, int]:
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        raise ValueError("page 必须是正整数") from None
    if page_number < 1:
        raise ValueError("page 必须是正整数")
    return page_number, _bounded_limit(page_size, default=20, ceiling=100)


def prediction_count_sql(
    start_date: str | None = None,
    end_date: str | None = None,
    signal_date: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    conditions, params = _date_range_conditions(start_date, end_date)
    if signal_date:
        conditions = ["`signal_date` = %s"]
        params = [validate_date(signal_date, "date")]
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return f"SELECT COUNT(`id`) AS total FROM {table_ref('prediction')}{where}", tuple(params)


def predictions_page_sql(
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    page_size: int = 20,
    signal_date: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    conditions, params = _date_range_conditions(start_date, end_date)
    if signal_date:
        conditions = ["`signal_date` = %s"]
        params = [validate_date(signal_date, "date")]
        size = 1
        offset = 0
    else:
        page_number, size = prediction_page_settings(page, page_size)
        offset = (page_number - 1) * size
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        f"SELECT {PREDICTION_COLUMNS} FROM {table_ref('prediction')}{where} "
        f"ORDER BY `signal_date` DESC, `id` DESC LIMIT {size} OFFSET {offset}"
    )
    return sql, tuple(params)


def recent_predictions_sql(limit: int = 20) -> tuple[str, tuple[Any, ...]]:
    size = _bounded_limit(limit, default=20, ceiling=250)
    sql = (
        f"SELECT {TREND_COLUMNS} FROM {table_ref('prediction')} "
        f"ORDER BY `signal_date` DESC, `id` DESC LIMIT {size}"
    )
    return sql, ()


def pending_predictions_sql(limit: int = 30) -> tuple[str, tuple[Any, ...]]:
    size = _bounded_limit(limit, default=30, ceiling=100)
    sql = (
        f"SELECT {TREND_COLUMNS} FROM {table_ref('prediction')} "
        "WHERE `validation_status` = 'pending' "
        f"ORDER BY `signal_date` DESC, `id` DESC LIMIT {size}"
    )
    return sql, ()


def run_status_sql(run_id: int | str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        f"SELECT {RUN_STATUS_COLUMNS} FROM {table_ref('run')} "
        "WHERE `id` = %s LIMIT 1"
    )
    return sql, (validate_run_id(run_id),)


def run_audit_sql(run_id: int | str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS run_id, `run_type`, `status`, `stage`, `source_file_name`, "
        "`file_sha256`, `source_size_bytes`, `latest_data_date`, `row_count`, "
        "`column_count`, `cycle_anchor`, `screening_start`, `screening_end_exclusive`, "
        "`mature_screening_count`, `train_start`, `train_end_exclusive`, `train_end_date`, "
        "`selected_factor_count`, `selected_factors`, `best_params`, `decision_threshold`, "
        "`config_hash`, `code_hash`, `created_at`, `started_at`, `finished_at` "
        f"FROM {table_ref('run')} WHERE `id` = %s LIMIT 1"
    )
    return sql, (validate_run_id(run_id),)


def selected_factors_sql() -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS run_id, `run_type`, `latest_data_date`, `selected_factor_count`, "
        f"`selected_factors` FROM {table_ref('run')} "
        "WHERE `status` = 'SUCCEEDED' AND `selected_factor_count` IS NOT NULL "
        "AND `selected_factor_count` > 0 ORDER BY `id` DESC LIMIT 1"
    )
    return sql, ()


def backtest_metrics_sql() -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS metrics_run_id, `run_type`, "
        "`latest_data_date` AS metrics_data_end, `finished_at` AS metrics_recorded_at, "
        f"`metrics` FROM {table_ref('run')} "
        "WHERE `status` = 'SUCCEEDED' AND `metrics` IS NOT NULL "
        "ORDER BY `id` DESC LIMIT 1"
    )
    return sql, ()


def run_metrics_sql(run_id: int | str) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT `id` AS metrics_run_id, `run_type`, `status`, `latest_data_date`, `metrics` "
        f"FROM {table_ref('run')} WHERE `id` = %s LIMIT 1"
    )
    return sql, (validate_run_id(run_id),)


def latest_explanation_sql() -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT r.`id` AS run_id, r.`factor_explanation`, r.`explanation_metadata` "
        f"FROM {table_ref('run')} r WHERE r.`id` = ("
        f"SELECT p.`run_id` FROM {table_ref('prediction')} p "
        "ORDER BY p.`signal_date` DESC, p.`id` DESC LIMIT 1) LIMIT 1"
    )
    return sql, ()


def parse_json_value(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "replace")
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def decorate_prediction(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    try:
        probability = float(item["probability"]) if item.get("probability") is not None else None
    except (TypeError, ValueError):
        probability = None
    item["probability"] = probability
    item["probability_up_pct"] = round(probability * 100, 2) if probability is not None else None
    return item


def decorate_run_status(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    metadata = parse_json_value(item.pop("explanation_metadata", None), {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    item["explanation_status"] = metadata.get("status", "LEGACY")
    return item


def decorate_selected_factors(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    factors = parse_json_value(item.get("selected_factors"), []) or []
    item["selected_factors"] = factors if isinstance(factors, list) else []
    return item


def decorate_metrics(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["metrics"] = parse_json_value(item.get("metrics"), None)
    return item


def decorate_explanation(row: dict[str, Any]) -> dict[str, Any]:
    metadata = parse_json_value(row.get("explanation_metadata"), {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    content = row.get("factor_explanation")
    status = str(metadata.get("status") or "LEGACY").upper()
    return {
        "run_id": row.get("run_id"),
        "status": status,
        "content": content,
        "generated_at": metadata.get("generated_at"),
        "has_content": bool(content and str(content).strip()),
        "is_retained_content": bool(content and str(content).strip() and status != "SUCCEEDED"),
        "error": metadata.get("error"),
        "evidence": metadata.get("evidence"),
    }


def default_allowed_tables() -> tuple[str, ...]:
    return env_list("RATES_ALLOWED_TABLES") or tuple(TABLES.values())


def schema_help() -> dict[str, Any]:
    return {
        "tables": {key: table_ref(key) for key in TABLES},
        "target": "锚定参考日 T，预测 T+26 相对 T+5 的 10 年期国债收益率涨跌",
        "date_format": "YYYY-MM-DD",
        "probability_unit": "0—1；probability_up_pct 为展示用百分数",
        "validation_status": {"pending": "真实标签未成熟", "realized": "真实标签已成熟"},
        "run_status": ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"],
        "pitfalls": [
            "只读取已保存结果；不启动训练、不调用大模型、不读取或解压 csv_gzip",
            "prediction、threshold 与 signal_label 保持原始保存口径，可能互不一致",
            "pending 预测不计入错误预测或准确率分母，actual/is_correct 的 NULL 不补零",
            "最新预测、最新成功运行与最近保存的回测指标可能来自不同 run_id",
            "因子解释 FAILED/RUNNING 时若正文非空，该正文可能是上次成功内容",
            "percentile_3y 已是 0—100；local_contribution 是对数胜算贡献",
        ],
        "visible_tables": list(default_allowed_tables()),
    }
