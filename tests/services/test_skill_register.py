"""技能登记（skill_library.register_skill_source）：对话创建的技能入库。

对话里建出的技能会先落在工作区（模型不一定听约定），收尾时用它把技能搬进
``skills/``，因此这里锁定三件事：校验、命名规范、移动（工作区不留副本）。
"""

import pytest

from easy_agent.bootstrap.config import Config
from easy_agent.services import skill_library

USERNAME = "szm"


@pytest.fixture()
def users_root(monkeypatch, tmp_path):
    """把用户工作区重定向到临时目录，避免污染仓库。"""
    root = tmp_path / "workspace" / "users"
    monkeypatch.setattr(
        Config, "get_user_workspace_dir", staticmethod(lambda username: root / username)
    )
    return root


def _make_skill_dir(parent, name, content=None):
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        content
        if content is not None
        else "---\nname: demo\ndescription: 演示技能\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    return d


def test_register_moves_skill_into_library(users_root, tmp_path):
    """登记即入库：子文件一起搬走，工作区源目录不再保留。"""
    src = _make_skill_dir(tmp_path / "ws", "pdf-report")
    (src / "scripts").mkdir()
    (src / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")

    result = skill_library.register_skill_source(USERNAME, src)

    assert result["dir_name"] == "pdf-report"
    assert result["path"] == skill_library.user_skills_dir(USERNAME) / "pdf-report"
    assert result["renamed"] is False
    assert result["warnings"] == []
    assert not src.exists(), "工作区不应保留技能副本"
    assert (result["path"] / "scripts" / "run.py").exists(), "子文件应一起迁移"


def test_register_requires_skill_md(users_root, tmp_path):
    d = tmp_path / "ws" / "no-skill"
    d.mkdir(parents=True)
    (d / "README.md").write_text("只有 README", encoding="utf-8")

    with pytest.raises(ValueError, match="SKILL.md"):
        skill_library.register_skill_source(USERNAME, d)


def test_register_rejects_empty_document(users_root, tmp_path):
    d = _make_skill_dir(tmp_path / "ws", "empty-skill", content="   \n")

    with pytest.raises(ValueError, match="内容为空"):
        skill_library.register_skill_source(USERNAME, d)


def test_register_rejects_missing_source(users_root, tmp_path):
    with pytest.raises(ValueError, match="源目录不存在"):
        skill_library.register_skill_source(USERNAME, tmp_path / "ws" / "nope")


def test_register_rejects_illegal_name(users_root, tmp_path):
    """目录名无法规范化（如纯中文）时明确报错，而不是落一个非法目录名。"""
    src = _make_skill_dir(tmp_path / "ws", "中文技能")

    with pytest.raises(ValueError, match="目录名不合法"):
        skill_library.register_skill_source(USERNAME, src)


def test_register_normalizes_dir_name(users_root, tmp_path):
    src = _make_skill_dir(tmp_path / "ws", "My Skill V2")

    result = skill_library.register_skill_source(USERNAME, src)

    assert result["dir_name"] == "my-skill-v2"
    assert result["renamed"] is True


def test_register_renames_on_conflict_with_enabled_skill(users_root, tmp_path):
    """重名不覆盖：已有技能保留，新技能追加 -2。"""
    existing = _make_skill_dir(skill_library.user_skills_dir(USERNAME), "pdf-report")
    src = _make_skill_dir(tmp_path / "ws", "pdf-report")

    result = skill_library.register_skill_source(USERNAME, src)

    assert result["dir_name"] == "pdf-report-2"
    assert existing.exists(), "已有技能不能被覆盖"
    assert (existing / "SKILL.md").exists()


def test_register_renames_on_conflict_with_disabled_skill(users_root, tmp_path):
    """停用目录里的同名技能也算占用，避免同一目录名两处出现。"""
    _make_skill_dir(skill_library.user_disabled_dir(USERNAME), "docx")
    src = _make_skill_dir(tmp_path / "ws", "docx")

    result = skill_library.register_skill_source(USERNAME, src)

    assert result["dir_name"] == "docx-2"


def test_register_warns_when_frontmatter_incomplete(users_root, tmp_path):
    """缺少 frontmatter 仍然入库（技能中心有兜底解析），但给出提示。"""
    src = _make_skill_dir(tmp_path / "ws", "bare-skill", content="# 没有 frontmatter\n")

    result = skill_library.register_skill_source(USERNAME, src)

    assert result["dir_name"] == "bare-skill"
    assert any("name" in w for w in result["warnings"])
    assert any("description" in w for w in result["warnings"])


def test_explicit_dir_name_wins(users_root, tmp_path):
    src = _make_skill_dir(tmp_path / "ws", "draft-folder")

    result = skill_library.register_skill_source(USERNAME, src, dir_name="report-builder")

    assert result["dir_name"] == "report-builder"


def test_normalize_skill_dir_name():
    assert skill_library.normalize_skill_dir_name("My Skill") == "my-skill"
    assert skill_library.normalize_skill_dir_name("PDF_v2") == "pdf-v2"
    assert skill_library.normalize_skill_dir_name("中文技能") == ""
    assert skill_library.normalize_skill_dir_name("") == ""
    assert len(skill_library.normalize_skill_dir_name("a" * 200)) <= 64
