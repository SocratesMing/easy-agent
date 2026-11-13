"""入库台账与原文存储数据访问：同步台账行、原始文件对象。"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from easy_agent.knowledges.core.repository_base import _now

_OBJECT_UPDATE_FIELDS = {
    "storage_key", "status", "size_bytes", "sha256", "error_code",
    "error_message", "stored_at", "deleted_at",
}
_SYNC_ITEM_UPDATE_FIELDS = {
    "document_id", "status", "attempts", "last_error", "publish_date",
}


class IngestionRepositoryMixin:
    """同步台账与原文对象查询/写入；由 KnowledgeRepository 组合。"""

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
