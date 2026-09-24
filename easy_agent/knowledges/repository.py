"""新版知识库 CRUD 数据访问层。

表名/列与旧版完全一致（复用现有数据）；方法签名与旧版
``knowledge/repository.py`` 保持兼容，裁剪了对账、回收站巡检等
服务层不再使用的查询分支。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from ..db.database import Database

logger = logging.getLogger(__name__)

_BASE_UPDATE_FIELDS = {
    "remote_dataset_id", "name", "description", "status",
    "deleted_at", "deleted_by", "purge_after",
}
_DOCUMENT_UPDATE_FIELDS = {
    "remote_document_id", "folder_id", "status", "progress", "error_code",
    "error_message", "deleted_at", "deleted_by", "purge_after",
    "previous_status", "document_version",
}
_OBJECT_UPDATE_FIELDS = {
    "storage_key", "status", "size_bytes", "sha256", "error_code",
    "error_message", "stored_at", "quarantined_at", "deleted_at", "last_verified_at",
}
_OPERATION_UPDATE_FIELDS = {
    "phase", "status", "current_count", "total_count", "progress",
    "retryable", "error_code", "error_message",
}

# 文档查询统一带出原始文件对象状态，供服务层组装 DocumentSummary
_DOCUMENT_SELECT = """
    SELECT d.*, COALESCE(o.status, 'unmanaged') AS original_status,
           o.sha256 AS original_sha256, o.storage_key AS original_storage_key,
           o.provider AS original_provider
    FROM knowledge_documents d
    LEFT JOIN knowledge_document_objects o
      ON o.document_id=d.id AND o.object_version=1
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_dict(row: object | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class KnowledgeRepository:
    """知识库模块唯一数据访问入口（事务由 Database.get_connection 管理）。"""

    def __init__(self, db: Database):
        self.db = db

    # ---------------- 通用内部辅助 ----------------

    def _update_by_id(
        self, table: str, row_id: str, allowed: set[str], fields: Mapping[str, object]
    ) -> None:
        """按白名单过滤字段后更新单行，并刷新 updated_at。"""
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE {table} SET {assignments} WHERE id=?",
                (*updates.values(), row_id),
            )

    def _count(self, sql: str, params: tuple) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, params)
            row = cursor.fetchone()
            return int(row["count"] if isinstance(row, dict) else row[0])

    @staticmethod
    def _document_filters(
        base_id: str,
        *,
        folder_id: str | None = None,
        direct_only: bool = False,
        status: str | None = None,
        query: str | None = None,
    ) -> tuple[str, list[object]]:
        clauses = ["d.base_id=?", "d.status!='deleted'"]
        parameters: list[object] = [base_id]
        if direct_only or folder_id is not None:
            if folder_id is None:
                clauses.append("d.folder_id IS NULL")
            else:
                clauses.append("d.folder_id=?")
                parameters.append(folder_id)
        if status:
            clauses.append("d.status=?")
            parameters.append(status)
        if query:
            clauses.append("LOWER(d.name) LIKE ?")
            parameters.append(f"%{query.lower()}%")
        return " AND ".join(clauses), parameters

    def _select_documents_where(self, where: str, params: tuple) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, f"{_DOCUMENT_SELECT} WHERE {where}", params)
            return [dict(row) for row in cursor.fetchall()]

    # ---------------- 知识库（个人/公共空间） ----------------

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
                    base_id, name, description, space_type, owner_user_id,
                    department_id, embedding_model, status, timestamp, timestamp,
                ),
            )
        return self.get_base(base_id)

    def get_base(self, base_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "SELECT * FROM knowledge_bases WHERE id=?", (base_id,))
            return _row_dict(cursor.fetchone())

    def list_candidate_bases(
        self,
        *,
        user_id: str,
        department_id: str | None,
        include_all_team_spaces: bool = False,
    ) -> list[dict[str, Any]]:
        """列出一个用户可见的知识库。

        公共（team）空间需要显式授权：owner、空间管理/查看白名单，
        或单库 department 权限行（fail-closed，不再默认同部门可见）。
        """
        parameters: list[object] = [user_id, user_id, user_id, user_id]
        department_sql = ""
        if department_id:
            department_sql = " OR (p.subject_type='department' AND p.subject_id=?)"
            parameters.append(department_id)
        admin_sql = " OR b.space_type='team'" if include_all_team_spaces else ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT DISTINCT b.* FROM knowledge_bases b
                LEFT JOIN knowledge_permissions p ON p.base_id=b.id
                WHERE b.status!='deleted' AND (b.owner_user_id=?
                   OR (p.subject_type='user' AND p.subject_id=?)
                   OR (b.space_type='team' AND EXISTS (
                        SELECT 1 FROM knowledge_team_space_managers m
                        JOIN users mu ON mu.user_id=m.user_id
                        WHERE m.user_id=?
                          AND COALESCE(NULLIF(mu.department_id, ''), mu.organization_id)=b.department_id))
                   OR (b.space_type='team' AND EXISTS (
                        SELECT 1 FROM knowledge_team_space_viewers v
                        JOIN users vu ON vu.user_id=v.user_id
                        WHERE v.user_id=?
                          AND COALESCE(NULLIF(vu.department_id, ''), vu.organization_id)=b.department_id))
                   {department_sql}{admin_sql})
                ORDER BY b.updated_at DESC
                """,
                tuple(parameters),
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_base(self, base_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_by_id("knowledge_bases", base_id, _BASE_UPDATE_FIELDS, fields)
        return self.get_base(base_id)

    def delete_base(self, base_id: str) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "DELETE FROM knowledge_bases WHERE id=?", (base_id,))
            return cursor.rowcount > 0

    def count_documents(self, base_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) AS count FROM knowledge_documents "
            "WHERE base_id=? AND status!='deleted'",
            (base_id,),
        )

    # ---------------- 公共空间白名单（管理/查看） ----------------

    def is_team_space_manager(self, user_id: str) -> bool:
        """用户是否持有立即生效的公共空间管理授权。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT 1 FROM knowledge_team_space_managers m
                JOIN users u ON u.user_id=m.user_id
                WHERE m.user_id=?
                  AND u.account_status='active'
                  AND u.username<>'admin'
                  AND COALESCE(u.department_id, u.organization_id, '')<>''
                """,
                (user_id,),
            )
            return cursor.fetchone() is not None

    def is_team_space_viewer(self, user_id: str) -> bool:
        """用户是否持有公共空间查看授权。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT 1 FROM knowledge_team_space_viewers v
                JOIN users u ON u.user_id=v.user_id
                WHERE v.user_id=?
                  AND u.account_status='active'
                  AND u.username<>'admin'
                """,
                (user_id,),
            )
            return cursor.fetchone() is not None

    def list_active_departments(self) -> list[dict[str, Any]]:
        """列出至少有一名在职账号的部门（人员表为第一阶段的权威来源）。"""
        expression = "COALESCE(NULLIF(department_id, ''), NULLIF(organization_id, ''))"
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT {expression} AS department_id,
                       MAX(COALESCE(department_name, '')) AS department_name
                FROM users
                WHERE account_status='active' AND {expression} IS NOT NULL
                GROUP BY {expression}
                ORDER BY department_name, department_id
                """,
            )
            return [dict(row) for row in cursor.fetchall()]

    # 公共空间 manager/viewer 白名单共用同一套 grant 表结构；
    # 表名由代码内硬编码传入，不来自用户输入。
    def _list_team_space_grants(self, table: str) -> list[dict[str, Any]]:
        """列出全部授权（含已停用账号，便于管理员显式回收）。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT g.user_id, g.granted_by, g.granted_at, g.updated_at,
                       u.username, u.display_name, u.account_status,
                       COALESCE(NULLIF(u.department_id, ''), u.organization_id) AS department_id,
                       u.department_name
                FROM {table} g JOIN users u ON u.user_id=g.user_id
                ORDER BY u.department_name, u.display_name, u.username
                """,
            )
            return [dict(row) for row in cursor.fetchall()]

    def _grant_team_space_permission(
        self,
        *,
        table: str,
        user_id: str,
        granted_by: str,
        label: str,
        require_department: bool,
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
            user = dict(user)
            if user["username"] == "admin":
                raise ValueError(f"admin 已具有全局公共空间{label}权限")
            if str(user.get("account_status") or "active") != "active":
                raise ValueError(f"停用账号不能获得公共空间{label}权限")
            if require_department and not str(user.get("department_id") or "").strip():
                raise ValueError("账号缺少部门，不能获得公共空间管理权限")
            self.db._execute(cursor, f"SELECT user_id FROM {table} WHERE user_id=?", (user_id,))
            if cursor.fetchone() is None:
                self.db._execute(
                    cursor,
                    f"INSERT INTO {table} (user_id, granted_by, granted_at, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, granted_by, timestamp, timestamp),
                )
            else:
                self.db._execute(
                    cursor,
                    f"UPDATE {table} SET granted_by=?, updated_at=? WHERE user_id=?",
                    (granted_by, timestamp, user_id),
                )
        for item in self._list_team_space_grants(table):
            if str(item["user_id"]) == user_id:
                return item
        raise RuntimeError(f"公共空间{label}权限保存失败")

    def _revoke_team_space_grant(self, table: str, user_id: str) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, f"DELETE FROM {table} WHERE user_id=?", (user_id,))
            return cursor.rowcount > 0

    def list_team_space_managers(self) -> list[dict[str, Any]]:
        return self._list_team_space_grants("knowledge_team_space_managers")

    def grant_team_space_manager(self, *, user_id: str, granted_by: str) -> dict[str, Any]:
        return self._grant_team_space_permission(
            table="knowledge_team_space_managers",
            user_id=user_id, granted_by=granted_by, label="管理", require_department=True,
        )

    def revoke_team_space_manager(self, user_id: str) -> bool:
        return self._revoke_team_space_grant("knowledge_team_space_managers", user_id)

    def list_team_space_viewers(self) -> list[dict[str, Any]]:
        return self._list_team_space_grants("knowledge_team_space_viewers")

    def grant_team_space_viewer(self, *, user_id: str, granted_by: str) -> dict[str, Any]:
        return self._grant_team_space_permission(
            table="knowledge_team_space_viewers",
            user_id=user_id, granted_by=granted_by, label="查看", require_department=False,
        )

    def revoke_team_space_viewer(self, user_id: str) -> bool:
        return self._revoke_team_space_grant("knowledge_team_space_viewers", user_id)

    # ---------------- 文件夹 ----------------

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
            self.db._execute(cursor, "SELECT * FROM knowledge_folders WHERE id=?", (folder_id,))
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
                LEFT JOIN knowledge_documents d ON d.folder_id=f.id AND d.status!='deleted'
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
        """仅删除既无文档也无子文件夹的空目录。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if self._count(
                "SELECT COUNT(*) AS count FROM knowledge_documents WHERE folder_id=?", (folder_id,)
            ):
                return False
            if self._count(
                "SELECT COUNT(*) AS count FROM knowledge_folders WHERE parent_id=?", (folder_id,)
            ):
                return False
            self.db._execute(cursor, "DELETE FROM knowledge_folders WHERE id=?", (folder_id,))
            return cursor.rowcount > 0

    # ---------------- 文档 ----------------

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
                    document_id, base_id, folder_id, name, content_type,
                    size_bytes, status, created_by, timestamp, timestamp,
                ),
            )
        return self.get_document(document_id)

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, f"{_DOCUMENT_SELECT} WHERE d.id=?", (document_id,)
            )
            return _row_dict(cursor.fetchone())

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
        where, parameters = self._document_filters(
            base_id, folder_id=folder_id, direct_only=direct_only, status=status, query=query
        )
        return self._select_documents_where(
            f"{where} ORDER BY d.updated_at DESC LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        )

    def count_documents_filtered(
        self,
        base_id: str,
        *,
        folder_id: str | None = None,
        direct_only: bool = False,
        status: str | None = None,
        query: str | None = None,
    ) -> int:
        where, parameters = self._document_filters(
            base_id, folder_id=folder_id, direct_only=direct_only, status=status, query=query
        )
        return self._count(
            f"SELECT COUNT(*) AS count FROM knowledge_documents d WHERE {where}",
            tuple(parameters),
        )

    def get_documents(self, base_id: str, document_ids: Iterable[str]) -> list[dict[str, Any]]:
        return self._get_documents_by_column(base_id, "d.id", document_ids)

    def get_documents_by_remote_ids(
        self, base_id: str, remote_document_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        return self._get_documents_by_column(base_id, "d.remote_document_id", remote_document_ids)

    def _get_documents_by_column(
        self, base_id: str, column: str, values: Iterable[str]
    ) -> list[dict[str, Any]]:
        values = list(dict.fromkeys(values))
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        return self._select_documents_where(
            f"d.base_id=? AND {column} IN ({placeholders}) AND d.status!='deleted'",
            (base_id, *values),
        )

    def update_document(self, document_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_by_id("knowledge_documents", document_id, _DOCUMENT_UPDATE_FIELDS, fields)
        return self.get_document(document_id)

    # ---------------- 原始文件对象 ----------------

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
                    object_id, document_id, provider, storage_key,
                    original_name, content_type, size_bytes, created_by, timestamp,
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

    def update_original_object(self, document_id: str, **fields: object) -> dict[str, Any] | None:
        current = self.get_original_object(document_id)
        if current is None:
            return None
        self._update_by_id(
            "knowledge_document_objects", str(current["id"]), _OBJECT_UPDATE_FIELDS, fields
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
                    audit_id, user_id, session_id, agent_id, document_id,
                    action, result, request_id, created_at,
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

    # ---------------- 权限 ----------------

    def list_permissions(self, base_id: str) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_permissions WHERE base_id=? ORDER BY subject_type, subject_id",
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
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "DELETE FROM knowledge_permissions WHERE base_id=?", (base_id,))
            for item in permissions:
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_permissions
                        (id, base_id, subject_type, subject_id, role,
                         created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), base_id, item["subject_type"],
                        item["subject_id"], item["role"], created_by, timestamp, timestamp,
                    ),
                )
        return self.list_permissions(base_id)

    def find_permission_subjects(
        self, *, subject_type: str, query: str, limit: int = 20
    ) -> list[dict[str, str]]:
        pattern = f"%{query.lower()}%"
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if subject_type == "user":
                self.db._execute(
                    cursor,
                    """
                    SELECT user_id AS subject_id, username AS label, organization_id AS secondary
                    FROM users
                    WHERE LOWER(username) LIKE ? OR LOWER(email) LIKE ?
                    ORDER BY username LIMIT ?
                    """,
                    (pattern, pattern, limit),
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
                    (pattern, limit),
                )
            else:
                raise ValueError("unsupported permission subject type")
            return [dict(row) for row in cursor.fetchall()]

    # ---------------- 操作记录 ----------------

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
                    operation_id, operation_type, resource_type, resource_id,
                    phase, status, int(retryable), created_by, timestamp, timestamp,
                ),
            )
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "SELECT * FROM knowledge_operations WHERE id=?", (operation_id,))
            return _row_dict(cursor.fetchone())

    def get_active_document_operation(self, document_id: str) -> dict[str, Any] | None:
        """文档最新一条未完成的 upload/parse 操作。"""
        return self._latest_document_operation(document_id, active_only=True)

    def get_latest_document_operation(self, document_id: str) -> dict[str, Any] | None:
        """文档最新一条操作（含已超时的历史操作）。"""
        return self._latest_document_operation(document_id, active_only=False)

    def _latest_document_operation(self, document_id: str, *, active_only: bool) -> dict[str, Any] | None:
        status_sql = " AND status IN ('accepted', 'running')" if active_only else ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT * FROM knowledge_operations
                WHERE resource_type='document' AND resource_id=?{status_sql}
                ORDER BY created_at DESC LIMIT 1
                """,
                (document_id,),
            )
            return _row_dict(cursor.fetchone())

    def update_operation(self, operation_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_operation_rows(
            "UPDATE knowledge_operations SET {assignments} WHERE id=?",
            fields,
            (operation_id,),
        )
        return self.get_operation(operation_id)

    def update_active_document_operations(self, document_id: str, **fields: object) -> int:
        """让进行中的 upload/parse 操作与上游文档状态保持一致。"""
        return self._update_operation_rows(
            """
            UPDATE knowledge_operations SET {assignments}
            WHERE resource_type='document' AND resource_id=? AND status IN ('accepted', 'running')
            """,
            fields,
            (document_id,),
        )

    def _update_operation_rows(self, sql_template: str, fields: Mapping[str, object], where: tuple) -> int:
        updates = {key: value for key, value in fields.items() if key in _OPERATION_UPDATE_FIELDS}
        if not updates:
            return 0
        if "retryable" in updates:
            updates["retryable"] = int(bool(updates["retryable"]))
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, sql_template.format(assignments=assignments), (*updates.values(), *where)
            )
            return cursor.rowcount

    def list_operations(
        self, *, created_by: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_operations WHERE created_by=? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (created_by, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_operations(self, *, created_by: str) -> int:
        return self._count(
            "SELECT COUNT(*) AS count FROM knowledge_operations WHERE created_by=?",
            (created_by,),
        )

    # ---------------- 会话知识范围 ----------------

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
            return [
                row["base_id"] if isinstance(row, dict) else row[0]
                for row in cursor.fetchall()
            ]

    def has_session_scope(self, *, session_id: str) -> bool:
        """会话是否保存过任何知识选择（fail-closed 开关：一旦存在，
        读取所选知识前必须持有严格 Bearer 身份）。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT 1 FROM session_knowledge_scopes WHERE session_id=? LIMIT 1",
                (session_id,),
            )
            return cursor.fetchone() is not None


__all__ = ["KnowledgeRepository"]
