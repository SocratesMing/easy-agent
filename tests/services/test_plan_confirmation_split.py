"""Plan confirmation creates a user message and a fresh assistant continuation."""

from types import SimpleNamespace

from easy_agent.services.agents.chat_turn import _mark_resumed_interaction
from easy_agent.services.streaming.messages import stable_history
from easy_agent.services.streaming.projector import last_user_message
from easy_agent.services.streaming.orchestrator import (
    _continuation_event_allowed,
    _continuation_tool_records,
    _continuation_view,
)


def test_mark_resumed_interaction_only_completes_answered_interrupt():
    message = {
        "role": "assistant",
        "blocks": [
            {
                "type": "tool_call",
                "tool_name": "ask_user",
                "tool_call_id": "ask-a",
                "approval_status": "pending",
                "pending_approval": True,
            },
            {
                "type": "tool_call",
                "tool_name": "ask_user",
                "tool_call_id": "ask-b",
                "approval_status": "pending",
                "pending_approval": True,
            },
        ],
        "pending_approval": {
            "thread_id": "s1-m1",
            "action_requests": [
                {
                    "tool_call_id": "ask-a",
                    "interrupt_id": "A",
                    "action_index": 0,
                },
                {
                    "tool_call_id": "ask-b",
                    "interrupt_id": "B",
                    "action_index": 0,
                },
            ],
        },
    }
    updated = _mark_resumed_interaction(
        message,
        [{"type": "respond", "interrupt_id": "A", "action_index": 0}],
    )
    assert updated["blocks"][0]["approval_status"] == "approved"
    assert "pending_approval" not in updated["blocks"][0]
    assert updated["blocks"][1]["approval_status"] == "pending"
    assert updated["pending_approval"]["action_requests"][0]["interrupt_id"] == "B"
    assert "pending_approval" in message


def test_display_only_confirmation_is_not_hydrated_as_a_model_turn():
    rows = [
        {"role": "user", "content": "原始需求", "turn_id": "m1"},
        {
            "role": "user",
            "content": "1. 项目 A：快速版本",
            "display_only": True,
            "message_kind": "plan_confirmation",
        },
        {"role": "assistant", "content": "继续执行"},
    ]
    assert [row["content"] for row in stable_history(rows)] == [
        "原始需求",
        "继续执行",
    ]


def test_resume_memory_source_skips_display_only_confirmation():
    rows = [
        {"role": "user", "content": "请规划三个项目"},
        {
            "role": "user",
            "content": "1. 项目 A：快速版本",
            "display_only": True,
            "message_kind": "plan_confirmation",
        },
        {"role": "assistant", "content": "继续执行"},
    ]
    assert last_user_message(rows) == "请规划三个项目"


def test_split_continuation_drops_replayed_interrupt_content():
    proc = SimpleNamespace(
        blocks=[
            {"type": "content", "content": "先确认", "step": 1},
            {
                "type": "tool_call",
                "tool_name": "ask_user",
                "tool_call_id": "ask-1",
                "step": 2,
            },
            {"type": "content", "content": "继续执行", "step": 3},
        ],
        accumulated_response="先确认继续执行",
        accumulated_thinking="思考",
        current_step=3,
        is_in_thinking=False,
        thinking_start_time=None,
    )
    view = _continuation_view(
        proc,
        step_boundary=1,
        response_prefix="先确认",
        thinking_prefix="思考",
    )
    assert view.accumulated_response == "继续执行"
    assert view.accumulated_thinking == ""
    assert view.current_step == 2
    assert view.blocks == [
        {
            "type": "tool_call",
            "tool_name": "ask_user",
            "tool_call_id": "ask-1",
            "step": 1,
        },
        {"type": "content", "content": "继续执行", "step": 2},
    ]
    assert _continuation_tool_records(
        [
            ["old", "a", {}, "", True, 0, 1],
            ["ask_user", "ask-1", {}, "answer", True, 0, 2],
            ["new", "b", {}, "", True, 0, 3],
        ],
        1,
        1,
    ) == [
        ["ask_user", "ask-1", {}, "answer", True, 0, 1],
        ["new", "b", {}, "", True, 0, 2],
    ]
    assert _continuation_event_allowed({"type": "content", "step": 1}, 1) is False
    assert _continuation_event_allowed({"type": "content", "step": 2}, 1) is True
