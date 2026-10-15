"""G7 外汇研判模型 MCP 工具（单表，六币种 JSON，只读）。

挂载路径：``/mcp/g7_fx/``

工具划分与 ``docs/MCP_docs/G7外汇研判模型_SQL清单_MCP信息.md`` 第 4.2 节一致。
返回契约：**无数据不是错误**——单快照工具给 ``found=false`` / ``data=null``，
列表与统计给空 ``items`` / 零计数；只有参数非法才返回 ``ok=false``。

三条在工具层固化的口径：

1. ``horizon`` 为 5 / 10 时**直接返回空、不查库**（现有服务只有 22 有正式结果）；
2. 页面窗口 ``floor`` = 库内最新信号日向前**一个自然月**，用 Python 日历算；
3. ``records`` 在 **Python 层解析**并按 ``Symbol`` 分组——不用 ``JSON_TABLE``。

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
from . import g7_fx

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "G7 FX research model assistant, read-only over already-saved snapshots. Call "
    "describe_g7_fx_schema before querying. Each row is a (signal_date, horizon_days) "
    "snapshot whose result_json.records array holds six currency pairs - it is NOT six "
    "database rows. Only horizon 22 has official results today; 5 and 10 return empty "
    "without touching the database, so never substitute 22 for them. Identify pairs by "
    "Symbol, never by array position. prob_up is the probability that THIS pair's quote "
    "rises (0-1), not a hit rate and not a US-dollar probability; Close is the FX quote, "
    "not a percentage. EURUSD/GBPUSD/AUDUSD rising means those currencies strengthen "
    "against the dollar, while USDJPY/USDCAD/USDCHF rising means the dollar strengthens - "
    "never describe both as 'the dollar went up'. impact_pp is already in probability "
    "percentage points: do not multiply by 100 and do not sum it into a return "
    "contribution. The page history window is the latest signal date back one CALENDAR "
    "month (inclusive); a date outside it is not found, and the tool never falls back to "
    "another date. Missing keys and nulls stay null - they mean 'not saved', not zero. "
    "test_accuracy is an upstream backtest metric, not the realised outcome of any "
    "current prediction."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景唯一一张表）。"""
    return policy_from_env("G7_FX_", default_allowed=g7_fx.default_allowed_tables())


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


def _base() -> dict[str, Any]:
    return {"model_name": g7_fx.MODEL_NAME}


async def _latest_and_floor(horizon: int) -> tuple[str | None, str | None]:
    """取库内最新信号日与其自然月下界（floor 在 Python 侧算）。"""
    rows = await _query(*g7_fx.latest_and_floor_sql(horizon))
    latest = None
    if rows and rows[0].get("latest_date") is not None:
        latest = str(rows[0]["latest_date"])
    return latest, (g7_fx.month_floor(latest) if latest else None)


def _empty_scope(horizon: int) -> dict[str, Any]:
    """5 / 10 期限：直接给空，不查库。"""
    return {
        "ok": True,
        **_base(),
        "horizon_days": horizon,
        "empty_reason": (
            f"期限 {horizon} 暂无正式落库结果；当前仅有 "
            f"{', '.join(str(h) for h in g7_fx.OFFICIAL_HORIZONS)} 交易日的结果，"
            f"不借用其它期限的数据"
        ),
    }


def _snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    result = g7_fx.decode_result(row.get("result_json"))
    return {
        "signal_date": str(row["signal_date"]) if row.get("signal_date") else None,
        "horizon_days": int(row["horizon_days"]) if row.get("horizon_days") else None,
        "snapshot_kind": g7_fx.snapshot_kind(result),
        "schema_version": result.get("schema_version"),
        "pair_count": len(g7_fx.extract_records(result)),
        "records": g7_fx.ordered_pairs(result),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "extra": {
            key: value
            for key, value in result.items()
            if key not in ("records", "schema_version", "snapshot_kind")
        },
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
        logger.warning(f"[g7_fx] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[g7_fx] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[g7_fx] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def _not_found(horizon: int, signal_date: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        **_base(),
        "horizon_days": horizon,
        "found": False,
        "data": None,
    }
    if signal_date:
        out["signal_date"] = signal_date
    return out


def build() -> FastMCP:
    mcp = FastMCP(
        "G7外汇研判",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_g7_fx_schema() -> dict[str, Any]:
        """Return the data dictionary for the G7 FX research model: snapshot layout,
        per-pair field units, the six-symbol list, the calendar-month window rule and the
        pitfalls. Call this before any query. prob_up is a per-pair probability, Close is
        a quote, and impact_pp is already in percentage points.
        """
        username = current_username()
        logger.info(f"[g7_fx] describe_g7_fx_schema | 用户: {username}")
        return {"ok": True, **g7_fx.schema_help()}

    # ── 范围与日期 ──────────────────────────────────────────────────────

    @mcp.tool()
    async def get_g7_fx_data_range(horizon: int | None = None) -> dict[str, Any]:
        """Snapshot count plus first and latest signal date for one horizon.
        horizon: 5 / 10 / 22, default 22. An empty table still returns one row with a
        zero count and null dates. 5 and 10 have no official results and return empty.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)
        return await _domain_query(
            "g7_fx",
            "get_g7_fx_data_range",
            username,
            lambda: g7_fx.range_sql(resolved),
            lambda rows: {
                **_base(),
                "horizon_days": resolved,
                "range": rows[0] if rows else None,
            },
        )

    @mcp.tool()
    async def list_g7_fx_dates(horizon: int | None = None) -> dict[str, Any]:
        """Selectable signal dates inside the page window (latest signal date back one
        CALENDAR month, inclusive), in descending order. horizon default 22. The window is
        anchored to the newest row in the database, not to today's date.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)

        async def run() -> dict[str, Any]:
            latest, floor = await _latest_and_floor(resolved)
            if not latest:
                return {
                    **_base(),
                    "horizon_days": resolved,
                    "latest": None,
                    "floor": None,
                    "count": 0,
                    "dates": [],
                }
            rows = await _query(*g7_fx.dates_sql(resolved, floor))
            dates = [str(r["signal_date"]) for r in rows if r.get("signal_date")]
            return {
                **_base(),
                "horizon_days": resolved,
                "latest": latest,
                "floor": floor,
                "count": len(dates),
                "dates": dates,
                "window_rule": "最新信号日向前一个自然月（含下界）",
            }

        try:
            result = await run()
        except Exception as e:
            logger.warning(f"[g7_fx] list_g7_fx_dates 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[g7_fx] list_g7_fx_dates | 用户: {username} | 行数={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    @mcp.tool()
    async def get_g7_fx_previous_date(
        signal_date: str, horizon: int | None = None
    ) -> dict[str, Any]:
        """The nearest earlier selectable signal date inside the window (for 'previous
        research date'). signal_date is required. This is NOT the previous calendar day
        and not guaranteed to be the previous trading day.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
            date = g7_fx.validate_date(signal_date, "signal_date")
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)

        async def run() -> dict[str, Any]:
            _, floor = await _latest_and_floor(resolved)
            rows = await _query(*g7_fx.previous_date_sql(resolved, date, floor))
            value = rows[0].get("previous_date") if rows else None
            return {
                **_base(),
                "horizon_days": resolved,
                "signal_date": date,
                "floor": floor,
                "previous_date": str(value) if value is not None else None,
            }

        try:
            result = await run()
        except Exception as e:
            logger.warning(f"[g7_fx] get_g7_fx_previous_date 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[g7_fx] get_g7_fx_previous_date | 用户: {username}")
        return json_safe({"ok": True, **result})

    # ── 快照 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_g7_fx_snapshot(
        date: str | None = None, horizon: int | None = None
    ) -> dict[str, Any]:
        """Full six-pair snapshot for the latest or a given signal date. date omitted
        means the latest; a date with no record inside the window returns found=false and
        does NOT fall back to another date. horizon default 22 (5/10 return empty).
        Extension fields and nulls are preserved as saved.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
            wanted = g7_fx.validate_date(date, "date")
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)

        async def run() -> dict[str, Any]:
            if not wanted:
                rows = await _query(*g7_fx.latest_snapshot_sql(resolved))
            else:
                _, floor = await _latest_and_floor(resolved)
                rows = await _query(*g7_fx.snapshot_sql(resolved, wanted, floor))
            if not rows:
                return _not_found(resolved, wanted)
            return {
                **_base(),
                "horizon_days": resolved,
                "found": True,
                "data": _snapshot_row(rows[0]),
            }

        try:
            result = await run()
        except Exception as e:
            logger.warning(f"[g7_fx] get_g7_fx_snapshot 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[g7_fx] get_g7_fx_snapshot | 用户: {username} | "
            f"找到={result.get('found', False)}"
        )
        return json_safe({"ok": True, **result})

    @mcp.tool()
    async def get_g7_fx_pair_result(
        symbol: str, date: str | None = None, horizon: int | None = None
    ) -> dict[str, Any]:
        """One currency pair's result on the latest or a given date: quote, probability,
        saved signal and the stored backtest metrics. symbol is required and must be one
        of the six pairs. prob_up is a per-pair probability, Close is a quote, and metrics
        that were never saved come back as null rather than 0. A missing date returns
        found=false without falling back.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
            pair_symbol = g7_fx.validate_symbol(symbol)
            wanted = g7_fx.validate_date(date, "date")
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)

        async def run() -> dict[str, Any]:
            if not wanted:
                rows = await _query(*g7_fx.latest_snapshot_sql(resolved))
            else:
                _, floor = await _latest_and_floor(resolved)
                rows = await _query(*g7_fx.snapshot_sql(resolved, wanted, floor))
            if not rows:
                return _not_found(resolved, wanted)
            row = rows[0]
            signal_date = str(row["signal_date"])
            result = g7_fx.decode_result(row.get("result_json"))
            pair = g7_fx.find_pair(result, pair_symbol)
            if pair is None:
                return {
                    **_base(),
                    "horizon_days": resolved,
                    "signal_date": signal_date,
                    "symbol": pair_symbol,
                    "found": False,
                    "data": None,
                    "note": "该快照中没有这个货币对的记录（按 Symbol 匹配，不按数组顺序）",
                }
            return {
                **_base(),
                "horizon_days": resolved,
                "signal_date": signal_date,
                "snapshot_kind": g7_fx.snapshot_kind(result),
                "symbol": pair_symbol,
                "found": True,
                "data": g7_fx.pair_metrics(pair, signal_date, resolved),
            }

        try:
            result = await run()
        except Exception as e:
            logger.warning(f"[g7_fx] get_g7_fx_pair_result 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[g7_fx] get_g7_fx_pair_result | 用户: {username} | "
            f"symbol={symbol} | 找到={result.get('found', False)}"
        )
        return json_safe({"ok": True, **result})

    @mcp.tool()
    async def list_g7_fx_history(
        start_date: str | None = None,
        end_date: str | None = None,
        horizon: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Recent snapshots inside a closed date range, restricted to the page window,
        with per-pair probability and signal only (no long explanations or price arrays).
        limit: 1-100, default 20. N counts model snapshots, not price observations.
        Returns matched_count and returned_count; items are in descending order.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)

        async def run() -> dict[str, Any]:
            _, floor = await _latest_and_floor(resolved)
            count_sql, count_params = g7_fx.history_count_sql(
                resolved, floor, start_date, end_date
            )
            rows_sql, rows_params = g7_fx.history_sql(
                resolved, floor, start_date, end_date, limit
            )
            counted = await _query(count_sql, count_params)
            rows = await _query(rows_sql, rows_params)
            items = []
            for row in rows:
                signal_date = str(row["signal_date"])
                result = g7_fx.decode_result(row.get("result_json"))
                items.append(g7_fx.history_item(result, signal_date, resolved))
            return {
                **_base(),
                "horizon_days": resolved,
                "floor": floor,
                "matched_count": (
                    int(counted[0]["matched_count"]) if counted else 0
                ),
                "returned_count": len(items),
                "items": items,
            }

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[g7_fx] list_g7_fx_history 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[g7_fx] list_g7_fx_history | 用户: {username} | "
            f"返回 {result.get('returned_count', 0)} 条"
        )
        return json_safe({"ok": True, **result})

    # ── 快照内明细 ──────────────────────────────────────────────────────

    async def _pair_or_none(
        symbol: str, date: str | None, horizon: int | None
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """取指定/最新快照并按 Symbol 提取币种；返回 (pair, signal_date, error)。"""
        resolved = g7_fx.validate_horizon(horizon)
        pair_symbol = g7_fx.validate_symbol(symbol)
        wanted = g7_fx.validate_date(date, "date")
        if not g7_fx.has_official_result(resolved):
            return None, None, "empty_scope"

        if not wanted:
            rows = await _query(*g7_fx.latest_snapshot_sql(resolved))
        else:
            _, floor = await _latest_and_floor(resolved)
            rows = await _query(*g7_fx.snapshot_sql(resolved, wanted, floor))
        if not rows:
            return None, wanted, "not_found"

        row = rows[0]
        signal_date = str(row["signal_date"])
        result = g7_fx.decode_result(row.get("result_json"))
        pair = g7_fx.find_pair(result, pair_symbol)
        if pair is None:
            return None, signal_date, "pair_missing"
        return pair, signal_date, None

    @mcp.tool()
    async def get_g7_fx_price_history(
        symbol: str,
        date: str | None = None,
        horizon: int | None = None,
        observations: int | None = None,
    ) -> dict[str, Any]:
        """Price observations saved INSIDE the chosen snapshot for one pair, ascending.
        observations: 30 or 60, default 30 - it counts price points, not calendar days,
        and is different from the N snapshots of list_g7_fx_history. Points missing or
        null return an empty list; later market data is never borrowed to fill history.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
            pair_symbol = g7_fx.validate_symbol(symbol)
            size = g7_fx.validate_observations(observations)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)

        try:
            pair, signal_date, reason = await _pair_or_none(symbol, date, horizon)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(
                f"[g7_fx] get_g7_fx_price_history 失败 | 用户: {username} | {e}"
            )
            return {"ok": False, "error": str(e)}

        if pair is None:
            return {
                **_not_found(resolved, signal_date),
                "symbol": pair_symbol,
                "note": reason,
            }

        points = g7_fx.pair_price_history(pair, size)
        logger.info(
            f"[g7_fx] get_g7_fx_price_history | 用户: {username} | "
            f"symbol={pair_symbol} | 点数={len(points)}"
        )
        return json_safe(
            {
                "ok": True,
                **_base(),
                "horizon_days": resolved,
                "signal_date": signal_date,
                "symbol": pair_symbol,
                "requested_observations": size,
                "point_count": len(points),
                "points": points,
                "note": (
                    "这些是该快照内已保存的行情观测，不是模型各期研判；"
                    "缺失或为空表示上游没存，不伪造"
                ),
            }
        )

    @mcp.tool()
    async def get_g7_fx_factor_explanation(
        symbol: str, date: str | None = None, horizon: int | None = None
    ) -> dict[str, Any]:
        """The saved factor explanation and numeric attributions for one pair, plus the
        maturity note, source markers and evaluation caveats. impact_pp is ALREADY in
        probability percentage points - never multiply by 100 and never sum the list into
        a return contribution. A missing explanation means nothing was saved for that
        date; the latest day's data is never copied over. Read-only; no model is invoked
        and no explanation is regenerated.
        """
        username = current_username()
        try:
            resolved = g7_fx.validate_horizon(horizon)
            pair_symbol = g7_fx.validate_symbol(symbol)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not g7_fx.has_official_result(resolved):
            return _empty_scope(resolved)

        try:
            pair, signal_date, reason = await _pair_or_none(symbol, date, horizon)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(
                f"[g7_fx] get_g7_fx_factor_explanation 失败 | 用户: {username} | {e}"
            )
            return {"ok": False, "error": str(e)}

        if pair is None:
            return {
                **_not_found(resolved, signal_date),
                "symbol": pair_symbol,
                "note": reason,
            }

        data = g7_fx.pair_explanation(pair, signal_date, resolved)
        logger.info(
            f"[g7_fx] get_g7_fx_factor_explanation | 用户: {username} | "
            f"symbol={pair_symbol} | 归因数={data.get('factor_impact_count', 0)}"
        )
        return json_safe(
            {
                "ok": True,
                **_base(),
                "horizon_days": resolved,
                "symbol": pair_symbol,
                "found": True,
                "data": data,
            }
        )

    return mcp
