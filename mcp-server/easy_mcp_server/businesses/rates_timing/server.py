"""利率择时模型 MCP 工具（只读已保存的预测与运行审计）

挂载路径：``/mcp/rates_timing/``

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
from . import rates_timing

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Rates timing model assistant, read-only over already-saved results. Call "
    "describe_rates_timing_schema before querying. It never starts a model run and never "
    "generates explanations. probability is stored as 0-1 raw probability; signal_label is "
    "bucketed by 0.4/0.5/0.6 while prediction is decided by that row's threshold, so a value "
    "between the threshold and 0.5 can be prediction=1 with a 震荡下行 label. Dates use "
    "YYYY-MM-DD."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景涉及的固定几张表）。"""
    return policy_from_env("RATES_", default_allowed=rates_timing.default_allowed_tables())


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
        logger.warning(f"[rates_timing] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[rates_timing] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[rates_timing] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "rates_timing",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
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

    return mcp
