"""Subagent specs 回归测试（FW-07）：显式 skills + 异常兜底 middleware。"""

from easy_agent.agent import build_subagent_specs
from easy_agent.agent.subagents import DEFAULT_SUBAGENTS


def test_subagents_get_explicit_skills_and_middleware():
    specs = build_subagent_specs("sess-1", result_log_truncate=500)
    assert len(specs) == len(DEFAULT_SUBAGENTS)
    assert {s["name"] for s in specs} == {"office-writer", "knowledge-researcher"}
    for spec in specs:
        assert spec["skills"] == ["/user-skills/"], (
            "官方语义：自定义 subagent 不继承主 agent skills，必须显式声明"
        )
        assert spec["middleware"], "子代理需要异常兜底/日志 middleware"


def test_specs_do_not_mutate_defaults():
    before = [dict(s) for s in DEFAULT_SUBAGENTS]
    build_subagent_specs("sess-2")
    assert [dict(s) for s in DEFAULT_SUBAGENTS] == before
