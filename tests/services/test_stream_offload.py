"""流式编排器的数据库读写不在事件循环线程上执行。

改造前持久化（思考 / 工具结果 / todos / 增量与最终消息 / 部分回复）和收尾副作用（工作区改名、
技能入库）都在事件循环上同步查库，业务库是 MySQL 时每次往返都会卡住所有并发的 SSE 流。
现在经 ``utils/blocking.run_blocking`` 放进线程；流被取消时，先等在途的写入完成再保存部分回复，
保持原来的写入顺序。
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from easy_agent.models.api import ChatRequest
from easy_agent.services.streaming import orchestrator
from tests.services.test_stream_split_golden import FakeDB, FakeGraph, FakeLogger, FakeProc, _agent

_DB_METHODS = (
    "get_session",
    "update_last_assistant_message",
    "record_tool_call",
    "record_thinking",
    "update_session_todos",
    "add_generated_file",
    "get_generated_files",
    "update_generated_file_paths",
    "update_session_workspace_name",
)


class RecordingDB(FakeDB):
    """记录每次调用发生在哪个线程，并保留调用完成的顺序。"""

    def __init__(self, loop_thread: int, delays: dict[str, float] | None = None):
        super().__init__()
        self.loop_thread = loop_thread
        self.delays = delays or {}
        self.on_loop: list[str] = []
        self.completed: list[str] = []
        self.started = threading.Event()


def _wrap(name):
    base = getattr(FakeDB, name)

    def wrapped(self, *args, **kwargs):
        if threading.get_ident() == self.loop_thread:
            self.on_loop.append(name)
        if name in self.delays:
            self.started.set()
            time.sleep(self.delays[name])
        result = base(self, *args, **kwargs)
        self.completed.append(name)
        return result

    return wrapped


for _name in _DB_METHODS:
    setattr(RecordingDB, _name, _wrap(_name))


@pytest.fixture(autouse=True)
def _fake_processor(monkeypatch):
    FakeProc.end_thinking = []
    FakeProc.instances = []
    target = orchestrator
    monkeypatch.setattr(target, "StreamProcessor", FakeProc)
    monkeypatch.setattr(target, "get_agent_config", lambda: None)


def _generator(db, graph):
    from easy_agent.services.streaming.orchestrator import chat_stream_generator

    return chat_stream_generator(
        ChatRequest(message="hi"), db, _agent(graph), "s1", "m1", "tester", session_logger=FakeLogger()
    )


async def test_chat_stream_writes_happen_off_the_event_loop():
    graph = FakeGraph(
        [
            [
                {"type": "thinking_start", "step": 1},
                {"type": "thinking", "content": "why", "step": 1},
                {"type": "thinking_end", "duration": 0.5, "step": 1},
                {"type": "tool_call", "tool_name": "execute", "tool_call_id": "c1", "arguments": {"command": "ls"}, "step": 1},
                {"type": "tool_result", "tool_name": "execute", "tool_call_id": "c1", "arguments": {"command": "ls"},
                 "result": "ok", "success": True, "duration": 0.2, "step": 1},
                {"type": "todo_list", "todos": [{"content": "x"}], "step": 1},
                # 超过增量落库的字数上限：走「立即落库」分支
                {"type": "content", "content": "x" * 20001},
            ]
        ]
    )
    db = RecordingDB(threading.get_ident())

    frames = [frame async for frame in _generator(db, graph)]

    assert frames
    assert db.on_loop == []
    for name in ("get_session", "record_thinking", "record_tool_call", "update_session_todos"):
        assert name in db.completed, name
    # 增量落库 + 最终消息
    assert db.completed.count("update_last_assistant_message") >= 2


async def test_cancel_saves_the_partial_reply_after_the_inflight_write():
    """取消时线程里的工具记录还在写：部分回复必须等它写完再保存，不能交叉。"""

    class StallingGraph(FakeGraph):
        async def astream(self, payload, stream_mode=None, config=None, **kwargs):
            yield (
                "messages",
                [
                    {"type": "content", "content": "partial "},
                    {"type": "tool_call", "tool_name": "execute", "tool_call_id": "c1", "arguments": {}, "step": 1},
                    {"type": "tool_result", "tool_name": "execute", "tool_call_id": "c1", "arguments": {},
                     "result": "ok", "success": True, "duration": 0.1, "step": 1},
                ],
            )
            await asyncio.sleep(3600)

    db = RecordingDB(threading.get_ident(), delays={"record_tool_call": 0.3})

    async def consume():
        return [frame async for frame in _generator(db, StallingGraph([]))]

    task = asyncio.create_task(consume())
    while not db.started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert db.on_loop == []
    assert "record_tool_call" in db.completed
    assert "update_last_assistant_message" in db.completed
    assert db.completed.index("record_tool_call") < db.completed.index("update_last_assistant_message")


class _RenamingAgent(SimpleNamespace):
    def rename_workspace(self, new_name):
        target = self.workspace_dir.parent / new_name
        self.workspace_dir.rename(target)
        self.workspace_dir = target
        return True


_WRITE = {"path": "a.md", "content": "x"}


async def test_stream_end_side_effects_query_off_the_event_loop(tmp_path, monkeypatch):
    """收尾的工作区改名（两次写库）和技能入库扫描（读生成文件记录）也在线程里查库。"""
    from easy_agent.services import skill_library
    from easy_agent.services.streaming.orchestrator import chat_stream_generator

    monkeypatch.setattr(skill_library, "user_skills_dir", lambda username: tmp_path / "skills")
    workspace = tmp_path / "s1"
    workspace.mkdir()
    graph = FakeGraph(
        [
            [
                {"type": "tool_call", "tool_name": "write_file", "tool_call_id": "w1", "arguments": _WRITE, "step": 1},
                {"type": "tool_result", "tool_name": "write_file", "tool_call_id": "w1", "arguments": _WRITE,
                 "result": "ok", "success": True, "duration": 0.1, "step": 1},
                {"type": "content", "content": "done"},
            ]
        ]
    )
    agent = _RenamingAgent(
        **{**vars(_agent(graph)), "username": "tester", "_workspace_renamed": False, "workspace_dir": workspace}
    )
    db = RecordingDB(threading.get_ident())

    frames = [
        frame
        async for frame in chat_stream_generator(
            ChatRequest(message="hi"), db, agent, "s1", "m1", "tester", session_logger=FakeLogger()
        )
    ]

    assert frames
    assert agent._workspace_renamed
    assert db.on_loop == []
    for name in ("get_generated_files", "update_generated_file_paths", "update_session_workspace_name"):
        assert name in db.completed, name


async def test_workspace_rename_writes_both_columns_even_if_cancelled(tmp_path):
    """改名的两次写库在同一次线程调用里：写第一条时被取消，第二条也写完，库里不会只改一半。"""
    from easy_agent.services.streaming.side_effects import maybe_rename_workspace

    workspace = tmp_path / "s1"
    workspace.mkdir()
    agent = _RenamingAgent(workspace_dir=workspace, _workspace_renamed=False)
    db = RecordingDB(threading.get_ident(), delays={"update_generated_file_paths": 0.2})
    records = [("write_file", "w1", _WRITE, "ok", True, 0.1, 1)]

    task = asyncio.create_task(maybe_rename_workspace(agent, db, "s1", records, "s1"))
    while not db.started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert db.completed == ["update_generated_file_paths", "update_session_workspace_name"]
    assert db.on_loop == []
