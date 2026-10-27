"""旧 ``sessions.messages`` JSON 列的收尾：启动时只补不删，一次性清空已被行表覆盖的 JSON。

A2（2026-10-18）起会话正文只写 ``session_messages`` 行表，``sessions.messages`` 只为旧会话
保留（新会话写空串）。旧会话的 JSON 不再更新，继续对话后行表会比 JSON 多，所以行表是权威，
这里的两个入口都不删除任何消息行：

- :func:`repair_missing_message_rows`（每次启动）：JSON 条数多于行数、且行表恰好是 JSON 的
  前缀时，按顺序补上缺的尾部；对不上就告警，两边都不动；
- :func:`clear_complete_legacy_blobs`（迁移 6，只跑一次）：行表已包含 JSON 全部消息的会话，
  把 JSON 清成空串。之后启动扫描不再读这些大字段，旧 JSON 也不会再被当成权威。

比对只看角色和正文：写行表时取的就是这两个字段（``session_messages.add_message_row``）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _session_ids(db, cursor, op: str) -> list[str]:
    """messages 是合法 JSON 数组、且 JSON 条数与行数满足 ``op`` 的会话。

    单条 SQL 完成比对（不走 Python 逐会话循环），MySQL 用 JSON_LENGTH，SQLite 用
    json_array_length；两侧都要求 messages 是合法 JSON 数组，避免脏数据 / 非数组结构被误判
    或触发函数报错。新会话的空串不是合法 JSON，天然被排除。
    """
    if db.db_type == "sqlite":
        valid = "json_valid(s.messages) AND json_type(s.messages) = 'array'"
        length = "json_array_length(s.messages)"
    else:
        valid = "JSON_VALID(s.messages) AND JSON_TYPE(s.messages) = 'ARRAY'"
        length = "JSON_LENGTH(s.messages)"
    db._execute(
        cursor,
        f"SELECT s.session_id FROM sessions s WHERE {valid} AND {length} {op} ("
        "  SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id)",
    )
    return sorted(r["session_id"] if isinstance(r, dict) else r[0] for r in cursor.fetchall())


def _legacy_messages(db, cursor, session_id: str) -> list:
    db._execute(cursor, "SELECT messages FROM sessions WHERE session_id=?", (session_id,))
    row = cursor.fetchone()
    raw = (row["messages"] if isinstance(row, dict) else row[0]) if row else None
    try:
        messages = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return messages if isinstance(messages, list) else []


def _row_keys(db, cursor, session_id: str) -> list[tuple]:
    db._execute(
        cursor,
        "SELECT role, content FROM session_messages WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    keys = []
    for r in cursor.fetchall():
        role, content = (r["role"], r["content"]) if isinstance(r, dict) else (r[0], r[1])
        keys.append((role or "user", content or ""))
    return keys


def _message_key(message) -> tuple:
    if not isinstance(message, dict):
        return (None, None)
    return (message.get("role") or "user", message.get("content") or "")


def repair_missing_message_rows(db, cursor) -> int:
    """JSON 条数多于行数的旧会话：行表是 JSON 的前缀时补上缺的尾部，返回补过的会话数。"""
    repaired = 0
    for sid in _session_ids(db, cursor, ">"):
        messages = _legacy_messages(db, cursor, sid)
        existing = _row_keys(db, cursor, sid)
        tail = messages[len(existing):]
        prefix_matches = [_message_key(m) for m in messages[: len(existing)]] == existing
        if not prefix_matches or not all(isinstance(m, dict) for m in tail):
            logger.warning(
                "跳过消息修复：行表与 sessions.messages 对不上，两边都不改动 | "
                "session=%s 行=%d JSON=%d",
                sid,
                len(existing),
                len(messages),
            )
            continue
        for msg in tail:
            db._execute(
                cursor,
                "INSERT INTO session_messages (session_id, role, content, extra_data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    sid,
                    msg.get("role", "user"),
                    msg.get("content", ""),
                    db._sanitize_extra_data(msg),
                    msg.get("timestamp", datetime.now().isoformat()),
                ),
            )
        repaired += 1
    if repaired:
        logger.info("数据修复：为 %d 个会话补回了 session_messages 缺失的行", repaired)
    return repaired


def clear_complete_legacy_blobs(db, cursor) -> None:
    """迁移 6：行表已包含 JSON 全部消息的旧会话，把 ``sessions.messages`` 清成空串。

    只清 JSON 恰好是行表前缀的会话（行表可以更多：A2 之后的新消息）；对不上的保留 JSON，
    留给人工核对。
    """
    cleared = kept = 0
    for sid in _session_ids(db, cursor, "<="):
        messages = _legacy_messages(db, cursor, sid)
        existing = _row_keys(db, cursor, sid)
        if [_message_key(m) for m in messages] != existing[: len(messages)]:
            kept += 1
            logger.warning(
                "保留旧 JSON：行表与 sessions.messages 对不上 | session=%s 行=%d JSON=%d",
                sid,
                len(existing),
                len(messages),
            )
            continue
        db._execute(cursor, "UPDATE sessions SET messages='' WHERE session_id=?", (sid,))
        cleared += 1
    logger.info("清空已被行表覆盖的 sessions.messages | 清空=%d 保留=%d", cleared, kept)
