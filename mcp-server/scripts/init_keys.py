"""mcp_api_keys 表的建表与 API Key 全生命周期管理。

明文只在签发时显示一次，库里只存 sha256，校验由 :mod:`easy_mcp_server.keys`
负责。主应用设置页也有一套签发（解耦完成前并存），但本脚本**不依赖主应用**，
可独立完成建表 / 签发 / 吊销 / 查看。

建表语句、表名、Key 前缀都从 :mod:`easy_mcp_server.contract` 取——表定义属于
mcp-server 自己的对外契约，本脚本与主应用同源同构，不再自带第二份定义。

用法（在 mcp-server 目录下）：

    uv run python -m scripts.init_keys                      # 建表 + 签发默认 demo key
    uv run python -m scripts.init_keys --only-create-table  # 只建表
    uv run python -m scripts.init_keys -u alice -b strategyqa  # 给指定用户/业务签发
    uv run python -m scripts.init_keys --list               # 查看全部 key 状态
    uv run python -m scripts.init_keys -u alice --list      # 只看某用户
    uv run python -m scripts.init_keys -u alice -b strategyqa --revoke  # 吊销
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
from easy_mcp_server.db import connection, fetch_all

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


def revoke(username: str, business: str, conn) -> int:
    """吊销指定用户+业务的 key，返回受影响行数（0 表示本来就没有生效中的 key）。

    校验侧有 60s TTL 缓存，最迟 1 分钟后旧 key 才会真正被拒。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE {API_KEY_TABLE} SET revoked=1, updated_at=%s "
            "WHERE username=%s AND business=%s AND revoked=0",
            (now, username, business),
        )
        return int(cursor.rowcount or 0)


def list_keys(username: str | None = None) -> list[dict]:
    """列出 key 状态（不含明文与哈希——库里本来也只有 sha256）。"""
    sql = (
        f"SELECT username, business, created_at, updated_at, revoked "
        f"FROM {API_KEY_TABLE}"
    )
    params: tuple = ()
    if username:
        sql += " WHERE username=%s"
        params = (username,)
    sql += " ORDER BY username, business"
    return fetch_all(sql, params)


def print_key_status(rows: list[dict], username: str | None = None) -> None:
    """打印 key 状态表；只给状态，不回显明文与哈希（库里也没有）。"""
    scope = f"用户 {username}" if username else "全部用户"
    if not rows:
        print(f"{scope}：没有任何 key 记录")
        return

    print(f"{scope}的 key 状态（共 {len(rows)} 条）：\n")
    print(f"  {'用户名':<12}{'业务':<20}{'状态':<8}最近更新")
    for row in rows:
        state = "已吊销" if int(row.get("revoked") or 0) == 1 else "生效中"
        updated = row.get("updated_at") or row.get("created_at") or "-"
        print(
            f"  {str(row.get('username')):<12}{str(row.get('business')):<20}"
            f"{state:<8}{updated}"
        )
    print("\n明文只在签发时显示一次，库里只有 sha256，无法找回；")
    print("需要新 key 就重签（旧的自动作废）：")
    print("  uv run python -m scripts.init_keys -u <用户名> -b <业务>")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-u", "--username", help="指定用户（签发/吊销需配合 -b）")
    parser.add_argument("-b", "--business", help="指定业务（需配合 -u）")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--only-create-table", action="store_true", help="只建表，不签发 key"
    )
    action.add_argument(
        "--revoke", action="store_true", help="吊销指定用户+业务的 key（需 -u -b）"
    )
    action.add_argument(
        "--list", action="store_true", help="查看 key 状态（可配合 -u 只看该用户）"
    )
    args = parser.parse_args()

    # --list 是只读的，允许只给 -u 甚至都不给
    if args.list:
        print_key_status(list_keys(args.username), args.username)
        return

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

        if args.revoke:
            affected = revoke(args.username, args.business, conn)
            if affected:
                print(f"已吊销 {args.username} / {args.business} 的 key")
                print("提示：校验侧有 60s TTL 缓存，最迟 1 分钟后旧 key 才真正被拒。")
            else:
                print(
                    f"没有生效中的 key：{args.username} / {args.business}（无需吊销）"
                )
            return

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
