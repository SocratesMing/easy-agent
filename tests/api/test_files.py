"""文件管理接口测试：上传、列表、解析、预览、工作区树、下载、删除。"""

import os
from pathlib import Path

from fastapi.testclient import TestClient


def _upload(client: TestClient, session_id=None):
    files = {"file": ("data.txt", b"hello content", "text/plain")}
    params = {"session_id": session_id} if session_id else {}
    resp = client.post("/agent/files/upload", files=files, params=params)
    assert resp.status_code == 200
    return resp.json()


def test_upload_and_list(client):
    up = _upload(client)
    assert up["filename"] == "data.txt"

    lst = client.get("/agent/files/list")
    assert lst.status_code == 200
    body = lst.json()
    assert body["total"] >= 1


def test_parse_file(client):
    up = _upload(client)
    resp = client.post("/agent/files/parse", params={"file_path": up["file_path"]})
    assert resp.status_code == 200
    assert "hello content" in resp.json()["content"]


def test_download_file(client):
    up = _upload(client)
    file_path = up["file_path"]
    resp = client.get(f"/agent/files/download/{file_path}")
    assert resp.status_code == 200
    assert resp.content == b"hello content"


def test_download_by_listed_relative_path(client):
    """前端「资产」面板用列表接口返回的相对路径下载。"""
    up = _upload(client)
    listed = client.get("/agent/files/list").json()
    item = next(f for f in listed["files"] if f["filename"] == Path(up["file_path"]).name)
    resp = client.get(f"/agent/files/download/{item['file_path']}")
    assert resp.status_code == 200
    assert resp.content == b"hello content"


def test_download_rejects_paths_outside_user_dirs(client, tmp_path):
    """SEC-05：绝对路径或 ``..`` 不能逃出本用户的上传目录 / 工作区。"""
    host_secret = tmp_path / "host-secret.txt"
    host_secret.write_text("sec05-host-secret", encoding="utf-8")
    other_user = Path(os.environ["TEST_UPLOADS_DIR"]) / "sec05_other" / "other-secret.txt"
    other_user.parent.mkdir(parents=True, exist_ok=True)
    other_user.write_text("sec05-other-user-secret", encoding="utf-8")
    try:
        for path in (str(host_secret), "%2e%2e/sec05_other/other-secret.txt"):
            resp = client.get(f"/agent/files/download/{path}")
            assert resp.status_code == 400, path
            assert b"sec05" not in resp.content
    finally:
        other_user.unlink()
        other_user.parent.rmdir()


def test_delete_missing_file_404(client):
    resp = client.delete("/agent/files/999999")
    assert resp.status_code == 404


def test_workspace_tree(client):
    resp = client.get("/agent/files/workspace/tree")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_preview_workspace_file(client):
    # 预览接口已接入单点登录 token_version 与活跃会话校验，必须使用真实注册流程
    # 签发的 token；旧式仅含 sub 的手工 token 应当被拒绝。
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "password": "secret123",
            "organization_id": "test-org",
            "employee_id": "E-preview-testuser",
        },
    )
    assert registered.status_code == 200
    token = registered.json()["access_token"]
    ws_file = (
        Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "testuser" / "preview.md"
    )
    ws_file.parent.mkdir(parents=True, exist_ok=True)
    ws_file.write_text("preview content")
    resp = client.get(
        "/agent/files/preview",
        params={"file_path": "preview.md", "token": token},
    )
    assert resp.status_code == 200
    assert resp.content == b"preview content"


def test_session_files_list(client):
    sess = client.post("/agent/sessions", json={}).json()["session_id"]
    files = {"file": ("data.txt", b"hello", "text/plain")}
    up = client.post(f"/agent/sessions/{sess}/upload", files=files)
    assert up.status_code == 200

    # 上传的会话文件通过 /agent/files/list?session_id 查看（get_session_files）
    resp = client.get("/agent/files/list", params={"session_id": sess})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_session_generated_files(client):
    # /agent/files/session/{id} 返回智能体生成的文件（generated_files）
    from easy_agent.db import get_database

    sess = client.post("/agent/sessions", json={}).json()["session_id"]
    db = get_database()
    db.add_generated_file(
        session_id=sess,
        message_id="m1",
        filename="result.txt",
        file_path="/tmp/result.txt",
        file_type="txt",
        size=10,
    )
    resp = client.get(f"/agent/files/session/{sess}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert any(f["filename"] == "result.txt" for f in resp.json())
