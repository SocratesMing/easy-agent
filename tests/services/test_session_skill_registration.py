"""会话收尾的技能入库（streaming._register_session_skills）。

对话里创建的技能要么被直接写进技能库，要么落在工作区；收尾时统一入库并清理，
保证「技能中心能看到」且「工作区不留副本」。判定依据是本次会话的 generated_files。
"""

import pytest

from easy_agent.agent import EasyAgent
from easy_agent.bootstrap.config import Config
from easy_agent.services import skill_categories, skill_library
from easy_agent.services.streaming import _register_session_skills

USERNAME = "szm"
SESSION_ID = "sess-skill-register"


@pytest.fixture()
def users_root(monkeypatch, tmp_path):
    """把用户工作区重定向到临时目录。

    必须与 agent fixture 里的 ``config.agent.workspace_dir`` 指向同一处：
    Agent 从配置推导用户技能目录，技能中心走 Config.get_user_workspace_dir，
    两者不一致会让"入库"落到技能中心看不到的地方。
    """
    root = tmp_path / "workspace"
    monkeypatch.setattr(
        Config, "get_user_workspace_dir", staticmethod(lambda username: root / username)
    )
    return root


@pytest.fixture()
def agent(monkeypatch, tmp_path, users_root):
    """轻量 EasyAgent：只验证入库钩子，跳过模型与后端创建。"""
    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    return EasyAgent(
        config=config,
        system_prompt="BASE PROMPT",
        username=USERNAME,
        session_id=SESSION_ID,
        workspace_dir=tmp_path / "ws",
    )


def _record(db, file_path: str):
    db.add_generated_file(
        session_id=SESSION_ID,
        message_id="msg-1",
        filename=str(file_path).rsplit("/", 1)[-1],
        file_path=str(file_path),
        file_type="md",
        size=0,
    )


def _assignments(username: str) -> dict:
    return skill_categories.load_store(username)["assignments"]


def test_workspace_skill_is_registered_and_cleaned(agent, db):
    """模型把技能写在工作区：登记进技能库、工作区目录消失、生成文件记录被清。"""
    source = agent.workspace_dir / "pdf-report"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: pdf-report\ndescription: 生成 PDF 报表\n---\n", encoding="utf-8"
    )
    _record(db, source / "SKILL.md")

    registered = _register_session_skills(agent, db, SESSION_ID, "test")

    assert [item["dir_name"] for item in registered] == ["pdf-report"]
    assert registered[0]["source"] == "workspace"
    target = skill_library.user_skills_dir(USERNAME) / "pdf-report"
    assert (target / "SKILL.md").is_file()
    assert not source.exists(), "工作区不应保留技能副本"
    assert db.get_generated_files(SESSION_ID) == [], "记录应随文件一起清理"
    assert _assignments(USERNAME).get("pdf-report"), "入库后应完成自动归类"


def test_direct_library_write_is_categorized_only(agent, db):
    """模型直接写进技能库：文件保持不动，只补归类并清掉工作区记录。"""
    skill_dir = skill_library.user_skills_dir(USERNAME) / "pdf-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pdf-report\ndescription: 生成 PDF 报表\n---\n", encoding="utf-8"
    )
    _record(db, "/user-skills/pdf-report/SKILL.md")  # 虚拟路径记录

    registered = _register_session_skills(agent, db, SESSION_ID, "test")

    assert [item["dir_name"] for item in registered] == ["pdf-report"]
    assert registered[0]["source"] == "direct"
    assert (skill_dir / "SKILL.md").is_file()
    assert _assignments(USERNAME).get("pdf-report") == "doc"
    assert db.get_generated_files(SESSION_ID) == []


def test_direct_write_with_ambiguous_content_keeps_default_category(agent, db):
    """内容与内置关键词都不沾边时归入「通用技能」，且同样写入归属（分类创建时定稿）。"""
    skill_dir = skill_library.user_skills_dir(USERNAME) / "my-first-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-first-skill\ndescription: 一个新技能\n---\n", encoding="utf-8"
    )
    _record(db, "/user-skills/my-first-skill/SKILL.md")

    registered = _register_session_skills(agent, db, SESSION_ID, "test")

    assert [item["dir_name"] for item in registered] == ["my-first-skill"]
    assert skill_dir.exists()
    assert _assignments(USERNAME).get("my-first-skill") == "default"


def test_non_skill_writes_are_ignored(agent, db):
    """普通文件写入（脚本、报告）不该被当成技能。"""
    work = agent.workspace_dir / "notes"
    work.mkdir(parents=True)
    (work / "run.py").write_text("print(1)", encoding="utf-8")
    _record(db, work / "run.py")

    assert _register_session_skills(agent, db, SESSION_ID, "test") == []
    assert work.exists()
    assert len(db.get_generated_files(SESSION_ID)) == 1


def test_no_records_returns_empty(agent, db):
    assert _register_session_skills(agent, db, SESSION_ID, "test") == []


def test_hidden_skill_dir_is_skipped(agent, db):
    """隐藏目录不是合法技能，不入库（技能中心也不会列出）。"""
    source = agent.workspace_dir / ".draft"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: draft\n---\n", encoding="utf-8")
    _record(db, source / "SKILL.md")

    assert _register_session_skills(agent, db, SESSION_ID, "test") == []
    assert source.exists()


def test_name_conflict_appends_suffix(agent, db):
    """与已有技能重名时追加后缀，不覆盖。"""
    existing = skill_library.user_skills_dir(USERNAME) / "docx"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("---\nname: docx\n---\n", encoding="utf-8")

    source = agent.workspace_dir / "docx"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: docx\n---\n", encoding="utf-8")
    _record(db, source / "SKILL.md")

    registered = _register_session_skills(agent, db, SESSION_ID, "test")

    assert [item["dir_name"] for item in registered] == ["docx-2"]
    assert (existing / "SKILL.md").is_file()


def test_missing_skill_md_falls_back_to_original_location(agent, db):
    """目录里没有 SKILL.md 时不入库，避免污染技能库。"""
    source = agent.workspace_dir / "half-done"
    source.mkdir(parents=True)
    (source / "notes.md").write_text("只写了一半", encoding="utf-8")
    # 记录路径以 SKILL.md 结尾但文件其实不存在（模型只声明未写）
    _record(db, source / "SKILL.md")

    assert _register_session_skills(agent, db, SESSION_ID, "test") == []
    assert source.exists()
