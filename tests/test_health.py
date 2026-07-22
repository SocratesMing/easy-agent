"""应用级接口测试：健康检查与配置查询。"""

from easy_agent.app import app
from fastapi.testclient import TestClient


def test_health(client: TestClient):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "database_initialized" in data


def test_config_endpoint(client: TestClient):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-v4-flash"
    assert "system_prompt" in data


def test_app_metadata(client: TestClient):
    routes = [getattr(r, "path", "") for r in app.routes]
    assert "/api/health" in routes
    assert "/api/auth/login" in routes
    assert "/api/sessions" in routes
    assert "/api/chat/stream" in routes
