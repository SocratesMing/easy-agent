"""本地开发用：初始化 mcp_api_keys 表并签发 demo API Key。

生产环境不要用它——正式流程是用户在主应用设置页签发（明文只显示一次，
库里只存 sha256）。这里只是为了本地把 mcp-server 跑起来而造数据。

表结构与主应用 ``easy_agent/db/database.py::init_tables`` 保持一致；
建表语句、表名、Key 前缀都从 :mod:`easy_mcp_server.contract` 取，
本脚本不再自带第二份定义。

用法（在 mcp-server 目录下）：

    uv run python -m scripts.init_keys                      # 建表 + 签发默认 demo key
    uv run python -m scripts.init_keys --only-create-table  # 只建表
    uv run python -m scripts.init_keys -u alice -b strategyqa  # 给指定用户/业务签发
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime

from easy_mcp_server.contract import (
    API_KEY_PREFIX,
    API_KEY_TABLE,
    api_keys_table_sql,
    business_names,
    hash_api_key,
)
from easy_mcp_server.db import connection

UPSERT_KEY = f"""
INSERT INTO {API_KEY_TABLE} (username, business, key_hash, created_at, updated_at, revoked)
VALUES (%s, %s, %s, %s, %s, 0)
ON DUPLICATE KEY UPDATE
    key_hash=VALUES(key_hash), updated_at=VALUES(updated_at), revoked=0
"""

# 默认 demo：(用户名, 业务)。业务名必须来自 businesses/ 下真实存在的包
DEMO_ENTRIES: tuple[tuple[str, str], ...] = (
    ("alice", "strategyqa"),
    ("bob", "strategyqa"),
)


def issue(username: str, business: str, conn) -> str:
    """签发一把 key，返回明文（仅此一次）；重签会使旧 key 失效。"""
    plaintext = API_KEY_PREFIX + secrets.token_urlsafe(32)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn.cursor() as cursor:
        cursor.execute(
            UPSERT_KEY, (username, business, hash_api_key(plaintext), now, now)
        )
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

    known = business_names()
    entries = (
        [(args.username, args.business)]
        if args.username and args.business
        else list(DEMO_ENTRIES)
    )
    # 业务名写错是最常见的坑（签了 key 却连不上，报的还是 401），这里直接拦下
    unknown = sorted({b for _, b in entries if b not in known})
    if unknown:
        print(f"错误：未知业务 {', '.join(unknown)}；可用业务：{', '.join(known)}", file=sys.stderr)
        sys.exit(2)

    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(api_keys_table_sql())
        print(f"表 {API_KEY_TABLE} 已就绪")

        if args.only_create_table:
            return

        issued = [(u, b, issue(u, b, conn)) for u, b in entries]

    print("\n以下明文仅显示一次（库中只存 sha256），请自行保存：\n")
    for username, business, plaintext in issued:
        print(f"  {username:<8} {business:<8} {plaintext}")
    print("\n用法：Authorization: Bearer <上面的明文>")


if __name__ == "__main__":
    main()
