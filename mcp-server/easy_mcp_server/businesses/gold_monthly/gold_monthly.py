"""黄金月频预测领域层（只读一张宽表，一行 = 一个预测基准日的一期预测 + 事后验证）。

表 ``fmqt_gold_monthly_forecast`` 是**平铺宽表**（不像其它场景把结果塞进
``result_json``），一行就是一期的全部信息。口径照
``docs/MCP_docs/黄金月频预测_SQL清单_MCP信息.md``：

- ``prob_up`` 是**小数概率**：``0.229329`` 表示 22.93%，**不是百分点**
- ``pred_direction`` / ``actual_direction``：**``1`` = 涨，``0`` = 跌**，
  不是 ``+1/-1``、也不是 ``true/false``
- ``actual_return`` 是 **T+22 个交易日对数收益率**：``-0.086108`` = 区间跌 8.61%，
  **不是**简单收益率
- ``hit``：``1`` 命中 / ``0`` 未命中 / **``NULL`` = 未兑现**——
  ⚠️ ``NULL`` **不能当 0**，否则命中率会被稀释成假信号
- 任何涉及 ``hit`` 的统计都必须 ``hit IS NOT NULL``（本模块在 SQL 里固化，
  不交给调用方决定）
- 命中只认 ``hit = 1``；写成 ``SUM(hit = pred_direction)`` 会让看跌组虚高
  （``pred_direction = 0`` 时未命中的 ``hit = 0`` 也被算成命中）
- 最新一期按 ``MAX(predict_date)`` 取，**不看 ``updated_at``**
- ``symbol`` 相同时 ``predict_date`` 唯一（唯一索引 ``uk_predict_date``）

与周频（``gold_weekly``）的两处关键差异，别混用：

- **窗口是 22 个交易日**（周频 5 个），所以"待验证"期数更多（当前 22 期），
  最新已兑现日比最新预测日**早约一个月**；
- **相邻两期的预测窗口重合 21 个交易日**，样本高度重叠——"连续命中 60 期"
  约等于"同一波行情判断对了 3 个月"，**不是 60 个独立事件**。

本模块不做训练、不生成预测、不重算回测，也不提供任何写入 SQL。
"""

from __future__ import annotations

import os
import re
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier

TABLES: dict[str, str] = {"main": "fmqt_gold_monthly_forecast"}

MODEL_NAME = "黄金月频预测"

DEFAULT_SYMBOL = "XAUUSD"
SYMBOLS: tuple[str, ...] = ("XAUUSD",)

#: 字段单位说明（原值返回，不换算）
UNITS: dict[str, str] = {
    "prob_up": "ratio_0_1（0.229329 表示 22.93%，不是百分点）",
    "pred_direction": "1 看涨 / 0 看跌",
    "actual_direction": "1 涨 / 0 跌；未兑现为 null",
    "actual_return": "T+22 个交易日对数收益率（小数）；未兑现为 null",
    "hit": "1 命中 / 0 未命中；未兑现为 null（不是 0）",
}

#: 明细列表返回的字段（不含 id / created_at / updated_at 这类运维字段）
DETAIL_FIELDS: tuple[str, ...] = (
    "predict_date",
    "symbol",
    "prob_up",
    "pred_direction",
    "actual_direction",
    "actual_return",
    "hit",
)

DEFAULT_DATES_LIMIT = 30
DEFAULT_HISTORY_LIMIT = 30
DEFAULT_PENDING_LIMIT = 10
DEFAULT_ROLLING_LIMIT = 30
DEFAULT_STREAK_LIMIT = 10
MAX_LIMIT = 200
MAX_DAYS = 250
MAX_PENDING_LIMIT = 50
MAX_STREAK_LIMIT = 50
MAX_WINDOW = 250
MIN_WINDOW = 5

#: 概率分档边界（月频页面「强势下跌 / 强势上涨」的分档线，固定口径）
CALIBRATION_EDGES: tuple[float, ...] = (0.30, 0.50, 0.70)

#: 收益分档阈值（对数收益率口径；月频是 22 个交易日的累计波动，±5% 是小涨小跌与大涨大跌的分界）
RETURN_BUCKET_EDGES: tuple[float, ...] = (-0.05, 0.0, 0.05)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def schema_name() -> str:
    load_env()
    return os.environ.get("GOLD_MONTHLY_SCHEMA", "").strip()


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


def validate_symbol(value: str | None) -> str:
    """标的代码：留空取缺省；只放行白名单内的值（要拼进 SQL 的枚举必须收窄）。"""
    text = str(value or "").strip().upper()
    if not text:
        return DEFAULT_SYMBOL
    if text not in SYMBOLS:
        raise ValueError(f"symbol 只支持 {', '.join(SYMBOLS)}（收到 {value!r}）")
    return text


def validate_date(value: str | None, name: str = "predict_date") -> str | None:
    """校验 ``YYYY-MM-DD``；留空返回 None（表示取最新预测日）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM-DD 格式（收到 {value!r}）")
    return text


def validate_required_date(value: str | None, name: str = "predict_date") -> str:
    """必填日期：留空即报错。"""
    resolved = validate_date(value, name)
    if resolved is None:
        raise ValueError(f"{name} 不能为空")
    return resolved


def bounded_limit(
    value: int | None,
    default: int,
    maximum: int = MAX_LIMIT,
) -> int:
    """条数：1~maximum；留空取调用方指定的缺省值。"""
    requested = int(value) if value is not None else default
    return max(1, min(requested, maximum))


def bounded_window(value: int | None, default: int = 50) -> int:
    """滚动窗口：5~250。"""
    requested = int(value) if value is not None else default
    return max(MIN_WINDOW, min(requested, MAX_WINDOW))


def bounded_rate(value: float | None, default: float, low: float, high: float) -> float:
    """比率参数（阈值）：限定在 [low, high]。"""
    resolved = float(value) if value is not None else default
    if resolved < low or resolved > high:
        raise ValueError(f"阈值必须在 {low} ~ {high} 之间（收到 {value!r}）")
    return resolved


def confidence_thresholds(hi: float | None, lo: float | None) -> tuple[float, float]:
    """高置信度阈值：``hi`` 看涨阈值（0.5~1）、``lo`` 看跌阈值（0~0.5）。

    缺省取月频页面强弱信号的分档线 ``0.70 / 0.30``。
    """
    high = bounded_rate(hi, 0.70, 0.5, 1.0)
    low = bounded_rate(lo, 0.30, 0.0, 0.5)
    if low >= high:
        raise ValueError(f"lo 必须小于 hi（收到 lo={low}, hi={high}）")
    return high, low


# ── 行处理 ──────────────────────────────────────────────────────────────


def _as_float(value: Any) -> float | None:
    """数值列：None 保持 None（未兑现不是 0）。"""
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


def detail_row(row: dict[str, Any]) -> dict[str, Any]:
    """明细行：原值返回，``hit`` 为 None 表示尚未兑现（保留 null，不改成 0）。"""
    out: dict[str, Any] = {}
    for field in DETAIL_FIELDS:
        value = row.get(field)
        if field == "predict_date":
            out[field] = str(value) if value is not None else None
        elif field in ("prob_up", "actual_return"):
            out[field] = _as_float(value)
        elif field in ("pred_direction", "actual_direction", "hit"):
            out[field] = _as_int(value)
        else:
            out[field] = value
    # 展示用：概率与收益转百分数（原字段仍是 0—1 小数）
    prob = out.get("prob_up")
    ret = out.get("actual_return")
    out["display"] = {
        "prob_up_pct": round(prob * 100, 2) if prob is not None else None,
        "actual_return_pct": round(ret * 100, 2) if ret is not None else None,
        "pending": out.get("hit") is None,
    }
    return out


def pending_row(row: dict[str, Any]) -> dict[str, Any]:
    """待验证行：只有预测，没有实际列。"""
    prob = _as_float(row.get("prob_up"))
    return {
        "predict_date": str(row["predict_date"]) if row.get("predict_date") else None,
        "symbol": row.get("symbol"),
        "prob_up": prob,
        "pred_direction": _as_int(row.get("pred_direction")),
        "display": {"prob_up_pct": round(prob * 100, 2) if prob is not None else None},
    }


def stat_row(row: dict[str, Any]) -> dict[str, Any]:
    """把统计查询的一行统一成数值类型（COUNT/SUM 在驱动里可能是 Decimal）。"""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in (
            "bucket",
            "subset",
            "yr",
            "prob_bucket",
            "run_start",
            "run_end",
            "predict_date",
            "through_date",
        ):
            out[key] = str(value) if value is not None else None
        elif key == "hit":
            out[key] = _as_int(value)
        else:
            out[key] = _as_float(value)
    return out


def schema_help() -> dict[str, Any]:
    """数据字典：字段、单位、陷阱。"""
    return {
        "model_name": MODEL_NAME,
        "table": TABLES["main"],
        "schema": schema_name() or None,
        "storage": (
            "一行 = 一个预测基准日的一期预测 + 事后验证（平铺宽表，不是 JSON 字段）"
        ),
        "symbol": {"default": DEFAULT_SYMBOL, "supported": list(SYMBOLS)},
        "units": dict(UNITS),
        "direction_scale": "1 = 涨，0 = 跌（pred_direction 与 actual_direction 同）",
        "horizon": "T+22 个交易日（月频）；周频是 T+5，两者不要混用",
        "accuracy_rule": (
            "所有命中率只在已兑现期数上计算（SQL 内固化 hit IS NOT NULL）；"
            "hit 为 null 表示预测日之后还不足 22 个交易日，不是未命中"
        ),
        "latest_rule": "最新一期按 MAX(predict_date) 判断，不按 updated_at",
        "detail_fields": list(DETAIL_FIELDS),
        "not_available": (
            "本表没有收益金额、持仓、交易成本或实盘记录；prob_up 是方向概率，"
            "不是预期涨幅——要收益口径只能用 actual_return（且仅已兑现期数有值）"
        ),
        "pitfalls": [
            "prob_up 是小数概率（0.229329 = 22.93%），不是百分点，不要当涨幅",
            "actual_return 是 T+22 对数收益率（-0.086108 = 跌 8.61%），不是简单收益率",
            "hit 为 null 是「未兑现」，不是「未命中」；全量求和会把待验证期算成错",
            "命中只看 hit = 1，不要写成 hit = pred_direction",
            "全样本命中率约 0.65，但分年区间 0.57~0.73、最近 30 期只有 0.47——"
            "回答「准不准」要给出观察区间与样本量",
            "相邻两期预测窗口重合 21 个交易日，样本高度重叠：连击长不等于模型变强",
            "预测记录从 2020-02-20 起（模型先攒训练样本），此前没有记录不是数据缺失",
        ],
    }


# ── SQL 构造（每条对应 SQL 清单的一个章节）────────────────────────────


def _symbol_filter(symbol: str) -> tuple[str, list[Any]]:
    return "`symbol` = %s", [symbol]


def coverage_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.16：库覆盖区间与更新时点（含总数与已兑现数）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    return (
        f"SELECT COUNT(*) AS total, SUM(`hit` IS NOT NULL) AS verified, "
        f"MIN(`predict_date`) AS first_date, MAX(`predict_date`) AS last_date, "
        f"MAX(`updated_at`) AS last_written FROM {table_ref()} WHERE {where}",
        tuple(params),
    )


def latest_date_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.1：最新预测日（走 uk_predict_date 索引，不要 ORDER BY ... LIMIT 1）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    return (
        f"SELECT MAX(`predict_date`) AS d FROM {table_ref()} WHERE {where}",
        tuple(params),
    )


def latest_verified_date_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.2：最新**已兑现**预测日（通常比最新预测日早约一个月）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    return (
        f"SELECT MAX(`predict_date`) AS d FROM {table_ref()} "
        f"WHERE {where} AND `hit` IS NOT NULL",
        tuple(params),
    )


def previous_date_sql(
    symbol: str | None = None, predict_date: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.3：给定日期之前最近的一个预测日（用于环比）。

    ``predict_date`` 留空时以最新预测日为基准。
    ⚠️ 「上一期」是**交易日相邻**，会跨周末、假期、月份，
    **不能**用 ``DATE_SUB(?, INTERVAL 1 DAY)`` 推算。
    """
    resolved_symbol = validate_symbol(symbol)
    where, params = _symbol_filter(resolved_symbol)
    date = validate_date(predict_date)
    if date:
        return (
            f"SELECT MAX(`predict_date`) AS d FROM {table_ref()} "
            f"WHERE {where} AND `predict_date` < %s",
            (*params, date),
        )
    return (
        f"SELECT MAX(`predict_date`) AS d FROM {table_ref()} "
        f"WHERE {where} AND `predict_date` < "
        f"(SELECT MAX(`predict_date`) FROM {table_ref()} WHERE {where})",
        (*params, *params),
    )


def dates_sql(symbol: str | None = None, days: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.4：最近 N 个预测日（倒序）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    size = bounded_limit(days, DEFAULT_DATES_LIMIT, MAX_DAYS)
    return (
        f"SELECT `predict_date` AS d FROM {table_ref()} WHERE {where} "
        f"ORDER BY `predict_date` DESC LIMIT {size}",
        tuple(params),
    )


def history_sql(
    symbol: str | None = None, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.5：最近 N 期明细（倒序，含未兑现的 null，不用 IFNULL 抹平）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    size = bounded_limit(limit, DEFAULT_HISTORY_LIMIT, MAX_LIMIT)
    return (
        f"SELECT `predict_date`, `symbol`, `prob_up`, `pred_direction`, "
        f"`actual_direction`, `actual_return`, `hit` FROM {table_ref()} "
        f"WHERE {where} ORDER BY `predict_date` DESC LIMIT {size}",
        tuple(params),
    )


def prediction_sql(
    symbol: str | None = None, predict_date: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.6：某一期预测详情；日期留空取最新预测日。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    date = validate_date(predict_date)
    if date:
        return (
            f"SELECT `predict_date`, `symbol`, `prob_up`, `pred_direction`, "
            f"`actual_direction`, `actual_return`, `hit` FROM {table_ref()} "
            f"WHERE {where} AND `predict_date` = %s",
            (*params, date),
        )
    return (
        f"SELECT `predict_date`, `symbol`, `prob_up`, `pred_direction`, "
        f"`actual_direction`, `actual_return`, `hit` FROM {table_ref()} "
        f"WHERE {where} ORDER BY `predict_date` DESC LIMIT 1",
        tuple(params),
    )


def pending_sql(
    symbol: str | None = None, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.7：未兑现（待验证）的预测（只有概率和方向，不返回实际列）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    size = bounded_limit(limit, DEFAULT_PENDING_LIMIT, MAX_PENDING_LIMIT)
    return (
        f"SELECT `predict_date`, `symbol`, `prob_up`, `pred_direction` "
        f"FROM {table_ref()} WHERE {where} AND `hit` IS NULL "
        f"ORDER BY `predict_date` DESC LIMIT {size}",
        tuple(params),
    )


def accuracy_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.8：整体命中率（分母只算已兑现期数，NULLIF 防除零）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    return (
        f"SELECT COUNT(*) AS total, SUM(`hit` IS NOT NULL) AS verified, "
        f"SUM(`hit` = 1) AS hits, "
        f"ROUND(SUM(`hit` = 1) / NULLIF(SUM(`hit` IS NOT NULL), 0), 4) AS hit_rate "
        f"FROM {table_ref()} WHERE {where}",
        tuple(params),
    )


def accuracy_by_year_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.9：按年命中率（AVG(hit) 天然跳过 null，分母即已兑现期数）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    return (
        f"SELECT YEAR(`predict_date`) AS yr, COUNT(*) AS total, "
        f"SUM(`hit` IS NOT NULL) AS verified, ROUND(AVG(`hit`), 4) AS hit_rate "
        f"FROM {table_ref()} WHERE {where} GROUP BY yr ORDER BY yr DESC",
        tuple(params),
    )


def calibration_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.10：概率分档命中率（判断概率有没有区分度）。

    分档边界 ``0.30 / 0.70`` 取月频页面「强势下跌 / 强势上涨」的分档线（固定口径）。
    ⚠️ 曲线**不是单调**的：两端（很强的看跌 / 看涨信号）都高于中间档，
    最不稳的是概率接近 0.5 的中间档。
    """
    where, params = _symbol_filter(validate_symbol(symbol))
    low, mid, high = CALIBRATION_EDGES
    return (
        f"SELECT CASE "
        f"WHEN `prob_up` < %s THEN '1 [0.00, 0.30)' "
        f"WHEN `prob_up` < %s THEN '2 [0.30, 0.50)' "
        f"WHEN `prob_up` < %s THEN '3 [0.50, 0.70)' "
        f"ELSE '4 [0.70, 1.00]' END AS prob_bucket, "
        f"COUNT(*) AS n, ROUND(AVG(`hit`), 4) AS hit_rate "
        f"FROM {table_ref()} WHERE {where} AND `hit` IS NOT NULL "
        f"GROUP BY prob_bucket ORDER BY prob_bucket",
        (low, mid, high, *params),
    )


def by_direction_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.11：多空分组命中率与占比（用标量子查询算占比，兼容 5.7/8.0）。"""
    where, params = _symbol_filter(validate_symbol(symbol))
    return (
        f"SELECT `pred_direction`, COUNT(*) AS n, "
        f"ROUND(COUNT(*) / (SELECT COUNT(*) FROM {table_ref()} "
        f"WHERE {where} AND `hit` IS NOT NULL), 4) AS share, "
        f"ROUND(SUM(`hit` = 1) / COUNT(*), 4) AS hit_rate "
        f"FROM {table_ref()} WHERE {where} AND `hit` IS NOT NULL "
        f"GROUP BY `pred_direction` ORDER BY `pred_direction` DESC",
        (*params, *params),
    )


def confidence_sql(
    symbol: str | None = None, hi: float | None = None, lo: float | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.12：高置信度子集命中率（阈值敏感性）。

    阈值是**口径不是结论**（就是强弱信号的分档线），反复调阈值刷同一批历史数据
    会过拟合——回答时要说清这是事后统计，不是实盘表现。
    """
    where, params = _symbol_filter(validate_symbol(symbol))
    high, low = confidence_thresholds(hi, lo)
    label_high = f"prob_up >= {high:g}"
    label_low = f"prob_up <= {low:g}"
    return (
        f"SELECT CASE "
        f"WHEN `prob_up` >= %s THEN %s "
        f"WHEN `prob_up` <= %s THEN %s "
        f"ELSE '其它' END AS subset, "
        f"COUNT(*) AS n, ROUND(SUM(`hit` = 1) / COUNT(*), 4) AS hit_rate "
        f"FROM {table_ref()} WHERE {where} AND `hit` IS NOT NULL "
        f"GROUP BY subset ORDER BY subset",
        (high, label_high, low, label_low, *params),
    )


def rolling_accuracy_sql(
    symbol: str | None = None,
    window: int | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """1.13：滚动 N 期命中率（窗口函数，需 MySQL 8.0+）。

    首 N-1 期只有部分前置数据，结果偏抖动。
    窗口大小改了就换了口径：50 期约等于 2.4 个月（每期是 22 个交易日的预测，
    但**每期一行**）。
    """
    where, params = _symbol_filter(validate_symbol(symbol))
    size = bounded_window(window)
    rows = bounded_limit(limit, DEFAULT_ROLLING_LIMIT, MAX_LIMIT)
    return (
        f"SELECT `predict_date`, `hit`, "
        f"ROUND(AVG(`hit`) OVER (ORDER BY `predict_date` "
        f"ROWS BETWEEN {size - 1} PRECEDING AND CURRENT ROW), 4) AS hit_rate "
        f"FROM {table_ref()} WHERE {where} AND `hit` IS NOT NULL "
        f"ORDER BY `predict_date` DESC LIMIT {rows}",
        tuple(params),
    )


def streak_sql(
    symbol: str | None = None, limit: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.14：连续命中 / 连续失误区间（窗口函数，需 MySQL 8.0+）。

    ⚠️ 连击长**不代表模型变强**：月频样本高度重叠（相邻两期预测窗口重合 21 个
    交易日），"连续命中 60 期"约等于"同一波行情判断对了 3 个月"，不是 60 个独立事件。
    """
    where, params = _symbol_filter(validate_symbol(symbol))
    rows = bounded_limit(limit, DEFAULT_STREAK_LIMIT, MAX_STREAK_LIMIT)
    return (
        f"SELECT `hit`, MIN(`predict_date`) AS run_start, "
        f"MAX(`predict_date`) AS run_end, COUNT(*) AS run_len FROM ("
        f"SELECT `predict_date`, `hit`, "
        f"ROW_NUMBER() OVER (ORDER BY `predict_date`) "
        f"- ROW_NUMBER() OVER (PARTITION BY `hit` ORDER BY `predict_date`) AS grp "
        f"FROM {table_ref()} WHERE {where} AND `hit` IS NOT NULL) t "
        f"GROUP BY `hit`, grp ORDER BY run_len DESC, run_end DESC LIMIT {rows}",
        tuple(params),
    )


def return_buckets_sql(symbol: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.15：实际收益分档命中率（分档阈值是**对数收益率**口径，±5%）。

    两端的命中率明显高于"小跌"档——趋势行情里模型更可靠，窄幅震荡里最容易判错方向。
    """
    where, params = _symbol_filter(validate_symbol(symbol))
    down, flat, up = RETURN_BUCKET_EDGES
    return (
        f"SELECT CASE "
        f"WHEN `actual_return` < %s THEN '1 跌超 5%' "
        f"WHEN `actual_return` < %s THEN '2 小跌' "
        f"WHEN `actual_return` < %s THEN '3 小涨' "
        f"ELSE '4 涨超 5%' END AS bucket, "
        f"COUNT(*) AS n, ROUND(AVG(`hit`), 4) AS hit_rate "
        f"FROM {table_ref()} WHERE {where} AND `hit` IS NOT NULL "
        f"GROUP BY bucket ORDER BY bucket",
        (down, flat, up, *params),
    )
