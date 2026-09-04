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
    """模拟共享数据库：活跃时间存在活动记录中，可供任意 worker 读取。"""

    def __init__(self, user=None):
        self.user = user
        self.activity = {}

    def get_user_by_username(self, username):
        if username == "alice":
            return self.user or _User()
        return None

    def touch_user_activity(self, username, timestamp):
        self.activity[username] = timestamp

    def get_user_activity_time(self, username):
        return self.activity.get(username)

    def clear_user_activity(self, username):
        self.activity.pop(username, None)


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
def clear_activity_state():
    auth_middleware._activity_write_throttle.clear()
    yield
    auth_middleware._activity_write_throttle.clear()


@pytest.mark.asyncio
async def test_recent_api_call_renews_expired_token_session(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    db = _Database()
    db.activity["alice"] = auth_middleware.time.time() - 30
    token = _expired_token()

    username = await auth_middleware.get_current_username(_Request(token), db)

    assert username == "alice"
    assert db.activity["alice"] > auth_middleware.time.time() - 2


@pytest.mark.asyncio
async def test_expired_token_fails_after_idle_timeout(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    db = _Database()
    db.activity["alice"] = auth_middleware.time.time() - 61
    token = _expired_token()

    with pytest.raises(HTTPException) as exc_info:
        await auth_middleware.get_current_username(_Request(token), db)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_unexpired_token_fails_after_idle_timeout(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    db = _Database()
    db.activity["alice"] = auth_middleware.time.time() - 61
    token = _active_token()

    with pytest.raises(HTTPException) as exc_info:
        await auth_middleware.get_current_username(_Request(token), db)

    assert exc_info.value.status_code == 401


def test_verify_token_sso_rejects_after_idle_timeout(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(1)
    )
    db = _Database()
    db.activity["alice"] = auth_middleware.time.time() - 61
    token = _active_token()

    assert auth_middleware.verify_token_sso(token, db) is None


@pytest.mark.asyncio
async def test_zero_idle_timeout_allows_expired_token_without_cache(monkeypatch):
    monkeypatch.setattr(
        auth_middleware, "get_agent_config", lambda: _config(0)
    )
    db = _Database()
    token = _expired_token()

    username = await auth_middleware.get_current_username(_Request(token), db)

    assert username == "alice"
    assert "alice" in db.activity


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
        activity = {}

        def increment_user_token_version(self, username):
            assert username == "alice"
            self.token_version += 1
            return self.token_version

        def get_user_activity_time(self, username):
            return self.activity.get(username)

        def clear_user_activity(self, username):
            self.activity.pop(username, None)

    database = _LogoutDatabase()
    token = create_access_token(
        data={"sub": "alice", "v": 1}, never_expires=True
    )
    auth_api._login_time_cache["alice"] = auth_api.datetime.now()
    auth_middleware.touch_user_activity(database, "alice", auth_middleware.time.time())

    await auth_api.logout("alice", _Request(token), database)

    assert database.token_version == 2
    assert "alice" not in auth_api._login_time_cache
    assert "alice" not in database.activity


@pytest.mark.asyncio
async def test_logout_logs_first_login_and_last_activity(caplog):
    class _LogoutDatabase:
        activity = {}

        def increment_user_token_version(self, username):
            return 2

        def touch_user_activity(self, username, timestamp):
            self.activity[username] = timestamp

        def get_user_activity_time(self, username):
            return self.activity.get(username)

        def clear_user_activity(self, username):
            self.activity.pop(username, None)

    token = create_access_token(
        data={"sub": "alice", "v": 1}, never_expires=True
    )
    first_login = datetime(2026, 8, 20, 15, 13, 21)
    last_activity = datetime(2026, 8, 20, 15, 32, 10)
    auth_api._login_time_cache["alice"] = first_login
    database = _LogoutDatabase()
    auth_middleware.touch_user_activity(database, "alice", last_activity.timestamp())
    caplog.set_level(logging.INFO, logger="easy_agent.api.auth")

    await auth_api.logout("alice", _Request(token), database)

    assert "[用户] 登出" in caplog.text
    assert "第一次登录时间: 2026-08-20 15:13:21" in caplog.text
    assert "上一次缓存更新时间: 2026-08-20 15:32:10" in caplog.text