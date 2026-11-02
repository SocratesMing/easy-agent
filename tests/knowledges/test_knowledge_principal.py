"""知识库鉴权与主鉴权一致：不看 exp，按单点登录版本和空闲登出判定，文案相同。

此前 ``get_knowledge_principal`` 单独校验 exp，签发 30 分钟后知识库接口先于其他接口 401，
被踢下线时返回英文文案，前端识别不出「被迫下线」而去自动续期。
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from easy_agent.db import login_state
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.knowledges.api import get_knowledge_principal
from easy_agent.utils.auth import create_access_token


def _config(idle_minutes):
    return {"config": SimpleNamespace(agent=SimpleNamespace(idle_logout_minutes=idle_minutes))}


def _request(token):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/agent/knowledge/v1/capabilities",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("10.0.0.5", 50000),
            "query_string": b"",
        }
    )


@pytest.fixture(autouse=True)
def _clear_throttle():
    login_state._activity_write_throttle.clear()
    yield
    login_state._activity_write_throttle.clear()


@pytest.fixture()
def alice(db, monkeypatch):
    """注册 alice 并登录一次，返回 (token_version, repo)；空闲登出 1 分钟。"""
    monkeypatch.setattr(login_state, "get_runtime_agent_config", lambda: _config(1))
    db.register_user(username="alice", password="secret-pass")
    version = db.increment_user_token_version("alice")
    repo = UserRepository(db)
    repo.touch_user_activity("alice", time.time())
    return version, repo


def test_token_past_exp_is_accepted_while_session_is_active(db, alice):
    version, _ = alice
    expired = create_access_token(
        data={"sub": "alice", "v": version}, expires_delta=timedelta(minutes=-5)
    )
    request = _request(expired)

    principal = get_knowledge_principal(request, db)

    assert principal.username == "alice"
    assert request.state.actor_username == "alice"


def test_idle_timeout_applies_to_knowledge_routes(db, alice):
    version, repo = alice
    repo.touch_user_activity("alice", time.time() - 61)
    token = create_access_token(data={"sub": "alice", "v": version})

    with pytest.raises(HTTPException) as exc_info:
        get_knowledge_principal(_request(token), db)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == login_state.EXPIRED_DETAIL
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_kicked_token_gets_the_same_detail_as_main_auth(db, alice):
    version, _ = alice
    stale = create_access_token(data={"sub": "alice", "v": version - 1})

    with pytest.raises(HTTPException) as exc_info:
        get_knowledge_principal(_request(stale), db)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == login_state.KICKED_DETAIL
