"""记忆整理可以用单独配置的模型（``memory_model``），留空时沿用会话的对话模型。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import easy_agent.agent.model as model_mod
from easy_agent.bootstrap.config_validation import KNOWN_TOP_LEVEL_KEYS
from easy_agent.services.streaming import side_effects


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(side_effects, "_MEMORY_MODELS", {})
    monkeypatch.setattr(side_effects, "_MEMORY_MODEL_WARNED", set())


def _agent(memory_model: str, models=("flash",)):
    config = SimpleNamespace(
        agent=SimpleNamespace(memory_model=memory_model),
        models={name: object() for name in models},
    )
    return SimpleNamespace(model="chat-model", config=config)


def test_without_memory_model_the_chat_model_is_used():
    assert side_effects._memory_llm(_agent("")) == "chat-model"
    # 测试替身没有 config 时同样回落
    assert side_effects._memory_llm(SimpleNamespace(model="chat-model")) == "chat-model"


def test_configured_memory_model_is_built_once_and_reused(monkeypatch):
    built: list[str] = []
    monkeypatch.setattr(
        model_mod, "create_model", lambda config, model_name=None, **_: built.append(model_name) or f"llm:{model_name}"
    )
    agent = _agent("flash")

    assert side_effects._memory_llm(agent) == "llm:flash"
    assert side_effects._memory_llm(agent) == "llm:flash"
    assert built == ["flash"]


def test_unknown_memory_model_falls_back_and_warns_once(caplog):
    agent = _agent("missing")
    with caplog.at_level(logging.WARNING, logger="easy_agent.chat_service"):
        assert side_effects._memory_llm(agent) == "chat-model"
        assert side_effects._memory_llm(agent) == "chat-model"
    assert caplog.text.count("memory_model=missing") == 1


def test_memory_model_build_failure_falls_back(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("bad provider")

    monkeypatch.setattr(model_mod, "create_model", _boom)
    assert side_effects._memory_llm(_agent("flash")) == "chat-model"


def test_memory_model_is_a_known_top_level_config_key():
    assert "memory_model" in KNOWN_TOP_LEVEL_KEYS
