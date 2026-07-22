"""文件管理接口测试：上传、列表、解析、预览、工作区树、下载、删除。"""

import os
from pathlib import Path

from fastapi.testclient import TestClient


def _upload(client: TestClient, session_id=None):
    files = {"file": ("data.txt", b"hello content", "text/plain")}
    params = {"session_id": session_id} if session_id else {}
    resp = client.post("/api/files/upload", files=files, params=params)
    assert resp.status_code == 200
    return resp.json()


def test_upload_and_list(client):
    up = _upload(client)
    assert up["filename"] == "data.txt"

    lst = client.get("/api/files/list")
    assert lst.status_code == 200
    body = lst.json()
    assert body["total"] >= 1


def test_parse_file(client):
    up = _upload(client)
    resp = client.post("/api/files/parse", params={"file_path": up["file_path"]})
    assert resp.status_code == 200
    assert "hello content" in resp.json()["content"]


def test_download_file(client):
    up = _upload(client)
    file_path = up["file_path"]
    resp = client.get(f"/api/files/download/{file_path}")
    assert resp.status_code == 200
    assert resp.content == b"hello content"


def test_delete_missing_file_404(client):
    resp = client.delete("/api/files/999999")
    assert resp.status_code == 404


def test_workspace_tree(client):
    resp = client.get("/api/files/workspace/tree")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_preview_workspace_file(client):
    from easy_agent.utils.auth import create_access_token

    token = create_access_token(data={"sub": "testuser"})
    ws_file = (
        Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "testuser" / "preview.md"
    )
    ws_file.parent.mkdir(parents=True, exist_ok=True)
    ws_file.write_text("preview content")
    resp = client.get(
        "/api/files/preview",
        params={"file_path": "preview.md", "token": token},
    )
    assert resp.status_code == 200
    assert resp.content == b"preview content"


def test_session_files_list(client):
    sess = client.post("/api/sessions", json={}).json()["session_id"]
    files = {"file": ("data.txt", b"hello", "text/plain")}
    up = client.post(f"/api/sessions/{sess}/upload", files=files)
    assert up.status_code == 200

    # 上传的会话文件通过 /api/files/list?session_id 查看（get_session_files）
    resp = client.get("/api/files/list", params={"session_id": sess})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_session_generated_files(client):
    # /api/files/session/{id} 返回智能体生成的文件（generated_files）
    from easy_agent.db import get_database

    sess = client.post("/api/sessions", json={}).json()["session_id"]
    db = get_database()
    db.add_generated_file(
        session_id=sess,
        message_id="m1",
        filename="result.txt",
        file_path="/tmp/result.txt",
        file_type="txt",
        size=10,
    )
    resp = client.get(f"/api/files/session/{sess}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert any(f["filename"] == "result.txt" for f in resp.json())
