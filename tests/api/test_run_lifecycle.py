"""多副本 Phase 2B：chat / resume 的 run 生命周期接入。

覆盖：
- /agent/chat/stream 在返回流前创建 running run，正常结束落 completed；
- 生成器异常 → failed（error 截断）且前端收到 error 帧；
- /cancel（CancelledError）→ cancelled；
- /agent/chat/resume 同样创建 run，request_json 记录 decisions；
- run 登记失败不影响聊天主链路。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import easy_agent.api.chat as chat_api
from easy_agent.models.db import SessionModel
from easy_agent.services.run_registry import new_run_id, start_run


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
    monkeypatch.setattr(
        chat_api,
        "register_stream_task",
        lambda session_id, task: bg_tasks.__setitem__(session_id, task),
    )
    monkeypatch.setattr(chat_api, "unregister_stream_task", lambda *args: None)

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
