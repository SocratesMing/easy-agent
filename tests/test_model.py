"""Characterization tests for easy_agent.model.

Pin current behavior of model creation, reasoning extraction and image
stripping so the Phase 1 structural refactor can be verified to preserve
behavior. Chat model constructors are lazy (no network until invoke), so
tests use a fake api_key.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from easy_agent.config import (
    AgentConfig,
    Config,
    LLMConfig,
    ProviderConfig,
    RetryConfig,
    ToolsConfig,
)
from easy_agent.model import (
    ReasoningChatOpenAI,
    create_model,
    extract_reasoning,
    strip_image_content,
)


def _make_config(protocol="openai", supports_vision=False, model_name="m"):
    provider = ProviderConfig(
        provider="p",
        api_key="sk-fake",
        model="mod",
        api_base="http://x",
        max_input_tokens=128000,
        protocol=protocol,
        supports_vision=supports_vision,
    )
    return Config(
        llm=LLMConfig(
            api_key="sk-fake",
            model="mod",
            provider="p",
            max_input_tokens=128000,
            protocol=protocol,
            supports_vision=supports_vision,
            retry=RetryConfig(enabled=True, max_retries=2),
        ),
        agent=AgentConfig(),
        tools=ToolsConfig(),
        models={model_name: provider},
        active_model=model_name,
    )


class TestCreateModel:
    def test_openai_protocol_returns_reasoning_chat_openai(self):
        model = create_model(_make_config(protocol="openai", supports_vision=False))
        assert isinstance(model, ReasoningChatOpenAI)
        assert isinstance(model, ChatOpenAI)
        assert model.model == "mod"
        assert model.supports_vision is False
        assert model.max_retries == 2

    def test_openai_protocol_supports_vision(self):
        model = create_model(_make_config(protocol="openai", supports_vision=True))
        assert model.supports_vision is True

    def test_anthropic_protocol_returns_chat_anthropic(self):
        model = create_model(_make_config(protocol="anthropic"))
        assert isinstance(model, ChatAnthropic)
        assert model.model == "mod"
        assert model.max_retries == 2

    def test_anthropic_thinking_budget_and_max_tokens(self):
        model = create_model(_make_config(protocol="anthropic"))
        assert model.thinking == {"type": "enabled", "budget_tokens": 10000}
        assert model.max_tokens == 16000

    def test_missing_api_key_raises(self):
        provider = ProviderConfig(provider="p", api_key="", model="mod", protocol="openai")
        config = Config(
            llm=LLMConfig(api_key="", model="mod", provider="p", protocol="openai"),
            agent=AgentConfig(),
            tools=ToolsConfig(),
            models={"m": provider},
            active_model="m",
        )
        with pytest.raises(ValueError):
            create_model(config)


class TestExtractReasoning:
    def test_reasoning_content(self):
        assert extract_reasoning({"reasoning_content": "thought"}) == "thought"

    def test_reasoning_alias(self):
        assert extract_reasoning({"reasoning": "r"}) == "r"

    def test_reason_content_alias(self):
        assert extract_reasoning({"reason_content": "rc"}) == "rc"

    def test_first_key_wins(self):
        assert extract_reasoning({"reasoning_content": "a", "reasoning": "b"}) == "a"

    def test_empty(self):
        assert extract_reasoning({}) == ""
        assert extract_reasoning(None) == ""


class TestStripImageContent:
    def test_replaces_image_url_with_text_placeholder(self):
        msgs = [HumanMessage(content=[
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ])]
        result = strip_image_content(msgs)
        content = result[0].content
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "看这张图"}
        assert content[1]["type"] == "text"
        assert "图片内容已省略" in content[1]["text"]

    def test_single_image_collapses_to_string(self):
        msgs = [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x.png"}}])]
        result = strip_image_content(msgs)
        assert isinstance(result[0].content, str)
        assert "图片内容已省略" in result[0].content

    def test_passes_through_plain_text(self):
        msgs = [HumanMessage(content="hello")]
        assert strip_image_content(msgs)[0].content == "hello"

    def test_idempotent(self):
        msgs = [HumanMessage(content=[{"type": "text", "text": "hi"}])]
        once = strip_image_content(msgs)
        twice = strip_image_content(once)
        assert twice[0].content == once[0].content


class TestAnthropicConstants:
    def test_constants_exist_and_match_previous_values(self):
        from easy_agent.model import (
            ANTHROPIC_MAX_TOKENS,
            ANTHROPIC_THINKING_BUDGET_TOKENS,
        )
        assert ANTHROPIC_THINKING_BUDGET_TOKENS == 10000
        assert ANTHROPIC_MAX_TOKENS == 16000


class TestResolveLlmConfig:
    def test_resolves_named_model(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="anthropic", supports_vision=True)
        cfg = resolve_llm_config(config, "m")
        assert cfg.provider == "p"
        assert cfg.model == "mod"
        assert cfg.protocol == "anthropic"
        assert cfg.max_input_tokens == 128000
        assert cfg.supports_vision is True

    def test_none_falls_back_to_active(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="openai")
        assert resolve_llm_config(config, None) is config.llm

    def test_unknown_model_falls_back_to_active(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="openai")
        assert resolve_llm_config(config, "nope") is config.llm
