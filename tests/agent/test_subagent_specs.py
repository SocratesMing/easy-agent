"""Subagent specs 回归测试（FW-07 / DA-10）：显式 skills、异常兜底与调用上限。"""

from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

from easy_agent.agent import build_subagent_specs
from easy_agent.agent.subagents import DEFAULT_SUBAGENTS


def test_subagents_get_explicit_skills_and_middleware():
    specs = build_subagent_specs("sess-1", result_log_truncate=500)
    assert len(specs) == len(DEFAULT_SUBAGENTS) + 1
    assert {s["name"] for s in specs} == {
        "general-purpose",
        "office-writer",
        "knowledge-researcher",
    }
    for spec in specs:
        assert spec["skills"] == ["/user-skills/"], (
            "官方语义：自定义 subagent 不继承主 agent skills，必须显式声明"
        )
        assert spec["middleware"], "子代理需要异常兜底/日志 middleware"


def test_subagents_share_the_main_agent_skill_sources():
    """DA-13 S2：公共技能挪到 /skills/builtin/ 后，office-writer 等子代理也要读得到。"""
    sources = ["/skills/builtin/", "/user-skills/"]
    for spec in build_subagent_specs("sess-1", skills_paths=sources):
        assert spec["skills"] == sources, spec["name"]


def test_general_purpose_override_keeps_upstream_prompt_and_inherits_tools():
    gp = next(s for s in build_subagent_specs() if s["name"] == "general-purpose")
    assert gp["description"] == GENERAL_PURPOSE_SUBAGENT["description"]
    assert gp["system_prompt"] == GENERAL_PURPOSE_SUBAGENT["system_prompt"]
    # 不声明 tools / interrupt_on → 按官方语义继承主代理的工具与审批配置
    assert "tools" not in gp
    assert "interrupt_on" not in gp


def test_every_subagent_has_call_limits_that_end_gracefully():
    for spec in build_subagent_specs(max_steps=7):
        model_limit = next(
            m for m in spec["middleware"] if isinstance(m, ModelCallLimitMiddleware)
        )
        tool_limit = next(
            m for m in spec["middleware"] if isinstance(m, ToolCallLimitMiddleware)
        )
        assert model_limit.run_limit == 7
        assert tool_limit.run_limit == 28
        # 子代理超限以消息收尾交回主代理，而不是抛异常打断整轮对话
        assert model_limit.exit_behavior == "end"
        assert tool_limit.exit_behavior == "end"


def test_specs_do_not_share_middleware_instances():
    first, second = build_subagent_specs(), build_subagent_specs()
    ids = {id(m) for spec in first for m in spec["middleware"]}
    assert not ids & {id(m) for spec in second for m in spec["middleware"]}


def test_specs_do_not_mutate_defaults():
    before = [dict(s) for s in DEFAULT_SUBAGENTS]
    gp_before = dict(GENERAL_PURPOSE_SUBAGENT)
    build_subagent_specs("sess-2")
    assert [dict(s) for s in DEFAULT_SUBAGENTS] == before
    assert dict(GENERAL_PURPOSE_SUBAGENT) == gp_before


# ── DA-11：子代理只拿需要的工具 ───────────────────────────────────────


def _named_tools(*names):
    from langchain_core.tools import StructuredTool

    return [
        StructuredTool.from_function(func=lambda: "ok", name=name, description=f"{name} tool")
        for name in names
    ]


def test_subagent_tools_are_narrowed_to_what_they_need():
    from easy_agent.agent.subagents import resolve_subagent_tools

    available = _named_tools(
        "web_search", "create_scheduled_task", "read_uploaded_file", "access_knowledge_original"
    )
    specs = {s["name"]: s for s in resolve_subagent_tools(build_subagent_specs(), available)}

    assert [t.name for t in specs["office-writer"]["tools"]] == ["read_uploaded_file"]
    assert [t.name for t in specs["knowledge-researcher"]["tools"]] == ["access_knowledge_original"]
    assert "tools" not in specs["general-purpose"]  # 官方语义：继承主代理工具
    for spec in specs.values():
        assert "use_tools" not in spec and "requires_tools" not in spec


def test_knowledge_researcher_is_dropped_without_its_tool():
    from easy_agent.agent.subagents import resolve_subagent_tools

    specs = {s["name"]: s for s in resolve_subagent_tools(build_subagent_specs(), _named_tools("web_search"))}

    assert "knowledge-researcher" not in specs
    assert specs["office-writer"]["tools"] == []
