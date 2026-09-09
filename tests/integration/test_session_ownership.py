"""跨用户会话访问隔离测试。

验证所有按 session_id 访问的接口都校验会话归属：用户 A 无法读取、修改、删除
用户 B 的会话记录、文件列表或工作区文件树。这是对 IDOR（不安全直接对象引用）
漏洞的回归测试。

直接调用处理函数并使用真实临时数据库，不依赖 TestClient lifespan（避免启动
重型调度器导致的挂起）。
"""

import pytest
from fastapi import HTTPException

from easy_agent.models.db import SessionModel
from easy_agent.utils.session import get_owned_session, session_owned_by


def _make_session(session_id: str, username: str) -> SessionModel:
    return SessionModel(
        session_id=session_id,
        title=f"title-{session_id}",
        messages=[],
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        username=username,
        workspace_name=f"ws_{session_id}",
    )


@pytest.fixture()
def two_user_sessions(db):
    """创建分属 userA / userB 的两个会话。"""
    db.create_session(_make_session("sess-A", "userA"))
    db.create_session(_make_session("sess-B", "userB"))
    return db


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_get_owned_session_returns_when_owner(two_user_sessions):
    session = get_owned_session(two_user_sessions, "sess-A", "userA")
    assert session.session_id == "sess-A"
    assert session.username == "userA"


def test_get_owned_session_denies_cross_user(two_user_sessions):
    """userB 访问 userA 的会话 -> 404（不泄露会话是否存在）。"""
    with pytest.raises(HTTPException) as exc:
        get_owned_session(two_user_sessions, "sess-A", "userB")
    assert exc.value.status_code == 404


def test_get_owned_session_404_for_missing(two_user_sessions):
    with pytest.raises(HTTPException) as exc:
        get_owned_session(two_user_sessions, "nonexistent", "userA")
    assert exc.value.status_code == 404


def test_session_owned_by_returns_none_cross_user(two_user_sessions):
    assert session_owned_by(two_user_sessions, "sess-A", "userB") is None
    assert session_owned_by(two_user_sessions, "sess-A", "userA") is not None
    assert session_owned_by(two_user_sessions, "nope", "userA") is None


# ---------------------------------------------------------------------------
# sessions.py endpoints
# ---------------------------------------------------------------------------


async def test_get_session_denies_cross_user(two_user_sessions):
    from easy_agent.api.sessions import get_session

    # userA reads own session -> OK
    detail = await get_session("sess-A", db=two_user_sessions, username="userA")
    assert detail.session_id == "sess-A"

    # userB reads userA's session -> 404
    with pytest.raises(HTTPException) as exc:
        await get_session("sess-A", db=two_user_sessions, username="userB")
    assert exc.value.status_code == 404


async def test_get_chat_history_denies_cross_user(two_user_sessions):
    from easy_agent.api.sessions import get_chat_history

    with pytest.raises(HTTPException) as exc:
        await get_chat_history("sess-A", db=two_user_sessions, username="userB")
    assert exc.value.status_code == 404


async def test_update_title_denies_cross_user(two_user_sessions):
    from easy_agent.api.sessions import update_title
    from easy_agent.models.api import UpdateTitleRequest

    req = UpdateTitleRequest(title="hacked")
    with pytest.raises(HTTPException) as exc:
        await update_title("sess-A", req, db=two_user_sessions, username="userB")
    assert exc.value.status_code == 404
    # 标题未被篡改
    assert two_user_sessions.get_session("sess-A").title == "title-sess-A"


async def test_toggle_pin_denies_cross_user(two_user_sessions):
    from easy_agent.api.sessions import toggle_pin

    with pytest.raises(HTTPException) as exc:
        await toggle_pin("sess-A", db=two_user_sessions, username="userB")
    assert exc.value.status_code == 404


async def test_delete_session_denies_cross_user(two_user_sessions):
    from easy_agent.api.sessions import delete_session

    with pytest.raises(HTTPException) as exc:
        await delete_session("sess-A", db=two_user_sessions, username="userB")
    assert exc.value.status_code == 404
    # 会话仍然存在
    assert two_user_sessions.get_session("sess-A") is not None


async def test_add_message_denies_cross_user(two_user_sessions):
    from easy_agent.api.sessions import add_message
    from easy_agent.models.api import AddMessageRequest

    req = AddMessageRequest(role="user", content="injected")
    with pytest.raises(HTTPException) as exc:
        await add_message("sess-A", req, db=two_user_sessions, username="userB")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# files.py endpoints
# ---------------------------------------------------------------------------


async def test_get_session_generated_files_denies_cross_user(two_user_sessions):
    from easy_agent.api.files import get_session_generated_files

    with pytest.raises(HTTPException) as exc:
        await get_session_generated_files(
            "sess-A", db=two_user_sessions, username="userB"
        )
    assert exc.value.status_code == 404


async def test_list_files_denies_cross_user(two_user_sessions):
    from easy_agent.api.files import list_files

    with pytest.raises(HTTPException) as exc:
        await list_files(
            db=two_user_sessions, username="userB", session_id="sess-A"
        )
    assert exc.value.status_code == 404


async def test_get_workspace_tree_denies_cross_user(two_user_sessions, monkeypatch):
    from easy_agent.api import files as files_mod
    from easy_agent.api.files import get_workspace_tree
    from easy_agent.config import Config

    tmp_base = two_user_sessions  # just for naming
    # 重定向工作区目录到临时目录，避免触碰真实文件系统
    import tempfile, os
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(
        Config, "get_user_workspace_dir",
        staticmethod(lambda u: tmp / "users" / u),
    )

    with pytest.raises(HTTPException) as exc:
        await get_workspace_tree(
            username="userB", session_id="sess-A", db=two_user_sessions
        )
    assert exc.value.status_code == 404
