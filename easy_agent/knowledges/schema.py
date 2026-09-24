"""新版知识库数据库 schema：表名/列与旧版完全一致，幂等初始化。

与旧版模块共用同一套表结构，可直接切到现有库而不迁移数据；旧版
MVP 库缺失的列（软删除、版本等）通过 ``_ensure_column`` 就地补齐。
相比旧版裁剪了 reconciliation / alert / runtime_heartbeat 表（对账
与可观测性不在新版范围内）及迁移账本表。
"""

from __future__ import annotations

import logging

from ..db.database import Database

logger = logging.getLogger(__name__)

# 旧版 MVP 建表后由 P0 迁移追加的列；对已存在的库幂等补齐
_BASE_P0_COLUMNS = (
    ("deleted_at", "VARCHAR(50) DEFAULT NULL"),
    ("deleted_by", "VARCHAR(255) DEFAULT NULL"),
    ("purge_after", "VARCHAR(50) DEFAULT NULL"),
)
_DOCUMENT_P0_COLUMNS = _BASE_P0_COLUMNS + (
    ("previous_status", "VARCHAR(30) DEFAULT NULL"),
    ("document_version", "INTEGER NOT NULL DEFAULT 1"),
)


def initialize_knowledge_schema(db: Database, cursor) -> None:
    """创建知识库模块全部数据表与索引（已存在时幂等跳过）。

    首个参数为 :class:`~easy_agent.db.database.Database` 实例（提供
    ``_execute`` / ``_create_index`` / ``_ensure_column`` 的跨库实现），
    与旧版调用方式保持一致。
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
            purge_after VARCHAR(50) DEFAULT NULL
        )
    """)
    for column, definition in _BASE_P0_COLUMNS:
        db._ensure_column(cursor, "knowledge_bases", column, definition)
    db._create_index(cursor, "idx_knowledge_bases_owner", "knowledge_bases", "owner_user_id")
    db._create_index(cursor, "idx_knowledge_bases_department", "knowledge_bases", "department_id")

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
            UNIQUE(base_id, name),
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        )
    """)
    db._ensure_column(cursor, "knowledge_folders", "parent_id", "VARCHAR(255) DEFAULT NULL")
    db._create_index(cursor, "idx_knowledge_folders_base", "knowledge_folders", "base_id")
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
    db._create_index(cursor, "idx_knowledge_documents_base", "knowledge_documents", "base_id")
    db._create_index(cursor, "idx_knowledge_documents_folder", "knowledge_documents", "folder_id")
    db._create_index(cursor, "idx_knowledge_documents_status", "knowledge_documents", "status")
    db._create_index(cursor, "idx_knowledge_documents_purge", "knowledge_documents", "status, purge_after")

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
            stored_at VARCHAR(50),
            quarantined_at VARCHAR(50),
            deleted_at VARCHAR(50),
            last_verified_at VARCHAR(50),
            UNIQUE(document_id, object_version),
            UNIQUE(provider, storage_key),
            FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
        )
    """)
    db._create_index(
        cursor, "idx_knowledge_document_objects_document", "knowledge_document_objects", "document_id, status"
    )
    db._create_index(
        cursor, "idx_knowledge_document_objects_status", "knowledge_document_objects", "status, created_at"
    )

    # ---- 原始文件访问审计 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_original_access_audit (
            id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            session_id VARCHAR(255) NOT NULL DEFAULT '',
            agent_id VARCHAR(255) NOT NULL DEFAULT '',
            document_id VARCHAR(255) NOT NULL,
            action VARCHAR(40) NOT NULL,
            result VARCHAR(40) NOT NULL,
            request_id VARCHAR(255) NOT NULL DEFAULT '',
            created_at VARCHAR(50) NOT NULL
        )
    """)
    db._create_index(
        cursor, "idx_knowledge_original_audit_document", "knowledge_original_access_audit", "document_id, created_at"
    )
    db._create_index(
        cursor, "idx_knowledge_original_audit_user", "knowledge_original_access_audit", "user_id, created_at"
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

    # ---- 异步任务队列（上传/解析等后台任务） ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_tasks (
            id VARCHAR(255) PRIMARY KEY,
            operation_id VARCHAR(255) NOT NULL,
            task_type VARCHAR(40) NOT NULL,
            resource_type VARCHAR(40) NOT NULL,
            resource_id VARCHAR(255) NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            status VARCHAR(30) NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            available_at VARCHAR(50) NOT NULL,
            locked_by VARCHAR(255),
            locked_at VARCHAR(50),
            heartbeat_at VARCHAR(50),
            timeout_at VARCHAR(50),
            request_id VARCHAR(128) NOT NULL,
            created_by VARCHAR(255) NOT NULL,
            last_error_code VARCHAR(100),
            last_error_message TEXT,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            completed_at VARCHAR(50),
            FOREIGN KEY (operation_id) REFERENCES knowledge_operations(id) ON DELETE CASCADE
        )
    """)
    db._create_index(cursor, "idx_knowledge_tasks_claim", "knowledge_tasks", "status, available_at")
    db._create_index(cursor, "idx_knowledge_tasks_resource", "knowledge_tasks", "resource_type, resource_id")
    db._create_index(cursor, "idx_knowledge_tasks_operation", "knowledge_tasks", "operation_id")

    # ---- 审计事件 ----
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

    # ---- 公共空间管理/查看白名单 ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_team_space_managers (
            user_id VARCHAR(255) PRIMARY KEY,
            granted_by VARCHAR(255) NOT NULL,
            granted_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    db._create_index(
        cursor, "idx_knowledge_team_managers_granted_by", "knowledge_team_space_managers", "granted_by, updated_at"
    )
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_team_space_viewers (
            user_id VARCHAR(255) PRIMARY KEY,
            granted_by VARCHAR(255) NOT NULL,
            granted_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    db._create_index(
        cursor, "idx_knowledge_team_viewers_granted_by", "knowledge_team_space_viewers", "granted_by, updated_at"
    )


__all__ = ["initialize_knowledge_schema"]
