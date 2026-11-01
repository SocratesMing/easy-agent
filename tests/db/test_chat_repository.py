"""C1 剩余域：``ChatRepository`` 会话/消息委托与归属校验。"""

import pytest

from easy_agent.db.database import Database
from easy_agent.db.repositories.agent_runs import AgentRunRepository
from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.db.repositories.file_repository import FileRepository
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.db.repositories.session_runtime import SessionRuntimeRepository
from easy_agent.db.repositories.stream_events import StreamEventRepository
from easy_agent.models.db import SessionModel
from easy_agent.models.exceptions import NotFoundError


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


def test_repository_exposes_session_files_for_upload_ports(tmp_path):
    db = _make_db(tmp_path)
    repo = ChatRepository(db)
    repo.create_session(_session())
    FileRepository(db).add_session_file(
        "sess-chat", "方案.pdf", "/tmp/方案.pdf", "pdf", 42, "alice"
    )

    rows = repo.get_session_files("sess-chat")
    assert [row["filename"] for row in rows] == ["方案.pdf"]
    assert repo.get_session_files("missing") == []


def test_repository_satisfies_session_uploads_workspace_port(tmp_path, monkeypatch):
    """session_uploads 直接吃 repository 端口（不再需要 repo.database）。"""
    from easy_agent.services import session_uploads as su

    db = _make_db(tmp_path)
    repo = ChatRepository(db)
    repo.create_session(_session())
    repo.update_session_workspace_name("sess-chat", "ws-chat")
    monkeypatch.setattr(
        su.Config,
        "get_user_workspace_dir",
        staticmethod(lambda username: tmp_path / "users" / username),
        raising=False,
    )

    workspace = su.get_session_workspace_dir("sess-chat", "alice", repo)
    assert workspace == tmp_path / "users" / "alice" / "session" / "ws-chat"


def test_repository_get_owned_session_meta_enforces_owner_and_404(tmp_path):
    db = _make_db(tmp_path)
    repo = ChatRepository(db)
    repo.create_session(_session(username="alice"))
    repo.update_session_workspace_name("sess-chat", "ws-chat")

    meta = repo.get_owned_session_meta("sess-chat", "alice")
    assert meta["username"] == "alice"
    assert meta["workspace_name"] == "ws-chat"

    with pytest.raises(NotFoundError) as cross_user:
        repo.get_owned_session_meta("sess-chat", "bob")
    assert cross_user.value.message == "会话不存在"

    with pytest.raises(NotFoundError) as missing:
        repo.get_owned_session_meta("missing", "alice")
    assert missing.value.message == "会话不存在"

    assert repo.renew_stream("missing") is False


def test_repository_composes_the_five_domain_repositories(tmp_path):
    """T3：ChatRepository 是组合门面，领域 repository 各自可单独注入/替换。"""
    db = _make_db(tmp_path)
    repo = ChatRepository(db)

    assert isinstance(repo.sessions, SessionRepository)
    assert isinstance(repo.messages, MessageRepository)
    assert isinstance(repo.runs, AgentRunRepository)
    assert isinstance(repo.runtime, SessionRuntimeRepository)
    assert isinstance(repo.stream_events, StreamEventRepository)
    # 兼容方法委托到组合出来的领域 repository（同一底层连接）
    assert repo.sessions.database is db
    repo.create_session(_session())
    repo.messages.add_message(
        "sess-chat", {"role": "user", "content": "组合", "timestamp": "t1"}
    )
    assert [m["content"] for m in repo.messages.get_messages("sess-chat")] == ["组合"]
    # run / 流事件域也能从门面直接拿到底层记录
    assert repo.runs.get_run("missing-run") is None
    assert repo.stream_events.clear_stream_events("sess-chat") == 0
