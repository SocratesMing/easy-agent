"""国内宏观经济指标预测 MCP 工具（报告 / 指标 / 多模型预测 / SHAP 归因）

挂载路径：``/mcp/macro_forecast/``

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
from . import domestic_macro

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Domestic macro forecast assistant. Call describe_domestic_macro_schema before querying. "
    "Reports are identified by forecast_month (YYYY-MM); follow-up queries use report_id / "
    "indicator_id. Indicator values are the model forecast, alongside the prior value, market "
    "consensus and the Zhang Yu team estimate. SHAP tools explain which features drove a "
    "prediction; feature contributions are signed (positive lifts the forecast)."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景涉及的固定几张表）。"""
    return policy_from_env("FORECAST_", default_allowed=domestic_macro.default_allowed_tables())


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
        logger.warning(f"[macro_forecast] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[macro_forecast] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[macro_forecast] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "国内宏观指标预测",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 国内宏观经济指标预测 ──────────────────────────────────────────

    @mcp.tool()
    def describe_domestic_macro_schema() -> dict[str, Any]:
        """查看国内宏观预测报告、指标、模型明细、SHAP 与解读表的口径。"""
        username = current_username()
        logger.info(f"[macro_forecast] describe_domestic_macro_schema | 用户: {username}")
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

    return mcp
