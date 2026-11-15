"""DA-02：/agent/chat/resume 在恢复前校验审批决策。

批处理 HITL 会先把恢复值存进 checkpoint 再校验，非法决策会让线程卡死，
所以接口必须在启动恢复流之前返回 400。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import easy_agent.api.chat as chat_api
import easy_agent.services.agents.chat_turn as chat_turn
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.models.db import SessionModel


@pytest.fixture(autouse=True)
def _clean_hubs():
    chat_api._session_stream_hubs.clear()
    yield
    chat_api._session_stream_hubs.clear()


def _pending_state():
    interrupt = SimpleNamespace(
        id="int-A",
        value={
            "action_requests": [{"name": "delete", "args": {"file_path": "/a.txt"}}],
            "review_configs": [{"action_name": "delete", "allowed_decisions": ["approve", "reject"]}],
        },
    )
    return SimpleNamespace(next=("tools",), tasks=[SimpleNamespace(interrupts=[interrupt])], values={})


@pytest.fixture
def resume_setup(db, monkeypatch):
    sid = "sess-resume-validate"
    db.create_session(
        SessionModel(
            session_id=sid,
            title="t",
            messages=[],
            created_at="2026-10-19T00:00:00",
            updated_at="2026-10-19T00:00:00",
            username="testuser",
            workspace_name=f"ws-{sid}",
        )
    )
    calls = []

    class _Graph:
        async def aget_state(self, config):
            return _pending_state()

    async def fake_agent(session_id, username, workspace_name, **kwargs):
        return SimpleNamespace(agent=_Graph(), workspace_dir=None, workspace_virtual_path="/workspace")

    async def fake_resume(**kwargs):
        calls.append(kwargs)
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_turn, "get_or_create_agent_for_session", fake_agent)
    monkeypatch.setattr(chat_turn, "resume_stream_generator", fake_resume)
    return sid, calls


def _resume(client, sid, decisions):
    return client.post(
        "/agent/chat/resume",
        json={"session_id": sid, "thread_id": f"{sid}-m1", "decisions": decisions, "message_id": "m1"},
    )


@pytest.mark.parametrize(
    "decisions",
    [
        [{"type": "approve", "interrupt_id": "int-gone"}],  # 已处理/过期的中断
        [{"type": "edit", "interrupt_id": "int-A"}],  # 该工具不允许 edit
        [{"type": "approve"}, {"type": "approve"}],  # 数量对不上
    ],
)
def test_invalid_decisions_get_400_without_starting_a_resume(client, resume_setup, decisions):
    sid, calls = resume_setup
    resp = _resume(client, sid, decisions)
    assert resp.status_code == 400
    assert "审批决策无效" in resp.json()["detail"]
    assert calls == []


def test_valid_tagged_decision_starts_the_resume(client, resume_setup):
    sid, calls = resume_setup
    resp = _resume(client, sid, [{"type": "approve", "interrupt_id": "int-A"}])
    assert resp.status_code == 200
    assert len(calls) == 1


def test_plan_confirmation_persists_user_message_and_splits_assistant(
    client, db, monkeypatch
):
    sid = "sess-plan-confirm"
    db.create_session(
        SessionModel(
            session_id=sid,
            title="plan",
            messages=[],
            created_at="2026-11-14T00:00:00",
            updated_at="2026-11-14T00:00:00",
            username="testuser",
            workspace_name=f"ws-{sid}",
        )
    )
    MessageRepository(db).add_message(
        sid,
        {
            "role": "assistant",
            "content": "先确认方案。",
            "timestamp": "2026-11-14T00:00:01",
            "blocks": [
                {
                    "type": "tool_call",
                    "tool_name": "ask_user",
                    "tool_call_id": "ask-1",
                    "approval_status": "pending",
                    "pending_approval": True,
                }
            ],
            "pending_approval": {
                "thread_id": f"{sid}-m1",
                "action_requests": [
                    {
                        "tool_name": "ask_user",
                        "tool_call_id": "ask-1",
                        "interrupt_id": "int-ask",
                        "action_index": 0,
                    }
                ],
            },
            "usage": {"step_count": 1, "elapsed_time": 2.0},
        },
    )
    calls = []

    async def fake_agent(session_id, username, workspace_name, **kwargs):
        return SimpleNamespace(
            agent=SimpleNamespace(), workspace_dir=None, workspace_virtual_path="/workspace"
        )

    async def fake_validate(*args, **kwargs):
        return None

    async def fake_resume(**kwargs):
        calls.append(kwargs)
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_turn, "get_or_create_agent_for_session", fake_agent)
    monkeypatch.setattr(chat_turn, "validate_resume_decisions", fake_validate)
    monkeypatch.setattr(chat_turn, "resume_stream_generator", fake_resume)

    resp = client.post(
        "/agent/chat/resume",
        json={
            "session_id": sid,
            "thread_id": f"{sid}-m1",
            "message_id": "continuation-1",
            "decisions": [
                {
                    "type": "respond",
                    "message": '{"answers":{"project":"fast"}}',
                    "interrupt_id": "int-ask",
                    "action_index": 0,
                }
            ],
            "confirmation_message": "1. 项目方案：快速版本",
            "confirmation_message_id": "confirm-1",
            "split_assistant_message": True,
        },
    )

    assert resp.status_code == 200
    assert calls[0]["split_assistant"] is True
    messages = db.get_session(sid).messages
    assert [message["role"] for message in messages] == ["assistant", "user"]
    assert messages[0]["blocks"][0]["approval_status"] == "approved"
    assert "pending_approval" not in messages[0]
    assert messages[1] == {
        **messages[1],
        "id": "confirm-1",
        "role": "user",
        "content": "1. 项目方案：快速版本",
        "display_only": True,
        "message_kind": "plan_confirmation",
    }
