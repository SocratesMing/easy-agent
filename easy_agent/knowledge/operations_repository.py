"""Persistence for P0 tasks, audit, reconciliation and operational alerts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

import pymysql

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
    def __init__(self, db: Database):
        self.db = db

    def create_task(
        self,
        *,
        operation_id: str,
        task_type: str,
        resource_type: str,
        resource_id: str,
        idempotency_key: str,
        payload: Mapping[str, object],
        request_id: str,
        created_by: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        task_id = str(uuid.uuid4())
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_tasks
                        (id, operation_id, task_type, resource_type, resource_id,
                         idempotency_key, payload_json, status, attempt_count,
                         max_attempts, available_at, request_id, created_by,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        operation_id,
                        task_type,
                        resource_type,
                        resource_id,
                        idempotency_key,
                        _safe_details(payload, max_bytes=16384),
                        max_attempts,
                        timestamp,
                        request_id,
                        created_by,
                        timestamp,
                        timestamp,
                    ),
                )
        except (sqlite3.IntegrityError, pymysql.IntegrityError):
            existing = self.get_task_by_idempotency(idempotency_key)
            if existing is None:
                raise
            return existing
        task = self.get_task(task_id)
        assert task is not None
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "SELECT * FROM knowledge_tasks WHERE id=?", (task_id,))
            return _row(cursor.fetchone())

    def get_task_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_tasks WHERE idempotency_key=?",
                (key,),
            )
            return _row(cursor.fetchone())

    def cancel_pending_tasks(self, *, resource_id: str, task_type: str | None = None) -> int:
        clauses = ["resource_id=?", "status IN ('queued','retry')"]
        params: list[object] = [resource_id]
        if task_type:
            clauses.append("task_type=?")
            params.append(task_type)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE knowledge_tasks SET status='cancelled', completed_at=?, updated_at=? "
                f"WHERE {' AND '.join(clauses)}",
                (utc_now(), utc_now(), *params),
            )
            return cursor.rowcount

    def supersede_document_dead_letters(
        self, *, resource_id: str, successful_task_id: str
    ) -> int:
        """Close obsolete dead letters after a replacement upload is accepted.

        The failed operation and its sanitized error are deliberately retained
        for audit.  Only its actionable task state changes, so the global
        dead-letter alert reflects work that still needs intervention instead
        of failures already recovered by a later retry.
        """

        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                UPDATE knowledge_tasks
                SET status='cancelled', completed_at=COALESCE(completed_at, ?),
                    updated_at=?
                WHERE resource_id=? AND id<>? AND status='dead_letter'
                  AND task_type IN ('document_upload', 'document_retry')
                """,
                (timestamp, timestamp, resource_id, successful_task_id),
            )
            return cursor.rowcount

    def recover_stale_tasks(self, *, stale_before: str) -> int:
        now = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                UPDATE knowledge_tasks
                SET status=CASE WHEN attempt_count>=max_attempts
                                THEN 'dead_letter' ELSE 'retry' END,
                    available_at=?, locked_by=NULL, locked_at=NULL,
                    heartbeat_at=NULL, timeout_at=NULL,
                    last_error_code='KNOWLEDGE_WORKER_LOST',
                    last_error_message='任务执行中断，已由新 Worker 接管',
                    updated_at=?
                WHERE status='running'
                  AND COALESCE(heartbeat_at, locked_at, updated_at)<?
                """,
                (now, now, stale_before),
            )
            return cursor.rowcount

    def claim_next_task(
        self, *, worker_id: str, timeout_seconds: float
    ) -> dict[str, Any] | None:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        timeout_at = (now_dt + timedelta(seconds=timeout_seconds)).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT id FROM knowledge_tasks
                WHERE status IN ('queued','retry') AND available_at<=?
                ORDER BY available_at, created_at LIMIT 20
                """,
                (now,),
            )
            candidates = [
                item["id"] if isinstance(item, dict) else item[0]
                for item in cursor.fetchall()
            ]
            for task_id in candidates:
                self.db._execute(
                    cursor,
                    """
                    UPDATE knowledge_tasks
                    SET status='running', attempt_count=attempt_count+1,
                        locked_by=?, locked_at=?, heartbeat_at=?, timeout_at=?,
                        updated_at=?
                    WHERE id=? AND status IN ('queued','retry') AND available_at<=?
                    """,
                    (worker_id, now, now, timeout_at, now, task_id, now),
                )
                if cursor.rowcount:
                    self.db._execute(
                        cursor, "SELECT * FROM knowledge_tasks WHERE id=?", (task_id,)
                    )
                    return _row(cursor.fetchone())
        return None

    def heartbeat_task(self, task_id: str, worker_id: str) -> bool:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                UPDATE knowledge_tasks SET heartbeat_at=?, updated_at=?
                WHERE id=? AND status='running' AND locked_by=?
                """,
                (timestamp, timestamp, task_id, worker_id),
            )
            return cursor.rowcount > 0

    def finish_task(self, task_id: str) -> None:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                UPDATE knowledge_tasks
                SET status='succeeded', completed_at=?, heartbeat_at=?, updated_at=?,
                    last_error_code=NULL, last_error_message=NULL
                WHERE id=?
                """,
                (timestamp, timestamp, timestamp, task_id),
            )

    def fail_task(
        self,
        task_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_backoff_seconds: float,
    ) -> str:
        task = self.get_task(task_id)
        if task is None:
            return "missing"
        attempts = int(task.get("attempt_count") or 0)
        maximum = int(task.get("max_attempts") or 1)
        status = "retry" if retryable and attempts < maximum else "dead_letter"
        now_dt = datetime.now(UTC)
        available = (
            now_dt + timedelta(seconds=retry_backoff_seconds * max(1, 2 ** (attempts - 1)))
        ).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                UPDATE knowledge_tasks
                SET status=?, available_at=?, locked_by=NULL, locked_at=NULL,
                    heartbeat_at=NULL, timeout_at=NULL, last_error_code=?,
                    last_error_message=?, updated_at=?,
                    completed_at=CASE WHEN ?='dead_letter' THEN ? ELSE NULL END
                WHERE id=?
                """,
                (
                    status,
                    available,
                    error_code[:100],
                    error_message[:1000],
                    now_dt.isoformat(),
                    status,
                    now_dt.isoformat(),
                    task_id,
                ),
            )
        return status

    def task_counts(self) -> dict[str, int]:
        result = {key: 0 for key in ("queued", "retry", "running", "succeeded", "dead_letter")}
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, "SELECT status, COUNT(*) AS count FROM knowledge_tasks GROUP BY status"
            )
            for item in cursor.fetchall():
                row = dict(item) if isinstance(item, dict) else {"status": item[0], "count": item[1]}
                result[str(row["status"])] = int(row["count"])
        return result

    def actionable_dead_letter_count(self) -> int:
        """Count only dead letters whose resource is still user-visible.

        Historical failures stay queryable, but a document or knowledge base
        that has entered its reversible deletion window no longer requires an
        operator to retry its former upload task.
        """

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT COUNT(*) AS count
                FROM knowledge_tasks t
                LEFT JOIN knowledge_documents d
                  ON t.resource_type='document' AND d.id=t.resource_id
                LEFT JOIN knowledge_bases document_base ON document_base.id=d.base_id
                LEFT JOIN knowledge_bases resource_base
                  ON t.resource_type='base' AND resource_base.id=t.resource_id
                WHERE t.status='dead_letter'
                  AND (
                    (t.resource_type='document' AND d.id IS NOT NULL
                     AND d.status!='deleted' AND document_base.status!='deleted')
                    OR (t.resource_type='base' AND resource_base.id IS NOT NULL
                        AND resource_base.status!='deleted')
                    OR t.resource_type NOT IN ('document', 'base')
                  )
                """,
            )
            row = cursor.fetchone()
            return int(row["count"] if isinstance(row, dict) else row[0])

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

    def begin_reconciliation(self, *, request_id: str) -> str:
        run_id = str(uuid.uuid4())
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """INSERT INTO knowledge_reconciliation_runs
                   (id,status,request_id,summary_json,started_at)
                   VALUES (?, 'running', ?, '{}', ?)""",
                (run_id, request_id[:128], utc_now()),
            )
        return run_id

    def finish_reconciliation(
        self, run_id: str, *, summary: Mapping[str, object], error_message: str | None = None
    ) -> None:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """UPDATE knowledge_reconciliation_runs
                   SET status=?, summary_json=?, error_message=?, completed_at=? WHERE id=?""",
                (
                    "failed" if error_message else "succeeded",
                    _safe_details(summary, max_bytes=16384),
                    error_message[:1000] if error_message else None,
                    timestamp,
                    run_id,
                ),
            )

    def upsert_reconciliation_issue(
        self,
        *,
        run_id: str,
        issue_key: str,
        issue_type: str,
        severity: str,
        base_id: str | None,
        document_id: str | None,
        details: Mapping[str, object],
    ) -> None:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT id FROM knowledge_reconciliation_issues WHERE issue_key=?",
                (issue_key,),
            )
            existing = cursor.fetchone()
            if existing:
                self.db._execute(
                    cursor,
                    """UPDATE knowledge_reconciliation_issues
                       SET run_id=?, issue_type=?, severity=?, base_id=?, document_id=?,
                           status='open', details_json=?, last_seen_at=?, resolved_at=NULL,
                           resolution_note=NULL WHERE issue_key=?""",
                    (run_id, issue_type, severity, base_id, document_id,
                     _safe_details(details), timestamp, issue_key),
                )
            else:
                self.db._execute(
                    cursor,
                    """INSERT INTO knowledge_reconciliation_issues
                       (id,run_id,issue_key,issue_type,severity,base_id,document_id,status,
                        details_json,first_seen_at,last_seen_at)
                       VALUES (?,?,?,?,?,?,?,'open',?,?,?)""",
                    (str(uuid.uuid4()), run_id, issue_key, issue_type, severity,
                     base_id, document_id, _safe_details(details), timestamp, timestamp),
                )

    def resolve_unseen_reconciliation_issues(self, *, seen_keys: set[str]) -> int:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if seen_keys:
                placeholders = ",".join("?" for _ in seen_keys)
                self.db._execute(
                    cursor,
                    f"""UPDATE knowledge_reconciliation_issues
                        SET status='resolved', resolved_at=?, resolution_note='not reproduced'
                        WHERE status='open' AND issue_key NOT IN ({placeholders})""",
                    (timestamp, *sorted(seen_keys)),
                )
            else:
                self.db._execute(
                    cursor,
                    """UPDATE knowledge_reconciliation_issues
                       SET status='resolved', resolved_at=?, resolution_note='not reproduced'
                       WHERE status='open'""",
                    (timestamp,),
                )
            return cursor.rowcount

    def list_reconciliation_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_reconciliation_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(item) for item in cursor.fetchall()]

    def list_reconciliation_issues(
        self, *, status: str = "open", limit: int = 200
    ) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """SELECT * FROM knowledge_reconciliation_issues
                   WHERE status=? ORDER BY severity DESC,last_seen_at DESC LIMIT ?""",
                (status, limit),
            )
            return [dict(item) for item in cursor.fetchall()]

    def upsert_heartbeat(
        self, *, component: str, instance_id: str, status: str, details: Mapping[str, object]
    ) -> None:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT component FROM knowledge_runtime_heartbeats
                WHERE component=? AND instance_id=?
                """,
                (component, instance_id),
            )
            if cursor.fetchone():
                self.db._execute(
                    cursor,
                    """
                    UPDATE knowledge_runtime_heartbeats
                    SET status=?, details_json=?, last_seen_at=?
                    WHERE component=? AND instance_id=?
                    """,
                    (status, _safe_details(details), timestamp, component, instance_id),
                )
            else:
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_runtime_heartbeats
                        (component, instance_id, status, details_json, last_seen_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (component, instance_id, status, _safe_details(details), timestamp),
                )

    def latest_heartbeat(self, component: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_runtime_heartbeats
                WHERE component=? ORDER BY last_seen_at DESC LIMIT 1
                """,
                (component,),
            )
            return _row(cursor.fetchone())

    def upsert_alert(
        self,
        *,
        dedup_key: str,
        alert_type: str,
        severity: str,
        summary: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, "SELECT id FROM knowledge_alerts WHERE dedup_key=?", (dedup_key,)
            )
            current = cursor.fetchone()
            if current:
                self.db._execute(
                    cursor,
                    """
                    UPDATE knowledge_alerts SET status='open', severity=?, summary=?,
                        details_json=?, last_seen_at=?, resolved_at=NULL WHERE dedup_key=?
                    """,
                    (severity, summary[:500], _safe_details(details or {}), timestamp, dedup_key),
                )
            else:
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_alerts
                        (id, dedup_key, alert_type, severity, status, summary,
                         details_json, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), dedup_key, alert_type, severity,
                        summary[:500], _safe_details(details or {}), timestamp, timestamp,
                    ),
                )

    def resolve_alert(self, dedup_key: str) -> None:
        timestamp = utc_now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                UPDATE knowledge_alerts SET status='resolved', resolved_at=?, last_seen_at=?
                WHERE dedup_key=? AND status='open'
                """,
                (timestamp, timestamp, dedup_key),
            )

    def list_open_alerts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_alerts WHERE status='open'
                ORDER BY severity DESC, last_seen_at DESC LIMIT ?
                """,
                (limit,),
            )
            return [dict(item) for item in cursor.fetchall()]


__all__ = ["KnowledgeOperationsRepository", "utc_now"]
