"""免密登录接口测试：用户名 + 用户ID 直登，不存在则自动注册。"""

import os
from pathlib import Path

from easy_agent.api import auth as auth_mod
from easy_agent.config import Config
from easy_agent.middleware import auth as auth_middleware


def _passwordless_login(client, username, user_id=None):
    payload = {"username": username}
    if user_id is not None:
        payload["user_id"] = user_id
    return client.post("/agent/auth/login-passwordless", json=payload)


def _get_profile(auth_client, token):
    resp = auth_client.get(
        "/agent/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    return resp.json()


def test_passwordless_login_registers_new_user_with_given_user_id(auth_client):
    resp = _passwordless_login(auth_client, "pluser1", "ext-123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "pluser1"
    assert data["access_token"]

    profile = _get_profile(auth_client, data["access_token"])
    assert profile["username"] == "pluser1"
    assert profile["user_id"] == "ext-123"


def test_passwordless_login_existing_user_directly(auth_client):
    reg = auth_client.post(
        "/agent/auth/register",
        json={"username": "plbob", "password": "secret123", "organization_id": "org-1"},
    )
    assert reg.status_code == 200

    resp = _passwordless_login(auth_client, "plbob", "0")
    assert resp.status_code == 200
    assert resp.json()["username"] == "plbob"


def test_passwordless_login_generates_unique_user_id_when_missing(auth_client):
    resp = _passwordless_login(auth_client, "pluser2")
    assert resp.status_code == 200
    profile = _get_profile(auth_client, resp.json()["access_token"])
    assert profile["user_id"] not in ("", "0")

    resp_zero = _passwordless_login(auth_client, "pluser3", "0")
    assert resp_zero.status_code == 200
    profile_zero = _get_profile(auth_client, resp_zero.json()["access_token"])
    assert profile_zero["user_id"] not in ("", "0")


def test_passwordless_login_existing_user_keeps_original_user_id(auth_client):
    first = _passwordless_login(auth_client, "pluser4", "ext-orig")
    assert first.status_code == 200
    assert _get_profile(auth_client, first.json()["access_token"])["user_id"] == "ext-orig"

    second = _passwordless_login(auth_client, "pluser4", "ext-other")
    assert second.status_code == 200
    assert _get_profile(auth_client, second.json()["access_token"])["user_id"] == "ext-orig"


def test_passwordless_login_rejects_user_id_taken_by_other_user(auth_client):
    first = _passwordless_login(auth_client, "pluserA", "dup-1")
    assert first.status_code == 200

    second = _passwordless_login(auth_client, "pluserB", "dup-1")
    assert second.status_code == 400


def test_passwordless_login_rejects_admin(auth_client):
    resp = _passwordless_login(auth_client, "admin", "0")
    assert resp.status_code == 403


def test_passwordless_login_creates_workspace_dirs(client):
    resp = _passwordless_login(client, "pluser-ws", "ext-ws")
    assert resp.status_code == 200

    workspace = Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "pluser-ws"
    assert workspace.is_dir()


def test_passwordless_login_validates_username(client):
    resp = client.post("/agent/auth/login-passwordless", json={"username": "a"})
    assert resp.status_code == 422


def test_db_passwordless_user_creation_semantics(db):
    user, created = db.get_or_create_passwordless_user("dbuser1", "ext-1")
    assert created is True
    assert user.user_id == "ext-1"

    same, created_again = db.get_or_create_passwordless_user("dbuser1", "whatever")
    assert created_again is False
    assert same.user_id == "ext-1"

    generated, created_gen = db.get_or_create_passwordless_user("dbuser2")
    assert created_gen is True
    assert generated.user_id and generated.user_id != "0"

    conflict, created_conflict = db.get_or_create_passwordless_user("dbuser3", "ext-1")
    assert conflict is None
    assert created_conflict is False


def test_models_endpoint_is_public(auth_client):
    resp = auth_client.get("/agent/settings/models")
    assert resp.status_code == 200
    assert "models" in resp.json()


def test_idle_logout_minutes_default_is_disabled(tmp_path):
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
""",
        encoding="utf-8",
    )
    config = Config.from_yaml(config_path)
    assert config.agent.idle_logout_minutes == 0


def test_middleware_idle_logout_fallback_is_zero(monkeypatch):
    monkeypatch.setattr(auth_middleware, "get_agent_config", lambda: {})
    assert auth_middleware._get_idle_logout_minutes() == 0


async def test_auth_config_fallback_reports_no_timeout(monkeypatch):
    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {})
    result = await auth_mod.get_auth_config("testuser")
    assert result["idle_logout_minutes"] == 0
