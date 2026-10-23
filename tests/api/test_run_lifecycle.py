"""多副本 Phase 2B：chat / resume / 专家执行的 run 生命周期接入。

覆盖：
- /agent/chat/stream 在返回流前创建 running run，正常结束落 completed；
- 生成器异常 → failed（error 截断）且前端收到 error 帧；
- /cancel（CancelledError）→ cancelled；
- /agent/chat/resume 同样创建 run，request_json 记录 decisions；
- 专家对话（/agent/chat/stream + expert_agent_id）的正常执行同样登记 run（删除会话时按它找每条消息一个的旧
  thread），相关性预检拒答的短路流不登记；
- run 登记失败不影响聊天主链路。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import easy_agent.api.chat as chat_api
import easy_agent.services.expert_runtime as expert_runtime
from easy_agent.models.db import SessionModel
from easy_agent.db.repositories import agent_runs as agent_runs_repo
from easy_agent.services.run_registry import new_run_id, start_run


async def _async_noop(*args, **kwargs):
    """替换写流锁的异步函数（aregister / aunregister_stream_task）。"""
    return None


@pytest.fixture(autouse=True)
def _clean_hubs():
    chat_api._session_stream_hubs.clear()
    yield
    chat_api._session_stream_hubs.clear()


def _seed_session(db, session_id: str, username: str = "testuser") -> None:
    db.create_session(
        SessionModel(
            session_id=session_id,
            title="run 生命周期",
            messages=[],
            created_at="2026-10-18T00:00:00",
            updated_at="2026-10-18T00:00:00",
            username=username,
            workspace_name=f"ws-{session_id}",
        )
    )
    db.update_session_workspace_name(session_id, f"ws-{session_id}")


def _patch_agent(monkeypatch):
    async def fake_agent(session_id, username, workspace_name, **kwargs):
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_agent)


def test_chat_stream_creates_run_and_marks_completed(client, db, monkeypatch):
    sid = "sess-run-complete"
    _seed_session(db, sid)
    _patch_agent(monkeypatch)

    async def fake_stream(**kwargs):
        yield 'data: {"type": "start"}\n\n'
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "hi", "session_id": sid, "message_id": "msg-complete"},
    )

    assert resp.status_code == 200
    assert '"type": "done"' in resp.text

    run = db.get_run(new_run_id(sid, "msg-complete"))
    assert run is not None
    assert run["session_id"] == sid
    assert run["username"] == "testuser"
    assert run["message_id"] == "msg-complete"
    assert run["status"] == "completed"
    assert run["lease_until"] == ""
    assert run["lease_owner"]  # 记录执行 Pod，便于审计
    payload = json.loads(run["request_json"])
    assert payload["message"] == "hi"


def test_chat_stream_exception_marks_failed_and_emits_error(client, db, monkeypatch):
    sid = "sess-run-fail"
    _seed_session(db, sid)
    _patch_agent(monkeypatch)

    async def fake_stream(**kwargs):
        yield 'data: {"type": "start"}\n\n'
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "hi", "session_id": sid, "message_id": "msg-fail"},
    )

    assert resp.status_code == 200
    assert '"type": "error"' in resp.text
    assert "boom" in resp.text

    run = db.get_run(new_run_id(sid, "msg-fail"))
    assert run["status"] == "failed"
    assert "RuntimeError" in run["error"]
    assert "boom" in run["error"]
    assert run["lease_until"] == ""


async def test_cancelled_stream_marks_run_cancelled(db, monkeypatch):
    sid = "sess-run-cancel"
    run_id = "run-cancel-1"
    assert start_run(db, run_id, sid, message_id="msg-cancel") is True

    bg_tasks: dict[str, asyncio.Task] = {}

    async def _capture(session_id, task):
        bg_tasks[session_id] = task

    monkeypatch.setattr(chat_api, "aregister_stream_task", _capture)
    monkeypatch.setattr(chat_api, "aunregister_stream_task", _async_noop)

    started = asyncio.Event()

    async def fake_gen():
        started.set()
        await asyncio.sleep(3600)
        yield 'data: {"type": "done"}\n\n'  # pragma: no cover - 取消后不可达

    agen = chat_api._detached_event_stream(
        fake_gen(), sid, sid, db=db, run_id=run_id
    )
    consumer = asyncio.create_task(agen.__anext__())
    await asyncio.wait_for(started.wait(), timeout=5)

    bg_tasks[sid].cancel()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(consumer, timeout=5)

    run = db.get_run(run_id)
    assert run["status"] == "cancelled"
    assert run["lease_until"] == ""


def test_resume_creates_run_with_decisions(client, db, monkeypatch):
    sid = "sess-run-resume"
    _seed_session(db, sid)
    _patch_agent(monkeypatch)

    async def fake_resume(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "resume_stream_generator", fake_resume)

    resp = client.post(
        "/agent/chat/resume",
        json={
            "session_id": sid,
            "thread_id": f"{sid}-msg-approve",
            "decisions": [{"type": "approve"}],
            "message_id": "msg-approve",
        },
    )

    assert resp.status_code == 200
    run = db.get_run(new_run_id(sid, "msg-approve"))
    assert run is not None
    assert run["status"] == "completed"
    payload = json.loads(run["request_json"])
    assert payload["session_id"] == sid
    assert payload["decisions"] == [{"type": "approve"}]
    assert payload["thread_id"] == f"{sid}-msg-approve"


def test_resume_without_message_id_still_creates_run(client, db, monkeypatch):
    sid = "sess-run-resume-nomsg"
    _seed_session(db, sid)
    _patch_agent(monkeypatch)

    async def fake_resume(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "resume_stream_generator", fake_resume)

    resp = client.post(
        "/agent/chat/resume",
        json={
            "session_id": sid,
            "thread_id": f"{sid}-m2",
            "decisions": [{"type": "reject"}],
        },
    )

    assert resp.status_code == 200
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM agent_runs WHERE session_id=?", (sid,)
        )
        rows = cursor.fetchall()
    assert len(rows) == 1


def test_run_registry_failure_does_not_break_chat(client, db, monkeypatch):
    sid = "sess-run-registry-down"
    _seed_session(db, sid)
    _patch_agent(monkeypatch)

    async def fake_stream(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)
    monkeypatch.setattr(chat_api, "start_run", lambda *args, **kwargs: False)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "hi", "session_id": sid, "message_id": "msg-x"},
    )

    assert resp.status_code == 200
    assert db.get_run(new_run_id(sid, "msg-x")) is None


# ── 专家对话（统一入口 /agent/chat/stream） ───────────────────────────


def _patch_chat_stream(monkeypatch, fake_stream):
    async def fake_agent(session_id, username, workspace_name, **kwargs):
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_agent)
    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)


def test_expert_run_creates_run_and_marks_completed(client, db, monkeypatch):
    sid = "sess-expert-run"
    _seed_session(db, sid)

    async def fake_stream(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    _patch_chat_stream(monkeypatch, fake_stream)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "hi", "session_id": sid, "message_id": "msg-expert"},
    )

    assert resp.status_code == 200
    run = db.get_run(new_run_id(sid, "msg-expert"))
    assert run is not None
    assert (run["status"], run["username"], run["message_id"]) == (
        "completed",
        "testuser",
        "msg-expert",
    )
    payload = json.loads(run["request_json"])
    assert payload["message"] == "hi"
    assert "expert_agent_id" in payload
    # 删除会话时按 run 的 message_id 找每条消息一个的旧 checkpoint thread
    assert agent_runs_repo.list_session_message_ids(db, sid) == ["msg-expert"]


def test_expert_run_exception_marks_failed(client, db, monkeypatch):
    sid = "sess-expert-run-fail"
    _seed_session(db, sid)

    async def fake_stream(**kwargs):
        yield 'data: {"type": "start"}\n\n'
        raise RuntimeError("expert boom")

    _patch_chat_stream(monkeypatch, fake_stream)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "hi", "session_id": sid, "message_id": "msg-expert-fail"},
    )

    assert resp.status_code == 200
    assert "expert boom" in resp.text
    run = db.get_run(new_run_id(sid, "msg-expert-fail"))
    assert run["status"] == "failed"
    assert "RuntimeError: expert boom" in run["error"]


def test_expert_refusal_does_not_create_a_run(client, db, monkeypatch):
    """相关性预检拒答：不建 Agent、瞬间写完，不是 run 生命周期。"""
    sid = "sess-expert-refusal"
    _seed_session(db, sid)

    async def must_not_stream(**kwargs):
        raise AssertionError("拒答时不应进入 Agent 生成器")
        yield  # pragma: no cover

    _patch_chat_stream(monkeypatch, must_not_stream)

    async def refuse(*args, **kwargs):
        return "irrelevant", "这个问题不在本专家的职责范围内。"

    monkeypatch.setattr(expert_runtime, "relevance_gate", refuse)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "今天天气如何", "session_id": sid, "message_id": "msg-refused"},
    )

    assert resp.status_code == 200
    assert "这个问题不在本专家的职责范围内" in resp.text
    assert db.get_run(new_run_id(sid, "msg-refused")) is None


def test_run_registry_failure_does_not_break_expert_run(client, db, monkeypatch):
    sid = "sess-expert-registry-down"
    _seed_session(db, sid)

    async def fake_stream(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    _patch_chat_stream(monkeypatch, fake_stream)
    monkeypatch.setattr(chat_api, "start_run", lambda *args, **kwargs: False)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "hi", "session_id": sid, "message_id": "msg-expert-x"},
    )

    assert resp.status_code == 200
    assert '"type": "done"' in resp.text
    assert db.get_run(new_run_id(sid, "msg-expert-x")) is None
