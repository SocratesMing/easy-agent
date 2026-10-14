"""黄金价格多因子归因 MCP 工具（单表宽表，只读）。

挂载路径：``/mcp/gold_analysis/``

工具按"用户会问的问题"划分，与
``docs/MCP_docs/黄金价格驱动因子分析_SQL清单_MCP信息.md`` 第二章的映射建议一致。

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
from . import gold_analysis

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Gold price multi-factor attribution assistant, read-only over one wide table. "
    "Call describe_gold_schema before querying. All values are DECIMALS in return terms "
    "(0.0185 means 1.85%), never percentage points and never 亿元. Each row is the "
    "CUMULATIVE contribution of that factor group over the most recent 30 trading days up "
    "to and including that date - there is no single-day contribution in this table, so the "
    "difference between adjacent rows is only a change in the cumulative value. Two models "
    "(多元线性回归 / 随机森林) each write one row per date; always pass model or the same "
    "date will return two rows and double the numbers. The six groups sum to the model's "
    "fitted cumulative return, which differs from the actual gold price move by a residual. "
    "Dates use YYYY-MM-DD and an omitted date means the latest trading day."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景唯一一张表）。"""
    return policy_from_env("GOLD_", default_allowed=gold_analysis.default_allowed_tables())


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


async def _latest(model: str | None) -> str | None:
    """取该模型的最新数据日期；查不到返回 None。"""
    sql, params = gold_analysis.latest_date_sql(model)
    rows = await _query(sql, params)
    return str(rows[0]["d"]) if rows and rows[0]["d"] is not None else None


async def _resolve_date(model: str | None, creation_time: str | None) -> str | None:
    """``creation_time`` 留空时回落到该模型最新交易日。"""
    if str(creation_time or "").strip():
        return creation_time
    return await _latest(model)


async def _resolve_or_error(
    model: str | None, creation_time: str | None
) -> tuple[str | None, dict[str, Any] | None]:
    """解析目标日期；参数非法或库里无数据时返回 (None, 可直接返回的错误结果)。

    日期解析发生在 ``_domain_query`` 之前，若不在这里兜住，``ValueError`` 会逃到
    MCP 层变成 ``ToolError``，与其它工具统一的 ``{"ok": false, "error": ...}`` 不一致。
    """
    try:
        target = await _resolve_date(model, creation_time)
    except ValueError as e:
        return None, {"ok": False, "error": str(e)}
    if target is None:
        return None, {"ok": False, "error": "表内没有任何归因记录"}
    return target, None


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
        logger.warning(f"[gold_analysis] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[gold_analysis] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[gold_analysis] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "gold_analysis",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_gold_schema() -> dict[str, Any]:
        """Return the gold attribution data dictionary: table, models, factor groups,
        units and pitfalls. Call this before any query. Values are decimals in return
        terms (0.0185 means 1.85%) and rows are cumulative 30-trading-day contributions.
        """
        username = current_username()
        logger.info(f"[gold_analysis] describe_gold_schema | 用户: {username}")
        return {"ok": True, **gold_analysis.schema_help()}

    # ── 日期 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_gold_latest_date(model: str | None = None) -> dict[str, Any]:
        """Latest available data date for a model (the two models may not be in sync).
        model: 多元线性回归 (default) or 随机森林.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "get_gold_latest_date",
            username,
            lambda: gold_analysis.latest_date_sql(model),
            lambda rows: {"latest_date": rows[0]["d"] if rows else None},
        )

    @mcp.tool()
    async def get_gold_prev_date(
        model: str | None = None, creation_time: str | None = None
    ) -> dict[str, Any]:
        """Previous available trading date before creation_time, for day-over-day
        comparison. creation_time omitted means the latest date.
        """
        username = current_username()
        target, error = await _resolve_or_error(model, creation_time)
        if error is not None:
            return error
        return await _domain_query(
            "gold",
            "get_gold_prev_date",
            username,
            lambda: gold_analysis.prev_date_sql(model, target),
            lambda rows: {"prev_date": rows[0]["d"] if rows else None},
        )

    @mcp.tool()
    async def list_gold_dates(
        model: str | None = None, days: int | None = None
    ) -> dict[str, Any]:
        """Recent trading dates in descending order (chart x-axis / date picker range).
        days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "list_gold_dates",
            username,
            lambda: gold_analysis.dates_sql(model, days),
            lambda rows: {"dates": [r["d"] for r in rows]},
        )

    # ── 归因结果 ────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_gold_attribution_history(
        model: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Recent attribution rows in descending date order (what happened lately).
        Every value is a decimal in return terms (0.0185 = 1.85%), cumulative over the
        last 30 trading days. limit: 1-50, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "list_gold_attribution_history",
            username,
            lambda: gold_analysis.history_sql(model, limit),
            lambda rows: {"records": rows},
        )

    @mcp.tool()
    async def get_gold_attribution(
        model: str | None = None, creation_time: str | None = None
    ) -> dict[str, Any]:
        """Cumulative contribution of each of the six factor groups on one date - the core
        query behind the contribution bar chart. Values are decimals in return terms
        (0.0185 means 1.85%); positive lifts gold, negative drags. creation_time omitted
        means the latest trading day.
        """
        username = current_username()
        target, error = await _resolve_or_error(model, creation_time)
        if error is not None:
            return error
        return await _domain_query(
            "gold",
            "get_gold_attribution",
            username,
            lambda: gold_analysis.attribution_sql(model, target),
            lambda rows: {"attribution": rows[0] if rows else None},
        )

    @mcp.tool()
    async def compare_gold_models(creation_time: str | None = None) -> dict[str, Any]:
        """Same-date comparison of the two models (多元线性回归 vs 随机森林); the gap
        between them is model uncertainty. creation_time omitted means the latest date.
        """
        username = current_username()
        target, error = await _resolve_or_error(None, creation_time)
        if error is not None:
            return error
        return await _domain_query(
            "gold",
            "compare_gold_models",
            username,
            lambda: gold_analysis.compare_models_sql(target),
            lambda rows: {"creation_time": target, "models": rows},
        )

    @mcp.tool()
    async def get_gold_group_trend(
        model: str | None = None, days: int | None = None
    ) -> dict[str, Any]:
        """Long-format contribution trend for all six groups over the last N trading days
        (one row per date per group), for stacked charts. days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "get_gold_group_trend",
            username,
            lambda: gold_analysis.group_trend_sql(model, days),
            lambda rows: {"points": rows},
        )

    @mcp.tool()
    async def get_gold_group_series(
        model: str | None = None, group: str = "", days: int | None = None
    ) -> dict[str, Any]:
        """Trend of a single factor group over the last N trading days, for a single-factor
        chart. group is required and limited to: comex_volume / fund_flow / usd_factor /
        treasury_factor / risk_sentiment / commodity_factor. days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "get_gold_group_series",
            username,
            lambda: gold_analysis.group_series_sql(model, group, days),
            lambda rows: {"group": group, "points": rows},
        )

    @mcp.tool()
    async def get_gold_model_coverage() -> dict[str, Any]:
        """Row count and covered date range per model, to check whether the two models are
        in sync and how long the history is.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "get_gold_model_coverage",
            username,
            gold_analysis.model_coverage_sql,
            lambda rows: {"models": rows},
        )

    @mcp.tool()
    async def get_gold_group_change(
        model: str | None = None, days: int | None = None
    ) -> dict[str, Any]:
        """Day-over-day change of each factor group (cumulative value minus the previous
        row's cumulative value). NOTE: this is NOT that day's single-day contribution.
        days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "get_gold_group_change",
            username,
            lambda: gold_analysis.group_change_sql(model, days),
            lambda rows: {"changes": rows},
        )

    @mcp.tool()
    async def get_gold_group_stats(
        model: str | None = None, days: int | None = None
    ) -> dict[str, Any]:
        """Min / max / average of each factor group over the last N trading days, used to
        tell whether today's value is high or low. days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "get_gold_group_stats",
            username,
            lambda: gold_analysis.group_stats_sql(model, days),
            lambda rows: {"stats": rows[0] if rows else None},
        )

    @mcp.tool()
    async def get_gold_total_contribution(
        model: str | None = None, days: int | None = None
    ) -> dict[str, Any]:
        """Sum of the six groups per date - the model's fitted cumulative return. It has a
        residual against the actual gold price move, so do NOT report it as the gold price
        change. days: 1-250, default 30.
        """
        username = current_username()
        return await _domain_query(
            "gold",
            "get_gold_total_contribution",
            username,
            lambda: gold_analysis.total_contribution_sql(model, days),
            lambda rows: {"series": rows},
        )

    return mcp
