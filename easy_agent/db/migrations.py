"""版本化数据库迁移（ARCH-16）。

背景：当前 `Database.init_tables()` 依赖「单版本号 + 幂等 DDL」快速路径，适合
建表/补列，但缺少**可审计、可回滚的按版本迁移记录**。本模块提供最小迁移框架：

- `schema_migrations` 表记录已应用版本（version PRIMARY KEY, description, applied_at）；
- 首次启用时写入 baseline（当前 SCHEMA_VERSION），避免把历史 DDL 重放一遍；
- 后续破坏性/数据型变更以 `Migration(version, description, fn)` 追加到 `MIGRATIONS`，
  启动时按版本顺序只执行未应用的迁移，并在同一事务内记录。

约定：新增纯 DDL（建表/加列）仍可走 `_init_tables_full` + bump SCHEMA_VERSION；
需要数据回填、改列类型、索引重建等非幂等操作时使用 migration。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from easy_agent.db.legacy_messages import clear_complete_legacy_blobs

logger = logging.getLogger("easy_agent.db.migrations")

MIGRATIONS_TABLE = "schema_migrations"
# 引入迁移框架时数据库已处于的 schema 版本；第一次启用只记 baseline，不重放历史 DDL。
BASELINE_VERSION = 5


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    fn: Callable[[object, object], None]


# 版本号必须严格递增（接着 baseline 往后编，与 SCHEMA_VERSION 各自独立）；
# 函数签名 (db, cursor) -> None。
MIGRATIONS: list[Migration] = [
    # 启动修复（只补不删）跑完之后执行：行表已包含旧 JSON 全部消息的会话把 JSON 清空，
    # 见 easy_agent/db/legacy_messages.py。
    Migration(
        version=6,
        description="clear sessions.messages already covered by session_messages",
        fn=clear_complete_legacy_blobs,
    ),
]


def ensure_migrations_table(db, cursor) -> None:
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            version INTEGER PRIMARY KEY,
            description VARCHAR(255) NOT NULL DEFAULT '',
            applied_at VARCHAR(50) NOT NULL
        )
        """
    )


def applied_versions(db, cursor) -> list[int]:
    ensure_migrations_table(db, cursor)
    db._execute(cursor, f"SELECT version FROM {MIGRATIONS_TABLE} ORDER BY version")
    rows = cursor.fetchall()
    return [int(r["version"] if isinstance(r, dict) else r[0]) for r in rows]


def record_migration(db, cursor, version: int, description: str) -> None:
    db._execute(
        cursor,
        f"INSERT INTO {MIGRATIONS_TABLE} (version, description, applied_at) VALUES (?, ?, ?)",
        (version, description, datetime.now().isoformat()),
    )


def record_baseline(db, cursor, baseline_version: int) -> bool:
    """首次启用迁移框架时写入 baseline；已是 baseline 或更新则跳过。"""
    versions = applied_versions(db, cursor)
    if versions:
        return False
    record_migration(db, cursor, int(baseline_version), "baseline")
    logger.info("数据库迁移账本已初始化 | baseline=%s", baseline_version)
    return True


def apply_pending_migrations(db, cursor) -> list[int]:
    """按版本顺序应用未执行的迁移，返回本次应用版本列表。"""
    done = set(applied_versions(db, cursor))
    applied: list[int] = []
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version in done:
            continue
        logger.info(
            "应用数据库迁移 | version=%s | %s", migration.version, migration.description
        )
        migration.fn(db, cursor)
        record_migration(db, cursor, migration.version, migration.description)
        applied.append(migration.version)
    return applied
