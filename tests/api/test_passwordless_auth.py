"""免密登录接口测试：用户名 + 用户ID 直登，不存在则自动注册。"""

import os
from pathlib import Path
import pytest

from easy_agent.api import auth as auth_mod
from easy_agent.bootstrap.config import Config
from easy_agent.db import login_state


@pytest.fixture(autouse=True)
def trusted_portal_policy(client, monkeypatch):
    # This suite exercises an explicitly enabled trusted portal deployment.
    policy = auth_mod.get_agent_config()["config"].personnel
    monkeypatch.setattr(policy, "passwordless_login_enabled", True)


def _passwordless_login(client, username, user_id=None, **profile):
    payload = {"username": username, **profile}
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
        json={
            "username": "plbob",
            "password": "secret123",
            "organization_id": "org-1",
            "employee_id": "E-plbob",
        },
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


def test_passwordless_login_allows_super_admin_liufengbao(auth_client):
    """免密门户部署的总管理员：liufengbao 允许免密直登。"""
    resp = _passwordless_login(auth_client, "liufengbao", "0")
    assert resp.status_code == 200
    assert resp.json()["username"] == "liufengbao"


def test_passwordless_login_creates_workspace_dirs(client):
    resp = _passwordless_login(client, "pluser-ws", "ext-ws")
    assert resp.status_code == 200

    workspace = Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "pluser-ws"
    assert workspace.is_dir()


def test_passwordless_login_creates_user_with_personnel_fields(auth_client, db):
    resp = _passwordless_login(
        auth_client,
        "plprofile1",
        "ext-profile-1",
        display_name="张三",
        employee_id="E-1001",
        department_id="D-01",
        department_name="研发部",
        division_name="平台处",
        team_name="AI组",
        email="zhangsan@example.com",
        position="工程师",
        mobile="13800000000",
    )
    assert resp.status_code == 200

    user = db.get_user_by_username("plprofile1")
    assert user.display_name == "张三"
    assert user.employee_id == "E-1001"
    assert user.department_id == "D-01"
    assert user.department_name == "研发部"
    assert user.division_name == "平台处"
    assert user.team_name == "AI组"
    assert user.email == "zhangsan@example.com"
    assert user.position == "工程师"
    assert user.mobile == "13800000000"
    # 知识库鉴权读 organization_id，必须与部门编号同步
    assert user.organization_id == "D-01"
    assert user.personnel_source == "portal"


def test_passwordless_login_department_id_falls_back_to_name(auth_client, db):
    resp = _passwordless_login(
        auth_client, "plprofile2", department_name="运营部"
    )
    assert resp.status_code == 200

    user = db.get_user_by_username("plprofile2")
    assert user.department_id == "运营部"
    assert user.department_name == "运营部"
    assert user.organization_id == "运营部"


def test_passwordless_login_only_fills_empty_personnel_fields(auth_client, db):
    first = _passwordless_login(
        auth_client,
        "plprofile3",
        department_id="D-02",
        department_name="研发部",
    )
    assert first.status_code == 200

    second = _passwordless_login(
        auth_client,
        "plprofile3",
        display_name="李四",
        division_name="数据处",
        team_name="报表组",
        employee_id="E-1002",
        email="lisi@example.com",
        position="分析师",
        mobile="13900000000",
        department_id="D-99",
        department_name="市场部",
    )
    assert second.status_code == 200

    user = db.get_user_by_username("plprofile3")
    # 已有部门不被覆盖
    assert user.department_id == "D-02"
    assert user.department_name == "研发部"
    assert user.organization_id == "D-02"
    # 空字段被补全
    assert user.display_name == "李四"
    assert user.division_name == "数据处"
    assert user.team_name == "报表组"
    assert user.employee_id == "E-1002"
    assert user.email == "lisi@example.com"
    assert user.position == "分析师"
    assert user.mobile == "13900000000"


def test_passwordless_login_skips_employee_id_taken_by_other_user(auth_client, db):
    first = _passwordless_login(
        auth_client, "plprofile4", employee_id="E-2001"
    )
    assert first.status_code == 200

    second = _passwordless_login(
        auth_client, "plprofile5", employee_id="E-2001"
    )
    assert second.status_code == 200
    assert db.get_user_by_username("plprofile5").employee_id == ""


def test_passwordless_login_rejects_admin_department(client):
    resp = client.post(
        "/agent/auth/login-passwordless",
        json={"username": "plbaddept", "department_id": "admin"},
    )
    assert resp.status_code == 422


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
    monkeypatch.setattr(login_state, "get_runtime_agent_config", lambda: {})
    assert login_state._get_idle_logout_minutes() == 0


async def test_auth_config_fallback_reports_no_timeout(monkeypatch):
    monkeypatch.setattr(auth_mod, "get_agent_config", lambda: {})
    result = await auth_mod.get_auth_config("testuser")
    assert result["idle_logout_minutes"] == 0
