"""G7 外汇研判模型领域层（只读一张历史表，六币种结果存在 JSON 字段里）。

表 ``fmqt_g7_fx_history`` 一行 = 一个「信号日期 + 预测期限」快照，**六个货币对的
完整结果保存在 ``result_json.records`` 数组里**（不是六行记录）。口径照
``docs/MCP_docs/G7外汇研判模型_SQL清单_MCP信息.md``：

- 唯一键是 ``(signal_date, horizon_days)``；当前正式期限只有 **22 交易日**，
  ``5`` / ``10`` 在现有服务里**直接返回空、不查库**
- ``prob_up`` / ``return5`` / ``rv20`` / ``test_accuracy`` 是 0—1 小数比率；
  ``Close`` 是**汇价**（如 EURUSD=1.1155），不是收益率百分数
- ``impact_pp`` 已经是**概率百分点**，不再乘 100，且**不可相加**成收益贡献
- 最新按指定 ``horizon_days`` 取最大 ``signal_date``，**不按 ``updated_at``**
- 页面历史窗口是「最新信号日向前**一个自然月**」（含下界），不是最近 30 天／
  30 条；``3-31`` 回溯到 ``2-28/29``，故 ``floor`` 用 **Python 日历**计算——
  MySQL 的 ``DATE_SUB`` 与 SQLite 的 ``-1 month`` 在月底口径上不一致
- 六个货币对按 ``Symbol`` 识别，**不假设 ``records[0]`` 永远是 EURUSD**
- ``EURUSD/GBPUSD/AUDUSD`` 上涨 = 非美元货币对美元走强；
  ``USDJPY/USDCAD/USDCHF`` 上涨 = 美元走强——**不能统一称"美元上涨"**
- 缺失键或 JSON null 一律保留为 null，不用 ``COALESCE(..., 0)`` 改成零

⚠️ 本模块**不使用** ``JSON_TABLE`` / ``JSON_EXTRACT`` / ``DATE_SUB``：前两个在
SQLite 仿真下不可用，后者在月底口径上与 Python 日历不一致。快照取回后在
Python 层解析 ``records`` 并按 ``Symbol`` 分组。

本模块不做训练、不生成预测、不重算回测、不调用大模型，也不提供任何写入 SQL。
"""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date as _date
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier

TABLES: dict[str, str] = {"main": "fmqt_g7_fx_history"}

MODEL_NAME = "G7外汇研判模型"

#: 工具接受的期限；**当前只有 22 有正式结果**，5/10 直接返回空
HORIZONS: tuple[int, ...] = (5, 10, 22)
DEFAULT_HORIZON = 22
#: 有正式落库结果的期限（其余在工具层直接返回空，不查库）
OFFICIAL_HORIZONS: tuple[int, ...] = (22,)

SYMBOLS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "USDJPY",
    "USDCAD",
    "USDCHF",
)
DEFAULT_SYMBOL_ORDER: tuple[str, ...] = SYMBOLS

#: 非美元货币对：上涨 = 该货币对美元走强（其余三个是美元走强）
NON_USD_BASE: tuple[str, ...] = ("EURUSD", "GBPUSD", "AUDUSD")

#: 单币种摘要返回的指标字段
PAIR_METRIC_FIELDS: tuple[str, ...] = (
    "signal_date",
    "horizon_days",
    "forecast_date",
    "Symbol",
    "Close",
    "prob_up",
    "signal",
    "return5",
    "rv20",
    "test_auc",
    "test_accuracy",
    "auc_ci_low",
    "auc_ci_high",
    "recent45_majority_accuracy",
    "recent45_hits",
    "recent45_n",
    "test_n",
    "test_origin_first",
    "test_origin_last",
    "test_maturity_last",
)

#: 解释类返回的字段
EXPLANATION_FIELDS: tuple[str, ...] = (
    "signal_date",
    "horizon_days",
    "Symbol",
    "factor_impacts",
    "factor_explanation",
    "maturity_copy",
    "evaluation_note",
    "source_generation",
    "source_name",
    "source_file",
    "snapshot_kind",
)

#: 概率类字段（0—1 小数，展示时乘 100；AUC 不乘）
RATIO_FIELDS: tuple[str, ...] = (
    "prob_up",
    "return5",
    "rv20",
    "test_auc",
    "test_accuracy",
    "auc_ci_low",
    "auc_ci_high",
    "recent45_majority_accuracy",
)

#: 默认快照缺失 ``snapshot_kind`` 时的展示默认值（不反写原 JSON）
DEFAULT_SNAPSHOT_KIND = "archived_snapshot"

DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 100
DEFAULT_OBSERVATIONS = 30
ALLOWED_OBSERVATIONS: tuple[int, ...] = (30, 60)
MAX_OBSERVATIONS = 60

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def schema_name() -> str:
    load_env()
    return os.environ.get("G7_FX_SCHEMA", "").strip()


def table_ref(key: str = "main") -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    table = f"`{assert_identifier(TABLES[key])}`"
    schema = schema_name()
    return f"`{assert_identifier(schema)}`.{table}" if schema else table


def default_allowed_tables() -> tuple[str, ...]:
    """结构探索白名单缺省值：本场景唯一一张表。"""
    return tuple(TABLES.values())


# ── 参数校验 ────────────────────────────────────────────────────────────


def validate_horizon(value: int | None) -> int:
    """预测期限：只接受 5 / 10 / 22。"""
    if value is None:
        return DEFAULT_HORIZON
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"horizon 必须是整数（收到 {value!r}）") from None
    if resolved not in HORIZONS:
        raise ValueError(
            f"horizon 只支持 {', '.join(str(h) for h in HORIZONS)}（收到 {value!r}）"
        )
    return resolved


def has_official_result(horizon: int) -> bool:
    """该期限是否有正式落库结果（没有就直接返回空，不查库）。"""
    return horizon in OFFICIAL_HORIZONS


def validate_symbol(value: str | None) -> str:
    """货币对：必填，且必须在六币种白名单内（枚举要拼进筛选，必须收窄）。"""
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError(f"symbol 不能为空，可选：{', '.join(SYMBOLS)}")
    if text not in SYMBOLS:
        raise ValueError(f"symbol 只支持 {', '.join(SYMBOLS)}（收到 {value!r}）")
    return text


def validate_date(value: str | None, name: str = "date") -> str | None:
    """校验 ``YYYY-MM-DD``（含真实日历校验）；留空返回 None（表示取最新）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM-DD 格式（收到 {value!r}）")
    try:
        year, month, day = (int(part) for part in text.split("-"))
        _date(year, month, day)
    except ValueError as e:
        raise ValueError(f"{name} 不是有效日期：{text}（{e}）") from None
    return text


def bounded_limit(
    value: int | None,
    default: int = DEFAULT_HISTORY_LIMIT,
    maximum: int = MAX_HISTORY_LIMIT,
) -> int:
    """条数：1~maximum；留空取缺省。"""
    requested = int(value) if value is not None else default
    return max(1, min(requested, maximum))


def validate_observations(value: int | None) -> int:
    """行情观测数：只接受 30 / 60（与现页面一致）。"""
    if value is None:
        return DEFAULT_OBSERVATIONS
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"observations 必须是整数（收到 {value!r}）") from None
    if resolved not in ALLOWED_OBSERVATIONS:
        raise ValueError(
            f"observations 只支持 {', '.join(str(n) for n in ALLOWED_OBSERVATIONS)}"
            f"（收到 {value!r}）"
        )
    return resolved


def month_floor(signal_date: str) -> str:
    """最近一个**自然月**的下界（含），用 Python 日历计算。

    ``2026-10-12`` → ``2026-09-12``；``3-31`` → ``2-28/29``。

    刻意不用 MySQL 的 ``DATE_SUB(..., INTERVAL 1 MONTH)``：它和 SQLite 的
    ``-1 month`` 在月底口径上不一致，而仓储侧本来就是在 Python 里算的。
    """
    resolved = validate_date(signal_date, "signal_date")
    assert resolved is not None
    year, month, day = (int(part) for part in resolved.split("-"))
    target_month = month - 1
    target_year = year
    if target_month < 1:
        target_month = 12
        target_year -= 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    return f"{target_year:04d}-{target_month:02d}-{min(day, last_day):02d}"


# ── JSON 解码与 records 提取 ────────────────────────────────────────────


def decode_result(value: Any) -> dict[str, Any]:
    """把 ``result_json`` 还原成对象（兼容 dict / str / bytes 三种驱动返回）。"""
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


def extract_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    """取 ``records`` 数组；不是数组时返回空列表（不伪造币种）。"""
    records = result.get("records")
    if not isinstance(records, list):
        return []
    return [item for item in records if isinstance(item, dict)]


def snapshot_kind(result: dict[str, Any]) -> str:
    """快照类型：缺失时展示层默认 ``archived_snapshot``（不反写原 JSON）。"""
    value = result.get("snapshot_kind")
    text = str(value).strip() if value is not None else ""
    return text or DEFAULT_SNAPSHOT_KIND


def find_pair(result: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    """按 ``Symbol`` 找币种记录——**不假设数组顺序**。"""
    wanted = symbol.strip().upper()
    for item in extract_records(result):
        if str(item.get("Symbol") or "").strip().upper() == wanted:
            return item
    return None


def ordered_pairs(result: dict[str, Any]) -> list[dict[str, Any]]:
    """六币种按固定顺序返回（缺失的币种不补造）。"""
    records = extract_records(result)
    by_symbol = {
        str(item.get("Symbol") or "").strip().upper(): item for item in records
    }
    ordered = [by_symbol[s] for s in DEFAULT_SYMBOL_ORDER if s in by_symbol]
    # 未在白名单顺序里的（上游扩展币种）追加在后，保留而不是丢弃
    ordered.extend(
        item
        for key, item in by_symbol.items()
        if key not in DEFAULT_SYMBOL_ORDER
    )
    return ordered


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pair_metrics(pair: dict[str, Any], signal_date: str, horizon: int) -> dict[str, Any]:
    """单币种指标投影：原值返回（含 null），概率类附带展示用百分数。"""
    data: dict[str, Any] = {}
    for field in PAIR_METRIC_FIELDS:
        if field == "signal_date":
            data[field] = str(pair.get("signal_date") or signal_date)
        elif field == "horizon_days":
            data[field] = pair.get("horizon_days") or horizon
        elif field in RATIO_FIELDS or field == "Close":
            data[field] = _as_float(pair.get(field))
        else:
            data[field] = pair.get(field)

    prob = _as_float(pair.get("prob_up"))
    data["display"] = {
        "prob_up_pct": round(prob * 100, 2) if prob is not None else None,
        "move_direction_note": (
            "非美元货币对（EURUSD/GBPUSD/AUDUSD）上涨 = 该货币对美元走强；"
            "USDJPY/USDCAD/USDCHF 上涨 = 美元走强"
        ),
    }
    return data


def pair_price_history(
    pair: dict[str, Any], observations: int = DEFAULT_OBSERVATIONS
) -> list[dict[str, Any]]:
    """快照内已存行情点：取最后 N 个，保持原有升序。

    ``price_history`` 缺失/为 null/空数组时返回空列表，**不伪造行情点**。
    """
    raw = pair.get("price_history")
    if not isinstance(raw, list):
        return []
    points = [item for item in raw if isinstance(item, dict)]
    if not points:
        return []
    tail = points[-observations:] if len(points) > observations else points
    return [
        {"date": str(item.get("date")), "close": _as_float(item.get("close"))}
        for item in tail
    ]


def pair_explanation(pair: dict[str, Any], signal_date: str, horizon: int) -> dict[str, Any]:
    """已存解释与数值归因：原样返回，缺失就是没有，不用最新日补旧日。"""
    data: dict[str, Any] = {}
    for field in EXPLANATION_FIELDS:
        if field == "signal_date":
            data[field] = str(pair.get("signal_date") or signal_date)
        elif field == "horizon_days":
            data[field] = pair.get("horizon_days") or horizon
        else:
            data[field] = pair.get(field)
    impacts = data.get("factor_impacts")
    data["factor_impact_count"] = len(impacts) if isinstance(impacts, list) else 0
    return data


def history_item(result: dict[str, Any], signal_date: str, horizon: int) -> dict[str, Any]:
    """历史摘要：只出日期 + 六币种的概率与信号，不展开长解释与行情数组。"""
    pairs = []
    for pair in ordered_pairs(result):
        pairs.append(
            {
                "Symbol": pair.get("Symbol"),
                "prob_up": _as_float(pair.get("prob_up")),
                "signal": pair.get("signal"),
                "pred_direction_note": (
                    "1 = 该货币对报价上行，0 = 下行"
                    if "Signal" not in pair
                    else pair.get("Signal")
                ),
                "prob_up_pct": (
                    round(_as_float(pair.get("prob_up")) * 100, 2)
                    if _as_float(pair.get("prob_up")) is not None
                    else None
                ),
            }
        )
    return {
        "signal_date": signal_date,
        "horizon_days": horizon,
        "pair_count": len(pairs),
        "pairs": pairs,
    }


def schema_help() -> dict[str, Any]:
    """数据字典：字段、单位、信号方向与陷阱。"""
    return {
        "model_name": MODEL_NAME,
        "table": TABLES["main"],
        "schema": schema_name() or None,
        "storage": (
            "一行 = 一个「信号日期 + 预测期限」快照；六个货币对的完整结果在 "
            "result_json.records 数组里，不是六行记录"
        ),
        "primary_key": "（signal_date, horizon_days）复合主键",
        "horizons": {
            "accepted": list(HORIZONS),
            "default": DEFAULT_HORIZON,
            "with_official_result": list(OFFICIAL_HORIZONS),
            "note": "5 / 10 期限在现有服务中直接返回空，不查询数据库，也不借用 22 日结果",
        },
        "symbols": list(SYMBOLS),
        "direction_note": (
            "EURUSD/GBPUSD/AUDUSD 上涨 = 非美元货币对美元走强；"
            "USDJPY/USDCAD/USDCHF 上涨 = 美元走强——不能统一称「美元上涨」"
        ),
        "units": {
            "Close": "该货币对即期报价（如 EURUSD=1.1155），不是收益率百分数",
            "prob_up": "0—1 小数（上涨概率），不是历史命中率",
            "return5": "0—1 小数比率（上游已算的近 5 期变动）",
            "rv20": "0—1 小数（20 期年化波动）",
            "test_auc / test_accuracy": "0—1（已存回溯指标）",
            "impact_pp": "概率百分点（已是百分点，不再乘 100，且不可相加）",
        },
        "window_rule": (
            "页面历史窗口 = 库内最新信号日向前一个自然月（含下界），"
            "不是最近 30 天/30 条；指定日早于窗口下界按无记录处理，不回退"
        ),
        "latest_rule": "按 horizon_days 取最大 signal_date，不按 updated_at",
        "not_available": (
            "本表没有每条预测的实际未来标签或「是否正确」列；test_accuracy 是上游"
            "评价区间指标，不能当作某条当前预测已验证的结果"
        ),
        "pitfalls": [
            "prob_up 是该货币对报价上涨概率，不是统一「美元上涨概率」，也不是命中率",
            "Close 是汇价原值，不要当成收益率或百分点",
            "impact_pp 已是百分点，不可相加成收益贡献，也不是经济因果",
            "snapshot_kind 缺失时展示默认 archived_snapshot，但原 JSON 缺省状态仍保留",
            "historical_reconstruction 是历史回溯恢复，不等于当日正式发布存档",
            "factor_impacts 缺失或为 null 表示没有已存归因，不是「影响为零」",
        ],
    }


# ── SQL 构造 ────────────────────────────────────────────────────────────


def range_sql(horizon: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.1：该期限的快照数量与起止日期（空表返回一行 0 / NULL）。"""
    resolved = validate_horizon(horizon)
    return (
        f"SELECT COUNT(*) AS snapshot_count, MIN(`signal_date`) AS first_date, "
        f"MAX(`signal_date`) AS latest_date FROM {table_ref()} "
        f"WHERE `horizon_days` = %s",
        (resolved,),
    )


def latest_and_floor_sql(horizon: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.2：最新信号日期（``floor`` 由 :func:`month_floor` 在 Python 侧计算）。"""
    resolved = validate_horizon(horizon)
    return (
        f"SELECT MAX(`signal_date`) AS latest_date FROM {table_ref()} "
        f"WHERE `horizon_days` = %s",
        (resolved,),
    )


def dates_sql(horizon: int | None = None, floor: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.3：窗口内可查询日期（降序），只读日期不加载 JSON。"""
    resolved = validate_horizon(horizon)
    conditions = ["`horizon_days` = %s"]
    params: list[Any] = [resolved]
    if floor:
        conditions.append("`signal_date` >= %s")
        params.append(floor)
    return (
        f"SELECT `signal_date` FROM {table_ref()} "
        f"WHERE {' AND '.join(conditions)} ORDER BY `signal_date` DESC",
        tuple(params),
    )


def previous_date_sql(
    horizon: int | None = None, signal_date: str | None = None, floor: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """2.4：窗口内指定日之前的最近可展示结果日（不是日期减一天）。"""
    resolved = validate_horizon(horizon)
    date = validate_date(signal_date, "signal_date")
    conditions = ["`horizon_days` = %s", "`signal_date` < %s"]
    params: list[Any] = [resolved, date]
    if floor:
        conditions.append("`signal_date` >= %s")
        params.append(floor)
    return (
        f"SELECT MAX(`signal_date`) AS previous_date FROM {table_ref()} "
        f"WHERE {' AND '.join(conditions)}",
        tuple(params),
    )


def latest_snapshot_sql(horizon: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.5：最新一份完整快照（六币种全在 result_json 里）。"""
    resolved = validate_horizon(horizon)
    return (
        f"SELECT `signal_date`, `horizon_days`, `result_json`, `created_at`, "
        f"`updated_at` FROM {table_ref()} WHERE `horizon_days` = %s "
        f"ORDER BY `signal_date` DESC LIMIT 1",
        (resolved,),
    )


def snapshot_sql(
    horizon: int | None = None, signal_date: str | None = None, floor: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """2.6：指定日完整快照（页面兼容口径，受窗口下界约束，不回退）。"""
    resolved = validate_horizon(horizon)
    date = validate_date(signal_date, "signal_date")
    conditions = ["`signal_date` = %s", "`horizon_days` = %s"]
    params: list[Any] = [date, resolved]
    if floor:
        conditions.append("`signal_date` >= %s")
        params.append(floor)
    return (
        f"SELECT `signal_date`, `horizon_days`, `result_json`, `created_at`, "
        f"`updated_at` FROM {table_ref()} WHERE {' AND '.join(conditions)}",
        tuple(params),
    )


def _history_filters(
    horizon: int,
    floor: str | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, list[Any]]:
    conditions = ["`horizon_days` = %s"]
    params: list[Any] = [horizon]
    if floor:
        conditions.append("`signal_date` >= %s")
        params.append(floor)
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
    return " WHERE " + " AND ".join(conditions), params


def history_count_sql(
    horizon: int | None = None,
    floor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """2.7：窗口内日期闭区间的快照总数。"""
    resolved = validate_horizon(horizon)
    where, params = _history_filters(resolved, floor, start_date, end_date)
    return (f"SELECT COUNT(*) AS matched_count FROM {table_ref()}{where}", tuple(params))


def history_sql(
    horizon: int | None = None,
    floor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """2.7：窗口内最近 N 份快照（SQL 降序，工具层不做反转）。"""
    resolved = validate_horizon(horizon)
    where, params = _history_filters(resolved, floor, start_date, end_date)
    size = bounded_limit(limit)
    return (
        f"SELECT `signal_date`, `horizon_days`, `result_json`, `created_at`, "
        f"`updated_at` FROM {table_ref()}{where} "
        f"ORDER BY `signal_date` DESC LIMIT {size}",
        tuple(params),
    )
