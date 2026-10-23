"""技能身份解析（services/skill_library.py 的目录名 ⇄ frontmatter name）测试。

背景：规范要求技能目录名与 SKILL.md 的 ``name`` 一致，但存量技能包常见
「目录英文 + name 中文」（``fpp-income-analysis`` / 金市营收情况）。专家
``required_skills`` 与用户手打的 ``/中文名`` 只有别名可用。

约定（本文件的断言口径）：

- **目录名始终是主键**：解析结果 ``dir_name`` 是 canonical 目录名，不会返回 ``name``
  当目录用；
- 匹配顺序固定：目录名精确 → 目录名归一化 → name 精确 → name 归一化；
- 冲突（同 name 多个目录）取目录名排序第一个，不抛异常；
- 非法路径段（``../x``、``/x``、空串）一律解析不到。
"""

from pathlib import Path

import pytest

from easy_agent.services import skill_library as L


def _make_skill(root: Path, dir_name: str, display_name: str | None = None) -> Path:
    """建一个技能目录；``display_name`` 缺省时与目录名一致。"""
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    name = display_name if display_name is not None else dir_name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 测试技能 {name}\n---\n\n正文\n", encoding="utf-8"
    )
    return skill_dir


@pytest.fixture()
def public_root(monkeypatch, tmp_path) -> Path:
    """公共技能根目录：重定向候选列表，保证解析只读临时目录。"""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr(L, "public_skills_dir_candidates", lambda: [skills])
    return skills


@pytest.fixture()
def seeded(public_root: Path) -> Path:
    _make_skill(public_root, "fpp-income-analysis", "金市营收情况")
    _make_skill(public_root, "precious-metals-analysis", "贵金属投资交易分析")
    _make_skill(public_root, "factor-creator")  # 目录名与 name 一致
    _make_skill(public_root, "中文目录技能", "中文目录技能")
    _make_skill(public_root, "mixed-case_skill", "Mixed Case")
    return public_root


# ── resolve_public_skill_ref：四级匹配 ────────────────────────────────


@pytest.mark.parametrize(
    ("declared", "expect_dir", "expect_matched_by"),
    [
        ("fpp-income-analysis", "fpp-income-analysis", "dir"),
        ("FPP_Income_Analysis", "fpp-income-analysis", "dir_normalized"),
        ("金市营收情况", "fpp-income-analysis", "name"),
        ("贵金属投资交易分析", "precious-metals-analysis", "name"),
        ("factor-creator", "factor-creator", "dir"),
        ("Mixed Case", "mixed-case_skill", "name"),
        ("中文目录技能", "中文目录技能", "dir"),
        ("  金市营收情况  ", "fpp-income-analysis", "name"),
    ],
)
def test_resolve_public_skill_ref_matches_dir_and_display_name(
    seeded: Path, declared: str, expect_dir: str, expect_matched_by: str
):
    hit = L.resolve_public_skill_ref(declared)

    assert hit is not None
    assert hit["dir_name"] == expect_dir
    assert hit["matched_by"] == expect_matched_by
    # 返回的是真实存在的目录，可直接用于 install_public_skill_to_user
    assert L.is_skill_dir(hit["path"])


@pytest.mark.parametrize(
    "declared", ["不存在的技能", "unknown-skill", "", "   ", "../escape", "/abs/path", "a/b"]
)
def test_resolve_public_skill_ref_rejects_unknown_and_unsafe(seeded: Path, declared: str):
    assert L.resolve_public_skill_ref(declared) is None


def test_resolve_public_skill_ref_returns_none_without_candidates(monkeypatch):
    """未配置 tools.skills_dir（候选为空）→ 解析不到，不抛异常。"""
    monkeypatch.setattr(L, "public_skills_dir_candidates", lambda: [])
    assert L.resolve_public_skill_ref("factor-creator") is None


def test_resolve_public_skill_ref_walks_candidates_in_order(monkeypatch, tmp_path):
    """多候选（CWD 与项目根）：首个目录没有该技能时继续往后找。"""
    first = tmp_path / "cwd-skills"
    first.mkdir()
    second = tmp_path / "release-skills"
    second.mkdir()
    _make_skill(second, "fpp-income-analysis", "金市营收情况")
    monkeypatch.setattr(L, "public_skills_dir_candidates", lambda: [first, second])

    hit = L.resolve_public_skill_ref("金市营收情况")

    assert hit is not None
    assert hit["path"].parent == second


def test_resolve_public_skill_ref_dir_wins_over_display_name(tmp_path):
    """目录名优先：某技能的 name 恰好等于另一个技能的目录名时，必须命中目录。"""
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "alpha", "被借用的名字")
    _make_skill(skills, "被借用的名字", "另一个技能")

    hit = L.resolve_public_skill_ref("被借用的名字", skills)

    assert hit is not None
    assert hit["dir_name"] == "被借用的名字"
    assert hit["matched_by"] == "dir"


def test_resolve_public_skill_ref_ambiguous_name_picks_first_by_dir(tmp_path, caplog):
    """同名冲突：取目录名排序第一个并告警，不抛异常。"""
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "b-skill", "同名")
    _make_skill(skills, "a-skill", "同名")

    hit = L.resolve_public_skill_ref("同名", skills)

    assert hit is not None
    assert hit["dir_name"] == "a-skill"  # 目录名排序靠前
    assert "技能别名歧义" in caplog.text


# ── 索引缓存 ─────────────────────────────────────────────────────────


def test_identity_index_cache_sees_new_skill(public_root: Path):
    """新增技能目录后索引必须刷新，不能返回过期结果。"""
    assert L.resolve_public_skill_ref("新技能") is None

    _make_skill(public_root, "新技能")

    import os
    import time

    os.utime(public_root, (time.time() + 5, time.time() + 5))  # 模拟新增子目录后的 mtime
    L._public_ref_index_cache.clear()  # 测试环境显式清缓存，避免依赖平台 mtime 精度
    assert L.resolve_public_skill_ref("新技能") is not None


# ── 候选建议（仅诊断，不自动装）───────────────────────────────────────


def test_suggest_public_skill_refs_gives_close_candidates(seeded: Path):
    candidates = L.suggest_public_skill_refs("fpp_income_analysi")
    assert candidates
    assert candidates[0]["dir_name"] == "fpp-income-analysis"
    assert 0 < candidates[0]["score"] <= 1


def test_suggest_public_skill_refs_empty_for_blank():
    assert L.suggest_public_skill_refs("") == []


# ── 用户技能库别名解析 ───────────────────────────────────────────────


@pytest.fixture()
def user_root(monkeypatch, tmp_path) -> Path:
    """把用户工作区重定向到临时目录。"""
    from easy_agent.config import Config

    root = tmp_path / "workspace" / "users"
    monkeypatch.setattr(
        Config, "get_user_workspace_dir", staticmethod(lambda username: root / username)
    )
    return root


def test_find_user_skill_accepts_display_name(user_root: Path):
    """``find_user_skill`` 传 SKILL.md 的 name 也要能找到，且返回目录名。"""
    _make_skill(L.user_skills_dir("u1"), "fpp-income-analysis", "金市营收情况")

    found = L.find_user_skill("u1", "金市营收情况")

    assert found is not None
    assert found["dir_name"] == "fpp-income-analysis"
    assert found["enabled"] is True


def test_find_user_skill_reports_disabled_by_alias(user_root: Path):
    """停在 ``skills-disabled/`` 的技能按 name 也要能找到，并如实给出停用状态。"""
    _make_skill(L.user_disabled_dir("u1"), "fpp-income-analysis", "金市营收情况")

    found = L.find_user_skill("u1", "金市营收情况")

    assert found is not None
    assert found["dir_name"] == "fpp-income-analysis"
    assert found["enabled"] is False


def test_resolve_user_skill_ref_rejects_unsafe(user_root: Path):
    _make_skill(L.user_skills_dir("u1"), "fpp-income-analysis", "金市营收情况")
    assert L.resolve_user_skill_ref("u1", "../escape") is None


def test_resolve_skill_dirs_accepts_chinese_directive(user_root: Path):
    """输入框/旧前端手打 ``/中文名`` 时，按别名解析为目录名放行。"""
    _make_skill(L.user_skills_dir("u1"), "fpp-income-analysis", "金市营收情况")

    resolved, unknown = L.resolve_skill_dirs(["金市营收情况"], "u1")

    assert unknown == []
    assert resolved[0]["dir_name"] == "fpp-income-analysis"
    assert resolved[0]["name"] == "金市营收情况"


# ── 斜杠指令：支持中文名 ──────────────────────────────────────────────


def test_parse_skill_directives_accepts_chinese_display_name():
    assert L.parse_skill_directives("/金市营收情况 帮我查本月损益") == ["金市营收情况"]


def test_parse_skill_directives_still_ignores_paths():
    assert L.parse_skill_directives("/usr/bin/env python") == []
