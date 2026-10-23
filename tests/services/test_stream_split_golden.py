"""C2 streaming 拆分 golden 测试：事件序列与落库行为。

用假的 StreamProcessor / Agent / DB 驱动公开生成器 ``chat_stream_generator`` 与
``resume_stream_generator``，锁住：

- SSE 事件名、顺序与关键字段；
- 增量落库 / 最终消息 / HITL 部分消息 / 审批决策的调用与载荷。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from easy_agent.models.api import ChatRequest
from easy_agent.persistence.checkpoints import checkpoint_instance
from easy_agent.services.sse import format_sse
from easy_agent.services.streaming import orchestrator
from easy_agent.services.streaming.orchestrator import (
    chat_stream_generator,
    resume_stream_generator,
)


def _events(frames):
    return [json.loads(frame.removeprefix("data: ").strip()) for frame in frames]


def _types(events):
    return [event["type"] for event in events]


class FakeProc:
    """确定性 StreamProcessor 替身：按脚本吐事件并维护累计状态。"""

    end_thinking: list[dict] = []
    instances: list = []

    def __init__(self, **kwargs):
        FakeProc.instances.append(self)
        self.init_kwargs = kwargs
        self.accumulated_response = kwargs.get("accumulated_response", "")
        self.accumulated_thinking = kwargs.get("accumulated_thinking", "") or ""
        self.blocks = list(kwargs.get("blocks") or [])
        self.current_step = kwargs.get("current_step") or 3
        self.last_context_tokens = kwargs.get("last_context_tokens") or 42
        self.last_usage = {
            "input_tokens": 12,
            "output_tokens": 7,
            "reasoning_tokens": 3,
        }
        self.is_in_thinking = False
        self.thinking_start_time = None
        self.handle_calls = []

    def handle(self, mode, data):
        self.handle_calls.append((mode, data))
        batch = data if isinstance(data, list) else []
        for event in batch:
            self._apply(event)
            yield event

    def _apply(self, event):
        event_type = event.get("type")
        if event_type == "content":
            content = event.get("content", "")
            self.accumulated_response += content
            self.blocks.append(
                {"type": "content", "content": content, "step": event.get("step", 0)}
            )
        elif event_type == "thinking":
            content = event.get("content", "")
            self.accumulated_thinking += content
            self.blocks.append(
                {
                    "type": "thinking",
                    "content": content,
                    "step": event.get("step", 0),
                    "duration": 0,
                }
            )
        elif event_type == "tool_call":
            self.blocks.append(
                {
                    "type": "tool_call",
                    "tool_name": event.get("tool_name", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                    "arguments": event.get("arguments", {}),
                    "result": "",
                    "success": True,
                    "duration": None,
                    "step": event.get("step", 0),
                }
            )
        elif event_type == "thinking_end":
            for block in reversed(self.blocks):
                if block.get("type") == "thinking":
                    block["duration"] = event.get("duration", 0)
                    break
        elif event_type == "tool_result":
            for block in self.blocks:
                if block.get("type") == "tool_call" and block.get(
                    "tool_call_id"
                ) == event.get("tool_call_id"):
                    block["result"] = event.get("result", "")
                    block["success"] = event.get("success", True)
                    block["duration"] = event.get("duration", 0)

    def _end_thinking(self):
        return iter(FakeProc.end_thinking)

    def _sse_blocks(self):
        return self.blocks


class FakeGraph:
    def __init__(self, batches, states=None):
        self.batches = batches
        self.states = list(states or [])
        self.astream_calls = []
        self.state_calls = 0

    async def astream(self, payload, stream_mode=None, config=None):
        self.astream_calls.append(
            {"payload": payload, "stream_mode": stream_mode, "config": config}
        )
        for batch in self.batches:
            yield ("messages", batch)

    async def aget_state(self, config):
        self.state_calls += 1
        if not self.states:
            return SimpleNamespace(next=None, tasks=[], values={"messages": []})
        if len(self.states) == 1:
            return self.states[0]
        return self.states.pop(0)


class FakeDB:
    def __init__(self, messages=None):
        self.session = SimpleNamespace(messages=list(messages or []))
        self.persisted = []
        self.recorded_tool_calls = []
        self.recorded_thinking = []
        self.todos = []
        self.generated_files = []

    def get_session(self, session_id):
        return self.session

    def update_last_assistant_message(self, session_id, message):
        self.persisted.append(message)
        if self.session.messages and self.session.messages[-1].get("role") == "assistant":
            self.session.messages[-1] = message
        else:
            self.session.messages.append(message)

    def record_tool_call(self, **kwargs):
        self.recorded_tool_calls.append(kwargs)

    def record_thinking(self, **kwargs):
        self.recorded_thinking.append(kwargs)

    def update_session_todos(self, session_id, todos):
        self.todos.append(todos)

    def add_generated_file(self, **kwargs):
        self.generated_files.append(kwargs)

    def get_generated_files(self, session_id):
        return list(self.generated_files)

    def update_generated_file_paths(self, *args, **kwargs):
        return None

    def update_session_workspace_name(self, *args, **kwargs):
        return None


class FakeLogger:
    def __init__(self):
        self.tool_calls = []
        self.responses = []

    def log_tool_call(self, **kwargs):
        self.tool_calls.append(kwargs)

    def log_assistant_response(self, **kwargs):
        self.responses.append(kwargs)


def _agent(graph):
    return SimpleNamespace(
        agent=graph,
        workspace_dir=Path("/tmp/c2-workspace"),
        workspace_virtual_path="/workspace",
        config=SimpleNamespace(llm=SimpleNamespace(provider="openai")),
        model=None,
        model_name="mock-model",
        max_steps=5,
        username="",
        _workspace_renamed=True,
        memory_file=None,
        long_term_memory_file=None,
    )


@pytest.fixture(autouse=True)
def _patch_impl(monkeypatch):
    FakeProc.end_thinking = []
    FakeProc.instances = []
    target = orchestrator
    monkeypatch.setattr(target, "StreamProcessor", FakeProc)
    monkeypatch.setattr(target, "get_agent_config", lambda: None)
    return target


async def _collect(generator):
    return [frame async for frame in generator]


async def test_chat_stream_golden_event_order_and_persistence():
    graph = FakeGraph(
        [
            [
                {"type": "thinking_start", "step": 1},
                {"type": "thinking", "content": "why", "step": 1},
                {"type": "thinking_end", "duration": 0.5, "step": 1},
                {"type": "content", "content": "hello"},
                {"type": "tool_call", "tool_name": "execute", "tool_call_id": "c1", "arguments": {"command": "ls"}, "step": 1},
                {"type": "tool_result", "tool_name": "execute", "tool_call_id": "c1", "arguments": {"command": "ls"}, "result": "ok", "success": True, "duration": 0.2, "step": 1},
                {"type": "todo_list", "todos": [{"content": "x"}], "step": 1},
                {"type": "content", "content": " world"},
            ],
            [{"type": "content", "content": "!"}],
        ]
    )
    db = FakeDB()
    session_logger = FakeLogger()

    frames = await _collect(
        chat_stream_generator(
            ChatRequest(message="hi"),
            db,
            _agent(graph),
            "s1",
            "m1",
            "tester",
            session_logger=session_logger,
        )
    )
    events = _events(frames)

    assert _types(events) == [
        "start",
        "thinking_start",
        "thinking",
        "thinking_end",
        "content",
        "tool_call",
        "tool_result",
        "todo_list",
        "content",
        "content",
        "content_end",
        "done",
    ]
    assert frames[0] == format_sse({"type": "start", "session_id": "s1"})
    assert [e["content"] for e in events if e["type"] == "content"] == [
        "hello",
        " world",
        "!",
    ]
    assert events[2] == {"type": "thinking", "content": "why", "step": 1}
    assert events[5]["tool_name"] == "execute"
    assert events[6]["result"] == "ok"
    assert events[7]["todos"] == [{"content": "x"}]

    done = events[-1]
    assert done["session_id"] == "s1"
    assert done["usage"] == {
        "input_tokens": 12,
        "output_tokens": 7,
        "reasoning_tokens": 3,
        "context_length": None,
        "auto_compress_tokens": 170000,
        "context_tokens": 42,
        "step_count": 3,
    }
    assert "blocks" not in done
    assert set(done) == {"type", "session_id", "elapsed_time", "usage"}
    assert "elapsed_time" not in done["usage"]
    assert done["elapsed_time"] >= 0

    # C3b 字节级 golden：done 帧键序 type→session_id→elapsed_time→usage，
    # chat 不带 blocks、usage 不含 elapsed_time（与迁移前字面量逐字节一致）。
    assert list(done) == ["type", "session_id", "elapsed_time", "usage"]
    assert list(done["usage"]) == [
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "context_length",
        "auto_compress_tokens",
        "context_tokens",
        "step_count",
    ]
    assert frames[-1] == format_sse(
        {
            "type": "done",
            "session_id": "s1",
            "elapsed_time": done["elapsed_time"],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 7,
                "reasoning_tokens": 3,
                "context_length": None,
                "auto_compress_tokens": 170000,
                "context_tokens": 42,
                "step_count": 3,
            },
        }
    )
    assert list(events[0]) == ["type", "session_id"]

    assert graph.astream_calls[0]["stream_mode"] == ["messages", "updates"]
    # DA-10：聊天流与恢复流同一套 config，必须带 recursion_limit 兜底
    # （FakeGraph 读不到节点 → 每轮按 10 步估算；max_steps=5 → 10 * 6 + 25）。
    assert graph.astream_calls[0]["config"] == {
        "configurable": {"thread_id": "s1-m1"},
        "recursion_limit": 85,
    }

    assert db.todos == [[{"content": "x"}]]
    assert db.recorded_thinking == [
        {
            "session_id": "s1",
            "message_id": "m1",
            "step": 1,
            "content": "why",
            "duration": 0.5,
        }
    ]
    assert db.recorded_tool_calls == [
        {
            "session_id": "s1",
            "message_id": "m1",
            "tool_name": "execute",
            "tool_call_id": "c1",
            "arguments": {"command": "ls"},
            "result": "ok",
            "success": True,
            "duration": 0.2,
            "step": 1,
        }
    ]

    final = db.persisted[-1]
    assert final["content"] == "hello world!"
    assert final["thinking"] == "why"
    assert final["thinking_duration"] == pytest.approx(0.5)
    assert final["usage"]["input_tokens"] == 12
    assert final["usage"]["output_tokens"] == 7
    assert final["usage"]["reasoning_tokens"] == 3
    assert final["usage"]["context_tokens"] == 42
    assert final["usage"]["step_count"] == 3
    assert [tc["tool_call_id"] for tc in final["tool_calls"]] == ["c1"]
    assert final["tool_calls"][0]["result"] == "ok"
    assert final["tool_calls"][0]["approval_status"] is None
    assert [block["type"] for block in final["blocks"]] == [
        "thinking",
        "content",
        "tool_call",
        "content",
        "content",
    ]
    assert session_logger.responses[0]["content"] == "hello world!"
    assert session_logger.tool_calls[0]["tool_call_id"] == "c1"


async def test_chat_stream_golden_end_thinking_event_at_stream_end():
    """流在思考中结束：_end_thinking 产出的 thinking_end 必须原样下发。"""
    graph = FakeGraph([[{"type": "thinking", "content": "t", "step": 1}]])
    FakeProc.end_thinking = [{"type": "thinking_end", "duration": 0.2, "step": 1}]
    db = FakeDB()

    frames = await _collect(
        chat_stream_generator(
            ChatRequest(message="hi"), db, _agent(graph), "s1", "m1", "tester"
        )
    )
    events = _events(frames)

    assert _types(events) == ["start", "thinking", "thinking_end", "done"]
    assert events[2] == {"type": "thinking_end", "duration": 0.2, "step": 1}


def _hitl_state():
    interrupt = SimpleNamespace(
        id="int-1",
        value={
            "action_requests": [
                {
                    "name": "execute",
                    "args": {"command": "rm -rf a.txt"},
                    "description": "删除文件",
                }
            ],
            "review_configs": [
                {
                    "action_name": "execute",
                    "allowed_decisions": ["approve", "reject"],
                }
            ],
        },
    )
    return SimpleNamespace(
        next="tools",
        tasks=[SimpleNamespace(interrupts=[interrupt])],
        values={
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "execute",
                            "args": {"command": "rm -rf a.txt"},
                            "id": "call-rm",
                        }
                    ],
                )
            ]
        },
    )


async def test_chat_hitl_golden_approval_payload_and_partial_message():
    graph = FakeGraph(
        [
            [
                {"type": "tool_call", "tool_name": "execute", "tool_call_id": "call-rm", "arguments": {"command": "rm -rf a.txt"}, "step": 1},
                {"type": "content", "content": "partial"},
            ]
        ],
        states=[_hitl_state()],
    )
    db = FakeDB()

    frames = await _collect(
        chat_stream_generator(
            ChatRequest(message="delete"), db, _agent(graph), "s1", "m1", "tester"
        )
    )
    events = _events(frames)

    assert _types(events) == [
        "start",
        "tool_call",
        "content",
        "content_end",
        "approval_required",
    ]
    approval = events[-1]
    assert approval["thread_id"] == "s1-m1"
    assert approval["action_requests"] == [
        {
            "tool_call_id": "call-rm",
            "tool_name": "execute",
            "arguments": {"command": "rm -rf a.txt"},
            "description": "删除文件",
            "allowed_decisions": ["approve", "reject"],
            "file_paths": ["a.txt"],
            # DA-02：追加字段（向后兼容），前端据此按中断作答
            "interrupt_id": "int-1",
            "action_index": 0,
        }
    ]
    # C3b：approval_required 走 emitter 快捷方法，键序为契约。
    assert list(approval) == ["type", "thread_id", "action_requests"]
    assert frames[-1] == format_sse(
        {
            "type": "approval_required",
            "thread_id": "s1-m1",
            "action_requests": approval["action_requests"],
        }
    )
    assert "done" not in _types(events)
    assert len(db.persisted) == 1
    partial = db.persisted[0]
    assert partial["content"] == "partial"
    assert partial["tool_calls"][0]["approval_status"] == "pending"
    assert partial["tool_calls"][0]["file_paths"] == ["a.txt"]
    assert partial["blocks"][0]["approval_status"] == "pending"
    assert partial["blocks"][0]["pending_approval"] is True
    assert partial["pending_approval"] == {
        "thread_id": "s1-m1",
        "action_requests": approval["action_requests"],
        # 审批所在的实例：多副本按 pod 隔离 checkpoint 时，恢复请求必须回到这里
        "instance": checkpoint_instance(),
    }


def _resume_messages():
    return [
        {"role": "user", "content": "delete it"},
        {
            "role": "assistant",
            "content": "deleting",
            "thinking": "hmm",
            "blocks": [
                {"type": "thinking", "content": "hmm", "step": 1, "duration": 0.1},
                {
                    "type": "tool_call",
                    "tool_name": "execute",
                    "tool_call_id": "c1",
                    "arguments": {"command": "rm a"},
                    "result": "",
                    "success": True,
                    "duration": None,
                    "step": 1,
                    "approval_status": "pending",
                    "pending_approval": True,
                },
            ],
            "tool_calls": [
                {
                    "tool_name": "execute",
                    "tool_call_id": "c1",
                    "arguments": {"command": "rm a"},
                    "result": "",
                    "success": True,
                    "duration": None,
                    "step": 1,
                    "approval_status": "pending",
                }
            ],
            "usage": {"step_count": 1},
        },
    ]


async def test_resume_stream_golden_decision_merge_and_authoritative_blocks():
    graph = FakeGraph(
        [[{"type": "content", "content": " done"}]],
        states=[SimpleNamespace(next=None, tasks=[], values={"messages": []})],
    )
    db = FakeDB(_resume_messages())

    frames = await _collect(
        resume_stream_generator(
            db,
            _agent(graph),
            "s1",
            "s1-m1",
            [{"type": "approve"}],
            session_logger=FakeLogger(),
            message_id="m1",
        )
    )
    events = _events(frames)

    assert _types(events) == ["start", "content", "content_end", "done"]
    proc = FakeProc.instances[0]
    assert proc.init_kwargs["current_step"] == 1
    assert proc.init_kwargs["accumulated_response"] == "deleting"
    init_tool_block = next(
        block
        for block in proc.init_kwargs["blocks"]
        if block.get("tool_call_id") == "c1"
    )
    assert init_tool_block["approval_status"] == "approved"
    assert "pending_approval" not in init_tool_block

    payload = graph.astream_calls[0]["payload"]
    assert type(payload).__name__ == "Command"
    assert payload.resume == {"decisions": [{"type": "approve"}]}

    done = events[-1]
    assert done["session_id"] == "s1"
    assert done["usage"]["input_tokens"] == 12
    assert done["usage"]["output_tokens"] == 7
    assert done["usage"]["context_tokens"] == 42
    assert done["usage"]["step_count"] == 1
    assert done["usage"]["elapsed_time"] >= 0
    assert done["blocks"][-1]["type"] == "content"
    assert set(done) == {"type", "session_id", "elapsed_time", "usage", "blocks"}
    assert done["usage"]["elapsed_time"] == done["elapsed_time"]

    # C3b 字节级 golden：resume done 的 blocks 必须排在 usage 之后、
    # usage 含 elapsed_time；与迁移前字面量键序逐字节一致。
    assert list(done) == ["type", "session_id", "elapsed_time", "usage", "blocks"]
    assert list(done["usage"]) == [
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "context_length",
        "auto_compress_tokens",
        "context_tokens",
        "elapsed_time",
        "step_count",
    ]
    assert frames[-1] == format_sse(
        {
            "type": "done",
            "session_id": "s1",
            "elapsed_time": done["elapsed_time"],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 7,
                "reasoning_tokens": 3,
                "context_length": 1000000,
                "auto_compress_tokens": 800000,
                "context_tokens": 42,
                "elapsed_time": done["usage"]["elapsed_time"],
                "step_count": 1,
            },
            "blocks": done["blocks"],
        }
    )
    done_tool_block = next(
        block for block in done["blocks"] if block.get("tool_call_id") == "c1"
    )
    assert done_tool_block["approval_status"] == "approved"

    final = db.persisted[-1]
    assert final["content"] == "deleting done"
    assert final["thinking"] == "hmm"
    assert final["tool_calls"][0]["tool_call_id"] == "c1"
    assert final["tool_calls"][0]["approval_status"] == "approved"
    final_tool_block = next(
        block for block in final["blocks"] if block.get("tool_call_id") == "c1"
    )
    assert "pending_approval" not in final_tool_block
    assert final_tool_block["approval_status"] == "approved"


async def test_resume_nested_hitl_golden_blocks_before_action_requests():
    """resume 嵌套中断帧：blocks 必须保持在 action_requests 之前（既有契约）。

    ``SSEEmitter.approval_required()`` 把 ``action_requests`` 排在 ``blocks`` 之前，
    因此 resume 站点改用 ``emit()`` 显式排序；本用例锁死该顺序与逐字节序列化。
    """
    graph = FakeGraph(
        [[{"type": "content", "content": " done"}]],
        states=[
            SimpleNamespace(next=None, tasks=[], values={"messages": []}),
            _hitl_state(),
        ],
    )
    db = FakeDB(_resume_messages())

    frames = await _collect(
        resume_stream_generator(
            db,
            _agent(graph),
            "s1",
            "s1-m1",
            [{"type": "approve"}],
            session_logger=FakeLogger(),
            message_id="m1",
        )
    )
    events = _events(frames)

    assert _types(events) == [
        "start",
        "content",
        "content_end",
        "approval_required",
    ]
    approval = events[-1]
    assert list(approval) == [
        "type",
        "thread_id",
        "blocks",
        "action_requests",
    ]
    assert approval["thread_id"] == "s1-m1"
    assert approval["action_requests"][0]["tool_call_id"] == "call-rm"
    assert any(
        block.get("tool_call_id") == "c1" for block in approval["blocks"]
    )
    expected = {
        "type": "approval_required",
        "thread_id": "s1-m1",
        "blocks": approval["blocks"],
        "action_requests": approval["action_requests"],
    }
    assert frames[-1] == format_sse(expected)
    assert "done" not in _types(events)


async def test_chat_stream_input_keeps_raw_message_and_renders_turn_context():
    """DA-03：本轮消息只存用户原话（id = message_id），本轮注入随 turn_context 传入。

    改造前工作区前缀、页面 / 知识库上下文都直接拼进本轮 HumanMessage；会话级 thread
    下它们会永久留在历史里。
    """
    graph = FakeGraph([[{"type": "content", "content": "ok"}]])
    history = [{"role": "user", "content": "q0"}, {"role": "assistant", "content": "a0"}]
    db = FakeDB(messages=[*history, {"role": "user", "content": "hi"}])

    await _collect(
        chat_stream_generator(
            ChatRequest(message="hi"),
            db,
            _agent(graph),
            "s1",
            "m1",
            "tester",
            parsed_content="<page_context>p</page_context>\n\nhi",
        )
    )

    payload = graph.astream_calls[0]["payload"]
    assert [m.content for m in payload["messages"]] == ["q0", "a0", "hi"]
    assert payload["messages"][-1].id == "m1"
    assert payload["turn_context"] == {
        "message_id": "m1",
        "content": "[workspace: /workspace/ | shell: cd /workspace]\n<page_context>p</page_context>\n\nhi",
    }
    assert payload["skills_metadata"] is None


async def test_chat_stream_input_uses_placeholder_for_files_only_turn():
    """只传文件、没写正文时，thread 里存一条占位，而不是空的用户消息。"""
    graph = FakeGraph([[{"type": "content", "content": "ok"}]])
    db = FakeDB(messages=[{"role": "user", "content": ""}])
    request = ChatRequest(message="", files=[{"filename": "a.pdf", "file_path": "/x/a.pdf"}])

    await _collect(chat_stream_generator(request, db, _agent(graph), "s1", "m1", "tester"))

    assert graph.astream_calls[0]["payload"]["messages"][-1].content == "[上传文件: a.pdf]"
