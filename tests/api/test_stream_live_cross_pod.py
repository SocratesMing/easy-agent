"""多副本 Phase 2A：不建本地 hub，仅靠 DB 的跨 Pod ``/stream/live`` 回放。"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from easy_agent.api import chat as chat_module
from easy_agent.db.repositories.stream_events import (
    append_stream_event,
    list_stream_events,
)
from easy_agent.models.db import SessionModel


async def _async_noop(*args, **kwargs):
    """替换写流锁的异步函数（aregister / aunregister_stream_task）。"""
    return None

SESSION_ID = "sess-cross-pod"


def _frame(event_type: str, **fields) -> str:
    return f"data: {json.dumps({'type': event_type, **fields}, ensure_ascii=False)}\n\n"


def _seed_session(db, username: str = "testuser") -> None:
    db.create_session(
        SessionModel(
            session_id=SESSION_ID,
            title="跨 Pod 回放",
            messages=[],
            created_at="2026-10-18T00:00:00",
            updated_at="2026-10-18T00:00:00",
            username=username,
            workspace_name="ws-cross-pod",
        )
    )
    db.update_session_workspace_name(SESSION_ID, "ws-cross-pod")


@pytest.fixture(autouse=True)
def _assert_no_local_hub():
    chat_module._session_stream_hubs.clear()
    yield
    chat_module._session_stream_hubs.clear()


def _seed_frames(db) -> list[dict]:
    frames = [
        _frame("start", session_id=SESSION_ID),
        _frame("thinking", content="思考中", step=1),
        _frame("content", content="最终回答", step=1),
        _frame("done", session_id=SESSION_ID, finish_reason="stop"),
    ]
    for frame in frames:
        append_stream_event(db, SESSION_ID, frame)
    return list_stream_events(db, SESSION_ID)


def test_stream_live_replays_db_events_without_local_hub(client, db):
    _seed_session(db)
    rows = _seed_frames(db)
    assert chat_module._session_stream_hubs == {}

    response = client.get(
        "/agent/chat/stream/live", params={"session_id": SESSION_ID}
    )

    assert response.status_code == 200
    body = response.text
    expected = [row["payload"] for row in rows]
    positions = [body.index(frame) for frame in expected]
    assert positions == sorted(positions), "事件必须按 event_id 顺序回放"
    assert body.count("data: ") == len(expected)
    assert '"type": "done"' in body
    # 回放不依赖本 Pod hub
    assert chat_module._session_stream_hubs == {}


def test_stream_live_last_event_id_skips_replayed_events(client, db):
    _seed_session(db)
    rows = _seed_frames(db)

    response = client.get(
        "/agent/chat/stream/live",
        params={"session_id": SESSION_ID, "last_event_id": rows[0]["event_id"]},
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "start"' not in body
    assert rows[1]["payload"] in body
    assert rows[-1]["payload"] in body


def test_stream_live_without_events_ends_with_already_completed(client, db):
    _seed_session(db)

    response = client.get(
        "/agent/chat/stream/live", params={"session_id": SESSION_ID}
    )

    assert response.status_code == 200
    assert '"type": "done"' in response.text
    assert '"already_completed": true' in response.text


async def test_new_stream_replaces_previous_turn_events(db, monkeypatch):
    """新流开始时清掉上一轮事件：默认 last_event_id=0 不会先回放到旧的 done。"""
    append_stream_event(db, SESSION_ID, _frame("done", session_id=SESSION_ID))

    monkeypatch.setattr(chat_module, "aregister_stream_task", _async_noop)
    monkeypatch.setattr(chat_module, "aunregister_stream_task", _async_noop)

    frames = [
        _frame("start", session_id=SESSION_ID),
        _frame("content", content="新回答"),
        _frame("done", session_id=SESSION_ID),
    ]

    async def _fake_gen():
        for frame in frames:
            yield frame

    chunks = [
        chunk
        async for chunk in chat_module._detached_event_stream(
            _fake_gen(), SESSION_ID, "s1", db=db
        )
    ]
    assert chunks == frames

    from easy_agent.services.stream_events import flush_all_stream_events

    await flush_all_stream_events()
    rows = list_stream_events(db, SESSION_ID)
    assert [row["payload"] for row in rows] == frames


def test_stream_status_uses_db_stream_owner(client, db):
    _seed_session(db)
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat(timespec="seconds")
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")

    db.upsert_session_runtime(
        SESSION_ID, stream_owner="pod-a:123", stream_until=future
    )
    active = client.get(
        "/agent/chat/stream/status", params={"session_id": SESSION_ID}
    )
    assert active.status_code == 200
    assert active.json() == {"session_id": SESSION_ID, "active": True}

    db.upsert_session_runtime(
        SESSION_ID, stream_owner="pod-a:123", stream_until=past
    )
    expired = client.get(
        "/agent/chat/stream/status", params={"session_id": SESSION_ID}
    )
    assert expired.json() == {"session_id": SESSION_ID, "active": False}

    # 其它用户 / 不存在的会话仍按 404 处理
    assert client.get(
        "/agent/chat/stream/status", params={"session_id": "missing"}
    ).status_code == 404
