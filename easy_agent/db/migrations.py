"""版本化数据库迁移（ARCH-16）。

背景：当前 `Database.init_tables()` 依赖「单版本号 + 幂等 DDL」快速路径，适合
建表/补列，但缺少**可审计、可回滚的按版本迁移记录**。本模块提供最小迁移框架：

- 已应用版本记在 `schema_meta`（键 `migration:<版本>`，值为应用时间与说明），与结构
  版本号同表；旧库的 `schema_migrations` 表在下次启动时导入后删除；
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

#: 结构版本号（db/schema.py）与迁移账本共用的键值表。
META_TABLE = "schema_meta"
#: 旧的迁移账本表：导入 schema_meta 后删除。
LEGACY_MIGRATIONS_TABLE = "schema_migrations"
_KEY_PREFIX = "migration:"
# 引入迁移框架时数据库已处于的 schema 版本；第一次启用只记 baseline，不重放历史 DDL。
BASELINE_VERSION = 5


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    fn: Callable[[object, object], None]


#: 迁移 6：清空已被行表覆盖的旧 JSON。还没执行过，说明这是升级后的第一次启动（schema.py 据此
#: 决定对不上的旧会话打 warning 还是 debug）。
CLEAR_LEGACY_BLOBS_VERSION = 6

# 版本号必须严格递增（接着 baseline 往后编，与 SCHEMA_VERSION 各自独立）；
# 函数签名 (db, cursor) -> None。
MIGRATIONS: list[Migration] = [
    # 启动修复（只补不删）跑完之后执行：行表已包含旧 JSON 全部消息的会话把 JSON 清空，
    # 见 easy_agent/db/legacy_messages.py。
    Migration(
        version=CLEAR_LEGACY_BLOBS_VERSION,
        description="clear sessions.messages already covered by session_messages",
        fn=clear_complete_legacy_blobs,
    ),
]


def ensure_meta_table(db, cursor) -> None:
    if db.db_type == "sqlite":
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {META_TABLE} "
            "(meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)"
        )
    else:
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {META_TABLE} "
            "(meta_key VARCHAR(64) NOT NULL PRIMARY KEY, "
            "meta_value VARCHAR(255) NOT NULL)"
        )


def table_exists(db, cursor, name: str) -> bool:
    if db.db_type == "sqlite":
        db._execute(cursor, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    else:
        db._execute(
            cursor,
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = ?",
            (name,),
        )
    return cursor.fetchone() is not None


def ensure_migration_ledger(db, cursor) -> None:
    """确保账本可用：旧库的 schema_migrations 表导入 schema_meta 后删除（只发生一次）。"""
    ensure_meta_table(db, cursor)
    if not table_exists(db, cursor, LEGACY_MIGRATIONS_TABLE):
        return
    db._execute(
        cursor, f"SELECT version, description, applied_at FROM {LEGACY_MIGRATIONS_TABLE}"
    )
    legacy = [
        (r["version"], r["description"], r["applied_at"]) if isinstance(r, dict) else tuple(r)
        for r in cursor.fetchall()
    ]
    done = set(applied_versions(db, cursor))
    for version, description, applied_at in legacy:
        if int(version) not in done:
            _record(db, cursor, int(version), description or "", applied_at or "")
    cursor.execute(f"DROP TABLE {LEGACY_MIGRATIONS_TABLE}")
    logger.info("迁移账本已并入 %s | 导入 %d 条", META_TABLE, len(legacy))


def applied_versions(db, cursor) -> list[int]:
    db._execute(
        cursor, f"SELECT meta_key FROM {META_TABLE} WHERE meta_key LIKE ?", (f"{_KEY_PREFIX}%",)
    )
    versions = []
    for r in cursor.fetchall():
        key = r["meta_key"] if isinstance(r, dict) else r[0]
        try:
            versions.append(int(key[len(_KEY_PREFIX):]))
        except ValueError:
            continue
    return sorted(versions)


def _record(db, cursor, version: int, description: str, applied_at: str) -> None:
    db._execute(
        cursor,
        f"INSERT INTO {META_TABLE} (meta_key, meta_value) VALUES (?, ?)",
        (f"{_KEY_PREFIX}{version}", f"{applied_at} {description}".strip()[:255]),
    )


def record_migration(db, cursor, version: int, description: str) -> None:
    _record(db, cursor, int(version), description, datetime.now().isoformat())


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
