"""知识库审计事件与任务计数的持久化访问（对照旧版精简）。

裁剪内容：任务队列写入/认领/心跳（新版上传为同步内联流程，不再落
knowledge_tasks）、reconciliation / alert / runtime_heartbeat 系列方法
（对应表已随新版 schema 一并裁剪）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..db import Database


_SENSITIVE_MARKERS = (
    "password",
    "token",
    "secret",
    "credential",
    "authorization",
    "api_key",
    "content",
    "snippet",
    "prompt",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _safe_details(value: object, *, max_bytes: int = 4096) -> str:
    """清洗并截断审计 details：敏感键脱敏、深度/数量受限、超长整体丢弃。"""

    def cleanse(item: object, depth: int = 0) -> object:
        if depth > 4:
            return "[truncated]"
        if isinstance(item, Mapping):
            return {
                str(key)[:100]: (
                    "[redacted]"
                    if any(marker in str(key).lower() for marker in _SENSITIVE_MARKERS)
                    else cleanse(child, depth + 1)
                )
                for key, child in list(item.items())[:50]
            }
        if isinstance(item, (list, tuple, set)):
            return [cleanse(child, depth + 1) for child in list(item)[:100]]
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return str(item)[:500]

    encoded = json.dumps(cleanse(value), ensure_ascii=False, separators=(",", ":"))
    raw = encoded.encode("utf-8")
    if len(raw) <= max_bytes:
        return encoded
    return json.dumps(
        {"truncated": True, "sha256_not_recorded": True},
        ensure_ascii=False,
        separators=(",", ":"),
    )


class KnowledgeOperationsRepository:
    """审计事件与任务统计的数据访问入口。"""

    def __init__(self, db: Database):
        self.db = db

    def record_audit(
        self,
        *,
        request_id: str,
        actor_user_id: str,
        actor_username: str,
        action: str,
        object_type: str,
        object_id: str,
        base_id: str | None = None,
        outcome: str = "success",
        reason_code: str | None = None,
        details: Mapping[str, object] | None = None,
        max_details_bytes: int = 4096,
    ) -> dict[str, Any]:
        event_id = str(uuid.uuid4())
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_audit_events
                    (id, request_id, actor_user_id, actor_username, action,
                     object_type, object_id, base_id, outcome, reason_code,
                     details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    request_id[:128],
                    actor_user_id[:255],
                    actor_username[:255],
                    action[:80],
                    object_type[:40],
                    object_id[:255],
                    base_id[:255] if base_id else None,
                    outcome[:20],
                    reason_code[:100] if reason_code else None,
                    _safe_details(details or {}, max_bytes=max_details_bytes),
                    timestamp,
                ),
            )
        return {"id": event_id, "request_id": request_id, "created_at": timestamp}

    def list_audits(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_audit_events
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            return [dict(item) for item in cursor.fetchall()]

    def task_counts(self) -> dict[str, int]:
        """按状态统计 knowledge_tasks（新版无写入方，保留供指标端点兼容）。"""
        result = {key: 0 for key in ("queued", "retry", "running", "succeeded", "dead_letter")}
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT status, COUNT(*) AS count FROM knowledge_tasks GROUP BY status",
            )
            for item in cursor.fetchall():
                row = dict(item) if isinstance(item, dict) else {"status": item[0], "count": item[1]}
                result[str(row["status"])] = int(row["count"])
        return result


__all__ = ["KnowledgeOperationsRepository", "utc_now"]
