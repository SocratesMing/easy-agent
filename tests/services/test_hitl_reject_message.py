"""DA-15：拒绝说明按工具生成（回给模型的 ToolMessage）。

改造前前端对所有工具都发同一句「请勿重试此删除命令」，拒绝定时任务、专家保存时也这么说。
"""

from __future__ import annotations

from easy_agent.services.hitl_state import HitlInterrupt, plan_resume, reject_message

LEGACY = "用户拒绝了此操作，请勿重试此删除命令。"


def _interrupt(*actions):
    return HitlInterrupt(
        interrupt_id="A",
        action_requests=list(actions),
        review_configs=[{"action_name": a["name"], "allowed_decisions": ["approve", "reject"]} for a in actions],
    )


def test_reject_message_names_what_was_rejected():
    assert "删除 /workspace/a.txt" in reject_message({"name": "delete", "args": {"file_path": "/workspace/a.txt"}})
    assert "执行命令 `rm a.txt`" in reject_message({"name": "execute", "args": {"command": "rm a.txt"}})
    assert "创建这个定时任务" in reject_message({"name": "create_scheduled_task", "args": {}})
    assert "保存这个专家" in reject_message({"name": "save_expert", "args": {}})
    assert "调用 mcp_tool" in reject_message({"name": "mcp_tool", "args": {}})
    assert "删除命令" not in reject_message({"name": "create_scheduled_task", "args": {}})


def test_missing_or_legacy_reject_message_is_generated_per_tool():
    interrupt = _interrupt(
        {"name": "create_scheduled_task", "args": {}},
        {"name": "delete", "args": {"file_path": "/workspace/a.txt"}},
    )
    plan = plan_resume(
        [interrupt],
        [
            {"type": "reject", "interrupt_id": "A", "action_index": 0},
            {"type": "reject", "message": LEGACY, "interrupt_id": "A", "action_index": 1},
        ],
    )
    first, second = plan.resume["A"]["decisions"]
    assert "创建这个定时任务" in first["message"]
    assert "删除 /workspace/a.txt" in second["message"] and second["message"] != LEGACY


def test_custom_reject_message_and_approvals_are_kept():
    interrupt = _interrupt({"name": "delete", "args": {}}, {"name": "delete", "args": {}})
    plan = plan_resume(
        [interrupt],
        [
            {"type": "reject", "message": "先别删", "interrupt_id": "A", "action_index": 0},
            {"type": "approve", "interrupt_id": "A", "action_index": 1},
        ],
    )
    assert plan.resume["A"]["decisions"] == [{"type": "reject", "message": "先别删"}, {"type": "approve"}]
