"""专家技能作用域（``services/tool_scope.py``）的守护测试。

对应需求：「专家创建时选定的技能包，对话时只用这些；一个都没选 = 全库可见；
公共专家 + 本轮另选 = 并集」。

| # | 场景 | 预期 |
|---|---|---|
| 1 | 专家未绑定技能包 | ``skill_whitelist=None``（全量可见，= 改造前行为） |
| 2 | 专家绑定技能包 | 只有这些目录进白名单，来源 ``expert`` |
| 3 | 专家绑定 + 本轮另选 | 并集去重保序，来源 ``expert+turn`` |
| 4 | 技能已停用 | 从白名单剔除（停用技能不在挂载源里） |
| 5 | 绑定的技能全都不存在 | 回退全量（不让一次脏数据把专家变成没技能） |
| 6 | 总开关关闭 | 一律全量，用于线上快速回退 |
| 7 | 保存专家时归一 | 停用的**保留**（用户可能稍后启用），不存在的剔除 |
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.services import agent_manager as am
from easy_agent.services import tool_scope as ts
from easy_agent.bootstrap.config import Config
from easy_agent.services import skill_library

USER = "testuser"


def _skill(root: Path, name: str, *, description: str = "demo") -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody of {name}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """把用户工作区重定向到 tmp，并按需创建技能目录。"""
    workspace = tmp_path / "users" / USER
    monkeypatch.setattr(
        Config,
        "get_user_workspace_dir",
        staticmethod(lambda username, config=None: workspace),
    )
    # 运行时配置不参与本用例：公共技能根置空，只验证用户库口径
    monkeypatch.setattr(ts.skill_library, "resolve_skill_ref", lambda *a, **k: None)
    monkeypatch.setattr(skill_library, "resolve_skill_ref", lambda *a, **k: None)
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    disabled_dir = workspace / "skills-disabled"
    disabled_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(skills=skills_dir, disabled=disabled_dir, workspace=workspace)


@pytest.fixture()
def scope_enabled(monkeypatch):
    """默认开启作用域开关（不依赖服务端配置初始化）。"""
    monkeypatch.setattr(
        am, "_agent_config", {"config": SimpleNamespace(expert_tool_scope_enabled=True)}
    )


# ── 1~3：来源与并集 ───────────────────────────────────────────────────


def test_no_expert_skills_keeps_full_library(env, scope_enabled):
    """专家没绑定技能包 → 全量可见（不选 = 默认都可以使用）。"""
    _skill(env.skills, "alpha")
    _skill(env.skills, "beta")

    scope = ts.resolve_tool_scope(USER, expert_skills=[], turn_skills=["beta"], sid="t1")

    assert scope.skill_whitelist is None
    assert scope.source == "none"
    assert "全量可见" in scope.describe()


def test_expert_skills_only(env, scope_enabled):
    """专家绑定了技能包 → 白名单就是这些（目录名口径）。"""
    _skill(env.skills, "alpha")
    _skill(env.skills, "beta")

    scope = ts.resolve_tool_scope(USER, expert_skills=["alpha"], sid="t2")

    assert scope.skill_whitelist == ("alpha",)
    assert scope.source == "expert"
    assert scope.signature() == (("alpha",),)


def test_expert_plus_turn_is_union(env, scope_enabled):
    """公共专家（或自建专家）+ 本轮另选技能 = 并集（需求点 4）。"""
    _skill(env.skills, "alpha")
    _skill(env.skills, "beta")
    _skill(env.skills, "gamma")

    scope = ts.resolve_tool_scope(
        USER, expert_skills=["alpha"], turn_skills=["gamma", "alpha"], sid="t3"
    )

    assert scope.skill_whitelist == ("alpha", "gamma")
    assert scope.source == "expert+turn"


def test_turn_skill_alias_resolves_to_dir(env, scope_enabled):
    """中文展示名会按别名解析成 canonical 目录名（存量技能包的常见形态）。"""
    _skill(env.skills, "gold-desk", description="金市营收情况")
    (env.skills / "gold-desk" / "SKILL.md").write_text(
        "---\nname: 金市营收情况\ndescription: demo\n---\nbody\n", encoding="utf-8"
    )

    scope = ts.resolve_tool_scope(USER, expert_skills=["金市营收情况"], sid="t4")

    assert scope.skill_whitelist == ("gold-desk",)


# ── 4~5：失效技能 ─────────────────────────────────────────────────────


def test_disabled_skill_is_dropped(env, scope_enabled):
    """停用的技能不在挂载源里，列进白名单只会让模型看到读不到的技能。"""
    _skill(env.skills, "alpha")
    _skill(env.disabled, "beta")

    scope = ts.resolve_tool_scope(USER, expert_skills=["alpha", "beta"], sid="t5")

    assert scope.skill_whitelist == ("alpha",)


def test_all_invalid_falls_back_to_full(env, scope_enabled):
    """绑定的技能全都不存在 → 回退全量（记 WARNING），不把专家变成"零技能"。"""
    _skill(env.skills, "alpha")

    scope = ts.resolve_tool_scope(USER, expert_skills=["gone-one", "gone-two"], sid="t6")

    assert scope.skill_whitelist is None
    assert scope.source == "expert"


# ── 6：总开关 ─────────────────────────────────────────────────────────


def test_scope_switch_off_returns_full(env, monkeypatch):
    """``expert.tool_scope_enabled=false`` → 全量挂载（线上快速回退）。"""
    _skill(env.skills, "alpha")
    monkeypatch.setattr(
        am, "_agent_config", {"config": SimpleNamespace(expert_tool_scope_enabled=False)}
    )

    scope = ts.resolve_tool_scope(USER, expert_skills=["alpha"], sid="t7")

    assert scope.skill_whitelist is None
    assert scope.source == "disabled"


# ── 7：保存专家时的归一 ───────────────────────────────────────────────


def test_normalize_expert_skills_keeps_disabled_drops_missing(env):
    """保存专家：停用的技能保留（以后可能启用），不存在的剔除。"""
    _skill(env.skills, "alpha")
    _skill(env.disabled, "beta")

    kept, dropped = ts.normalize_expert_skills(USER, ["alpha", "beta", "gone", "alpha"], "t8")

    assert kept == ["alpha", "beta"]
    assert dropped == ["gone"]
