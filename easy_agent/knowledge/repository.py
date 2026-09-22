"""Thin CRUD repository for the six-table knowledge MVP schema."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from ..db.database import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_dict(row: object | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class KnowledgeRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_base(
        self,
        *,
        name: str,
        description: str,
        space_type: str,
        owner_user_id: str,
        department_id: str | None,
        embedding_model: str,
        status: str = "creating",
        base_id: str | None = None,
    ) -> dict[str, Any]:
        base_id = base_id or str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_bases
                    (id, remote_dataset_id, name, description, space_type,
                     owner_user_id, department_id, embedding_model, status,
                     created_at, updated_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    base_id,
                    name,
                    description,
                    space_type,
                    owner_user_id,
                    department_id,
                    embedding_model,
                    status,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_base(base_id)

    def get_base(self, base_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, "SELECT * FROM knowledge_bases WHERE id=?", (base_id,)
            )
            return _row_dict(cursor.fetchone())

    def list_candidate_bases(
        self,
        *,
        user_id: str,
        department_id: str | None,
        include_all_team_spaces: bool = False,
    ) -> list[dict[str, Any]]:
        parameters: list[object] = [user_id, user_id]
        department_sql = ""
        if department_id:
            department_sql = """
                OR (b.space_type='team' AND b.department_id=?)
                OR (p.subject_type='department' AND p.subject_id=?)
            """
            parameters.extend([department_id, department_id])
        admin_sql = " OR b.space_type='team'" if include_all_team_spaces else ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT DISTINCT b.*
                FROM knowledge_bases b
                LEFT JOIN knowledge_permissions p ON p.base_id=b.id
                WHERE b.status!='deleted' AND (b.owner_user_id=?
                   OR (p.subject_type='user' AND p.subject_id=?)
                   {department_sql}
                   {admin_sql}
                   )
                ORDER BY b.updated_at DESC
                """,
                tuple(parameters),
            )
            return [dict(row) for row in cursor.fetchall()]

    def is_team_space_manager(self, user_id: str) -> bool:
        """Return whether the user has an immediately effective allowlist grant."""

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT 1
                FROM knowledge_team_space_managers m
                JOIN users u ON u.user_id=m.user_id
                WHERE m.user_id=?
                  AND u.account_status='active'
                  AND u.username<>'admin'
                  AND COALESCE(u.department_id, u.organization_id, '')<>''
                """,
                (user_id,),
            )
            return cursor.fetchone() is not None

    def list_active_departments(self) -> list[dict[str, Any]]:
        """Return departments backed by at least one active personnel account.

        The personnel table is the authoritative source in the first-phase
        deployment.  Legacy ``organization_id`` values remain supported while
        the explicit ``department_id`` migration is being completed.
        """

        department_expression = (
            "COALESCE(NULLIF(department_id, ''), NULLIF(organization_id, ''))"
        )
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT {department_expression} AS department_id,
                       MAX(COALESCE(department_name, '')) AS department_name
                FROM users
                WHERE account_status='active'
                  AND {department_expression} IS NOT NULL
                GROUP BY {department_expression}
                ORDER BY department_name, department_id
                """,
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_team_space_managers(self) -> list[dict[str, Any]]:
        """List grants, including suspended users so admin can explicitly revoke them."""

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT m.user_id, m.granted_by, m.granted_at, m.updated_at,
                       u.username, u.display_name, u.account_status,
                       COALESCE(NULLIF(u.department_id, ''), u.organization_id) AS department_id,
                       u.department_name
                FROM knowledge_team_space_managers m
                JOIN users u ON u.user_id=m.user_id
                ORDER BY u.department_name, u.display_name, u.username
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def grant_team_space_manager(
        self, *, user_id: str, granted_by: str
    ) -> dict[str, Any]:
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT user_id, username, account_status, "
                "COALESCE(NULLIF(department_id, ''), organization_id) AS department_id "
                "FROM users WHERE user_id=?",
                (user_id,),
            )
            user = cursor.fetchone()
            if user is None:
                raise LookupError("用户不存在")
            user = dict(user) if not isinstance(user, dict) else user
            if str(user.get("username")) == "admin":
                raise ValueError("admin 已具有全局团队空间管理权限")
            if str(user.get("account_status") or "active") != "active":
                raise ValueError("停用账号不能获得团队空间管理权限")
            if not str(user.get("department_id") or "").strip():
                raise ValueError("账号缺少部门，不能获得团队空间管理权限")
            self.db._execute(
                cursor,
                "SELECT user_id FROM knowledge_team_space_managers WHERE user_id=?",
                (user_id,),
            )
            if cursor.fetchone() is None:
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_team_space_managers
                        (user_id, granted_by, granted_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, granted_by, timestamp, timestamp),
                )
            else:
                self.db._execute(
                    cursor,
                    """
                    UPDATE knowledge_team_space_managers
                    SET granted_by=?, updated_at=? WHERE user_id=?
                    """,
                    (granted_by, timestamp, user_id),
                )
        for item in self.list_team_space_managers():
            if str(item["user_id"]) == user_id:
                return item
        raise RuntimeError("团队空间管理权限保存失败")

    def revoke_team_space_manager(self, user_id: str) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "DELETE FROM knowledge_team_space_managers WHERE user_id=?",
                (user_id,),
            )
            return cursor.rowcount > 0

    def update_base(self, base_id: str, **fields: object) -> dict[str, Any] | None:
        allowed = {
            "remote_dataset_id",
            "name",
            "description",
            "status",
            "deleted_at",
            "deleted_by",
            "purge_after",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_base(base_id)
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE knowledge_bases SET {assignments} WHERE id=?",
                tuple(updates.values()) + (base_id,),
            )
        return self.get_base(base_id)

    def delete_base(self, base_id: str) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "DELETE FROM knowledge_bases WHERE id=?", (base_id,))
            return cursor.rowcount > 0

    def create_folder(
        self, *, base_id: str, name: str, parent_id: str | None = None, sort_order: int = 0
    ) -> dict[str, Any]:
        folder_id = str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_folders
                    (id, base_id, parent_id, name, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (folder_id, base_id, parent_id, name, sort_order, timestamp, timestamp),
            )
        return self.get_folder(folder_id)

    def get_folder(self, folder_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, "SELECT * FROM knowledge_folders WHERE id=?", (folder_id,)
            )
            return _row_dict(cursor.fetchone())

    def list_folders(self, base_id: str) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT f.*, COUNT(DISTINCT d.id) AS document_count,
                       COUNT(DISTINCT child.id) AS child_count
                FROM knowledge_folders f
                LEFT JOIN knowledge_documents d
                    ON d.folder_id=f.id AND d.status!='deleted'
                LEFT JOIN knowledge_folders child ON child.parent_id=f.id
                WHERE f.base_id=?
                GROUP BY f.id
                ORDER BY f.sort_order, f.name
                """,
                (base_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_folder(self, folder_id: str, *, name: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "UPDATE knowledge_folders SET name=?, updated_at=? WHERE id=?",
                (name, _now(), folder_id),
            )
        return self.get_folder(folder_id)

    def delete_empty_folder(self, folder_id: str) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT COUNT(*) AS count FROM knowledge_documents WHERE folder_id=?",
                (folder_id,),
            )
            row = cursor.fetchone()
            count = row["count"] if isinstance(row, dict) else row[0]
            if count:
                return False
            self.db._execute(
                cursor, "SELECT COUNT(*) AS count FROM knowledge_folders WHERE parent_id=?", (folder_id,)
            )
            row = cursor.fetchone()
            child_count = row["count"] if isinstance(row, dict) else row[0]
            if child_count:
                return False
            self.db._execute(
                cursor, "DELETE FROM knowledge_folders WHERE id=?", (folder_id,)
            )
            return cursor.rowcount > 0

    def create_document(
        self,
        *,
        base_id: str,
        folder_id: str | None,
        name: str,
        content_type: str,
        size_bytes: int,
        created_by: str,
        status: str = "pending",
        document_id: str | None = None,
    ) -> dict[str, Any]:
        document_id = document_id or str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_documents
                    (id, remote_document_id, base_id, folder_id, name,
                     content_type, size_bytes, status, progress, error_code,
                     error_message, created_by, created_at, updated_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    document_id,
                    base_id,
                    folder_id,
                    name,
                    content_type,
                    size_bytes,
                    status,
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_document(document_id)

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT d.*, COALESCE(o.status, 'unmanaged') AS original_status,
                       o.sha256 AS original_sha256, o.storage_key AS original_storage_key,
                       o.provider AS original_provider
                FROM knowledge_documents d
                LEFT JOIN knowledge_document_objects o
                  ON o.document_id=d.id AND o.object_version=1
                WHERE d.id=?
                """,
                (document_id,),
            )
            return _row_dict(cursor.fetchone())

    def create_original_object(
        self,
        *,
        document_id: str,
        provider: str,
        storage_key: str,
        original_name: str,
        content_type: str,
        size_bytes: int,
        created_by: str,
    ) -> dict[str, Any]:
        object_id = str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_document_objects
                    (id, document_id, object_version, provider, storage_key,
                     status, original_name, content_type, size_bytes, sha256,
                     error_code, error_message, created_by, created_at)
                VALUES (?, ?, 1, ?, ?, 'staging', ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    object_id,
                    document_id,
                    provider,
                    storage_key,
                    original_name,
                    content_type,
                    size_bytes,
                    created_by,
                    timestamp,
                ),
            )
        return self.get_original_object(document_id)

    def get_original_object(self, document_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_document_objects
                WHERE document_id=?
                ORDER BY object_version DESC LIMIT 1
                """,
                (document_id,),
            )
            return _row_dict(cursor.fetchone())

    def update_original_object(
        self, document_id: str, **fields: object
    ) -> dict[str, Any] | None:
        allowed = {
            "storage_key",
            "status",
            "size_bytes",
            "sha256",
            "error_code",
            "error_message",
            "stored_at",
            "quarantined_at",
            "deleted_at",
            "last_verified_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_original_object(document_id)
        assignments = ", ".join(f"{column}=?" for column in updates)
        current = self.get_original_object(document_id)
        if current is None:
            return None
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE knowledge_document_objects SET {assignments} WHERE id=?",
                tuple(updates.values()) + (current["id"],),
            )
        return self.get_original_object(document_id)

    def create_original_access_audit(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        document_id: str,
        action: str,
        result: str,
        request_id: str = "",
    ) -> dict[str, Any]:
        audit_id = str(uuid.uuid4())
        created_at = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_original_access_audit
                    (id, user_id, session_id, agent_id, document_id,
                     action, result, request_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    user_id,
                    session_id,
                    agent_id,
                    document_id,
                    action,
                    result,
                    request_id,
                    created_at,
                ),
            )
        return {
            "id": audit_id,
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "document_id": document_id,
            "action": action,
            "result": result,
            "request_id": request_id,
            "created_at": created_at,
        }

    def list_documents_missing_originals(
        self, base_id: str | None = None, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses = ["d.status!='deleted'", "d.remote_document_id IS NOT NULL", "o.id IS NULL"]
        parameters: list[object] = []
        if base_id:
            clauses.append("d.base_id=?")
            parameters.append(base_id)
        parameters.append(limit)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT d.* FROM knowledge_documents d
                LEFT JOIN knowledge_document_objects o ON o.document_id=d.id
                WHERE {' AND '.join(clauses)}
                ORDER BY d.created_at LIMIT ?
                """,
                tuple(parameters),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_documents(
        self,
        base_id: str,
        *,
        folder_id: str | None = None,
        direct_only: bool = False,
        status: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["d.base_id=?", "d.status!='deleted'"]
        parameters: list[object] = [base_id]
        if direct_only:
            if folder_id is None:
                clauses.append("d.folder_id IS NULL")
            else:
                clauses.append("d.folder_id=?")
                parameters.append(folder_id)
        elif folder_id is not None:
            clauses.append("d.folder_id=?")
            parameters.append(folder_id)
        if status:
            clauses.append("d.status=?")
            parameters.append(status)
        if query:
            clauses.append("LOWER(d.name) LIKE ?")
            parameters.append(f"%{query.lower()}%")
        parameters.extend([limit, offset])
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT d.*, COALESCE(o.status, 'unmanaged') AS original_status,
                       o.sha256 AS original_sha256, o.storage_key AS original_storage_key,
                       o.provider AS original_provider
                FROM knowledge_documents d
                LEFT JOIN knowledge_document_objects o
                  ON o.document_id=d.id AND o.object_version=1
                WHERE {' AND '.join(clauses)}
                ORDER BY d.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple(parameters),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_documents_filtered(
        self,
        base_id: str,
        *,
        folder_id: str | None = None,
        direct_only: bool = False,
        status: str | None = None,
        query: str | None = None,
    ) -> int:
        clauses = ["base_id=?", "status!='deleted'"]
        parameters: list[object] = [base_id]
        if direct_only:
            if folder_id is None:
                clauses.append("folder_id IS NULL")
            else:
                clauses.append("folder_id=?")
                parameters.append(folder_id)
        elif folder_id is not None:
            clauses.append("folder_id=?")
            parameters.append(folder_id)
        if status:
            clauses.append("status=?")
            parameters.append(status)
        if query:
            clauses.append("LOWER(name) LIKE ?")
            parameters.append(f"%{query.lower()}%")
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"SELECT COUNT(*) AS count FROM knowledge_documents WHERE {' AND '.join(clauses)}",
                tuple(parameters),
            )
            row = cursor.fetchone()
            return int(row["count"] if isinstance(row, dict) else row[0])

    def get_documents(self, base_id: str, document_ids: Iterable[str]) -> list[dict[str, Any]]:
        document_ids = list(dict.fromkeys(document_ids))
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT d.*, COALESCE(o.status, 'unmanaged') AS original_status,
                       o.sha256 AS original_sha256, o.storage_key AS original_storage_key,
                       o.provider AS original_provider
                FROM knowledge_documents d
                LEFT JOIN knowledge_document_objects o
                  ON o.document_id=d.id AND o.object_version=1
                WHERE d.base_id=? AND d.id IN ({placeholders}) AND d.status!='deleted'
                """,
                (base_id, *document_ids),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_documents_by_remote_ids(
        self, base_id: str, remote_document_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        remote_document_ids = list(dict.fromkeys(remote_document_ids))
        if not remote_document_ids:
            return []
        placeholders = ",".join("?" for _ in remote_document_ids)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT d.*, COALESCE(o.status, 'unmanaged') AS original_status,
                       o.sha256 AS original_sha256, o.storage_key AS original_storage_key,
                       o.provider AS original_provider
                FROM knowledge_documents d
                LEFT JOIN knowledge_document_objects o
                  ON o.document_id=d.id AND o.object_version=1
                WHERE d.base_id=? AND d.remote_document_id IN ({placeholders})
                  AND d.status!='deleted'
                """,
                (base_id, *remote_document_ids),
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_document(
        self, document_id: str, **fields: object
    ) -> dict[str, Any] | None:
        allowed = {
            "remote_document_id",
            "folder_id",
            "status",
            "progress",
            "error_code",
            "error_message",
            "deleted_at",
            "deleted_by",
            "purge_after",
            "previous_status",
            "document_version",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_document(document_id)
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE knowledge_documents SET {assignments} WHERE id=?",
                tuple(updates.values()) + (document_id,),
            )
        return self.get_document(document_id)

    def list_all_bases(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            sql = "SELECT * FROM knowledge_bases"
            if not include_deleted:
                sql += " WHERE status!='deleted'"
            sql += " ORDER BY created_at"
            self.db._execute(cursor, sql)
            return [dict(row) for row in cursor.fetchall()]

    def list_all_documents(
        self, *, base_id: str | None = None, include_deleted: bool = False, limit: int = 2000
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[object] = []
        if base_id:
            clauses.append("d.base_id=?")
            parameters.append(base_id)
        if not include_deleted:
            clauses.append("d.status!='deleted'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT d.*, o.status AS original_status, o.storage_key AS original_storage_key,
                       o.sha256 AS original_sha256, o.size_bytes AS original_size_bytes
                FROM knowledge_documents d
                LEFT JOIN knowledge_document_objects o
                  ON o.document_id=d.id AND o.object_version=1
                {where} ORDER BY d.created_at LIMIT ?
                """,
                (*parameters, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_due_for_purge(self, *, before: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_documents
                WHERE status='deleted' AND purge_after IS NOT NULL AND purge_after<=?
                ORDER BY purge_after LIMIT ?
                """,
                (before, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_bases_due_for_purge(
        self, *, before: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_bases
                WHERE status='deleted' AND purge_after IS NOT NULL AND purge_after<=?
                ORDER BY purge_after LIMIT ?
                """,
                (before, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_documents(self, base_id: str) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT COUNT(*) AS count FROM knowledge_documents
                WHERE base_id=? AND status!='deleted'
                """,
                (base_id,),
            )
            row = cursor.fetchone()
            return int(row["count"] if isinstance(row, dict) else row[0])

    def list_permissions(self, base_id: str) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_permissions
                WHERE base_id=? ORDER BY subject_type, subject_id
                """,
                (base_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def replace_permissions(
        self,
        *,
        base_id: str,
        permissions: Iterable[Mapping[str, str]],
        created_by: str,
    ) -> list[dict[str, Any]]:
        timestamp = _now()
        items = list(permissions)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, "DELETE FROM knowledge_permissions WHERE base_id=?", (base_id,)
            )
            for item in items:
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_permissions
                        (id, base_id, subject_type, subject_id, role,
                         created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        base_id,
                        item["subject_type"],
                        item["subject_id"],
                        item["role"],
                        created_by,
                        timestamp,
                        timestamp,
                    ),
                )
        return self.list_permissions(base_id)

    def create_operation(
        self,
        *,
        operation_type: str,
        resource_type: str,
        resource_id: str,
        phase: str,
        status: str,
        created_by: str,
        retryable: bool = False,
    ) -> dict[str, Any]:
        operation_id = str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_operations
                    (id, type, resource_type, resource_id, phase, status,
                     current_count, total_count, progress, retryable, error_code,
                     error_message, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 1, NULL, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    operation_id,
                    operation_type,
                    resource_type,
                    resource_id,
                    phase,
                    status,
                    int(retryable),
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_operations WHERE id=?",
                (operation_id,),
            )
            return _row_dict(cursor.fetchone())

    def get_active_document_operation(
        self, document_id: str
    ) -> dict[str, Any] | None:
        """Return the newest unfinished upload/parse operation for a document."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_operations
                WHERE resource_type='document' AND resource_id=?
                  AND status IN ('accepted', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (document_id,),
            )
            return _row_dict(cursor.fetchone())

    def get_latest_document_operation(
        self, document_id: str
    ) -> dict[str, Any] | None:
        """Return the newest operation, including a previously timed-out one."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_operations
                WHERE resource_type='document' AND resource_id=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (document_id,),
            )
            return _row_dict(cursor.fetchone())

    def update_operation(
        self, operation_id: str, **fields: object
    ) -> dict[str, Any] | None:
        allowed = {
            "phase",
            "status",
            "current_count",
            "total_count",
            "progress",
            "retryable",
            "error_code",
            "error_message",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "retryable" in updates:
            updates["retryable"] = int(bool(updates["retryable"]))
        if not updates:
            return self.get_operation(operation_id)
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE knowledge_operations SET {assignments} WHERE id=?",
                tuple(updates.values()) + (operation_id,),
            )
        return self.get_operation(operation_id)

    def update_active_document_operations(
        self, document_id: str, **fields: object
    ) -> int:
        """Keep upload/parse operations aligned with the upstream document state."""
        allowed = {
            "phase", "status", "current_count", "total_count", "progress",
            "retryable", "error_code", "error_message",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "retryable" in updates:
            updates["retryable"] = int(bool(updates["retryable"]))
        if not updates:
            return 0
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                UPDATE knowledge_operations SET {assignments}
                WHERE resource_type='document' AND resource_id=?
                  AND status IN ('accepted', 'running')
                """,
                tuple(updates.values()) + (document_id,),
            )
            return cursor.rowcount

    def list_operations(
        self, *, created_by: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_operations
                WHERE created_by=? ORDER BY updated_at DESC LIMIT ? OFFSET ?
                """,
                (created_by, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_operations(self, *, created_by: str) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT COUNT(*) AS count FROM knowledge_operations WHERE created_by=?",
                (created_by,),
            )
            row = cursor.fetchone()
            return int(row["count"] if isinstance(row, dict) else row[0])

    def find_permission_subjects(
        self, *, subject_type: str, query: str, limit: int = 20
    ) -> list[dict[str, str]]:
        query_pattern = f"%{query.lower()}%"
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if subject_type == "user":
                self.db._execute(
                    cursor,
                    """
                    SELECT user_id AS subject_id, username AS label,
                           organization_id AS secondary
                    FROM users
                    WHERE LOWER(username) LIKE ? OR LOWER(email) LIKE ?
                    ORDER BY username LIMIT ?
                    """,
                    (query_pattern, query_pattern, limit),
                )
            elif subject_type == "department":
                self.db._execute(
                    cursor,
                    """
                    SELECT DISTINCT organization_id AS subject_id,
                           organization_id AS label, '' AS secondary
                    FROM users
                    WHERE organization_id!='' AND LOWER(organization_id) LIKE ?
                    ORDER BY organization_id LIMIT ?
                    """,
                    (query_pattern, limit),
                )
            else:
                raise ValueError("unsupported permission subject type")
            return [dict(row) for row in cursor.fetchall()]

    def replace_session_scope(
        self, *, session_id: str, user_id: str, base_ids: Iterable[str]
    ) -> list[str]:
        base_ids = list(dict.fromkeys(base_ids))
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "DELETE FROM session_knowledge_scopes WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            )
            for sort_order, base_id in enumerate(base_ids):
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO session_knowledge_scopes
                        (session_id, base_id, user_id, created_at, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, base_id, user_id, timestamp, sort_order),
                )
        return self.get_session_scope(session_id=session_id, user_id=user_id)

    def get_session_scope(self, *, session_id: str, user_id: str) -> list[str]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT base_id FROM session_knowledge_scopes
                WHERE session_id=? AND user_id=?
                ORDER BY sort_order, created_at, base_id
                """,
                (session_id, user_id),
            )
            return [row["base_id"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()]

    def has_session_scope(self, *, session_id: str) -> bool:
        """Return whether a session has any saved knowledge selection.

        This intentionally does not accept a caller-supplied user id.  Chat uses
        it only as a fail-closed switch: once a scope exists, a strict bearer
        identity is required before any selected knowledge can be read.
        """

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT 1 FROM session_knowledge_scopes
                WHERE session_id=? LIMIT 1
                """,
                (session_id,),
            )
            return cursor.fetchone() is not None


__all__ = ["KnowledgeRepository"]
