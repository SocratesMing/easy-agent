"""黄金月频预测 MCP 工具（单表宽表，只读，含事后验证）。

挂载路径：``/mcp/gold_monthly/``

工具划分与 ``docs/MCP_docs/黄金月频预测_SQL清单_MCP信息.md`` 第 2.2 节一致。
返回契约：**无数据不是错误**——单期工具给 ``found=false`` / ``data=null``，
列表与统计给空 ``items`` / 零计数；只有参数非法才返回 ``ok=false``。

工具层固化三件事（不交给调用方决定）：

1. 所有命中率统计强制 ``hit IS NOT NULL``——未兑现的 ``NULL`` 不是"未命中"；
2. 命中只认 ``hit = 1``，不用 ``hit = pred_direction``（会让看跌组虚高）；
3. 最新一期按 ``MAX(predict_date)``，不看 ``updated_at``；
   「上一期」按表里实际存在的预测日回溯，**不用 ``DATE_SUB`` 推算**
   （交易日相邻会跨周末、假期、月份）。

并发：MCP SDK 对同步工具函数是直接调用、不丢线程池，而 pymysql 是阻塞调用，
因此涉 IO 的工具都写成 ``async def`` + ``anyio.to_thread.run_sync``。
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from ...context import current_username
from ...querykit import json_safe, policy_from_env, readonly_query
from . import gold_monthly

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Gold (XAUUSD) monthly direction-forecast assistant, read-only over already-saved "
    "predictions and their realised outcomes. Call describe_gold_monthly_schema before "
    "querying. The horizon is T+22 trading days (monthly), NOT the weekly model's T+5. "
    "prob_up is a PROBABILITY ratio (0.229329 means 22.93%), NOT an expected return. "
    "pred_direction and actual_direction use 1 = up, 0 = down. actual_return is a T+22 "
    "LOG return (-0.086108 means -8.61%), not a simple return. hit is 1 hit / 0 miss / "
    "NULL not-yet-realised - NULL is NOT a miss, and every accuracy figure here is "
    "computed on realised rows only, so never treat the ~22 pending periods as "
    "failures. Answering 'is the model accurate?' must state BOTH the observation "
    "window and the sample size: the full-sample hit rate is about 0.65 while yearly "
    "rates range 0.57-0.73 and the last 30 periods are only about 0.47, so a handful of "
    "recent periods proves nothing. Adjacent periods overlap by 21 trading days, so a "
    "long streak is roughly one market call held for a few months, not many independent "
    "events. The probability buckets are NOT monotonic: both extreme buckets beat the "
    "middle, and values near 0.5 are least reliable. Confidence thresholds are chosen "
    "after the fact, not live performance. Prediction records start at 2020-02-20 (the "
    "model needs a training window), so earlier dates are simply not in this table."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景唯一一张表）。"""
    return policy_from_env(
        "GOLD_MONTHLY_", default_allowed=gold_monthly.default_allowed_tables()
    )


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


def _base() -> dict[str, Any]:
    return {
        "model_name": gold_monthly.MODEL_NAME,
        "units": dict(gold_monthly.UNITS),
    }


def _date_value(rows: list[dict[str, Any]]) -> str | None:
    """取 ``d`` 列的日期字符串（无行或 NULL 都返回 None）。"""
    if not rows:
        return None
    value = rows[0].get("d")
    return str(value) if value is not None else None


async def _domain_query(
    domain: str,
    tool: str,
    username: str,
    build_query,
    format_rows,
) -> dict[str, Any]:
    """Run a fixed parameterized domain query and return a consistent MCP result."""
    try:
        sql, params = build_query()
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    try:
        rows = await _query(sql, params)
    except Exception as e:
        logger.warning(f"[gold_monthly] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[gold_monthly] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[gold_monthly] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "黄金月频预测",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_gold_monthly_schema() -> dict[str, Any]:
        """Return the data dictionary for monthly gold forecasts: the single table, the
        T+22 horizon, the units (probability ratio, log return), and the pitfalls. Call
        this before any query.
        """
        username = current_username()
        logger.info(f"[gold_monthly] describe_gold_monthly_schema | 用户: {username}")
        return {"ok": True, **gold_monthly.schema_help()}

    # ── 单期 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_gold_monthly_latest(symbol: str | None = None) -> dict[str, Any]:
        """The newest forecast: date, up-probability and direction. Note the newest
        period is usually NOT yet realised (the horizon is 22 trading days), so the
        actual columns are typically null - use get_gold_monthly_verified_date for the
        newest period that actually has an outcome.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_latest",
            username,
            lambda: gold_monthly.prediction_sql(symbol),
            lambda rows: (
                {**_base(), "found": False, "data": None}
                if not rows
                else {**_base(), "found": True, "data": gold_monthly.detail_row(rows[0])}
            ),
        )

    @mcp.tool()
    async def get_gold_monthly_verified_date(symbol: str | None = None) -> dict[str, Any]:
        """The newest forecast date that already has an actual outcome. This is the
        right anchor for 'recent actual performance' questions - it is typically about
        one month EARLIER than the newest forecast date, because the horizon is 22
        trading days.
        """
        username = current_username()
        try:
            resolved = gold_monthly.validate_symbol(symbol)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_verified_date",
            username,
            lambda: gold_monthly.latest_verified_date_sql(resolved),
            lambda rows: {
                **_base(),
                "symbol": resolved,
                "predict_date": _date_value(rows),
                "note": "最新已兑现预测日（通常早于最新预测日约 22 个交易日）",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_prev_date(
        symbol: str | None = None, predict_date: str | None = None
    ) -> dict[str, Any]:
        """The nearest earlier forecast date stored in the table, for period-over-period
        comparisons. predict_date omitted means relative to the newest forecast date.
        This is NOT calendar yesterday: forecast dates are adjacent TRADING days and
        skip weekends, holidays and month boundaries.
        """
        username = current_username()
        try:
            resolved = gold_monthly.validate_symbol(symbol)
            base = gold_monthly.validate_date(predict_date) or "最新预测日"
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_prev_date",
            username,
            lambda: gold_monthly.previous_date_sql(resolved, predict_date),
            lambda rows: {
                **_base(),
                "symbol": resolved,
                "base_date": base,
                "prev_date": _date_value(rows),
            },
        )

    @mcp.tool()
    async def get_gold_monthly(
        symbol: str | None = None, predict_date: str | None = None
    ) -> dict[str, Any]:
        """Full detail for one forecast date: up-probability, predicted direction, and -
        once realised - actual direction, actual return and whether it hit. predict_date
        omitted means the newest forecast date. Null actual columns mean not yet
        realised, which is normal for recent dates - never read them as zero or as a
        miss. prob_up is a 0-1 probability and actual_return is a T+22 log return; both
        are also given as percentages under 'display' for convenience.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly",
            username,
            lambda: gold_monthly.prediction_sql(symbol, predict_date),
            lambda rows: (
                {**_base(), "found": False, "data": None}
                if not rows
                else {**_base(), "found": True, "data": gold_monthly.detail_row(rows[0])}
            ),
        )

    # ── 列表 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_gold_monthly_dates(
        symbol: str | None = None, days: int | None = None
    ) -> dict[str, Any]:
        """The most recent forecast dates, newest first - the date axis for trend charts
        and the selectable range for a date picker. days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "list_gold_monthly_dates",
            username,
            lambda: gold_monthly.dates_sql(symbol, days),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "dates": [_date_value([row]) for row in rows],
            },
        )

    @mcp.tool()
    async def list_gold_monthly_history(
        symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Recent forecast detail rows, newest first, including not-yet-realised periods
        (their actual columns are null, shown as 'pending' under 'display'). limit:
        1-200, default 30. Nulls are preserved, never flattened to zero.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "list_gold_monthly_history",
            username,
            lambda: gold_monthly.history_sql(symbol, limit),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.detail_row(row) for row in rows],
            },
        )

    @mcp.tool()
    async def list_gold_monthly_pending(
        symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Forecasts awaiting an outcome: fewer than 22 trading days have passed, so only
        the probability and direction exist. limit: 1-50, default 10. These are pending,
        NOT failures - exclude them from any accuracy claim.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "list_gold_monthly_pending",
            username,
            lambda: gold_monthly.pending_sql(symbol, limit),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.pending_row(row) for row in rows],
                "note": "未兑现（待验证）：只有概率与方向，不是未命中",
            },
        )

    # ── 命中率统计（全部只在已兑现期数上计算）──────────────────────────

    @mcp.tool()
    async def get_gold_monthly_accuracy(symbol: str | None = None) -> dict[str, Any]:
        """Overall direction hit rate with sample sizes: total periods, realised
        periods, hits and the hit rate. The rate is computed on realised periods only,
        because the most recent ~22 periods have no outcome yet. Answers
        'is this model accurate?' - always quote the observation window and sample size:
        the full sample is about 0.65, yearly rates span 0.57-0.73, and the last 30
        periods are only about 0.47.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_accuracy",
            username,
            lambda: gold_monthly.accuracy_sql(symbol),
            lambda rows: {
                **_base(),
                "data": gold_monthly.stat_row(rows[0]) if rows else None,
                "note": "命中率分母为已兑现期数（hit IS NOT NULL），未兑现期不计入",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_accuracy_by_year(symbol: str | None = None) -> dict[str, Any]:
        """Hit rate by calendar year, to judge stability or whether the model decayed in
        some period. Each row gives total periods, realised periods and the hit rate
        (realised periods only). Year-to-year variation is large, so do not read a single
        year as the model's quality.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_accuracy_by_year",
            username,
            lambda: gold_monthly.accuracy_by_year_sql(symbol),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.stat_row(row) for row in rows],
            },
        )

    @mcp.tool()
    async def get_gold_monthly_calibration(symbol: str | None = None) -> dict[str, Any]:
        """Hit rate by up-probability bucket, to judge whether the probability is usable.
        Buckets: [0.00,0.30), [0.30,0.50), [0.50,0.70), [0.70,1.00]. The curve is NOT
        monotonic - both extreme buckets beat the middle, and values near 0.5 are the
        least reliable, so the value of the probability lies in the high-confidence ends.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_calibration",
            username,
            lambda: gold_monthly.calibration_sql(symbol),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.stat_row(row) for row in rows],
                "note": "分档线 0.30 / 0.70 是固定口径；曲线非单调，越靠近 0.5 越不稳",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_by_direction(symbol: str | None = None) -> dict[str, Any]:
        """Bull vs bear breakdown: how often the model called each direction, and each
        direction's own hit rate. Shows the model's bias (it leans bullish) and that the
        two directions do not have the same accuracy. Hits are counted only as hit = 1.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_by_direction",
            username,
            lambda: gold_monthly.by_direction_sql(symbol),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.stat_row(row) for row in rows],
                "note": "pred_direction 1 = 看涨 / 0 = 看跌",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_confidence(
        symbol: str | None = None,
        hi: float | None = None,
        lo: float | None = None,
    ) -> dict[str, Any]:
        """Hit rate for high-confidence subsets: periods with prob_up >= hi (bullish
        threshold) or <= lo (bearish threshold), versus everything else. hi: 0.5-1,
        default 0.70; lo: 0-0.5, default 0.30. Thresholds are a presentation choice, not
        a conclusion - sweeping them over the same history overfits, so state that this
        is an after-the-fact statistic rather than live performance.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_confidence",
            username,
            lambda: gold_monthly.confidence_sql(symbol, hi, lo),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.stat_row(row) for row in rows],
                "note": "阈值是事后统计口径，不是实盘表现；反复调阈值会过拟合",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_rolling_accuracy(
        symbol: str | None = None,
        window: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Rolling hit rate over the last N periods, for judging the model's recent
        state. window: 5-250, default 50 (about 2.4 months, since each row is one
        period); limit: 1-200, default 30. Requires MySQL 8.0+. The first N-1 rows have
        only partial history and look noisy - do not draw conclusions from them.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_rolling_accuracy",
            username,
            lambda: gold_monthly.rolling_accuracy_sql(symbol, window, limit),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.stat_row(row) for row in rows],
                "note": "窗口期数含当期；开头几期前置数据不足，结果偏抖动",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_streak(
        symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Longest runs of consecutive hits and consecutive misses, for 'has it been
        wrong lately?'. limit: 1-50, default 10. Requires MySQL 8.0+. A long run does
        NOT mean the model improved: adjacent periods overlap by 21 trading days, so a
        60-period hit run is roughly one market call held for about three months, not 60
        independent events.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_streak",
            username,
            lambda: gold_monthly.streak_sql(symbol, limit),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.stat_row(row) for row in rows],
                "note": "样本高度重叠：连击长不等于模型变强",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_return_buckets(symbol: str | None = None) -> dict[str, Any]:
        """Hit rate bucketed by the realised move: down more than 5%, small down, small
        up, up more than 5% (log-return thresholds; monthly is 22 trading days of
        cumulative move). Shows what kind of market the model handles: it is much better
        in trending markets and worst in narrow ranges.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_return_buckets",
            username,
            lambda: gold_monthly.return_buckets_sql(symbol),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [gold_monthly.stat_row(row) for row in rows],
                "note": "分档阈值 ±5% 是对数收益率口径（22 个交易日累计波动）",
            },
        )

    @mcp.tool()
    async def get_gold_monthly_coverage(symbol: str | None = None) -> dict[str, Any]:
        """Data coverage and freshness: total periods, realised periods, first and last
        forecast date, and the last write time. Use MAX(predict_date) (last_date) to
        judge freshness, NOT updated_at - a row's updated_at is not refreshed when values
        are unchanged. Records start at 2020-02-20 because the model needs a training
        window first; earlier dates are not missing data.
        """
        username = current_username()
        return await _domain_query(
            "gold_monthly",
            "get_gold_monthly_coverage",
            username,
            lambda: gold_monthly.coverage_sql(symbol),
            lambda rows: {
                **_base(),
                "data": gold_monthly.stat_row(rows[0]) if rows else None,
                "note": "判断新鲜度看 last_date，不看 last_written（值不变时不刷新）",
            },
        )

    return mcp
