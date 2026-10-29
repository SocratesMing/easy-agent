"""D4：``EASY_MCP_TOOL_PREFIX`` 打开后的端到端行为（``get_mcp_tools``）。

关闭时保持旧行为：跨 server 同名工具保留第一个、与内建工具同名直接丢弃。
打开时按 ``{server}__{tool}`` 改名，两个 server 的同名工具同时可用，且原名记进
``metadata["mcp_original_name"]`` 供 HITL 启发式与审计展示使用。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from easy_agent.services import mcp


class _StatelessClient:
    """不提供 session() 的客户端：走 mcp.py 的 stateless 回退分支。"""

    def __init__(self, connections):
        self.connections = connections

    async def get_tools(self, server_name=None):
        return [
            SimpleNamespace(name="search"),
            SimpleNamespace(name="delete"),  # 与内建 delete 同名
        ]


@pytest.fixture
def _patched(monkeypatch, tmp_path):
    config_file = tmp_path / "mcp.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp, "_mcp_tools_cache", {})
    monkeypatch.setattr(mcp, "_mcp_clients", {})
    monkeypatch.setattr(mcp, "MultiServerMCPClient", _StatelessClient)
    monkeypatch.setattr(mcp, "_resolve_mcp_config_path", lambda username=None: config_file)
    monkeypatch.setattr(
        mcp,
        "load_mcp_config",
        lambda username=None: {
            "alpha": {"transport": "streamable_http", "url": "http://alpha"},
            "beta": {"transport": "streamable_http", "url": "http://beta"},
        },
    )
    return tmp_path


async def test_prefix_off_keeps_legacy_dedupe(monkeypatch, _patched):
    monkeypatch.delenv("EASY_MCP_TOOL_PREFIX", raising=False)
    tools = await mcp.get_mcp_tools()
    # 跨 server 同名保留第一个；与内建 delete 同名的被丢弃
    assert [tool.name for tool in tools] == ["search"]


async def test_prefix_on_keeps_both_servers(monkeypatch, _patched):
    monkeypatch.setenv("EASY_MCP_TOOL_PREFIX", "1")
    tools = await mcp.get_mcp_tools()
    assert sorted(tool.name for tool in tools) == [
        "alpha__delete",
        "alpha__search",
        "beta__delete",
        "beta__search",
    ]


async def test_prefix_on_records_original_name(monkeypatch, _patched):
    monkeypatch.setenv("EASY_MCP_TOOL_PREFIX", "1")
    tools = await mcp.get_mcp_tools()
    by_name = {tool.name: tool for tool in tools}
    assert by_name["alpha__search"].metadata["mcp_original_name"] == "search"
    assert by_name["alpha__search"].metadata["mcp_server"] == "alpha"
    # 内建工具名不受影响（前缀后不再冲突）
    assert "delete" not in by_name
