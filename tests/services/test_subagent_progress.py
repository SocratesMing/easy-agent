"""子智能体实时进度（subagent_progress）提取、隔离与持久化测试。"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from easy_agent.db.message_serialization import (
    _sanitize_blocks,
    _sanitize_message_for_storage,
)
from easy_agent.services.streaming.orchestrator import _continuation_event_allowed
from easy_agent.services.streaming.sse import SSEEvent
from easy_agent.services.streaming.subagent_progress import (
    SubagentProgressTracker,
    astream_supports_subgraphs,
    summarize_subagent_tool,
)


def test_summarize_subagent_tool_labels():
    assert summarize_subagent_tool("read_file", {"file_path": "/w/sales.xlsx"}) == "读取 sales.xlsx"
    assert summarize_subagent_tool("execute", {"command": "pytest -q"}) == "执行 pytest -q"
    assert summarize_subagent_tool("grep", {"pattern": "TODO"}) == "搜索 TODO"
    assert summarize_subagent_tool("web_search", {"query": "LangGraph"}) == "联网搜索 LangGraph"
    assert summarize_subagent_tool("custom_tool", {}) == "调用 custom_tool"


def test_tracker_distills_tool_call_result_and_summary_and_syncs_parent_block():
    tracker = SubagentProgressTracker()
    tracker.register_task("call-task-1", "research")
    proc = SimpleNamespace(
        blocks=[
            {
                "type": "tool_call",
                "tool_name": "task",
                "tool_call_id": "call-task-1",
                "arguments": {"description": "调研方案", "subagent_type": "research"},
            }
        ]
    )

    ai_call = AIMessage(
        content="",
        tool_calls=[
            {"name": "read_file", "args": {"file_path": "/w/spec.md"}, "id": "sub-c1"}
        ],
    )
    evs1 = tracker.handle_subgraph_event(
        ("tools:ns-1",), "updates", {"model": {"messages": [ai_call]}}, proc=proc
    )
    assert len(evs1) == 1
    assert evs1[0]["type"] == SSEEvent.SUBAGENT_PROGRESS
    assert evs1[0]["tool_call_id"] == "call-task-1"
    assert evs1[0]["subagent_type"] == "research"
    assert evs1[0]["stage"] == "tool_call"
    assert evs1[0]["summary"] == "读取 spec.md"

    tool_res = ToolMessage(content="ok", name="read_file", tool_call_id="sub-c1")
    evs2 = tracker.handle_subgraph_event(
        ("tools:ns-1",), "updates", {"tools": {"messages": [tool_res]}}, proc=proc
    )
    assert len(evs2) == 1
    assert evs2[0]["stage"] == "tool_result"
    assert evs2[0]["success"] is True

    # tool_result 回填到同一行，不重复占两行
    steps = proc.blocks[0]["subagent_steps"]
    assert len(steps) == 1
    assert steps[0]["summary"] == "读取 spec.md"
    assert steps[0]["success"] is True

    ai_done = AIMessage(content="这是子任务的汇总结果")
    evs3 = tracker.handle_subgraph_event(
        ("tools:ns-1",), "updates", {"model": {"messages": [ai_done]}}, proc=proc
    )
    assert len(evs3) == 1
    assert evs3[0]["stage"] == "summary"
    assert len(proc.blocks[0]["subagent_steps"]) == 2

    # messages 模式的 token 增量被忽略，不污染事件流
    assert tracker.handle_subgraph_event(("tools:ns-1",), "messages", (ai_done, {})) == []

    completed = tracker.complete_task("call-task-1")
    assert completed is not None
    assert completed[0] == "research"


def test_tracker_isolates_parallel_subagent_namespaces():
    tracker = SubagentProgressTracker()
    tracker.register_task("call-a", "research")
    tracker.register_task("call-b", "general-purpose")

    ev_a = tracker.handle_subgraph_event(
        ("tools:ns-a",),
        "updates",
        {"model": {"messages": [AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/a"}, "id": "1"}])]}},
    )
    ev_b = tracker.handle_subgraph_event(
        ("tools:ns-b",),
        "updates",
        {"model": {"messages": [AIMessage(content="", tool_calls=[{"name": "grep", "args": {"pattern": "x"}, "id": "2"}])]}},
    )
    assert ev_a[0]["tool_call_id"] == "call-a"
    assert ev_b[0]["tool_call_id"] == "call-b"


def test_astream_supports_subgraphs_detection():
    async def with_subgraphs(inp, stream_mode=None, config=None, subgraphs=False):
        yield inp

    async def with_kwargs(inp, **kwargs):
        yield inp

    async def minimal(inp, stream_mode=None, config=None):
        yield inp

    assert astream_supports_subgraphs(with_subgraphs) is True
    assert astream_supports_subgraphs(with_kwargs) is True
    assert astream_supports_subgraphs(minimal) is False


def test_subagent_steps_preserved_in_message_serialization():
    block = {
        "type": "tool_call",
        "order": 1,
        "tool_name": "task",
        "tool_call_id": "call-task-1",
        "arguments": {"description": "分析"},
        "result": "完成",
        "success": True,
        "duration": 2.5,
        "step": 1,
        "subagent_steps": [
            {"stage": "tool_result", "tool_name": "read_file", "summary": "读取 a.md", "success": True, "step": 1}
        ],
    }
    sanitized_msg = _sanitize_message_for_storage(
        None, {"role": "assistant", "content": "", "blocks": [block]}
    )
    assert sanitized_msg["blocks"][0]["subagent_steps"][0]["summary"] == "读取 a.md"

    sanitized_blks = _sanitize_blocks(None, [block])
    assert sanitized_blks[0]["subagent_steps"][0]["summary"] == "读取 a.md"


def test_continuation_allows_subagent_progress_regardless_of_step_boundary():
    ev = {"type": SSEEvent.SUBAGENT_PROGRESS, "tool_call_id": "c1", "step": 1}
    assert _continuation_event_allowed(ev, step_boundary=5) is True
