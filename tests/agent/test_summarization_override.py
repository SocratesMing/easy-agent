"""DA-06：摘要阈值走官方的「同名实例原位替换」，不再替换框架私有工厂。

主代理与每个子代理（含显式覆盖的 general-purpose）都只能有一个
``SummarizationMiddleware``，而且必须是本项目按 config 阈值构造的那一份；建图期间
``deepagents.graph.create_summarization_middleware`` 保持原样（改造前会被临时替换）。
夹具见 ``tests/agent/conftest.py``。
"""

from __future__ import annotations

import easy_agent.agent.core as agent_mod


def test_build_does_not_patch_the_framework_factory(build_easy_agent, compiled_agents):
    build_easy_agent()

    assert compiled_agents
    for call in compiled_agents:
        assert call["factory_untouched"], call["name"]
        assert sum(m.name == "SummarizationMiddleware" for m in call["middleware"]) == 1, call["name"]


def test_every_agent_uses_the_configured_summarization(build_easy_agent, compiled_agents, monkeypatch):
    built = []
    real = agent_mod.build_summarization_middleware

    def recording(config, model, backend, **kwargs):
        instance = real(config, model, backend, **kwargs)
        built.append(instance)
        return instance

    monkeypatch.setattr(agent_mod, "build_summarization_middleware", recording)
    build_easy_agent()

    names = {call["name"] for call in compiled_agents}
    assert {"easy-agent", "general-purpose"} <= names
    for call in compiled_agents:
        summarizers = [m for m in call["middleware"] if m.name == "SummarizationMiddleware"]
        assert len(summarizers) == 1, call["name"]
        assert any(summarizers[0] is instance for instance in built), call["name"]
