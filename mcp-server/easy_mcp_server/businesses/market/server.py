"""Market 行情与持仓 MCP 工具。

不做 SQL 注入面：不接收任意 SQL，所有数据库访问都是固定的参数化语句。
用户身份由鉴权中间件解析 API Key 后注入，业务代码只通过
:func:`easy_mcp_server.context.current_username` 获取。

错误语义（FW-14）：数据源/查询失败一律抛 :class:`ToolError`（FastMCP 输出
``isError=true``），对外只给稳定错误码 + 人类可读说明；异常原文只进服务端日志。
空结果是合法成功（``isError=false``），不要用错误文案冒充结果。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...context import current_username
from ...db import fetch_all
from ...errors import tool_failure

PRECIOUS_METAL_SYMBOLS = {
    "gold": "XAUUSD",
    "黄金": "XAUUSD",
    "silver": "XAGUSD",
    "白银": "XAGUSD",
    "platinum": "XPTUSD",
    "铂金": "XPTUSD",
    "palladium": "XPAUSD",
    "钯金": "XPAUSD",
}

FOREX_SYMBOLS = {
    "usdcnh": "USDCNH",
    "美元兑人民币": "USDCNH",
    "人民币": "USDCNH",
    "eurusd": "EURUSD",
    "欧元": "EURUSD",
    "usdjpy": "USDJPY",
    "日元": "USDJPY",
    "gbpusd": "GBPUSD",
    "英镑": "GBPUSD",
    "audusd": "AUDUSD",
    "澳元": "AUDUSD",
    "usdchf": "USDCHF",
    "瑞郎": "USDCHF",
}

POSITION_TERMS = ("持仓", "头寸", "仓位")
_PRECIOUS_TERMS = ("贵金属", "黄金", "白银", "铂金", "钯金", "gold", "silver")
_FOREX_TERMS = ("外汇", "汇率", "美元", "人民币", "欧元", "日元", "英镑", "澳元", "瑞郎")


def _symbols_from_text(text: str, mapping: dict[str, str]) -> list[str]:
    symbols: list[str] = []
    lowered = text.lower()
    for keyword, symbol in mapping.items():
        if keyword in lowered and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def parse_market_question(question: str) -> dict[str, Any]:
    """把自然语言问题解析为查询意图。"""
    text = question.strip()
    lowered = text.lower()
    if any(term in text for term in POSITION_TERMS):
        return {"category": "positions", "symbols": [], "intent": "positions"}
    if any(term in text or term in lowered for term in _PRECIOUS_TERMS):
        return {
            "category": "precious_metals",
            "symbols": _symbols_from_text(text, PRECIOUS_METAL_SYMBOLS),
            "intent": "quotes",
        }
    if any(term in text or term in lowered for term in _FOREX_TERMS):
        return {
            "category": "forex",
            "symbols": _symbols_from_text(text, FOREX_SYMBOLS),
            "intent": "quotes",
        }
    return {"category": "all", "symbols": [], "intent": "quotes"}


def _quote_rows(
    category: str | None = None, symbols: list[str] | None = None
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if category and category != "all":
        conditions.append("category=%s")
        params.append(category)
    if symbols:
        placeholders = ",".join(["%s"] * len(symbols))
        conditions.append(f"symbol IN ({placeholders})")
        params.extend(symbols)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return fetch_all(
        "SELECT symbol, name, category, price, change_percent, updated_at "
        f"FROM market_quotes {where_clause} ORDER BY symbol",
        tuple(params),
    )


def _position_rows(username: str) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT symbol, side, quantity, entry_price, mark_price, "
        "unrealized_pnl, updated_at "
        "FROM market_positions WHERE username=%s "
        "ORDER BY updated_at DESC",
        (username,),
    )


def _format_quote(quote: dict[str, Any]) -> str:
    return (
        f"{quote['symbol']}（{quote['name']}）最新价 {quote['price']}，"
        f"涨跌幅 {quote['change_percent']}%，更新时间 {quote['updated_at']}"
    )


def _format_positions(username: str, positions: list[dict[str, Any]]) -> str:
    if not positions:
        return f"{username} 当前没有持仓。"
    total_pnl = sum(float(p.get("unrealized_pnl", 0)) for p in positions)
    details = "; ".join(
        f"{p['symbol']} {p['side']} {p['quantity']}，浮动盈亏 {p['unrealized_pnl']}"
        for p in positions
    )
    return f"{username} 当前共 {len(positions)} 笔持仓，总浮动盈亏 {total_pnl:.2f}。{details}"


def build() -> FastMCP:
    mcp = FastMCP(
        "market",
        instructions=(
            "Use ask_market for natural-language market questions. "
            "Use get_latest_quotes for explicit quote queries and get_my_positions "
            "for the authenticated user's positions."
        ),
        streamable_http_path="/",
        stateless_http=True,
    )

    @mcp.tool(structured_output=True)
    def ask_market(question: str) -> dict[str, Any]:
        """Answer a natural-language market question with the latest data."""
        username = current_username()
        parsed = parse_market_question(question)

        if parsed["intent"] == "positions":
            try:
                positions = _position_rows(username)
                answer = _format_positions(username, positions)
            except Exception as exc:  # noqa: BLE001 - 统一转 MCP 错误语义
                raise tool_failure(
                    "market_positions_unavailable",
                    "持仓数据暂时不可用，请稍后重试",
                    exc,
                    tool="ask_market",
                ) from None
            return {
                "category": "positions",
                "positions": positions,
                "answer": answer,
            }

        try:
            quotes = _quote_rows(parsed["category"], parsed["symbols"])
            if not quotes:
                answer = "暂无匹配的行情数据。"
            elif len(quotes) == 1:
                answer = _format_quote(quotes[0])
            else:
                answer = "；".join(_format_quote(q) for q in quotes)
        except Exception as exc:  # noqa: BLE001 - 统一转 MCP 错误语义
            raise tool_failure(
                "market_quotes_unavailable",
                "行情数据暂时不可用，请稍后重试",
                exc,
                tool="ask_market",
            ) from None
        return {"category": parsed["category"], "quotes": quotes, "answer": answer}

    @mcp.tool(structured_output=True)
    def get_latest_quotes(
        category: str | None = None, symbols: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Get the latest market quotes, optionally filtered by category/symbol."""
        current_username()
        try:
            return _quote_rows(category, symbols)
        except Exception as exc:  # noqa: BLE001 - 统一转 MCP 错误语义
            raise tool_failure(
                "market_quotes_unavailable",
                "行情数据暂时不可用，请稍后重试",
                exc,
                tool="get_latest_quotes",
            ) from None

    @mcp.tool(structured_output=True)
    def get_my_positions() -> list[dict[str, Any]]:
        """Get positions for the user identified by the API key."""
        username = current_username()
        try:
            return _position_rows(username)
        except Exception as exc:  # noqa: BLE001 - 统一转 MCP 错误语义
            raise tool_failure(
                "market_positions_unavailable",
                "持仓数据暂时不可用，请稍后重试",
                exc,
                tool="get_my_positions",
            ) from None

    return mcp
