"""Versioned database schema owned by the independent knowledge module."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_value(row, key: str, index: int = 0):
    if isinstance(row, dict):
        return row.get(key)
    return row[index] if row else None


def _ensure_migration_table(db, cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_schema_migrations (
            version INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            applied_at VARCHAR(50) NOT NULL
        )
        """
    )


def _migration_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _record_migration(db, cursor, version: int, name: str, content: str) -> None:
    checksum = _migration_checksum(content)
    db._execute(
        cursor,
        "SELECT checksum FROM knowledge_schema_migrations WHERE version=?",
        (version,),
    )
    current = cursor.fetchone()
    if current:
        if str(_row_value(current, "checksum")) != checksum:
            raise RuntimeError(
                f"knowledge migration {version} checksum mismatch"
            )
        return
    db._execute(
        cursor,
        """
        INSERT INTO knowledge_schema_migrations
            (version, name, checksum, applied_at)
        VALUES (?, ?, ?, ?)
        """,
        (version, name, checksum, _now()),
    )


def _split_sql(content: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).rstrip().rstrip(";"))
            buffer = []
    if buffer:
        statements.append("\n".join(buffer))
    return statements


def _apply_p0_migration(db, cursor) -> None:
    path = _MIGRATIONS_DIR / "0002_p0_production.sql"
    content = path.read_text(encoding="utf-8")
    db._execute(
        cursor,
        "SELECT checksum FROM knowledge_schema_migrations WHERE version=?",
        (2,),
    )
    current = cursor.fetchone()
    if current:
        if str(_row_value(current, "checksum")) != _migration_checksum(content):
            raise RuntimeError("knowledge migration 2 checksum mismatch")
        return

    for table, column, definition in (
        ("knowledge_documents", "deleted_at", "VARCHAR(50) DEFAULT NULL"),
        ("knowledge_documents", "deleted_by", "VARCHAR(255) DEFAULT NULL"),
        ("knowledge_documents", "purge_after", "VARCHAR(50) DEFAULT NULL"),
        ("knowledge_documents", "previous_status", "VARCHAR(30) DEFAULT NULL"),
        ("knowledge_documents", "document_version", "INTEGER NOT NULL DEFAULT 1"),
        ("knowledge_bases", "deleted_at", "VARCHAR(50) DEFAULT NULL"),
        ("knowledge_bases", "deleted_by", "VARCHAR(255) DEFAULT NULL"),
        ("knowledge_bases", "purge_after", "VARCHAR(50) DEFAULT NULL"),
    ):
        db._ensure_column(cursor, table, column, definition)

    for statement in _split_sql(content):
        cursor.execute(statement)

    for index_name, table, columns in (
        ("idx_knowledge_tasks_claim", "knowledge_tasks", "status, available_at"),
        ("idx_knowledge_tasks_resource", "knowledge_tasks", "resource_type, resource_id"),
        ("idx_knowledge_tasks_operation", "knowledge_tasks", "operation_id"),
        ("idx_knowledge_audit_request", "knowledge_audit_events", "request_id, created_at"),
        ("idx_knowledge_audit_actor", "knowledge_audit_events", "actor_user_id, created_at"),
        ("idx_knowledge_audit_object", "knowledge_audit_events", "object_type, object_id, created_at"),
        ("idx_knowledge_reconcile_issue", "knowledge_reconciliation_issues", "status, issue_type, last_seen_at"),
        ("idx_knowledge_documents_purge", "knowledge_documents", "status, purge_after"),
        ("idx_knowledge_alert_status", "knowledge_alerts", "status, severity, last_seen_at"),
    ):
        db._create_index(cursor, index_name, table, columns)
    db._create_unique_index(
        cursor,
        "uq_knowledge_reconcile_issue_key",
        "knowledge_reconciliation_issues",
        "issue_key",
    )
    _record_migration(db, cursor, 2, "p0_production", content)


def _apply_team_space_manager_migration(db, cursor) -> None:
    path = _MIGRATIONS_DIR / "0003_team_space_managers.sql"
    content = path.read_text(encoding="utf-8")
    db._execute(
        cursor,
        "SELECT checksum FROM knowledge_schema_migrations WHERE version=?",
        (3,),
    )
    current = cursor.fetchone()
    if current:
        if str(_row_value(current, "checksum")) != _migration_checksum(content):
            raise RuntimeError("knowledge migration 3 checksum mismatch")
        return

    for statement in _split_sql(content):
        cursor.execute(statement)
    db._create_index(
        cursor,
        "idx_knowledge_team_managers_granted_by",
        "knowledge_team_space_managers",
        "granted_by, updated_at",
    )
    _record_migration(db, cursor, 3, "team_space_managers", content)


def _apply_session_scope_order_migration(db, cursor) -> None:
    path = _MIGRATIONS_DIR / "0004_session_scope_order.sql"
    content = path.read_text(encoding="utf-8")
    db._execute(
        cursor,
        "SELECT checksum FROM knowledge_schema_migrations WHERE version=?",
        (4,),
    )
    current = cursor.fetchone()
    if current:
        if str(_row_value(current, "checksum")) != _migration_checksum(content):
            raise RuntimeError("knowledge migration 4 checksum mismatch")
        return

    # Database._ensure_column emits the appropriate metadata check for SQLite
    # and MySQL. Existing rows receive zero and are deterministically ordered by
    # created_at/base_id; new selections persist their UI order explicitly.
    db._ensure_column(
        cursor,
        "session_knowledge_scopes",
        "sort_order",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _record_migration(db, cursor, 4, "session_scope_order", content)


def expected_knowledge_migrations() -> dict[int, tuple[str, str]]:
    """Return immutable migration metadata used by deploy-time validation."""

    baseline = "easyagent-knowledge-mvp-v1"
    p0_content = (_MIGRATIONS_DIR / "0002_p0_production.sql").read_text(
        encoding="utf-8"
    )
    team_manager_content = (
        _MIGRATIONS_DIR / "0003_team_space_managers.sql"
    ).read_text(encoding="utf-8")
    session_scope_content = (
        _MIGRATIONS_DIR / "0004_session_scope_order.sql"
    ).read_text(encoding="utf-8")
    return {
        1: ("mvp_baseline", _migration_checksum(baseline)),
        2: ("p0_production", _migration_checksum(p0_content)),
        3: ("team_space_managers", _migration_checksum(team_manager_content)),
        4: ("session_scope_order", _migration_checksum(session_scope_content)),
    }


def validate_knowledge_schema_cursor(db, cursor) -> list[dict[str, object]]:
    """Validate with an existing transaction (used by production startup)."""

    expected = expected_knowledge_migrations()
    try:
        cursor.execute(
            "SELECT version, name, checksum, applied_at "
            "FROM knowledge_schema_migrations ORDER BY version"
        )
        rows = cursor.fetchall()
    except Exception as exc:
        raise RuntimeError(
            "knowledge schema is not initialized; run the migration command first"
        ) from exc
    applied = {
        int((_row_value(row, "version"))): {
            "version": int(_row_value(row, "version")),
            "name": str(_row_value(row, "name", 1)),
            "checksum": str(_row_value(row, "checksum", 2)),
            "applied_at": str(_row_value(row, "applied_at", 3)),
        }
        for row in rows
    }
    for version, (name, checksum) in expected.items():
        current = applied.get(version)
        if current is None:
            raise RuntimeError(f"knowledge migration {version} is pending")
        if current["name"] != name or current["checksum"] != checksum:
            raise RuntimeError(f"knowledge migration {version} checksum mismatch")
    return [applied[version] for version in sorted(expected)]


def validate_knowledge_schema(db) -> list[dict[str, object]]:
    """Fail closed when a numbered migration is missing or has changed."""

    with db.get_connection() as connection:
        return validate_knowledge_schema_cursor(db, connection.cursor())

def initialize_knowledge_schema(db, cursor) -> None:
    """Create the six-table MVP knowledge schema."""

    _ensure_migration_table(db, cursor)

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
            updated_at VARCHAR(50) NOT NULL
        )
    """)
    db._create_index(
        cursor, "idx_knowledge_bases_owner", "knowledge_bases", "owner_user_id"
    )
    db._create_index(
        cursor,
        "idx_knowledge_bases_department",
        "knowledge_bases",
        "department_id",
    )

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
    db._ensure_column(
        cursor, "knowledge_folders", "parent_id", "VARCHAR(255) DEFAULT NULL"
    )
    db._create_index(
        cursor, "idx_knowledge_folders_base", "knowledge_folders", "base_id"
    )
    db._create_index(
        cursor, "idx_knowledge_folders_parent", "knowledge_folders", "parent_id"
    )

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
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
            FOREIGN KEY (folder_id) REFERENCES knowledge_folders(id) ON DELETE SET NULL
        )
    """)
    db._create_index(
        cursor,
        "idx_knowledge_documents_base",
        "knowledge_documents",
        "base_id",
    )
    db._create_index(
        cursor,
        "idx_knowledge_documents_folder",
        "knowledge_documents",
        "folder_id",
    )
    db._create_index(
        cursor,
        "idx_knowledge_documents_status",
        "knowledge_documents",
        "status",
    )

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
        cursor,
        "idx_knowledge_document_objects_document",
        "knowledge_document_objects",
        "document_id, status",
    )
    db._create_index(
        cursor,
        "idx_knowledge_document_objects_status",
        "knowledge_document_objects",
        "status, created_at",
    )

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
        cursor,
        "idx_knowledge_original_audit_document",
        "knowledge_original_access_audit",
        "document_id, created_at",
    )
    db._create_index(
        cursor,
        "idx_knowledge_original_audit_user",
        "knowledge_original_access_audit",
        "user_id, created_at",
    )

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
        cursor,
        "idx_knowledge_permissions_subject",
        "knowledge_permissions",
        "subject_type, subject_id",
    )

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
        cursor,
        "idx_knowledge_operations_resource",
        "knowledge_operations",
        "resource_type, resource_id",
    )
    db._create_index(
        cursor,
        "idx_knowledge_operations_creator",
        "knowledge_operations",
        "created_by, updated_at",
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_knowledge_scopes (
            session_id VARCHAR(255) NOT NULL,
            base_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            PRIMARY KEY (session_id, base_id, user_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        )
    """)
    db._create_index(
        cursor,
        "idx_session_knowledge_user",
        "session_knowledge_scopes",
        "user_id, session_id",
    )

    # Version 1 represents the existing MVP schema.  New structural changes are
    # applied only through numbered migrations so startup is deterministic and
    # a changed migration can never be silently accepted.
    _record_migration(db, cursor, 1, "mvp_baseline", "easyagent-knowledge-mvp-v1")
    _apply_p0_migration(db, cursor)
    _apply_team_space_manager_migration(db, cursor)
    _apply_session_scope_order_migration(db, cursor)
