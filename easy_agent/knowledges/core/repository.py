"""知识库 CRUD 数据访问层。"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from easy_agent.knowledges.core.repository_base import RepositoryBaseMixin, _now
from easy_agent.knowledges.core.repository_bases import BaseRepositoryMixin
from easy_agent.knowledges.core.repository_documents import DocumentRepositoryMixin
from easy_agent.knowledges.core.repository_folders import FolderRepositoryMixin

logger = logging.getLogger(__name__)

_OBJECT_UPDATE_FIELDS = {
    "storage_key", "status", "size_bytes", "sha256", "error_code",
    "error_message", "stored_at", "deleted_at",
}
_OPERATION_UPDATE_FIELDS = {
    "phase", "status", "current_count", "total_count", "progress",
    "retryable", "error_code", "error_message",
}
_SYNC_ITEM_UPDATE_FIELDS = {
    "document_id", "status", "attempts", "last_error", "publish_date",
}




class KnowledgeRepository(
    BaseRepositoryMixin,
    DocumentRepositoryMixin,
    FolderRepositoryMixin,
    RepositoryBaseMixin,
):
    """知识库模块唯一数据访问入口（事务由 Database.get_connection 管理）。"""



    # ---------------- 文档 SQL 同步台账 ----------------

    def create_or_get_sync_item(
        self, *, source: str, source_record_id: str, publish_date: str | None
    ) -> dict[str, Any]:
        """幂等创建同步台账行；已存在时直接返回现有行。"""
        item_id = str(uuid.uuid4())
        timestamp = _now()
        try:
            self._execute_write(
                """
                INSERT INTO knowledge_source_sync_items
                    (id, source, source_record_id, document_id, status,
                     attempts, last_error, publish_date, created_at, updated_at)
                VALUES (?, ?, ?, NULL, 'queued', 0, NULL, ?, ?, ?)
                """,
                (item_id, source, source_record_id, publish_date, timestamp, timestamp),
            )
        except Exception:  # noqa: BLE001 — UNIQUE 冲突：并发/重跑时复用已有行
            existing = self.get_sync_item(source, source_record_id)
            if existing is None:
                raise
            return existing
        return self.get_sync_item(source, source_record_id) or {}

    def get_sync_item(self, source: str, source_record_id: str) -> dict[str, Any] | None:
        return self._fetch_one(
            "SELECT * FROM knowledge_source_sync_items "
            "WHERE source=? AND source_record_id=?",
            (source, source_record_id),
        )

    def update_sync_item(self, item_id: str, **fields: object) -> None:
        self._update_by_id(
            "knowledge_source_sync_items",
            item_id,
            _SYNC_ITEM_UPDATE_FIELDS,
            fields,
        )

    def list_sync_items(
        self, *, source: str, statuses: Iterable[str], limit: int = 100
    ) -> list[dict[str, Any]]:
        statuses = list(dict.fromkeys(statuses))
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        return self._fetch_all(
            "SELECT * FROM knowledge_source_sync_items "
            f"WHERE source=? AND status IN ({placeholders}) "
            "ORDER BY updated_at LIMIT ?",
            (source, *statuses, limit),
        )

    def count_sync_items(self, *, source: str) -> dict[str, int]:
        """按状态统计同步台账（用于完整性核对：总数 = 各状态之和）。"""
        rows = self._fetch_all(
            "SELECT status, COUNT(*) AS count FROM knowledge_source_sync_items "
            "WHERE source=? GROUP BY status",
            (source,),
        )
        return {str(row["status"]): int(row["count"]) for row in rows}

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
        self._execute_write(
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
        return self._fetch_one(
            """
            SELECT * FROM knowledge_document_objects
            WHERE document_id=?
            ORDER BY object_version DESC LIMIT 1
            """,
            (document_id,),
        )

    def update_original_object(self, document_id: str, **fields: object) -> dict[str, Any] | None:
        current = self.get_original_object(document_id)
        if current is None:
            return None
        self._update_by_id(
            "knowledge_document_objects", str(current["id"]), _OBJECT_UPDATE_FIELDS, fields
        )
        return self.get_original_object(document_id)

    # ---------------- 权限 ----------------

    def list_permissions(self, base_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            "SELECT * FROM knowledge_permissions WHERE base_id=? ORDER BY subject_type, subject_id",
            (base_id,),
        )

    def list_permissions_for_bases(self, base_ids: Iterable[str]) -> list[dict[str, Any]]:
        """批量查询多个知识库的授权行，避免列表页逐库查询。"""

        ids = list(dict.fromkeys(base_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return self._fetch_all(
            f"SELECT * FROM knowledge_permissions WHERE base_id IN ({placeholders}) "
            "ORDER BY base_id, subject_type, subject_id",
            tuple(ids),
        )

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

    def list_permission_directory_users(
        self, *, exclude_user_id: str = ""
    ) -> list[dict[str, Any]]:
        """列出权限管理可选人员目录（在职账号，含部门/处室/团队层级）。

        exclude_user_id 用于剔除库 owner——owner 天然可见，无需出现在
        勾选名单中。部门标识优先 department_id，缺失时回退 organization_id。
        """

        department_expr = "COALESCE(NULLIF(department_id, ''), NULLIF(organization_id, ''))"
        return self._fetch_all(
            f"""
            SELECT user_id,
                   COALESCE(username, '') AS username,
                   COALESCE(NULLIF(display_name, ''), username, '') AS display_name,
                   COALESCE({department_expr}, '') AS department_id,
                   COALESCE(NULLIF(department_name, ''), {department_expr}, '') AS department_name,
                   COALESCE(division_name, '') AS division_name,
                   COALESCE(team_name, '') AS team_name
            FROM users
            WHERE account_status='active' AND username<>'admin'
              AND (?='' OR user_id<>?)
            ORDER BY department_name, division_name, team_name, display_name
            """,
            (exclude_user_id, exclude_user_id),
        )

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
        self._execute_write(
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
        return self._fetch_one(
            "SELECT * FROM knowledge_operations WHERE id=?", (operation_id,)
        )

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
        updates = self._filter_update_fields(
            fields, _OPERATION_UPDATE_FIELDS, coerce_retryable=True
        )
        if not updates:
            return 0
        assignments = ", ".join(f"{column}=?" for column in updates)
        return self._execute_write(
            sql_template.format(assignments=assignments), (*updates.values(), *where)
        )

    def list_operations(
        self, *, created_by: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._fetch_all(
            "SELECT * FROM knowledge_operations WHERE created_by=? "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (created_by, limit, offset),
        )

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
        rows = self._fetch_all(
            """
            SELECT base_id FROM session_knowledge_scopes
            WHERE session_id=? AND user_id=?
            ORDER BY sort_order, created_at, base_id
            """,
            (session_id, user_id),
        )
        return [row["base_id"] for row in rows]

    def has_session_scope(self, *, session_id: str) -> bool:
        """会话是否保存过任何知识选择（fail-closed 开关：一旦存在，
        读取所选知识前必须持有严格 Bearer 身份）。"""
        row = self._fetch_one(
            "SELECT 1 FROM session_knowledge_scopes WHERE session_id=? LIMIT 1",
            (session_id,),
        )
        return row is not None

    def list_chat_session_rows(
        self, *, user_id: str, base_id: str
    ) -> list[tuple[str, str, str]]:
        """按知识范围关联反查会话，返回 (session_id, username, title)，新创建者优先。

        供面板问答定位「当前轮」会话：同一用户对同一知识库可能存在多轮
        隐藏会话（开启新对话会轮换），created_at 最新者即当前轮。
        """
        rows = self._fetch_all(
            """
            SELECT s.session_id, s.username, s.title
            FROM sessions s
            JOIN session_knowledge_scopes sc ON sc.session_id = s.session_id
            WHERE sc.user_id=? AND sc.base_id=?
            ORDER BY s.created_at DESC
            """,
            (user_id, base_id),
        )
        return [
            (row["session_id"], row["username"], row["title"]) for row in rows
        ]


__all__ = ["KnowledgeRepository"]
