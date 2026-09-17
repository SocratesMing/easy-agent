"""初始化 market 业务表并灌入演示数据。

用法：cd mcp-server && uv run python -m easy_mcp_server.businesses.market.seed

API Key 的签发已移交给主应用（设置页），本脚本只负责业务数据表。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from easy_mcp_server.db import connection


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def demo_market_rows() -> list[dict[str, Any]]:
    updated_at = _now()
    rows = [
        ("XAUUSD", "黄金/美元", "precious_metals", 2385.4, 0.42),
        ("XAGUSD", "白银/美元", "precious_metals", 29.1, 0.85),
        ("XPTUSD", "铂金/美元", "precious_metals", 975.2, -0.22),
        ("XPAUSD", "钯金/美元", "precious_metals", 1024.6, 0.31),
        ("USDCNH", "美元/人民币", "forex", 7.16, -0.12),
        ("EURUSD", "欧元/美元", "forex", 1.085, 0.18),
        ("USDJPY", "美元/日元", "forex", 149.32, 0.26),
        ("GBPUSD", "英镑/美元", "forex", 1.272, -0.09),
        ("AUDUSD", "澳元/美元", "forex", 0.668, 0.14),
        ("USDCHF", "美元/瑞郎", "forex", 0.879, 0.07),
    ]
    return [
        {
            "symbol": symbol,
            "name": name,
            "category": category,
            "price": price,
            "change_percent": change_percent,
            "updated_at": updated_at,
        }
        for symbol, name, category, price, change_percent in rows
    ]


def demo_position_rows() -> list[dict[str, Any]]:
    updated_at = _now()
    rows = [
        ("szm", "XAUUSD", "long", 0.5, 2350.0, 2385.4, 17.7),
        ("szm", "USDCNH", "short", 10000.0, 7.18, 7.16, 200.0),
        ("zr6", "XAGUSD", "long", 100.0, 28.2, 29.1, 90.0),
        ("zr6", "EURUSD", "short", 5000.0, 1.09, 1.085, 25.0),
    ]
    return [
        {
            "username": username,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "mark_price": mark_price,
            "unrealized_pnl": unrealized_pnl,
            "updated_at": updated_at,
        }
        for username, symbol, side, quantity, entry_price, mark_price, unrealized_pnl in rows
    ]


def ensure_schema(connection_ctx) -> None:
    with connection_ctx.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS market_quotes (
                symbol VARCHAR(16) PRIMARY KEY,
                name VARCHAR(64) NOT NULL,
                category VARCHAR(32) NOT NULL,
                price DECIMAL(18,6) NOT NULL,
                change_percent DECIMAL(8,4) NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS market_positions (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                symbol VARCHAR(16) NOT NULL,
                side VARCHAR(8) NOT NULL,
                quantity DECIMAL(18,6) NOT NULL,
                entry_price DECIMAL(18,6) NOT NULL,
                mark_price DECIMAL(18,6) NOT NULL,
                unrealized_pnl DECIMAL(18,6) NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE KEY uq_market_positions_user_symbol_side (username, symbol, side),
                INDEX idx_market_positions_username (username)
            )
            """
        )


def seed_market_data(connection_ctx) -> None:
    with connection_ctx.cursor() as cursor:
        for quote in demo_market_rows():
            cursor.execute(
                """
                INSERT INTO market_quotes
                    (symbol, name, category, price, change_percent, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name=VALUES(name),
                    category=VALUES(category),
                    price=VALUES(price),
                    change_percent=VALUES(change_percent),
                    updated_at=VALUES(updated_at)
                """,
                (
                    quote["symbol"],
                    quote["name"],
                    quote["category"],
                    quote["price"],
                    quote["change_percent"],
                    quote["updated_at"],
                ),
            )
        for position in demo_position_rows():
            cursor.execute(
                """
                INSERT INTO market_positions
                    (username, symbol, side, quantity, entry_price, mark_price,
                     unrealized_pnl, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    quantity=VALUES(quantity),
                    entry_price=VALUES(entry_price),
                    mark_price=VALUES(mark_price),
                    unrealized_pnl=VALUES(unrealized_pnl),
                    updated_at=VALUES(updated_at)
                """,
                (
                    position["username"],
                    position["symbol"],
                    position["side"],
                    position["quantity"],
                    position["entry_price"],
                    position["mark_price"],
                    position["unrealized_pnl"],
                    position["updated_at"],
                ),
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Only create tables, do not insert demo rows",
    )
    args = parser.parse_args()

    with connection() as conn:
        ensure_schema(conn)
        if not args.skip_seed:
            seed_market_data(conn)
    print("market 表已就绪")


if __name__ == "__main__":
    main()
