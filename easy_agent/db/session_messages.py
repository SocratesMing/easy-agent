"""Session message row helpers used by Database."""

from __future__ import annotations

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def add_message_row(db, session_id: str, message: dict) -> None:
    role = message.get("role", "user")
    content = message.get("content", "")
    extra_data = db._sanitize_extra_data(message)
    timestamp = message.get("timestamp", datetime.now().isoformat())

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO session_messages (session_id, role, content, extra_data, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, extra_data, timestamp),
        )
        db._execute(
            cursor,
            "UPDATE sessions SET updated_at=?, message_count=message_count+1 WHERE session_id=?",
            (datetime.now().isoformat(), session_id),
        )


def get_messages_from_rows(db, session_id: str) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT role, content, extra_data, created_at FROM session_messages WHERE session_id=? ORDER BY id",
            (session_id,),
        )
        rows = cursor.fetchall()

    messages = []
    for row in rows:
        d = dict(row) if not isinstance(row, dict) else row
        msg = {
            "role": d["role"],
            "content": d["content"] or "",
            "timestamp": d["created_at"],
        }
        if d.get("extra_data"):
            raw = d["extra_data"]
            try:
                extra = json.loads(raw)
                msg.update(extra)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("解析消息extra_data失败 session=%s: %s", session_id, e)
                extra = db._try_repair_json(raw)
                if extra:
                    msg.update(extra)
        messages.append(msg)
    return messages
