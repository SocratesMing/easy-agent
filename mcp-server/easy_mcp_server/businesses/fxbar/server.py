"""外汇 K 线问数（fxbar）MCP 工具。

**数据性质**：外汇市场高频 K 线**大表**，公共行情，不做行级隔离
（与 strategyqa 的"只能看自己的策略"不同）。风险不在权限而在**性能**——
一次不设边界的查询就能拖垮数据库，所以：

- 所有工具都强制收敛到「合约 + 频率 + 有限时间窗 + LIMIT」
- **不暴露**任意 SQL 工具（`query`）：模型写出全表扫的代价不可控
- 时间窗缺省「最近 N 小时」，需要更早数据时显式传 `start_time` / `end_time`
  或 `days=N`；超过上限会被收敛并在返回里标记 `window.clamped`

并发：MCP SDK 对同步工具函数是**直接调用**、不会丢线程池，而数据库访问是阻塞的
pymysql 调用。因此凡涉及 IO 的工具都写成 ``async def`` 并把阻塞调用交给
``anyio.to_thread.run_sync``；纯计算的 ``describe_fx_schema`` 保持同步，避免占用线程池。
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from ...context import current_username
from ...querykit import json_safe, policy_from_env, readonly_query
from ...querykit import describe_table as kit_describe_table
from ...querykit import list_tables as kit_list_tables
from . import fxbar

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Foreign-exchange K-line (OHLC bar) data assistant, backed by a LARGE table. "
    "Every query MUST be scoped to one contract + one frequency + a bounded time window. "
    "Workflow: call describe_fx_schema first to learn the columns, enums and time format; "
    "call list_fx_instruments to discover which contracts/channels actually have data; "
    "then call get_fx_bars. "
    "By default only the most recent window (FX_DEFAULT_WINDOW_HOURS, usually 24h) is "
    "returned — this is intentional for performance. When the user asks for another period, "
    "pass start_time / end_time (UTC, 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS') or days=N. "
    "Timestamps are millisecond UTC integers; prices are FX rates, not percentage changes. "
    "If a result looks truncated, narrow the window instead of raising the limit."
)


def _policy():
    """本次请求的表可见性策略（读 FX_ALLOWED_TABLES / FX_DENY_TABLES）。"""
    return policy_from_env("FX_")


def build() -> FastMCP:
    mcp = FastMCP(
        "外汇 K 线",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 领域工具 ────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_fx_schema() -> dict[str, Any]:
        """Return the FX K-line data dictionary: columns, enums, time format and the
        window rules (default lookback, maximum span).

        Call this before any query so you use the right frequency / side / data_type
        values, and so you know that timestamps are millisecond UTC and that the table
        is large (always scope by contract_code + frequency).
        """
        # 纯计算，无 IO，保持同步以免占用线程池
        username = current_username()
        logger.info(f"[fxbar] describe_fx_schema | 用户: {username}")
        return {"ok": True, **fxbar.schema_help()}

    @mcp.tool()
    async def list_fx_instruments(
        channel: str | None = None,
        days: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List which FX contracts actually have data, with channel, bar count and the
        covered time range (newest/oldest).

        Use this when the user is unsure about the exact contract code (e.g. EURUSD vs
        EUR/USD) or asks "which pairs do we have". Only scans the recent `days`
        (default 7) — the full history would be too expensive on this table.

        Args:
            channel: BEST / FXALL / ... ; omit to use the configured defaults;
                pass "ALL" to ignore the channel filter.
            days: lookback window in days (default 7).
            limit: max number of contract/channel rows to return.
        """
        username = current_username()
        lookback = int(days) if days else 7
        try:
            sql, params = fxbar.instruments_sql(channel=channel, days=lookback, limit=limit)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await anyio.to_thread.run_sync(readonly_query, sql, params)
        except Exception as e:
            logger.warning(f"[fxbar] list_fx_instruments 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = fxbar.decorate(rows)
        logger.info(
            f"[fxbar] list_fx_instruments | 用户: {username} | "
            f"渠道: {channel or '默认'} | 行数: {len(items)}"
        )
        return {
            "ok": True,
            "lookback_days": lookback,
            "row_count": len(items),
            "summary": fxbar.summarize_instruments(items, lookback),
            "rows": items,
        }

    @mcp.tool()
    async def get_fx_bars(
        contract_code: str,
        frequency: str | None = None,
        days: float | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        channel: str | None = None,
        side: str | None = None,
        data_type: str | None = None,
        instrument: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Get FX K-line bars for ONE contract within a bounded time window.

        Without time arguments it returns the most recent window only
        (FX_DEFAULT_WINDOW_HOURS, usually 24h) — that is the intended default for this
        large table. To query another period, either:

        - pass `days=N` for "the last N days", or
        - pass `start_time` / `end_time` for an explicit range (UTC).

        Ranges longer than FX_MAX_WINDOW_DAYS (default 31) are automatically shortened
        from the end and reported as `window.clamped = true`.

        Args:
            contract_code: required, e.g. EURUSD / USDCNY.
            frequency: 1M / 5M / 15M / 1H / 4H / 1D (default from config, usually 1H).
            days: relative window in days (mutually exclusive with start/end_time).
            start_time: UTC, 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'.
            end_time: UTC, same format.
            channel: BEST / FXALL / ... ; omit for configured defaults, "ALL" for all.
            side: BID / ASK / MID.
            data_type: OHLC / TRADE / MID.
            instrument: SPOT / SWAP / FWD.
            limit: max bars to return (newest first).
        """
        username = current_username()
        try:
            freq = fxbar.normalize_frequency(frequency)
            window = fxbar.resolve_window(days=days, start_time=start_time, end_time=end_time)
            sql, params = fxbar.bars_sql(
                contract_code=contract_code,
                frequency=freq,
                window=window,
                channel=channel,
                side=side,
                data_type=data_type,
                instrument=instrument,
                limit=limit,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await anyio.to_thread.run_sync(readonly_query, sql, params)
        except Exception as e:
            logger.warning(f"[fxbar] get_fx_bars 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = fxbar.decorate(rows)
        effective_limit = fxbar.row_limit(limit)
        logger.info(
            f"[fxbar] get_fx_bars | 用户: {username} | {contract_code} {freq} | "
            f"窗: {window.to_dict()['start']} ~ {window.to_dict()['end']} | 行数: {len(items)}"
        )
        return {
            "ok": True,
            "contract_code": contract_code,
            "frequency": freq,
            "window": window.to_dict(),
            "row_count": len(items),
            "truncated": len(items) >= effective_limit,
            "summary": fxbar.summarize_bars(items, contract_code, freq, window),
            "rows": items,
        }

    @mcp.tool()
    async def get_latest_fx_quote(
        contract_code: str,
        frequency: str | None = None,
        channel: str | None = None,
        side: str | None = None,
        data_type: str | None = None,
        lookback_days: int | None = None,
    ) -> dict[str, Any]:
        """Get the LATEST single bar for one contract (e.g. "现在 EURUSD 多少").

        Internally still uses a lookback window (default 7 days) rather than an
        unbounded "order by time desc", because the primary key is a concatenated
        string and a full scan would be unacceptable on this table. If nothing is
        found, retry with a larger `lookback_days`.

        Args:
            contract_code: required, e.g. EURUSD.
            frequency: 1M / 5M / 15M / 1H / 4H / 1D.
            channel: BEST / FXALL / ... ; "ALL" to ignore the filter.
            side: BID / ASK / MID.
            data_type: OHLC / TRADE / MID.
            lookback_days: how far back to search (default 7).
        """
        username = current_username()
        try:
            freq = fxbar.normalize_frequency(frequency)
            sql, params = fxbar.latest_sql(
                contract_code=contract_code,
                frequency=freq,
                channel=channel,
                side=side,
                data_type=data_type,
                lookback_days=lookback_days,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await anyio.to_thread.run_sync(readonly_query, sql, params)
        except Exception as e:
            logger.warning(f"[fxbar] get_latest_fx_quote 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = fxbar.decorate(rows)
        if not items:
            logger.info(f"[fxbar] get_latest_fx_quote 未命中 | 用户: {username} | {contract_code}")
            return {
                "ok": False,
                "error": (
                    f"{contract_code} {freq} 在最近 {lookback_days or 7} 天内没有数据。"
                    "可尝试：加大 lookback_days、换 frequency，"
                    "或用 list_fx_instruments 确认该合约是否有数据。"
                ),
            }

        logger.info(f"[fxbar] get_latest_fx_quote | 用户: {username} | {contract_code} {freq}")
        return {"ok": True, "contract_code": contract_code, "frequency": freq, "quote": items[0]}

    # ── 结构探索（只读 information_schema，不返回业务数据） ─────────────

    @mcp.tool()
    async def list_tables() -> list[dict[str, Any]]:
        """List queryable tables/views of the FX data source with Chinese comments.

        Only schema metadata is returned; use list_fx_instruments for actual data.
        """
        username = current_username()
        logger.info(f"[fxbar] list_tables | 用户: {username}")
        return await anyio.to_thread.run_sync(kit_list_tables, _policy())

    @mcp.tool()
    async def describe_table(table: str) -> dict[str, Any]:
        """Return the column dictionary of one table/view (schema only, no rows)."""
        username = current_username()
        try:
            info = await anyio.to_thread.run_sync(kit_describe_table, table, _policy())
        except ValueError as e:
            logger.warning(f"[fxbar] describe_table | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[fxbar] describe_table | 用户: {username} | 表: {info['table']}")
        return {"ok": True, **json_safe(info)}

    return mcp
