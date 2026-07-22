"""会话接口测试：创建、列表、详情、改标题、置顶、消息、历史、删除、文件。"""

import io

from fastapi.testclient import TestClient


def _create_session(client: TestClient, title=None):
    body = {"title": title} if title else {}
    resp = client.post("/api/sessions", json=body)
    assert resp.status_code == 200
    return resp.json()["session_id"]


def test_create_session_default_title(client):
    sid = _create_session(client)
    detail = client.get(f"/api/sessions/{sid}")
    assert detail.status_code == 200
    assert detail.json()["session_id"] == sid


def test_create_session_with_title(client):
    sid = _create_session(client, title="我的会话")
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["title"] == "我的会话"


def test_list_and_count(client):
    _create_session(client)
    _create_session(client)
    lst = client.get("/api/sessions")
    assert lst.status_code == 200
    sessions = lst.json()
    assert isinstance(sessions, list)
    assert len(sessions) >= 2

    cnt = client.get("/api/sessions/count")
    assert cnt.status_code == 200
    assert cnt.json()["total_sessions"] >= 2


def test_get_missing_session_404(client):
    resp = client.get("/api/sessions/does-not-exist")
    assert resp.status_code == 404


def test_update_title(client):
    sid = _create_session(client)
    resp = client.put(f"/api/sessions/{sid}/title", json={"title": "新标题"})
    assert resp.status_code == 200
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "新标题"


def test_pin_session(client):
    sid = _create_session(client)
    before = client.get(f"/api/sessions/{sid}").json().get("pinned", 0)
    resp = client.put(f"/api/sessions/{sid}/pin")
    assert resp.status_code == 200
    after = resp.json()["pinned"]
    assert after != before
    assert after in (0, 1)


def test_add_message_and_history(client):
    sid = _create_session(client)
    add = client.post(
        f"/api/sessions/{sid}/messages",
        json={"role": "user", "content": "你好"},
    )
    assert add.status_code == 200
    assert add.json()["message_count"] >= 1

    hist = client.get(f"/api/sessions/{sid}/history")
    assert hist.status_code == 200
    msgs = hist.json()["messages"]
    assert any(m["content"] == "你好" for m in msgs)


def test_delete_session(client):
    sid = _create_session(client)
    resp = client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_upload_session_file(client):
    sid = _create_session(client)
    files = {"file": ("hello.txt", b"hello world", "text/plain")}
    resp = client.post(f"/api/sessions/{sid}/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "hello.txt"
    assert data["size"] == len(b"hello world")
