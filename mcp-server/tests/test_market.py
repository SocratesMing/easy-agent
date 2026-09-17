"""Market 业务测试：意图解析、SQL 参数化、用户数据隔离。"""

from __future__ import annotations

import asyncio

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import easy_mcp_server.businesses.market.server as market_server

from easy_mcp_server.app import create_app
from easy_mcp_server.businesses.market.seed import (
    demo_market_rows,
    demo_position_rows,
)
from easy_mcp_server.businesses.market.server import (
    _format_positions,
    _position_rows,
    _quote_rows,
    build,
    parse_market_question,
)


# ── 意图解析 ────────────────────────────────────────────────────────────


def test_parse_detects_positions_intent():
    parsed = parse_market_question("查看我的持仓")
    assert parsed["intent"] == "positions"
    assert parsed["category"] == "positions"


def test_parse_detects_precious_metals():
    parsed = parse_market_question("现在黄金多少钱")
    assert parsed["category"] == "precious_metals"
    assert "XAUUSD" in parsed["symbols"]


def test_parse_detects_forex_by_chinese_keyword():
    parsed = parse_market_question("美元兑人民币汇率是多少")
    assert parsed["category"] == "forex"
    assert "USDCNH" in parsed["symbols"]


def test_parse_defaults_to_all_quotes():
    parsed = parse_market_question("今天市场怎么样")
    assert parsed["category"] == "all"
    assert parsed["symbols"] == []


# ── SQL 构造（参数化，无注入面） ────────────────────────────────────────


def test_quote_rows_filters_by_category_and_symbols(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params=()):
        captured["sql"] = sql
        captured["params"] = tuple(params)
        return []

    monkeypatch.setattr(market_server, "fetch_all", fake_fetch_all)
    _quote_rows("forex", ["EURUSD", "USDJPY"])

    assert "category=%s" in captured["sql"]
    assert "symbol IN (%s,%s)" in captured["sql"]
    assert captured["params"] == ("forex", "EURUSD", "USDJPY")


def test_quote_rows_without_filters(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params=()):
        captured["sql"] = sql
        captured["params"] = tuple(params)
        return []

    monkeypatch.setattr(market_server, "fetch_all", fake_fetch_all)
    _quote_rows()
    assert "WHERE" not in captured["sql"]
    assert captured["params"] == ()


def test_position_rows_scoped_to_user(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params=()):
        captured["sql"] = sql
        captured["params"] = tuple(params)
        return []

    monkeypatch.setattr(market_server, "fetch_all", fake_fetch_all)
    _position_rows("alice")
    assert "username=%s" in captured["sql"]
    assert captured["params"] == ("alice",)


def test_format_positions_empty():
    assert "没有持仓" in _format_positions("alice", [])


def test_format_positions_summary():
    text = _format_positions(
        "alice",
        [
            {"symbol": "XAUUSD", "side": "long", "quantity": 1, "unrealized_pnl": 12.5},
            {"symbol": "EURUSD", "side": "short", "quantity": 2, "unrealized_pnl": -3},
        ],
    )
    assert "alice" in text and "2 笔持仓" in text and "9.50" in text


# ── 种子数据 ────────────────────────────────────────────────────────────


def test_demo_market_rows_include_gold_and_usdcnh():
    symbols = {quote["symbol"] for quote in demo_market_rows()}
    assert "XAUUSD" in symbols
    assert "USDCNH" in symbols


def test_demo_position_rows_are_user_scoped():
    usernames = {position["username"] for position in demo_position_rows()}
    assert usernames == {"szm", "zr6"}


# ── 端到端：通过 MCP 客户端验证工具与用户隔离 ──────────────────────────


@pytest.fixture
async def market_env(monkeypatch):
    """挂载真实 market 业务，数据库查询替换为内存行。"""
    rows: dict[str, list[dict]] = {"positions": [], "quotes": []}

    def fake_fetch_all(sql, params=()):
        if "market_positions" in sql:
            username = params[0]
            return [r for r in rows["positions"] if r["username"] == username]
        return list(rows["quotes"])

    monkeypatch.setattr(market_server, "fetch_all", fake_fetch_all)

    verifier = lambda business, key: {"mk-alice": "alice", "mk-bob": "bob"}.get(key)
    app = create_app(verifier, businesses_override=[("market", build())])
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]

    class Holder:
        base = f"http://127.0.0.1:{port}"

    Holder.rows = rows
    try:
        yield Holder
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


async def _call_tool(base: str, key: str, tool: str, args: dict | None = None):
    async with streamablehttp_client(
        f"{base}/mcp/market/", headers={"Authorization": f"Bearer {key}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, args or {})


async def test_get_my_positions_scoped_to_api_key_owner(market_env):
    market_env.rows["positions"] = [
        {"username": "alice", "symbol": "XAUUSD", "side": "long", "quantity": 1,
         "entry_price": 2000, "mark_price": 2010, "unrealized_pnl": 10, "updated_at": "2026-09-04"},
        {"username": "bob", "symbol": "EURUSD", "side": "short", "quantity": 2,
         "entry_price": 1.1, "mark_price": 1.08, "unrealized_pnl": 4, "updated_at": "2026-09-04"},
    ]
    result = await _call_tool(market_env.base, "mk-alice", "get_my_positions")
    text = result.content[0].text
    assert "XAUUSD" in text
    assert "EURUSD" not in text


async def test_ask_market_returns_quotes(market_env):
    market_env.rows["quotes"] = [
        {"symbol": "XAUUSD", "name": "黄金", "category": "precious_metals",
         "price": 2010.5, "change_percent": 0.5, "updated_at": "2026-09-04 10:00:00"}
    ]
    result = await _call_tool(market_env.base, "mk-alice", "ask_market",
                              {"question": "现在黄金多少钱"})
    text = result.content[0].text
    assert "XAUUSD" in text
