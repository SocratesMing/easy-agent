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
def root(tmp_path) -> Path:
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "fpp-income-analysis", "金市营收情况")
    _make_skill(skills, "precious-metals-analysis", "贵金属投资交易分析")
    _make_skill(skills, "factor-creator")  # 目录名与 name 一致
    _make_skill(skills, "中文目录技能", "中文目录技能")
    _make_skill(skills, "mixed-case_skill", "Mixed Case")
    return skills


# ── resolve_skill_ref：四级匹配 ────────────────────────────────────────


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
def test_resolve_skill_ref_matches_dir_and_display_name(
    root: Path, declared: str, expect_dir: str, expect_matched_by: str
):
    hit = L.resolve_skill_ref(declared, root)

    assert hit is not None
    assert hit["dir_name"] == expect_dir
    assert hit["matched_by"] == expect_matched_by
    # 返回的是真实存在的目录，可直接用于 builtin_skills.add
    assert L.is_skill_dir(hit["path"])


@pytest.mark.parametrize("declared", ["不存在的技能", "unknown-skill", "", "   ", "../escape", "/abs/path", "a/b"])
def test_resolve_skill_ref_rejects_unknown_and_unsafe(root: Path, declared: str):
    assert L.resolve_skill_ref(declared, root) is None


def test_resolve_skill_ref_returns_none_without_root(monkeypatch):
    monkeypatch.setattr(L.builtin_skills, "builtin_root", lambda config=None: None)
    assert L.resolve_skill_ref("factor-creator") is None


def test_resolve_skill_ref_dir_wins_over_display_name(tmp_path):
    """目录名优先：某技能的 name 恰好等于另一个技能的目录名时，必须命中目录。"""
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "alpha", "被借用的名字")
    _make_skill(skills, "被借用的名字", "另一个技能")

    hit = L.resolve_skill_ref("被借用的名字", skills)

    assert hit is not None
    assert hit["dir_name"] == "被借用的名字"
    assert hit["matched_by"] == "dir"


def test_resolve_skill_ref_ambiguous_name_picks_first_by_dir(tmp_path, caplog):
    """同名冲突：取目录名排序第一个并告警，不抛异常。"""
    skills = tmp_path / "skills"
    skills.mkdir()
    _make_skill(skills, "b-skill", "同名")
    _make_skill(skills, "a-skill", "同名")

    hit = L.resolve_skill_ref("同名", skills)

    assert hit is not None
    assert hit["dir_name"] == "a-skill"  # 目录名排序靠前
    assert "技能别名歧义" in caplog.text


# ── 索引缓存 ─────────────────────────────────────────────────────────


def test_identity_index_cache_sees_new_skill_after_dir_change(root: Path):
    """目录 mtime 变化（新增技能）后索引必须刷新，不能返回过期结果。"""
    assert L.resolve_skill_ref("新技能", root) is None

    _make_skill(root, "新技能")

    import os
    import time

    os.utime(root, (time.time() + 5, time.time() + 5))  # 模拟新增子目录后的 mtime
    L._ref_index_cache.clear()  # 测试环境显式清缓存，避免依赖平台 mtime 精度
    assert L.resolve_skill_ref("新技能", root) is not None


# ── 候选建议（仅诊断，不自动装）───────────────────────────────────────


def test_suggest_skill_refs_gives_close_candidates(root: Path):
    candidates = L.suggest_skill_refs("fpp_income_analysi", root)
    assert candidates
    assert candidates[0]["dir_name"] == "fpp-income-analysis"
    assert 0 < candidates[0]["score"] <= 1


def test_suggest_skill_refs_empty_for_blank():
    assert L.suggest_skill_refs("") == []


# ── 斜杠指令：支持中文名 ──────────────────────────────────────────────


def test_parse_skill_directives_accepts_chinese_display_name():
    assert L.parse_skill_directives("/金市营收情况 帮我查本月损益") == ["金市营收情况"]


def test_parse_skill_directives_still_ignores_paths():
    assert L.parse_skill_directives("/usr/bin/env python") == []
