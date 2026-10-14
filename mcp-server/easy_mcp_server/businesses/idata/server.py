"""iData 数据分析看板 MCP 工具（机构流向 / 个券机构 / 债券借贷 / 大模型点评）

挂载路径：``/mcp/idata/``

MCP 工具按"用户会问的问题"切分，而非照搬原始 SQL。设计说明见
``docs/MCP_docs/`` 下对应的 SQL 清单。

并发：MCP SDK 对同步工具函数是直接调用、不丢线程池，而 pymysql 是阻塞调用，
因此涉 IO 的工具都写成 ``async def`` + ``anyio.to_thread.run_sync``。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

import anyio
from mcp.server.fastmcp import FastMCP

from ...context import current_username
from ...querykit import json_safe, policy_from_env, readonly_query
from . import idata

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "iData bond analytics assistant. Call describe_idata_schema before querying. Amounts are "
    "ALWAYS in 亿元 (100M CNY) with an _yi suffix; concentration and ratios are percentages "
    "(24.53 means 24.53%). Dates use YYYYMMDD and an omitted date means the latest trading "
    "day. Use get_institution_flow for institution matrices/trends by varying group_by, "
    "get_bond_counterparts for bond counterparties, and get_lending_snapshot for lending "
    "balances. The institution table mixes total rows with detail rows; the tools already "
    "isolate them via include_total."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景涉及的固定几张表）。"""
    return policy_from_env("IDATA_", default_allowed=idata.default_allowed_tables())


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)



async def _latest_date(source: str) -> str | None:
    """取某表的最新交易日；查不到返回 None。"""
    sql, params = idata.trade_dates_sql(source=source, limit=1)
    rows = await _query(sql, params)
    return str(rows[0]["d"]) if rows else None


async def _prev_date(source: str, date: str) -> str | None:
    """取给定日期之前最近的一个交易日。"""
    sql, params = idata.trade_dates_sql(source=source, limit=1, before=date)
    rows = await _query(sql, params)
    return str(rows[0]["d"]) if rows else None


async def _resolve_dates(
    source: str,
    date: str | None = None,
    dates: Sequence[str] | None = None,
    fallback_days: int = 1,
) -> list[str]:
    """把 ``date`` / ``dates`` 解析成具体日期列表；都为空时回落到最近 N 个交易日。"""
    provided = idata.normalize_dates(dates, date)
    if provided:
        return provided
    sql, params = idata.trade_dates_sql(source=source, limit=fallback_days)
    rows = await _query(sql, params)
    return [str(r["d"]) for r in rows]


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
        logger.warning(f"[idata] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[idata] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[idata] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "idata",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_idata_schema() -> dict[str, Any]:
        """Return the iData data dictionary: tables, columns, enums, units and pitfalls.

        Call this before any query. Key points it documents: amounts are in 亿元 with a
        _yi suffix, dates are YYYYMMDD, bond lending balance uses a specific amount
        type/side pair, and the institution table mixes total rows with detail rows.
        """
        # 纯计算，无 IO，保持同步以免占用线程池
        username = current_username()
        logger.info(f"[idata] describe_idata_schema | 用户: {username}")
        return {"ok": True, **idata.schema_help()}

    # ── 交易日 ──────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_trade_dates(
        source: str = "daily",
        limit: int = 1,
        before: str | None = None,
        start: str | None = None,
        end: str | None = None,
        ascending: bool = False,
    ) -> dict[str, Any]:
        """List trading dates from one of the three data sources.

        Covers "what is the latest trading day", "the previous trading day"
        (before=some date, limit=1), "the last N days" (limit=N) and an explicit range
        (start/end).

        Args:
            source: daily (institution flows) | bond (per-bond counterparties) |
                lending (bond lending). Default daily.
            limit: number of dates; default 1 (the latest trading day).
            before: only dates strictly earlier than this YYYYMMDD date.
            start / end: inclusive range; overrides limit.
            ascending: default false (newest first).
        """
        username = current_username()
        try:
            sql, params = idata.trade_dates_sql(
                source=source, limit=limit, before=before, start=start, end=end,
                ascending=ascending,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] get_trade_dates 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        dates = [str(r["d"]) for r in rows]
        logger.info(
            f"[idata] get_trade_dates | 用户: {username} | "
            f"source={source} | {len(dates)} 个"
        )
        return {"ok": True, "source": source, "count": len(dates), "dates": dates}

    # ── 机构流向 ────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_institution_flow(
        date: str | None = None,
        dates: list[str] | None = None,
        group_by: list[str] | None = None,
        include_total: bool = True,
        bond_type: str | None = None,
        institution: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Institution flows (buy/sell/net, 亿元) for one day or several days.

        This ONE tool covers every institution-level view — pick the dimensions with
        `group_by` instead of looking for a separate tool per matrix:

        - ["institution"]                       → each institution's net flow
        - ["institution","bond_type"]           → institution × bond type matrix
        - ["institution","term"]                → institution × term matrix
        - ["institution","term","bond_type"]    → full cross-section (include_total=false)
        - ["date","institution"]                → multi-day trend per institution
        - ["date","term"]                       → term stack for one institution

        Args:
            date: one trading day (YYYYMMDD); omit for the latest day.
            dates: several trading days (mutually exclusive with date).
            group_by: dimension list; see above. Default ["institution"].
            include_total: true uses the source table's total rows (day-level summary),
                false uses detail rows. Never mix them or amounts double.
            bond_type: 国债 / 政金债 / 信用债 / 地方政府债 / 同业存单 / 全部 / 合计.
            institution: restrict to one institution (used by the term-stack view).
            limit: max rows.
        """
        username = current_username()
        try:
            resolve_dates = await _resolve_dates("daily", date=date, dates=dates)
            if not resolve_dates:
                return {"ok": False, "error": "没有可用的交易日（该表可能为空）"}

            # 期限堆叠是独立口径（按 6 档分桶），单独走一条 SQL
            if group_by and "term" in group_by and "date" in group_by and not include_total:
                sql, params = idata.institution_term_stack_sql(
                    dates=resolve_dates, institution=institution, limit=limit
                )
            else:
                sql, params = idata.institution_flow_sql(
                    dates=resolve_dates,
                    group_by=group_by,
                    include_total=include_total,
                    bond_type=bond_type,
                    institution=institution,
                    limit=limit,
                )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] get_institution_flow 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_flow(rows)
        logger.info(
            f"[idata] get_institution_flow | 用户: {username} | "
            f"日期={resolve_dates} | 维度={group_by or ['institution']} | 行数={len(items)}"
        )
        return {
            "ok": True,
            "dates": resolve_dates,
            "group_by": group_by or ["institution"],
            "include_total": include_total,
            "row_count": len(items),
            "unit": "亿元",
            "rows": json_safe(items),
        }

    # ── 个券机构 ────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_bonds(
        date: str | None = None,
        keyword: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List tradable bonds (code + short name) on a trading day.

        Use it to resolve a bond code from a name fragment (keyword), e.g. keyword="国债".

        Args:
            date: YYYYMMDD; omit for the latest day.
            keyword: fuzzy match on bond code or name.
            limit: max rows.
        """
        username = current_username()
        try:
            resolve_dates = await _resolve_dates("bond", date=date)
            if not resolve_dates:
                return {"ok": False, "error": "没有可用的交易日（该表可能为空）"}
            sql, params = idata.bond_list_sql(
                date=resolve_dates[0], keyword=keyword, limit=limit
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] list_bonds 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        logger.info(
            f"[idata] list_bonds | 用户: {username} | "
            f"日期={resolve_dates[0]} | 行数={len(rows)}"
        )
        return {
            "ok": True,
            "date": resolve_dates[0],
            "row_count": len(rows),
            "rows": json_safe(rows),
        }

    @mcp.tool()
    async def get_bond_counterparts(
        bond_id: str,
        date: str | None = None,
        dates: list[str] | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Who traded one specific bond: net buy/sell (亿元) plus volume share (%) per
        counterparty institution, with the top buyer and top seller.

        A positive net_yi means the institution bought more than it sold.

        Args:
            bond_id: bond code, e.g. 260205.
            date: YYYYMMDD; omit for the latest day.
            dates: several days instead of one.
            limit: max rows.
        """
        username = current_username()
        try:
            resolve_dates = await _resolve_dates("bond", date=date, dates=dates)
            if not resolve_dates:
                return {"ok": False, "error": "没有可用的交易日（该表可能为空）"}
            sql, params = idata.bond_counterparts_sql(
                bond_id=bond_id, dates=resolve_dates, limit=limit
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] get_bond_counterparts 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_bond_rows(rows)
        buyer, seller = idata.pick_extremes(items)
        logger.info(
            f"[idata] get_bond_counterparts | 用户: {username} | "
            f"{bond_id} | 日期={resolve_dates} | 行数={len(items)}"
        )
        return {
            "ok": True,
            "bond_id": bond_id,
            "dates": resolve_dates,
            "unit": "亿元",
            "row_count": len(items),
            "top_buyer": json_safe(buyer),
            "top_seller": json_safe(seller),
            "rows": json_safe(items),
        }

    @mcp.tool()
    async def get_bond_trend(
        bond_id: str,
        date: str | None = None,
        dates: list[str] | None = None,
        days: int = 5,
        institution: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Day-by-day counterparty trend for one bond (institution × date, 亿元).

        Args:
            bond_id: bond code.
            date / dates: explicit trading days.
            days: when no date is given, use the most recent N trading days (default 5).
            institution: restrict to one counterparty.
            limit: max rows.
        """
        username = current_username()
        try:
            lookback = max(1, min(int(days or 5), 250))
            resolve_dates = await _resolve_dates(
                "bond", date=date, dates=dates, fallback_days=lookback
            )
            if not resolve_dates:
                return {"ok": False, "error": "没有可用的交易日（该表可能为空）"}
            sql, params = idata.bond_trend_sql(
                bond_id=bond_id, dates=resolve_dates, institution=institution, limit=limit
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] get_bond_trend 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_bond_rows(rows)
        logger.info(
            f"[idata] get_bond_trend | 用户: {username} | "
            f"{bond_id} | 日期数={len(resolve_dates)} | 行数={len(items)}"
        )
        return {
            "ok": True,
            "bond_id": bond_id,
            "dates": sorted(resolve_dates),
            "unit": "亿元",
            "row_count": len(items),
            "rows": json_safe(items),
        }

    @mcp.tool()
    async def list_top_active_bonds(
        date: str | None = None,
        top: int = 8,
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Most actively traded bonds on a day, ranked by Σ|net flow| across institutions
        (each entry carries its top buyer and top seller).

        Args:
            date: YYYYMMDD; omit for the latest day.
            top: how many bonds to return (default 8).
            limit: safety cap on raw rows fetched before ranking.
        """
        username = current_username()
        try:
            resolve_dates = await _resolve_dates("bond", date=date)
            if not resolve_dates:
                return {"ok": False, "error": "没有可用的交易日（该表可能为空）"}
            sql, params = idata.top_active_bonds_sql(date=resolve_dates[0], limit=limit)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] list_top_active_bonds 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        ranked = idata.rank_active_bonds(rows, top=max(1, min(int(top or 8), 50)))
        logger.info(
            f"[idata] list_top_active_bonds | 用户: {username} | "
            f"日期={resolve_dates[0]} | 原始行={len(rows)} | 返回={len(ranked)}"
        )
        return {
            "ok": True,
            "date": resolve_dates[0],
            "unit": "亿元",
            "count": len(ranked),
            "bonds": json_safe(ranked),
        }

    # ── 债券借贷 ────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_lending_snapshot(
        date: str | None = None,
        include_change: bool = True,
        only_core: bool = False,
        top: int = 7,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Bond lending snapshot: balance (亿元) and concentration (%) per bond, plus
        optional day-over-day changes and the core tracked bonds.

        Balance follows the lending-balance convention (amount type 19, side 1) — it is
        NOT the traded volume.

        Args:
            date: YYYYMMDD; omit for the latest day.
            include_change: also compare with the previous trading day and split the bonds
                into balance-increasing / decreasing groups.
            only_core: return only the bonds that are continuously updated (present on
                every trading day) and rank highest by balance.
            top: how many core bonds to keep when only_core is true (default 7).
            limit: max rows.
        """
        username = current_username()
        try:
            resolve_dates = await _resolve_dates("lending", date=date)
            if not resolve_dates:
                return {"ok": False, "error": "没有可用的交易日（该表可能为空）"}
            today = resolve_dates[0]
            sql, params = idata.lending_snapshot_sql(date=today, limit=limit)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
            snapshot = idata.decorate_lending_rows(rows)

            changes: dict[str, Any] | None = None
            if include_change:
                prev = await _prev_date("lending", today)
                if prev:
                    prev_sql, prev_params = idata.lending_snapshot_sql(date=prev, limit=limit)
                    prev_rows = await _query(prev_sql, prev_params)
                    compared = idata.with_change(snapshot, idata.decorate_lending_rows(prev_rows))
                    up = [r for r in compared if (r.get("change_yi") or 0) > 0]
                    down = [r for r in compared if (r.get("change_yi") or 0) < 0]
                    up.sort(key=lambda r: (r.get("balance_yi") or 0, r.get("concentration") or 0),
                            reverse=True)
                    down.sort(key=lambda r: (r.get("balance_yi") or 0, r.get("concentration") or 0),
                              reverse=True)
                    changes = {"prev_date": prev, "bal_up": up, "bal_down": down}
                    snapshot = compared

            core: list[dict[str, Any]] = []
            if only_core:
                days_sql, days_params = idata.lending_bond_days_sql()
                day_rows = await _query(days_sql, days_params)
                core = idata.filter_core_bonds(snapshot, day_rows, top=top)
                snapshot = core
        except Exception as e:
            logger.warning(f"[idata] get_lending_snapshot 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        logger.info(
            f"[idata] get_lending_snapshot | 用户: {username} | "
            f"日期={today} | 行数={len(snapshot)} | 环比={bool(changes)}"
        )
        return {
            "ok": True,
            "date": today,
            "unit": "亿元",
            "row_count": len(snapshot),
            "changes": json_safe(changes),
            "rows": json_safe(snapshot),
        }

    @mcp.tool()
    async def get_lending_trend(
        code: str | None = None,
        dates: list[str] | None = None,
        days: int = 10,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Lending balance (亿元) and concentration (%) trend over several trading days.

        Args:
            code: a single bond code; omit to get all bonds in the window.
            dates: explicit trading days.
            days: when no date is given, use the most recent N trading days (default 10).
            limit: max rows.
        """
        username = current_username()
        try:
            lookback = max(1, min(int(days or 10), 250))
            resolve_dates = await _resolve_dates("lending", dates=dates, fallback_days=lookback)
            if not resolve_dates:
                return {"ok": False, "error": "没有可用的交易日（该表可能为空）"}
            sql, params = idata.lending_trend_sql(
                dates=resolve_dates,
                codes=[code] if code and str(code).strip() else None,
                limit=limit,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] get_lending_trend 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_lending_rows(rows)
        logger.info(
            f"[idata] get_lending_trend | 用户: {username} | "
            f"code={code or '全部'} | 日期数={len(resolve_dates)} | 行数={len(items)}"
        )
        return {
            "ok": True,
            "code": code,
            "dates": sorted(resolve_dates),
            "unit": "亿元",
            "row_count": len(items),
            "rows": json_safe(items),
        }

    # ── 大模型点评 ──────────────────────────────────────────────────────

    @mcp.tool()
    async def get_llm_comment(
        scene: str,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Fetch the pre-generated LLM commentary for one scene.

        Args:
            scene: institution_daily | bond_institution | bond_lending.
            date: YYYYMMDD analysis date; omit for the latest successful one.
        """
        username = current_username()
        try:
            sql, params = idata.llm_comment_sql(scene=scene, date=date)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            rows = await _query(sql, params)
        except Exception as e:
            logger.warning(f"[idata] get_llm_comment 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        if not rows:
            logger.info(f"[idata] get_llm_comment 未命中 | 用户: {username} | {scene}")
            return {
                "ok": False,
                "error": f"场景 {scene} 没有成功生成的点评（可换 date 或稍后再试）。",
            }

        logger.info(f"[idata] get_llm_comment | 用户: {username} | {scene}")
        return {"ok": True, "scene": scene, "comment": json_safe(rows[0])}

    return mcp
