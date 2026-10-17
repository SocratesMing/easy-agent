"""C1 剩余域：``FileRepository`` 委托、归属校验与删除权限。"""

import pytest
from fastapi import HTTPException

from easy_agent.db.database import Database
from easy_agent.db.repositories.file_repository import FileRepository
from easy_agent.models.db import SessionModel


def _make_db(tmp_path) -> Database:
    db = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "files.db")}}
    )
    db.init_tables()
    return db


def _session(session_id: str = "sess-files", username: str = "alice") -> SessionModel:
    return SessionModel(
        session_id=session_id,
        title="文件会话",
        messages=[],
        created_at="2026-10-18T00:00:00",
        updated_at="2026-10-18T00:00:00",
        username=username,
        workspace_name="ws-files",
    )


def test_repository_delegates_session_files_and_generated_files(tmp_path):
    db = _make_db(tmp_path)
    repo = FileRepository(db)
    db.create_session(_session())
    db.update_session_workspace_name("sess-files", "ws-files")

    assert repo.database is db
    repo.add_session_file(
        session_id="sess-files",
        filename="report.pdf",
        file_path="/tmp/report.pdf",
        file_type="pdf",
        size=123,
        username="alice",
    )
    files = repo.get_session_files("sess-files")
    assert files[0]["filename"] == "report.pdf"
    assert repo.get_session_files("missing") == []

    db.add_generated_file(
        "sess-files", "msg-1", "chart.png", "/tmp/chart.png", "png", 456
    )
    generated = repo.get_generated_files("sess-files")
    assert generated[0]["filename"] == "chart.png"
    assert repo.get_generated_filenames("alice") == {"chart.png"}
    assert repo.get_generated_filenames("bob") == set()

    file_id = files[0]["id"]
    assert repo.delete_file(file_id, username="bob") is False
    assert repo.delete_file(file_id, username="alice") is True
    assert repo.get_session_files("sess-files") == []


def test_repository_get_owned_session_meta_enforces_owner_and_404(tmp_path):
    db = _make_db(tmp_path)
    repo = FileRepository(db)
    db.create_session(_session(username="alice"))
    db.update_session_workspace_name("sess-files", "ws-files")

    meta = repo.get_owned_session_meta("sess-files", "alice")
    assert meta["username"] == "alice"
    assert meta["workspace_name"] == "ws-files"

    with pytest.raises(HTTPException) as cross_user:
        repo.get_owned_session_meta("sess-files", "bob")
    assert cross_user.value.status_code == 404
    assert cross_user.value.detail == "会话不存在"

    with pytest.raises(HTTPException) as missing:
        repo.get_owned_session_meta("missing", "alice")
    assert missing.value.status_code == 404
