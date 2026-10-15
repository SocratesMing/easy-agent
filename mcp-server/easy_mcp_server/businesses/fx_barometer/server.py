"""外汇晴雨指数 MCP 工具（单表，整行 JSON，只读）。

挂载路径：``/mcp/fx_barometer/``

工具划分与 ``docs/MCP_docs/外汇晴雨指数_SQL清单_MCP信息.md`` 第六节的工具清单一致
（另有 ``describe_fx_barometer_schema`` 提供口径）。返回契约：**某日无记录不是错误**
——单日工具给 ``found=false``，列表给空 ``items``；只有参数非法才返回 ``ok=false``。

工具层固化的口径（不交给调用方判断）：

1. **数据不是实时行情**：只报库里的覆盖范围，不宣称"已更新到今日"；
2. **某日无记录就返回空**：绝不拿相邻日期的预测顶替（dashboard 会核对末行日期）；
3. **准备期**（``forecast`` 为 JSON ``null``）返回 ``preparing=true`` 且节点为 ``null``，
   **不补造区间**；判定用 ``JSON_TYPE(...)='OBJECT'``，避免把准备期的 null 当成有预测；
4. **分位区间是名义区间**、``averageR2`` 不是概率、``annualMu`` 不是收益——
   这些只出现在说明里，工具不替用户解读成结论。

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
from . import fx_barometer

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "USDCNY FX barometer assistant, read-only over one table where each row is a "
    "trading day holding a full result JSON. Call describe_fx_barometer_schema before "
    "querying. This is NOT a live quote feed: status only reports the stored date range "
    "and the history JSON is a RECOMPUTED study, not the snapshot published back then - "
    "never present the latest row as today's rate. A date with no row returns empty; "
    "never substitute another day's forecast for it (the dashboard tool verifies that "
    "the last row really is the requested date). forecast is a JSON null during the "
    "preparation period, in which case there are no 3M/6M nodes - report empty and mark "
    "it as preparing, never invent intervals. Node order is fixed by an import gate: "
    "index 0 is 3M and index 1 is 6M. The inner 25-75 and outer 5-95 bands are NOMINAL "
    "quantile intervals without validated coverage - do not call them a verified 90% "
    "interval. model.averageR2 is a test score that may be negative and is NOT the "
    "probability the forecast is right; annualMu and annualSigma are annualised decimals "
    "and are not guaranteed returns. Unknown extra fields in result_json are kept as-is "
    "and must not be dropped. The date inside result_json should equal signal_date; say "
    "so if they differ."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景唯一一张表）。"""
    return policy_from_env(
        "FX_BAROMETER_", default_allowed=fx_barometer.default_allowed_tables()
    )


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


def _base() -> dict[str, Any]:
    return {
        "model_name": fx_barometer.MODEL_NAME,
        "symbol": fx_barometer.SYMBOL,
        "units": dict(fx_barometer.UNITS),
    }


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
        logger.warning(f"[fx_barometer] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[fx_barometer] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(
        f"[fx_barometer] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}"
    )
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "外汇晴雨指数",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_fx_barometer_schema() -> dict[str, Any]:
        """Return the data dictionary for the USDCNY FX barometer: the single table, the
        result JSON shape, the 3M/6M node contract, the units and the pitfalls. Call
        this before any query.
        """
        username = current_username()
        logger.info(f"[fx_barometer] describe_fx_barometer_schema | 用户: {username}")
        return {"ok": True, **fx_barometer.schema_help()}

    # ── 状态与日期 ──────────────────────────────────────────────────────

    @mcp.tool()
    async def fx_barometer_status() -> dict[str, Any]:
        """Stored coverage: row count and the first/latest signal date. This is NOT a
        freshness or liveness check - the data is not a live feed, and an unchanged
        latest date means no new recomputation has been imported, not that the market
        is closed.
        """
        username = current_username()
        return await _domain_query(
            "fx_barometer",
            "fx_barometer_status",
            username,
            fx_barometer.range_sql,
            lambda rows: {
                **_base(),
                **fx_barometer.status_payload(rows[0] if rows else {}),
            },
        )

    @mcp.tool()
    async def fx_barometer_dates() -> dict[str, Any]:
        """All selectable trading days, ascending, each flagged with whether a forecast
        exists. first_forecast_date is the first day that actually has one; earlier days
        are the preparation period. Very large tables are capped, and 'truncated' says
        so.
        """
        username = current_username()
        limit = fx_barometer.DEFAULT_DATES_LIMIT
        return await _domain_query(
            "fx_barometer",
            "fx_barometer_dates",
            username,
            lambda: fx_barometer.dates_sql(limit),
            lambda rows: {**_base(), **fx_barometer.dates_payload(rows, limit)},
        )

    # ── 单日结果 ────────────────────────────────────────────────────────

    @mcp.tool()
    async def fx_barometer_result(date: str | None = None) -> dict[str, Any]:
        """The complete stored result for one trading day; date omitted means the latest
        stored day. Returns the full parsed JSON plus convenience fields. A date with no
        row returns found=false - no neighbouring day is substituted. When preparing is
        true the forecast has not been produced yet (forecast is JSON null).
        """
        username = current_username()
        return await _domain_query(
            "fx_barometer",
            "fx_barometer_result",
            username,
            lambda: fx_barometer.result_sql(date),
            lambda rows: (
                {**_base(), "found": False, "data": None}
                if not rows
                else {**_base(), "found": True, **fx_barometer.result_payload(rows[0])}
            ),
        )

    # ── 区间分页历史 ────────────────────────────────────────────────────

    @mcp.tool()
    async def fx_barometer_history(
        start_date: str | None = None,
        end_date: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Newest-first paginated history over a CLOSED date range, returning the full
        result JSON per row. Dates may be omitted (open-ended). page starts at 1;
        page_size is 1-200, default 50. total is the row count for the same range, so
        the caller can page through everything. The range is never silently narrowed to
        the last month.
        """
        username = current_username()
        try:
            resolved_page = fx_barometer.validate_page(page)
            resolved_size = fx_barometer.validate_page_size(page_size)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            count_sql, count_params = fx_barometer.range_count_sql(start_date, end_date)
            rows_sql, rows_params = fx_barometer.history_sql(
                start_date, end_date, resolved_page, resolved_size
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            count_rows = await _query(count_sql, count_params)
            rows = await _query(rows_sql, rows_params)
        except Exception as e:
            logger.warning(f"[fx_barometer] fx_barometer_history 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        total = 0
        if count_rows and count_rows[0].get("total_count") is not None:
            try:
                total = int(count_rows[0]["total_count"])
            except (TypeError, ValueError):
                total = 0
        items = [fx_barometer.result_payload(row) for row in rows]
        logger.info(
            f"[fx_barometer] fx_barometer_history | 用户: {username} | "
            f"total={total} 本页={len(items)}"
        )
        return json_safe(
            {
                "ok": True,
                **_base(),
                "start_date": fx_barometer.validate_date(start_date, "start_date"),
                "end_date": fx_barometer.validate_date(end_date, "end_date"),
                "page": resolved_page,
                "page_size": resolved_size,
                "total": total,
                "count": len(items),
                "items": items,
            }
        )

    # ── 仪表盘 ──────────────────────────────────────────────────────────

    @mcp.tool()
    async def fx_barometer_dashboard(
        date: str | None = None, ohlc_limit: int | None = None
    ) -> dict[str, Any]:
        """The OHLC history up to and including the selected day, plus THAT day's own
        3M/6M forecast. date omitted means the latest stored day. The series never
        contains days after the selected one, and the forecast always comes from the
        selected row itself - never from a more recent run. If the requested date has no
        row, found=false is returned instead of silently falling back to the previous
        day. ohlc_limit (1-2000, default 250) trims the returned series for token
        economy and sets 'truncated'; note MA20 needs 19 extra earlier rows, so a
        trimmed series cannot be used to recompute historical averages - page
        fx_barometer_history for the full history.
        """
        username = current_username()
        try:
            size = fx_barometer.bounded_ohlc_limit(ohlc_limit)
            requested = fx_barometer.validate_date(date)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            sql, params = fx_barometer.dashboard_sql(date)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(
                f"[fx_barometer] fx_barometer_dashboard 失败 | 用户: {username} | {e}"
            )
            return {"ok": False, "error": str(e)}

        if not rows:
            return json_safe(
                {
                    "ok": True,
                    **_base(),
                    "found": False,
                    "date": requested,
                    "note": "库里没有该日及之前的记录",
                }
            )

        last = rows[-1]
        selected_date = (
            str(last["signal_date"]) if last.get("signal_date") is not None else None
        )
        # 请求了具体日期时，末行必须是那一天，否则如实说"没有该日"，不冒充
        if requested and selected_date != requested:
            logger.info(
                f"[fx_barometer] fx_barometer_dashboard | 用户: {username} | "
                f"请求 {requested}，库中最近为 {selected_date}"
            )
            return json_safe(
                {
                    "ok": True,
                    **_base(),
                    "found": False,
                    "date": requested,
                    "latest_available": selected_date,
                    "note": (
                        f"{requested} 没有记录；库里该日之前最近的交易日是 "
                        f"{selected_date}。不沿用它的预测顶替，请改传该日期或省略"
                    ),
                }
            )

        series_rows = rows[-size:]
        series = fx_barometer.ohlc_series(series_rows)
        selected = fx_barometer.result_payload(last)
        logger.info(
            f"[fx_barometer] fx_barometer_dashboard | 用户: {username} | "
            f"date={selected_date} | 历史={len(rows)} 返回={len(series)}"
        )
        return json_safe(
            {
                "ok": True,
                **_base(),
                "found": True,
                "date": selected_date,
                "total_rows": len(rows),
                "truncated": len(series) < len(rows),
                "selected": selected,
                "ohlc_series": series,
                "note": (
                    "OHLC 序列只到选定日；3M/6M 取自选定行自身。"
                    "序列被截断时不能用来重算历史均线（MA20 需额外前 19 条）"
                ),
            }
        )

    # ── 研判区间与模型 ──────────────────────────────────────────────────

    @mcp.tool()
    async def fx_barometer_intervals(date: str) -> dict[str, Any]:
        """The 3M/6M quantile nodes and the full model metadata for one day. date is
        REQUIRED - without a day there is no 'that day's study'. Node index 0 is 3M and
        index 1 is 6M. Bands are nominal quantiles, not a validated coverage. When the
        day is in the preparation period both nodes are null and preparing is true; no
        interval is fabricated. averageR2 is a test score (may be negative) and is NOT a
        probability, and annualMu is not a guaranteed return.
        """
        username = current_username()
        return await _domain_query(
            "fx_barometer",
            "fx_barometer_intervals",
            username,
            lambda: fx_barometer.intervals_sql(date),
            lambda rows: (
                {
                    **_base(),
                    "found": False,
                    "data": None,
                    "warning": "该日没有记录；不沿用其它日期的节点",
                }
                if not rows
                else {**_base(), "found": True, **fx_barometer.interval_payload(rows[0])}
            ),
        )

    return mcp
