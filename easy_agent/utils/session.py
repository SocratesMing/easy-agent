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
    from ..db import Database
    from ..models.db import SessionModel


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


def session_owned_by(
    db: "Database", session_id: str, username: str
) -> "SessionModel | None":
    """Return the session if it exists **and** belongs to *username*.

    Returns ``None`` when the session does not exist or is owned by another
    user.  Use this when the caller needs to distinguish "not found / not mine"
    from a genuine error (e.g. ``chat_stream`` creates a new session when the
    ID is unknown but must reject an ID that belongs to someone else).
    """
    session = db.get_session(session_id)
    if not session or (session.username or "") != (username or ""):
        return None
    return session
