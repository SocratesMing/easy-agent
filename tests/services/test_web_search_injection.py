"""联网搜索开关的 Agent 注入与缓存行为。"""

from types import SimpleNamespace

import pytest

from easy_agent.agent import EasyAgent
from easy_agent.config import Config
from easy_agent.services import agent_manager
from easy_agent.tools.web_search import PROVIDER_TAVILY, WEB_SEARCH_SYSTEM_PROMPT


@pytest.fixture
def agent_config():
    config = Config.load()
    config.web_search.enabled = True
    config.web_search.provider = PROVIDER_TAVILY
    config.web_search.api_url = ""
    config.web_search.api_key = "test-key"
    return config


def _patch_manager(monkeypatch, config):
    """把 agent_manager 的外部依赖替换为轻量假实现。"""
    monkeypatch.setattr(
        agent_manager,
        "_agent_config",
        {"config": config, "system_prompt": "SYS", "skills_root": "", "agent_env": "test", "win": False},
    )

    async def _no_mcp(_username=None):
        return []

    monkeypatch.setattr(agent_manager, "load_mcp_tools_for_user", _no_mcp)
    monkeypatch.setattr(agent_manager, "get_database", lambda: SimpleNamespace(get_user_by_username=lambda u: None))

    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model_name = kwargs.get("model_name") or config.active_model
            self.enable_web_search = kwargs.get("enable_web_search", False)
            self.mcp_tools = kwargs.get("mcp_tools") or []
            self.workspace_dir = kwargs.get("workspace_name") or "ws"
            created.append(self)

    monkeypatch.setattr(agent_manager, "EasyAgent", FakeAgent)
    monkeypatch.setattr(agent_manager, "_session_agents", {})
    return created


async def test_web_search_tool_injected_when_enabled(monkeypatch, agent_config):
    created = _patch_manager(monkeypatch, agent_config)

    agent = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-on", "testuser", "ws-on", enable_web_search=True
    )

    assert agent.enable_web_search is True
    names = [getattr(t, "name", "") for t in agent.mcp_tools]
    assert "web_search" in names
    tool = next(t for t in agent.mcp_tools if getattr(t, "name", "") == "web_search")
    assert tool.provider == PROVIDER_TAVILY
    assert tool.api_url == "https://api.tavily.com/search"
    assert len(created) == 1


async def test_web_search_not_injected_when_disabled(monkeypatch, agent_config):
    _patch_manager(monkeypatch, agent_config)

    agent = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-off", "testuser", "ws-off", enable_web_search=False
    )

    assert agent.enable_web_search is False
    assert all(getattr(t, "name", "") != "web_search" for t in agent.mcp_tools)


async def test_web_search_missing_key_falls_back_to_off(monkeypatch, agent_config):
    agent_config.web_search.api_key = ""
    _patch_manager(monkeypatch, agent_config)

    agent = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-nokey", "testuser", "ws-nokey", enable_web_search=True
    )

    # 未配置 api_key：不注入工具，也不追加联网搜索提示词
    assert agent.enable_web_search is False
    assert all(getattr(t, "name", "") != "web_search" for t in agent.mcp_tools)


async def test_agent_cache_hit_and_toggle_rebuild(monkeypatch, agent_config):
    created = _patch_manager(monkeypatch, agent_config)

    first = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-cache", "testuser", "ws-cache", enable_web_search=True
    )
    second = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-cache", "testuser", "ws-cache", enable_web_search=True
    )
    assert second is first
    assert len(created) == 1

    # 关闭联网搜索 -> 缓存的 Agent 不匹配，驱逐后重建
    third = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-cache", "testuser", "ws-cache", enable_web_search=False
    )
    assert third is not first
    assert len(created) == 2
    assert third.enable_web_search is False


async def test_hitl_resume_reuses_web_search_agent(monkeypatch, agent_config):
    """enable_web_search=None（HITL 恢复）时必须复用缓存 Agent，避免丢失中断态。"""
    created = _patch_manager(monkeypatch, agent_config)

    agent = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-resume", "testuser", "ws-resume", enable_web_search=True
    )
    resumed = await agent_manager.get_or_create_agent_for_session(
        "sess-ws-resume", "testuser", "ws-resume"
    )

    assert resumed is agent
    assert resumed.enable_web_search is True
    assert len(created) == 1


def test_easy_agent_prompt_mentions_web_search(monkeypatch, tmp_path, agent_config):
    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)

    enabled = EasyAgent(
        config=agent_config,
        system_prompt="BASE",
        username="testuser",
        session_id="sess-prompt-on",
        workspace_dir=tmp_path / "ws-on",
        enable_web_search=True,
    )
    disabled = EasyAgent(
        config=agent_config,
        system_prompt="BASE",
        username="testuser",
        session_id="sess-prompt-off",
        workspace_dir=tmp_path / "ws-off",
    )

    assert WEB_SEARCH_SYSTEM_PROMPT.strip() in enabled.system_prompt
    assert WEB_SEARCH_SYSTEM_PROMPT.strip() not in disabled.system_prompt
