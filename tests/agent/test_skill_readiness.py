"""技能数据通道预检（agent/skill_readiness.py）的单测。

对应事故：2026-11-19 金市营收会话里，fpp-income-analysis 的 SKILL.md 要求 12 个
``*_tool``，会话只注入了外汇/贵金属 MCP 工具（交集 0），模型读完文档无工具可调，
反复重读同一份 SKILL.md（思考 297 字符 ×3 逐字相同）直至守卫收口。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from easy_agent.agent.skill_readiness import (  # noqa: E402
    SkillGap,
    declared_tools,
    format_gap_hint,
    gap_fingerprint,
    skill_tool_gaps,
)

_REPO = Path(__file__).resolve().parents[2]
_FPP = _REPO / "easy_agent" / "skills" / "fpp-income-analysis" / "SKILL.md"


def test_declared_tools_reads_backticked_tool_names_from_real_skill():
    """真实 SKILL.md：第三节点名的工具应被识别，带参数的调用式写法不能算。"""
    if not _FPP.is_file():  # pragma: no cover - 仓库缺该技能时跳过
        pytest.skip("fpp-income-analysis 不在仓库")
    declared = declared_tools(_FPP.read_text(encoding="utf-8"))

    assert "get_income_matrix_tool" in declared
    assert "get_plan_completion_tool" in declared
    assert "list_dimensions_tool" in declared
    # 正文里还有 `get_income_matrix(metric='net_opr_incom')` 这种带参数的写法，
    # 它不是工具全名，不能当成一次声明
    assert "get_income_matrix" not in declared
    assert len(declared) >= 10


def test_declared_tools_reads_frontmatter_include_tools():
    text = (
        "---\n"
        "name: demo\n"
        "description: d\n"
        "metadata:\n"
        "  include_tools: save_expert fetch_url\n"
        "---\n"
        "# demo\n"
    )
    assert declared_tools(text) == ("save_expert", "fetch_url")


def test_declared_tools_empty_for_plain_skill():
    text = "---\nname: demo\ndescription: d\n---\n# demo\n用 `read_file` 读文件。\n"
    assert declared_tools(text) == ()


def test_gap_is_fully_missing_when_no_tool_available(tmp_path):
    skill = tmp_path / "income"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: income\ndescription: d\n---\n"
        "调用 `get_income_overview_tool` 与 `rank_income_tool`。\n",
        encoding="utf-8",
    )

    gaps = skill_tool_gaps([tmp_path], ["fx_kline_qa", "gold_analysis"])

    assert set(gaps) == {"income"}
    gap = gaps["income"]
    assert gap.total == 2
    assert gap.available == 0
    assert gap.fully_missing is True
    assert set(gap.missing) == {"get_income_overview_tool", "rank_income_tool"}
    assert "数据通道未接入" in format_gap_hint(gap)


def test_gap_uses_suffix_match_for_prefixed_mcp_tools(tmp_path):
    """开启 MCP 前缀后实际工具叫 ``baop__get_income_overview_tool``，仍算就位。"""
    skill = tmp_path / "income"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: income\ndescription: d\n---\n调用 `get_income_overview_tool`。\n",
        encoding="utf-8",
    )

    assert skill_tool_gaps([tmp_path], ["baop__get_income_overview_tool"]) == {}


def test_gap_partial_when_some_tools_missing(tmp_path):
    skill = tmp_path / "mixed"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: mixed\ndescription: d\n---\n"
        "`get_a_tool` 与 `get_b_tool`。\n",
        encoding="utf-8",
    )

    gap = skill_tool_gaps([tmp_path], ["get_a_tool"])["mixed"]
    assert gap.fully_missing is False
    assert gap.missing == ("get_b_tool",)
    hint = format_gap_hint(gap)
    assert "⚠️" in hint
    assert "get_b_tool" in hint


def test_skill_without_declaration_is_ignored(tmp_path):
    skill = tmp_path / "plain"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: plain\ndescription: d\n---\n随便写点说明。\n", encoding="utf-8"
    )
    assert skill_tool_gaps([tmp_path], []) == {}


def test_gap_fingerprint_stable_and_sorted(tmp_path):
    gaps = {
        "b": SkillGap(declared=("x_tool",), missing=("x_tool",)),
        "a": SkillGap(declared=("y_tool", "z_tool"), missing=("y_tool",)),
    }
    fp = gap_fingerprint(gaps)
    # 格式：gap:<技能>=<缺失数>/<声明数>:<缺失清单>
    assert fp == ["gap:a=1/2:y_tool", "gap:b=1/1:x_tool"]
    assert gap_fingerprint(gaps) == fp


def test_gap_fingerprint_distinguishes_fully_missing_transition():
    """审核发现的缺陷：只压 missing 时，"全缺"→"部分就位" 指纹相同。

    提示文案由 fully_missing 决定（⛔ vs ⚠️），指纹必须跟着变，否则技能升级后
    进程内旧图会永久渲染 ⛔，让模型拒绝一个已经部分可用的技能。
    """
    fully = {"s": SkillGap(declared=("x_tool",), missing=("x_tool",))}
    partly = {"s": SkillGap(declared=("x_tool", "save_expert"), missing=("x_tool",))}
    assert gap_fingerprint(fully) != gap_fingerprint(partly)


def test_missing_skill_dir_is_tolerated():
    assert skill_tool_gaps([None, "/no/such/dir"], []) == {}


def test_builtin_tool_declaration_is_not_a_gap(tmp_path):
    """技能用 include_tools 声明框架内置工具时不能判成"数据通道缺工具"。

    内置工具永远在（不来自 MCP、也不进 raw_tools），core._skill_gaps 会把
    ``FRAMEWORK_BUILTIN_TOOLS`` 并进可用集；这里锁住这条契约。
    """
    from easy_agent.agent.skill_readiness import FRAMEWORK_BUILTIN_TOOLS

    skill = tmp_path / "reader"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reader\ndescription: d\nmetadata:/n  include_tools: read_file grep\n---\n",
        encoding="utf-8",
    )
    assert skill_tool_gaps([tmp_path], list(FRAMEWORK_BUILTIN_TOOLS)) == {}
    assert "read_file" in FRAMEWORK_BUILTIN_TOOLS


def test_builtin_tool_declaration_is_not_a_gap(tmp_path):
    """技能用 include_tools 声明框架内置工具时不能判成"数据通道缺工具"。

    内置工具永远在（不来自 MCP、也不进 raw_tools），core._skill_gaps 会把
    ``FRAMEWORK_BUILTIN_TOOLS`` 并进可用集；这里锁住这条契约。
    """
    from easy_agent.agent.skill_readiness import FRAMEWORK_BUILTIN_TOOLS

    skill = tmp_path / "reader"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reader\ndescription: d\nmetadata:\n  include_tools: read_file grep\n---\n",
        encoding="utf-8",
    )
    assert skill_tool_gaps([tmp_path], list(FRAMEWORK_BUILTIN_TOOLS)) == {}
    assert "read_file" in FRAMEWORK_BUILTIN_TOOLS
