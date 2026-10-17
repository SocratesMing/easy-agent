"""会话内工具调用/思考记录 repository（ARCH-03 Phase 2）。

从 `Database` 抽出的第四块领域 SQL：tool_call_records + thinking_records。
"""

from __future__ import annotations

import json
from datetime import datetime


def record_tool_call(
    db,
    session_id: str,
    message_id: str,
    tool_name: str,
    tool_call_id: str,
    arguments: dict,
    result: str = None,
    success: bool = True,
    duration: float = None,
    step: int = 0,
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO tool_call_records
            (session_id, message_id, tool_name, tool_call_id, arguments, result, success, duration, step, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                message_id,
                tool_name,
                tool_call_id,
                json.dumps(arguments, ensure_ascii=False),
                result,
                1 if success else 0,
                duration,
                step,
                datetime.now().isoformat(),
            ),
        )


def get_tool_calls(db, session_id: str) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM tool_call_records WHERE session_id=? ORDER BY created_at",
            (session_id,),
        )
        rows = cursor.fetchall()
    results = []
    for row in rows:
        d = dict(row)
        if d.get("arguments"):
            try:
                d["arguments"] = json.loads(d["arguments"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(d)
    return results


def record_thinking(
    db,
    session_id: str,
    message_id: str,
    step: int,
    content: str,
    duration: float = None,
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        # 同一 (session_id, message_id, step) 可能被分段思考结束多次触发：
        # 先清除旧记录再写入，避免历史思考出现重复行。
        db._execute(
            cursor,
            "DELETE FROM thinking_records WHERE session_id=? AND message_id=? AND step=?",
            (session_id, message_id, step),
        )
        db._execute(
            cursor,
            """
            INSERT INTO thinking_records (session_id, message_id, step, content, duration, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                message_id,
                step,
                content,
                duration,
                datetime.now().isoformat(),
            ),
        )


def get_thinkings(db, session_id: str) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM thinking_records WHERE session_id=? ORDER BY step, created_at",
            (session_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]
