"""Characterization tests for easy_agent.model.

Pin current behavior of model creation and reasoning extraction. Chat model
constructors are lazy (no network until invoke), so tests use a fake api_key.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

import pytest
from langchain_anthropic import ChatAnthropic
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
)


def _make_config(protocol="openai", model_name="m"):
    provider = ProviderConfig(
        provider="p",
        api_key="sk-fake",
        model="mod",
        api_base="http://x",
        context_length=128000,
        protocol=protocol,
    )
    return Config(
        llm=LLMConfig(
            api_key="sk-fake",
            model="mod",
            provider="p",
            context_length=128000,
            protocol=protocol,
                retry=RetryConfig(enabled=True, max_retries=2),
        ),
        agent=AgentConfig(),
        tools=ToolsConfig(),
        models={model_name: provider},
        active_model=model_name,
    )


class TestCreateModel:
    def test_openai_protocol_returns_reasoning_chat_openai(self):
        model = create_model(_make_config(protocol="openai"))
        assert isinstance(model, ReasoningChatOpenAI)
        assert isinstance(model, ChatOpenAI)
        assert model.model == "mod"
        # 多模态清理不再由 supports_vision 开关控制（该字段已移除）
        assert not hasattr(model, "supports_vision")
        assert model.max_retries == 2

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
        config = _make_config(protocol="anthropic")
        cfg = resolve_llm_config(config, "m")
        assert cfg.provider == "p"
        assert cfg.model == "mod"
        assert cfg.protocol == "anthropic"
        assert cfg.context_length == 128000

    def test_none_falls_back_to_active(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="openai")
        assert resolve_llm_config(config, None) is config.llm

    def test_unknown_model_falls_back_to_active(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="openai")
        assert resolve_llm_config(config, "nope") is config.llm
