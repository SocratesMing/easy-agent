"""外汇行情（HBase）MCP 工具。

当前承载外汇 K 线 / Tick 行情场景，四张表按 `渠道 | 产品类型 | 数据类型` 划分，
表名遵循 `HSDC_HDS2_{CHANNEL}_{INSTRUMENT}_{DATATYPE}`。

设计取舍：不给模型任何"写条件"的面（没有类似 SQL 的通用入口），只有参数化工具：

- `describe_fx_schema`：口径字典，先看表/字段/易错点再取数
- `get_fx_bars`：主力工具，覆盖"近 X 小时某合约 K 线"这类高频问法
- `list_contracts`：先发现合约代码，再问行情
- `list_tables` / `describe_table` / `sample_rows`：通用探索，兜底新问法

身份：完全复用全局 Bearer 鉴权中间件（`easy_mcp_server/auth.py`），中间件按 URL 前缀
`/mcp/hbaseqa/` 提取业务名并反查 username。业务代码只调 `current_username()`。

数据：只读 scan。Thrift 侧无法设置权限隔离，同一批可见表对每个 Key 返回相同结果，
但每次调用都会记录 username；想要收敛用 `.env` 的 `HBASEQA_ALLOWED_TABLES`。
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from ...context import current_username
from . import client, fxspot, guard, metadata, times

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Foreign-exchange market data assistant backed by HBase. "
    "Call describe_fx_schema first for the table naming rule and column semantics, "
    "then list_contracts to discover contract codes, then get_fx_bars to fetch OHLCV. "
    "Time arguments accept ISO strings or millisecond integers; omitting them only "
    "covers a short recent window, so always pass start/end/hours/days for history. "
    "Ask for the newest data with order='desc'. Use list_tables, describe_table and "
    "sample_rows only when the domain tools cannot answer."
)


def build() -> FastMCP:
    mcp = FastMCP(
        "hbaseqa",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径字典 ────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_fx_schema() -> dict:
        """Return the FX market-data dictionary: table naming rule, column family,
        every qualifier with its Chinese meaning, time-field semantics and pitfalls.

        Call this BEFORE list_contracts / get_fx_bars so you use the right dimensions
        and never invent a qualifier.
        """
        username = current_username()
        logger.info(f"[hbaseqa] describe_fx_schema | 用户: {username}")
        return fxspot.schema_help()

    # ── 领域工具 ────────────────────────────────────────────────────────

    @mcp.tool()
    def list_contracts(
        table: str | None = None,
        channel: str | None = None,
        instrument: str | None = None,
        datatype: str | None = None,
        start: str | int | None = None,
        end: str | int | None = None,
        hours: int | None = None,
        days: int | None = None,
        limit: int = 50,
    ) -> dict:
        """Discover which contract codes quoted inside a time window.

        Returns contract code, source table, channel/instrument, frequency, bar count,
        first/last bar time and the last close. Run this BEFORE get_fx_bars because
        stored codes may carry a suffix (EURUSD is stored as EURUSDSP).
        Time accepts ISO strings or millisecond ints.
        """
        username = current_username()
        try:
            start_ms, end_ms = times.resolve_window(start, end, hours, days)
        except ValueError as e:
            logger.warning(
                f"[hbaseqa] list_contracts 参数非法 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        # 选表要访问 Thrift，属于"可能失败的 I/O"，不能塞进只捕 ValueError 的块里
        try:
            tables = _resolve_tables(table, channel, instrument, datatype)
        except ValueError as e:
            logger.warning(
                f"[hbaseqa] list_contracts 选表失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        except Exception as e:
            logger.warning(
                f"[hbaseqa] list_contracts 列举失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        try:
            payload = fxspot.collect_contracts(tables, start_ms, end_ms, limit)
        except Exception as e:
            logger.warning(
                f"[hbaseqa] list_contracts 失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        logger.info(
            f"[hbaseqa] list_contracts | 用户: {username} | 表: {tables} | "
            f"合约数: {payload['row_count']}"
        )
        return {"ok": True, **payload}

    @mcp.tool()
    def get_fx_bars(
        contract_codes: list[str] | None = None,
        table: str | None = None,
        channel: str | None = None,
        instrument: str | None = None,
        datatype: str | None = None,
        start: str | int | None = None,
        end: str | int | None = None,
        hours: int | None = None,
        days: int | None = None,
        frequency: str | None = None,
        buysell: str | None = None,
        granularity: str = "auto",
        order: str = "asc",
        limit: int | None = None,
    ) -> dict:
        """Get OHLCV bars for the given contracts inside a time window.

        This is the primary tool for FX market questions. contract_codes accept the
        bare pair too (EURUSD is expanded to EURUSDSP). frequency filters the bar size;
        '1N' means one minute and aliases such as '1m' are normalised for you.
        buysell filters the side and must be BID, ASK or MID when given.

        granularity controls how much data comes back and MUST be chosen to fit the
        window: one month of 1-minute bars is ~130k rows and cannot be returned.
          'auto'   (default) - daily for windows longer than 2 days, raw otherwise
          'daily' / 'hourly' - keep only the last bar per contract per day/hour
          'latest'           - only the newest bar per contract. Cheapest option: it
                               reverse-probes a few rows, so use it to answer "is there
                               data on day X", "how recent is the data" - never 'raw'
          'raw'              - every bar, capped by HBASEQA_MAX_ROWS
        Downsampled results also carry a compact per-contract `contracts` summary
        (first/last close, change_pct) - read that first, it is usually enough to
        answer "how did it move". NOTE: with daily/hourly the high/low are only the
        sampled points' range, NOT the true extremes; use granularity='raw' on a
        narrower window if exact extremes matter.

        order='asc' returns oldest N rows, order='desc' returns the NEWEST N rows.
        For a long window with granularity='raw', order='desc' gives the newest bars
        while 'asc' would only give the oldest ones.
        Time accepts ISO strings or millisecond ints; without any time argument only a
        short recent window is read. Return row_count is capped by HBASEQA_MAX_ROWS.

        Always read `contracts` (per-contract summary) before `rows`; and read `notes` -
        it states when results are truncated, downsampled or missing a contract.
        """
        username = current_username()
        try:
            start_ms, end_ms = times.resolve_window(start, end, hours, days)
            checked_order = fxspot.assert_order(order)
            checked_side = fxspot.assert_buysell(buysell)
            checked_granularity = fxspot.assert_granularity(granularity)
        except ValueError as e:
            logger.warning(
                f"[hbaseqa] get_fx_bars 参数非法 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        # 选表走 Thrift，可能抛连接类异常，必须和参数校验分开兜
        try:
            tables = _resolve_tables(table, channel, instrument, datatype)
        except ValueError as e:
            logger.warning(
                f"[hbaseqa] get_fx_bars 选表失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        except Exception as e:
            logger.warning(
                f"[hbaseqa] get_fx_bars 列举失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        try:
            payload = fxspot.collect(
                tables,
                start_ms,
                end_ms,
                contract_codes=contract_codes,
                frequency=frequency,
                buysell=checked_side,
                order=checked_order,
                limit=limit,
                granularity=checked_granularity,
            )
        except Exception as e:
            logger.warning(
                f"[hbaseqa] get_fx_bars 失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        logger.info(
            f"[hbaseqa] get_fx_bars | 用户: {username} | 表: {tables} | "
            f"合约: {contract_codes} | 行数: {payload['row_count']}"
        )
        return {"ok": True, **payload}

    # ── 通用探索（兜底） ────────────────────────────────────────────────

    @mcp.tool()
    def list_tables() -> list[dict]:
        """List every queryable HBase table with the channel / instrument / datatype
        parsed from its name (e.g. HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH).

        Tables not listed cannot be queried. Use these dimensions to pick the right
        table for get_fx_bars.
        """
        username = current_username()
        logger.info(f"[hbaseqa] list_tables | 用户: {username}")
        try:
            return metadata.list_tables()
        except Exception as e:
            logger.warning(
                f"[hbaseqa] list_tables 失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            # 保持返回 list 契约，但把 ok=false 与可操作提示带上
            return [{"ok": False, "error": _failure(e)}]

    @mcp.tool()
    def describe_table(table: str) -> dict:
        """Return one table's dictionary: dimensions, column families, every qualifier
        with Chinese meaning, and how rowkey/time filtering is currently configured.

        Use it before writing filters so you never invent a qualifier.
        """
        username = current_username()
        try:
            guard.assert_table_name(table)
            if not guard.is_visible(table):
                raise ValueError(f"表不可查询: {table}")
            info = metadata.describe_table(table)
        except ValueError as e:
            logger.warning(
                f"[hbaseqa] describe_table 非法表名 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        except Exception as e:
            logger.warning(
                f"[hbaseqa] describe_table 失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        logger.info(f"[hbaseqa] describe_table | 用户: {username} | 表: {info['table']}")
        return {"ok": True, **info}

    @mcp.tool()
    def sample_rows(table: str, limit: int = 5) -> dict:
        """Return a few real rows from a table (max 20).

        Use it to learn actual value shapes such as rowkey format, FREQUENCY codes and
        BUYSELL sides before asking for a specific time window.
        """
        username = current_username()
        try:
            payload = metadata.sample_rows(table, limit)
        except ValueError as e:
            logger.warning(
                f"[hbaseqa] sample_rows 非法表名 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        except Exception as e:
            logger.warning(
                f"[hbaseqa] sample_rows 失败 | 用户: {username} | {guard.exception_text(e)}"
            )
            return {"ok": False, "error": _failure(e)}
        logger.info(
            f"[hbaseqa] sample_rows | 用户: {username} | 表: {table} | "
            f"行数: {payload['row_count']}"
        )
        return {"ok": True, **payload}

    return mcp


def _failure(exc: Exception) -> str:
    """把底层异常翻译成"能据此行动"的错误文案。

    连接类错误（WinError 10053/10054、thrift TTransportException）原本会直接裸奔到
    MCP 客户端，模型只会看到一串 Windows 错误码。这里补上判断依据与下一步动作。
    """
    # 必须用 exception_text：thriftpy2 的异常 __str__ 可能返回 bytes，
    # 直接插值会抛 TypeError 把真实错误盖掉
    text = guard.exception_text(exc)
    hint = client.thrift_version_hint(exc)
    if hint:
        return f"{text}｜{hint}"
    if client.is_connection_error(exc):
        return (
            f"{text}｜HBase Thrift 通道异常（连接被回收或端口/协议不匹配）。"
            "重试一次通常可恢复；持续失败请核对 HBASEQA_THRIFT_HOST/PORT "
            "指向的是 Thrift Server（不是 ZooKeeper 2181），"
            "以及 HBASEQA_THRIFT_TRANSPORT（buffered/framed）与服务端一致"
        )
    return text


def _resolve_tables(
    table: str | None,
    channel: str | None = None,
    instrument: str | None = None,
    datatype: str | None = None,
) -> list[str]:
    """确定要扫哪些表：显式表名优先，否则按三个维度反查。

    维度查不到东西时把可见表清单一并抛回去——模型最常见的错误就是猜表名。
    """
    if table:
        checked = guard.assert_table_name(table)
        if not guard.is_visible(checked):
            raise ValueError(f"表不可查询: {table}")
        return [checked]

    tables = metadata.resolve_tables(channel, instrument, datatype)
    if not tables:
        available = [item["table"] for item in metadata.list_tables()]
        hint = f"，可用表: {available}" if available else ""
        raise ValueError(f"没有匹配 '渠道={channel} 产品={instrument} 类型={datatype}' 的表{hint}")
    return tables
