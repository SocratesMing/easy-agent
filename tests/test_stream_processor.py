import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from easy_agent.services.stream_processor import StreamProcessor


def test_handle_unknown_mode_returns_empty():
    p = StreamProcessor(sid="s1")
    assert p.handle("updates", {}) == []
    assert p.handle("weird", object()) == []


def test_start_and_finalize():
    p = StreamProcessor(sid="s1", session_id="sess")
    assert p.start() == [{"type": "start", "session_id": "sess"}]
    done = p.finalize(session_id="sess", elapsed_time=1.2)
    assert done[0]["type"] == "done"
    assert done[0]["session_id"] == "sess"
    assert done[0]["usage"]["step_count"] == 0
    assert done[0]["blocks"] == []


from langchain_core.messages import AIMessage


def _ai_with_tool_call():
    return AIMessage(
        content="",
        tool_calls=[{"name": "ls", "args": {"path": "/workspace/"}, "id": "tc1"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )


def test_model_update_emits_tool_call_and_token_usage():
    p = StreamProcessor(sid="s1")
    events = p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    types = [e["type"] for e in events]
    assert "tool_call" in types and "token_usage" in types
    tc = next(e for e in events if e["type"] == "tool_call")
    assert tc["tool_name"] == "ls"
    assert tc["tool_call_id"] == "tc1"
    assert tc["arguments"] == {"path": "/workspace/"}
    assert tc["step"] == 1
    tu = next(e for e in events if e["type"] == "token_usage")
    assert tu["input_tokens"] == 100
    assert tu["output_tokens"] == 20
    assert tu["total_tokens"] == 120
    assert tu["context_tokens"] == 100
    assert tu["step_count"] == 1
    assert p.blocks[0]["tool_call_id"] == "tc1"
    assert p.blocks[0]["duration"] is None


from langchain_core.messages import ToolMessage


def test_tools_update_emits_matched_tool_result():
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    events = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="file1\nfile2", tool_call_id="tc1", name="ls")
    ]}})
    assert len(events) == 1
    tr = events[0]
    assert tr["type"] == "tool_result"
    assert tr["tool_call_id"] == "tc1"
    assert tr["tool_name"] == "ls"
    assert tr["result"] == "file1\nfile2"
    assert tr["success"] is True
    assert tr["duration"] is not None
    assert p.blocks[0]["duration"] is not None
    assert p.blocks[0]["result"] == "file1\nfile2"


def test_tool_result_id_mismatch_does_not_crash():
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    events = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="x", tool_call_id="other", name="grep")
    ]}})
    assert events[0]["tool_call_id"] == "other"
    assert p.blocks[0]["duration"] is None


from langchain_core.messages import AIMessageChunk


def test_messages_reasoning_emits_thinking_events():
    p = StreamProcessor(sid="s1")
    c1 = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hello "})
    c2 = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "world"})
    e1 = p.handle("messages", (c1, {}))
    assert any(e["type"] == "thinking_start" for e in e1)
    assert any(e["type"] == "thinking" and e["content"] == "hello " for e in e1)


def test_messages_text_emits_content_event():
    p = StreamProcessor(sid="s1")
    c = AIMessageChunk(content="hi there")
    events = p.handle("messages", (c, {}))
    assert any(e["type"] == "content" and e["content"] == "hi there" for e in events)


def test_content_whitespace_tokens_are_forwarded():
    """纯空白 token（换行/缩进）必须作为 content 事件下发并累积进正文块，
    否则前端实时渲染缺少块间换行，markdown（标题/表格/代码块）会显示成原文。"""
    p = StreamProcessor(sid="s1")
    ev1 = p.handle("messages", (AIMessageChunk(content="## 标题"), {}))
    ev2 = p.handle("messages", (AIMessageChunk(content="\n\n"), {}))
    ev3 = p.handle("messages", (AIMessageChunk(content="- 项目一"), {}))

    contents = [
        e["content"]
        for e in ev1 + ev2 + ev3
        if e.get("type") == "content"
    ]
    assert contents == ["## 标题", "\n\n", "- 项目一"]
    # 正文块累积原文（含空白），与 message.content 一致
    assert p.accumulated_response == "## 标题\n\n- 项目一"
    cblks = [b for b in p.blocks if b.get("type") == "content"]
    assert cblks and cblks[0]["content"] == "## 标题\n\n- 项目一"


def test_hybrid_thinking_then_tool_call_then_result():
    """End-to-end hybrid: messages(thinking) + updates(model tool_call) + updates(tools result)."""
    p = StreamProcessor(sid="s1")
    # 1. messages: thinking tokens
    ev = p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "let me think"}), {}))
    assert any(e["type"] == "thinking_start" for e in ev)
    # 2. messages: content ends thinking
    ev = p.handle("messages", (AIMessageChunk(content="ok"), {}))
    assert any(e["type"] == "thinking_end" for e in ev)
    assert any(e["type"] == "content" and e["content"] == "ok" for e in ev)
    # 3. updates: model node -> tool_call + token_usage
    ev = p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    assert any(e["type"] == "tool_call" for e in ev)
    assert any(e["type"] == "token_usage" for e in ev)
    # 4. updates: tools node -> tool_result
    ev = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="done", tool_call_id="tc1", name="ls")]}})
    assert ev[0]["type"] == "tool_result"
    # blocks: [thinking, content, tool_call]（正文按 step 计入 blocks）
    types = [b["type"] for b in p.blocks]
    assert types == ["thinking", "content", "tool_call"]
    assert p.blocks[1]["content"] == "ok"
    assert p.blocks[2]["duration"] is not None
    assert p.blocks[2]["result"] == "done"
    # finalize
    done = p.finalize(session_id="s", elapsed_time=0.5)[0]
    assert done["type"] == "done"
    assert done["usage"]["step_count"] == p.current_step


def test_step_not_double_counted_for_thinking_plus_tool():
    """One model turn with thinking + tool_call = one step (not two)."""
    p = StreamProcessor(sid="s1")
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hmm"}), {}))
    p.handle("messages", (AIMessageChunk(content="ok"), {}))
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    assert p.current_step == 1
    assert p.blocks[0]["step"] == 1
    assert p.blocks[1]["step"] == 1


def test_step_increments_each_turn():
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    assert p.current_step == 1
    p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="r", tool_call_id="tc1", name="ls")]}})
    p.handle("updates", {"model": {"messages": [AIMessage(
        content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc2"}],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})]}})
    assert p.current_step == 2


def test_token_usage_enriched_fields():
    p = StreamProcessor(sid="s1", max_input_tokens=200000,
                        auto_compress_tokens=170000, pre_session_tokens=500,
                        start_time=time.time() - 2.0)
    events = p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    tu = next(e for e in events if e["type"] == "token_usage")
    assert tu["session_estimate"] == 500 + 100 + 20
    assert tu["max_input_tokens"] == 200000
    assert tu["auto_compress_tokens"] == 170000
    assert tu["elapsed_time"] >= 1.5


def test_tool_result_carries_arguments_from_block():
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    tr = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="r", tool_call_id="tc1", name="ls")]}})[0]
    assert tr["arguments"] == {"path": "/workspace/"}


def test_todo_list_from_write_todos_args():
    todos = [{"title": "a"}, {"title": "b"}]
    p = StreamProcessor(sid="s1")
    events = p.handle("updates", {"model": {"messages": [AIMessage(
        content="",
        tool_calls=[{"name": "write_todos", "args": {"todos": todos}, "id": "wt1"}],
    )]}})
    assert any(e["type"] == "todo_list" for e in events)
    tl = next(e for e in events if e["type"] == "todo_list")
    assert tl["todos"] == todos


def test_todo_list_not_duplicated():
    p = StreamProcessor(sid="s1")
    todos = [{"title": "a"}]
    msg = AIMessage(content="",
        tool_calls=[{"name": "write_todos", "args": {"todos": todos}, "id": "wt1"}])
    e1 = p.handle("updates", {"model": {"messages": [msg]}})
    e2 = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content='[{"title":"a"}]', tool_call_id="wt1", name="write_todos")]}})
    assert sum(1 for e in e1 + e2 if e["type"] == "todo_list") == 1


def test_finalize_enriched_usage():
    p = StreamProcessor(sid="s1", max_input_tokens=200000,
                        auto_compress_tokens=170000, pre_session_tokens=500)
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    done = p.finalize(session_id="s", elapsed_time=3.0,
                      session_total_tokens=999)[0]
    assert done["usage"]["session_estimate"] == 999
    assert done["usage"]["max_input_tokens"] == 200000
    assert done["usage"]["step_count"] == 1
    assert isinstance(done["blocks"], list)
    assert done["blocks"][0]["tool_name"] == "ls"


def test_tool_result_error_detection():
    """Tool result with error text -> success=False (preserves _is_error_result heuristic)."""
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    tr = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="Error: File not found", tool_call_id="tc1", name="ls")]}})[0]
    assert tr["success"] is False
    assert p.blocks[0]["success"] is False


def test_tool_result_ok_success_true():
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    tr = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="file1\nfile2", tool_call_id="tc1", name="ls")]}})[0]
    assert tr["success"] is True


def test_thinking_per_step_no_cross_turn_merge():
    """Each model turn gets its own thinking card with an incrementing step,
    stored separately (step-incrementing). Consecutive turns must NOT merge into
    one card, while still ending thinking on each tool call so the card does not
    stay stuck on "思考中"."""
    p = StreamProcessor(sid="s1")
    # Turn 1: think -> tool_call (NO content between)
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think-1"}), {}))
    ev_model = p.handle("updates", {"model": {"messages": [AIMessage(
        content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}])]}})
    assert any(e["type"] == "thinking_end" for e in ev_model), "thinking must end on tool_call"
    assert p.is_in_thinking is False
    p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="r", tool_call_id="tc1", name="ls")]}})

    # Turn 2: think again -> content
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think-2"}), {}))
    ev_content = p.handle("messages", (AIMessageChunk(content="answer"), {}))

    thinking_blocks = [b for b in p.blocks if b["type"] == "thinking"]
    assert len(thinking_blocks) == 2, "each turn gets its own thinking card"
    assert thinking_blocks[0]["step"] == 1
    assert thinking_blocks[0]["content"] == "think-1"
    assert thinking_blocks[0]["duration"] is not None
    assert thinking_blocks[1]["step"] == 2
    assert thinking_blocks[1]["content"] == "think-2"
    # Each card must have a duration (not None), otherwise the frontend renders a
    # perpetual "正在思考…" state.
    assert thinking_blocks[1]["duration"] is not None
    assert any(e["type"] == "thinking_end" for e in ev_content)


def test_thinking_no_split_within_step_after_tool_call():
    """Late reasoning arriving after the model's AIMessage (same turn, before the
    tool result) must reuse the existing thinking card for that step instead of
    creating a new one -- which would split one turn's thinking into two cards.
    The step must still advance on the next turn."""
    p = StreamProcessor(sid="s1")
    # Turn 1: think -> AIMessage(tool_call) -> late reasoning (same turn)
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think-a"}), {}))
    p.handle("updates", {"model": {"messages": [AIMessage(
        content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}])]}})
    assert p.is_in_thinking is False
    # Late reasoning for the SAME turn (no tool result yet -> same step)
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think-b"}), {}))
    thinking_blocks = [b for b in p.blocks if b["type"] == "thinking"]
    assert len(thinking_blocks) == 1, "no within-step split"
    assert thinking_blocks[0]["step"] == 1
    assert thinking_blocks[0]["content"] == "think-athink-b"
    assert p.is_in_thinking is True
    # Tool result ends the turn (and the reopened thinking)
    ev_tool = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="r", tool_call_id="tc1", name="ls")]}})
    assert any(e["type"] == "thinking_end" for e in ev_tool), "reopened thinking ends on tool result"
    assert p.is_in_thinking is False
    assert thinking_blocks[0]["duration"] is not None
    # Turn 2 must advance the step (new card, not merged with turn 1)
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think-c"}), {}))
    thinking_blocks = [b for b in p.blocks if b["type"] == "thinking"]
    assert len(thinking_blocks) == 2
    assert thinking_blocks[1]["step"] == 2
    assert thinking_blocks[1]["content"] == "think-c"


def test_thinking_no_split_within_step_after_content():
    """Reasoning -> content -> more reasoning within one turn reuses the same
    thinking card (no split), and the step does not advance mid-turn."""
    p = StreamProcessor(sid="s1")
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "r1"}), {}))
    p.handle("messages", (AIMessageChunk(content="partial"), {}))
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "r2"}), {}))
    thinking_blocks = [b for b in p.blocks if b["type"] == "thinking"]
    assert len(thinking_blocks) == 1, "no within-step split around content"
    assert thinking_blocks[0]["step"] == 1
    assert thinking_blocks[0]["content"] == "r1r2"


def test_thinking_duration_set_on_correct_block_not_last():
    """When content arrives after a thinking+tool_call sequence, the duration
    must be applied to the thinking block, not the trailing tool_call block."""
    p = StreamProcessor(sid="s1")
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hmm"}), {}))
    # thinking already ended here via _on_ai_message below too; but simulate the
    # direct content path: think then content with a tool_call block in between.
    p.handle("updates", {"model": {"messages": [AIMessage(
        content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}])]}})
    p.handle("messages", (AIMessageChunk(content="done"), {}))
    thinking = next(b for b in p.blocks if b["type"] == "thinking")
    tool = next(b for b in p.blocks if b["type"] == "tool_call")
    assert thinking["duration"] is not None
    assert tool["duration"] is None  # tool has no result yet


def test_thinking_dedup_aggregate_reemission_mid_turn():
    """When the full turn reasoning is re-emitted as an aggregate chunk while
    still thinking, it must not be appended again -- otherwise one step's
    thinking content renders twice on the frontend."""
    p = StreamProcessor(sid="s1")
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hello "}), {}))
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "world"}), {}))
    # Provider re-emits the FULL accumulated reasoning (aggregate chunk)
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hello world"}), {}))
    thinking_blocks = [b for b in p.blocks if b["type"] == "thinking"]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["content"] == "hello world"


def test_thinking_dedup_aggregate_after_end():
    """Aggregate re-emission after thinking already ended (reopen scenario) must
    not reopen the card nor duplicate content."""
    p = StreamProcessor(sid="s1")
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "plan"}), {}))
    p.handle("updates", {"model": {"messages": [AIMessage(
        content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}])]}})
    assert p.is_in_thinking is False
    # Re-emit the full reasoning after the AIMessage ended thinking
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "plan"}), {}))
    thinking_blocks = [b for b in p.blocks if b["type"] == "thinking"]
    assert len(thinking_blocks) == 1, "aggregate must not create a second card"
    assert thinking_blocks[0]["content"] == "plan"
    assert p.is_in_thinking is False, "aggregate must not reopen thinking"


def test_thinking_reopen_no_flicker_keeps_duration():
    """Reopening a same-step thinking card (late reasoning) must NOT reset its
    duration to None, so the card does not flicker back to '正在思考…'."""
    p = StreamProcessor(sid="s1")
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "a"}), {}))
    p.handle("updates", {"model": {"messages": [AIMessage(
        content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}])]}})
    ended_duration = next(b for b in p.blocks if b["type"] == "thinking")["duration"]
    assert ended_duration is not None
    # Late reasoning for the same turn reopens the card
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "b"}), {}))
    blk = next(b for b in p.blocks if b["type"] == "thinking")
    assert blk["duration"] is not None, "reopen must not flicker duration back to None"
    assert blk["content"] == "ab"


def test_thinking_event_carries_full_content_and_delta():
    """The `thinking` SSE event carries both the delta (`content`) and the FULL
    per-step content (`full_content`). The frontend SETs `full_content` (idempotent
    against re-emitted/aggregate chunks) while an old frontend can still append the
    `content` delta -- so neither cache state duplicates the thinking."""
    p = StreamProcessor(sid="s1")
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hello "}), {}))
    ev = p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "world"}), {}))
    thinking_events = [e for e in ev if e["type"] == "thinking"]
    assert thinking_events, "should emit a thinking event"
    last = thinking_events[-1]
    # delta of the second chunk
    assert last["content"] == "world"
    # full per-step content after the second delta
    assert last["full_content"] == "hello world"


def test_thinking_dedup_prefix_reemission():
    """A large prefix re-emission (chunk already present at the start of the
    accumulated reasoning) must be skipped, not appended again."""
    p = StreamProcessor(sid="s1")
    big = "x" * 80
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": big}), {}))
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "y"}), {}))
    # provider re-emits the earlier prefix chunk
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": big}), {}))
    thinking_blocks = [b for b in p.blocks if b["type"] == "thinking"]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["content"] == big + "y"


def test_content_block_per_step_interleaved_order():
    """正文按 step 计入 blocks，历史会话按 order 还原"思考->正文->工具"的真实顺序，
    而非把所有正文合并到最后。"""
    p = StreamProcessor(sid="s1")
    # Step 1: think -> content -> tool_call
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "t1"}), {}))
    p.handle("messages", (AIMessageChunk(content="正文1"), {}))
    p.handle("updates", {"model": {"messages": [AIMessage(
        content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc1"}])]}})
    p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="r", tool_call_id="tc1", name="ls")]}})
    # Step 2: think -> content (final answer)
    p.handle("messages", (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "t2"}), {}))
    p.handle("messages", (AIMessageChunk(content="正文2"), {}))

    types_steps = [(b["type"], b["step"]) for b in p.blocks]
    assert types_steps == [
        ("thinking", 1), ("content", 1), ("tool_call", 1),
        ("thinking", 2), ("content", 2),
    ], types_steps
    # order 严格递增：正文夹在思考/工具之间，而非全部排到末尾
    orders = [b["order"] for b in p.blocks]
    assert orders == sorted(orders) and len(set(orders)) == len(orders), orders
    # 各 step 正文独立保留（非合并）
    content_blocks = [b for b in p.blocks if b["type"] == "content"]
    assert [b["content"] for b in content_blocks] == ["正文1", "正文2"]
    # 累计正文仍可用于上下文重建
    assert p.accumulated_response == "正文1正文2"
