"""会话文件/生成文件 repository（ARCH-03 Phase 2）。

从 `Database` 抽出的 files 领域：session_files + generated_files。
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("easy_agent.db.files")


def add_session_file(
    db,
    session_id: str,
    filename: str,
    file_path: str,
    file_type: str,
    size: int,
    username: str = "",
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO session_files (session_id, filename, file_path, file_type, size, uploaded_at, username)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                filename,
                file_path,
                file_type,
                size,
                datetime.now().isoformat(),
                username,
            ),
        )
        return getattr(cursor, "lastrowid", None)


def get_session_files(db, session_id: str) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM session_files WHERE session_id=? ORDER BY uploaded_at DESC",
            (session_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_file_record(db, file_id: int, username: str | None = None) -> dict | None:
    """按 id 读取单条会话文件记录（删除前取 ``file_path`` 用）。

    对象存储模式下 ``file_path`` 可能是 ``store://`` URI，删除 DB 行之后还需要
    凭它清理 Store 对象，因此先读后删。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if username:
            db._execute(
                cursor,
                "SELECT * FROM session_files WHERE id=? AND username=?",
                (file_id, username),
            )
        else:
            db._execute(cursor, "SELECT * FROM session_files WHERE id=?", (file_id,))
        row = cursor.fetchone()
    return dict(row) if row else None


def delete_file(db, file_id: int, username: str | None = None) -> bool:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if username:
            db._execute(
                cursor,
                "DELETE FROM session_files WHERE id=? AND username=?",
                (file_id, username),
            )
        else:
            db._execute(cursor, "DELETE FROM session_files WHERE id=?", (file_id,))
        return cursor.rowcount > 0


def add_generated_file(
    db,
    session_id: str,
    message_id: str,
    filename: str,
    file_path: str,
    file_type: str,
    size: int,
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO generated_files (session_id, message_id, filename, file_path, file_type, size, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                message_id,
                filename,
                file_path,
                file_type,
                size,
                datetime.now().isoformat(),
            ),
        )


def get_generated_files(db, session_id: str) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM generated_files WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def delete_generated_file(db, file_id: int) -> bool:
    """删除单条生成文件记录（例如技能已入库、工作区不再保留该文件）。"""
    if file_id is None:
        return False
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db._execute(cursor, "DELETE FROM generated_files WHERE id=?", (file_id,))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"删除生成文件记录失败 | id={file_id} | {exc}")
        return False


def get_generated_filenames(db, username: str) -> set[str]:
    """返回指定用户所有会话中生成的文件原始文件名集合。"""
    if not username:
        return set()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT gf.filename FROM generated_files gf
            JOIN sessions s ON gf.session_id = s.session_id
            WHERE s.username = ?
            """,
            (username,),
        )
        rows = cursor.fetchall()
    return {r["filename"] for r in rows if r["filename"]}
