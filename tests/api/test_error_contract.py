"""统一 API 错误体契约测试（ARCH-08）。"""


def test_http_error_body_has_detail_and_code(client):
    resp = client.get("/agent/sessions/definitely-missing-session")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]
    assert body["code"] == "HTTP_404"


def test_validation_error_body_has_detail_code_and_errors(client):
    resp = client.post(
        "/agent/auth/register", json={"username": "bob", "password": "secret123"}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"] == "请求参数校验失败"
    assert body["code"] == "VALIDATION_ERROR"
    assert isinstance(body["errors"], list) and body["errors"]
