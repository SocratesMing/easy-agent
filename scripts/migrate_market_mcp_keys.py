"""一次性迁移：market_mcp_api_keys -> mcp_api_keys（business='market'）。

背景：MCP API Key 从"子项目自管"改为"主应用统一签发"（统一表 mcp_api_keys，
含 business 列）。旧 key 哈希仍然有效——哈希算法未变（sha256），只需搬行。

用法：python scripts/migrate_market_mcp_keys.py [--drop-old]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from easy_agent.db import get_database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drop-old",
        action="store_true",
        help="迁移成功后删除旧的 market_mcp_api_keys 表",
    )
    args = parser.parse_args()

    db = get_database()
    if db.db_type != "mysql":
        print("旧 key 表只存在于 MySQL，当前数据库不是 MySQL，无需迁移")
        return

    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT username, api_key_hash, created_at, updated_at, revoked "
            "FROM market_mcp_api_keys"
        )
        rows = cursor.fetchall()
        print(f"旧表 market_mcp_api_keys 共 {len(rows)} 行")

        for row in rows:
            row = dict(row) if isinstance(row, dict) else dict(zip(
                ("username", "api_key_hash", "created_at", "updated_at", "revoked"), row
            ))
            cursor.execute(
                """
                INSERT INTO mcp_api_keys
                    (username, business, key_hash, created_at, updated_at, revoked)
                VALUES (%s, 'market', %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    key_hash=VALUES(key_hash), updated_at=VALUES(updated_at), revoked=VALUES(revoked)
                """,
                (
                    row["username"],
                    row["api_key_hash"],
                    row["created_at"],
                    row["updated_at"],
                    row["revoked"],
                ),
            )
        conn.commit()
        print(f"已迁移 {len(rows)} 行到 mcp_api_keys（business='market'）")

        if args.drop_old:
            cursor.execute("DROP TABLE IF EXISTS market_mcp_api_keys")
            conn.commit()
            print("已删除旧表 market_mcp_api_keys")
        else:
            print("旧表保留，确认无误后可手动删除（或加 --drop-old）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
