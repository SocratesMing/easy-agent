"""DA-03 P1：一个会话一个 thread（``agent.session_thread``）。

改造前每条消息一个 ``{session_id}-{message_id}`` thread、每轮从 DB 重建全量历史：
越过摘要阈值后每轮都重新摘要，摘要状态、todos 不跨轮。开关打开后 thread_id = session_id，
每轮只输入本轮消息（加上需要补齐的内容）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware, turn_input
from easy_agent.models.api import ChatRequest
from easy_agent.services.session_thread import (
    INTERRUPTED_SUFFIX,
    StaleApprovalError,
    abandon_pending_approval,
    approval_handle,
    handle_turn_id,
    plan_turn,
    resolve_resume_thread,
    session_thread_enabled,
)
from easy_agent.services.streaming import chat_stream_generator, resume_stream_generator


class Recorder(BaseChatModel):
    """记录主代理每次调用的输入；最后一条用户消息含 WRITE 时发起需要审批的 write_file。"""

    inputs: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.inputs.append(list(messages))
        last = messages[-1]
        if last.type == "human" and "WRITE" in str(last.content):
            reply = AIMessage(
                content="",
                tool_calls=[{"name": "write_file", "args": {"file_path": "/x.txt", "content": "x"}, "id": "w1"}],
            )
        else:
            reply = AIMessage(content=f"answer:{str(last.content)[:20]}")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        # 真实模型是流式的：StreamProcessor 只从流式 token 累计正文（DB 投影的来源）
        reply = self._generate(messages, stop, run_manager, **kwargs).generations[0].message
        chunk = AIMessageChunk(
            content=reply.content,
            tool_call_chunks=[
                {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                for i, tc in enumerate(reply.tool_calls)
            ],
        )
        yield ChatGenerationChunk(message=chunk)

    def humans(self, call: int = -1) -> list[str]:
        return [str(m.content) for m in self.inputs[call] if m.type == "human"]


class FakeDB:
    """chat_stream_generator / resume_stream_generator 用到的最小 DB 接口。"""

    def __init__(self):
        self.messages: list[dict] = []

    def get_session(self, session_id):
        return SimpleNamespace(messages=self.messages)

    def add_user(self, content: str, turn_id: str | None = None):
        message = {"role": "user", "content": content}
        if turn_id:
            message["turn_id"] = turn_id
        self.messages.append(message)

    def update_last_assistant_message(self, session_id, message):
        if self.messages and self.messages[-1].get("role") == "assistant":
            self.messages[-1] = message
        else:
            self.messages.append(message)

    def __getattr__(self, name):  # record_tool_call / record_thinking / update_session_todos ...
        return lambda *args, **kwargs: None


def _agent(tmp_path, *, session_thread=True, interrupt_on=None):
    model = Recorder()
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    graph = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[
            TurnAwareSummarizationMiddleware(
                model, backend=backend, trigger=("tokens", 1_000_000), keep=("messages", 4)
            )
        ],
        checkpointer=InMemorySaver(),
        interrupt_on=interrupt_on,
    )
    agent = SimpleNamespace(
        agent=graph,
        config=SimpleNamespace(
            agent=SimpleNamespace(session_thread=session_thread),
            llm=SimpleNamespace(provider="openai"),
        ),
        workspace_dir=None,
        workspace_virtual_path="/workspace",
        max_steps=10,
        session_context=None,
        memory_file=None,
        long_term_memory_file=None,
    )
    return agent, model


async def _chat(agent, db, text, turn_id, session_id="s1"):
    db.add_user(text, turn_id)
    frames = [
        frame
        async for frame in chat_stream_generator(
            ChatRequest(message=text), db, agent, session_id, turn_id, "tester"
        )
    ]
    return [json.loads(f.removeprefix("data: ").strip()) for f in frames]


async def _thread_humans(agent, thread_id="s1"):
    state = await agent.agent.aget_state({"configurable": {"thread_id": thread_id}})
    return [str(m.content) for m in state.values.get("messages", []) if m.type == "human"]


# ── 纯函数 ────────────────────────────────────────────────────────────


def test_approval_handle_round_trip():
    assert approval_handle("s1", "m1") == "s1-m1"
    assert handle_turn_id("s1", "s1-m1") == "m1"
    assert handle_turn_id("s1", "s2-m1") is None
    assert handle_turn_id("s1", "s1-") is None
    assert handle_turn_id("s1", None) is None


def test_session_thread_flag_accepts_config_or_global_dict():
    on = SimpleNamespace(agent=SimpleNamespace(session_thread=True))
    assert session_thread_enabled(on) is True
    assert session_thread_enabled({"config": on}) is True
    assert session_thread_enabled(None) is False
    assert session_thread_enabled(SimpleNamespace(agent=SimpleNamespace())) is False


def test_abandon_pending_approval_marks_it_rejected():
    db = FakeDB()
    db.messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "pending_approval": {"thread_id": "s1-m1"},
            "tool_calls": [{"tool_call_id": "a", "approval_status": "pending"}],
            "blocks": [{"type": "tool_call", "tool_call_id": "a", "approval_status": "pending", "pending_approval": True}],
        },
    ]
    assert abandon_pending_approval(db, "s1") is True
    last = db.messages[-1]
    assert "pending_approval" not in last
    assert last["tool_calls"][0]["approval_status"] == "rejected"
    assert last["blocks"][0] == {"type": "tool_call", "tool_call_id": "a", "approval_status": "rejected"}
    assert abandon_pending_approval(db, "s1") is False


# ── plan_turn（真实图 + InMemorySaver） ───────────────────────────────


async def test_flag_off_keeps_per_message_threads(tmp_path):
    agent, _ = _agent(tmp_path, session_thread=False)
    history = [{"role": "user", "content": "q0", "turn_id": "m0"}, {"role": "assistant", "content": "a0"}]

    plan = await plan_turn(agent, session_id="s1", turn_id="m1", history=history, raw="q1")

    assert (plan.thread_id, plan.mode) == ("s1-m1", "per-message")
    assert [m.content for m in plan.messages] == ["q0", "a0", "q1"]


async def test_empty_thread_is_hydrated_from_the_db(tmp_path):
    agent, _ = _agent(tmp_path)
    history = [{"role": "user", "content": "q0", "turn_id": "m0"}, {"role": "assistant", "content": "a0"}]

    plan = await plan_turn(agent, session_id="s1", turn_id="m1", history=history, raw="q1")

    assert (plan.thread_id, plan.mode, plan.checkpoint_id) == ("s1", "hydrate", None)
    assert [m.content for m in plan.messages] == ["q0", "a0", "q1"]
    assert plan.messages[-1].id == "m1"


async def test_two_turns_share_one_thread_and_only_the_new_message_is_input(tmp_path):
    agent, model = _agent(tmp_path)
    db = FakeDB()

    await _chat(agent, db, "q1", "m1")
    plan = await plan_turn(agent, session_id="s1", turn_id="m2", history=db.messages, raw="q2")
    assert plan.mode == "continue" and plan.checkpoint_id
    assert [m.content for m in plan.messages] == ["q2"]

    await _chat(agent, db, "q2", "m2")

    assert model.humans(-1) == ["q1", "q2"]  # 模型从 thread 看到完整历史
    assert await _thread_humans(agent) == ["q1", "q2"]  # 没有因为重复输入历史而翻倍
    state = await agent.agent.aget_state({"configurable": {"thread_id": "s1-m2"}})
    assert not state.values  # 不再有每条消息一个的 thread


async def test_db_turns_missing_from_the_thread_are_caught_up(tmp_path):
    """定时任务、开关关闭期间的对话等写进 DB 却没进 thread 的轮次，补进本轮输入。"""
    agent, model = _agent(tmp_path)
    db = FakeDB()
    await _chat(agent, db, "q1", "m1")
    db.messages += [{"role": "user", "content": "定时任务提示"}, {"role": "assistant", "content": "日报"}]

    plan = await plan_turn(agent, session_id="s1", turn_id="m2", history=db.messages, raw="q2")

    assert plan.mode == "continue" and plan.caught_up_rows == 2
    assert [m.content for m in plan.messages] == ["定时任务提示", "日报", "q2"]


async def test_interrupted_reply_tail_is_restored(tmp_path):
    """上一轮取消在模型输出途中：用户看到的半截回复 thread 里没有，补成一条 AIMessage。"""
    agent, _ = _agent(tmp_path)
    db = FakeDB()
    await _chat(agent, db, "q1", "m1")
    db.messages[-1]["content"] += " and more text the user saw"

    plan = await plan_turn(agent, session_id="s1", turn_id="m2", history=db.messages, raw="q2")

    assert plan.restored_tail
    assert plan.messages[0].content == " and more text the user saw" + INTERRUPTED_SUFFIX
    assert plan.messages[-1].content == "q2"

    # DB 正文与 thread 一致时不补
    db.messages[-1]["content"] = "answer:q1"
    plan = await plan_turn(agent, session_id="s1", turn_id="m2", history=db.messages, raw="q2")
    assert not plan.restored_tail and [m.content for m in plan.messages] == ["q2"]


async def test_thread_that_no_longer_matches_the_db_is_rehydrated(tmp_path):
    agent, _ = _agent(tmp_path)
    db = FakeDB()
    await _chat(agent, db, "q1", "m1")
    history = [{"role": "user", "content": "other", "turn_id": "x"}, {"role": "assistant", "content": "a"}]

    plan = await plan_turn(agent, session_id="s1", turn_id="m2", history=history, raw="q2")

    assert plan.mode == "rehydrate"
    assert [m.content for m in plan.messages] == ["other", "a", "q2"]
    assert await _thread_humans(agent) == []  # 旧 thread 已删除，重新水合时不会叠加


# ── HITL：审批句柄 ─────────────────────────────────────────────────────


async def test_approval_on_a_session_thread_uses_the_handle_and_resumes_the_session_thread(tmp_path):
    agent, _ = _agent(tmp_path, interrupt_on={"write_file": True})
    db = FakeDB()

    events = await _chat(agent, db, "please WRITE", "m1")
    approval = events[-1]
    assert approval["type"] == "approval_required"
    assert approval["thread_id"] == "s1-m1"  # 前端句柄格式不变
    assert db.messages[-1]["pending_approval"]["thread_id"] == "s1-m1"

    thread = await resolve_resume_thread(agent, session_id="s1", handle="s1-m1", last_message=db.messages[-1])
    assert thread == "s1"

    frames = [
        f
        async for f in resume_stream_generator(
            db, agent, "s1", thread, [{"type": "approve"}], message_id="m1", handle="s1-m1"
        )
    ]
    assert json.loads(frames[-1].removeprefix("data: ").strip())["type"] == "done"
    assert (tmp_path / "x.txt").exists()


async def test_new_message_makes_the_old_approval_stale(tmp_path):
    agent, model = _agent(tmp_path, interrupt_on={"write_file": True})
    db = FakeDB()
    await _chat(agent, db, "please WRITE", "m1")

    await _chat(agent, db, "never mind", "m2")

    patched = [m for m in model.inputs[-1] if m.type == "tool" and m.tool_call_id == "w1"]
    assert patched and "did not complete" in patched[0].content
    assert not (tmp_path / "x.txt").exists()
    with pytest.raises(StaleApprovalError):
        await resolve_resume_thread(agent, session_id="s1", handle="s1-m1", last_message=db.messages[-1])


async def test_pending_approval_from_before_the_upgrade_still_resumes(tmp_path):
    """兼容期：升级前挂起在旧的每消息 thread 上、且仍是会话最后一条的审批。"""
    agent, _ = _agent(tmp_path, interrupt_on={"write_file": True})
    legacy = {"configurable": {"thread_id": "s1-m0"}}
    await agent.agent.ainvoke(turn_input([HumanMessage("please WRITE", id="m0")], message_id="m0", rendered="please WRITE"), legacy)
    pending = {"role": "assistant", "content": "", "pending_approval": {"thread_id": "s1-m0"}}

    assert await resolve_resume_thread(agent, session_id="s1", handle="s1-m0", last_message=pending) == "s1-m0"
    with pytest.raises(StaleApprovalError):
        await resolve_resume_thread(agent, session_id="s1", handle="s1-m0", last_message={"role": "user", "content": "x"})


async def _seed_thread(agent, thread_id):
    """恢复只针对已有 checkpoint 的 thread（空 thread 会被拒绝，见 tests/services/test_per_pod_approvals.py）。"""
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    await agent.agent.checkpointer.aput(config, empty_checkpoint(), {}, {})


async def test_flag_off_resume_uses_the_handle_as_thread(tmp_path):
    agent, _ = _agent(tmp_path, session_thread=False)
    await _seed_thread(agent, "s1-m1")
    assert await resolve_resume_thread(agent, session_id="s1", handle="s1-m1", last_message=None) == "s1-m1"


async def test_legacy_snapshot_saver_keeps_per_message_threads(tmp_path):
    """MySQL 回退的 legacy 快照 saver 每次写入都 pickle 全量，会话 thread 下不启用。"""
    from easy_agent.persistence.checkpoints import PersistentMemorySaver

    agent, _ = _agent(tmp_path)
    agent.agent.checkpointer = PersistentMemorySaver("s1", db=None)

    plan = await plan_turn(agent, session_id="s1", turn_id="m1", history=[], raw="q1")
    assert (plan.thread_id, plan.mode) == ("s1-m1", "per-message")
    await _seed_thread(agent, "s1-m1")
    assert await resolve_resume_thread(agent, session_id="s1", handle="s1-m1", last_message=None) == "s1-m1"
