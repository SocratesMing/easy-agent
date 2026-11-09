"""记忆的增量更新：模型给出新增 / 修改 / 删除指令，程序按指令改写，没动的条目一字不改。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.services.agents import prompts as prompt_loader
from easy_agent.services.memory import manager as memory_manager
from easy_agent.services.memory.manager import (
    LONG_TERM_SECTIONS,
    SESSION_SECTIONS,
    update_long_term_memory_after_session,
    update_memory_after_session,
    write_memory,
)
from easy_agent.services.memory.ops import (
    MemoryItem,
    annotate_for_prompt,
    apply_compaction,
    apply_memory_response,
    apply_ops,
    fit_to_budget,
    parse_memory,
    parse_ops,
    render_memory,
)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "easy_agent" / "config" / "prompts"

SESSION = (
    "# 会话记忆\n\n"
    "## 决策与理由\n- 数据库统一用 MySQL —— 开发与生产一致\n\n"
    "## 偏好与约定\n- 注释一律用中文\n"
)


@pytest.fixture(autouse=True)
def _authoritative_prompts(monkeypatch):
    monkeypatch.setattr(prompt_loader, "_CONFIGURED_DIR", PROMPTS_DIR)


class _LLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[0].content)
        return SimpleNamespace(content=self.reply)


def _ops(**kwargs) -> str:
    return json.dumps({"add": [], "update": [], "delete": [], **kwargs}, ensure_ascii=False)


# ── 纯函数 ──────────────────────────────────────────────────────────────


def test_current_memory_is_numbered_for_the_model():
    assert annotate_for_prompt(SESSION) == (
        "## 决策与理由\n- [1] 数据库统一用 MySQL —— 开发与生产一致\n\n## 偏好与约定\n- [2] 注释一律用中文"
    )
    assert annotate_for_prompt("") == ""


def test_parse_ops_tolerates_fences_and_id_spellings():
    ops = parse_ops(
        '```json\n{"add": ["金额单位统一为元"], "update": [{"id": "[2]", "text": "- [2] 注释用英文"}], "delete": ["1"]}\n```'
    )

    assert ops.add == [("", "金额单位统一为元")]
    assert ops.update == [(2, "注释用英文")]
    assert ops.delete == [1]
    assert parse_ops("# 会话记忆\n- 一条") is None


def test_apply_ops_changes_only_the_referenced_entries():
    items = parse_memory(SESSION)
    ops = parse_ops(
        _ops(
            add=[
                {"section": "偏好与约定", "text": "金额单位统一为元"},
                {"section": "随便一个小节", "text": "接口带 /agent 前缀"},
                {"section": "偏好与约定", "text": "注释一律用中文"},  # 与已有条目相同：跳过
            ],
            update=[{"id": 1, "text": "数据库改用 PostgreSQL —— 用户改了主意"}, {"id": 9, "text": "越界"}],
        )
    )

    result, counts = apply_ops(items, ops, sections=SESSION_SECTIONS)

    assert counts == {"added": 2, "updated": 1, "deleted": 0}
    assert [(item.section, item.text) for item in result] == [
        ("决策与理由", "数据库改用 PostgreSQL —— 用户改了主意"),
        ("偏好与约定", "注释一律用中文"),
        ("偏好与约定", "金额单位统一为元"),
        ("决策与理由", "接口带 /agent 前缀"),  # 未知小节归入第一个规定小节
    ]


def test_render_keeps_prescribed_sections_first_and_legacy_sections_after():
    items = [
        MemoryItem("未分类", "旧格式遗留的一条"),
        MemoryItem("偏好与约定", "注释用中文"),
        MemoryItem("决策与理由", "用 MySQL"),
    ]

    assert render_memory(items, title="会话记忆", sections=SESSION_SECTIONS) == (
        "# 会话记忆\n\n## 决策与理由\n- 用 MySQL\n\n## 偏好与约定\n- 注释用中文\n\n## 未分类\n- 旧格式遗留的一条\n"
    )


def test_response_modes():
    no_change = apply_memory_response(_ops(), SESSION, title="会话记忆", sections=SESSION_SECTIONS)
    broken = apply_memory_response('{"add": [', SESSION, title="会话记忆", sections=SESSION_SECTIONS)
    rewrite = apply_memory_response(
        "# 会话记忆\n\n## 偏好与约定\n- [2] 注释一律用中文\n", SESSION, title="会话记忆", sections=SESSION_SECTIONS
    )

    assert (no_change.markdown, no_change.mode) == (None, "ops")
    assert (broken.markdown, broken.mode) == (None, "invalid")  # 坏 JSON 不能当记忆写进去
    assert rewrite.mode == "rewrite" and "[2]" not in rewrite.markdown


# ── 接入记忆更新 ────────────────────────────────────────────────────────


def test_session_update_applies_ops_and_keeps_untouched_entries_verbatim(tmp_path):
    memory_file = tmp_path / "memory.md"
    write_memory(memory_file, SESSION)
    llm = _LLM(_ops(add=[{"section": "偏好与约定", "text": "金额单位统一为元"}]))

    assert update_memory_after_session(memory_file, "金额都用元", "好的", llm=llm) is True

    assert "- [1] 数据库统一用 MySQL —— 开发与生产一致" in llm.prompts[0]
    assert "当前约" in llm.prompts[0]
    final = memory_file.read_text(encoding="utf-8")
    assert "- 数据库统一用 MySQL —— 开发与生产一致" in final
    assert "- 注释一律用中文" in final and "- 金额单位统一为元" in final


def test_no_change_leaves_the_memory_file_untouched(tmp_path):
    memory_file = tmp_path / "memory.md"
    write_memory(memory_file, SESSION)
    before = memory_file.read_text(encoding="utf-8")

    assert update_memory_after_session(memory_file, "你好", "你好！", llm=_LLM(_ops())) is False
    assert memory_file.read_text(encoding="utf-8") == before


def test_long_term_update_applies_ops_and_still_strips_role_scope_rules(tmp_path):
    memory_file = tmp_path / "AGENTS.md"
    write_memory(memory_file, "# 用户长期记忆\n\n## 未分类\n- （暂无跨会话长期有效的用户信息）\n")
    llm = _LLM(
        _ops(
            add=[
                {"section": "工作背景", "text": "固收研究员，主要看利率债"},
                {"section": "工作背景", "text": "只答债券问题，汇率拒答"},
            ],
            delete=[1],
        )
    )

    assert update_long_term_memory_after_session(memory_file, "我是固收研究员", "好的", llm=llm) is True

    final = memory_file.read_text(encoding="utf-8")
    assert "## 工作背景" in final and "固收研究员，主要看利率债" in final
    assert "暂无" not in final
    assert "拒答" not in final  # 角色职责 / 拒答规则仍由原有兜底剔除


def test_section_names_match_the_prompts():
    session_prompt = (PROMPTS_DIR / "memory_update.md").read_text(encoding="utf-8")
    long_term_prompt = (PROMPTS_DIR / "long_term_memory.md").read_text(encoding="utf-8")

    assert all(f"`{name}`" in session_prompt for name in SESSION_SECTIONS)
    assert all(f"`## {name}`" in long_term_prompt for name in LONG_TERM_SECTIONS)


def test_builtin_fallback_prompts_match_the_prompt_files():
    """兜底提示词与文件逐字一致：部署漏挂提示词目录时，行为不会悄悄换成另一套写法。"""
    for constant, name in (
        ("FALLBACK_UPDATE_MEMORY_PROMPT", "memory_update"),
        ("FALLBACK_LONG_TERM_MEMORY_PROMPT", "long_term_memory"),
        ("FALLBACK_COMPACT_PROMPT", "memory_compact"),
    ):
        expected = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
        assert getattr(memory_manager, constant).strip() == expected, constant


# ── 超出上限：按指令精简，不整份重写 ────────────────────────────────────────

_LONG = "频率支持 annual/semiannual/quarterly/monthly/weekly/daily/continuous，" * 3


class _ScriptedLLM:
    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[0].content)
        return SimpleNamespace(content=self.replies[len(self.prompts) - 1])


def test_fit_to_budget_drops_the_longest_entries_whole():
    markdown = SESSION + f"- {_LONG}\n- 结果用表格展示\n"

    fitted, dropped = fit_to_budget(markdown, title="会话记忆", sections=SESSION_SECTIONS, max_chars=120)

    assert dropped == 1 and len(fitted) <= 120
    assert "## 决策与理由" in fitted and "## 偏好与约定" in fitted
    assert "频率支持" not in fitted and "结果用表格展示" in fitted
    assert "记忆已压缩" not in fitted


def test_compaction_only_accepts_update_and_delete():
    reply = _ops(add=[{"section": "偏好与约定", "text": "新增的不算"}], delete=[2])

    change = apply_compaction(reply, SESSION, title="会话记忆", sections=SESSION_SECTIONS)

    assert "新增的不算" not in change.markdown and "注释一律用中文" not in change.markdown
    assert apply_compaction("# 会话记忆\n- 整份重写", SESSION, title="会话记忆", sections=SESSION_SECTIONS).markdown is None


def test_over_budget_session_memory_is_compacted_with_ops(tmp_path):
    memory_file = tmp_path / "memory.md"
    write_memory(memory_file, SESSION)
    llm = _ScriptedLLM(
        _ops(add=[{"section": "偏好与约定", "text": _LONG + str(i)} for i in range(4)]),
        _ops(delete=[3, 4, 5], update=[{"id": 6, "text": "支持多种复利频率"}]),
    )

    assert update_memory_after_session(memory_file, "加频率选项", "已加上", llm=llm) is True

    assert len(llm.prompts) == 2  # 一次增量修改 + 一次精简，不调整份重写的压缩
    assert "超过了 600 字的上限" in llm.prompts[1] and "- [6] " in llm.prompts[1]
    final = memory_file.read_text(encoding="utf-8")
    assert len(final) <= 600
    assert "## 决策与理由" in final and "## 偏好与约定" in final
    assert "- 数据库统一用 MySQL —— 开发与生产一致" in final and "- 支持多种复利频率" in final
    assert "记忆已压缩" not in final


def test_unusable_compaction_reply_falls_back_to_dropping_whole_entries(tmp_path):
    memory_file = tmp_path / "memory.md"
    write_memory(memory_file, SESSION)
    llm = _ScriptedLLM(
        _ops(add=[{"section": "偏好与约定", "text": _LONG + str(i)} for i in range(4)]),
        "# 会话记忆\n\n## 原则\n- 整份重写的回答不被接受\n",
    )

    assert update_memory_after_session(memory_file, "加频率选项", "已加上", llm=llm) is True

    final = memory_file.read_text(encoding="utf-8")
    assert len(llm.prompts) == 2 and len(final) <= 600
    assert "## 原则" not in final and "## 决策与理由" in final
    assert "- 注释一律用中文" in final  # 短的偏好留下，长的实现细节先丢
    assert "记忆已压缩" not in final
