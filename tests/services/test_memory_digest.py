"""交给会话记忆整理的本轮摘要：工具调用 / 改动的文件 / 审批结果，回复保留开头和结尾。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import easy_agent.services.memory.manager as memory_manager
from easy_agent.services.agents import prompts as prompt_loader
from easy_agent.services.memory.digest import MAX_ACTIONS, clip_middle, describe_turn_actions
from easy_agent.services.memory.manager import build_memory_update_prompt, update_memory_after_session
from easy_agent.services.memory.triggers import TurnBatcher
from easy_agent.services.streaming import side_effects


@pytest.fixture(autouse=True)
def _authoritative_prompts(monkeypatch):
    monkeypatch.setattr(
        prompt_loader,
        "_CONFIGURED_DIR",
        Path(__file__).resolve().parents[2] / "easy_agent" / "config" / "prompts",
    )


def _record(name, args, *, success=True, result="ok", status=None):
    return {"tool_name": name, "arguments": args, "result": result, "success": success, "approval_status": status}


def test_turn_actions_list_key_arguments_outcomes_and_changed_files():
    digest = describe_turn_actions(
        [
            _record("write_todos", {"todos": []}),
            _record("read_file", {"file_path": "/skills/builtin/docx/SKILL.md"}),
            _record("execute", {"command": "uv run python build.py"}, success=False, result="ModuleNotFoundError: docx"),
            _record("execute", {"command": "uv run python build.py"}, success=False, result="ModuleNotFoundError: docx"),
            _record("write_file", {"file_path": "/workspace/report.docx"}),
            _record("delete", {"file_path": "/workspace/old"}, status="rejected"),
            _record("save_expert", '{"name": "宏观专家"}', status="approved"),
        ]
    )

    lines = digest.splitlines()
    assert "write_todos" not in digest  # 待办列表不算操作
    assert lines[0] == "- read_file /skills/builtin/docx/SKILL.md ✓"
    assert lines[1] == "- execute `uv run python build.py` ✗ 失败：ModuleNotFoundError: docx ×2"
    assert "- delete /workspace/old ✗ 用户拒绝" in lines
    assert any(line.startswith("- save_expert") and "✓（用户批准）" in line for line in lines)
    # 被拒绝的删除不算改动
    assert lines[-1] == "本轮改动的文件：/workspace/report.docx"


def test_turn_actions_keep_head_and_tail_when_there_are_many():
    records = [_record("read_file", {"file_path": f"/workspace/f{i}.md"}) for i in range(MAX_ACTIONS + 5)]

    lines = describe_turn_actions(records).splitlines()

    assert len(lines) == MAX_ACTIONS + 1
    assert "/workspace/f0.md" in lines[0]
    assert "中间省略 5 条" in lines[4]
    assert f"/workspace/f{MAX_ACTIONS + 4}.md" in lines[-1]
    assert describe_turn_actions([]) == "" and describe_turn_actions(None) == ""


def test_clip_middle_keeps_the_ending_of_long_replies():
    reply = "结论：" + "正文" * 2000 + "下一步：等用户确认口径"

    clipped = clip_middle(reply, 2000)

    assert len(clipped) <= 2000
    assert clipped.startswith("结论：") and clipped.endswith("下一步：等用户确认口径")
    assert "中间省略" in clipped
    assert clip_middle("短回复", 2000) == "短回复"


def test_session_memory_prompt_shows_turn_actions():
    with_actions = build_memory_update_prompt("", "q", "a", turn_actions="- execute `make` ✗ 失败")
    without = build_memory_update_prompt("", "q", "a")

    assert "本轮操作" in with_actions and "- execute `make` ✗ 失败" in with_actions
    assert "执行过程本身不要写进记忆" in with_actions
    assert "（无）" in without


def test_session_memory_update_passes_turn_actions_to_the_model(tmp_path):
    prompts: list[str] = []

    class _LLM:
        def invoke(self, messages):
            prompts.append(messages[0].content)
            return SimpleNamespace(content="# 会话记忆\n\n## 决策与理由\n- 改用 python-docx —— docx 库缺失\n")

    assert update_memory_after_session(
        tmp_path / "memory.md", "生成报告", "已生成", llm=_LLM(), turn_actions="- execute `build` ✗ 失败"
    )
    assert "- execute `build` ✗ 失败" in prompts[0]


def test_submit_memory_updates_builds_turn_actions_from_tool_records(monkeypatch, tmp_path):
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(side_effects, "_MEMORY_UPDATE_EXECUTOR", pool)
    monkeypatch.setattr(side_effects, "SESSION_BATCHER", TurnBatcher(every_n_turns=1))  # 每轮都整理
    seen: list[str] = []
    monkeypatch.setattr(
        memory_manager,
        "update_memory_after_session",
        lambda memory_file, **kwargs: seen.append(kwargs["turn_actions"]),
    )
    agent = SimpleNamespace(memory_file=tmp_path / "memory.md", long_term_memory_file=None, model=object())
    # 与流式收尾一致的记录元组：名称、id、参数、结果、成败、耗时、step、审批状态
    records = [["write_file", "t1", {"file_path": "/workspace/a.md"}, "ok", True, 0.1, 1, None]]

    try:
        side_effects.submit_memory_updates(agent, "s1", None, "q", "a", "s1", tool_call_records=records)
    finally:
        pool.shutdown(wait=True)

    assert seen == ["- write_file /workspace/a.md ✓\n本轮改动的文件：/workspace/a.md"]
