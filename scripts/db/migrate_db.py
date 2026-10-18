#!/usr/bin/env python3
"""一次性数据库迁移入口（多副本 Phase 3）。

用于替代「每个 Pod 启动都跑 DDL」：

    python scripts/db/migrate_db.py            # 执行迁移（内部持有跨进程迁移锁）
    python scripts/db/migrate_db.py --check    # 只校验结构版本（不迁移）

K8s 中可作为 Job/initContainer 执行；Pod 设 `EASY_AUTO_MIGRATE=0` 后启动只
校验，结构不匹配会 fail-fast 并提示本脚本。
"""

from __future__ import annotations

import argparse
import os
import sys

from easy_agent.db import init_database
from easy_agent.initialization import initialize_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description="Easy Agent database migrate/check")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验 schema_version/关键表，不执行迁移",
    )
    args = parser.parse_args()

    runtime = initialize_runtime()
    config = runtime.config
    if config is None:
        print("ERROR: 配置未加载，无法迁移数据库", file=sys.stderr)
        return 2

    db_config = config.database.model_dump()
    if args.check:
        os.environ["EASY_AUTO_MIGRATE"] = "0"
        db = init_database(db_config)
        print(f"[check] schema OK | type={db.db_type}")
        db.close()
        return 0

    os.environ["EASY_AUTO_MIGRATE"] = "1"
    db = init_database(db_config)
    print(f"[migrate] schema OK | type={db.db_type}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
