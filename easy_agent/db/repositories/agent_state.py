"""LangGraph store 的持久化 repository（``langgraph_store`` 表）。

连接/事务由传入的 db 对象提供。

HITL checkpoint 不在这个文件里：旧的 ``agent_checkpoints`` 全量快照后端已删除，
现在由 ``easy_agent/persistence/mysql_saver.py``（共享 4 张 cp_* 表）与官方
``AsyncSqliteSaver`` 承载，两者的生命周期都在 ``persistence/checkpoints.py``。
"""

from __future__ import annotations

from datetime import datetime


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


class AgentStateRepository:
    """LangGraph ``store`` 键值表（``langgraph_store``）的领域入口。

    ``persistence/stores.py`` 的 ``SqlStore`` 从 T3c 起只依赖本对象；SQL 与
    upsert 语义仍在模块级函数里，本类不改行为。
    """

    def __init__(self, db):
        self._db = db

    def store_get(self, namespace: str, item_key: str) -> dict | None:
        return store_get(self._db, namespace, item_key)


    def store_put(self, namespace: str, item_key: str, value: str) -> None:
        store_put(self._db, namespace, item_key, value)

    def store_delete(self, namespace: str, item_key: str) -> None:
        store_delete(self._db, namespace, item_key)

    def store_search(
        self, namespace_prefix: str, *, limit: int = 10, offset: int = 0
    ) -> list[dict]:
        return store_search(
            self._db, namespace_prefix, limit=limit, offset=offset
        )

    def store_list_namespaces(self) -> list[str]:
        return store_list_namespaces(self._db)


__all__ = ["AgentStateRepository"]
