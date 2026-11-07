"""Session message row helpers used by Database."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from easy_agent.db.message_serialization import (
    _sanitize_extra_data,
    try_repair_json,
)

logger = logging.getLogger(__name__)


def add_message_row(db, session_id: str, message: dict) -> None:
    role = message.get("role", "user")
    content = message.get("content", "")
    extra_data = _sanitize_extra_data(db, message)
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
                extra = try_repair_json(raw)
                if extra:
                    msg.update(extra)
        messages.append(msg)
    return messages


def _message_has_pending_approval(msg: dict) -> bool:
    """单条 assistant 消息是否仍待审批。

    与前端 ``utils/hitlApproval.js::messageHasPendingApproval`` 逐分支同源：
    还有待审批工具卡片，或还有挂不上卡片的操作（子代理内部审批）等着作答。
    口径必须一致，否则列表徽标会与主交互区的审批面板互相打架。
    """
    pending = msg.get("pending_approval")
    # 旧落库形态：数组
    if isinstance(pending, list):
        return len(pending) > 0
    blocks = [b for b in (msg.get("blocks") or []) if isinstance(b, dict)]
    has_pending_block = any(
        b.get("type") == "tool_call" and b.get("approval_status") == "pending"
        for b in blocks
    )
    if pending is None:
        # 显式 null = 前端已作答（handleToolApproval 清空载荷），块上残留的 pending 不算数
        if "pending_approval" in msg:
            return False
        return has_pending_block
    if not isinstance(pending, dict) or not pending.get("thread_id"):
        return has_pending_block
    if has_pending_block:
        return True
    action_requests = pending.get("action_requests")
    if not isinstance(action_requests, list):
        return False
    for ar in action_requests:
        if not isinstance(ar, dict):
            continue
        tool_call_id = ar.get("tool_call_id")
        # 没有 tool_call_id 的操作只能靠消息级面板作答 -> 仍待审批
        if not tool_call_id:
            return True
        block = next(
            (
                b
                for b in blocks
                if b.get("tool_call_id") == tool_call_id or b.get("id") == tool_call_id
            ),
            None,
        )
        if block is None or block.get("approval_status") == "pending":
            return True
    return False


def pending_approval_session_ids(db, session_ids: list[str]) -> set[str]:
    """批量判定会话是否仍有待审批（会话列表「待审批」徽标用）。

    判据与前端 ``derivePendingApproval`` 同源：只看**最后一条消息**——
    末尾是 user 消息说明已发起新一轮对话，上一轮的审批不再阻塞；末尾是 assistant
    消息则看它的消息级 ``pending_approval`` 载荷（及块状态兜底）。

    一次查询取每个会话最后一条消息，避免逐个会话拉全量历史（刷新页面时前端
    需要一个请求就拿到全部徽标状态）。``extra_data`` 里没有审批相关字样时直接跳过
    JSON 解析，正常会话不产生解析开销。
    """
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return set()
    placeholders = ",".join(["?"] * len(ids))
    sql = f"""
        SELECT m.session_id, m.role, m.extra_data
        FROM session_messages m
        JOIN (
            SELECT session_id, MAX(id) AS last_id
            FROM session_messages
            WHERE session_id IN ({placeholders})
            GROUP BY session_id
        ) t ON m.id = t.last_id
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, sql, tuple(ids))
        rows = cursor.fetchall()

    pending_ids: set[str] = set()
    for row in rows:
        d = dict(row) if not isinstance(row, dict) else row
        session_id = d.get("session_id")
        if not session_id:
            continue
        # 末尾是 user 消息：新一轮对话已开始，上一轮的待审批不再阻塞
        if d.get("role") != "assistant":
            continue
        raw = d.get("extra_data") or ""
        # 预筛：没有审批相关字样就无需解析 JSON（绝大多数正常会话走这条）
        if "pending_approval" not in raw and "approval_status" not in raw:
            continue
        msg: dict = {}
        try:
            extra = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            extra = try_repair_json(raw)
        if isinstance(extra, dict):
            msg = extra
        if _message_has_pending_approval(msg):
            pending_ids.add(session_id)
    return pending_ids
