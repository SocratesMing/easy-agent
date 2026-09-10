"""HITL 恢复决策规范化测试。"""

from types import SimpleNamespace

from easy_agent.services.streaming import (
    hanging_hitl_tool_count,
    normalize_resume_decisions,
)


def test_hanging_hitl_tool_count_sums_all_interrupt_actions():
    state = SimpleNamespace(
        tasks=[
            SimpleNamespace(
                interrupts=[
                    SimpleNamespace(
                        value={
                            "action_requests": [
                                {"name": "execute"},
                                {"name": "execute"},
                            ]
                        }
                    )
                ]
            )
        ]
    )

    assert hanging_hitl_tool_count(state) == 2


def test_single_decision_is_applied_to_all_hanging_tools():
    decisions = [{"type": "approve"}]

    normalized = normalize_resume_decisions(decisions, hanging_tool_count=2)

    assert normalized == [{"type": "approve"}, {"type": "approve"}]


def test_matching_decision_count_is_preserved():
    decisions = [
        {"type": "approve"},
        {"type": "reject", "message": "不要执行"},
    ]

    normalized = normalize_resume_decisions(decisions, hanging_tool_count=2)

    assert normalized is decisions


def test_single_reject_message_is_copied_without_mutation():
    decisions = [{"type": "reject", "message": "拒绝"}]

    normalized = normalize_resume_decisions(decisions, hanging_tool_count=3)

    assert normalized == [
        {"type": "reject", "message": "拒绝"},
        {"type": "reject", "message": "拒绝"},
        {"type": "reject", "message": "拒绝"},
    ]
    assert decisions == [{"type": "reject", "message": "拒绝"}]


def test_ambiguous_counts_are_left_unchanged():
    decisions = [{"type": "approve"}, {"type": "reject"}]

    normalized = normalize_resume_decisions(decisions, hanging_tool_count=3)

    assert normalized is decisions
