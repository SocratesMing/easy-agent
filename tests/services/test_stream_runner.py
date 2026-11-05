"""T5：chat / resume 共用流主循环（``_run_stream``）的行为锁。

两个公开生成器现在只负责准备输入与元数据；主循环、HITL 收尾、取消/异常兜底都在
``_run_stream``。本文件锁住合并后的三条语义：

- 生成器不再各写一份主循环 / 收尾 / 兜底（源码结构，防止重复代码长回来）；
- 流中异常仍走统一兜底：chat 修正 thinking / 工具状态后下发 error 帧；
- resume 的取消兜底仍用 ``with_approval=True`` 保存部分回复（审批状态不丢）。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time

import pytest

from easy_agent.models.api import ChatRequest
from easy_agent.services.streaming import orchestrator
from tests.services.test_stream_split_golden import (
    FakeDB,
    FakeGraph,
    FakeLogger,
    FakeProc,
    _agent,
    _hitl_state,
    _resume_messages,
)


class _FailingDB(FakeDB):
    """准备阶段查库失败（模拟 DB 抖动）。"""

    def get_session(self, session_id):
        raise RuntimeError("db down")


class _CancelledDB(FakeDB):
    """准备阶段就被取消（DB 调用抛 CancelledError）。"""

    def get_session(self, session_id):
        raise asyncio.CancelledError()


class _ThinkingProc(FakeProc):
    """把 thinking_start 记到状态上，用于验证异常兜底的 thinking_end 修正。"""

    def _apply(self, event):
        if event.get("type") == "thinking_start":
            self.is_in_thinking = True
            self.thinking_start_time = time.time()
        super()._apply(event)


class _ExplodingGraph(FakeGraph):
    """先吐两帧再抛异常：验证流中异常走 ``_stream_error_frames``。"""

    async def astream(self, payload, stream_mode=None, config=None, **kwargs):
        yield (
            "messages",
            [
                {"type": "thinking_start", "step": 1},
                {"type": "thinking", "content": "why", "step": 1},
                {"type": "tool_call", "tool_name": "execute", "tool_call_id": "c1",
                 "arguments": {}, "step": 1},
            ],
        )
        raise RuntimeError("boom")


class _StallingGraph(FakeGraph):
    """吐一帧后挂起，用于在流中间制造取消。"""

    def __init__(self):
        super().__init__([])
        self.started = asyncio.Event()

    async def astream(self, payload, stream_mode=None, config=None, **kwargs):
        yield ("messages", [{"type": "content", "content": "partial"}])
        self.started.set()
        await asyncio.sleep(3600)


@pytest.fixture(autouse=True)
def _patch_impl(monkeypatch):
    FakeProc.end_thinking = []
    FakeProc.instances = []
    monkeypatch.setattr(orchestrator, "StreamProcessor", FakeProc)
    monkeypatch.setattr(orchestrator, "get_agent_config", lambda: None)


async def _frames(generator):
    return [frame async for frame in generator]


def _events(frames):
    return [json.loads(frame.removeprefix("data: ").strip()) for frame in frames]


def _types(events):
    return [event["type"] for event in events]


def test_generators_delegate_the_shared_loop_to_run_stream():
    """两个生成器只准备输入，不再各自实现主循环 / 收尾 / 兜底。"""
    chat_src = inspect.getsource(orchestrator.chat_stream_generator)
    resume_src = inspect.getsource(orchestrator.resume_stream_generator)
    for source in (chat_src, resume_src):
        assert "_run_stream(" in source
        for marker in (
            "pump_stream(",
            "detect_hitl_interruption(",
            "finalize_stream(",
            "persist_partial(",
            "_end_thinking(",
        ):
            assert marker not in source, marker


async def test_chat_prep_error_emits_error_frame_and_writes_nothing():
    """准备阶段异常：补统一文案的 error 帧，不落任何 assistant 消息。"""
    db = _FailingDB()
    frames = await _frames(
        orchestrator.chat_stream_generator(
            ChatRequest(message="hi"), db, _agent(FakeGraph([])), "s1", "m1", "tester"
        )
    )
    events = _events(frames)

    assert _types(events) == ["start", "error"]
    assert events[-1]["content"] == "处理失败: db down"
    assert db.persisted == []


async def test_chat_passes_server_system_context_to_the_turn_input(monkeypatch):
    captured = {}

    async def fake_run(run, payload):
        captured.update(payload)
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr(orchestrator, "_run_stream", fake_run)
    await _frames(orchestrator.chat_stream_generator(
        ChatRequest(message="生成ppt"), FakeDB(), _agent(FakeGraph([])), "s1", "m1", "tester",
        system_context="本轮已通过服务端意图检查",
        scope_approved=True,
    ))
    assert captured["turn_context"]["system_context"] == "本轮已通过服务端意图检查"
    assert captured["turn_context"]["scope_approved"] is True
    assert captured["messages"][-1].content == "生成ppt"


async def test_chat_prep_cancel_propagates_without_empty_partial():
    """准备阶段被取消：没有可保存的内容，不写空 assistant 消息，取消继续上抛。"""
    db = _CancelledDB()
    with pytest.raises(asyncio.CancelledError):
        await _frames(
            orchestrator.chat_stream_generator(
                ChatRequest(message="hi"), db, _agent(FakeGraph([])), "s1", "m1", "tester"
            )
        )
    assert db.persisted == []


async def test_chat_stream_error_uses_shared_fallback(monkeypatch):
    """流中异常：thinking_end 修正 + 未完成工具标记「执行中断」+ error 帧。"""
    monkeypatch.setattr(orchestrator, "StreamProcessor", _ThinkingProc)
    db = FakeDB()
    frames = await _frames(
        orchestrator.chat_stream_generator(
            ChatRequest(message="hi"), db, _agent(_ExplodingGraph([])), "s1", "m1", "tester"
        )
    )
    events = _events(frames)

    assert _types(events) == [
        "start",
        "thinking_start",
        "thinking",
        "tool_call",
        "thinking_end",
        "error",
    ]
    assert events[-2]["type"] == "thinking_end"
    # FakeProc 默认 current_step=3；异常兜底沿用 proc 的当前步数（与合并前一致）
    assert events[-2]["step"] == 3
    assert events[-1]["content"] == "处理失败: boom"
    # chat 的异常兜底不落库（与合并前一致），只修正内存状态
    assert db.persisted == []
    proc = FakeProc.instances[0]
    assert proc.is_in_thinking is False
    tool_block = proc.blocks[-1]
    assert tool_block["type"] == "tool_call"
    assert tool_block["success"] is False
    assert tool_block["result"] == "执行中断: boom"
    assert tool_block["duration"] == 0


async def test_resume_cancel_saves_partial_reply_with_approval():
    """resume 取消：走共用兜底，但保存的部分回复带审批状态（with_approval=True）。"""
    graph = _StallingGraph()
    graph.states = [_hitl_state()]
    db = FakeDB(_resume_messages())

    task = asyncio.create_task(
        _frames(
            orchestrator.resume_stream_generator(
                db,
                _agent(graph),
                "s1",
                "s1-m1",
                [{"type": "approve"}],
                session_logger=FakeLogger(),
                message_id="m1",
            )
        )
    )
    await graph.started.wait()
    task.cancel()
    frames = await task
    events = _events(frames)

    assert _types(events) == ["start", "content", "error"]
    assert events[-1]["content"] == "请求被取消"
    # 取消时保存的部分回复：DB 载入的正文 + 流中新增正文
    partial = db.persisted[-1]
    assert partial["content"] == "deletingpartial"
    # 审批决策已应用到 blocks，with_approval=True 才会带回 approved
    assert partial["tool_calls"][0]["tool_call_id"] == "c1"
    assert partial["tool_calls"][0]["approval_status"] == "approved"
