"""认证接口测试：注册、登录、资料、改密、配置。"""

from datetime import timedelta

from easy_agent.api import auth as auth_mod
from easy_agent.app import app


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


def test_token_lifetime_zero_idle_logout(monkeypatch):
    """idle_logout_minutes=0（不登出、一直登录）时签发超长有效期 token。"""

    class _Agent:
        idle_logout_minutes = 0

    class _Cfg:
        agent = _Agent()

    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {"config": _Cfg()})
    assert auth_mod._get_token_lifetime() == timedelta(days=365)


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


def test_reset_password_success(client):
    _register(client, "testuser")
    resp = client.post(
        "/api/auth/reset-password",
        json={"username": "testuser", "new_password": "new12345"},
    )
    assert resp.status_code == 200
    login = client.post(
        "/api/auth/login", json={"username": "testuser", "password": "new12345"}
    )
    assert login.status_code == 200


def test_reset_password_missing_user(client):
    resp = client.post(
        "/api/auth/reset-password",
        json={"username": "nobody", "new_password": "new12345"},
    )
    assert resp.status_code == 404


def test_auth_config(client):
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "max_input_tokens" in data
    assert "preset_questions" in data
