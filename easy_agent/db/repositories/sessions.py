"""会话与消息 SQL repository（ARCH-03 Phase 2 / T3）。

从 Database 抽出的 sessions/messages 领域 SQL 函数：会话 CRUD、消息行、legacy
JSON 只读回退与计数。session_messages 的行级 helper 仍由本模块惰性调用。

T3 起 sanitize / JSON 修复等与连接无关的纯函数在
:mod:`easy_agent.db.message_serialization`（本模块只 re-export
``_sanitize_extra_data`` 供既有测试使用），用来打断
``repositories.sessions`` ↔ ``session_messages`` 的循环导入。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from easy_agent.models.db import SessionModel

from easy_agent.db.message_serialization import (  # noqa: F401 - 兼容重导出
    _sanitize_extra_data,
)
from easy_agent.db.session_messages import get_messages_from_rows as _from_rows
from easy_agent.db.session_messages import add_message_row as _add_row

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

def list_sessions(
    db, limit: int = 50, offset: int = 0, username: str | None = None
) -> list[SessionModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if username:
            db._execute(
                cursor,
                """
                SELECT s.session_id, s.title, s.created_at, s.updated_at, s.username,
                       s.workspace_name, s.pinned, s.message_count
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
                       s.workspace_name, s.pinned, s.message_count
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


def update_last_assistant_message(db, session_id: str, message: dict):
    update_last_assistant_message_row(db, session_id, message)


#: extra_data 序列化后的长度上限（字符）
#: 超限后的第一步：各类块的长字段截到这个长度
#: 仍然超限时的兜底：长字段只留开头这么多字并加省略号


def add_message_row(db, session_id: str, message: dict):

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
                db._execute(
                    cursor,
                    "UPDATE sessions SET message_count=message_count+1 WHERE session_id=?",
                    (session_id,),
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
                "UPDATE sessions SET message_count=message_count+1 WHERE session_id=?",
                (session_id,),
            )
        db._execute(
            cursor,
            "UPDATE sessions SET updated_at=? WHERE session_id=?",
            (datetime.now().isoformat(), session_id),
        )


def get_messages_from_rows(db, session_id: str) -> list[dict]:

    return _from_rows(db, session_id)

def get_messages(db, session_id: str) -> list[dict]:
    session = get_session(db, session_id)
    return session.messages if session else []

def count_sessions(db, username: str | None = None) -> int:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if username:
            db._execute(
                cursor,
                "SELECT COUNT(*) AS n FROM sessions WHERE username=? AND title NOT LIKE ?",
                (username, "[定时任务]%"),
            )
        else:
            db._execute(
                cursor,
                "SELECT COUNT(*) AS n FROM sessions WHERE title NOT LIKE ?",
                ("[定时任务]%",),
            )
        row = cursor.fetchone()
        if not row:
            return 0
        # MySQL 连接使用 DictCursor（db/database.py），SQLite 返回 tuple：
        # 两种游标都要兼容（回归 D2：MySQL 下 row[0] 会 KeyError: 0）。
        return int(row["n"]) if isinstance(row, dict) else int(row[0])

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
