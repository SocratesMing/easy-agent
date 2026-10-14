"""用户登录态滑动过期测试。"""

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from easy_agent.api import auth as auth_api
from easy_agent.middleware import auth as auth_middleware
from easy_agent.utils.auth import create_access_token, decode_access_token


class _AgentConfig:
    def __init__(self, idle_logout_minutes):
        self.idle_logout_minutes = idle_logout_minutes


def _config(idle_logout_minutes):
    return {"config": SimpleNamespace(agent=_AgentConfig(idle_logout_minutes))}


class _Request:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}


class _User:
    token_version = 1


class _Database:
    def __init__(self, user=None):
        self.user = user

    def get_user_by_username(self, username):
        if username == "alice":
            return self.user or _User()
        return None


def _expired_token(username="alice", token_version=1):
    return create_access_token(
        data={"sub": username, "v": token_version},
        expires_delta=timedelta(minutes=-1),
    )


def _active_token(username="alice", token_version=1):
    return create_access_token(
        data={"sub": username, "v": token_version},
        expires_delta=timedelta(minutes=30),
    )


@pytest.fixture(autouse=True)
def clear_user_activity_cache():
    yield
    auth_middleware._user_activity_cache.clear()


@pytest.mark.asyncio
async def test_recent_api_call_renews_expired_token_session(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    auth_middleware._user_activity_cache.clear()
    auth_middleware._user_activity_cache["alice"] = auth_middleware.time.time() - 30
    token = _expired_token()

    username = await auth_middleware.get_current_username(
        _Request(token), _Database()
    )

    assert username == "alice"
    assert auth_middleware._user_activity_cache["alice"] > auth_middleware.time.time() - 2


@pytest.mark.asyncio
async def test_expired_token_fails_after_idle_timeout(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    auth_middleware._user_activity_cache.clear()
    auth_middleware._user_activity_cache["alice"] = auth_middleware.time.time() - 61
    token = _expired_token()

    with pytest.raises(HTTPException) as exc_info:
        await auth_middleware.get_current_username(_Request(token), _Database())

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_unexpired_token_fails_after_idle_timeout(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    auth_middleware._user_activity_cache.clear()
    auth_middleware._user_activity_cache["alice"] = auth_middleware.time.time() - 61
    token = _active_token()

    with pytest.raises(HTTPException) as exc_info:
        await auth_middleware.get_current_username(_Request(token), _Database())

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_recent_signed_token_restores_idle_cache_after_process_restart(monkeypatch):
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: _config(1))
    auth_middleware._user_activity_cache.clear()

    username = await auth_middleware.get_current_username(
        _Request(_active_token()), _Database()
    )

    assert username == "alice"
    assert auth_middleware._user_activity_cache["alice"] > auth_middleware.time.time() - 2


def test_stale_token_issue_time_cannot_reopen_idle_window(monkeypatch):
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: _config(1))
    auth_middleware._user_activity_cache.clear()
    now = auth_middleware.time.time()

    assert auth_middleware._user_session_is_active(
        "alice", now=now, token_issued_at=now - 61
    ) is False


def test_verify_token_sso_rejects_after_idle_timeout(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    auth_middleware._user_activity_cache.clear()
    auth_middleware._user_activity_cache["alice"] = auth_middleware.time.time() - 61
    token = _active_token()

    assert auth_middleware.verify_token_sso(token, _Database()) is None


@pytest.mark.asyncio
async def test_main_auth_rejects_disabled_user_even_with_current_token(monkeypatch):
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: _config(0))
    disabled = SimpleNamespace(token_version=1, account_status="disabled")

    with pytest.raises(HTTPException) as exc_info:
        await auth_middleware.get_current_username(
            _Request(_active_token()), _Database(disabled)
        )

    assert exc_info.value.status_code == 403


def test_query_token_auth_rejects_disabled_user(monkeypatch):
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: _config(0))
    disabled = SimpleNamespace(token_version=1, account_status="disabled")

    assert auth_middleware.verify_token_sso(
        _active_token(), _Database(disabled)
    ) is None


@pytest.mark.asyncio
async def test_zero_idle_timeout_allows_expired_token_without_cache(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(0)
    )
    auth_middleware._user_activity_cache.clear()
    token = _expired_token()

    username = await auth_middleware.get_current_username(
        _Request(token), _Database()
    )

    assert username == "alice"
    assert "alice" in auth_middleware._user_activity_cache


def test_zero_idle_timeout_issues_non_expiring_token(monkeypatch):
    monkeypatch.setattr(
        auth_api, "get_agent_config", lambda: _config(0)
    )

    assert auth_api._token_never_expires() is True
    token = create_access_token(
        data={"sub": "alice", "v": 1}, never_expires=True
    )

    assert "exp" not in decode_access_token(token)


@pytest.mark.asyncio
async def test_logout_invalidates_never_expiring_token():
    class _LogoutDatabase:
        token_version = 1

        def increment_user_token_version(self, username, *, require_active=False):
            assert username == "alice"
            self.token_version += 1
            return self.token_version

    database = _LogoutDatabase()
    token = create_access_token(
        data={"sub": "alice", "v": 1}, never_expires=True
    )
    auth_api._login_time_cache["alice"] = auth_api.datetime.now()
    auth_middleware._user_activity_cache["alice"] = auth_middleware.time.time()

    await auth_api.logout("alice", _Request(token), database)

    assert database.token_version == 2
    assert "alice" not in auth_api._login_time_cache
    assert "alice" not in auth_middleware._user_activity_cache


@pytest.mark.asyncio
async def test_logout_logs_first_login_and_last_activity(caplog):
    class _LogoutDatabase:
        def increment_user_token_version(self, username, *, require_active=False):
            return 2

    token = create_access_token(
        data={"sub": "alice", "v": 1}, never_expires=True
    )
    first_login = datetime(2026, 8, 20, 15, 13, 21)
    last_activity = datetime(2026, 8, 20, 15, 32, 10)
    auth_api._login_time_cache["alice"] = first_login
    auth_middleware._user_activity_cache["alice"] = last_activity.timestamp()
    caplog.set_level(logging.INFO, logger="easy_agent.api.auth")

    await auth_api.logout("alice", _Request(token), _LogoutDatabase())

    assert "[用户] 登出" in caplog.text
    assert "第一次登录时间: 2026-08-20 15:13:21" in caplog.text
    assert "上一次缓存更新时间: 2026-08-20 15:32:10" in caplog.text
