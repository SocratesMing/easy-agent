"""会话接口测试：创建、列表、详情、改标题、置顶、消息、历史、删除、文件。"""


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


def test_history_keeps_raw_thinking(client, db):
    """历史读取保留原文思考：前端「思考过程」要显示真实内容，不能退化成固定摘要。"""
    sid = _create_session(client)
    thinking = "先核对用户给的表格字段，再决定是否调用工具补数据"
    db.add_message(
        sid,
        {
            "role": "assistant",
            "content": "答案",
            "thinking": thinking,
            "thinking_duration": 1.2,
            "blocks": [
                {"type": "thinking", "order": 0, "content": thinking, "step": 0, "duration": 1.2}
            ],
        },
    )

    detail = client.get(f"/agent/sessions/{sid}").json()
    assert detail["messages"][-1]["thinking"] == thinking
    assert detail["messages"][-1]["blocks"][0]["content"] == thinking

    hist = client.get(f"/agent/sessions/{sid}/history").json()
    assert hist["messages"][-1]["thinking"] == thinking


def test_delete_session(client):
    sid = _create_session(client)
    resp = client.delete(f"/agent/sessions/{sid}")
    assert resp.status_code == 200
    assert client.get(f"/agent/sessions/{sid}").status_code == 404


def test_delete_session_removes_its_artifacts_dir(client):
    """DA-04：框架产物在工作区之外的隐藏目录里，删除会话时要一并删除。"""
    from easy_agent.agent.backend import agent_artifacts_dir

    sid = _create_session(client)
    artifacts = agent_artifacts_dir("testuser", sid)
    (artifacts / ".agent" / "large_tool_results").mkdir(parents=True)
    (artifacts / ".agent" / "large_tool_results" / "call_1").write_text("x", encoding="utf-8")

    assert client.delete(f"/agent/sessions/{sid}").status_code == 200
    assert not artifacts.exists()


def test_delete_session_deletes_its_checkpoint_threads(client, db, monkeypatch):
    """DA-03：删除会话时一并删除 checkpoint thread（会话 thread 与每条消息一个的旧 thread）。

    官方 saver 全局共享，改造前删除会话只清 legacy 快照，旧 thread 永远留在库里。
    """
    import easy_agent.api.sessions as sessions_api
    from easy_agent.db.repositories.agent_runs import start_run

    sid = _create_session(client)
    for mid in ("m1", "m2"):
        start_run(db, f"{sid}:{mid}", sid, "testuser", mid, "{}", "pod", "2099-01-01T00:00:00+00:00")
    calls = []

    async def fake_delete(session_id, message_ids=()):
        calls.append((session_id, sorted(message_ids)))

    monkeypatch.setattr(sessions_api, "delete_session_threads", fake_delete)

    assert client.delete(f"/agent/sessions/{sid}").status_code == 200
    assert calls == [(sid, ["m1", "m2"])]


def test_delete_session_still_releases_checkpoints_when_run_lookup_fails(client, monkeypatch):
    """读取 run 记录失败时，仍要删除会话 thread、释放 legacy 快照（改造前释放一定会执行）。"""
    import easy_agent.api.sessions as sessions_api
    from easy_agent.db.session_repository import SessionRepository

    def broken(self, session_id):
        raise RuntimeError("db down")

    calls = []

    async def fake_delete(session_id, message_ids=()):
        calls.append((session_id, list(message_ids)))

    monkeypatch.setattr(SessionRepository, "list_run_message_ids", broken)
    monkeypatch.setattr(sessions_api, "delete_session_threads", fake_delete)

    sid = _create_session(client)
    assert client.delete(f"/agent/sessions/{sid}").status_code == 200
    assert calls == [(sid, [])]


def test_upload_session_file(client):
    sid = _create_session(client)
    files = {"file": ("hello.txt", b"hello world", "text/plain")}
    resp = client.post(f"/agent/sessions/{sid}/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "hello.txt"
    assert data["size"] == len(b"hello world")


def test_compute_session_usage_reports_the_latest_call_not_a_sum():
    """token 字段与流式 token_usage 同口径：最近一次模型调用，不跨轮相加。

    每轮的输入已包含之前各轮的历史，相加会重复计算（10 + 20 加成 30）；
    耗时与步数可以相加，仍按会话累计。
    """
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
    # 旧消息里残留的 total_tokens 不返回
    assert usage == {
        "input_tokens": 20,
        "output_tokens": 8,
        "reasoning_tokens": 6,
        "context_tokens": 20,
        "elapsed_time": 3.0,
        "step_count": 3,
    }
    # 旧消息无 reasoning_tokens / context_tokens 字段：思考按 0，上下文回退 input_tokens
    legacy = compute_session_usage([
        {"role": "assistant", "usage": {
            "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
    ])
    assert legacy["reasoning_tokens"] == 0
    assert legacy["context_tokens"] == 1


def test_compute_session_usage_skips_replies_without_a_model_call():
    """专家拒答等没有调用模型的回复（用量全为 0）不覆盖上一轮的真实用量。"""
    from easy_agent.api.sessions import compute_session_usage

    usage = compute_session_usage([
        {"role": "assistant", "usage": {
            "input_tokens": 50, "output_tokens": 9, "context_tokens": 50,
            "elapsed_time": 1.0, "step_count": 1}},
        {"role": "user"},
        {"role": "assistant", "usage": {
            "input_tokens": 0, "output_tokens": 0, "context_tokens": 0,
            "elapsed_time": 0.2, "step_count": 0}},
    ])
    assert usage["input_tokens"] == 50
    assert usage["output_tokens"] == 9
    assert usage["context_tokens"] == 50
    assert usage["elapsed_time"] == 1.2


def test_session_endpoints_report_the_window_of_the_latest_turn_model(client, db, monkeypatch):
    """会话不记录所用模型：窗口取最近一轮落库的 context_length，不取当前默认模型。"""
    from easy_agent.api import sessions as sessions_api

    monkeypatch.setattr(sessions_api, "get_context_length", lambda: 1_000_000)
    sid = _create_session(client)
    db.add_message(sid, {"role": "assistant", "content": "a", "usage": {
        "input_tokens": 900, "output_tokens": 10, "context_tokens": 900,
        "context_length": 131072, "elapsed_time": 1.0, "step_count": 1}})

    for path in (f"/agent/sessions/{sid}", f"/agent/sessions/{sid}/history"):
        data = client.get(path).json()
        assert data["context_length"] == 131072, path
        assert data["usage"]["context_tokens"] == 900, path
        assert data["usage"]["input_tokens"] == 900, path


def test_session_endpoints_fall_back_to_the_default_window_for_old_messages(client, db, monkeypatch):
    """改造前落库的消息没有 context_length：退回当前默认模型的窗口。"""
    from easy_agent.api import sessions as sessions_api

    monkeypatch.setattr(sessions_api, "get_context_length", lambda: 4242)
    sid = _create_session(client)
    db.add_message(sid, {"role": "assistant", "content": "a", "usage": {
        "input_tokens": 5, "output_tokens": 1, "context_tokens": 5}})

    assert client.get(f"/agent/sessions/{sid}").json()["context_length"] == 4242
    assert client.get(f"/agent/sessions/{sid}/history").json()["context_length"] == 4242


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
    assert "from easy_agent.api.deps import" in source


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
