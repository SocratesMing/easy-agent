"""投资研究 MCP 工具（iData、国内宏观预测、利率择时、周度研报观点）。

**工具划分原则**：按"用户会问的问题"切，而不是按原始接口切。因此

- 交易日查询（原 10 条 SQL）合并为 1 个 ``get_trade_dates``
- 机构流向（原 9 条 SQL）合并为 1 个 ``get_institution_flow``，维度靠 ``group_by`` 组合
- 个券机构的"净买卖"与"成交量占比"是同一条 SQL，合并为 ``get_bond_counterparts``

其中 iData 场景保留原有 10 个工具；其他场景按各自 SQL 清单中的查询口径注册。
设计说明见 ``mcp-server/iData-MCP工具设计.md`` 与 ``docs/MCP_docs/``。

**iData 统一行为**：iData 工具的 ``date`` 留空 → 自动取该表最新交易日；
iData 金额一律**亿元**（字段名带 ``_yi``）。其他场景按各自领域口径处理日期与单位。

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
from . import domestic_macro, idata, rates_timing, weekly_research

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Investment research data assistant for four domains: iData bond analytics, domestic "
    "macro forecasts, rates timing, and weekly research opinions/macro forecasts. Call the "
    "matching schema tool first: describe_idata_schema, describe_domestic_macro_schema, "
    "describe_rates_timing_schema, or describe_weekly_research_schema. For iData, amounts "
    "are ALWAYS in 亿元 (100M CNY) with an _yi suffix; concentration and ratios are "
    "percentages (24.53 means 24.53%). Dates use YYYYMMDD and an omitted date means the "
    "latest trading day. Use get_institution_flow for institution matrices/trends by "
    "varying group_by, get_bond_counterparts for bond counterparties, and "
    "get_lending_snapshot for lending balances. Macro forecast reports use YYYY-MM and "
    "report_id/indicator_id for follow-up queries. Weekly research uses "
    "YYYYMMDD-YYYYMMDD labels. Rates tools only read saved results; they never start a run "
    "or generate an explanation."
)


def _all_allowed_tables() -> tuple[str, ...]:
    """本业务四个场景涉及的全部表，作为结构探索白名单的缺省值。

    每个场景模块各自声明 ``default_allowed_tables()``，这里做汇总——只用单个
    场景的话，模型做结构探索时看不到其它场景的表。
    """
    return tuple(
        table
        for scenario in (idata, domestic_macro, rates_timing, weekly_research)
        for table in scenario.default_allowed_tables()
    )


def _policy():
    """结构探索的可见性策略。

    缺省把白名单收敛到本业务四个场景**全部**的表（不这样做的话，模型做结构
    探索时能看到同库里所有业务表）。``IDATA_ALLOWED_TABLES`` 可整体覆盖，
    ``IDATA_DENY_TABLES`` 可追加屏蔽。
    """
    return policy_from_env("IDATA_", default_allowed=_all_allowed_tables())


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
        logger.warning(
            f"[investment_research] {tool} 失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[investment_research] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(
        f"[investment_research] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}"
    )
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "investment_research",
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
        logger.info(f"[investment_research] describe_idata_schema | 用户: {username}")
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
            logger.warning(f"[investment_research] get_trade_dates 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        dates = [str(r["d"]) for r in rows]
        logger.info(
            f"[investment_research] get_trade_dates | 用户: {username} | "
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
            logger.warning(f"[investment_research] get_institution_flow 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_flow(rows)
        logger.info(
            f"[investment_research] get_institution_flow | 用户: {username} | "
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
            logger.warning(f"[investment_research] list_bonds 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        logger.info(
            f"[investment_research] list_bonds | 用户: {username} | "
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
            logger.warning(f"[investment_research] get_bond_counterparts 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_bond_rows(rows)
        buyer, seller = idata.pick_extremes(items)
        logger.info(
            f"[investment_research] get_bond_counterparts | 用户: {username} | "
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
            logger.warning(f"[investment_research] get_bond_trend 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_bond_rows(rows)
        logger.info(
            f"[investment_research] get_bond_trend | 用户: {username} | "
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
            logger.warning(f"[investment_research] list_top_active_bonds 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        ranked = idata.rank_active_bonds(rows, top=max(1, min(int(top or 8), 50)))
        logger.info(
            f"[investment_research] list_top_active_bonds | 用户: {username} | "
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
            logger.warning(f"[investment_research] get_lending_snapshot 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        logger.info(
            f"[investment_research] get_lending_snapshot | 用户: {username} | "
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
            logger.warning(f"[investment_research] get_lending_trend 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = idata.decorate_lending_rows(rows)
        logger.info(
            f"[investment_research] get_lending_trend | 用户: {username} | "
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
            logger.warning(f"[investment_research] get_llm_comment 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        if not rows:
            logger.info(f"[investment_research] get_llm_comment 未命中 | 用户: {username} | {scene}")
            return {
                "ok": False,
                "error": f"场景 {scene} 没有成功生成的点评（可换 date 或稍后再试）。",
            }

        logger.info(f"[investment_research] get_llm_comment | 用户: {username} | {scene}")
        return {"ok": True, "scene": scene, "comment": json_safe(rows[0])}

    # ── 国内宏观经济指标预测 ──────────────────────────────────────────

    @mcp.tool()
    def describe_domestic_macro_schema() -> dict[str, Any]:
        """查看国内宏观预测报告、指标、模型明细、SHAP 与解读表的口径。"""
        username = current_username()
        logger.info(f"[investment_research] describe_domestic_macro_schema | 用户: {username}")
        return {"ok": True, **domestic_macro.schema_help()}

    @mcp.tool()
    async def list_reports(limit: int = 100) -> dict[str, Any]:
        """列出可用的国内宏观预测报告，按 forecast_month 倒序。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "list_reports", username,
            lambda: domestic_macro.report_list_sql(limit),
            lambda rows: {"count": len(rows), "reports": rows},
        )

    @mcp.tool()
    async def get_report_by_month(forecast_month: str) -> dict[str, Any]:
        """按预测月份 YYYY-MM 定位报告；后续指标查询使用返回的 report_id。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_report_by_month", username,
            lambda: domestic_macro.report_by_month_sql(forecast_month),
            lambda rows: {
                "found": bool(rows),
                "report": rows[0] if rows else None,
                "complete": bool(rows and rows[0].get("status") == "done"),
            },
        )

    @mcp.tool()
    async def get_report_meta(report_id: int) -> dict[str, Any]:
        """按 report_id 查询报告元信息、数据截止月、模型池和落库状态。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_report_meta", username,
            lambda: domestic_macro.report_meta_sql(report_id),
            lambda rows: {"found": bool(rows), "report": rows[0] if rows else None},
        )

    @mcp.tool()
    async def get_overview_indicators(report_id: int, limit: int = 500) -> dict[str, Any]:
        """查询某份报告的全部有效指标，板块顺序为增长、通胀、信用、外贸、其他。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_overview_indicators", username,
            lambda: domestic_macro.overview_indicators_sql(report_id, limit),
            lambda rows: {"report_id": report_id, "count": len(rows), "indicators": rows},
        )

    @mcp.tool()
    async def get_report_summary(report_id: int) -> dict[str, Any]:
        """统计报告中各板块的有效指标数量。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_report_summary", username,
            lambda: domestic_macro.report_summary_sql(report_id),
            lambda rows: {"report_id": report_id, "groups": rows},
        )

    @mcp.tool()
    async def get_indicators_by_group(
        report_id: int, group_cat: str, limit: int = 500
    ) -> dict[str, Any]:
        """按板块筛选报告指标；group_cat 仅支持增长、通胀、信用、外贸、其他。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_indicators_by_group", username,
            lambda: domestic_macro.indicators_by_group_sql(report_id, group_cat, limit),
            lambda rows: {
                "report_id": report_id,
                "group_cat": group_cat,
                "count": len(rows),
                "indicators": rows,
            },
        )

    @mcp.tool()
    async def search_indicator_by_name(keyword: str, limit: int = 100) -> dict[str, Any]:
        """跨报告模糊搜索指标名，返回月份与 indicator_id；建议使用准确名称继续查询。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "search_indicator_by_name", username,
            lambda: domestic_macro.search_indicator_sql(keyword, limit),
            lambda rows: {"count": len(rows), "matches": rows},
        )

    @mcp.tool()
    async def get_indicator_snapshot(indicator_id: int) -> dict[str, Any]:
        """查询单指标前值、模型预测、市场预期、张瑜预测与 R²。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_indicator_snapshot", username,
            lambda: domestic_macro.indicator_snapshot_sql(indicator_id),
            lambda rows: {"found": bool(rows), "indicator": rows[0] if rows else None},
        )

    @mcp.tool()
    async def get_model_predictions(indicator_id: int, limit: int = 100) -> dict[str, Any]:
        """按综合排名查询某指标参与预测的模型与各自预测值。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_model_predictions", username,
            lambda: domestic_macro.model_predictions_sql(indicator_id, limit),
            lambda rows: {"indicator_id": indicator_id, "count": len(rows), "models": rows},
        )

    @mcp.tool()
    async def get_feature_contributions(
        indicator_id: int, limit: int = 500
    ) -> dict[str, Any]:
        """查询指标 Top-3 模型的完整 SHAP 归因，正值抬升预测、负值压低预测。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_feature_contributions", username,
            lambda: domestic_macro.feature_contributions_sql(indicator_id, limit),
            lambda rows: {
                "indicator_id": indicator_id,
                "count": len(rows),
                "contributions": rows,
            },
        )

    @mcp.tool()
    async def get_selected_features(indicator_id: int, limit: int = 500) -> dict[str, Any]:
        """列出某指标建模最终使用的特征名；此清单不包含 SHAP 贡献。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_selected_features", username,
            lambda: domestic_macro.selected_features_sql(indicator_id, limit),
            lambda rows: {
                "indicator_id": indicator_id,
                "count": len(rows),
                "features": [row.get("feature") for row in rows],
            },
        )

    @mcp.tool()
    async def get_top_shap_features(indicator_id: int, limit: int = 5) -> dict[str, Any]:
        """按 SHAP 绝对值返回影响力最大的特征，limit 最大 50。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_top_shap_features", username,
            lambda: domestic_macro.top_shap_features_sql(indicator_id, limit),
            lambda rows: {"indicator_id": indicator_id, "features": rows},
        )

    @mcp.tool()
    async def compare_indicator_months(
        indicator_name: str, limit: int = 250
    ) -> dict[str, Any]:
        """精确匹配指标全名并按月份升序查询预测序列；可先用搜索工具确认全名。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "compare_indicator_months", username,
            lambda: domestic_macro.compare_indicator_months_sql(indicator_name, limit),
            lambda rows: {"indicator_name": indicator_name, "count": len(rows), "months": rows},
        )

    @mcp.tool()
    async def get_llm_analysis(
        report_id: int, scenario: str, indicator_id: int | None = None
    ) -> dict[str, Any]:
        """查询已保存的宏观解读；只有 status=done 的 content 可以引用为完成内容。"""
        username = current_username()
        return await _domain_query(
            "domestic_macro", "get_llm_analysis", username,
            lambda: domestic_macro.llm_analysis_sql(report_id, scenario, indicator_id),
            lambda rows: {
                "found": bool(rows),
                "analysis": rows[0] if rows else None,
                "usable": bool(rows and rows[0].get("status") == "done"),
            },
        )

    # ── 利率择时模型（只读已保存结果） ─────────────────────────────────

    @mcp.tool()
    def describe_rates_timing_schema() -> dict[str, Any]:
        """查看利率择时模型的预测目标、概率单位、运行状态和解释证据口径。"""
        username = current_username()
        logger.info(f"[investment_research] describe_rates_timing_schema | 用户: {username}")
        return {"ok": True, **rates_timing.schema_help()}

    @mcp.tool()
    async def get_rates_latest_signal_date() -> dict[str, Any]:
        """返回预测结果表最新 signal_date；它不代表源因子 CSV 的最新日期。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_latest_signal_date", username,
            rates_timing.latest_signal_date_sql,
            lambda rows: {
                "found": bool(rows and rows[0].get("latest_signal_date") is not None),
                "latest_signal_date": rows[0].get("latest_signal_date") if rows else None,
            },
        )

    @mcp.tool()
    async def get_rates_previous_signal_date(date: str) -> dict[str, Any]:
        """返回指定日期之前最近一条已落库预测日期，不推断完整市场交易日历。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_previous_signal_date", username,
            lambda: rates_timing.previous_signal_date_sql(date),
            lambda rows: {
                "found": bool(rows and rows[0].get("previous_signal_date") is not None),
                "previous_signal_date": rows[0].get("previous_signal_date") if rows else None,
            },
        )

    @mcp.tool()
    async def get_rates_recent_signal_dates(limit: int = 20) -> dict[str, Any]:
        """列出最近 1—250 个已落库预测日期，按新到旧排列。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_recent_signal_dates", username,
            lambda: rates_timing.recent_signal_dates_sql(limit),
            lambda rows: {"count": len(rows), "dates": [row.get("signal_date") for row in rows]},
        )

    @mcp.tool()
    async def get_rates_prediction(date: str | None = None) -> dict[str, Any]:
        """查询最新或指定 YYYY-MM-DD 参考日预测，保留概率、阈值、分类与原中文信号。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_prediction", username,
            lambda: rates_timing.prediction_sql(date),
            lambda rows: {
                "found": bool(rows),
                "target": "参考日 T 的 T+26 相对 T+5 十年期国债收益率涨跌",
                "probability_unit": "ratio_0_1",
                "data": rates_timing.decorate_prediction(rows[0]) if rows else None,
            },
        )

    @mcp.tool()
    async def list_rates_predictions(
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """分页查询历史预测；date 优先于日期区间，范围含首尾，page_size 最大 100。"""
        username = current_username()
        try:
            if date and str(date).strip():
                page_number, page_size_value = 1, 1
            else:
                page_number, page_size_value = rates_timing.prediction_page_settings(page, page_size)
            count_sql, count_params = rates_timing.prediction_count_sql(
                start_date=start_date, end_date=end_date, signal_date=date
            )
            page_sql, page_params = rates_timing.predictions_page_sql(
                start_date=start_date,
                end_date=end_date,
                page=page,
                page_size=page_size,
                signal_date=date,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        try:
            count_rows = await _query(count_sql, count_params)
            rows = await _query(page_sql, page_params)
        except Exception as e:
            logger.warning(
                f"[investment_research] list_rates_predictions 失败 | "
                f"域=rates_timing | 用户: {username} | {e}"
            )
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "total": int(count_rows[0].get("total") or 0) if count_rows else 0,
            "page": page_number,
            "page_size": page_size_value,
            "items": [rates_timing.decorate_prediction(row) for row in rows],
        }

    @mcp.tool()
    async def get_rates_run_status(run_id: int) -> dict[str, Any]:
        """查询运行状态与解释状态；仅读取，不会重启或修改任务。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_run_status", username,
            lambda: rates_timing.run_status_sql(run_id),
            lambda rows: {
                "found": bool(rows),
                "run": rates_timing.decorate_run_status(rows[0]) if rows else None,
            },
        )

    @mcp.tool()
    async def get_rates_run_audit(run_id: int) -> dict[str, Any]:
        """查询指定运行的输入、筛选窗口、训练窗口、入选因子和参数审计。"""
        username = current_username()

        def format_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
            item = dict(rows[0]) if rows else None
            if item:
                for key in ("selected_factors", "best_params"):
                    item[key] = rates_timing.parse_json_value(item.get(key), None)
            return {"found": bool(item), "audit": item}

        return await _domain_query(
            "rates_timing", "get_rates_run_audit", username,
            lambda: rates_timing.run_audit_sql(run_id), format_audit,
        )

    @mcp.tool()
    async def get_rates_selected_factors() -> dict[str, Any]:
        """查询最近成功运行保存的入选因子及其 run_id。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_selected_factors", username,
            rates_timing.selected_factors_sql,
            lambda rows: {
                "found": bool(rows),
                "run": rates_timing.decorate_selected_factors(rows[0]) if rows else None,
            },
        )

    @mcp.tool()
    async def get_rates_backtest_metrics() -> dict[str, Any]:
        """查询最近保存的成功回测指标快照及其来源；不会重新计算指标。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_backtest_metrics", username,
            rates_timing.backtest_metrics_sql,
            lambda rows: {
                "found": bool(rows),
                "metrics_snapshot": rates_timing.decorate_metrics(rows[0]) if rows else None,
                "note": "这是已保存的回测快照，可能与最新预测来自不同 run_id。",
            },
        )

    @mcp.tool()
    async def get_rates_latest_explanation() -> dict[str, Any]:
        """读取最新预测关联的已存因子解释与状态；不触发解释生成。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_latest_explanation", username,
            rates_timing.latest_explanation_sql,
            lambda rows: {
                "found": bool(rows),
                "explanation": rates_timing.decorate_explanation(rows[0]) if rows else None,
            },
        )

    @mcp.tool()
    async def get_rates_latest_factor_evidence() -> dict[str, Any]:
        """读取最新预测已保存的量化因子证据；百分位为 0—100，贡献为对数胜算。"""
        username = current_username()
        return await _domain_query(
            "rates_timing", "get_rates_latest_factor_evidence", username,
            rates_timing.latest_explanation_sql,
            lambda rows: {
                "found": bool(rows),
                "run_id": rows[0].get("run_id") if rows else None,
                "evidence": rates_timing.decorate_explanation(rows[0]).get("evidence")
                if rows else None,
            },
        )

    # ── 研报观点矩阵与周度宏观预测 ────────────────────────────────────

    @mcp.tool()
    def describe_weekly_research_schema() -> dict[str, Any]:
        """查看周度观点矩阵和宏观预测 JSON 表的查询口径及数值/文字区别。"""
        username = current_username()
        logger.info(f"[investment_research] describe_weekly_research_schema | 用户: {username}")
        return {"ok": True, **weekly_research.schema_help()}

    @mcp.tool()
    async def list_weekly_opinion_weeks(limit: int = 100) -> dict[str, Any]:
        """列出观点矩阵中可用周标签，排除内部 test 数据。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "list_weekly_opinion_weeks", username,
            lambda: weekly_research.list_opinion_weeks_sql(limit),
            lambda rows: {"count": len(rows), "weeks": [row.get("week") for row in rows]},
        )

    @mcp.tool()
    async def get_weekly_opinion_summary(week: str) -> dict[str, Any]:
        """查询某周观点覆盖的机构数与观点条数。week 格式为 YYYYMMDD-YYYYMMDD。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_opinion_summary", username,
            lambda: weekly_research.opinion_summary_sql(week),
            lambda rows: {"found": bool(rows), "summary": rows[0] if rows else None},
        )

    @mcp.tool()
    async def get_weekly_opinions(
        week: str,
        institution: str | None = None,
        category: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """展开某周的一行一个观点；可按机构或 category 筛选，week 使用周标签。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_opinions", username,
            lambda: weekly_research.opinion_details_sql(week, institution, category, limit),
            lambda rows: {"week": week, "count": len(rows), "opinions": rows},
        )

    @mcp.tool()
    async def get_weekly_macro_opinions(
        week: str, institution: str | None = None, limit: int = 500
    ) -> dict[str, Any]:
        """只看观点矩阵中 category 为宏观指标/宏观的机构方向观点。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_macro_opinions", username,
            lambda: weekly_research.macro_opinions_sql(week, institution, limit),
            lambda rows: {"week": week, "count": len(rows), "opinions": rows},
        )

    @mcp.tool()
    async def get_weekly_opinion_direction_counts(
        week: str, institution: str | None = None, limit: int = 500
    ) -> dict[str, Any]:
        """统计某周各机构的看涨、看跌、中性观点条数，可限定一家机构。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_opinion_direction_counts", username,
            lambda: weekly_research.opinion_direction_counts_sql(week, institution, limit),
            lambda rows: {"week": week, "counts": rows},
        )

    @mcp.tool()
    async def get_weekly_asset_consensus(week: str, limit: int = 250) -> dict[str, Any]:
        """查询某周各资产看涨/看跌/中性家数，识别市场共识与分歧。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_asset_consensus", username,
            lambda: weekly_research.asset_consensus_sql(week, limit),
            lambda rows: {"week": week, "assets": rows},
        )

    @mcp.tool()
    async def get_weekly_opinion_sources(
        week: str, asset: str, limit: int = 250
    ) -> dict[str, Any]:
        """追溯某周某资产观点的研报标题、来源日期、原文摘录和判定理由。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_opinion_sources", username,
            lambda: weekly_research.opinion_sources_sql(week, asset, limit),
            lambda rows: {"week": week, "asset": asset, "sources": rows},
        )

    @mcp.tool()
    async def get_weekly_asset_opinion_trend(
        asset: str, limit: int = 250
    ) -> dict[str, Any]:
        """查询某资产跨周观点方向和当时价格锚点；价格不可用于推算期间涨跌。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_asset_opinion_trend", username,
            lambda: weekly_research.asset_opinion_trend_sql(asset, limit),
            lambda rows: {"asset": asset, "count": len(rows), "trend": rows},
        )

    @mcp.tool()
    async def get_weekly_market_overview(week: str) -> dict[str, Any]:
        """读取指定周的市场综述 Markdown 正文。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_market_overview", username,
            lambda: weekly_research.market_overview_sql(week),
            lambda rows: {
                "found": bool(rows),
                "week": week,
                "market_overview": rows[0].get("market_overview") if rows else None,
            },
        )

    @mcp.tool()
    async def list_weekly_macro_weeks(limit: int = 100) -> dict[str, Any]:
        """列出有宏观预测数据的周及数据生成时间、指标数量。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "list_weekly_macro_weeks", username,
            lambda: weekly_research.list_macro_weeks_sql(limit),
            lambda rows: {"count": len(rows), "weeks": rows},
        )

    @mcp.tool()
    async def get_weekly_macro_indicators(week: str, limit: int = 500) -> dict[str, Any]:
        """展开某周宏观预测表的一行一个指标，预测值以原始字符串保留。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_macro_indicators", username,
            lambda: weekly_research.macro_indicators_sql(week, limit),
            lambda rows: {"week": week, "count": len(rows), "indicators": rows},
        )

    @mcp.tool()
    async def get_weekly_macro_forecasts(week: str, limit: int = 1000) -> dict[str, Any]:
        """展开某周动态机构键对应的指标预测，不预设机构名单。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_macro_forecasts", username,
            lambda: weekly_research.macro_forecasts_sql(week, limit),
            lambda rows: {"week": week, "count": len(rows), "forecasts": rows},
        )

    @mcp.tool()
    async def get_weekly_macro_indicator_trend(
        indicator: str, limit: int = 250
    ) -> dict[str, Any]:
        """精确匹配宏观指标名，按周查询共识与实际值的历史序列。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_macro_indicator_trend", username,
            lambda: weekly_research.macro_indicator_trend_sql(indicator, limit),
            lambda rows: {"indicator": indicator, "count": len(rows), "trend": rows},
        )

    @mcp.tool()
    async def get_weekly_macro_coverage(week: str, limit: int = 500) -> dict[str, Any]:
        """统计某周每个宏观指标有多少家机构给出预测。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_macro_coverage", username,
            lambda: weekly_research.macro_coverage_sql(week, limit),
            lambda rows: {"week": week, "coverage": rows},
        )

    @mcp.tool()
    async def get_weekly_macro_numeric_average(
        week: str, limit: int = 500
    ) -> dict[str, Any]:
        """按机构仅数值预测计算均值；文字值会排除，避免被误转成数字。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_macro_numeric_average", username,
            lambda: weekly_research.macro_numeric_average_sql(week, limit),
            lambda rows: {"week": week, "averages": rows},
        )

    @mcp.tool()
    async def get_weekly_macro_text_mode(
        week: str, indicator: str, limit: int = 100
    ) -> dict[str, Any]:
        """对指定指标的文字型机构预测计数；按 n 降序排列，第一行是众数。"""
        username = current_username()
        return await _domain_query(
            "weekly_research", "get_weekly_macro_text_mode", username,
            lambda: weekly_research.macro_text_mode_sql(week, indicator, limit),
            lambda rows: {
                "week": week,
                "indicator": indicator,
                "mode": rows[0] if rows else None,
                "counts": rows,
            },
        )

    return mcp
