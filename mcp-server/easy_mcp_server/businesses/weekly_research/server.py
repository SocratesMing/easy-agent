"""研报观点矩阵与周度宏观预测 MCP 工具

挂载路径：``/mcp/weekly_research/``

MCP 工具按"用户会问的问题"切分，而非照搬原始 SQL。设计说明见
``docs/MCP_docs/`` 下对应的 SQL 清单。

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
from . import weekly_research

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Weekly research opinions and institution macro forecasts. Call "
    "describe_weekly_research_schema before querying. Weeks are labelled YYYYMMDD-YYYYMMDD. "
    "The opinions table stores one row per week with a JSON payload; the tools expand it into "
    "one row per opinion (institution / category / asset / direction). Use the "
    "get_weekly_macro_* tools for the macro forecast table. Category values include 境内利率 / "
    "境外利率 / 外汇 / 全球股指 / 大宗 / 宏观指标."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景涉及的固定几张表）。"""
    return policy_from_env("WEEKLY_RESEARCH_", default_allowed=weekly_research.default_allowed_tables())


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)



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
        logger.warning(f"[weekly_research] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[weekly_research] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[weekly_research] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "weekly_research",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 研报观点矩阵与周度宏观预测 ────────────────────────────────────

    @mcp.tool()
    def describe_weekly_research_schema() -> dict[str, Any]:
        """查看周度观点矩阵和宏观预测 JSON 表的查询口径及数值/文字区别。"""
        username = current_username()
        logger.info(f"[weekly_research] describe_weekly_research_schema | 用户: {username}")
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
