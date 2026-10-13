"""技能库写权限策略（agent.skill_write）的规则生成与匹配。

对话创建技能依赖这套规则：允许**新建**技能目录，禁止改写/删除已有技能，并堵住
glob 不匹配点文件带来的绕过。规则由 ``EasyAgent._build_permissions`` 生成，判定由
deepagents 的 ``_check_fs_permission`` 完成（按列表顺序取第一条命中，默认 allow）。
"""

import pytest

from easy_agent.agent import EasyAgent
from easy_agent.config import Config

# 框架私有函数：权限匹配语义（deny/allow、glob 方言）由它决定，这里直接锁定行为
_fs = pytest.importorskip("deepagents.middleware.filesystem")
_check_fs_permission = _fs._check_fs_permission

EXISTING_SKILLS = ("docx", "pdf-report")
USERNAME = "szm"


def _make_agent(monkeypatch, tmp_path, policy: str) -> EasyAgent:
    """构造轻量 Agent：只验证权限规则生成，跳过模型与后端创建。"""
    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    config.agent.skill_write = policy
    # 保留旧的 denied_dirs 写法：new-only/allow 下写禁令应被 skill_write 接管
    config.agent.denied_dirs = [{"path": "/user-skills", "operations": ["write"]}]

    for name in EXISTING_SKILLS:
        d = tmp_path / "workspace" / USERNAME / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            "---\nname: x\ndescription: y\n---\n", encoding="utf-8"
        )

    return EasyAgent(
        config=config,
        system_prompt="BASE PROMPT",
        username=USERNAME,
        session_id="sess-skill-write",
        workspace_dir=tmp_path / "ws",
    )


def _perm(agent: EasyAgent, path: str, operation: str = "write") -> str:
    rules = agent._build_permissions(
        agent._resolve_skills_paths(), agent._safe_external_dirs()
    )
    return _check_fs_permission(rules, operation, path)


def test_new_skill_writable_but_existing_skills_protected(monkeypatch, tmp_path):
    """核心诉求：能新建技能，改不动已有技能。"""
    agent = _make_agent(monkeypatch, tmp_path, "new-only")

    assert _perm(agent, "/user-skills/brand-new/SKILL.md") == "allow"
    assert _perm(agent, "/user-skills/brand-new/scripts/run.py") == "allow"

    assert _perm(agent, "/user-skills/docx/SKILL.md") == "deny"
    assert _perm(agent, "/user-skills/pdf-report/references/x.md") == "deny"


def test_hidden_paths_blocked(monkeypatch, tmp_path):
    """glob 的 ** 不匹配点开头名字，必须显式列出才堵得住绕过。"""
    agent = _make_agent(monkeypatch, tmp_path, "new-only")

    assert _perm(agent, "/user-skills/.probe.md") == "deny"
    assert _perm(agent, "/user-skills/.draft/SKILL.md") == "deny"


def test_loose_file_in_library_blocked(monkeypatch, tmp_path):
    """技能库根下不允许写散文件（技能必须是目录）。"""
    agent = _make_agent(monkeypatch, tmp_path, "new-only")

    assert _perm(agent, "/user-skills/loose.md") == "deny"


def test_similar_name_is_treated_as_new(monkeypatch, tmp_path):
    """前缀相同的名字不算已有技能（docx 与 docx-2 不互相影响）。"""
    agent = _make_agent(monkeypatch, tmp_path, "new-only")

    assert _perm(agent, "/user-skills/docx-2/SKILL.md") == "allow"


def test_read_is_unaffected(monkeypatch, tmp_path):
    """使用技能需要读 SKILL.md，读权限不受影响。"""
    agent = _make_agent(monkeypatch, tmp_path, "new-only")

    assert _perm(agent, "/user-skills/docx/SKILL.md", "read") == "allow"


def test_workspace_is_unaffected(monkeypatch, tmp_path):
    agent = _make_agent(monkeypatch, tmp_path, "new-only")

    assert _perm(agent, "/workspace/report.md") == "allow"


def test_policy_deny_blocks_everything(monkeypatch, tmp_path):
    """skill_write=deny 保持旧行为：整库（含隐藏路径）禁写。"""
    agent = _make_agent(monkeypatch, tmp_path, "deny")

    assert _perm(agent, "/user-skills/brand-new/SKILL.md") == "deny"
    assert _perm(agent, "/user-skills/.probe.md") == "deny"
    assert _perm(agent, "/user-skills/docx/SKILL.md") == "deny"


def test_policy_allow_adds_no_skill_rules(monkeypatch, tmp_path):
    """skill_write=allow 时不生成技能库规则（denied_dirs 的 write 禁令也被接管）。"""
    agent = _make_agent(monkeypatch, tmp_path, "allow")

    rules = agent._build_permissions(
        agent._resolve_skills_paths(), agent._safe_external_dirs()
    )
    assert not [r for r in rules if any("/user-skills" in p for p in r.paths)]
    assert _perm(agent, "/user-skills/docx/SKILL.md") == "allow"


def test_invalid_policy_falls_back_to_new_only(monkeypatch, tmp_path):
    agent = _make_agent(monkeypatch, tmp_path, "whatever")

    assert agent._skill_write_policy() == "new-only"
    assert _perm(agent, "/user-skills/brand-new/SKILL.md") == "allow"


def test_skill_dir_created_when_user_has_no_skill(monkeypatch, tmp_path):
    """新用户（一个技能都没有）也要有可写路径，否则建不出第一个技能。"""
    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    config.agent.skill_write = "new-only"
    config.agent.denied_dirs = []

    agent = EasyAgent(
        config=config,
        system_prompt="BASE PROMPT",
        username="newbie",
        session_id="sess-first-skill",
        workspace_dir=tmp_path / "ws",
    )
    assert agent._resolve_skills_paths() == []  # 还没有技能可加载

    backend = agent._build_backend([], agent._safe_external_dirs())
    assert "/user-skills/" in backend.routes
    assert _perm(agent, "/user-skills/first-skill/SKILL.md") == "allow"
