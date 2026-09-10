"""当前用户名注入上下文：系统提示词与 shell 执行环境。"""

import pytest

from easy_agent.agent import EasyAgent
from easy_agent.config import Config


@pytest.fixture
def agent(monkeypatch, tmp_path):
    """构造轻量 EasyAgent：跳过模型与后端创建（只验证上下文注入）。"""
    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    skill_dir = tmp_path / "workspace" / "szm" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\n---\n", encoding="utf-8"
    )
    return EasyAgent(
        config=config,
        system_prompt="BASE PROMPT",
        username="szm",
        session_id="sess-001",
        workspace_dir=tmp_path / "ws",
    )


def test_system_prompt_contains_current_username(agent):
    assert "## 当前用户" in agent.system_prompt
    assert "szm" in agent.system_prompt


def test_system_prompt_explains_skill_placeholder(agent):
    """技能文档里的 {userId} / {username} 指当前用户名。"""
    assert "{userId}" in agent.system_prompt
    assert "{username}" in agent.system_prompt


def test_runtime_context_defers_to_deepagents(agent):
    """路径与技能使用说明由 DeepAgents 中间件注入，不重复写入系统提示词。"""
    prompt = agent.system_prompt

    assert prompt.count("## 当前用户") == 1
    assert "## Workspace:" not in prompt
    assert "## Memory:" not in prompt
    assert "## User Skills:" not in prompt
    assert "/user-skills/" not in prompt


def test_shell_env_carries_username(agent):
    assert agent.shell_env["EASY_USERNAME"] == "szm"


def test_shell_env_does_not_leak_host_environment(agent):
    """保持隔离：只带用户名，不继承宿主环境（避免泄露密钥等变量）。"""
    assert set(agent.shell_env) == {"EASY_USERNAME"}
