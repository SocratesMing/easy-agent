"""会话与消息 SQL repository（ARCH-03 Phase 2）。

从 Database 抽出的 sessions/messages 领域：会话 CRUD、消息行、JSON blob 兼容、
sanitize 逻辑与计数。session_messages 的行级 helper 仍由本模块惰性调用。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from easy_agent.models.db import SessionModel

logger = logging.getLogger("easy_agent.db.sessions")

# A2：legacy sessions.messages 回退读取计数（仅观测；确认无存量依赖后删除该列）。
_LEGACY_FALLBACK_COUNT = 0


def create_session(db, session_data: SessionModel) -> SessionModel:
    """创建会话行：只写元数据，消息正文一律走 ``session_messages`` 行表。

    A2（会话消息单一写路径）：不再 ``json.dumps(session_data.messages)`` 落
    ``sessions.messages``。该列仅为 legacy 兼容保留（schema NOT NULL），新会话
    写入空串而不是 ``'[]'``，原因：

    - 空串不是合法 JSON 数组，启动期 ``_repair_missing_message_rows`` 的
      「JSON 条数 vs 行数」比对会跳过它；若写 ``'[]'``，新会话（JSON 0 条、
      行表 N 条）会被判定为不一致，重启时按空 JSON 重建并删除全部消息行；
    - ``_get_legacy_session_messages`` 对空值直接返回 ``[]``，不触发反序列化。

    返回对象仍保留调用方传入的 ``messages``（内存语义/API 契约不变），但不会落库。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO sessions (session_id, title, messages, created_at, updated_at, username)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_data.session_id,
                session_data.title,
                "",
                session_data.created_at,
                session_data.updated_at,
                session_data.username,
            ),
        )
    return session_data

def get_session_owner(db, session_id: str) -> Optional[dict]:
    """轻量会话归属查询（只取 username/workspace_name/title）。

    归属校验、工作区定位等路径无需全量消息；用本方法避免 get_session()
    对长会话做全量消息加载（轻量接口被拖慢的主因之一）。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT session_id, username, workspace_name, title "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return dict(row) if not isinstance(row, dict) else row

def _get_legacy_session_messages(db, session_id: str) -> list:
    """[DEPRECATED] 读取 legacy ``sessions.messages`` JSON（仅历史会话回退）。

    A2 起新会话不再写 JSON 正文，本函数只服务「行表为空 + JSON 非空」的存量
    会话；下个小版本删除 ``sessions.messages`` 列（计划 E1）后一并移除。
    空值 / ``'[]'`` 直接返回 ``[]``，不做反序列化。
    """
    global _LEGACY_FALLBACK_COUNT
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT messages FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
    if not row:
        return []
    raw = dict(row).get("messages") if not isinstance(row, dict) else row.get("messages")
    if not raw:
        return []
    if isinstance(raw, str) and raw.strip() in ("", "[]"):
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list) or not data:
        return []
    _LEGACY_FALLBACK_COUNT += 1
    logger.info(
        "legacy sessions.messages 回退读取（deprecated）| session=%s messages=%d "
        "fallback_count=%d",
        session_id,
        len(data),
        _LEGACY_FALLBACK_COUNT,
    )
    return data

def update_session_meta(db,
    session_id: str,
    title: str,
    updated_at: str,
    username: str | None = None,
) -> None:
    """只更新会话元信息（标题/时间/用户名），不重写 legacy messages 大字段。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if username is None:
            db._execute(
                cursor,
                "UPDATE sessions SET title=?, updated_at=? WHERE session_id=?",
                (title, updated_at, session_id),
            )
        else:
            db._execute(
                cursor,
                "UPDATE sessions SET title=?, updated_at=?, username=? WHERE session_id=?",
                (title, updated_at, username, session_id),
            )

def get_session(db, session_id: str) -> Optional[SessionModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT session_id, title, created_at, updated_at, username, workspace_name, todos
            FROM sessions WHERE session_id = ?
            """,
            (session_id,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    row = dict(row) if not isinstance(row, dict) else row

    # A2：正常路径只读行表；仅当行表为空时才尝试 legacy 回退。
    # 新会话 sessions.messages 为空串，回退函数直接返回 []（不反序列化）；
    # 只有存量历史会话的非空 JSON 才会被读取，避免热路径读数十 MB 大字段。
    messages = get_messages_from_rows(db, session_id)
    if not messages:
        messages = _get_legacy_session_messages(db, session_id)

    todos = []
    todos_raw = row.get("todos", None)
    if todos_raw:
        try:
            todos = (
                json.loads(todos_raw) if isinstance(todos_raw, str) else todos_raw
            )
        except (json.JSONDecodeError, ValueError):
            todos = []

    return SessionModel(
        session_id=row["session_id"],
        title=row["title"],
        messages=messages,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        username=row.get("username", ""),
        workspace_name=row.get("workspace_name", ""),
        todos=todos,
    )

def list_sessions(db, limit: int = 50, offset: int = 0, username: str = None
) -> list[SessionModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if username:
            db._execute(
                cursor,
                """
                SELECT s.session_id, s.title, s.created_at, s.updated_at, s.username,
                       s.workspace_name, s.pinned,
                       (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id) AS message_count
                FROM sessions s WHERE s.username = ? AND s.title NOT LIKE ?
                ORDER BY s.pinned DESC, s.updated_at DESC LIMIT ? OFFSET ?
                """,
                (username, "[定时任务]%", limit, offset),
            )
        else:
            db._execute(
                cursor,
                """
                SELECT s.session_id, s.title, s.created_at, s.updated_at, s.username,
                       s.workspace_name, s.pinned,
                       (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id) AS message_count
                FROM sessions s WHERE s.title NOT LIKE ?
                ORDER BY s.pinned DESC, s.updated_at DESC LIMIT ? OFFSET ?
                """,
                ("[定时任务]%", limit, offset),
            )
        rows = cursor.fetchall()

    sessions = []
    for row in rows:
        row = dict(row) if not isinstance(row, dict) else row
        sessions.append(
            SessionModel(
                session_id=row["session_id"],
                title=row["title"],
                messages=[{}] * int(row.get("message_count") or 0),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                username=row.get("username", ""),
                workspace_name=row.get("workspace_name", ""),
                pinned=int(row.get("pinned", 0)),
            )
        )
    return sessions

def update_session(db, session_data: SessionModel):
    """更新会话元信息（标题/时间/用户名），不重写消息正文。

    A2：``session_data.messages`` 不再序列化回写 ``sessions.messages``；
    消息只通过 ``session_messages`` 行表写入，保持单一写路径。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            UPDATE sessions SET title=?, updated_at=?, username=?
            WHERE session_id=?
            """,
            (
                session_data.title,
                session_data.updated_at,
                session_data.username,
                session_data.session_id,
            ),
        )

def delete_session(db, session_id: str) -> bool:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        # 附属消息行必须一并删除：get_session 优先从 session_messages 表读消息，
        # 若删除会话时留下孤儿行，之后重建同 ID 会话（如知识库隐藏会话）
        # 会把旧消息读回并写进 messages JSON，表现为"删除后历史复活"
        db._execute(
            cursor, "DELETE FROM session_messages WHERE session_id=?", (session_id,)
        )
        db._execute(
            cursor, "DELETE FROM sessions WHERE session_id=?", (session_id,)
        )
        return cursor.rowcount > 0

def existing_session_ids(db, session_ids) -> set[str]:
    """返回 ``session_ids`` 里仍然存在的会话 id（每 500 个一条 IN 查询；checkpoint 对账用）。"""
    ids = [sid for sid in dict.fromkeys(session_ids) if sid]
    found: set[str] = set()
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db._execute(
                cursor,
                f"SELECT session_id FROM sessions WHERE session_id IN ({placeholders})",
                tuple(chunk),
            )
            rows = cursor.fetchall()
        for row in rows:
            row = dict(row) if not isinstance(row, dict) else row
            found.add(row["session_id"])
    return found


def update_session_workspace_name(db, session_id: str, workspace_name: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE sessions SET workspace_name=? WHERE session_id=?",
            (workspace_name, session_id),
        )

def update_session_todos(db, session_id: str, todos: list):
    """Update the todo list for a session, replacing any existing plan."""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE sessions SET todos=? WHERE session_id=?",
            (json.dumps(todos, ensure_ascii=False), session_id),
        )

def update_generated_file_paths(db, session_id: str, old_prefix: str, new_prefix: str
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT id, file_path FROM generated_files WHERE session_id=?",
            (session_id,),
        )
        rows = cursor.fetchall()
        for row in rows:
            row_dict = dict(row) if not isinstance(row, dict) else row
            old_path = row_dict["file_path"]
            file_id = row_dict["id"]
            if old_path.startswith(old_prefix):
                new_path = new_prefix + old_path[len(old_prefix) :]
                db._execute(
                    cursor,
                    "UPDATE generated_files SET file_path=? WHERE id=?",
                    (new_path, file_id),
                )

def add_message(db, session_id: str, message: dict):
    add_message_row(db, session_id, message)

def _sanitize_message_for_storage(db, message: dict) -> dict:
    # 不做内容截断：历史上下文需要完整回灌给模型，落库时截断会导致
    # 下一轮上下文 token 明显缩水（实测 50% → 36%）。
    # 列类型 MySQL 为 MEDIUMTEXT(16MB)、SQLite 为 TEXT，足以容纳完整内容；
    # 上下文体积改由 SummarizationMiddleware 在模型调用前按 token 阈值压缩。
    sanitized = {
        "role": message.get("role", ""),
        "content": message.get("content", "") or "",
        "timestamp": message.get("timestamp", ""),
    }
    if message.get("thinking"):
        sanitized["thinking"] = message["thinking"]
    if message.get("thinking_duration") is not None:
        sanitized["thinking_duration"] = message["thinking_duration"]
    if message.get("usage"):
        sanitized["usage"] = message["usage"]
    for key in ("knowledge_evidence", "knowledge_warnings"):
        if isinstance(message.get(key), list):
            sanitized[key] = message[key]
    if message.get("tool_calls"):
        # 与 _sanitize_extra_data 对齐：不再截断为前 20 条。
        # HITL 恢复后新增的工具调用位于列表尾部，截断会导致历史会话中
        # HITL 之后的工具调用整体丢失。
        sanitized["tool_calls"] = [
            {
                "tool_name": tc.get("tool_name", "") or tc.get("name", ""),
                "tool_call_id": tc.get("tool_call_id", ""),
                "arguments": tc.get("arguments", {}),
                "result": str(tc.get("result", "")),
                "success": tc.get("success", True),
                "duration": tc.get("duration"),
                "step": tc.get("step", 0),
                "approval_status": tc.get("approval_status"),
            }
            for tc in message["tool_calls"][:1000]
        ]
    if message.get("blocks"):
        # 分离 content blocks 和其他 blocks，确保 content 不被截断
        content_blocks = [
            b for b in message["blocks"] if b.get("type") == "content"
        ]
        other_blocks = [b for b in message["blocks"] if b.get("type") != "content"]

        # 保留全部非 content blocks（原先仅保留前 30 个）：
        # 长会话/HITL 恢复后产生的 thinking/tool_call 块 order 靠后，
        # 截断会导致「切回历史会话时 HITL 之后的工具与思考过程不显示」。
        # sessions.messages 列为 MEDIUMTEXT(16MB)，且各块 result/content 已做长度裁剪。
        sanitized_blocks = []
        for b in other_blocks[:1000] + content_blocks:
            block_type = b.get("type", "")
            s = {"type": block_type, "order": b.get("order", 0)}
            if block_type == "thinking":
                s["content"] = b.get("content", "") or ""
                s["duration"] = b.get("duration")
                s["step"] = b.get("step", 0)
            elif block_type == "tool_call":
                s["tool_name"] = b.get("tool_name", "")
                s["tool_call_id"] = b.get("tool_call_id", "")
                s["arguments"] = b.get("arguments", {})
                s["result"] = str(b.get("result", ""))
                s["success"] = b.get("success", True)
                s["duration"] = b.get("duration")
                s["step"] = b.get("step", 0)
                # 保留 HITL 审批标记：历史会话显示「已批准/已拒绝/待审批」，
                # 且恢复流依赖 approval_status == "pending" 定位待审批工具。
                if b.get("approval_status"):
                    s["approval_status"] = b["approval_status"]
                if b.get("pending_approval"):
                    s["pending_approval"] = True
                if b.get("file_paths"):
                    s["file_paths"] = b["file_paths"]
            elif block_type == "content":
                s["content"] = b.get("content", "") or ""
                s["step"] = b.get("step", 0)
            else:
                s["content"] = b.get("content", "") or ""
            sanitized_blocks.append(s)
        # 按 order 排序恢复原始顺序
        sanitized_blocks.sort(key=lambda x: x.get("order", 0))
        sanitized["blocks"] = sanitized_blocks
    if message.get("pending_approval"):
        # HITL 待审批信息（thread_id + 操作详情），供历史会话恢复审批 UI 与继续执行
        sanitized["pending_approval"] = message["pending_approval"]
    return sanitized

def update_last_assistant_message(db, session_id: str, message: dict):
    update_last_assistant_message_row(db, session_id, message)

def _sanitize_blocks(db, blocks: list) -> list:
    """规范化 blocks：分离 content 与其他块、按类型挑选字段、按 order 还原顺序。

    content 块不计入数量上限，避免正文被截断。
    """
    # 分离 content blocks 和其他 blocks，确保 content 不被截断
    content_blocks = []
    other_blocks = []
    for b in blocks:
        if b.get("type") == "content":
            content_blocks.append(b)
        else:
            other_blocks.append(b)

    # 保留全部非 content blocks（不再限制数量）：
    # 长会话/多次 HITL 会产生数十至上百个 tool_call/thinking 块，
    # 限制数量会导致后期（最晚出现的）块在历史记录中整体丢失。
    # 列类型为 MEDIUMTEXT(16MB)，配合下方 max_len 渐进式裁剪控制体积。
    max_other = 1000
    truncated_other = other_blocks[:max_other]

    sanitized_blocks = []
    for b in truncated_other + content_blocks:
        block_type = b.get("type", "")
        sanitized = {"type": block_type, "order": b.get("order", 0)}
        if block_type == "thinking":
            sanitized["content"] = b.get("content", "") or ""
            sanitized["duration"] = b.get("duration")
            sanitized["step"] = b.get("step", 0)
        elif block_type == "tool_call":
            sanitized["tool_name"] = b.get("tool_name", "")
            sanitized["tool_call_id"] = b.get("tool_call_id", "")
            sanitized["arguments"] = b.get("arguments", {})
            sanitized["result"] = str(b.get("result", ""))
            sanitized["success"] = b.get("success", True)
            sanitized["duration"] = b.get("duration")
            sanitized["step"] = b.get("step", 0)
            # 保留 HITL 审批标记，使历史会话也能显示「已批准/已拒绝/待审批」
            if b.get("approval_status"):
                sanitized["approval_status"] = b["approval_status"]
            if b.get("pending_approval"):
                sanitized["pending_approval"] = True
            if b.get("file_paths"):
                sanitized["file_paths"] = b["file_paths"]
        elif block_type == "content":
            sanitized["content"] = b.get("content", "") or ""
        else:
            sanitized["content"] = b.get("content", "") or ""
        sanitized_blocks.append(sanitized)
    # 按 order 排序恢复原始顺序
    sanitized_blocks.sort(key=lambda x: x.get("order", 0))
    return sanitized_blocks

def _sanitize_tool_calls(db, tool_calls: list) -> list:
    """规范化 tool_calls 字段（最多保留 1000 条）。"""
    return [
        {
            "tool_name": tc.get("tool_name", "") or tc.get("name", ""),
            "tool_call_id": tc.get("tool_call_id", ""),
            "arguments": tc.get("arguments", {}),
            "result": str(tc.get("result", "")),
            "success": tc.get("success", True),
            "duration": tc.get("duration"),
            "step": tc.get("step", 0),
            "approval_status": tc.get("approval_status"),
        }
        for tc in tool_calls[:1000]
    ]

#: extra_data 序列化后的长度上限（字符）
_MAX_EXTRA_DATA_CHARS = 5_000_000
#: 超限后的第一步：各类块的长字段截到这个长度
_TRIM_LIMITS = {"tool_call": ("result", 200), "thinking": ("content", 200), "content": ("content", 500)}
#: 仍然超限时的兜底：长字段只留开头这么多字并加省略号
_STRIP_LIMITS = {"tool_call": ("result", 100), "thinking": ("content", 100), "content": ("content", 200)}


def _trim_long_fields(extra: dict) -> bool:
    """把块、工具调用结果和 thinking 的长字段截到固定长度，返回是否截断过。"""
    trimmed = False
    blocks = extra.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            rule = _TRIM_LIMITS.get(block.get("type"))
            if rule is None:
                continue
            field, limit = rule
            value = str(block.get(field, ""))
            if len(value) > limit:
                block[field] = value[:limit]
                trimmed = True
    tool_calls = extra.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            value = str(tc.get("result", ""))
            if len(value) > 200:
                tc["result"] = value[:200]
                trimmed = True
    if "thinking" in extra and len(str(extra["thinking"])) > 200:
        extra["thinking"] = str(extra["thinking"])[:200]
        trimmed = True
    return trimmed


def _head(value, limit: int) -> str:
    return str(value)[:limit] + "..." if value else ""


def _strip_to_metadata(extra: dict) -> None:
    """最终兜底：清空参数、长字段只留开头，保留 blocks / tool_calls 的结构
    （tool_name、success、duration 等元信息）。"""
    blocks = extra.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if block.get("type") == "tool_call":
                block["arguments"] = {}
            rule = _STRIP_LIMITS.get(block.get("type"))
            if rule is not None:
                field, limit = rule
                block[field] = _head(block.get(field), limit)
    tool_calls = extra.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            tc["arguments"] = {}
            tc["result"] = _head(tc.get("result"), 100)


def _sanitize_extra_data(db, message: dict) -> str | None:
    extra_keys = {
        k: v
        for k, v in message.items()
        if k not in ("role", "content", "timestamp")
    }
    if not extra_keys:
        return None

    if isinstance(extra_keys.get("blocks"), list):
        extra_keys["blocks"] = _sanitize_blocks(db, extra_keys["blocks"])
    if isinstance(extra_keys.get("tool_calls"), list):
        extra_keys["tool_calls"] = _sanitize_tool_calls(db, extra_keys["tool_calls"])

    # thinking 不截断：完整思考用于下一轮上下文；超大时由下方的裁剪兜底
    result = json.dumps(extra_keys, ensure_ascii=False)
    if len(result) <= _MAX_EXTRA_DATA_CHARS:
        return result

    # 先截断长字段（不直接删除 blocks / tool_calls）；截断一次后不会再有可截的内容
    if _trim_long_fields(extra_keys):
        result = json.dumps(extra_keys, ensure_ascii=False)
        if len(result) <= _MAX_EXTRA_DATA_CHARS:
            return result

    _strip_to_metadata(extra_keys)
    return json.dumps(extra_keys, ensure_ascii=False)

def add_message_row(db, session_id: str, message: dict):
    from easy_agent.db.session_messages import add_message_row as _add_row

    _add_row(db, session_id, message)

def annotate_user_turn(db, session_id: str, turn_id: str, fields: dict) -> bool:
    """把 ``fields`` 合并进本轮用户消息行的 extra_data，按 ``turn_id`` 定位（DA-03）。

    本轮用户消息刚写入，正常就是最后一条用户行，所以只看最近几条。
    """
    if not turn_id or not fields:
        return False
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT id, extra_data FROM session_messages WHERE session_id=? AND role=? "
            "ORDER BY id DESC LIMIT 5",
            (session_id, "user"),
        )
        for row in cursor.fetchall():
            row_id = row["id"] if isinstance(row, dict) else row[0]
            raw = row["extra_data"] if isinstance(row, dict) else row[1]
            try:
                extra = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                continue
            if not isinstance(extra, dict) or extra.get("turn_id") != turn_id:
                continue
            extra.update(fields)
            db._execute(
                cursor,
                "UPDATE session_messages SET extra_data=? WHERE id=?",
                (json.dumps(extra, ensure_ascii=False), row_id),
            )
            return True
    return False


def update_last_assistant_message_row(db, session_id: str, message: dict):
    role = "assistant"
    content = message.get("content", "")
    extra_data = _sanitize_extra_data(db, message)
    timestamp = message.get("timestamp", datetime.now().isoformat())

    with db.get_connection() as conn:
        cursor = conn.cursor()
        # 检查最后一条消息是否是 assistant（不区分 role 排序取最后一条）
        db._execute(
            cursor,
            "SELECT id, role FROM session_messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        last_row = cursor.fetchone()

        if last_row and (
            last_row["id"] if isinstance(last_row, dict) else last_row[0]
        ):
            last_role = (
                last_row["role"] if isinstance(last_row, dict) else last_row[1]
            )
            if last_role == "assistant":
                # 最后一条是 assistant，更新它
                msg_id = (
                    last_row["id"] if isinstance(last_row, dict) else last_row[0]
                )
                db._execute(
                    cursor,
                    "UPDATE session_messages SET content=?, extra_data=?, created_at=? WHERE id=?",
                    (content, extra_data, timestamp, msg_id),
                )
            else:
                # 最后一条不是 assistant（通常是 user），新增一行
                db._execute(
                    cursor,
                    """
                    INSERT INTO session_messages (session_id, role, content, extra_data, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, role, content, extra_data, timestamp),
                )
        else:
            # 没有任何消息，新增一行
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
            "UPDATE sessions SET updated_at=? WHERE session_id=?",
            (datetime.now().isoformat(), session_id),
        )

def try_repair_json(raw: str) -> dict | None:
    if not raw:
        return None

    def _scan_structural_ends(text: str) -> list[int]:
        ends = []
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in ("}", "]"):
                ends.append(i)
        return ends

    def _close_brackets(candidate: str) -> dict | None:
        in_str = False
        esc = False
        stack = []
        for ch in candidate:
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in ("{", "["):
                stack.append(ch)
            elif ch in ("}", "]"):
                if stack:
                    stack.pop()
        for b in reversed(stack):
            candidate += "}" if b == "{" else "]"
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None

    blocks_start = raw.find('"blocks": [')
    if blocks_start > 0:
        search_area = raw[: len(raw) - 200]
        last_boundary = search_area.rfind('}, {"type"')
        if last_boundary > blocks_start:
            truncated = raw[: last_boundary + 1]
            result = _close_brackets(truncated)
            if result is not None:
                return result

    structural_ends = _scan_structural_ends(raw)
    for pos in reversed(structural_ends):
        candidate = raw[: pos + 1]
        result = _close_brackets(candidate)
        if result is not None:
            return result

    return None

def get_messages_from_rows(db, session_id: str) -> list[dict]:
    from easy_agent.db.session_messages import get_messages_from_rows as _from_rows

    return _from_rows(db, session_id)

def get_messages(db, session_id: str) -> list[dict]:
    session = get_session(db, session_id)
    return session.messages if session else []

def count_sessions(db, username: str = None) -> int:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if username:
            db._execute(
                cursor,
                "SELECT COUNT(*) FROM sessions WHERE username=? AND title NOT LIKE ?",
                (username, "[定时任务]%"),
            )
        else:
            db._execute(
                cursor,
                "SELECT COUNT(*) FROM sessions WHERE title NOT LIKE ?",
                ("[定时任务]%",),
            )
        row = cursor.fetchone()
        return row[0] if row else 0

def update_session_title(db, session_id: str, title: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE sessions SET title=? WHERE session_id=?",
            (title, session_id),
        )

def toggle_session_pin(db, session_id: str) -> int:
    """切换会话置顶状态，返回新的 pinned 值"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT pinned FROM sessions WHERE session_id=?",
            (session_id,),
        )
        row = cursor.fetchone()
        current = int(row["pinned"]) if row else 0
        new_val = 0 if current else 1
        db._execute(
            cursor,
            "UPDATE sessions SET pinned=? WHERE session_id=?",
            (new_val, session_id),
        )
        return new_val
