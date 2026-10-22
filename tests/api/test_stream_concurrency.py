"""流式入口的并发互斥测试：同一会话已有流在跑时，聊天、专家执行、HITL 恢复都直接返回 409。

409 必须在用户消息落库之前返回。专家执行入口原来没有这道检查：并发请求先落一条用户消息、
做完相关性预检、建好 Agent，最后才在 ``_detached_event_stream`` 的兜底里拿到错误事件，
会话里多出一条没有回复的用户消息。
"""

from types import SimpleNamespace

import pytest

import easy_agent.api.chat as chat_api
import easy_agent.services.expert_runtime as expert_runtime
from easy_agent.services import agent_manager

BUSY = "本会话正在生成回复，请等待完成或先取消当前生成"


@pytest.fixture()
def busy_session(client):
    """一个属于 testuser、且本进程里登记着运行中流任务的会话。

    登记要在应用关闭前撤掉（本 fixture 依赖 client，先于它收尾），否则关闭时
    drain_stream_tasks 会去等这个假任务。
    """
    session_id = client.post("/agent/sessions", json={}).json()["session_id"]
    agent_manager._session_stream_tasks[session_id] = SimpleNamespace(done=lambda: False)
    yield session_id
    agent_manager._session_stream_tasks.pop(session_id, None)


@pytest.fixture(autouse=True)
def _no_agent(monkeypatch):
    async def must_not_build(*args, **kwargs):
        raise AssertionError("会话正忙时不应创建 Agent")

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", must_not_build)


def test_expert_run_on_a_busy_session_is_409(client, db, busy_session, monkeypatch):
    async def must_not_gate(*args, **kwargs):
        raise AssertionError("会话正忙时不应做相关性预检")

    monkeypatch.setattr(expert_runtime, "relevance_gate", must_not_gate)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "你好", "session_id": busy_session, "expert_agent_id": "preset_x"},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"] == BUSY
    assert db.get_session(busy_session).messages == []


def test_chat_stream_on_a_busy_session_is_409(client, db, busy_session):
    resp = client.post("/agent/chat/stream", json={"message": "你好", "session_id": busy_session})

    assert resp.status_code == 409
    assert resp.json()["detail"] == BUSY
    assert db.get_session(busy_session).messages == []


def test_resume_on_a_busy_session_is_409(client, busy_session):
    resp = client.post(
        "/agent/chat/resume",
        json={
            "session_id": busy_session,
            "thread_id": f"{busy_session}-m1",
            "decisions": [{"type": "approve"}],
        },
    )

    assert resp.status_code == 409
    assert resp.json()["detail"] == BUSY


def test_a_finished_stream_does_not_block_the_next_turn(busy_session):
    """登记的任务已结束（done）时不算忙：只看运行中的任务。"""
    agent_manager._session_stream_tasks[busy_session] = SimpleNamespace(done=lambda: True)

    chat_api.reject_concurrent_stream(busy_session)
