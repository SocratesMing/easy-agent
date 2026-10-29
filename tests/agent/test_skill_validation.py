"""技能 frontmatter 校验（官方 Agent Skills 规范，deepagents 0.7.22+）。

官方硬约束：

- ``name`` 必填、必须与父目录名一致、1–64 字符、仅小写字母/数字/连字符；
- ``description`` 必填、≤1024 字符；
- ``compatibility`` ≤500 字符；
- ``metadata.include_tools`` 必须是空格分隔字符串（YAML 列表不会被框架匹配）；
- 发现阶段跳过 >10MB 的 ``SKILL.md``。
"""

from __future__ import annotations

import os
from pathlib import Path

from easy_agent.agent.skills import discover_skills, validate_skills


def _write_skill(root: Path, dir_name: str, frontmatter: str) -> Path:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n# body\n", encoding="utf-8")
    return skill_dir


def _errors(root: Path, dir_name: str) -> list[str]:
    items = {item["name"]: item for item in validate_skills(str(root))}
    return items[dir_name]["errors"]


def test_valid_skill_has_no_errors(tmp_path: Path):
    _write_skill(
        tmp_path,
        "pdf-tools",
        "name: pdf-tools\ndescription: 处理 PDF 时使用。\nmetadata:\n  include_tools: save_expert\n",
    )
    assert _errors(tmp_path, "pdf-tools") == []


def test_name_must_match_directory(tmp_path: Path):
    _write_skill(tmp_path, "pdf-tools", "name: other-name\ndescription: ok\n")
    assert any("必须与目录名" in e for e in _errors(tmp_path, "pdf-tools"))


def test_name_charset_rejects_underscore_and_uppercase(tmp_path: Path):
    _write_skill(tmp_path, "strategy_fx", "name: strategy_fx\ndescription: ok\n")
    assert any("只能包含小写字母" in e for e in _errors(tmp_path, "strategy_fx"))

    _write_skill(tmp_path, "Bad-Name", "name: Bad-Name\ndescription: ok\n")
    assert any("只能包含小写字母" in e for e in _errors(tmp_path, "Bad-Name"))


def test_name_length_limit(tmp_path: Path):
    long_name = "a" * 65
    _write_skill(tmp_path, long_name, f"name: {long_name}\ndescription: ok\n")
    assert any("超过 64 字符" in e for e in _errors(tmp_path, long_name))


def test_description_required_and_limited(tmp_path: Path):
    _write_skill(tmp_path, "no-desc", "name: no-desc\n")
    assert any("description 为空" in e for e in _errors(tmp_path, "no-desc"))

    _write_skill(tmp_path, "long-desc", "name: long-desc\ndescription: " + "x" * 1025 + "\n")
    assert any("description 超过 1024" in e for e in _errors(tmp_path, "long-desc"))


def test_compatibility_limit(tmp_path: Path):
    _write_skill(
        tmp_path,
        "compat",
        "name: compat\ndescription: ok\ncompatibility: " + "x" * 501 + "\n",
    )
    assert any("compatibility 超过 500" in e for e in _errors(tmp_path, "compat"))


def test_include_tools_list_form_is_rejected(tmp_path: Path):
    _write_skill(
        tmp_path,
        "list-tools",
        "name: list-tools\ndescription: ok\nmetadata:\n  include_tools:\n    - save_expert\n",
    )
    assert any("空格分隔字符串" in e for e in _errors(tmp_path, "list-tools"))


def test_missing_frontmatter_is_reported(tmp_path: Path):
    skill_dir = tmp_path / "no-front"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
    assert any("缺少 frontmatter" in e for e in _errors(tmp_path, "no-front"))


def test_discovery_skips_oversized_skill_md(tmp_path: Path):
    big = _write_skill(tmp_path, "big-skill", "name: big-skill\ndescription: ok\n")
    with open(big / "SKILL.md", "ab") as fh:
        fh.truncate(10 * 1024 * 1024 + 1)  # 稀疏文件，避免真的写 10MB

    names = [item["name"] for item in discover_skills(str(tmp_path))]
    assert "big-skill" not in names
    assert (big / "SKILL.md").stat().st_size > 10 * 1024 * 1024
    os.remove(big / "SKILL.md")
