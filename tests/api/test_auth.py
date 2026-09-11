"""认证接口测试：注册、登录、资料、改密、配置。"""

from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from easy_agent.api import auth as auth_mod
from easy_agent.app import app
from easy_agent.config import Config
from easy_agent.models.api import LoginRequest, ResetPasswordRequest


def _register(client, username, password="secret123", employee_id=None):
    # 工号是必填且全局唯一：默认按用户名派生一个，需要专门测试工号时再显式传入。
    # 机构ID 与邮箱已不是注册项。
    return client.post(
        "/agent/auth/register",
        json={
            "username": username,
            "password": password,
            "employee_id": employee_id if employee_id is not None else f"E-{username}",
        },
    )


def test_register_success(client):
    resp = _register(client, "alice")
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    assert data["token_type"] == "bearer"
    assert data["username"] == "alice"


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
    # 缺少必填 employee_id
    resp = client.post("/agent/auth/register", json={"username": "bob", "password": "secret123"})
    assert resp.status_code == 422


def test_register_duplicate(client):
    assert _register(client, "carol").status_code == 200
    # 用户名已存在返回 400
    assert _register(client, "carol").status_code == 400


def test_login_wrong_password(client):
    _register(client, "dave")
    resp = client.post("/agent/auth/login", json={"username": "dave", "password": "wrong"})
    assert resp.status_code == 401


def test_login_success_and_profile(auth_client):
    client = auth_client
    _register(client, "erin")
    login = client.post(
        "/agent/auth/login", json={"username": "erin", "password": "secret123"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    prof = client.get("/agent/auth/profile", headers={"Authorization": f"Bearer {token}"})
    assert prof.status_code == 200
    assert prof.json()["username"] == "erin"


def test_update_profile(client):
    _register(client, "testuser")
    resp = client.put(
        "/agent/auth/profile",
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
    resp = client.get("/agent/auth/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "context_length" in data
    assert "preset_questions" in data


def test_register_requires_employee_id(client):
    """工号是必填字段，缺失时 422。"""
    resp = client.post(
        "/agent/auth/register",
        json={"username": "bob", "password": "secret123"},
    )
    assert resp.status_code == 422


def test_register_ignores_legacy_org_and_email(auth_client):
    """注册不再要求机构ID与邮箱；旧客户端若仍传这两个字段会被忽略，注册后为空。"""
    client = auth_client
    resp = client.post(
        "/agent/auth/register",
        json={
            "username": "kevin",
            "password": "secret123",
            "employee_id": "E4004",
            "organization_id": "org-legacy",
            "email": "legacy@x.com",
        },
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    prof = client.get(
        "/agent/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert prof.status_code == 200
    assert prof.json()["organization_id"] == ""
    assert prof.json()["email"] == ""


def test_register_duplicate_employee_id(client):
    """工号全局唯一：换个用户名也不能复用同一个工号。"""
    assert _register(client, "frank", employee_id="E1001").status_code == 200
    resp = _register(client, "grace", employee_id="E1001")
    assert resp.status_code == 400
    assert "工号" in resp.json()["detail"]


def test_login_with_employee_id(client):
    """登录标识支持工号，与用户名等效。"""
    assert _register(client, "henry", employee_id="E2002").status_code == 200

    by_employee = client.post(
        "/agent/auth/login", json={"username": "E2002", "password": "secret123"}
    )
    assert by_employee.status_code == 200
    assert by_employee.json()["username"] == "henry"

    by_username = client.post(
        "/agent/auth/login", json={"username": "henry", "password": "secret123"}
    )
    assert by_username.status_code == 200
    assert by_username.json()["username"] == "henry"


def test_login_wrong_password_with_employee_id(client):
    """用工号登录同样要校验密码。"""
    _register(client, "judy", employee_id="E2003")
    resp = client.post(
        "/agent/auth/login", json={"username": "E2003", "password": "wrong"}
    )
    assert resp.status_code == 401


def test_profile_returns_employee_id(auth_client):
    """个人资料返回工号。"""
    client = auth_client
    _register(client, "ivan", employee_id="E3003")
    login = client.post(
        "/agent/auth/login", json={"username": "E3003", "password": "secret123"}
    )
    token = login.json()["access_token"]

    prof = client.get(
        "/agent/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert prof.status_code == 200
    assert prof.json()["employee_id"] == "E3003"


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
