"""Session ownership helpers - prevent cross-user access to session data.

All session-by-ID API endpoints must verify that the requesting user owns the
session before returning, modifying, or deleting it.  Without this check any
authenticated user who knows (or guesses) another user's ``session_id`` could
read their full conversation history, hijack their chat, browse their workspace
files, or delete their sessions.

These helpers centralise the ownership check so it cannot be forgotten.  A
non-existent session and a session owned by another user both return **404**
(not 403) to avoid leaking which session IDs exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from easy_agent.db import Database
    from easy_agent.models.db import SessionModel


def get_owned_session_meta(
    db: "Database", session_id: str, username: str
) -> dict:
    """轻量版 get_owned_session：只返回归属/工作区所需字段（不加载消息）。

    用于只校验归属或只要 workspace_name 的接口（文件预览/下载、chat 取消/
    resume 等），避免对长会话做全量消息加载。
    """
    meta = db.get_session_owner(session_id)
    if not meta or (meta.get("username") or "") != (username or ""):
        raise HTTPException(status_code=404, detail="会话不存在")
    return meta


def get_owned_session(
    db: "Database", session_id: str, username: str
) -> "SessionModel":
    """Return *session_id* iff it belongs to *username*, else raise 404.

    Raises ``HTTPException(404)`` when the session does not exist **or** is
    owned by a different user.  Use this in every endpoint that acts on a
    session by ID.
    """
    session = db.get_session(session_id)
    if not session or (session.username or "") != (username or ""):
        raise HTTPException(status_code=404, detail="会话不存在")
    return session
