"""HITL 状态解析纯函数测试（ARCH-03 Phase 3 抽取）。"""

from types import SimpleNamespace

from easy_agent.services.hitl_state import (
    _extract_hitl_request,
    _hitl_action_names,
    _hitl_interrupt_items,
    _hitl_review_config_map,
    hanging_hitl_tool_count,
    normalize_resume_decisions,
)


def _interrupt(interrupt_id, value):
    return SimpleNamespace(id=interrupt_id, value=value)


def _state(*tasks, next_=("tools",)):
    return SimpleNamespace(next=list(next_), tasks=list(tasks))


def _task(*interrupts):
    return SimpleNamespace(interrupts=list(interrupts))


def test_extract_interrupt_request():
    value = {
        "action_requests": [{"name": "execute"}, {"name": "write_file"}],
        "review_configs": [{"action_name": "execute"}, {"action_name": "write_file"}],
    }
    state = _state(_task(_interrupt("i1", value)))
    items = _hitl_interrupt_items(state)
    assert items == [("i1", value)]
    assert _extract_hitl_request(state) is value
    assert _hitl_action_names(value) == {"execute", "write_file"}
    assert set(_hitl_review_config_map(value)) == {"execute", "write_file"}
    assert hanging_hitl_tool_count(state) == 2


def test_extract_hitl_request_none_without_next():
    state = _state(_task(_interrupt("i1", {"action_requests": []})), next_=())
    assert _extract_hitl_request(state) is None
    assert _hitl_interrupt_items(state)[0][0] == "i1"


def test_normalize_resume_decisions():
    assert normalize_resume_decisions([{"type": "approve"}], hanging_tool_count=3) == [
        {"type": "approve"},
        {"type": "approve"},
        {"type": "approve"},
    ]
    assert normalize_resume_decisions(
        [{"type": "approve"}, {"type": "reject"}], hanging_tool_count=2
    ) == [{"type": "approve"}, {"type": "reject"}]
    assert normalize_resume_decisions([], hanging_tool_count=2) == []
