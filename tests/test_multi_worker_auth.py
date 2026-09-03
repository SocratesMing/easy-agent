"""多 worker 部署下登录态跨进程共享的回归测试。

背景：后端以 uvicorn --workers > 1 启动时，登录请求可能落在 worker A，
后续请求落在 worker B。若登录态/活跃时间只存在进程内存中，worker B 会
把活跃用户判为「空闲过期」返回 401，导致用户登录后立刻被登出。

修复方向：把 idle-logout 的活跃时间戳持久化到共享数据库，任意 worker 都能读到。
本测试用「两个独立 Database 实例指向同一 SQLite 文件」模拟两个 worker 进程。
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from easy_agent.db import Database, init_database
from easy_agent.middleware import auth as auth_middleware
from easy_agent.utils import auth as auth_utils
from easy_agent.utils.auth import create_access_token


class _Request:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}"}


class _AgentConfig:
    def __init__(self, idle_logout_minutes):
        self.idle_logout_minutes = idle_logout_minutes


def _config(minutes):
    return {"config": SimpleNamespace(agent=_AgentConfig(minutes))}


@pytest.fixture()
def two_workers(tmp_path):
    """(db_a, db_b)：两个独立 Database 实例共享同一 SQLite 文件，模拟两个 worker。"""
    path = tmp_path / "shared.db"
    db_a = init_database({"type": "sqlite", "sqlite": {"path": str(path)}})
    db_b = Database({"type": "sqlite", "sqlite": {"path": str(path)}})
    return db_a, db_b


@pytest.fixture(autouse=True)
def _clear_throttle():
    auth_middleware._activity_write_throttle.clear()
    yield
    auth_middleware._activity_write_throttle.clear()


def _register_and_login(db, username="alice"):
    db.register_user(username=username, password="secret")
    version = db.increment_user_token_version(username)
    return create_access_token(
        data={"sub": username, "v": version},
        expires_delta=timedelta(minutes=30),
    )


@pytest.mark.asyncio
async def test_login_activity_visible_to_other_worker(two_workers, monkeypatch):
    """worker A 登录并 touch 活跃时间后，worker B 应能读到活跃状态，不能立刻踢下线。"""
    db_a, db_b = two_workers
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: _config(5))
    token = _register_and_login(db_a)

    auth_middleware.touch_user_activity(db_a, "alice")
    auth_middleware._activity_write_throttle.clear()

    username = await auth_middleware.get_current_username(_Request(token), db_b)

    assert username == "alice"


@pytest.mark.asyncio
async def test_idle_timeout_still_enforced_across_workers(two_workers, monkeypatch):
    """空闲超时在跨 worker 后依然生效：共享 DB 里活跃时间超过阈值即返回 401。"""
    db_a, db_b = two_workers
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: _config(1))
    token = _register_and_login(db_a)

    db_a.clear_user_activity("alice")
    db_a.touch_user_activity("alice", auth_middleware.time.time() - 61)
    auth_middleware._activity_write_throttle.clear()

    with pytest.raises(HTTPException) as exc_info:
        await auth_middleware.get_current_username(_Request(token), db_b)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_active_user_on_other_worker_passes_idle_check(two_workers, monkeypatch):
    """worker A 刚 touch（60 秒内），worker B 校验同一用户应通过。"""
    db_a, db_b = two_workers
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: _config(1))
    token = _register_and_login(db_a)

    db_a.touch_user_activity("alice", auth_middleware.time.time())
    auth_middleware._activity_write_throttle.clear()

    username = await auth_middleware.get_current_username(_Request(token), db_b)

    assert username == "alice"


def test_jwt_secret_shared_across_workers(tmp_path, monkeypatch):
    """未设置 EASY_JWT_SECRET 时，密钥应持久化到文件，多 worker 读到同一密钥。"""
    monkeypatch.delenv("EASY_JWT_SECRET", raising=False)
    secret_file = tmp_path / ".jwt_secret"

    secret_a = auth_utils._load_or_create_secret(secret_file)
    secret_b = auth_utils._load_or_create_secret(secret_file)

    assert secret_a == secret_b
    assert secret_file.exists()