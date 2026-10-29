"""tests/agent 共享夹具：真实 EasyAgent 建图 + 记录主代理 / 子代理的编译参数。"""

from __future__ import annotations

import deepagents.graph as da_graph
import deepagents.middleware.subagents as da_subagents
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

import easy_agent.agent.core as agent_mod
from easy_agent.agent import EasyAgent
from easy_agent.agent.graph_cache import reset_agent_graph_cache
from easy_agent.bootstrap.config import Config


class _FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def build_easy_agent(tmp_path, monkeypatch):
    """真实 EasyAgent 共享编译图 + 假模型（不联网）；``tools`` 作为注入工具传入。"""
    reset_agent_graph_cache()
    monkeypatch.setenv("EASY_AGENT_GRAPH_CACHE", "1")
    saver = MemorySaver()
    monkeypatch.setattr(agent_mod, "_shared_graph_checkpointer", lambda: saver)
    monkeypatch.setattr(
        agent_mod, "create_model", lambda *a, **k: _FakeModel(messages=iter([AIMessage(content="ok")]))
    )

    def build(tools=None):
        reset_agent_graph_cache()
        cfg = Config.load()
        cfg.agent.workspace_dir = str(tmp_path / "workspace")
        cfg.agent.memories_dir = str(tmp_path / "memories")
        return EasyAgent(
            config=cfg,
            system_prompt="P",
            username="u1",
            session_id="s1",
            workspace_name="w1",
            mcp_tools=list(tools or []),
            enable_hitl=True,
        )

    yield build
    reset_agent_graph_cache()


@pytest.fixture
def compiled_agents(monkeypatch):
    """按编译顺序记录主代理与各子代理拿到的 name / middleware / tools。"""
    original_factory = da_graph.create_summarization_middleware
    calls: list[dict] = []

    def spy(module):
        original = module.create_agent

        def wrapper(*args, **kwargs):
            calls.append(
                {
                    "name": kwargs.get("name"),
                    "middleware": list(kwargs.get("middleware") or []),
                    "tools": list(kwargs.get("tools") or []),
                    "factory_untouched": da_graph.create_summarization_middleware is original_factory,
                }
            )
            return original(*args, **kwargs)

        monkeypatch.setattr(module, "create_agent", wrapper)

    spy(da_graph)
    spy(da_subagents)
    return calls
