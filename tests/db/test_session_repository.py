"""C1 第一批：``SessionRepository`` 委托与归属校验。

用临时 SQLite ``Database`` 直接构造 repository，不依赖 FastAPI app/lifespan。
覆盖 API 当前使用的全部方法，重点确认：
- repository 方法确实委托到既有 repository 函数（返回类型/字段不变）；
- ``get_owned_session`` 保持 404 语义（不存在与跨用户一致）；
- A2 之后消息仍只写 ``session_messages`` 行表。
"""

import pytest
from fastapi import HTTPException

from easy_agent.db.database import Database
from easy_agent.db.session_repository import SessionRepository
from easy_agent.models.db import SessionModel


def _make_db(tmp_path) -> Database:
    db = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "repository.db")}}
    )
    db.init_tables()
    return db


def _session(session_id: str = "sess-c1", username: str = "alice") -> SessionModel:
    return SessionModel(
        session_id=session_id,
        title="原始标题",
        messages=[],
        created_at="2026-10-18T00:00:00",
        updated_at="2026-10-18T00:00:00",
        username=username,
        workspace_name="ws",
    )


def _raw_messages(db: Database, session_id: str) -> str:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT messages FROM sessions WHERE session_id=?",
            (session_id,),
        )
        row = cursor.fetchone()
    return row["messages"] if isinstance(row, dict) else row[0]


def test_repository_wraps_database_and_delegates_session_crud(tmp_path):
    db = _make_db(tmp_path)
    repo = SessionRepository(db)

    assert repo.database is db

    created = repo.create_session(_session())
    assert created.session_id == "sess-c1"

    repo.update_session_workspace_name("sess-c1", "ws-renamed")
    loaded = repo.get_session("sess-c1")
    assert loaded is not None
    assert loaded.title == "原始标题"
    assert loaded.workspace_name == "ws-renamed"
    assert loaded.username == "alice"

    listed = repo.list_sessions(username="alice")
    assert [item.session_id for item in listed] == ["sess-c1"]
    assert repo.list_sessions(username="bob") == []
    assert repo.count_sessions(username="alice") == 1
    assert repo.count_sessions(username="bob") == 0

    repo.update_session_title("sess-c1", "新标题")
    assert repo.get_session("sess-c1").title == "新标题"

    # 置顶是 toggle：两次调用回到 0。get_session 不 SELECT pinned（沿用原 SQL），
    # 通过 list_sessions 的 pinned 字段验证。
    assert repo.toggle_session_pin("sess-c1") == 1
    assert repo.list_sessions(username="alice")[0].pinned == 1
    assert repo.toggle_session_pin("sess-c1") == 0
    assert repo.list_sessions(username="alice")[0].pinned == 0


def test_repository_delegates_messages_and_keeps_a2_single_write(tmp_path):
    db = _make_db(tmp_path)
    repo = SessionRepository(db)
    repo.create_session(_session())

    repo.add_message(
        "sess-c1", {"role": "user", "content": "你好", "timestamp": "t1"}
    )
    repo.add_message(
        "sess-c1", {"role": "assistant", "content": "收到", "timestamp": "t2"}
    )

    assert [m["content"] for m in repo.get_messages("sess-c1")] == ["你好", "收到"]
    assert repo.get_messages("missing") == []
    # A2：正文只进 session_messages，legacy 列仍是空串。
    assert _raw_messages(db, "sess-c1") == ""


def test_repository_delegates_session_files(tmp_path):
    db = _make_db(tmp_path)
    repo = SessionRepository(db)
    repo.create_session(_session())

    repo.add_session_file(
        session_id="sess-c1",
        filename="report.pdf",
        file_path="/tmp/report.pdf",
        file_type="pdf",
        size=1234,
        username="alice",
    )

    files = repo.get_session_files("sess-c1")
    assert len(files) == 1
    assert files[0]["filename"] == "report.pdf"
    assert files[0]["file_type"] == "pdf"
    assert files[0]["size"] == 1234
    assert files[0]["username"] == "alice"
    assert repo.get_session_files("missing") == []


def test_repository_get_owned_session_enforces_owner_and_404(tmp_path):
    db = _make_db(tmp_path)
    repo = SessionRepository(db)
    repo.create_session(_session(username="alice"))

    assert repo.get_owned_session("sess-c1", "alice").session_id == "sess-c1"
    assert repo.get_session_owner("sess-c1")["username"] == "alice"
    assert repo.get_session_owner("missing") is None

    with pytest.raises(HTTPException) as cross_user:
        repo.get_owned_session("sess-c1", "bob")
    assert cross_user.value.status_code == 404
    assert cross_user.value.detail == "会话不存在"

    with pytest.raises(HTTPException) as missing:
        repo.get_owned_session("missing", "alice")
    assert missing.value.status_code == 404
    assert missing.value.detail == "会话不存在"


def test_repository_delete_session_removes_rows(tmp_path):
    db = _make_db(tmp_path)
    repo = SessionRepository(db)
    repo.create_session(_session())
    repo.add_message(
        "sess-c1", {"role": "user", "content": "to-delete", "timestamp": "t1"}
    )

    assert repo.delete_session("sess-c1") is True
    assert repo.get_session("sess-c1") is None
    assert repo.get_messages("sess-c1") == []
    # 再删一次返回 False（与既有 delete_session 语义一致）。
    assert repo.delete_session("sess-c1") is False


def test_count_sessions_handles_mysql_dict_cursor_rows():
    """D2 回归：MySQL DictCursor 返回 dict（如 {"n": N}），不能按 tuple 取 row[0]。"""
    from easy_agent.db.repositories import sessions as sessions_repo

    class _DictCursor:
        def __init__(self, row):
            self._row = row

        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return self._row

    class _ConnectionCtx:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return self._cursor

    class _FakeDb:
        def __init__(self, row):
            self._cursor = _DictCursor(row)

        def get_connection(self):
            return _ConnectionCtx(self._cursor)

        def _execute(self, cursor, sql, params):
            cursor.execute(sql, params)

    assert sessions_repo.count_sessions(_FakeDb({"n": 3}), username="alice") == 3
    assert sessions_repo.count_sessions(_FakeDb({"n": 7})) == 7
    assert sessions_repo.count_sessions(_FakeDb(None), username="alice") == 0
