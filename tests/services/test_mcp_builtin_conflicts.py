"""DA-08：MCP 工具不得与 deepagents 内建工具同名。

同名工具会静默顶掉内建实现（例如 MCP 的 ``delete`` 覆盖 0.7 新增的文件删除工具），
HITL 也按工具名匹配。冲突名单以工具注册表为唯一事实来源。
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from easy_agent.services import mcp
from easy_agent.tools.registry import builtin_tool_names


def test_conflict_list_comes_from_registry_and_covers_delete():
    assert mcp._BUILTIN_TOOL_NAMES == frozenset(builtin_tool_names())
    assert "delete" in mcp._BUILTIN_TOOL_NAMES


async def test_mcp_tools_shadowing_builtins_are_dropped(monkeypatch, tmp_path):
    class StatelessClient:
        def __init__(self, connections):
            self.connections = connections

        async def get_tools(self, server_name=None):
            return [
                SimpleNamespace(name="delete"),
                SimpleNamespace(name="read_file"),
                SimpleNamespace(name="crm_lookup"),
            ]

    config_file = tmp_path / "mcp.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp, "_mcp_tools_cache", {})
    monkeypatch.setattr(mcp, "_mcp_clients", {})
    monkeypatch.setattr(mcp, "MultiServerMCPClient", StatelessClient)
    monkeypatch.setattr(mcp, "_resolve_mcp_config_path", lambda username=None: config_file)
    monkeypatch.setattr(
        mcp,
        "load_mcp_config",
        lambda username=None: {"crm": {"transport": "streamable_http", "url": "http://crm"}},
    )

    tools = await mcp.get_mcp_tools()

    assert [tool.name for tool in tools] == ["crm_lookup"]


class _RecordingModel(GenericFakeChatModel):
    bound: list = []

    def bind_tools(self, tools, **kwargs):
        type(self).bound = [getattr(t, "name", None) for t in tools]
        return self


def test_registry_lists_every_tool_deepagents_actually_adds(tmp_path):
    """契约：框架真正注入的内建工具名必须都在注册表里（升级 deepagents 时第一时间暴露）。"""
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain.agents.middleware import TodoListMiddleware

    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = create_deep_agent(
        model=model,
        backend=LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True),
        middleware=[TodoListMiddleware()],
        subagents=[{"name": "helper", "description": "d", "system_prompt": "p"}],
    )
    agent.invoke({"messages": [{"role": "user", "content": "hi"}]})

    assert {"delete", "execute", "task", "write_todos"} <= set(_RecordingModel.bound)
    assert set(_RecordingModel.bound) <= set(builtin_tool_names())
