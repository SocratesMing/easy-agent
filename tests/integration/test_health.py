"""应用级接口测试：健康检查与配置查询。"""

from easy_agent.app import app
from fastapi.testclient import TestClient


def test_health(client: TestClient):
    resp = client.get("/agent/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "database_initialized" in data


def test_config_endpoint(client: TestClient):
    resp = client.get("/agent/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-v4-flash"
    assert "system_prompt" in data


def test_app_metadata(client: TestClient):
    routes = [getattr(r, "path", "") for r in app.routes]
    assert "/agent/health" in routes
    assert "/agent/auth/login" in routes
    assert "/agent/sessions" in routes
    assert "/agent/chat/stream" in routes


def test_unknown_agent_path_returns_json_404(client: TestClient):
    """未注册的 /agent/* 必须返回 JSON 404，不能落到 SPA 兜底返回 index.html。

    否则前端 response.json() 会报 "Unexpected token '<'"，掩盖接口不存在的真实原因。
    """
    resp = client.get("/agent/not-a-real-endpoint")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "Not Found"}


def test_frontend_fallback_still_serves_html(client: TestClient):
    """非 /agent 路径仍走 SPA 兜底（保持前端路由/静态资源行为不变）。"""
    resp = client.get("/some/spa/route")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")

