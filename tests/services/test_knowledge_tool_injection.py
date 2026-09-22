"""知识库原文工具注入：未启用时不改变 Agent 的 tools 列表。"""

from types import SimpleNamespace


def test_extend_agent_tools_noop_when_disabled(monkeypatch, tmp_path):
    from easy_agent.knowledge import agent_extension
    from easy_agent.knowledge.config import KnowledgeConfig

    monkeypatch.setattr(KnowledgeConfig, "load", classmethod(lambda cls, *a, **k: cls()))
    base = ["tool-a"]
    out = agent_extension.extend_agent_tools(base, config=SimpleNamespace(agent=SimpleNamespace(workspace_dir=str(tmp_path))), username="u", session_id="s", workspace_name="w")
    assert out == base  # enabled=False → 不注入
