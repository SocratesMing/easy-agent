"""Market data MCP server example.

The server exposes market quotes and per-user positions through MCP tools.
It intentionally does not accept SQL from the agent; all database access is
performed with fixed, parameterized statements.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime
from contextlib import contextmanager
from typing import Any, Iterable

import pymysql
from mcp.server.fastmcp import FastMCP

from easy_agent.config import Config
from easy_agent.utils.env_loader import load_project_env


logger = logging.getLogger(__name__)


mcp = FastMCP(
    "market-data",
    instructions=(
        "Use ask_market for natural-language market questions. "
        "Use get_latest_quotes for explicit quote queries and get_my_positions "
        "for the authenticated user's positions."
    ),
)


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


def _mysql_config(mysql_config: dict[str, Any] | None = None) -> dict[str, Any]:
    if mysql_config is None:
        try:
            app_config = Config.load()
            if app_config.database.type == "mysql":
                mysql_config = app_config.database.mysql.model_dump()
        except Exception as e:
            logger.warning(f"读取主应用 MySQL 配置失败，回退到 .env: {e}")

    if mysql_config is not None:
        config = {
            key: mysql_config[key]
            for key in (
                "host",
                "port",
                "user",
                "password",
                "database",
                "charset",
                "connect_timeout",
                "read_timeout",
                "write_timeout",
            )
            if key in mysql_config
        }
    else:
        load_project_env()
        config = {
            "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("MYSQL_PORT", "3306")),
            "user": os.environ.get("MYSQL_USER", "root"),
            "password": os.environ.get("MYSQL_PASSWORD", ""),
            "database": os.environ.get("MYSQL_DATABASE", "agent"),
        }

    config.setdefault("charset", "utf8mb4")
    config["cursorclass"] = pymysql.cursors.DictCursor
    config["autocommit"] = True
    return config


@contextmanager
def _get_connection(mysql_config: dict[str, Any] | None = None):
    connection = pymysql.connect(**_mysql_config(mysql_config))
    try:
        yield connection
    finally:
        connection.close()


def _fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with _get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return list(cursor.fetchall())


def _fetch_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with _get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return cursor.fetchone()


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def authenticate_api_key(api_key: str) -> str:
    row = _fetch_one(
        "SELECT username FROM market_mcp_api_keys "
        "WHERE api_key_hash=%s AND revoked=0",
        (hash_api_key(api_key),),
    )
    if not row:
        raise PermissionError("Invalid MARKET_MCP_API_KEY")
    return str(row["username"])


def issue_api_key(username: str, mysql_config: dict[str, Any] | None = None) -> str:
    """Issue a new API key for a user and replace any existing key."""
    api_key = secrets.token_urlsafe(32)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _get_connection(mysql_config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS market_mcp_api_keys (
                    username VARCHAR(64) PRIMARY KEY,
                    api_key_hash CHAR(64) NOT NULL UNIQUE,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    revoked TINYINT(1) NOT NULL DEFAULT 0
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO market_mcp_api_keys
                    (username, api_key_hash, created_at, updated_at, revoked)
                VALUES (%s, %s, %s, %s, 0)
                ON DUPLICATE KEY UPDATE
                    api_key_hash=VALUES(api_key_hash),
                    updated_at=VALUES(updated_at),
                    revoked=0
                """,
                (username, hash_api_key(api_key), now, now),
            )
    return api_key


def _current_username() -> str:
    api_key = os.environ.get("MARKET_MCP_API_KEY", "")
    return authenticate_api_key(api_key)


def _symbols_from_text(text: str, mapping: dict[str, str]) -> list[str]:
    symbols: list[str] = []
    lowered = text.lower()
    for keyword, symbol in mapping.items():
        if keyword in lowered and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def parse_market_question(question: str) -> dict[str, Any]:
    text = question.strip()
    lowered = text.lower()
    position_terms = ("持仓", "头寸", "仓位")
    if any(term in text for term in position_terms):
        return {"category": "positions", "symbols": [], "intent": "positions"}

    precious_terms = ("贵金属", "黄金", "白银", "铂金", "钯金", "gold", "silver")
    forex_terms = ("外汇", "汇率", "美元", "人民币", "欧元", "日元", "英镑", "澳元", "瑞郎")
    if any(term in text or term in lowered for term in precious_terms):
        return {
            "category": "precious_metals",
            "symbols": _symbols_from_text(text, PRECIOUS_METAL_SYMBOLS),
            "intent": "quotes",
        }
    if any(term in text or term in lowered for term in forex_terms):
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
    return _fetch_all(
        "SELECT symbol, name, category, price, change_percent, updated_at "
        f"FROM market_quotes {where_clause} ORDER BY symbol",
        params,
    )


def _position_rows(username: str) -> list[dict[str, Any]]:
    return _fetch_all(
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
    total_pnl = sum(float(position.get("unrealized_pnl", 0)) for position in positions)
    details = "; ".join(
        f"{position['symbol']} {position['side']} {position['quantity']}，"
        f"浮动盈亏 {position['unrealized_pnl']}"
        for position in positions
    )
    return f"{username} 当前共 {len(positions)} 笔持仓，总浮动盈亏 {total_pnl:.2f}。{details}"


@mcp.tool()
def ask_market(question: str) -> dict[str, Any]:
    """Answer a natural-language market question with the latest data."""
    username = _current_username()
    parsed = parse_market_question(question)

    if parsed["intent"] == "positions":
        positions = _position_rows(username)
        return {
            "category": "positions",
            "positions": positions,
            "answer": _format_positions(username, positions),
        }

    quotes = _quote_rows(parsed["category"], parsed["symbols"])
    if not quotes:
        answer = "暂无匹配的行情数据。"
    elif len(quotes) == 1:
        answer = _format_quote(quotes[0])
    else:
        answer = "；".join(_format_quote(quote) for quote in quotes)
    return {
        "category": parsed["category"],
        "quotes": quotes,
        "answer": answer,
    }


@mcp.tool()
def get_latest_quotes(
    category: str | None = None, symbols: list[str] | None = None
) -> list[dict[str, Any]]:
    """Get the latest market quotes, optionally filtered by category/symbol."""
    _current_username()
    return _quote_rows(category, symbols)


@mcp.tool()
def get_my_positions() -> list[dict[str, Any]]:
    """Get positions for the user identified by MARKET_MCP_API_KEY."""
    username = _current_username()
    return _position_rows(username)


if __name__ == "__main__":
    mcp.run()
