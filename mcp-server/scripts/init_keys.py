"""本地开发用：初始化 mcp_api_keys 表并签发 demo API Key。

生产环境不要用它——正式流程是用户在主应用设置页签发（明文只显示一次，
库里只存 sha256）。这里只是为了本地把 mcp-server 跑起来而造数据。

表结构与主应用 `easy_agent/db/database.py::init_tables` 保持一致，避免两边分叉。

用法（在 mcp-server 目录下）：

    uv run python -m scripts.init_keys                      # 建表 + 签发默认 demo key
    uv run python -m scripts.init_keys --only-create-table  # 只建表
    uv run python -m scripts.init_keys -u alice -b dataqa   # 给指定用户/业务签发
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
from datetime import datetime

from easy_mcp_server.db import connection

KEY_PREFIX = "mcp_"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS mcp_api_keys (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(64) NOT NULL,
    business VARCHAR(64) NOT NULL,
    key_hash CHAR(64) NOT NULL UNIQUE,
    created_at VARCHAR(50) NOT NULL,
    updated_at VARCHAR(50) NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    UNIQUE KEY uk_mcp_api_keys_user_business (username, business)
)
"""

UPSERT_KEY = """
INSERT INTO mcp_api_keys (username, business, key_hash, created_at, updated_at, revoked)
VALUES (%s, %s, %s, %s, %s, 0)
ON DUPLICATE KEY UPDATE
    key_hash=VALUES(key_hash), updated_at=VALUES(updated_at), revoked=0
"""

# 默认 demo：(用户名, 业务)。业务名必须与 businesses/ 下的包名一致
DEMO_ENTRIES: tuple[tuple[str, str], ...] = (
    ("alice", "dataqa"),
    ("bob", "dataqa"),
    ("alice", "market"),
)


def issue(username: str, business: str, conn) -> str:
    """签发一把 key，返回明文（仅此一次）；重签会使旧 key 失效。"""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn.cursor() as cursor:
        cursor.execute(UPSERT_KEY, (username, business, key_hash, now, now))
    return plaintext


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-u", "--username", help="只给该用户签发（需配合 -b）")
    parser.add_argument("-b", "--business", help="只签发该业务（需配合 -u）")
    parser.add_argument(
        "--only-create-table", action="store_true", help="只建表，不签发 key"
    )
    args = parser.parse_args()

    if bool(args.username) ^ bool(args.business):
        parser.error("-u 与 -b 必须同时给出")

    entries = (
        [(args.username, args.business)]
        if args.username and args.business
        else list(DEMO_ENTRIES)
    )

    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_TABLE)
        print("表 mcp_api_keys 已就绪")

        if args.only_create_table:
            return

        issued = [(u, b, issue(u, b, conn)) for u, b in entries]

    print("\n以下明文仅显示一次（库中只存 sha256），请自行保存：\n")
    for username, business, plaintext in issued:
        print(f"  {username:<8} {business:<8} {plaintext}")
    print("\n用法：Authorization: Bearer <上面的明文>")


if __name__ == "__main__":
    main()
