"""DA-03 P1：会话 thread 在接口层的行为（``agent.session_thread`` 打开）。

- 两轮对话落在同一个 thread（``thread_id = session_id``），历史不重复输入；
- 发新消息时，上一轮遗留的待审批标为拒绝；
- 审批句柄对不上会话 thread 当前这一轮时，恢复接口返回 409；
- 本轮起点 checkpoint 记到本轮用户消息上。
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

import easy_agent.api.chat as chat_api
import easy_agent.persistence.checkpoints as checkpoints
from easy_agent.db.repositories import sessions as sessions_repo
from easy_agent.models.db import SessionModel
from easy_agent.services import agent_manager

class _Silent(GenericFakeChatModel):
    """只用来编译一张读状态的图，不会被调用。"""

    messages: object = iter(())

    def bind_tools(self, tools, **kwargs):
        return self


_ON = {"config": SimpleNamespace(agent=SimpleNamespace(session_thread=True))}


def _session(db, sid="sess-thread"):
    db.create_session(
        SessionModel(
            session_id=sid,
            title="t",
            messages=[],
            created_at="2026-10-20T00:00:00",
            updated_at="2026-10-20T00:00:00",
            username="testuser",
            workspace_name=f"ws-{sid}",
        )
    )
    return sid


def test_new_message_marks_the_left_over_approval_rejected(client, db, monkeypatch):
    sid = _session(db)
    db.add_message(sid, {"role": "user", "content": "删掉 a.txt", "turn_id": "m1"})
    db.add_message(
        sid,
        {
            "role": "assistant",
            "content": "",
            "pending_approval": {"thread_id": f"{sid}-m1"},
            "blocks": [{"type": "tool_call", "tool_call_id": "c1", "approval_status": "pending"}],
        },
    )
    monkeypatch.setattr(chat_api, "_get_agent_config", lambda: _ON)
    monkeypatch.setattr(checkpoints, "sqlite_checkpointer_active", lambda: True)

    async def fake_get_agent(session_id, username, workspace_name, **kwargs):
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    async def fake_stream(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)

    resp = client.post("/agent/chat/stream", json={"message": "算了", "session_id": sid, "message_id": "m2"})

    assert resp.status_code == 200
    abandoned = db.get_session(sid).messages[1]
    assert "pending_approval" not in abandoned
    assert abandoned["blocks"][0]["approval_status"] == "rejected"


def test_legacy_snapshot_saver_does_not_abandon_approvals(client, db, monkeypatch):
    """legacy 快照 saver 下仍是每条消息一个 thread，旧审批还能恢复，DB 里不改成拒绝。"""
    sid = _session(db, "sess-legacy")
    db.add_message(sid, {"role": "user", "content": "删掉 a.txt", "turn_id": "m1"})
    pending = {"role": "assistant", "content": "", "pending_approval": {"thread_id": f"{sid}-m1"}}
    db.add_message(sid, pending)
    monkeypatch.setattr(chat_api, "_get_agent_config", lambda: _ON)
    monkeypatch.setattr(checkpoints, "sqlite_checkpointer_active", lambda: False)

    async def fake_get_agent(session_id, username, workspace_name, **kwargs):
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    async def fake_stream(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)

    client.post("/agent/chat/stream", json={"message": "算了", "session_id": sid, "message_id": "m2"})

    assert db.get_session(sid).messages[1]["pending_approval"] == {"thread_id": f"{sid}-m1"}


def test_resume_with_a_handle_for_an_older_turn_returns_409(client, db, monkeypatch):
    sid = _session(db, "sess-stale")
    monkeypatch.setattr(chat_api, "_get_agent_config", lambda: _ON)
    calls = []

    class _Graph:
        async def aget_state(self, config):
            # 会话 thread 已在下一轮（m2），且没有挂起的中断
            return SimpleNamespace(next=(), tasks=[], values={"turn_context": {"message_id": "m2"}})

    async def fake_agent(session_id, username, workspace_name, **kwargs):
        return SimpleNamespace(agent=_Graph(), config=_ON["config"], max_steps=5)

    async def fake_resume(**kwargs):
        calls.append(kwargs)
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_agent)
    monkeypatch.setattr(chat_api, "resume_stream_generator", fake_resume)

    resp = client.post(
        "/agent/chat/resume",
        json={"session_id": sid, "thread_id": f"{sid}-m1", "decisions": [{"type": "approve"}], "message_id": "m1"},
    )

    assert resp.status_code == 409
    assert "审批已失效" in resp.json()["detail"]
    assert calls == []


def test_annotate_user_turn_merges_fields_into_the_matching_row(db):
    sid = _session(db, "sess-annotate")
    db.add_message(sid, {"role": "user", "content": "q1", "turn_id": "m1"})
    db.add_message(sid, {"role": "assistant", "content": "a1"})
    db.add_message(sid, {"role": "user", "content": "q2", "turn_id": "m2"})

    assert sessions_repo.annotate_user_turn(db, sid, "m1", {"turn_checkpoint_id": "cp-1"}) is True
    assert sessions_repo.annotate_user_turn(db, sid, "missing", {"turn_checkpoint_id": "x"}) is False

    first, _, last = db.get_session(sid).messages
    assert (first["turn_id"], first["turn_checkpoint_id"]) == ("m1", "cp-1")
    assert "turn_checkpoint_id" not in last


def _session_id_from(resp) -> str:
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            event = json.loads(line.removeprefix("data: "))
            if event.get("session_id"):
                return event["session_id"]
    raise AssertionError(resp.text)


def test_two_turns_share_one_checkpoint_thread(client, db, monkeypatch):
    """端到端：真实 EasyAgent + 官方 SQLite saver（需 sqlite-saver extra）。"""
    sqlite = pytest.importorskip("langgraph.checkpoint.sqlite")
    cfg = agent_manager.get_agent_config()["config"]
    monkeypatch.setattr(cfg.agent, "session_thread", True)

    first = client.post("/agent/chat/stream", json={"message": "第一问", "message_id": "m1"})
    sid = _session_id_from(first)
    second = client.post("/agent/chat/stream", json={"message": "第二问", "session_id": sid, "message_id": "m2"})
    assert first.status_code == second.status_code == 200

    with sqlite.SqliteSaver.from_conn_string(os.environ["EASY_CHECKPOINT_SQLITE_PATH"]) as saver:
        threads = {c.config["configurable"]["thread_id"] for c in saver.list(None)}
        # messages 是 DeltaChannel（checkpoint 只存增量），要经图的 get_state 重建
        reader = create_deep_agent(model=_Silent(), checkpointer=saver)
        state = reader.get_state({"configurable": {"thread_id": sid}})

    assert threads == {sid}  # 没有 {session_id}-{message_id} 线程
    humans = [m.content for m in state.values["messages"] if m.type == "human"]
    assert humans == ["第一问", "第二问"]  # 第二轮只输入新消息，历史没有重复
    user_rows = [m for m in db.get_session(sid).messages if m["role"] == "user"]
    assert user_rows[1]["turn_id"] == "m2" and user_rows[1]["turn_checkpoint_id"]
