"""C1 剩余域：``ChatRepository`` 会话/消息委托与归属校验。"""

import pytest
from fastapi import HTTPException

from easy_agent.db.database import Database
from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.models.db import SessionModel


def _make_db(tmp_path) -> Database:
    db = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "chat.db")}}
    )
    db.init_tables()
    return db


def _session(session_id: str = "sess-chat", username: str = "alice") -> SessionModel:
    return SessionModel(
        session_id=session_id,
        title="聊天会话",
        messages=[],
        created_at="2026-10-18T00:00:00",
        updated_at="2026-10-18T00:00:00",
        username=username,
        workspace_name="ws-chat",
    )


def test_repository_delegates_session_and_message_writes(tmp_path):
    db = _make_db(tmp_path)
    repo = ChatRepository(db)

    assert repo.database is db
    repo.create_session(_session())
    repo.update_session_workspace_name("sess-chat", "ws-renamed")
    assert repo.get_session("sess-chat").workspace_name == "ws-renamed"

    repo.add_message(
        "sess-chat",
        {"role": "user", "content": "你好", "timestamp": "t1"},
    )
    repo.add_message(
        "sess-chat",
        {"role": "assistant", "content": "收到", "timestamp": "t2"},
    )
    repo.update_session_meta("sess-chat", "新标题", "t3", "alice")
    loaded = repo.get_session("sess-chat")
    assert loaded.title == "新标题"
    assert [m["content"] for m in loaded.messages] == ["你好", "收到"]
    assert repo.get_session("missing") is None


def test_repository_get_owned_session_meta_enforces_owner_and_404(tmp_path):
    db = _make_db(tmp_path)
    repo = ChatRepository(db)
    repo.create_session(_session(username="alice"))
    repo.update_session_workspace_name("sess-chat", "ws-chat")

    meta = repo.get_owned_session_meta("sess-chat", "alice")
    assert meta["username"] == "alice"
    assert meta["workspace_name"] == "ws-chat"

    with pytest.raises(HTTPException) as cross_user:
        repo.get_owned_session_meta("sess-chat", "bob")
    assert cross_user.value.status_code == 404
    assert cross_user.value.detail == "会话不存在"

    with pytest.raises(HTTPException) as missing:
        repo.get_owned_session_meta("missing", "alice")
    assert missing.value.status_code == 404

    assert repo.renew_stream("missing") is False
