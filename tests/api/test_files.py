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


def test_upload(client):
    up = _upload(client)
    assert up["filename"] == "data.txt"
    assert up["size"] == len(b"hello content")


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


def test_workspace_tree_hides_internal_files(tmp_path):
    """记忆锁 ``.memory.jsonl.lock``、记忆 sidecar ``memory.jsonl`` 等框架产物不列给前端
    （文件树看不到）；``memory.md`` 本身照常列出。"""
    from easy_agent.api.files import build_workspace_tree

    ws = tmp_path / "alice"
    ws.mkdir()
    (ws / ".memory.jsonl.lock").write_text("", encoding="utf-8")
    (ws / "AGENTS.jsonl.lock").write_text("", encoding="utf-8")
    (ws / ".gitignore").write_text("x", encoding="utf-8")
    (ws / "memory.jsonl").write_text('{"type": "meta"}\n', encoding="utf-8")
    (ws / "memory.md").write_text("# 会话记忆\n", encoding="utf-8")
    (ws / "report.md").write_text("hi", encoding="utf-8")

    assert [item["name"] for item in build_workspace_tree(ws)] == ["memory.md", "report.md"]


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

    from easy_agent.db import get_database

    rows = get_database().get_session_files(sess)
    assert rows and rows[0]["filename"] == "data.txt"


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


def test_download_accepts_the_stored_upload_path(tmp_path, monkeypatch):
    """上传接口返回的落库路径相对服务进程目录（生产配置下上传目录是相对路径），也要能下载。"""
    from easy_agent.api.files import _resolve_download_file

    monkeypatch.chdir(tmp_path)
    upload_dir, workspace_dir = Path("data/uploads/alice"), Path("workspace/alice")
    (tmp_path / upload_dir).mkdir(parents=True)
    (tmp_path / workspace_dir).mkdir(parents=True)
    (tmp_path / upload_dir / "abc_report.txt").write_text("mine", encoding="utf-8")
    (tmp_path / "data/uploads/bob").mkdir(parents=True)
    (tmp_path / "data/uploads/bob/secret.txt").write_text("bob", encoding="utf-8")

    found = _resolve_download_file(upload_dir, workspace_dir, "data/uploads/alice/abc_report.txt")
    assert found.read_text(encoding="utf-8") == "mine"

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as other_user:
        _resolve_download_file(upload_dir, workspace_dir, "data/uploads/bob/secret.txt")
    assert other_user.value.status_code == 404

# ── SEC-06：/agent/files/parse 只解析 store URI 或调用者目录内文件 ──────


def test_parse_rejects_paths_outside_user_dirs(client, tmp_path):
    """SEC-06：``/etc/hostname`` 等调用者目录外的绝对路径 → 403「无权访问该文件」。"""
    secret = tmp_path / "sec06_outside_secret.txt"
    secret.write_text("sec06-outside-secret", encoding="utf-8")
    for path in ("/etc/hostname", str(secret)):
        resp = client.post("/agent/files/parse", params={"file_path": path})
        assert resp.status_code == 403, path
        assert resp.json()["detail"] == "无权访问该文件"
        assert "sec06-outside-secret" not in resp.text


def test_parse_rejects_other_users_upload(client):
    """SEC-06：其他用户上传目录内的文件 → 403，且不回显内容。"""
    other = Path(os.environ["TEST_UPLOADS_DIR"]) / "sec06_bob" / "secret.txt"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("sec06-bob-secret", encoding="utf-8")
    try:
        resp = client.post("/agent/files/parse", params={"file_path": str(other)})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "无权访问该文件"
        assert "sec06-bob-secret" not in resp.text
    finally:
        other.unlink()
        other.parent.rmdir()


def test_parse_rejects_path_traversal(client):
    """SEC-06：``..`` 穿越 → 400「非法的文件路径」。"""
    for path in ("../escape.txt", "sub/../../escape.txt"):
        resp = client.post("/agent/files/parse", params={"file_path": path})
        assert resp.status_code == 400, path
        assert resp.json()["detail"] == "非法的文件路径"


def test_parse_rejects_other_users_store_uri(client):
    """SEC-06：跨用户 store:// URI → 403（materialize_upload 归属校验）。"""
    resp = client.post(
        "/agent/files/parse", params={"file_path": "store://uploads/sec06_bob/x.txt"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "UPLOAD_STORE_ACCESS_DENIED"


def test_parse_accepts_workspace_relative_file(client):
    """SEC-06：调用者工作区内的相对路径仍可解析。"""
    ws_file = (
        Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "testuser" / "parse-me.txt"
    )
    ws_file.parent.mkdir(parents=True, exist_ok=True)
    ws_file.write_text("workspace parse content", encoding="utf-8")

    resp = client.post("/agent/files/parse", params={"file_path": "parse-me.txt"})
    assert resp.status_code == 200
    assert "workspace parse content" in resp.json()["content"]


# ── SEC-07：/agent/files/preview 未认证不再回退默认用户 ─────────────────


def test_preview_requires_auth(client):
    """SEC-07：既无 Authorization 也无 token 查询参数 → 401。"""
    resp = client.get("/agent/files/preview", params={"file_path": "preview.md"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "未认证，请先登录"


def test_preview_rejects_invalid_token(client):
    """SEC-07：无效 token（查询参数或 Header）→ 401，不回退默认用户。"""
    for params, headers in (
        ({"file_path": "preview.md", "token": "not-a-valid-token"}, {}),
        ({"file_path": "preview.md"}, {"Authorization": "Bearer not-a-valid-token"}),
    ):
        resp = client.get("/agent/files/preview", params=params, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "未认证，请先登录"


def test_preview_accepts_authorization_header(client):
    """SEC-07：有效 Bearer Header 仍可预览。"""
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "password": "secret123",
            "organization_id": "test-org",
            "employee_id": "E-preview-header-testuser",
        },
    )
    assert registered.status_code == 200
    token = registered.json()["access_token"]

    ws_file = (
        Path(os.environ["TEST_WORKSPACE_DIR"])
        / "users"
        / "testuser"
        / "header-preview.md"
    )
    ws_file.parent.mkdir(parents=True, exist_ok=True)
    ws_file.write_text("header preview content", encoding="utf-8")

    resp = client.get(
        "/agent/files/preview",
        params={"file_path": "header-preview.md"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.content == b"header preview content"

