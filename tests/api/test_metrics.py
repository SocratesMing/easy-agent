"""``/agent/metrics`` 鉴权、中间件 RED 记录与排除路径测试（无网络）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from easy_agent.app import app
from easy_agent.middleware import get_current_username
from easy_agent.middleware.metrics import should_record_request
from easy_agent.services.metrics import reset_metrics, snapshot_metrics


@pytest.fixture()
def as_admin(client: TestClient):
    """把当前用户临时切成 admin，结束后恢复 conftest 的 testuser。"""
    original = app.dependency_overrides[get_current_username]
    app.dependency_overrides[get_current_username] = lambda: "admin"
    yield client
    app.dependency_overrides[get_current_username] = original


def test_metrics_endpoint_requires_admin(client: TestClient):
    reset_metrics()

    response = client.get("/agent/metrics")

    assert response.status_code == 403
    assert response.json()["detail"] == "仅管理员可以查看运行指标"


def test_metrics_endpoint_rejects_anonymous(auth_client: TestClient):
    reset_metrics()

    response = auth_client.get("/agent/metrics")

    assert response.status_code == 401


def test_metrics_endpoint_admin_returns_json_snapshot(as_admin: TestClient):
    reset_metrics()

    response = as_admin.get("/agent/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert set(data) >= {
        "generated_at",
        "uptime_seconds",
        "process",
        "http",
        "sse",
        "tools",
        "hitl",
    }
    assert data["http"]["total"] == 0  # 指标端点自身不计数
    assert data["sse"]["active"] == 0
    assert data["hitl"]["waiting"] == 0


def test_middleware_records_business_request(client: TestClient):
    reset_metrics()

    assert client.get("/agent/config").status_code == 200

    http = snapshot_metrics()["http"]
    assert http["total"] == 1
    assert http["status_codes"] == {"200": 1}
    route = next(item for item in http["routes"] if item["route"] == "/agent/config")
    assert route["method"] == "GET"
    assert route["count"] == 1
    assert route["errors"] == 0
    assert route["latency_ms"]["count"] == 1


def test_middleware_records_4xx_as_client_error(client: TestClient):
    reset_metrics()

    assert client.get("/agent/not-a-real-endpoint").status_code == 404

    http = snapshot_metrics()["http"]
    assert http["total"] == 1
    assert http["client_errors"] == 1
    assert http["errors"] == 0
    assert http["error_rate"] == 0.0
    assert http["status_codes"] == {"404": 1}


def test_middleware_skips_probes_docs_and_static(client: TestClient):
    reset_metrics()

    assert client.get("/agent/live").status_code == 200
    assert client.get("/agent/ready").status_code in (200, 503)
    assert client.get("/docs").status_code == 200
    assert client.get("/assets/app.js").status_code in (200, 404)

    assert snapshot_metrics()["http"]["total"] == 0


def test_should_record_request_rules():
    for path in ("/agent/live", "/agent/ready", "/ready", "/agent/metrics", "/docs", "/"):
        assert should_record_request(path) is False, path
    for path in ("/agent/config", "/agent/sessions", "/agent/chat/stream"):
        assert should_record_request(path) is True, path
    assert should_record_request("/static/app.js") is False
    assert should_record_request("/favicon.ico") is False
    assert should_record_request("/agent/config", "OPTIONS") is False
