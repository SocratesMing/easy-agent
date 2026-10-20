"""DA-02：多个待审批中断时按 interrupt_id 作答。

并行的 ``task`` 子代理各自触发 HITL 时，图里同时挂着多个中断。改造前：
``approval_required`` 只带第一个中断，恢复时却把同一组决策套给所有中断——
用户点一次「批准」，界面上没出现过的操作也被批准了。
"""

from __future__ import annotations

import itertools
import json
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from easy_agent.services.hitl_state import (
    HitlDecisionError,
    HitlInterrupt,
    pending_hitl_interrupts,
    plan_resume,
)
from easy_agent.services.streaming.projector import (
    decision_status_by_interrupt,
    mark_hitl_pending_interrupts,
    match_action_tool_call_ids,
)

APPROVE_REJECT = ["approve", "reject"]


def _interrupt(interrupt_id, *names):
    return HitlInterrupt(
        interrupt_id=interrupt_id,
        action_requests=[{"name": n, "args": {"i": i}} for i, n in enumerate(names)],
        review_configs=[{"action_name": n, "allowed_decisions": APPROVE_REJECT} for n in names],
    )


# ── plan_resume：纯函数 ───────────────────────────────────────────────────


def test_tagged_decisions_answer_only_their_interrupt():
    interrupts = [_interrupt("A", "delete"), _interrupt("B", "create_scheduled_task")]
    plan = plan_resume(interrupts, [{"type": "reject", "message": "不要删", "interrupt_id": "B"}])

    assert plan.resume == {"B": {"decisions": [{"type": "reject", "message": "不要删"}]}}
    assert set(plan.answered) == {"B"}
    assert not plan.legacy


def test_single_tagged_decision_covers_its_whole_batch():
    plan = plan_resume([_interrupt("A", "delete", "delete")], [{"type": "approve", "interrupt_id": "A"}])
    assert plan.resume == {"A": {"decisions": [{"type": "approve"}, {"type": "approve"}]}}


def test_tagged_decisions_follow_action_index_order():
    plan = plan_resume(
        [_interrupt("A", "delete", "execute")],
        [
            {"type": "reject", "interrupt_id": "A", "action_index": 1},
            {"type": "approve", "interrupt_id": "A", "action_index": 0},
        ],
    )
    approve, reject = plan.resume["A"]["decisions"]
    assert approve == {"type": "approve"}
    # 没带说明的拒绝由后端按工具补一句（DA-15）
    assert reject["type"] == "reject" and "执行这条命令" in reject["message"]


def test_legacy_decision_never_reaches_interrupts_the_old_ui_did_not_show():
    """回归：旧前端只展示第一个中断，单个决策只能作用于它，其余保持挂起。"""
    interrupts = [_interrupt("A", "delete"), _interrupt("B", "create_scheduled_task")]
    plan = plan_resume(interrupts, [{"type": "approve"}])

    assert plan.resume == {"A": {"decisions": [{"type": "approve"}]}}
    assert "B" not in plan.resume
    assert plan.legacy


def test_legacy_single_interrupt_keeps_the_flat_resume_shape():
    plan = plan_resume([_interrupt("A", "execute", "execute")], [{"type": "approve"}])
    assert plan.resume == {"decisions": [{"type": "approve"}, {"type": "approve"}]}


def test_no_pending_interrupt_passes_decisions_through_unchanged():
    plan = plan_resume([], [{"type": "approve", "action_index": 0}])
    assert plan.resume == {"decisions": [{"type": "approve"}]}


@pytest.mark.parametrize(
    "decisions, message",
    [
        ([], "非空"),
        ([{"type": "approve", "interrupt_id": "ghost"}], "不在待审批列表"),
        ([{"type": "approve", "interrupt_id": "A"}, {"type": "approve"}], "要么全部"),
        ([{"type": "edit", "interrupt_id": "A"}], "不允许决策"),
        ([{"type": "maybe"}], "未知的审批决策类型"),
        ([{"type": "approve"}, {"type": "approve"}, {"type": "approve"}], "收到 3 个决策"),
    ],
)
def test_invalid_decisions_are_rejected_before_resuming(decisions, message):
    with pytest.raises(HitlDecisionError, match=message):
        plan_resume([_interrupt("A", "delete", "delete")], decisions)


def test_pending_hitl_interrupts_ignores_non_hitl_payloads():
    state = SimpleNamespace(
        tasks=[
            SimpleNamespace(interrupts=[SimpleNamespace(id="A", value={"action_requests": [{"name": "delete"}]})]),
            SimpleNamespace(interrupts=[SimpleNamespace(id="E", value={"requests": [{"key": "date"}]})]),
        ]
    )
    assert [i.interrupt_id for i in pending_hitl_interrupts(state)] == ["A"]


# ── 投影：载荷与 tool_call_id 对齐 ───────────────────────────────────────


def test_tool_call_ids_match_by_name_and_args_not_by_position():
    """回归：同名的 execute 里只有删除命令需要审批，不能按下标对到 ls 上。"""
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "execute", "args": {"command": "ls"}, "id": "call-ls"},
            {"name": "execute", "args": {"command": "rm a.txt"}, "id": "call-rm"},
        ],
    )
    actions = [{"name": "execute", "args": {"command": "rm a.txt"}}]
    assert match_action_tool_call_ids([last_ai], actions) == ["call-rm"]


def test_payload_carries_every_interrupt_and_marks_only_matched_blocks():
    last_ai = AIMessage(
        content="",
        tool_calls=[{"name": "task", "args": {"description": "x", "subagent_type": "gp"}, "id": "t1"}],
    )
    main = HitlInterrupt(
        "A",
        [{"name": "task", "args": {"description": "x", "subagent_type": "gp"}, "description": "d"}],
        [{"action_name": "task", "allowed_decisions": APPROVE_REJECT}],
    )
    nested = HitlInterrupt(
        "B",
        [{"name": "delete", "args": {"file_path": "/a.txt"}, "description": "d"}],
        [{"action_name": "delete", "allowed_decisions": APPROVE_REJECT}],
    )
    partial = {
        "tool_calls": [],
        "blocks": [{"type": "tool_call", "tool_call_id": "t1", "tool_name": "task"}],
    }

    payload = mark_hitl_pending_interrupts(partial, [main, nested], [last_ai], "s1-m1")

    assert [(p["interrupt_id"], p["action_index"], p["tool_call_id"]) for p in payload] == [
        ("A", 0, "t1"),
        ("B", 0, ""),  # 子代理内部的操作在主代理消息里没有对应调用 → 前端独立面板
    ]
    assert partial["blocks"][0]["pending_approval"] is True
    assert partial["pending_approval"] == {"thread_id": "s1-m1", "action_requests": payload}


def test_decision_status_only_covers_answered_interrupts():
    pending = [
        {"interrupt_id": "A", "action_index": 0, "tool_call_id": "c1"},
        {"interrupt_id": "B", "action_index": 0, "tool_call_id": "c2"},
    ]
    assert decision_status_by_interrupt({"A": [{"type": "reject"}]}, pending) == {"c1": "rejected"}
    # 改造前落库的记录没有 interrupt_id：返回空，调用方退回旧口径
    assert decision_status_by_interrupt({"A": [{"type": "approve"}]}, [{"tool_call_id": "c1"}]) == {}


# ── 真实 deepagents：并行子代理各自触发 HITL ─────────────────────────────


class _ToolCallingFake(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                id=message.id,
                tool_call_chunks=[
                    {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                    for i, tc in enumerate(message.tool_calls or [])
                ],
            )
        )


def _parallel_subagent_graph():
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langgraph.checkpoint.memory import MemorySaver

    main = _ToolCallingFake(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "task", "args": {"description": "delete a", "subagent_type": "cleaner"}, "id": "t1"},
                        {"name": "task", "args": {"description": "delete b", "subagent_type": "cleaner"}, "id": "t2"},
                    ],
                ),
                AIMessage(content="all done"),
            ]
        )
    )
    sub = _ToolCallingFake(
        messages=itertools.chain(
            [
                AIMessage(content="", tool_calls=[{"name": "delete", "args": {"file_path": "/a.txt"}, "id": "d1"}]),
                AIMessage(content="", tool_calls=[{"name": "delete", "args": {"file_path": "/b.txt"}, "id": "d2"}]),
            ],
            itertools.repeat(AIMessage(content="sub done")),
        )
    )
    return create_deep_agent(
        model=main,
        backend=StateBackend(),
        checkpointer=MemorySaver(),
        interrupt_on={"delete": {"allowed_decisions": APPROVE_REJECT}},
        subagents=[{"name": "cleaner", "description": "deletes", "system_prompt": "delete", "model": sub, "tools": []}],
    )


async def _drain(graph, payload, config):
    async for _ in graph.astream(payload, stream_mode=["messages", "updates"], config=config):
        pass


async def test_parallel_subagent_approvals_are_answered_one_interrupt_at_a_time():
    from langgraph.types import Command

    from easy_agent.services.stream_processor import StreamProcessor
    from easy_agent.services.streaming.orchestrator import (
        detect_hitl_interruption,
        plan_resume_for_agent,
    )

    graph = _parallel_subagent_graph()
    agent = SimpleNamespace(agent=graph)
    config = {"configurable": {"thread_id": "s1-m1"}}
    await _drain(graph, {"messages": [HumanMessage(content="clean up")]}, config)

    interruption = await detect_hitl_interruption(
        agent=agent, stream_config=config, proc=StreamProcessor(sid="t"), elapsed_time=0, thread_id="s1-m1"
    )
    shown = interruption.action_requests_payload
    assert sorted(a["arguments"]["file_path"] for a in shown) == ["/a.txt", "/b.txt"]
    assert len({a["interrupt_id"] for a in shown}) == 2
    assert all(a["tool_call_id"] == "" for a in shown)  # 子代理内部操作，主代理消息里无对应调用

    first, second = shown
    plan = await plan_resume_for_agent(
        agent=agent,
        stream_config=config,
        decisions=[{"type": "approve", "interrupt_id": first["interrupt_id"]}],
    )
    await _drain(graph, Command(resume=plan.resume), config)

    remaining = pending_hitl_interrupts(await graph.aget_state(config))
    assert [i.interrupt_id for i in remaining] == [second["interrupt_id"]]

    plan = await plan_resume_for_agent(
        agent=agent,
        stream_config=config,
        decisions=[{"type": "reject", "message": "no", "interrupt_id": second["interrupt_id"]}],
    )
    await _drain(graph, Command(resume=plan.resume), config)
    state = await graph.aget_state(config)
    assert not state.next
    assert state.values["messages"][-1].content == "all done"


async def test_legacy_untagged_decision_leaves_other_interrupts_pending():
    """回归（改造前此用例失败）：旧前端的单个「批准」不得连带批准第二个子代理的删除。"""
    from easy_agent.services.streaming.orchestrator import build_resume_command

    graph = _parallel_subagent_graph()
    agent = SimpleNamespace(agent=graph)
    config = {"configurable": {"thread_id": "s1-m2"}}
    await _drain(graph, {"messages": [HumanMessage(content="clean up")]}, config)
    before = (await graph.aget_state(config)).tasks
    before_ids = [i.id for t in before for i in t.interrupts]
    assert len(before_ids) == 2

    command = await build_resume_command(
        agent=agent, stream_config=config, decisions=[{"type": "approve"}], sid="t"
    )
    await _drain(graph, command, config)

    after = (await graph.aget_state(config)).tasks
    assert [i.id for t in after for i in t.interrupts] == [before_ids[1]]


# ── 恢复流：只给本次作答的中断记审批状态 ─────────────────────────────────


class _FakeDB:
    def __init__(self, messages):
        self.session = SimpleNamespace(messages=messages)
        self.persisted = []

    def get_session(self, session_id):
        return self.session

    def update_last_assistant_message(self, session_id, message):
        self.persisted.append(message)
        self.session.messages[-1] = message


class _StaticGraph:
    """恢复后什么都不产出、两个中断仍挂着（模拟只答了其中一个、另一个继续挂起）。"""

    def __init__(self, state):
        self.state = state
        self.payloads = []

    async def astream(self, payload, **kwargs):
        self.payloads.append(payload)
        if False:  # pragma: no cover - async generator
            yield None

    async def aget_state(self, config):
        return self.state


async def test_resume_stamps_only_the_answered_interrupt(monkeypatch):
    from easy_agent.services.streaming import orchestrator

    monkeypatch.setattr(orchestrator, "get_agent_config", lambda: None)
    actions = [
        {"tool_call_id": "c1", "tool_name": "delete", "arguments": {"file_path": "/main.txt"},
         "interrupt_id": "A", "action_index": 0},
        {"tool_call_id": "", "tool_name": "delete", "arguments": {"file_path": "/sub.txt"},
         "interrupt_id": "B", "action_index": 0},
    ]
    messages = [
        {"role": "user", "content": "clean"},
        {
            "role": "assistant",
            "content": "",
            "blocks": [{"type": "tool_call", "tool_name": "delete", "tool_call_id": "c1",
                        "arguments": {"file_path": "/main.txt"}, "result": "", "duration": None,
                        "step": 1, "approval_status": "pending", "pending_approval": True}],
            "tool_calls": [],
            "pending_approval": {"thread_id": "s1-m1", "action_requests": actions},
            "usage": {"step_count": 1},
        },
    ]
    state = SimpleNamespace(
        next=("tools",),
        values={"messages": [AIMessage(content="", tool_calls=[
            {"name": "delete", "args": {"file_path": "/main.txt"}, "id": "c1"}])]},
        tasks=[
            SimpleNamespace(interrupts=[SimpleNamespace(id="A", value={
                "action_requests": [{"name": "delete", "args": {"file_path": "/main.txt"}}],
                "review_configs": [{"action_name": "delete", "allowed_decisions": APPROVE_REJECT}]})]),
            SimpleNamespace(interrupts=[SimpleNamespace(id="B", value={
                "action_requests": [{"name": "delete", "args": {"file_path": "/sub.txt"}}],
                "review_configs": [{"action_name": "delete", "allowed_decisions": APPROVE_REJECT}]})]),
        ],
    )
    graph = _StaticGraph(state)
    agent = SimpleNamespace(agent=graph, max_steps=5, model=None, model_name="m", workspace_dir=None)
    db = _FakeDB(messages)

    frames = [
        frame
        async for frame in orchestrator.resume_stream_generator(
            db, agent, "s1", "s1-m1", [{"type": "approve", "interrupt_id": "B"}], message_id="m1"
        )
    ]

    assert graph.payloads[0].resume == {"B": {"decisions": [{"type": "approve"}]}}
    assert any('"approval_required"' in frame for frame in frames)
    main_block = db.session.messages[-1]["blocks"][0]
    assert main_block["approval_status"] == "pending"  # A 没被回答，不能被记成已批准
    assert main_block.get("pending_approval") is True
