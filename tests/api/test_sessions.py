"""会话接口测试：创建、列表、详情、改标题、置顶、消息、历史、删除、文件。"""

import io

from fastapi.testclient import TestClient


def _create_session(client: TestClient, title=None):
    body = {"title": title} if title else {}
    resp = client.post("/agent/sessions", json=body)
    assert resp.status_code == 200
    return resp.json()["session_id"]


def test_create_session_default_title(client):
    sid = _create_session(client)
    detail = client.get(f"/agent/sessions/{sid}")
    assert detail.status_code == 200
    assert detail.json()["session_id"] == sid


def test_create_session_with_title(client):
    sid = _create_session(client, title="我的会话")
    detail = client.get(f"/agent/sessions/{sid}").json()
    assert detail["title"] == "我的会话"


def test_list_and_count(client):
    _create_session(client)
    _create_session(client)
    lst = client.get("/agent/sessions")
    assert lst.status_code == 200
    sessions = lst.json()
    assert isinstance(sessions, list)
    assert len(sessions) >= 2

    cnt = client.get("/agent/sessions/count")
    assert cnt.status_code == 200
    assert cnt.json()["total_sessions"] >= 2


def test_get_missing_session_404(client):
    resp = client.get("/agent/sessions/does-not-exist")
    assert resp.status_code == 404


def test_update_title(client):
    sid = _create_session(client)
    resp = client.put(f"/agent/sessions/{sid}/title", json={"title": "新标题"})
    assert resp.status_code == 200
    assert client.get(f"/agent/sessions/{sid}").json()["title"] == "新标题"


def test_pin_session(client):
    sid = _create_session(client)
    before = client.get(f"/agent/sessions/{sid}").json().get("pinned", 0)
    resp = client.put(f"/agent/sessions/{sid}/pin")
    assert resp.status_code == 200
    after = resp.json()["pinned"]
    assert after != before
    assert after in (0, 1)


def test_add_message_and_history(client):
    sid = _create_session(client)
    add = client.post(
        f"/agent/sessions/{sid}/messages",
        json={"role": "user", "content": "你好"},
    )
    assert add.status_code == 200
    assert add.json()["message_count"] >= 1

    hist = client.get(f"/agent/sessions/{sid}/history")
    assert hist.status_code == 200
    msgs = hist.json()["messages"]
    assert any(m["content"] == "你好" for m in msgs)


def test_delete_session(client):
    sid = _create_session(client)
    resp = client.delete(f"/agent/sessions/{sid}")
    assert resp.status_code == 200
    assert client.get(f"/agent/sessions/{sid}").status_code == 404


def test_upload_session_file(client):
    sid = _create_session(client)
    files = {"file": ("hello.txt", b"hello world", "text/plain")}
    resp = client.post(f"/agent/sessions/{sid}/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "hello.txt"
    assert data["size"] == len(b"hello world")


def test_compute_session_usage_sums_reasoning_tokens():
    """会话级聚合需带上思考 token（output 的子集，不并入 total 重复计数）。"""
    from easy_agent.api.sessions import compute_session_usage

    usage = compute_session_usage([
        {"role": "user"},
        {"role": "assistant", "usage": {
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "reasoning_tokens": 3, "context_tokens": 10,
            "elapsed_time": 1.0, "step_count": 1}},
        {"role": "assistant", "usage": {
            "input_tokens": 20, "output_tokens": 8, "total_tokens": 28,
            "reasoning_tokens": 6, "context_tokens": 20,
            "elapsed_time": 2.0, "step_count": 2}},
    ])
    assert usage["input_tokens"] == 30
    assert usage["output_tokens"] == 13
    assert usage["reasoning_tokens"] == 9
    # 已不再统计会话累计总量（旧消息里残留的 total_tokens 不参与聚合）
    assert "total_tokens" not in usage
    # 旧消息无 reasoning_tokens 字段时按 0 处理，不报错
    legacy = compute_session_usage([
        {"role": "assistant", "usage": {
            "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
    ])
    assert legacy["reasoning_tokens"] == 0


# --- C1：API 层依赖 SessionRepository，而不是 Database 门面 -----------------


def test_api_sessions_module_has_no_database_facade_import():
    """静态回归：路由文件不再 import Database / 直接调用 db.* 业务方法。"""
    import inspect

    import easy_agent.api.sessions as sessions_api

    source = inspect.getsource(sessions_api)
    assert "from ..db import Database" not in source
    assert "Annotated[Database" not in source
    # 路由依赖必须是 repository provider（10 个路由）。
    assert source.count("Annotated[SessionRepository, Depends(get_session_repository)]") == 10
    # 批次 4：provider 统一到 api.deps，本模块不再声明数据库依赖。
    assert "Depends(get_database)" not in source
    assert "def get_session_repository(" not in source
    assert "from .deps import" in source


def test_api_routes_use_overridden_session_repository(client, db):
    """黑盒回归：把依赖覆盖成 repository 后，路由正常且确实走 repository。"""
    from easy_agent.api.sessions import get_session_repository
    from easy_agent.app import app
    from easy_agent.db.session_repository import SessionRepository

    class _SpyRepository(SessionRepository):
        def __init__(self, database):
            super().__init__(database)
            self.calls = []

        def create_session(self, session_data):
            self.calls.append("create_session")
            return super().create_session(session_data)

        def get_owned_session(self, session_id, username):
            self.calls.append("get_owned_session")
            return super().get_owned_session(session_id, username)

        def list_sessions(self, *args, **kwargs):
            self.calls.append("list_sessions")
            return super().list_sessions(*args, **kwargs)

    spy = _SpyRepository(db)
    app.dependency_overrides[get_session_repository] = lambda: spy
    try:
        sid = _create_session(client)
        assert client.get(f"/agent/sessions/{sid}").status_code == 200
        assert client.get("/agent/sessions").status_code == 200
    finally:
        app.dependency_overrides.pop(get_session_repository, None)

    assert "create_session" in spy.calls
    assert "get_owned_session" in spy.calls
    assert "list_sessions" in spy.calls
