"""DA-13：``metadata.include_tools`` 的解析与子代理门控（官方 skills 规范）。

官方约定（deepagents 0.7.22+）：

- ``include_tools`` 写在 ``SKILL.md`` frontmatter 的 ``metadata`` 下，**空格分隔字符串**；
  YAML 列表形式框架不会匹配；
- 工具在模型读到（或 pin 了）声明它的技能之前不可见；
- 自定义子代理不继承主代理的 middleware，需要用同一批工具实例各挂一份
  ``SkillsMiddleware(tools=...)``。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from easy_agent.agent.skills import declared_include_tools, skill_tool_resolver
from easy_agent.agent.subagents import apply_skill_gated_tools


def _write_skill(root: Path, name: str, frontmatter: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n{frontmatter}---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_collects_space_separated_tools(tmp_path: Path):
    _write_skill(tmp_path, "alpha", "metadata:\n  include_tools: tool_a tool_b\n")
    _write_skill(tmp_path, "beta", "metadata:\n  include_tools: tool_c\n")
    _write_skill(tmp_path, "gamma", "")  # 未声明
    assert declared_include_tools([tmp_path]) == frozenset({"tool_a", "tool_b", "tool_c"})


def test_ignores_yaml_list_form(tmp_path: Path):
    """官方明确：YAML 列表形式永远匹配不到，解析器同样忽略。"""
    _write_skill(tmp_path, "alpha", "metadata:\n  include_tools:\n    - tool_a\n")
    assert declared_include_tools([tmp_path]) == frozenset()


def test_missing_dirs_and_broken_frontmatter_are_skipped(tmp_path: Path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
    assert declared_include_tools([tmp_path, tmp_path / "nope"]) == frozenset()
    assert declared_include_tools(None) == frozenset()


@tool
def gated_tool(value: str) -> str:
    """A gated tool used by a skill."""
    return value


@tool
def plain_tool(value: str) -> str:
    """A normal tool."""
    return value


class _FakeBackend:
    """apply_skill_gated_tools 只把 backend 传给 SkillsMiddleware，这里不需要真实实现。"""


def test_subagent_gated_tools_move_into_skills_middleware():
    specs = [
        {"name": "writer", "tools": [gated_tool, plain_tool]},
        {"name": "helper", "tools": [plain_tool]},
        {"name": "inherit"},  # 没有 tools：按官方语义继承主代理，不能动
    ]
    result = apply_skill_gated_tools(
        specs, [gated_tool], ["/skills/builtin/", "/user-skills/"], _FakeBackend()
    )

    writer, helper, inherit = result
    assert writer["tools"] == [plain_tool]
    middleware = writer["middleware"][0]
    assert middleware.name == "SkillsMiddleware"
    assert middleware.sources == ["/skills/builtin/", "/user-skills/"]
    assert "gated_tool" not in [t.name for t in writer["tools"]]

    assert helper["tools"] == [plain_tool]
    assert "middleware" not in helper

    assert "tools" not in inherit and "middleware" not in inherit


def test_subagent_with_only_gated_tools_keeps_empty_tools_list():
    """只剩门控工具时保留 tools=[]：删掉键会退回「继承主代理全部工具」。"""
    result = apply_skill_gated_tools(
        [{"name": "only", "tools": [gated_tool]}], [gated_tool], ["/user-skills/"], _FakeBackend()
    )
    assert result[0]["tools"] == []
    assert result[0]["middleware"][0].name == "SkillsMiddleware"


def test_no_skills_paths_keeps_tools_untouched():
    spec = {"name": "writer", "tools": [gated_tool]}
    result = apply_skill_gated_tools([spec], [gated_tool], [], _FakeBackend())
    assert result[0]["tools"] == [gated_tool]


# ── D2：没配公共技能目录 ──────────────────────────────────────────────


def test_unconfigured_public_skills_dir_is_skipped(tmp_path: Path):
    """没配公共技能目录时 EasyAgent 传进来的是 None，改造前这里抛 TypeError、建图失败。"""
    _write_skill(tmp_path, "alpha", "metadata:\n  include_tools: tool_a\n")
    assert declared_include_tools([None, tmp_path]) == frozenset({"tool_a"})
    assert declared_include_tools([None, ""]) == frozenset()


# ── D4：开启 MCP 前缀后，技能里写的仍是原名 ──────────────────────────────


@tool("srv__fetch_data")
def prefixed_tool(value: str) -> str:
    """An MCP tool renamed with its server prefix."""
    return value


@tool("other__fetch_data")
def same_name_other_server(value: str) -> str:
    """The same MCP tool name on another server."""
    return value


def test_skill_tool_resolver_matches_exact_then_unique_suffix():
    resolve = skill_tool_resolver([gated_tool, prefixed_tool])

    assert resolve("gated_tool", None) == [gated_tool]
    assert resolve("srv__fetch_data", None) == [prefixed_tool]
    assert resolve("fetch_data", None) == [prefixed_tool]  # 原名 → 唯一的带前缀工具
    assert resolve("unknown", None) == []


def test_skill_tool_resolver_does_not_guess_ambiguous_names():
    resolve = skill_tool_resolver([prefixed_tool, same_name_other_server])

    assert resolve("fetch_data", None) == []
    assert resolve("other__fetch_data", None) == [same_name_other_server]


def test_subagent_skills_middleware_resolves_original_names(monkeypatch):
    """子代理各挂一份 SkillsMiddleware，同样要能用原名带出带前缀的工具。"""
    import deepagents.middleware.skills as skills_mod

    recorded: list[dict] = []

    class _Recorder:
        name = "SkillsMiddleware"

        def __init__(self, **kwargs):
            recorded.append(kwargs)

    monkeypatch.setattr(skills_mod, "SkillsMiddleware", _Recorder)
    apply_skill_gated_tools(
        [{"name": "fetcher", "tools": [prefixed_tool, plain_tool]}],
        [prefixed_tool],
        ["/user-skills/"],
        _FakeBackend(),
    )

    assert recorded[0]["tools"]("fetch_data", None) == [prefixed_tool]
