"""认证接口测试：注册、登录、资料、改密、配置。"""

from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from easy_agent.api import auth as auth_mod
from easy_agent.app import app
from easy_agent.config import Config
from easy_agent.models.api import LoginRequest, ResetPasswordRequest


def _register(client, username, password="secret123", org="org-1"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "organization_id": org},
    )


def test_register_success(client):
    resp = _register(client, "alice")
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    assert data["token_type"] == "bearer"
    assert data["username"] == "alice"


def test_register_is_rejected_when_personnel_policy_disables_it(client, db, monkeypatch):
    config = SimpleNamespace(
        personnel=SimpleNamespace(self_registration_enabled=False)
    )
    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {"config": config})

    response = _register(client, "blocked.user")

    assert response.status_code == 403
    assert "关闭自助注册" in response.json()["detail"]
    assert db.get_user_by_username("blocked.user") is None


def test_token_lifetime_zero_idle_logout(monkeypatch):
    """idle_logout_minutes=0（不登出、一直登录）时签发无过期 token。"""

    class _Agent:
        idle_logout_minutes = 0

    class _Cfg:
        agent = _Agent()

    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {"config": _Cfg()})
    assert auth_mod._token_never_expires() is True


def test_token_lifetime_default_when_idle_enabled(monkeypatch):
    """idle_logout_minutes>0 时保持默认 30 分钟 token 有效期。"""

    class _Agent:
        idle_logout_minutes = 5

    class _Cfg:
        agent = _Agent()

    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {"config": _Cfg()})
    assert auth_mod._get_token_lifetime() == timedelta(minutes=30)


def test_register_missing_fields(client):
    # 缺少必填 organization_id
    resp = client.post("/api/auth/register", json={"username": "bob", "password": "secret123"})
    assert resp.status_code == 422


def test_register_duplicate(client):
    assert _register(client, "carol").status_code == 200
    # 用户名已存在返回 400
    assert _register(client, "carol").status_code == 400


def test_login_wrong_password(client):
    _register(client, "dave")
    resp = client.post("/api/auth/login", json={"username": "dave", "password": "wrong"})
    assert resp.status_code == 401


def test_login_success_and_profile(auth_client):
    client = auth_client
    _register(client, "erin")
    login = client.post(
        "/api/auth/login", json={"username": "erin", "password": "secret123"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    prof = client.get("/api/auth/profile", headers={"Authorization": f"Bearer {token}"})
    assert prof.status_code == 200
    assert prof.json()["username"] == "erin"


def test_update_profile(client):
    _register(client, "testuser")
    resp = client.put(
        "/api/auth/profile",
        json={"nickname": "测试昵称", "email": "new@x.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "new@x.com"


async def test_admin_can_list_users(db):
    db.register_user("admin", "admin")
    db.register_user("alice", "secret123")

    users = await auth_mod.list_users(db, "admin")

    assert users.total == 2
    assert {user.username for user in users.users} == {"admin", "alice"}


async def test_admin_can_reset_user_password_to_default(db):
    db.register_user("admin", "admin")
    db.register_user("alice", "secret123")

    result = await auth_mod.admin_reset_password("alice", "admin", db)

    assert result["status"] == "success"
    assert db.verify_user_password("alice", "123456") is not None


async def test_admin_default_password_reset_rejects_admin_target(db):
    admin = db.get_user_by_username("admin")
    original_hash = admin.password_hash

    with pytest.raises(HTTPException) as error:
        await auth_mod.admin_reset_password("admin", "admin", db)

    assert error.value.status_code == 400
    assert db.get_user_by_username("admin").password_hash == original_hash


async def test_admin_reset_password_allows_login_with_default(db, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {})
    db.register_user("alice", "secret123")
    await auth_mod.admin_reset_password("alice", "admin", db)

    response = await auth_mod.login(
        LoginRequest(username="alice", password="123456"),
        SimpleNamespace(headers={}, client=None),
        db,
    )

    assert response.username == "alice"


async def test_non_admin_cannot_list_users_or_reset_password(db):
    db.register_user("alice", "secret123")

    with pytest.raises(HTTPException) as list_error:
        await auth_mod.list_users(db, "alice")
    with pytest.raises(HTTPException) as reset_error:
        await auth_mod.admin_reset_password("alice", "alice", db)

    assert list_error.value.status_code == 403
    assert reset_error.value.status_code == 403


async def test_legacy_reset_password_requires_admin(db):
    request = ResetPasswordRequest(username="alice", new_password="new12345")

    with pytest.raises(HTTPException) as error:
        await auth_mod.reset_password(request, "alice", db)

    assert error.value.status_code == 403


def test_login_request_allows_default_reset_password():
    request = LoginRequest(username="alice", password="123456")

    assert request.password == "123456"


async def test_reset_password_missing_user(db):
    request = ResetPasswordRequest(username="nobody", new_password="new12345")

    with pytest.raises(HTTPException) as error:
        await auth_mod.reset_password(request, "admin", db)

    assert error.value.status_code == 404


def test_auth_config(client):
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "max_input_tokens" in data
    assert "preset_questions" in data


async def test_auth_config_returns_configured_welcome_title(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test
models:
  test:
    provider: test
    api_key: test-key
    model: test-model
    api_base: https://example.invalid
app_welcome_title: "自定义欢迎语"
""",
        encoding="utf-8",
    )
    config = Config.from_yaml(config_path)
    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {"config": config})

    result = await auth_mod.get_auth_config("testuser")

    assert result["app_welcome_title"] == "自定义欢迎语"
