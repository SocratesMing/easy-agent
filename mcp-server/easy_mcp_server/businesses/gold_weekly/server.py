"""黄金周频预测 MCP 工具（单表宽表，只读，含事后验证）。

挂载路径：``/mcp/gold_weekly/``

工具划分与 ``docs/MCP_docs/黄金周频预测_SQL清单_MCP信息.md`` 第 2.2 节一致。
返回契约：**无数据不是错误**——单期工具给 ``found=false`` / ``data=null``，
列表与统计给空 ``items`` / 零计数；只有参数非法才返回 ``ok=false``。

工具层固化三件事（不交给调用方决定）：

1. 所有命中率统计强制 ``hit IS NOT NULL``——未兑现的 ``NULL`` 不是"未命中"；
2. 命中只认 ``hit = 1``，不用 ``hit = pred_direction``（会让看跌组虚高）；
3. 最新一期按 ``MAX(predict_date)``，不看 ``updated_at``。

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
from . import gold_weekly

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Gold (XAUUSD) weekly direction-forecast assistant, read-only over already-saved "
    "predictions and their realised outcomes. Call describe_gold_weekly_schema before "
    "querying. prob_up is a PROBABILITY ratio (0.583002 means 58.30%), NOT an expected "
    "return. pred_direction and actual_direction use 1 = up, 0 = down. actual_return is "
    "a T+5 LOG return (-0.018953 means -1.90%), not a simple return. hit is 1 hit / 0 "
    "miss / NULL not-yet-realised - NULL is NOT a miss, and every accuracy figure here "
    "is computed on realised rows only, so never treat the recent pending weeks as "
    "failures. Answering 'is the model accurate?' must state both the observation "
    "window and the sample size: the full-sample hit rate is about 0.59 while yearly "
    "rates range 0.53-0.67, so a handful of recent periods proves nothing. Thresholds "
    "used by the confidence tool are chosen after the fact, not live performance. "
    "Prediction records start at 2017-10-10 (the model needs a training window), so "
    "dates before that are simply not part of this table."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景唯一一张表）。"""
    return policy_from_env(
        "GOLD_WEEKLY_", default_allowed=gold_weekly.default_allowed_tables()
    )


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


def _base() -> dict[str, Any]:
    return {
        "model_name": gold_weekly.MODEL_NAME,
        "units": dict(gold_weekly.UNITS),
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
        logger.warning(f"[gold_weekly] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[gold_weekly] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[gold_weekly] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def _stats_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """统计类查询：逐行转数值；空结果就是空列表，不编零值。"""
    return {**_base(), "count": len(rows), "items": [gold_weekly.stat_row(r) for r in rows]}


def _single_stat(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """单行统计（如整体命中率、覆盖区间）；无行返回 data=null。"""
    if not rows:
        return {**_base(), "found": False, "data": None}
    return {**_base(), "found": True, "data": gold_weekly.stat_row(rows[0])}


def _details(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **_base(),
        "count": len(rows),
        "items": [gold_weekly.detail_row(r) for r in rows],
    }


def _pending(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **_base(),
        "count": len(rows),
        "items": [gold_weekly.pending_row(r) for r in rows],
    }


def build() -> FastMCP:
    mcp = FastMCP(
        "黄金周频预测",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_gold_weekly_schema() -> dict[str, Any]:
        """Return the data dictionary for the gold weekly forecast model: field units,
        direction encoding, the null-means-pending rule and the accuracy pitfalls. Call
        this before any query. prob_up is a 0-1 probability ratio, actual_return is a
        T+5 log return, and hit = null means not yet realised rather than a miss.
        """
        username = current_username()
        logger.info(f"[gold_weekly] describe_gold_weekly_schema | 用户: {username}")
        return {"ok": True, **gold_weekly.schema_help()}

    # ── 日期 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_gold_forecast_latest(symbol: str | None = None) -> dict[str, Any]:
        """Latest saved weekly prediction (date, probability and direction). symbol
        defaults to XAUUSD. A missing table returns found=false rather than an error.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_latest",
            username,
            lambda: gold_weekly.prediction_sql(symbol, None),
            lambda rows: (
                {**_base(), "found": False, "data": None}
                if not rows
                else {**_base(), "found": True, "data": gold_weekly.detail_row(rows[0])}
            ),
        )

    @mcp.tool()
    async def get_gold_forecast_verified_date(symbol: str | None = None) -> dict[str, Any]:
        """Latest prediction date that HAS a realised outcome (hit is not null). This is
        normally EARLIER than the latest prediction date, because the newest weeks have
        not completed their 5-day horizon yet.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_verified_date",
            username,
            lambda: gold_weekly.latest_verified_date_sql(symbol),
            lambda rows: {**_base(), "verified_date": _date_value(rows)},
        )

    @mcp.tool()
    async def get_gold_forecast_prev_date(
        symbol: str | None = None, predict_date: str | None = None
    ) -> dict[str, Any]:
        """The previous available prediction date strictly before the given date (used
        for period-over-period comparisons). predict_date omitted means the latest
        prediction date; this is NOT the previous calendar day.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_prev_date",
            username,
            lambda: gold_weekly.previous_date_sql(symbol, predict_date),
            lambda rows: {**_base(), "previous_date": _date_value(rows)},
        )

    @mcp.tool()
    async def list_gold_forecast_dates(
        symbol: str | None = None, days: int | None = None
    ) -> dict[str, Any]:
        """Recent prediction dates in descending order (date-picker range).
        days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "list_gold_forecast_dates",
            username,
            lambda: gold_weekly.dates_sql(symbol, days),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "dates": [str(r["d"]) for r in rows if r.get("d") is not None],
            },
        )

    @mcp.tool()
    async def list_gold_forecast_history(
        symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Recent predictions in descending order with their realised outcome when
        available. limit: 1-200, default 30. The three outcome fields stay null for
        weeks that have not matured - null is not zero and is not a miss.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "list_gold_forecast_history",
            username,
            lambda: gold_weekly.history_sql(symbol, limit),
            _details,
        )

    @mcp.tool()
    async def get_gold_forecast(
        symbol: str | None = None, predict_date: str | None = None
    ) -> dict[str, Any]:
        """One prediction period in full, including its realised outcome. predict_date
        omitted means the latest prediction date; a date with no record returns
        found=false and does NOT fall back to another date.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast",
            username,
            lambda: gold_weekly.prediction_sql(symbol, predict_date),
            lambda rows: (
                {**_base(), "found": False, "data": None}
                if not rows
                else {**_base(), "found": True, "data": gold_weekly.detail_row(rows[0])}
            ),
        )

    @mcp.tool()
    async def list_gold_forecast_pending(
        symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Predictions not yet realised (hit is null) - what the model currently thinks
        about the coming weeks. limit: 1-50, default 10. These rows have no outcome yet,
        so they must never be counted as misses.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "list_gold_forecast_pending",
            username,
            lambda: gold_weekly.pending_sql(symbol, limit),
            _pending,
        )

    @mcp.tool()
    async def get_gold_forecast_coverage(symbol: str | None = None) -> dict[str, Any]:
        """Table coverage: total periods, realised periods, first/last prediction date
        and the last write time. Use this for data-freshness questions. Note the table
        starts at 2017-10-10 - earlier dates are outside the model's sample, not missing.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_coverage",
            username,
            lambda: gold_weekly.coverage_sql(symbol),
            _single_stat,
        )

    # ── 准确率与统计 ────────────────────────────────────────────────────

    @mcp.tool()
    async def get_gold_forecast_accuracy(symbol: str | None = None) -> dict[str, Any]:
        """Overall direction hit rate with sample sizes. Counts and the rate are computed
        on REALISED periods only, because the newest weeks have not completed their
        horizon. Always answer accuracy questions together with the sample size: the
        whole-sample rate is about 0.59 while yearly rates span 0.53-0.67.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_accuracy",
            username,
            lambda: gold_weekly.accuracy_sql(symbol),
            _single_stat,
        )

    @mcp.tool()
    async def get_gold_forecast_accuracy_by_year(symbol: str | None = None) -> dict[str, Any]:
        """Hit rate per calendar year - use it to show whether the model stayed stable
        over time. Realised periods only.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_accuracy_by_year",
            username,
            lambda: gold_weekly.accuracy_by_year_sql(symbol),
            _stats_rows,
        )

    @mcp.tool()
    async def get_gold_forecast_calibration(symbol: str | None = None) -> dict[str, Any]:
        """Hit rate per prob_up band ([0,0.50) / [0.50,0.55) / [0.55,0.60) / [0.60,1.00]),
        i.e. whether the model's probability has any discriminating power. A monotonically
        rising hit rate means prob_up can be used for ranking or thresholding. Realised
        periods only.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_calibration",
            username,
            lambda: gold_weekly.calibration_sql(symbol),
            _stats_rows,
        )

    @mcp.tool()
    async def get_gold_forecast_by_direction(symbol: str | None = None) -> dict[str, Any]:
        """Long/short split: how many periods the model predicted up versus down, each
        group's share, and each group's hit rate. Realised periods only.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_by_direction",
            username,
            lambda: gold_weekly.by_direction_sql(symbol),
            _stats_rows,
        )

    @mcp.tool()
    async def get_gold_forecast_confidence(
        symbol: str | None = None, hi: float | None = None, lo: float | None = None
    ) -> dict[str, Any]:
        """Hit rate restricted to high-confidence subsets: prob_up >= hi (default 0.60)
        and prob_up <= lo (default 0.40), plus everything else. hi in 0.5-1.0, lo in
        0.0-0.5, lo < hi. These thresholds were chosen after looking at the history, so
        present the result as an ex-post statistic, not live trading performance.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_confidence",
            username,
            lambda: gold_weekly.confidence_sql(symbol, hi, lo),
            _stats_rows,
        )

    @mcp.tool()
    async def get_gold_forecast_rolling_accuracy(
        symbol: str | None = None, window: int | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Rolling N-period hit rate over realised periods, most recent first - use it to
        spot a recent deterioration. window: 5-250, default 50 (about 2.5 months). The
        earliest rows only have partial preceding data, so they are noisier. Requires
        MySQL 8.0+.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_rolling_accuracy",
            username,
            lambda: gold_weekly.rolling_accuracy_sql(symbol, window, limit),
            _stats_rows,
        )

    @mcp.tool()
    async def get_gold_forecast_streak(
        symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Longest consecutive hit and miss runs (start date, end date, length), most
        recent/longest first. limit: 1-50, default 10. Requires MySQL 8.0+. Realised
        periods only, so an unfinished run at the table edge is not counted.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_streak",
            username,
            lambda: gold_weekly.streak_sql(symbol, limit),
            _stats_rows,
        )

    @mcp.tool()
    async def get_gold_forecast_return_buckets(symbol: str | None = None) -> dict[str, Any]:
        """Hit rate grouped by the realised T+5 log return (< -2%, small down, small up,
        > +2%) - shows in which market regimes the model does better. The bucket edges
        are LOG-return thresholds. Realised periods only.
        """
        username = current_username()
        return await _domain_query(
            "gold_weekly",
            "get_gold_forecast_return_buckets",
            username,
            lambda: gold_weekly.return_buckets_sql(symbol),
            _stats_rows,
        )

    return mcp
