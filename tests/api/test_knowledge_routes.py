"""knowledge 路由接线测试：路由生效且中间件为 additive。"""


def test_knowledge_status_route_registered(client):
    # 路由存在：未认证应为 401/403，而不是 SPA catch-all 的 HTML 200
    resp = client.get("/agent/knowledge/v1/status")
    assert resp.status_code in (401, 403)
    assert "text/html" not in resp.headers.get("content-type", "")


def test_request_id_header_is_added(client):
    resp = client.get("/agent/health")
    assert resp.headers.get("X-Request-Id")
