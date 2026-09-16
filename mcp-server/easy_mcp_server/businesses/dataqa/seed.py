"""创建 XBOND 长表视图（幂等）。

用法：cd mcp-server && uv run python -m easy_mcp_server.businesses.dataqa.seed

只做一件事：在基表 `fmut_mkt_bonddpanyhis` 之上建（或重建）长表视图
`v_xbond_depth`，把 5 档宽表展开成"一行一档"的长表。不建表、不写业务数据、
不迁移任何存量数据。
"""

from __future__ import annotations

import argparse

from easy_mcp_server.db import connection

from easy_mcp_server.businesses.dataqa.xbond import base_table, create_view_sql, depth_view


def base_table_exists(conn, table: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
            (table,),
        )
        return cursor.fetchone() is not None


def ensure_view(conn) -> bool:
    table, view = base_table(), depth_view()
    if not base_table_exists(conn, table):
        print(f"基表 {table} 不存在，跳过建视图（请先导入 XBOND 行情数据）")
        return False

    with conn.cursor() as cursor:
        cursor.execute(create_view_sql(table, view))
    print(f"视图 {view} 已就绪（来源 {table}）")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查基表是否存在，不建视图",
    )
    args = parser.parse_args()

    with connection() as conn:
        if args.check:
            exists = base_table_exists(conn, base_table())
            print(f"基表 {base_table()} 存在: {exists}")
            return
        ensure_view(conn)


if __name__ == "__main__":
    main()
