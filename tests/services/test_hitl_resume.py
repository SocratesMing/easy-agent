"""HITL 恢复决策规范化测试。"""

from types import SimpleNamespace

from easy_agent.services.streaming.approvals import (
    action_args,
    action_name,
    answered_actions,
    decision_label,
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


# ── 人工审批日志：操作与决策配对（2026-11-13） ────────────────────────────


def test_answered_actions_pairs_by_interrupt_and_action_index():
    actions = [
        {"tool_name": "delete", "arguments": {"file_path": "/a"}, "interrupt_id": "i2", "action_index": 0},
        {"tool_name": "execute", "arguments": {"command": "rm /b"}, "interrupt_id": "i1", "action_index": 0},
    ]

    pairs = answered_actions(
        {"i1": [{"type": "approve"}], "i2": [{"type": "reject"}]}, actions
    )

    assert [(action_name(a), d["type"]) for a, d in pairs] == [
        ("execute", "approve"),
        ("delete", "reject"),
    ]


def test_answered_actions_falls_back_to_all_actions_without_interrupt_id():
    """旧前端/旧记录没有 interrupt_id：按键为 None 时退回全量操作。"""
    actions = [
        {"name": "delete", "args": {"file_path": "/a"}, "action_index": 1},
        {"name": "save_expert", "args": {"name": "x"}, "action_index": 0},
    ]

    pairs = answered_actions({None: [{"type": "approve"}]}, actions)

    assert [(action_name(a), d["type"]) for a, d in pairs] == [("save_expert", "approve")]


def test_answered_actions_handles_missing_payload():
    assert answered_actions({"i1": [{"type": "approve"}]}, None) == []
    assert answered_actions(None, [{"name": "delete"}]) == []


def test_action_name_and_args_accept_both_shapes():
    """中断原始结构用 name/args，落库回放用 tool_name/arguments。"""
    assert action_name({"name": "delete"}) == "delete"
    assert action_name({"tool_name": "delete"}) == "delete"
    assert action_args({"args": {"a": 1}}) == {"a": 1}
    assert action_args({"arguments": {"a": 1}}) == {"a": 1}
    assert action_args({}) == {}


def test_decision_label_covers_all_decision_types():
    assert decision_label({"type": "approve"}) == "批准执行"
    assert decision_label({"type": "edit"}) == "修改参数后执行"
    assert decision_label({"type": "reject"}) == "拒绝执行"
    assert decision_label({"type": "respond"}) == "直接回复"
    assert decision_label({"type": "whatever"}) == "whatever"
