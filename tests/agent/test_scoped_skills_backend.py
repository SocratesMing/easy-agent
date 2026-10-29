"""``backends/scoped_skills.py``：专家技能硬隔离的读写边界。

技能清单由 ``SkillsMiddleware`` 经 ``backend.ls`` 发现，所以白名单必须在
**backend 层**过滤；同时未选中的技能也必须「读不到」——否则模型可以直接
``read_file`` 打开它的 ``SKILL.md``（里面写着脚本路径和执行方式）。

用例只依赖临时目录，不建 Agent。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from easy_agent.backends.scoped_skills import ScopedSkillsBackend


def _skill(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: demo\n---\nbody of {name}\n", encoding="utf-8"
    )
    (root / name / "script.py").write_text("print('hi')\n", encoding="utf-8")


@pytest.fixture()
def root(tmp_path) -> Path:
    for name in ("alpha", "beta", "gamma"):
        _skill(tmp_path, name)
    (tmp_path / "README.md").write_text("库说明\n", encoding="utf-8")
    return tmp_path


def test_scope_none_exposes_everything(root):
    """``None`` = 全量（改造前行为），一个都不少。"""
    backend = ScopedSkillsBackend(root, None)
    names = {entry["path"] for entry in (backend.ls("/").entries or [])}

    assert names == {"/alpha/", "/beta/", "/gamma/", "/README.md"}
    assert backend.read("/beta/SKILL.md").file_data is not None


def test_empty_scope_hides_all_skills(root):
    """``[]`` = 显式无技能（白名单归一后剩空集合时不会走到这里，但语义要成立）。"""
    backend = ScopedSkillsBackend(root, frozenset())
    names = {entry["path"] for entry in (backend.ls("/").entries or [])}

    assert names == {"/README.md"}  # 根下散落文件不是技能，保留
    assert backend.read("/alpha/SKILL.md").error


def test_only_whitelisted_visible(root):
    """只有白名单内的技能出现在清单里。"""
    backend = ScopedSkillsBackend(root, {"alpha"})
    names = {entry["path"] for entry in (backend.ls("/").entries or [])}

    assert names == {"/alpha/", "/README.md"}


def test_read_outside_scope_is_blocked(root):
    """未选中的技能读不到（read / grep / glob / download 四条路径）。"""
    backend = ScopedSkillsBackend(root, {"alpha"})

    assert backend.read("/beta/SKILL.md").error
    assert backend.read("/beta/script.py").error
    assert not backend.glob("**/*.md", "/beta").matches
    assert backend.download_files(["/beta/SKILL.md"])[0].error == "file_not_found"
    # 自己选中的技能照常可读
    assert backend.download_files(["/alpha/SKILL.md"])[0].error is None


def test_write_stays_available(root):
    """用户技能库仍可写（``agent.skill_write=new-only`` 需要能新建技能）。"""
    backend = ScopedSkillsBackend(root, {"alpha"})

    result = backend.write("/new-skill/SKILL.md", "---\nname: new-skill\n---\n")

    assert result.error is None
    assert (root / "new-skill" / "SKILL.md").is_file()
