"""联网搜索开关的 Agent 注入与缓存行为。"""

from collections import OrderedDict
from types import SimpleNamespace

import pytest

from easy_agent.agent import EasyAgent
from easy_agent.bootstrap.config import Config
from easy_agent.services.agents import manager as agent_manager
from easy_agent.tools.registry import require_tool_spec
from easy_agent.tools.web_search import PROVIDER_TAVILY

WEB_SEARCH_SYSTEM_PROMPT = require_tool_spec("web_search").prompt_note


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

    async def _no_mcp(_username=None, server_names=None):
        return []

    monkeypatch.setattr(agent_manager, "load_mcp_tools_for_user", _no_mcp)
    _fake = SimpleNamespace(get_user_by_username=lambda u: None)
    for _provider in (
        "get_session_repository",
        "get_session_runtime_repository",
        "get_user_repository",
    ):
        monkeypatch.setattr(agent_manager, _provider, lambda _fake=_fake: _fake)

    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model_name = kwargs.get("model_name") or config.active_model
            self.enable_web_search = kwargs.get("enable_web_search", False)
            # 缓存驱逐会比对审批模式：替身必须带上，否则每次都被判「模式变化」
            self.approval_mode = kwargs.get("approval_mode") or "manual"
            self.mcp_tools = kwargs.get("mcp_tools") or []
            self.workspace_dir = kwargs.get("workspace_name") or "ws"
            created.append(self)

    monkeypatch.setattr(agent_manager, "EasyAgent", FakeAgent)
    monkeypatch.setattr(agent_manager, "_sessions", OrderedDict())
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


def test_easy_agent_prompt_has_off_discipline_when_disabled(monkeypatch, tmp_path, agent_config):
    """关闭联网搜索时注入「联网纪律」：禁规划联网步骤、禁 execute/shell 联网取数。"""
    from easy_agent.tools.registry import WEB_SEARCH_OFF_PROMPT_NOTE

    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)

    disabled = EasyAgent(
        config=agent_config,
        system_prompt="BASE",
        username="testuser",
        session_id="sess-prompt-off2",
        workspace_dir=tmp_path / "ws-off2",
    )
    enabled = EasyAgent(
        config=agent_config,
        system_prompt="BASE",
        username="testuser",
        session_id="sess-prompt-on2",
        workspace_dir=tmp_path / "ws-on2",
        enable_web_search=True,
    )

    # 关闭：纪律段在，且明确覆盖 shell 逃逸通道（curl/urllib）与规划（write_todos）
    assert WEB_SEARCH_OFF_PROMPT_NOTE.strip() in disabled.system_prompt
    assert "urllib" in disabled.system_prompt and "write_todos" in disabled.system_prompt
    # 开启：不出现关闭纪律（两段互斥），引导段在
    assert WEB_SEARCH_OFF_PROMPT_NOTE.strip() not in enabled.system_prompt
    assert WEB_SEARCH_SYSTEM_PROMPT.strip() in enabled.system_prompt


def test_off_discipline_note_content_contract():
    """纪律段文案契约：必须封住 shell 逃逸、必须约束规划、必须引导用户开启。"""
    from easy_agent.tools.registry import WEB_SEARCH_OFF_PROMPT_NOTE

    text = WEB_SEARCH_OFF_PROMPT_NOTE
    # shell 逃逸通道逐一点名（防止后续改文案时漏掉）
    for token in ("curl", "wget", "urllib", "requests", "httpx", "execute"):
        assert token in text, f"纪律段缺少对 {token} 的约束"
    # 规划约束与引导开启
    assert "write_todos" in text
    assert "地球" in text
    # 约束范围限定在 execute/shell（MCP/连接器等进程内工具调用不在文案约束范围）
    assert "execute/shell" in text
    # 复述历史结果时的引用格式与时间习惯（来源可点击、时间符合国人阅读习惯）
    assert "北京时间" in text and "MM-dd HH:mm" in text
    assert "反引号" in text and "裸 URL" in text


def test_web_search_notes_citation_and_time_contract():
    """开启/关闭两段提示词共同契约：来源必须 markdown 链接、时间一律北京时间 MM-dd HH:mm。"""
    from easy_agent.tools.registry import WEB_SEARCH_OFF_PROMPT_NOTE, require_tool_spec

    on_note = require_tool_spec("web_search").prompt_note
    off_note = WEB_SEARCH_OFF_PROMPT_NOTE

    for text in (on_note, off_note):
        assert "北京时间" in text, "提示词必须要求换算北京时间"
        assert "MM-dd HH:mm" in text, "提示词必须约束时间格式"
        assert "不要出现「UTC」字样" in text, "提示词必须禁止 UTC 展示"
        assert "[媒体名](完整URL)" in text or "[媒体名](链接)" in text, "来源必须走 markdown 链接"
    # 开启段的示例不再教模型写 UTC（旧示例时间已移除；文案中仅保留 UTC+8 换算说明）
    assert "10:00 UTC" not in on_note
    assert "09:00 UTC" not in on_note
