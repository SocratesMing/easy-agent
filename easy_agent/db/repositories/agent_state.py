"""HITL checkpoint 与 LangGraph store 的持久化 repository（ARCH-03 Phase 2）。

从 `Database` 抽出的第二块领域 SQL：agent_checkpoints + langgraph_store。
连接/事务仍由传入的 db 对象提供。
"""

from __future__ import annotations

from datetime import datetime


# ── agent_checkpoints ──────────────────────────────────────────────
def load_agent_checkpoint(db, session_id: str) -> bytes | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT payload FROM agent_checkpoints WHERE session_id=?",
            (session_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    payload = row["payload"] if isinstance(row, dict) else row[0]
    return bytes(payload) if payload is not None else None


def save_agent_checkpoint(db, session_id: str, payload: bytes) -> None:
    now = datetime.now().isoformat()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if db.db_type == "mysql":
            db._execute(
                cursor,
                """
                INSERT INTO agent_checkpoints (session_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=VALUES(updated_at)
                """,
                (session_id, payload, now),
            )
        else:
            db._execute(
                cursor,
                """
                INSERT INTO agent_checkpoints (session_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (session_id, payload, now),
            )


def delete_agent_checkpoint(db, session_id: str) -> None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor, "DELETE FROM agent_checkpoints WHERE session_id=?", (session_id,)
        )


# ── langgraph_store ────────────────────────────────────────────────
def store_get(db, namespace: str, item_key: str) -> dict | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT namespace, item_key, value, created_at, updated_at "
            "FROM langgraph_store WHERE namespace=? AND item_key=?",
            (namespace, item_key),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return dict(row) if not isinstance(row, dict) else row


def store_put(db, namespace: str, item_key: str, value: str) -> None:
    now = datetime.now().isoformat()
    existing = store_get(db, namespace, item_key)
    created = existing["created_at"] if existing else now
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if db.db_type == "mysql":
            db._execute(
                cursor,
                """
                INSERT INTO langgraph_store (namespace, item_key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE value=VALUES(value), updated_at=VALUES(updated_at)
                """,
                (namespace, item_key, value, created, now),
            )
        else:
            db._execute(
                cursor,
                """
                INSERT INTO langgraph_store (namespace, item_key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, item_key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (namespace, item_key, value, created, now),
            )


def store_delete(db, namespace: str, item_key: str) -> None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "DELETE FROM langgraph_store WHERE namespace=? AND item_key=?",
            (namespace, item_key),
        )


def store_search(
    db, namespace_prefix: str, *, limit: int = 10, offset: int = 0
) -> list[dict]:
    like = f"{namespace_prefix}%" if namespace_prefix else "%"
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT namespace, item_key, value, created_at, updated_at
            FROM langgraph_store
            WHERE namespace = ? OR namespace LIKE ?
            ORDER BY namespace, item_key
            LIMIT ? OFFSET ?
            """,
            (namespace_prefix, like if namespace_prefix else "%", limit, offset),
        )
        rows = cursor.fetchall()
    return [dict(r) if not isinstance(r, dict) else r for r in rows]


def store_list_namespaces(db) -> list[str]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT DISTINCT namespace FROM langgraph_store")
        rows = cursor.fetchall()
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row["namespace"])
        else:
            out.append(row[0])
    return out
