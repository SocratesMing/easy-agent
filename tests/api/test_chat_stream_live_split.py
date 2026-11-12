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
from easy_agent.services.streaming import hub as stream_hub

def _runtime(db):
    """T3c：会话运行期状态走领域 repository（Database 转发方法已删除）。"""
    from easy_agent.db.repositories.session_runtime import SessionRuntimeRepository

    return SessionRuntimeRepository(db)


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

    _runtime(db).upsert_session_runtime(
        SESSION_ID, stream_owner="pod-a:123", stream_until=future
    )
    active = client.get(
        "/agent/chat/stream/status", params={"session_id": SESSION_ID}
    )
    assert active.status_code == 200
    body = active.json()
    assert body["session_id"] == SESSION_ID
    assert body["active"] is True
    # 正在跑就不需要额外的回放判据（避免多一次流事件查询）
    assert body["replayable"] is False

    _runtime(db).upsert_session_runtime(
        SESSION_ID, stream_owner="pod-a:123", stream_until=past
    )
    expired = client.get(
        "/agent/chat/stream/status", params={"session_id": SESSION_ID}
    )
    assert expired.status_code == 200
    assert expired.json()["active"] is False
    # 没有事件：没过程可回放
    assert expired.json()["replayable"] is False

    assert client.get(
        "/agent/chat/stream/status", params={"session_id": "missing"}
    ).status_code == 404


def test_stream_status_marks_unfinished_events_as_replayable(client, db):
    """没在跑但过程事件比落库消息新时，打开会话要能挂一次 live 回放。

    HITL 待审批、进程崩溃、写入器异常都停在这些非终态帧上：落库的只有半截消息，
    思考过程还在 ``stream_events`` 里。终态帧（done/error）说明该轮已终结，
    事件只是历史缓冲，不能再回放（否则会把上一轮的正文重放一遍）。
    """
    _seed_session(db)

    def status():
        response = client.get(
            "/agent/chat/stream/status", params={"session_id": SESSION_ID}
        )
        assert response.status_code == 200
        return response.json()["replayable"]

    # 停在待审批：replayable
    append_stream_event(db, SESSION_ID, _frame("thinking", content="想一下"))
    append_stream_event(db, SESSION_ID, _frame("approval_required", action_requests=[]))
    assert status() is True

    # 已收尾：done 是终态，不再回放
    append_stream_event(db, SESSION_ID, _frame("done", already_completed=True))
    assert status() is False


def test_stream_live_emits_heartbeat_while_idle(client, db, monkeypatch):
    """/stream/live 空闲轮询期也要发心跳，避免代理按「上游静默」掐断连接。"""
    _seed_session(db)
    append_stream_event(db, SESSION_ID, _frame("start", session_id=SESSION_ID))
    monkeypatch.setattr(chat_live, "heartbeat_interval_seconds", lambda: 0.01)
    rounds = []

    def _active(_runtime, _session_id):
        rounds.append(1)
        # 前两轮活跃、之后失效：让空闲轮询走完 2 个轮次后正常收尾。
        return len(rounds) <= 2

    monkeypatch.setattr(chat_live, "is_stream_active", _active)

    response = client.get(
        "/agent/chat/stream/live", params={"session_id": SESSION_ID}
    )

    assert response.status_code == 200
    assert ": keep-alive" in response.text, "空闲期必须出现心跳注释帧"
    assert '"type": "done"' in response.text, "心跳不得影响收尾补 done"


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


def test_stream_live_keeps_replaying_after_approval_required(client, db, monkeypatch):
    """HITL 恢复后事件是追加的：回放到 approval_required 不能停。

    以前 ``approval_required`` 按终态处理，回放到它就 return，导致跨 Pod 打开一个
    已恢复过的会话只能看到中断前那一段，恢复后的正文全部丢失。
    """
    _seed_session(db)
    frames = [
        _frame("thinking", content="先想一下", step=1),
        _frame("approval_required", thread_id=SESSION_ID, action_requests=[]),
        _frame("content", content="批准后继续写的正文", step=2),
        _frame("done", session_id=SESSION_ID, finish_reason="stop"),
    ]
    for frame in frames:
        append_stream_event(db, SESSION_ID, frame)

    response = client.get(
        "/agent/chat/stream/live", params={"session_id": SESSION_ID}
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "approval_required"' in body
    assert "批准后继续写的正文" in body, "approval_required 之后的事件必须继续回放"
    assert '"type": "done"' in body


def test_stream_live_does_not_synthesize_done_while_approval_pending(client, db, monkeypatch):
    _seed_session(db)
    frames = [
        _frame("thinking", content="先想一下", step=1),
        _frame("approval_required", thread_id=SESSION_ID, action_requests=[]),
    ]
    for frame in frames:
        append_stream_event(db, SESSION_ID, frame)
    # 会话没有流锁（HITL 中断时已释放），空闲轮询会走完 2 轮
    monkeypatch.setattr(chat_live, "is_stream_active", lambda *a, **k: False)

    response = client.get(
        "/agent/chat/stream/live", params={"session_id": SESSION_ID}
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "approval_required"' in body
    assert '"type": "done"' not in body, "待审批收尾补 done 会清掉前端审批状态"
