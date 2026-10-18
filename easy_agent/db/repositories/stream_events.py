"""跨 Pod SSE 事件持久化 repository（多副本 Phase 2A）。

``stream_events`` 表只追加、不原地修改：``event_id`` 是库级自增主键，天然提供
多副本并发写入下的全局顺序；回放端用 ``after_id`` 游标分页读取，逐条透传
``payload``（完整 SSE frame），因此顺序与 wire 格式都不依赖任何进程内状态。

连接/事务由传入的 ``Database`` 对象提供，SQL 占位符统一用 ``?``
（``Database._execute`` 在 MySQL 下自动转成 ``%s``）。
"""

from __future__ import annotations

from datetime import datetime

# 单次回放分页上限：与 /stream/live 的轮询节奏配套，避免单次查询拖住事件循环。
DEFAULT_LIST_LIMIT = 500


def append_stream_event(db, session_id: str, payload: str) -> int:
    """追加一条 SSE frame，返回库级 ``event_id``（自增）。"""
    created_at = datetime.now().isoformat()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "INSERT INTO stream_events (session_id, payload, created_at) VALUES (?, ?, ?)",
            (session_id, payload, created_at),
        )
        event_id = cursor.lastrowid
    return int(event_id or 0)


def list_stream_events(
    db, session_id: str, after_id: int = 0, limit: int = DEFAULT_LIST_LIMIT
) -> list[dict]:
    """按 ``event_id`` 升序返回 ``after_id`` 之后的事件（分页回放）。"""
    if limit <= 0:
        return []
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT event_id, session_id, payload, created_at FROM stream_events "
            "WHERE session_id=? AND event_id>? ORDER BY event_id ASC LIMIT ?",
            (session_id, int(after_id or 0), int(limit)),
        )
        rows = cursor.fetchall()
    result: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            result.append(dict(row))
        else:
            # sqlite3.Row / PyMySQL tuple：按 SELECT 列序映射
            result.append(
                {
                    "event_id": row[0],
                    "session_id": row[1],
                    "payload": row[2],
                    "created_at": row[3],
                }
            )
    return result


def clear_stream_events(db, session_id: str) -> int:
    """删除某会话的旧流事件，返回删除行数。

    每次新流开始时调用：``stream_events`` 是「当前/最近一次流」的回放缓冲，
    不是跨轮次审计日志。否则默认 ``last_event_id=0`` 的重连会先回放到上一轮
    的 ``done`` 而立即结束（旧实现每个新 hub 本来就是空历史）。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "DELETE FROM stream_events WHERE session_id=?",
            (session_id,),
        )
        deleted = cursor.rowcount
    return int(deleted or 0)


def delete_stream_events_before(db, cutoff_iso: str) -> int:
    """删除 ``created_at`` 早于截止时间的旧事件，返回删除行数（清理预留）。"""
    if not cutoff_iso:
        return 0
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "DELETE FROM stream_events WHERE created_at<?",
            (cutoff_iso,),
        )
        deleted = cursor.rowcount
    return int(deleted or 0)


__all__ = [
    "DEFAULT_LIST_LIMIT",
    "append_stream_event",
    "clear_stream_events",
    "delete_stream_events_before",
    "list_stream_events",
]
