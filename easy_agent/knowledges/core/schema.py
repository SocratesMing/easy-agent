"""知识库数据库 schema：幂等初始化。

已存在的库通过 ``_ensure_column`` 就地补齐缺失列（软删除、版本等），
可直接使用现有库而不迁移数据。不含 reconciliation / alert /
runtime_heartbeat 及迁移账本表。废弃对象（knowledge_tasks、
knowledge_team_space_viewers 死表，并入审计事件的原始文件访问审计表，
无查询支撑的二级索引）由 ``_cleanup_legacy_schema`` 幂等清除。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型注解使用，避免 db.database <-> schema 的模块级循环
    from easy_agent.db.database import Database

logger = logging.getLogger(__name__)

# 注册到 Database.init_tables()（schema 注册制，避免 db <-> knowledges 循环）。
from easy_agent.db.migrations import table_exists  # noqa: E402
from easy_agent.db.schema_registry import register_schema_initializer  # noqa: E402

# 目录树同步要求同一知识库内不同父节点下允许同名子文件夹
# （如 2026-09-12/中金公司 与 2026-09-13/中金公司），唯一约束需为
# (base_id, parent_id, name)；既有库可能存在 (base_id, name) 约束，需放宽。
_FOLDER_UNIQUE_RE = re.compile(
    r"UNIQUE\s*\(\s*`?base_id`?\s*,\s*`?name`?\s*\)", re.IGNORECASE
)
_FOLDER_UNIQUE_NAME = "uk_knowledge_folders_base_parent_name"

# 对已存在的库幂等补齐这些列
_BASE_P0_COLUMNS = (
    ("deleted_at", "VARCHAR(50) DEFAULT NULL"),
    ("deleted_by", "VARCHAR(255) DEFAULT NULL"),
    ("purge_after", "VARCHAR(50) DEFAULT NULL"),
    # 权限管理勾选名单模式：1=仅勾选人员可见（屏蔽公共空间默认同部门可见）
    ("access_restricted", "INTEGER NOT NULL DEFAULT 0"),
)
_DOCUMENT_P0_COLUMNS = (
    ("deleted_at", "VARCHAR(50) DEFAULT NULL"),
    ("deleted_by", "VARCHAR(255) DEFAULT NULL"),
    ("purge_after", "VARCHAR(50) DEFAULT NULL"),
    ("previous_status", "VARCHAR(30) DEFAULT NULL"),
    ("document_version", "INTEGER NOT NULL DEFAULT 1"),
    # 业务元数据（文档 SQL 批量同步写入；检索时作为硬过滤条件）
    ("source", "VARCHAR(127) DEFAULT NULL"),
    ("source_record_id", "VARCHAR(255) DEFAULT NULL"),
    ("batch_id", "VARCHAR(255) DEFAULT NULL"),
    ("report_title", "VARCHAR(255) DEFAULT NULL"),
    ("report_tag", "VARCHAR(127) DEFAULT NULL"),
    ("report_second_source", "VARCHAR(127) DEFAULT NULL"),
    ("publish_date", "VARCHAR(10) DEFAULT NULL"),
    # 解析计时：区分“排队等待”与“真正开始解析”，供超时判定使用
    ("parse_started_at", "VARCHAR(50) DEFAULT NULL"),
)


def initialize_knowledge_schema(db: Database, cursor) -> None:
    """创建知识库模块全部数据表与索引（已存在时幂等跳过）。

    首个参数为 :class:`~easy_agent.db.database.Database` 实例（提供
    ``_execute`` / ``_create_index`` / ``_ensure_column`` 的跨库实现）。
    """

    # ---- 知识库（个人/公共空间） ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            id VARCHAR(255) PRIMARY KEY,
            remote_dataset_id VARCHAR(255) UNIQUE,
            name VARCHAR(127) NOT NULL,
            description TEXT NOT NULL,
            space_type VARCHAR(20) NOT NULL,
            owner_user_id VARCHAR(255) NOT NULL,
            department_id VARCHAR(255),
            embedding_model VARCHAR(255) NOT NULL,
            status VARCHAR(30) NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            deleted_at VARCHAR(50) DEFAULT NULL,
            deleted_by VARCHAR(255) DEFAULT NULL,
            purge_after VARCHAR(50) DEFAULT NULL,
            access_restricted INTEGER NOT NULL DEFAULT 0
        )
    """)
    for column, definition in _BASE_P0_COLUMNS:
        db._ensure_column(cursor, "knowledge_bases", column, definition)
    db._create_index(cursor, "idx_knowledge_bases_owner", "knowledge_bases", "owner_user_id")

    # ---- 文件夹 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_folders (
            id VARCHAR(255) PRIMARY KEY,
            base_id VARCHAR(255) NOT NULL,
            parent_id VARCHAR(255),
            name VARCHAR(127) NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            UNIQUE(base_id, parent_id, name),
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        )
    """)
    db._ensure_column(cursor, "knowledge_folders", "parent_id", "VARCHAR(255) DEFAULT NULL")
    _migrate_folder_unique_constraint(db, cursor)
    # 按 base_id 查走 UNIQUE(base_id, parent_id, name) 的前缀，不单建 base_id 索引。
    db._create_index(cursor, "idx_knowledge_folders_parent", "knowledge_folders", "parent_id")

    # ---- 文档 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id VARCHAR(255) PRIMARY KEY,
            remote_document_id VARCHAR(255) UNIQUE,
            base_id VARCHAR(255) NOT NULL,
            folder_id VARCHAR(255),
            name VARCHAR(255) NOT NULL,
            content_type VARCHAR(255) NOT NULL,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            status VARCHAR(30) NOT NULL,
            progress REAL,
            error_code VARCHAR(100),
            error_message TEXT,
            created_by VARCHAR(255) NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            deleted_at VARCHAR(50) DEFAULT NULL,
            deleted_by VARCHAR(255) DEFAULT NULL,
            purge_after VARCHAR(50) DEFAULT NULL,
            previous_status VARCHAR(30) DEFAULT NULL,
            document_version INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
            FOREIGN KEY (folder_id) REFERENCES knowledge_folders(id) ON DELETE SET NULL
        )
    """)
    for column, definition in _DOCUMENT_P0_COLUMNS:
        db._ensure_column(cursor, "knowledge_documents", column, definition)
    db._create_index(cursor, "idx_knowledge_documents_folder", "knowledge_documents", "folder_id")
    # 元数据检索：日期区间 / 来源 / 标签过滤。两个索引都以 base_id 开头，按库查询也走它们，
    # 不再单建 base_id 索引。
    db._create_index(
        cursor,
        "idx_knowledge_documents_base_publish",
        "knowledge_documents",
        "base_id, publish_date",
    )
    db._create_index(
        cursor,
        "idx_knowledge_documents_base_source",
        "knowledge_documents",
        "base_id, report_second_source",
    )
    # 文档 SQL 同步幂等键：同一知识库内同一来源的同一业务记录只入库一次。
    # 必须带 base_id：软删除行与旧库历史行不能阻止新库重新入库。
    _migrate_source_record_unique_index(db, cursor)
    db._create_unique_index(
        cursor,
        "uq_knowledge_documents_source_record",
        "knowledge_documents",
        "source, source_record_id, base_id",
    )
    # 兜底清理按 status 扫描软删除文档，复合索引最左前缀已覆盖该路径，
    # 无需单列 status 索引；purge_after 为历史列，仅保留以兼容既有库。
    db._create_index(cursor, "idx_knowledge_documents_purge", "knowledge_documents", "status, purge_after")

    # ---- 文档 SQL 同步台账（断点续传 / 失败重试 / 漏传核对） ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_source_sync_items (
            id VARCHAR(255) PRIMARY KEY,
            source VARCHAR(127) NOT NULL,
            source_record_id VARCHAR(255) NOT NULL,
            document_id VARCHAR(255),
            status VARCHAR(30) NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            publish_date VARCHAR(10),
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            UNIQUE(source, source_record_id)
        )
    """)
    db._create_index(
        cursor,
        "idx_knowledge_source_sync_status",
        "knowledge_source_sync_items",
        "source, status",
    )

    # ---- 原始文件对象 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_document_objects (
            id VARCHAR(255) PRIMARY KEY,
            document_id VARCHAR(255) NOT NULL,
            object_version INTEGER NOT NULL DEFAULT 1,
            provider VARCHAR(30) NOT NULL,
            storage_key VARCHAR(512) NOT NULL,
            status VARCHAR(30) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            content_type VARCHAR(255) NOT NULL,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            sha256 VARCHAR(64),
            error_code VARCHAR(100),
            error_message TEXT,
            created_by VARCHAR(255) NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50),
            stored_at VARCHAR(50),
            quarantined_at VARCHAR(50),
            deleted_at VARCHAR(50),
            last_verified_at VARCHAR(50),
            UNIQUE(document_id, object_version),
            UNIQUE(provider, storage_key),
            FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
        )
    """)
    # 查询仅按 document_id 取最新版本，UNIQUE(document_id, object_version)
    # 最左前缀已覆盖，无需额外二级索引。
    # 存量库（v2 建表初期版本）幂等补列：repository._update_by_id 统一刷新 updated_at
    db._ensure_column(
        cursor, "knowledge_document_objects", "updated_at", "VARCHAR(50) DEFAULT NULL"
    )

    # ---- 权限 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_permissions (
            id VARCHAR(255) PRIMARY KEY,
            base_id VARCHAR(255) NOT NULL,
            subject_type VARCHAR(20) NOT NULL,
            subject_id VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL,
            created_by VARCHAR(255) NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            UNIQUE(base_id, subject_type, subject_id),
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        )
    """)
    db._create_index(
        cursor, "idx_knowledge_permissions_subject", "knowledge_permissions", "subject_type, subject_id"
    )

    # ---- 操作记录 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_operations (
            id VARCHAR(255) PRIMARY KEY,
            type VARCHAR(40) NOT NULL,
            resource_type VARCHAR(40) NOT NULL,
            resource_id VARCHAR(255) NOT NULL,
            phase VARCHAR(40) NOT NULL,
            status VARCHAR(40) NOT NULL,
            current_count INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 1,
            progress REAL,
            retryable INTEGER NOT NULL DEFAULT 0,
            error_code VARCHAR(100),
            error_message TEXT,
            created_by VARCHAR(255) NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL
        )
    """)
    db._create_index(
        cursor, "idx_knowledge_operations_resource", "knowledge_operations", "resource_type, resource_id"
    )
    db._create_index(
        cursor, "idx_knowledge_operations_creator", "knowledge_operations", "created_by, updated_at"
    )

    # ---- 会话知识范围 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_knowledge_scopes (
            session_id VARCHAR(255) NOT NULL,
            base_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (session_id, base_id, user_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        )
    """)
    db._ensure_column(cursor, "session_knowledge_scopes", "sort_order", "INTEGER NOT NULL DEFAULT 0")
    db._create_index(cursor, "idx_session_knowledge_user", "session_knowledge_scopes", "user_id, session_id")

    # ---- 审计事件（含原始文件访问：action=knowledge.original.<动作>） ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_audit_events (
            id VARCHAR(255) PRIMARY KEY,
            request_id VARCHAR(128) NOT NULL,
            actor_user_id VARCHAR(255) NOT NULL,
            actor_username VARCHAR(255) NOT NULL,
            action VARCHAR(80) NOT NULL,
            object_type VARCHAR(40) NOT NULL,
            object_id VARCHAR(255) NOT NULL,
            base_id VARCHAR(255),
            outcome VARCHAR(20) NOT NULL,
            reason_code VARCHAR(100),
            details_json TEXT NOT NULL,
            created_at VARCHAR(50) NOT NULL
        )
    """)
    db._create_index(cursor, "idx_knowledge_audit_request", "knowledge_audit_events", "request_id, created_at")
    db._create_index(cursor, "idx_knowledge_audit_actor", "knowledge_audit_events", "actor_user_id, created_at")
    db._create_index(
        cursor, "idx_knowledge_audit_object", "knowledge_audit_events", "object_type, object_id, created_at"
    )

    # ---- 公共空间管理白名单（仅按 PK user_id 查询，无需二级索引） ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_team_space_managers (
            user_id VARCHAR(255) PRIMARY KEY,
            granted_by VARCHAR(255) NOT NULL,
            granted_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    _cleanup_legacy_schema(db, cursor)


def _cleanup_legacy_schema(db: Database, cursor) -> None:
    """幂等清除废弃的表与索引（仅限显式列出的对象，逐个容错跳过）。

    - knowledge_tasks：数据库任务队列设计已被进程内 worker 取代，全库无读写；
    - knowledge_team_space_viewers：从来没有写入和读取（只有删除人员时的一句 DELETE）；
    - knowledge_original_access_audit：并入 knowledge_audit_events，旧行先搬过去再删表；
    - 其余为无查询路径支撑、或已是别的索引前缀的二级索引。
    """

    # 死表：SQLite / MySQL 均支持 DROP TABLE IF EXISTS
    for table in ("knowledge_tasks", "knowledge_team_space_viewers"):
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"删除废弃表 {table} 失败（可忽略）: {exc}")

    _merge_original_access_audit(db, cursor)

    # 死索引：MySQL 无 DROP INDEX IF EXISTS，逐个尝试；SQLite 语法不含表名
    legacy_indexes = (
        ("knowledge_bases", "idx_knowledge_bases_department"),
        ("knowledge_documents", "idx_knowledge_documents_status"),
        ("knowledge_documents", "idx_knowledge_documents_base"),
        ("knowledge_folders", "idx_knowledge_folders_base"),
        ("knowledge_document_objects", "idx_knowledge_document_objects_document"),
        ("knowledge_document_objects", "idx_knowledge_document_objects_status"),
        ("knowledge_team_space_managers", "idx_knowledge_team_managers_granted_by"),
    )
    for table, index in legacy_indexes:
        try:
            if db.db_type == "mysql":
                cursor.execute(f"DROP INDEX `{index}` ON `{table}`")
            else:
                cursor.execute(f"DROP INDEX IF EXISTS `{index}`")
        except Exception:  # noqa: BLE001
            # 索引不存在（新装库）即目标状态，静默跳过
            pass


_ORIGINAL_AUDIT_TABLE = "knowledge_original_access_audit"


def _merge_original_access_audit(db: Database, cursor) -> None:
    """旧的原始文件访问审计并入 knowledge_audit_events，再删除旧表（每个库只发生一次）。

    按同一 id 搬运，重复的跳过（中途失败重跑不会重复）。动作记为
    ``knowledge.original.<action>``，会话 / agent 放进 details_json；用户名、所属知识库从
    users / knowledge_documents 补，查不到时用户名为空、知识库为 NULL。
    """
    if not table_exists(db, cursor, _ORIGINAL_AUDIT_TABLE):
        return
    if db.db_type == "mysql":
        insert = "INSERT IGNORE"
        action = "CONCAT('knowledge.original.', a.action)"
        details = "JSON_OBJECT('session_id', a.session_id, 'agent_id', a.agent_id)"
    else:
        insert = "INSERT OR IGNORE"
        action = "'knowledge.original.' || a.action"
        details = "json_object('session_id', a.session_id, 'agent_id', a.agent_id)"
    cursor.execute(f"""
        {insert} INTO knowledge_audit_events
            (id, request_id, actor_user_id, actor_username, action, object_type,
             object_id, base_id, outcome, reason_code, details_json, created_at)
        SELECT a.id, SUBSTR(a.request_id, 1, 128), a.user_id, COALESCE(u.username, ''),
               {action}, 'document', a.document_id, d.base_id, SUBSTR(a.result, 1, 20),
               NULL, {details}, a.created_at
        FROM {_ORIGINAL_AUDIT_TABLE} a
        LEFT JOIN users u ON u.user_id = a.user_id
        LEFT JOIN knowledge_documents d ON d.id = a.document_id
    """)
    moved = cursor.rowcount
    cursor.execute(f"DROP TABLE {_ORIGINAL_AUDIT_TABLE}")
    logger.info("原始文件访问审计已并入 knowledge_audit_events | 搬运 %s 条", moved)


def _migrate_folder_unique_constraint(db: Database, cursor) -> None:
    """将存量 knowledge_folders 唯一约束 (base_id, name) 迁移为 (base_id, parent_id, name)。

    幂等：新约束已存在时跳过。SQLite 通过重建表实现（项目未开启
    PRAGMA foreign_keys，DROP 不会被 knowledge_documents 的外键引用阻挡）；
    MySQL 通过 DROP INDEX / ADD UNIQUE KEY 实现。parent_id 为 NULL 的根级
    同名不受数据库唯一性保护（NULL 不参与唯一判断），由服务层显式查重兜底。
    """
    try:
        if db.db_type == "mysql":
            _migrate_folder_unique_mysql(cursor)
        else:
            _migrate_folder_unique_sqlite(cursor)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"knowledge_folders 唯一约束迁移检查失败（可忽略新装库）: {exc}")


def _migrate_folder_unique_mysql(cursor) -> None:
    cursor.execute("SHOW INDEX FROM knowledge_folders")
    rows = cursor.fetchall()
    columns_by_index: dict[str, list[str]] = {}
    for row in rows:
        if int(row["Non_unique"]) != 0:
            continue
        columns_by_index.setdefault(str(row["Key_name"]), []).append(str(row["Column_name"]))
    has_base_name_unique = any(
        columns == ["base_id", "name"] for columns in columns_by_index.values()
    )
    has_new = any(
        columns == ["base_id", "parent_id", "name"] for columns in columns_by_index.values()
    )
    if has_new:
        return
    if not has_base_name_unique:
        return
    base_name_unique_name = next(
        name for name, columns in columns_by_index.items() if columns == ["base_id", "name"]
    )
    cursor.execute(f"ALTER TABLE knowledge_folders DROP INDEX `{base_name_unique_name}`")
    cursor.execute(
        "ALTER TABLE knowledge_folders "
        f"ADD UNIQUE KEY `{_FOLDER_UNIQUE_NAME}` (base_id, parent_id, name)"
    )
    logger.info("knowledge_folders 唯一约束已迁移: (base_id, name) → (base_id, parent_id, name)")


def _migrate_folder_unique_sqlite(cursor) -> None:
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='knowledge_folders'"
    )
    row = cursor.fetchone()
    create_sql = (row["sql"] if row is not None else "") or ""
    if not _FOLDER_UNIQUE_RE.search(create_sql):
        return
    cursor.execute("""
        CREATE TABLE knowledge_folders_migrate (
            id VARCHAR(255) PRIMARY KEY,
            base_id VARCHAR(255) NOT NULL,
            parent_id VARCHAR(255),
            name VARCHAR(127) NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            UNIQUE(base_id, parent_id, name),
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        INSERT INTO knowledge_folders_migrate
            (id, base_id, parent_id, name, sort_order, created_at, updated_at)
        SELECT id, base_id, parent_id, name, sort_order, created_at, updated_at
        FROM knowledge_folders
    """)
    cursor.execute("DROP TABLE knowledge_folders")
    cursor.execute("ALTER TABLE knowledge_folders_migrate RENAME TO knowledge_folders")
    logger.info("knowledge_folders 唯一约束已迁移: (base_id, name) → (base_id, parent_id, name)")


_SOURCE_RECORD_UNIQUE_NAME = "uq_knowledge_documents_source_record"
_SOURCE_RECORD_UNIQUE_COLUMNS = ["source", "source_record_id", "base_id"]


def _migrate_source_record_unique_index(db: Database, cursor) -> None:
    """把文档同步幂等键 (source, source_record_id) 放宽为含 base_id 的三列。

    只按来源 + 记录 ID 建唯一键时，软删除行与已删除知识库的历史行会永久占位：
    删库/删文档后重跑同步会报 1062 Duplicate entry，业务记录再也无法入库。
    加入 base_id 后同一记录可在新库重新入库，同库内仍保持唯一（同库软删除行
    由同步逻辑视为已存在并跳过，不会重复上传）。幂等：已是三列时直接跳过。
    """
    try:
        columns = _index_columns(db, cursor, "knowledge_documents", _SOURCE_RECORD_UNIQUE_NAME)
        if columns is None or columns == _SOURCE_RECORD_UNIQUE_COLUMNS:
            return
        if db.db_type == "mysql":
            cursor.execute(
                f"ALTER TABLE knowledge_documents DROP INDEX `{_SOURCE_RECORD_UNIQUE_NAME}`"
            )
        else:
            cursor.execute(f"DROP INDEX IF EXISTS {_SOURCE_RECORD_UNIQUE_NAME}")
        logger.info(
            "knowledge_documents 同步幂等键已迁移: (%s) → (%s)",
            ", ".join(columns),
            ", ".join(_SOURCE_RECORD_UNIQUE_COLUMNS),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"knowledge_documents 同步幂等键迁移检查失败: {exc}")


def _index_columns(db: Database, cursor, table: str, index_name: str) -> list[str] | None:
    """返回索引的列顺序；索引不存在时返回 None（兼容两套方言）。"""
    if db.db_type == "mysql":
        db._execute(cursor, f"SHOW INDEX FROM {table} WHERE Key_name=?", (index_name,))
    else:
        cursor.execute(f"PRAGMA index_info({index_name})")
    rows = cursor.fetchall()
    if not rows:
        return None
    if db.db_type == "mysql":
        return [_row_value(row, "Column_name", 4) for row in rows]
    return [_row_value(row, "name", 2) for row in rows]


def _row_value(row, key: str, index: int) -> str:
    """同时兼容 dict 游标（MySQL）与 sqlite3.Row/普通元组。"""
    try:
        return str(row[key])
    except (KeyError, IndexError, TypeError):
        return str(row[index])


__all__ = ["initialize_knowledge_schema"]


# 模块被 import 即注册（幂等：重复 import 不会重复注册）。
register_schema_initializer(initialize_knowledge_schema)
