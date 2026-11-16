"""T5：chat / resume 共用流主循环（``_run_stream``）的行为锁。

两个公开生成器现在只负责准备输入与元数据；主循环、HITL 收尾、取消/异常兜底都在
``_run_stream``。本文件锁住合并后的三条语义：

- 提前关闭公开生成器时，内部流在计费身份复位前关闭；
- 流中异常仍走统一兜底：chat 修正 thinking / 工具状态后下发 error 帧；
- resume 的取消兜底仍用 ``with_approval=True`` 保存部分回复（审批状态不丢）。
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from easy_agent.models.api import ChatRequest
from easy_agent.services.streaming import orchestrator
from easy_agent.services.streaming.outcome import StreamOutcome
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


class _TimingOutGraph(FakeGraph):
    """先吐正文再抛 asyncio.TimeoutError：模拟模型流式静默（StreamChunkTimeoutError）。"""

    async def astream(self, payload, stream_mode=None, config=None, **kwargs):
        yield ("messages", [{"type": "content", "content": "前半段", "step": 1}])
        raise asyncio.TimeoutError("No streaming chunk received within 120.0 seconds")


class _FakeStreamChunkTimeoutError(Exception):
    """类名与 ``langchain_openai.StreamChunkTimeoutError`` 一致（判定按类名，不依赖导入）。"""


_FakeStreamChunkTimeoutError.__name__ = "StreamChunkTimeoutError"


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


async def test_chat_prep_error_emits_error_frame_and_writes_nothing():
    """准备阶段异常：补统一文案的 error 帧，不落任何 assistant 消息。"""
    db = _FailingDB()
    outcome = StreamOutcome()
    frames = await _frames(
        orchestrator.chat_stream_generator(
            ChatRequest(message="hi"), db, _agent(FakeGraph([])), "s1", "m1", "tester",
            outcome=outcome,
        )
    )
    events = _events(frames)

    assert _types(events) == ["start", "error"]
    assert events[-1]["content"] == "处理失败: db down"
    assert db.persisted == []
    assert outcome.error == events[-1]["content"]


@pytest.mark.parametrize("resume", [False, True])
async def test_closing_public_generator_closes_inner_stream_with_identity(monkeypatch, resume):
    from easy_agent.utils.usage_context import UsageIdentity, get_usage_identity, usage_scope

    observed = []
    identity = UsageIdentity("real-id", "s1")

    async def resolve(*args):
        return identity

    async def stream(run, payload):
        try:
            observed.append(("stream", get_usage_identity()))
            yield run.emitter.content("partial")
        finally:
            observed.append(("closed", get_usage_identity()))

    monkeypatch.setattr(orchestrator, "resolve_usage_identity", resolve)
    monkeypatch.setattr(orchestrator, "_run_stream", stream)
    graph = FakeGraph([], states=[_hitl_state()] if resume else [])
    if resume:
        generator = orchestrator.resume_stream_generator(
            FakeDB(_resume_messages()), _agent(graph), "s1", "s1-m1", [{"type": "approve"}],
            session_logger=FakeLogger(), message_id="m1",
        )
    else:
        generator = orchestrator.chat_stream_generator(
            ChatRequest(message="hi"), FakeDB(), _agent(graph), "s1", "m1", "tester",
        )
    with usage_scope("parent"):
        assert _types(_events([await anext(generator)])) == ["start"]
        assert _types(_events([await anext(generator)])) == ["content"]
        await generator.aclose()
        assert get_usage_identity() == UsageIdentity("parent")
        assert observed == [("stream", identity), ("closed", identity)]


async def test_chat_identity_survives_client_disconnect_until_background_finishes(monkeypatch):
    from easy_agent.api import chat
    from easy_agent.utils.usage_context import UsageIdentity, get_usage_identity, usage_scope

    identity = UsageIdentity("real-id", "usage-detached")
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []
    registered = []

    async def register(session_id, task):
        registered.append(task)

    async def unregister(session_id, task):
        observed.append(("after-stream", get_usage_identity()))

    async def stream(run, payload):
        observed.append(("before-disconnect", get_usage_identity()))
        entered.set()
        await release.wait()
        observed.append(("after-disconnect", get_usage_identity()))
        yield run.emitter.content("finished")

    async def unexpected_lookup(*args):
        pytest.fail("An explicitly supplied identity must not be resolved again")

    monkeypatch.setattr(orchestrator, "resolve_usage_identity", unexpected_lookup)
    monkeypatch.setattr(orchestrator, "_run_stream", stream)
    monkeypatch.setattr(chat, "aregister_stream_task", register)
    monkeypatch.setattr(chat, "aunregister_stream_task", unregister)
    monkeypatch.setattr(chat, "is_session_streaming", lambda session_id: False)
    generator = orchestrator.chat_stream_generator(
        ChatRequest(message="hi"), FakeDB(), _agent(FakeGraph([])),
        identity.session_id, "m1", "tester", usage_identity=identity,
    )
    response = chat._detached_event_stream(generator, identity.session_id, "usage")
    try:
        with usage_scope("unrelated-parent"):
            assert _types(_events([await anext(response)])) == ["start"]
            await asyncio.wait_for(entered.wait(), 5)
            # Closing the client response leaves the registered worker running.
            await response.aclose()
            assert not registered[0].done()
            assert get_usage_identity() == UsageIdentity("unrelated-parent")
        release.set()
        await asyncio.wait_for(registered[0], 5)
        assert observed == [
            ("before-disconnect", identity), ("after-disconnect", identity),
            ("after-stream", UsageIdentity("unrelated-parent")),
        ]
        assert chat._session_stream_hubs[identity.session_id].done
        assert get_usage_identity() is None
    finally:
        release.set()
        await response.aclose()
        if registered:
            await asyncio.wait_for(registered[0], 5)
        chat._session_stream_hubs.pop(identity.session_id, None)


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
    assert "error" not in db.persisted[-1]
    # 其他异常不带 code：帧结构与改动前一致（validate 也不会因它报错）
    assert "code" not in events[-1]
    # chat 异常兜底也保存半成品（补齐「取消 / HITL 恢复都会存、普通对话不存」的历史
    # 差异，否则刷新会被更短的 DB 版本覆盖），并且与超时一样带上常驻兜底文案：
    # 只发帧不落库的话，提示活不过流结束后的那次历史回填。
    assert len(db.persisted) == 1
    assert db.persisted[-1]["fallback_note"] == orchestrator.ERROR_FALLBACK_NOTE
    # 落库的是中性文案，原始异常只进日志与 error 帧（不写进聊天历史）
    assert "boom" not in db.persisted[-1]["fallback_note"]
    proc = FakeProc.instances[0]
    assert proc.is_in_thinking is False
    tool_block = proc.blocks[-1]
    assert tool_block["type"] == "tool_call"
    assert tool_block["success"] is False
    assert tool_block["result"] == "执行中断: boom"
    assert tool_block["duration"] == 0
    assert db.persisted[-1]["blocks"] == proc.blocks


async def test_chat_read_error_preserves_generated_content():
    class ReadErrorGraph(FakeGraph):
        async def astream(self, *args, **kwargs):
            yield ("messages", [{"type": "content", "content": "已完成部分分析"}])
            raise httpx.ReadError("")

    db = FakeDB()
    events = _events(await _frames(orchestrator.chat_stream_generator(
        ChatRequest(message="hi"), db, _agent(ReadErrorGraph([])), "s1", "m1", "tester"
    )))
    partial = db.persisted[-1]
    assert partial["content"] == "已完成部分分析"
    assert "模型连接中断" in events[-1]["content"]
    assert partial["fallback_note"] == orchestrator.ERROR_FALLBACK_NOTE
    assert "error" not in partial


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


def test_is_timeout_error_covers_sdk_timeouts_and_cause_chain():
    """超时判定：asyncio.TimeoutError 及其同义类、SDK 的按名匹配、以及被包装的原因链。"""
    assert orchestrator.is_timeout_error(asyncio.TimeoutError("silent"))
    assert orchestrator.is_timeout_error(_FakeStreamChunkTimeoutError("silent"))
    wrapped = RuntimeError("APIConnectionError")
    wrapped.__cause__ = asyncio.TimeoutError("inner")
    assert orchestrator.is_timeout_error(wrapped)
    assert not orchestrator.is_timeout_error(RuntimeError("boom"))
    assert not orchestrator.is_timeout_error(None)


async def test_timeout_error_emits_timeout_code_and_persists_fallback_note():
    """模型流式超时：error 帧带 code=timeout，半成品 + 兜底文案一起落库。

    顺序很关键——先落库再发帧，前端 ``syncSessionFromServer`` 整表替换时才不会
    把刚显示的兜底文案又抹掉。
    """
    db = FakeDB()
    frames = []
    async for frame in orchestrator.chat_stream_generator(
        ChatRequest(message="hi"), db, _agent(_TimingOutGraph([])), "s1", "m1", "tester"
    ):
        if _events([frame])[0]["type"] == "error":
            assert db.persisted[-1]["fallback_note"] == orchestrator.TIMEOUT_FALLBACK_NOTE
        frames.append(frame)
    events = _events(frames)

    assert _types(events)[-1] == "error"
    assert events[-1]["content"] == orchestrator.TIMEOUT_FALLBACK_NOTE
    assert events[-1]["code"] == "timeout"
    # 已生成正文 + 兜底文案都进库（extra_data，无需改表）
    partial = db.persisted[-1]
    assert partial["content"] == "前半段"
    assert partial["fallback_note"] == orchestrator.TIMEOUT_FALLBACK_NOTE
    assert "error" not in partial


@pytest.mark.parametrize("timed_out", [False, True])
async def test_resume_error_keeps_approval_status_and_matching_message(timed_out):
    """恢复流普通异常和超时都保留审批状态，并持久化各自的提示。"""
    graph = FakeGraph(
        [  # 先吐一段正文，随后抛超时
            [{"type": "content", "content": "续写", "step": 1}],
        ]
    )
    original_astream = graph.astream

    async def _astream(payload, stream_mode=None, config=None, **kwargs):
        async for batch in original_astream(payload, stream_mode, config, **kwargs):
            yield batch
        if timed_out:
            raise asyncio.TimeoutError("silent")
        raise RuntimeError("boom")

    graph.astream = _astream
    graph.states = [_hitl_state()]
    db = FakeDB(_resume_messages())
    outcome = StreamOutcome()

    frames = await _frames(
        orchestrator.resume_stream_generator(
            db,
            _agent(graph),
            "s1",
            "s1-m1",
            [{"type": "approve"}],
            session_logger=FakeLogger(),
            message_id="m1",
            outcome=outcome,
        )
    )
    events = _events(frames)

    assert _types(events)[-1] == "error"
    assert outcome.error == events[-1]["content"]
    partial = db.persisted[-1]
    if timed_out:
        assert events[-1]["code"] == "timeout"
        assert partial["fallback_note"] == events[-1]["content"] == orchestrator.TIMEOUT_FALLBACK_NOTE
        assert "error" not in partial
    else:
        assert "code" not in events[-1]
        assert events[-1]["content"] == "处理失败: boom"
        assert partial["fallback_note"] == orchestrator.ERROR_FALLBACK_NOTE
        assert "error" not in partial
    assert partial["tool_calls"][0]["approval_status"] == "approved"


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("timed_out", [False, True])
async def test_error_frame_survives_partial_persistence_failure(resume, timed_out, caplog):
    class BrokenDB(FakeDB):
        def update_last_assistant_message(self, session_id, message):
            raise RuntimeError("db write failed")

    graph = _TimingOutGraph([]) if timed_out else _ExplodingGraph([])
    db = BrokenDB(_resume_messages() if resume else None)
    if resume:
        graph.states = [_hitl_state()]
        stream = orchestrator.resume_stream_generator(
            db, _agent(graph), "s1", "s1-m1", [{"type": "approve"}],
            session_logger=FakeLogger(), message_id="m1",
        )
    else:
        stream = orchestrator.chat_stream_generator(
            ChatRequest(message="hi"), db, _agent(graph), "s1", "m1", "tester"
        )

    events = _events(await _frames(stream))

    assert events[-1]["type"] == "error"
    if timed_out:
        assert events[-1]["code"] == "timeout"
        assert events[-1]["content"] == orchestrator.TIMEOUT_FALLBACK_NOTE
    else:
        assert "code" not in events[-1]
        assert events[-1]["content"] == "处理失败: boom"
    assert "保存部分回复失败: db write failed" in caplog.text
