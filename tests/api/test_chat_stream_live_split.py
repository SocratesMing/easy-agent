"""批次 1C：chat 流事件中枢拆分后的兼容性与只读端点回归。

覆盖：
- ``/stream/status``、``/stream/live`` 路由仍挂在原 URL 且只挂一次；
- status 以 DB 的 ``session_runtime.stream_owner/stream_until`` 为准；
- live 仍可从 ``stream_events`` 表回放终态（不依赖本 Pod hub）；
- ``from easy_agent.api.chat import _StreamHub`` 等旧导入路径继续可用。
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from easy_agent.api import chat as chat_module
from easy_agent.api import chat_live
from easy_agent.db.repositories.stream_events import (
    append_stream_event,
    list_stream_events,
)
from easy_agent.models.db import SessionModel
from easy_agent.services import stream_hub

SESSION_ID = "sess-live-split"


def _frame(event_type: str, **fields) -> str:
    return f"data: {json.dumps({'type': event_type, **fields}, ensure_ascii=False)}\n\n"


def _seed_session(db, username: str = "testuser") -> None:
    db.create_session(
        SessionModel(
            session_id=SESSION_ID,
            title="拆分回归",
            messages=[],
            created_at="2026-10-18T00:00:00",
            updated_at="2026-10-18T00:00:00",
            username=username,
            workspace_name="ws-live-split",
        )
    )
    db.update_session_workspace_name(SESSION_ID, "ws-live-split")


@pytest.fixture(autouse=True)
def _clear_hubs():
    stream_hub._session_stream_hubs.clear()
    yield
    stream_hub._session_stream_hubs.clear()


def test_live_routes_mounted_once_on_original_urls():
    paths = [route.path for route in chat_module.router.routes]
    assert paths.count("/agent/chat/stream/status") == 1
    assert paths.count("/agent/chat/stream/live") == 1
    assert chat_live.router.prefix == "/agent/chat"
    assert chat_live.router.tags == ["Chat"]
    # 旧模块路径继续暴露处理函数（OpenAPI operationId/依赖不变）
    assert chat_module.chat_stream_status is chat_live.chat_stream_status
    assert chat_module.chat_stream_live is chat_live.chat_stream_live


def test_stream_hub_old_import_paths_still_work():
    from easy_agent.api.chat import _StreamHub

    assert _StreamHub is stream_hub.StreamHub
    assert _StreamHub is stream_hub._StreamHub
    assert chat_module._session_stream_hubs is stream_hub._session_stream_hubs
    assert chat_module._stream_hub_for is stream_hub.stream_hub_for
    assert chat_module._HUB_HISTORY_MAX_CHARS == stream_hub._HUB_HISTORY_MAX_CHARS

    hub = _StreamHub("compat-session")
    queue = hub.subscribe()
    hub.broadcast(_frame("content", content="hi"))
    assert queue.get_nowait().startswith("data: ")


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
    assert expired.status_code == 200
    assert expired.json() == {"session_id": SESSION_ID, "active": False}

    assert client.get(
        "/agent/chat/stream/status", params={"session_id": "missing"}
    ).status_code == 404


def test_stream_live_replays_terminal_events_from_db(client, db):
    _seed_session(db)
    frames = [
        _frame("start", session_id=SESSION_ID),
        _frame("thinking", content="思考中", step=1),
        _frame("content", content="最终回答", step=1),
        _frame("done", session_id=SESSION_ID, finish_reason="stop"),
    ]
    for frame in frames:
        append_stream_event(db, SESSION_ID, frame)
    rows = list_stream_events(db, SESSION_ID)

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
    # 只读端点不得依赖/创建本 Pod 的进程内 hub
    assert stream_hub._session_stream_hubs == {}
