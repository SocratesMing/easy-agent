"""下线一个已移除的业务：清理共享库里的注册记录与残留 API Key。

删除业务包（``businesses/<name>/``）之后，共享库里还会留下两类残留，
它们都不会自动消失：

1. ``mcp_businesses`` 的登记记录 —— ``registry.publish()`` 只做 upsert、
   **从不删除**，不清就会让主应用设置页一直显示已下线的业务，
   用户还能对它签发一把永远用不了的 Key
2. ``mcp_api_keys`` 的密钥记录 —— 业务已不存在，这些 Key 无法通过任何
   URL 使用，但它们会一直留在库里，且看起来是"正常未吊销"状态

本脚本把这两步做成一个显式动作。刻意**不做成 publish() 的自动行为**：
多实例部署（多 pod / 主备中心）滚动更新时版本不一致，
"某实例发现业务少了就删"会误删其他版本实例刚登记的业务。

用法（在 mcp-server 目录下）：

    uv run python -m scripts.retire_business oldbiz            # 预览（缺省不删）
    uv run python -m scripts.retire_business oldbiz --apply    # 实际执行
    uv run python -m scripts.retire_business oldbiz --apply --keep-keys  # 只清注册表
"""

from __future__ import annotations

import argparse
import sys

from easy_mcp_server.contract import (
    API_KEY_TABLE,
    BUSINESS_REGISTRY_TABLE,
    business_names,
)
from easy_mcp_server.db import connection, fetch_all

# (表名, 标记业务归属的列名) —— 两张表里这个列的名字不同
TARGETS: tuple[tuple[str, str], ...] = (
    (BUSINESS_REGISTRY_TABLE, "name"),
    (API_KEY_TABLE, "business"),
)


def _count(table: str, column: str, business: str) -> int:
    """统计待删行数；表不存在或读失败都返回 0（首次部署可能还没建表）。"""
    try:
        rows = fetch_all(f"SELECT COUNT(*) AS c FROM {table} WHERE {column}=%s", (business,))
    except Exception:
        return 0
    return int(rows[0]["c"]) if rows else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清理已下线业务在共享库里的注册记录与 API Key",
    )
    parser.add_argument("business", help="业务名（即已删除的 businesses/<name>/ 包名）")
    parser.add_argument("--apply", action="store_true", help="实际执行删除（缺省只预览）")
    parser.add_argument(
        "--keep-keys",
        action="store_true",
        help=f"保留 {API_KEY_TABLE} 记录，只清注册表",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使 businesses/ 下仍有该业务包也执行（默认拒绝，防止误清在线业务）",
    )
    args = parser.parse_args(argv)

    business = args.business.strip()
    if not business:
        parser.error("业务名不能为空")

    # 护栏：业务包还在，说明代码没删干净。此时清注册表会造成
    # "服务仍在提供该业务，但设置页看不到、无法签发 Key" 的错位状态。
    if business in business_names() and not args.force:
        print(
            f"错误：businesses/ 下仍存在业务包 {business}。\n"
            f"      请先删除该目录；若确认要清理，加 --force。",
            file=sys.stderr,
        )
        return 2

    targets = list(TARGETS)
    if args.keep_keys:
        targets = [item for item in targets if item[0] != API_KEY_TABLE]

    counts = [(table, _count(table, column, business)) for table, column in targets]
    total = sum(count for _, count in counts)
    if not total:
        print(f"业务 {business}：共享库中无残留记录，无需清理。")
        return 0

    print(f"业务 {business} 的残留记录：")
    for table, count in counts:
        print(f"  {table:<20} {count} 行")

    if not args.apply:
        print("\n以上为预览。确认无误后加 --apply 实际执行。")
        return 0

    with connection() as conn:
        with conn.cursor() as cursor:
            for table, column in targets:
                cursor.execute(f"DELETE FROM {table} WHERE {column}=%s", (business,))

    print(f"\n已清理业务 {business} 的残留记录（共 {total} 行）。")
    print("提示：主应用设置页的业务列表读自 mcp_businesses，刷新页面即可看到变化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
