"""策略问数（strategyqa）MCP 工具。

**权限模型**：行级隔离。所有查询强制带上 `AUTHOR = 当前登录账号`，
只看得到自己创建的策略。为了让隔离真正闭环，这里**不暴露**任何"任意 SQL"
或"整表采样"的工具：

- 没有 `query`：模型无法自己拼 SQL 绕开 AUTHOR 条件
- 没有 `sample_rows`：避免采样出他人策略的任意行
- `list_tables` / `describe_table` 只读 `information_schema`，不返回业务数据

前提：主应用的用户名必须与表里的 `AUTHOR` 一致（统一账号体系）。

并发：MCP SDK 对同步工具函数是**直接调用**、不会丢线程池，而数据库访问是阻塞的
pymysql 调用。因此凡涉及 IO 的工具都写成 ``async def`` 并把阻塞调用交给
``anyio.to_thread.run_sync``，避免一个慢查询拖住所有用户。
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
from . import strategy

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Strategy table (fmut2_strategy_manage) Q&A assistant. "
    "Row-level isolation is enforced: every result contains ONLY strategies whose "
    "AUTHOR equals the logged-in account. Other people's strategies are not visible, "
    "and there is no raw-SQL tool to bypass this. "
    "Use list_strategies for '我有哪些策略 / 按类型筛选 / 收益率排序' questions and "
    "get_strategy for one strategy's detail. "
    "Call describe_strategy_schema first when unsure about columns, enums or the "
    "millisecond timestamp format. "
    "PROFIT_LOSS_CHART is a large text column and is never returned."
)


def _policy():
    """本次请求的表可见性策略（读 STRATEGY_ALLOWED_TABLES / STRATEGY_DENY_TABLES）。"""
    return policy_from_env("STRATEGY_")


def build() -> FastMCP:
    mcp = FastMCP(
        "strategyqa",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 策略领域工具 ────────────────────────────────────────────────────

    @mcp.tool()
    def describe_strategy_schema() -> dict[str, Any]:
        """Return the strategy-table dictionary: columns, enums, time format and
        the visibility rule (you only ever see strategies you authored).

        Call this before writing any strategy query so you use the right columns,
        know that timestamps are milliseconds, and understand the isolation rule.
        """
        # 纯计算，无 IO，保持同步以免占用线程池
        username = current_username()
        logger.info(f"[strategyqa] describe_strategy_schema | 用户: {username}")
        return {"ok": True, **strategy.schema_help()}

    @mcp.tool()
    async def list_strategies(
        keyword: str | None = None,
        strategy_type: str | None = None,
        source: str | None = None,
        report_type: int | None = None,
        order_by: str = "update_time",
        descending: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List the strategies authored by the current account, newest first.

        keyword matches strategy id / name / tags (LIKE). strategy_type is 自营 or
        做市. source is LOC / REM / WS. report_type is 1(回测) / 2(仿真) / 3(实盘).
        order_by accepts update_time / create_time / total_yield / rp_num.
        The AUTHOR filter is applied automatically and cannot be overridden.
        """
        username = current_username()
        sql, params = strategy.list_strategies_sql(
            viewer=username,
            keyword=keyword,
            strategy_type=strategy_type,
            source=source,
            report_type=report_type,
            order_by=order_by,
            descending=descending,
            limit=limit,
        )
        try:
            rows = await anyio.to_thread.run_sync(readonly_query, sql, params)
        except Exception as e:
            logger.warning(f"[strategyqa] list_strategies 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        items = strategy.decorate(rows)
        logger.info(f"[strategyqa] list_strategies | 账号: {username} | 行数: {len(items)}")
        return {
            "ok": True,
            "row_count": len(items),
            "summary": strategy.summarize(items, username),
            "rows": [json_safe(row) for row in items],
        }

    @mcp.tool()
    async def get_strategy(strategy_id: str) -> dict[str, Any]:
        """Get the detail of one strategy authored by the current account.

        Returns description, address and report id in addition to the list
        columns. Strategies authored by someone else are not accessible and will
        be reported as not found. The large PROFIT_LOSS_CHART column is not
        included.
        """
        username = current_username()
        sql, params = strategy.get_strategy_sql(username, strategy_id)
        try:
            rows = await anyio.to_thread.run_sync(readonly_query, sql, params)
        except Exception as e:
            logger.warning(f"[strategyqa] get_strategy 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        if not rows:
            logger.info(f"[strategyqa] get_strategy 未命中 | 账号: {username} | {strategy_id}")
            return {
                "ok": False,
                "error": f"账号 {username} 名下没有策略 {strategy_id}（只能查看自己创建的策略）",
            }

        item = strategy.decorate(rows)[0]
        logger.info(f"[strategyqa] get_strategy | 账号: {username} | 策略: {strategy_id}")
        return {"ok": True, "strategy": json_safe(item)}

    # ── 结构探索（只读 information_schema，不返回业务数据） ─────────────

    @mcp.tool()
    async def list_tables() -> list[dict[str, Any]]:
        """List queryable tables and views with Chinese comments and row counts.

        Only schema metadata is returned; use list_strategies for actual data.
        """
        username = current_username()
        logger.info(f"[strategyqa] list_tables | 用户: {username}")
        return await anyio.to_thread.run_sync(kit_list_tables, _policy())

    @mcp.tool()
    async def describe_table(table: str) -> dict[str, Any]:
        """Return the column dictionary of one table or view (schema only).

        Includes column name, SQL type, nullability, default, key, Chinese comment
        and enum/set candidate values. Does not return any rows.
        """
        username = current_username()
        try:
            info = await anyio.to_thread.run_sync(kit_describe_table, table, _policy())
        except ValueError as e:
            logger.warning(f"[strategyqa] describe_table | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[strategyqa] describe_table | 用户: {username} | 表: {info['table']}")
        return {"ok": True, **info}

    return mcp
