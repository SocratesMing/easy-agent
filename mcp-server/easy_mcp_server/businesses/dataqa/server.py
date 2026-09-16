"""问数（NL → SQL）MCP 工具，当前承载 XBOND 本币债券行情场景。

设计取舍：不做自然语言硬解析，改为"元数据/口径说明 + 领域工具 + 通用只读 SQL"，
由客户端模型决定怎么取数，服务端只负责收敛与审计。

三类工具的分工：

- `describe_xbond_schema` / `list_bonds` / `get_bond_quotes`：XBOND 领域工具，
  覆盖"近一个月不同债券的行情"这类高频问法，默认走长表视图 + 每日粒度，
  结果紧凑且不会选错档位
- `list_tables` / `describe_table` / `sample_rows`：通用探索，兜底新问法
- `query`：只接受单条只读 SELECT，作为以上都覆盖不到时的逃生口

并发：MCP SDK 对同步工具函数是**直接调用**、不会丢线程池，而数据库访问是阻塞的
pymysql 调用，放在事件循环里会让一个慢查询拖住所有用户。因此凡涉及 IO 的工具都写成
``async def`` 并把阻塞调用交给 ``anyio.to_thread.run_sync``；纯计算工具保持同步。

身份：完全复用全局 Bearer 鉴权中间件（`easy_mcp_server/auth.py`）。中间件按
URL 前缀 `/mcp/dataqa/` 提取业务名，用 API Key 反查 username 后写入 ContextVar；
业务代码只调 `current_username()`，不感知 HTTP 与数据库。每个用户在主应用设置页
为 dataqa 单独签发一把 Key（明文仅显示一次）。

数据：复用主库连接（`MYSQL_*`），未做行级权限隔离——同一库内可见表对每个用户
返回相同结果，但每次查询都会记录 username。
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from ...context import current_username
from . import metadata
from . import xbond
from .datasource import readonly_query
from .guard import assert_identifier, assert_readonly, json_safe, max_rows

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "XBOND bond market data assistant. "
    "For '近一个月债券行情' style questions, use get_bond_quotes directly "
    "(days=30, granularity='daily', level=1); it reads the long-format view "
    f"{xbond.depth_view()} where one row is one bond, one timestamp, one price level. "
    "Call describe_xbond_schema first if you are unsure about columns or levels. "
    "Use list_bonds to discover bond codes, and query only for questions the "
    "domain tools cannot answer. Also available: list_tables, describe_table, "
    "sample_rows. Prefer the domain tools over raw SQL; in query, only a single "
    "read-only SELECT is allowed and results are capped."
)


def _result(sql: str, rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    truncated = len(rows) > limit
    kept = rows[:limit]
    columns = list(kept[0].keys()) if kept else []
    return {
        "ok": True,
        "sql": sql,
        "columns": columns,
        "row_count": len(kept),
        "truncated": truncated,
        "rows": [json_safe(row) for row in kept],
    }


def build() -> FastMCP:
    mcp = FastMCP(
        "dataqa",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── XBOND 领域工具 ──────────────────────────────────────────────────

    @mcp.tool()
    def describe_xbond_schema() -> dict[str, Any]:
        """Return the XBOND market-data dictionary: base table, long-format view,
        price-level semantics, time filtering rules and typical question mappings.

        Call this before writing any XBOND SQL so you use the right columns.
        """
        # 纯计算，无 IO，保持同步以免无谓地占用线程池
        username = current_username()
        logger.info(f"[dataqa] describe_xbond_schema | 用户: {username}")
        return {"ok": True, **xbond.schema_help()}

    @mcp.tool()
    async def list_bonds(
        keyword: str | None = None, days: int = 30, limit: int = 50
    ) -> dict[str, Any]:
        """List bonds that quoted in the last N days.

        Returns bond code, name, type, term, quote row count and last update time.
        Use keyword to filter by bond code or name. Call this to discover which
        bond codes exist before asking for quotes.
        """
        username = current_username()
        sql, params = xbond.list_bonds_sql(keyword, days, limit)
        try:
            rows = await anyio.to_thread.run_sync(readonly_query, sql, params)
        except Exception as e:
            logger.warning(f"[dataqa] list_bonds 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e), "sql": sql}
        logger.info(f"[dataqa] list_bonds | 用户: {username} | 债券数: {len(rows)}")
        return {
            "ok": True,
            "sql": sql,
            "row_count": len(rows),
            "rows": [json_safe(row) for row in rows],
        }

    @mcp.tool()
    async def get_bond_quotes(
        bond_codes: list[str] | None = None,
        days: int = 30,
        level: int | None = 1,
        granularity: str = "daily",
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Get XBOND two-sided quotes for the last N days.

        This is the primary tool for questions like "近一个月各债券行情".
        granularity: 'daily' = last quote per bond per day (default, compact),
        'latest' = newest quote per bond, 'raw' = every tick ordered by time.
        level: price level 1-5, 1 is the best bid/offer; pass null for all levels.
        bond_codes: omit to query every bond seen in the window.
        """
        username = current_username()
        try:
            sql, params = xbond.depth_sql(bond_codes, days, level, granularity, limit)
        except ValueError as e:
            logger.warning(f"[dataqa] get_bond_quotes 参数非法 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}

        try:
            rows = await anyio.to_thread.run_sync(readonly_query, sql, params)
        except Exception as e:
            logger.warning(f"[dataqa] get_bond_quotes 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e), "sql": sql}

        quotes = xbond.format_quotes(rows)
        logger.info(
            f"[dataqa] get_bond_quotes | 用户: {username} | 天数: {days} | "
            f"粒度: {granularity} | 行数: {len(quotes)}"
        )
        return {
            "ok": True,
            "sql": sql,
            "row_count": len(quotes),
            "summary": xbond.summarize_quotes(quotes, days, granularity),
            "rows": [json_safe(row) for row in quotes[: xbond.row_limit(limit)]],
        }

    # ── 通用问数工具 ───────────────────────────────────────────────────

    @mcp.tool()
    async def list_tables() -> list[dict[str, Any]]:
        """List every queryable table and view with its Chinese comment, type and
        approximate row count.

        Tables that are not listed cannot be queried. Prefer the XBOND view for
        market data questions.
        """
        username = current_username()
        logger.info(f"[dataqa] list_tables | 用户: {username}")
        return await anyio.to_thread.run_sync(metadata.list_tables)

    @mcp.tool()
    async def describe_table(table: str) -> dict[str, Any]:
        """Return the column dictionary of one table or view.

        Includes column name, SQL type, nullability, default, key, Chinese comment
        and enum/set candidate values. Use it before writing SQL so you never
        invent a column.
        """
        username = current_username()
        try:
            assert_identifier(table)
        except ValueError as e:
            logger.warning(f"[dataqa] describe_table 非法表名 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        try:
            info = await anyio.to_thread.run_sync(metadata.describe_table, table)
        except ValueError as e:
            logger.warning(f"[dataqa] describe_table | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[dataqa] describe_table | 用户: {username} | 表: {info['table']}")
        return {"ok": True, **info}

    @mcp.tool()
    async def sample_rows(table: str, limit: int = 5) -> dict[str, Any]:
        """Return a few real rows from a table or view (max 20).

        Use it to understand the actual shape of values such as date format,
        status codes and units before writing your query.
        """
        username = current_username()
        try:
            rows = await anyio.to_thread.run_sync(metadata.sample_rows, table, limit)
        except ValueError as e:
            logger.warning(f"[dataqa] sample_rows | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[dataqa] sample_rows | 用户: {username} | 表: {table} | 行数: {len(rows)}")
        return _result(f"SELECT * FROM `{table}`", rows, max(1, min(int(limit or 5), 20)))

    @mcp.tool()
    async def query(sql: str, limit: int | None = None) -> dict[str, Any]:
        """Run one READ-ONLY SELECT and return columns, rows and a summary.

        Escape hatch for questions the XBOND tools cannot answer. Only a single
        SELECT/WITH statement is allowed: writes, stacked statements and system
        schemas are rejected. A LIMIT is appended when missing and the result is
        capped (DATAQA_MAX_ROWS, default 500). Returns {"ok": false, "error": ...}
        instead of raising, so you can fix the SQL and retry.
        """
        username = current_username()
        try:
            safe_sql = assert_readonly(sql, limit)
        except ValueError as e:
            logger.warning(f"[dataqa] query 被拦截 | 用户: {username} | {e} | sql: {sql}")
            return {"ok": False, "error": str(e), "sql": sql}

        try:
            rows = await anyio.to_thread.run_sync(readonly_query, safe_sql)
        except Exception as e:
            logger.warning(f"[dataqa] query 执行失败 | 用户: {username} | {e} | sql: {safe_sql}")
            return {"ok": False, "error": str(e), "sql": safe_sql}

        logger.info(
            f"[dataqa] query | 用户: {username} | 行数: {len(rows)} | sql: {safe_sql}"
        )
        return _result(safe_sql, rows, limit or max_rows())

    return mcp
