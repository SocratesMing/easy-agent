"""会话运行期状态 repository（ARCH-03 Phase 2 第一步）。

从 `Database` 中抽出 session_runtime 相关 SQL：连接/事务仍由传入的 db 对象
提供，repository 只负责该领域的读写，避免 `Database` 继续膨胀。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def get_session_runtime(db, session_id: str) -> dict | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor, "SELECT * FROM session_runtime WHERE session_id=?", (session_id,)
        )
        row = cursor.fetchone()
    if not row:
        return None
    return dict(row) if not isinstance(row, dict) else row


def try_acquire_session_stream(
    db,
    session_id: str,
    owner: str,
    until_iso: str,
    now_iso: str,
) -> tuple[bool, dict | None]:
    """原子抢占会话流锁；成功返回 (True, None)，被占用返回 (False, row)。"""

    def _read_row(cursor) -> dict | None:
        db._execute(
            cursor,
            "SELECT * FROM session_runtime WHERE session_id=?",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row) if not isinstance(row, dict) else row

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            UPDATE session_runtime
            SET stream_owner=?, stream_until=?, cancel_requested=0, updated_at=?
            WHERE session_id=?
              AND (stream_owner IS NULL OR stream_owner='' OR stream_until < ?)
            """,
            (owner, until_iso, now_iso, session_id, now_iso),
        )
        if cursor.rowcount > 0:
            return True, None

        row = _read_row(cursor)
        if row is not None:
            return False, row

        try:
            db._execute(
                cursor,
                """
                INSERT INTO session_runtime
                (session_id, connectors, edit_target, stream_owner, stream_until,
                 cancel_requested, updated_at)
                VALUES (?, NULL, '', ?, ?, 0, ?)
                """,
                (session_id, owner, until_iso, now_iso),
            )
            return True, None
        except Exception:  # noqa: BLE001 - 唯一键冲突（并发初始化）
            return False, _read_row(cursor)


def renew_session_stream(
    db, session_id: str, owner: str, until_iso: str, now_iso: str
) -> bool:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE session_runtime SET stream_until=?, updated_at=? "
            "WHERE session_id=? AND stream_owner=?",
            (until_iso, now_iso, session_id, owner),
        )
        return cursor.rowcount > 0



def release_session_stream(db, session_id: str, owner: str, now_iso: str) -> bool:
    """条件释放会话流锁：只有锁仍归 ``owner`` 时才清空，返回是否释放了。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE session_runtime SET stream_owner='', stream_until='', "
            "cancel_requested=0, updated_at=? WHERE session_id=? AND stream_owner=?",
            (now_iso, session_id, owner),
        )
        return cursor.rowcount > 0


def upsert_session_runtime(
    db,
    session_id: str,
    *,
    connectors: Any = None,
    edit_target: Any = None,
    stream_owner: Any = None,
    stream_until: Any = None,
    cancel_requested: Any = None,
    clear_connectors: bool = False,
) -> None:
    now = datetime.now().isoformat()
    current = get_session_runtime(db, session_id) or {}
    if clear_connectors:
        connectors_val = None
    elif connectors is not None:
        connectors_val = connectors
    else:
        connectors_val = current.get("connectors")
    values = (
        session_id,
        connectors_val,
        edit_target if edit_target is not None else current.get("edit_target") or "",
        stream_owner if stream_owner is not None else current.get("stream_owner") or "",
        stream_until if stream_until is not None else current.get("stream_until") or "",
        int(cancel_requested)
        if cancel_requested is not None
        else int(current.get("cancel_requested") or 0),
        now,
    )
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if db.db_type == "mysql":
            db._execute(
                cursor,
                """
                INSERT INTO session_runtime
                (session_id, connectors, edit_target, stream_owner, stream_until, cancel_requested, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                  connectors=VALUES(connectors),
                  edit_target=VALUES(edit_target),
                  stream_owner=VALUES(stream_owner),
                  stream_until=VALUES(stream_until),
                  cancel_requested=VALUES(cancel_requested),
                  updated_at=VALUES(updated_at)
                """,
                values,
            )
        else:
            db._execute(
                cursor,
                """
                INSERT INTO session_runtime
                (session_id, connectors, edit_target, stream_owner, stream_until, cancel_requested, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  connectors=excluded.connectors,
                  edit_target=excluded.edit_target,
                  stream_owner=excluded.stream_owner,
                  stream_until=excluded.stream_until,
                  cancel_requested=excluded.cancel_requested,
                  updated_at=excluded.updated_at
                """,
                values,
            )
