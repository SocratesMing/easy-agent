"""1 年期国债模型 MCP 工具（单表，结果存在 JSON 字段里，只读）。

挂载路径：``/mcp/treasury_1y/``

工具划分与 ``docs/MCP_docs/1年期国债模型_SQL清单_MCP信息.md`` 第四章的映射建议一致。
返回契约：**无数据不是错误**——单日工具给 ``found=false`` / ``data=null``，
历史工具给空 ``items`` 与零计数；只有参数非法才返回 ``ok=false``。

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
from . import treasury_1y

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "1-year treasury yield model assistant, read-only over already-saved results. Call "
    "describe_treasury_1y_schema before querying. The whole daily result lives in one "
    "result_json field. yield_pct is ALREADY a percentage value (1.2147 means 1.2147%) - "
    "never multiply it by 100; probs, percentile and trade_threshold are 0-1 ratios. "
    "yield_change_2d_bp is in basis points and may be null - null does not mean zero. All "
    "signals describe the YIELD direction: 1 up, -1 down, 0 watch. A yield rise is bearish "
    "for bond prices, so never phrase signal=1 as a bond price gain. The latest row is the "
    "max signal_date, not the max updated_at. A missing date returns found=false rather "
    "than falling back to another day. History uses each row's own threshold, signals and "
    "advice - do not recompute them from the latest threshold. This table has no realised "
    "labels, hit rate, backtest AUC or factor list; do not claim to query them."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景唯一一张表）。"""
    return policy_from_env(
        "TREASURY_1Y_", default_allowed=treasury_1y.default_allowed_tables()
    )


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


def _base() -> dict[str, Any]:
    return {
        "model_name": treasury_1y.MODEL_NAME,
        "units": dict(treasury_1y.UNITS),
    }


def _single(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """单日完整结果：无记录返回 found=false，不造假默认值。"""
    if not rows:
        return {**_base(), "found": False, "data": None}
    row = rows[0]
    result = treasury_1y.decode_result(row.get("result_json"))
    return {
        **_base(),
        "found": True,
        "data": {
            "signal_date": str(row["signal_date"]),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "result_json": result,
            "display": treasury_1y.display_values(result),
        },
    }


def _summary_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """信号摘要：收益率、概率、保护与资金面/技术面方向。"""
    if not rows:
        return {**_base(), "found": False, "data": None}
    row = rows[0]
    result = treasury_1y.decode_result(row.get("result_json"))
    data = {"signal_date": str(row["signal_date"])}
    for field in treasury_1y.SUMMARY_FIELDS:
        data[field] = result.get(field)
    data["signal_blocked"] = treasury_1y.normalize_blocked(result.get("signal_blocked"))
    data["display"] = treasury_1y.display_values(result)
    return {**_base(), "found": True, "data": data}


def _explanation_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """已存的资金面与技术面解释、建议及来源（不生成新解释）。"""
    if not rows:
        return {**_base(), "found": False, "data": None}
    row = rows[0]
    result = treasury_1y.decode_result(row.get("result_json"))
    fields = (
        "strategy_id",
        "funding_signal",
        "funding_advice",
        "funding_explanation",
        "technical_signal",
        "technical_advice",
        "technical_explanation",
        "funding_source",
        "technical_source",
    )
    data: dict[str, Any] = {"signal_date": str(row["signal_date"])}
    for field in fields:
        data[field] = result.get(field)
    return {**_base(), "found": True, "data": data}


def _history_item(row: dict[str, Any]) -> dict[str, Any]:
    """历史列表的精简投影：只留摘要字段，去掉每期长解释与来源。"""
    result = treasury_1y.decode_result(row.get("result_json"))
    return {
        "signal_date": str(row["signal_date"]),
        "result_json": treasury_1y.summarize(result),
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
        logger.warning(f"[treasury_1y] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[treasury_1y] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[treasury_1y] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "1 年期国债模型",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_treasury_1y_schema() -> dict[str, Any]:
        """Return the data dictionary for the 1-year treasury yield model: storage layout
        (everything in result_json), field units, signal scale and pitfalls. Call this
        before any query. yield_pct is a percentage value already, probabilities are 0-1
        ratios, and all signals describe the yield direction.
        """
        username = current_username()
        logger.info(f"[treasury_1y] describe_treasury_1y_schema | 用户: {username}")
        return {"ok": True, **treasury_1y.schema_help()}

    # ── 日期 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_treasury_1y_data_range() -> dict[str, Any]:
        """First and last available result dates plus the total row count. An empty table
        still returns one row with zero count and null dates.
        """
        username = current_username()
        return await _domain_query(
            "treasury_1y",
            "get_treasury_1y_data_range",
            username,
            treasury_1y.range_sql,
            lambda rows: {"range": rows[0] if rows else None},
        )

    @mcp.tool()
    async def get_treasury_1y_previous_date(date: str) -> dict[str, Any]:
        """The latest saved result date strictly before the given date. This is NOT the
        previous calendar day nor necessarily the previous trading day. date is required.
        """
        username = current_username()
        return await _domain_query(
            "treasury_1y",
            "get_treasury_1y_previous_date",
            username,
            lambda: treasury_1y.previous_date_sql(date),
            lambda rows: {
                "previous_signal_date": (str(rows[0]["previous_signal_date"]) if rows and rows[0]["previous_signal_date"] else None)
            },
        )

    @mcp.tool()
    async def list_treasury_1y_dates(limit: int | None = None) -> dict[str, Any]:
        """Recent available result dates in descending order (date picker range).
        limit: 1-250, default 20.
        """
        username = current_username()
        return await _domain_query(
            "treasury_1y",
            "list_treasury_1y_dates",
            username,
            lambda: treasury_1y.dates_sql(limit),
            lambda rows: {"dates": [str(r["signal_date"]) for r in rows]},
        )

    # ── 结果 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_treasury_1y_result(date: str | None = None) -> dict[str, Any]:
        """Full saved result for the latest or a given reference date. date omitted means
        the latest signal_date; a date with no data returns found=false and does NOT fall
        back to another day. Returns the decoded result_json object with all fields,
        including any extension fields.
        """
        username = current_username()
        return await _domain_query(
            "treasury_1y",
            "get_treasury_1y_result",
            username,
            lambda: (
                treasury_1y.result_sql(date)
                if str(date or "").strip()
                else treasury_1y.latest_result_sql()
            ),
            _single,
        )

    @mcp.tool()
    async def list_treasury_1y_history(
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Most recent N saved results inside a closed date range (dates filtered first,
        then the last N taken). N counts records, not calendar days. limit: 1-250,
        default 30. Returns matched_count (all rows in range), returned_count and items;
        items carry summary fields only - per-day long explanations are omitted. Nulls
        stay null and historical advice is never recomputed.
        """
        username = current_username()
        try:
            count_sql, count_params = treasury_1y.history_count_sql(start_date, end_date)
            rows_sql, rows_params = treasury_1y.history_sql(start_date, end_date, limit)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            counted = await _query(count_sql, count_params)
            rows = await _query(rows_sql, rows_params)
        except Exception as e:
            logger.warning(f"[treasury_1y] list_treasury_1y_history 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = [_history_item(row) for row in rows]
        logger.info(
            f"[treasury_1y] list_treasury_1y_history | 用户: {username} | "
            f"范围内 {int(counted[0]['matched_count']) if counted else 0} 条，返回 {len(items)} 条"
        )
        return json_safe(
            {
                "ok": True,
                "matched_count": int(counted[0]["matched_count"]) if counted else 0,
                "returned_count": len(items),
                "items": items,
            }
        )

    @mcp.tool()
    async def get_treasury_1y_signal_summary(date: str | None = None) -> dict[str, Any]:
        """Yield, probabilities, protection state and funding/technical signal directions
        for the latest or a given date. date omitted means the latest; a missing date
        returns found=false. The signals are read as saved - do not override them by
        comparing prob_up_7d with trade_threshold.
        """
        username = current_username()
        return await _domain_query(
            "treasury_1y",
            "get_treasury_1y_signal_summary",
            username,
            lambda: (
                treasury_1y.result_sql(date)
                if str(date or "").strip()
                else treasury_1y.latest_result_sql()
            ),
            _summary_rows,
        )

    @mcp.tool()
    async def get_treasury_1y_explanation(date: str | None = None) -> dict[str, Any]:
        """Already-saved funding and technical explanations, advice and sources for the
        latest or a given date. Nothing is generated or fetched from upstream workbooks;
        a missing date returns found=false. The record's updated_at is not the time the
        explanation was written.
        """
        username = current_username()
        return await _domain_query(
            "treasury_1y",
            "get_treasury_1y_explanation",
            username,
            lambda: (
                treasury_1y.result_sql(date)
                if str(date or "").strip()
                else treasury_1y.latest_result_sql()
            ),
            _explanation_rows,
        )

    return mcp
