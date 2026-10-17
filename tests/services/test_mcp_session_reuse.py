"""D3 MCP 持久会话复用测试（全部离线，不依赖网络 / 真实 MCP server）。"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from anyio import ClosedResourceError
from langchain_core.tools import ToolException
from mcp.types import CallToolResult, TextContent, Tool

from easy_agent.services import mcp, mcp_sessions
from easy_agent.services.mcp_sessions import MCPSessionManager


def _tool_def(name: str, description: str = "Echo tool.") -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": [],
        },
    )


def _text(result: Any) -> str:
    """把官方工具返回的内容块 / 元组 / 字符串统一成文本，便于断言。"""
    content = result[0] if isinstance(result, tuple) else result
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


class FakeSession:
    def __init__(self, client: "FakeClient", server: str, generation: int) -> None:
        self.client = client
        self.server = server
        self.generation = generation
        self.closed = False
        self.list_calls = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, cursor: str | None = None, **kwargs: Any):
        self.list_calls += 1
        self.client.list_calls += 1
        return SimpleNamespace(
            tools=list(self.client.tools_by_server.get(self.server, [])),
            nextCursor=None,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        progress_callback: Any = None,
        **kwargs: Any,
    ):
        payload = dict(arguments or {})
        self.calls.append((name, payload))
        if self.client.on_call is not None:
            result = self.client.on_call(self, name, payload)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                return result
        return CallToolResult(
            content=[
                TextContent(type="text", text=f"{name}:{payload.get('text', '')}")
            ],
            isError=False,
        )


class FakeClient:
    """只实现官方 ``session()`` 公开 API 的测试替身。"""

    instances: list["FakeClient"] = []

    def __init__(self, connections: dict[str, dict[str, Any]]) -> None:
        self.connections = connections
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.sessions: list[FakeSession] = []
        self.list_calls = 0
        self.on_call = None
        self.tools_by_server = {
            name: [_tool_def(f"{name}_echo", f"{name} echo")] for name in connections
        }
        FakeClient.instances.append(self)

    @asynccontextmanager
    async def session(self, server_name: str, *, auto_initialize: bool = True):
        self.opened.append(server_name)
        session = FakeSession(self, server_name, len(self.opened))
        self.sessions.append(session)
        try:
            yield session
        finally:
            session.closed = True
            self.closed.append(server_name)

    async def get_tools(self, server_name: str | None = None):
        raise AssertionError("persistent path must not call stateless get_tools()")


def _stdio_conn() -> dict[str, Any]:
    return {"transport": "stdio", "command": "fake-mcp", "args": []}


@pytest.fixture
async def manager(monkeypatch):
    mgr = MCPSessionManager(
        idle_seconds=0,
        connect_timeout=5,
        close_timeout=5,
        call_timeout=0,
    )
    monkeypatch.setattr(mcp_sessions, "_manager", mgr)
    mcp._mcp_tools_cache.clear()
    mcp._mcp_clients.clear()
    FakeClient.instances.clear()
    try:
        yield mgr
    finally:
        await mgr.aclose()
        mcp._mcp_tools_cache.clear()
        mcp._mcp_clients.clear()


async def test_same_server_two_calls_share_one_session(manager):
    client = FakeClient({"alpha": _stdio_conn()})

    tools = await manager.load_server_tools(
        client, "alpha", _stdio_conn(), scope="scope-a"
    )
    first = await tools[0].ainvoke({"text": "one"})
    second = await tools[0].ainvoke({"text": "two"})

    assert client.opened == ["alpha"]
    assert len(client.sessions) == 1
    assert client.sessions[0].calls == [
        ("alpha_echo", {"text": "one"}),
        ("alpha_echo", {"text": "two"}),
    ]
    assert "alpha_echo:one" in _text(first)
    assert "alpha_echo:two" in _text(second)
    stats = manager.snapshot()["stdio"]
    assert stats["created"] == 1
    assert stats["active"] == 1


async def test_different_servers_have_isolated_sessions(manager):
    client = FakeClient({"alpha": _stdio_conn(), "beta": _stdio_conn()})

    alpha_tools = await manager.load_server_tools(
        client, "alpha", _stdio_conn(), scope="scope-a"
    )
    beta_tools = await manager.load_server_tools(
        client, "beta", _stdio_conn(), scope="scope-a"
    )
    await alpha_tools[0].ainvoke({"text": "a"})
    await beta_tools[0].ainvoke({"text": "b"})

    assert client.opened == ["alpha", "beta"]
    assert len(client.sessions) == 2
    assert all(not session.closed for session in client.sessions)

    await manager.aclose()
    assert all(session.closed for session in client.sessions)


async def test_concurrent_calls_share_one_session(manager):
    client = FakeClient({"alpha": _stdio_conn()})

    async def slow_call(session, name, payload):
        await asyncio.sleep(0.01)
        return CallToolResult(
            content=[TextContent(type="text", text=f"{name}:{payload['text']}")],
            isError=False,
        )

    client.on_call = slow_call
    tools = await manager.load_server_tools(
        client, "alpha", _stdio_conn(), scope="scope-a"
    )

    await asyncio.gather(
        tools[0].ainvoke({"text": "a"}),
        tools[0].ainvoke({"text": "b"}),
    )

    assert client.opened == ["alpha"]
    assert len(client.sessions) == 1
    assert len(client.sessions[0].calls) == 2


async def test_transport_error_rebuilds_once_and_retries(manager):
    client = FakeClient({"alpha": _stdio_conn()})
    call_count = {"n": 0}

    def flaky(session, name, payload):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ClosedResourceError()
        return CallToolResult(
            content=[TextContent(type="text", text=f"{name}:{payload['text']}")],
            isError=False,
        )

    client.on_call = flaky
    tools = await manager.load_server_tools(
        client, "alpha", _stdio_conn(), scope="scope-a"
    )

    out = await tools[0].ainvoke({"text": "x"})

    assert "alpha_echo:x" in _text(out)
    assert client.opened == ["alpha", "alpha"]
    assert len(client.sessions) == 2
    assert manager.snapshot()["stdio"]["retried"] == 1

    await manager.aclose()
    assert client.sessions[0].closed is True


async def test_tool_exception_is_not_retried(manager):
    client = FakeClient({"alpha": _stdio_conn()})

    def bad_tool(session, name, payload):
        raise ToolException("invalid arguments")

    client.on_call = bad_tool
    tools = await manager.load_server_tools(
        client, "alpha", _stdio_conn(), scope="scope-a"
    )

    with pytest.raises(ToolException):
        await tools[0].ainvoke({"text": "x"})

    assert client.opened == ["alpha"]
    assert len(client.sessions) == 1
    assert manager.snapshot()["stdio"]["retried"] == 0


async def test_idle_expiry_rebuilds_on_next_call(monkeypatch):
    mgr = MCPSessionManager(
        idle_seconds=0.05,
        connect_timeout=5,
        close_timeout=5,
        call_timeout=0,
    )
    monkeypatch.setattr(mcp_sessions, "_manager", mgr)
    client = FakeClient({"alpha": _stdio_conn()})
    try:
        tools = await mgr.load_server_tools(
            client, "alpha", _stdio_conn(), scope="scope-a"
        )
        await tools[0].ainvoke({"text": "one"})
        await asyncio.sleep(0.12)
        await tools[0].ainvoke({"text": "two"})

        assert client.opened == ["alpha", "alpha"]
        assert len(client.sessions) == 2
    finally:
        await mgr.aclose()


async def test_call_timeout_retires_session_without_retry(monkeypatch):
    mgr = MCPSessionManager(
        idle_seconds=0,
        connect_timeout=5,
        close_timeout=5,
        call_timeout=0.02,
    )
    monkeypatch.setattr(mcp_sessions, "_manager", mgr)
    client = FakeClient({"alpha": _stdio_conn()})

    async def hang(session, name, payload):
        await asyncio.sleep(1)

    client.on_call = hang
    try:
        tools = await mgr.load_server_tools(
            client, "alpha", _stdio_conn(), scope="scope-a"
        )
        with pytest.raises(TimeoutError):
            await tools[0].ainvoke({"text": "slow"})

        assert client.opened == ["alpha"]
        assert mgr.snapshot()["stdio"]["retried"] == 0
        await mgr.aclose()
        assert client.sessions[0].closed is True
    finally:
        await mgr.aclose()


async def test_scope_invalidation_only_closes_matching_scope(manager):
    client = FakeClient({"alpha": _stdio_conn()})

    scope_a = await manager.load_server_tools(
        client, "alpha", _stdio_conn(), scope="scope-a"
    )
    scope_b = await manager.load_server_tools(
        client, "alpha", _stdio_conn(), scope="scope-b"
    )
    await scope_a[0].ainvoke({"text": "a"})
    await scope_b[0].ainvoke({"text": "b"})
    assert len(client.sessions) == 2

    mcp_sessions.close_mcp_scope_sync("scope-a")
    for _ in range(20):
        if client.sessions[0].closed:
            break
        await asyncio.sleep(0)

    assert client.sessions[0].closed is True
    assert client.sessions[1].closed is False


async def test_invalidate_mcp_cache_closes_and_rebuilds(
    manager, monkeypatch, tmp_path
):
    config_file = tmp_path / "mcp.json"
    config_file.write_text("{}", encoding="utf-8")
    servers = {"alpha": _stdio_conn()}
    monkeypatch.setattr(
        mcp, "_resolve_mcp_config_path", lambda username=None: config_file
    )
    monkeypatch.setattr(mcp, "load_mcp_config", lambda username=None: dict(servers))
    monkeypatch.setattr(mcp, "MultiServerMCPClient", FakeClient)

    tools = await mcp.get_mcp_tools()
    assert [tool.name for tool in tools] == ["alpha_echo"]
    assert tools[0].description == "alpha echo"
    client = FakeClient.instances[-1]

    await tools[0].ainvoke({"text": "one"})
    await tools[0].ainvoke({"text": "two"})
    assert client.opened == ["alpha"]

    cached_tools = await mcp.get_mcp_tools()
    assert FakeClient.instances[-1] is client
    cached_out = await cached_tools[0].ainvoke({"text": "cached"})
    assert "alpha_echo:cached" in _text(cached_out)
    assert client.opened == ["alpha"]

    marked = await tools[0].ainvoke({"text": "marked"})
    marked_text = _text(marked)
    assert "[MCP source: alpha/alpha_echo;" in marked_text
    assert "alpha_echo:marked" in marked_text

    mcp.invalidate_mcp_cache()
    await manager.aclose()
    assert client.sessions[0].closed is True

    reloaded = await mcp.get_mcp_tools()
    client2 = FakeClient.instances[-1]
    assert client2 is not client
    out = await reloaded[0].ainvoke({"text": "again"})
    assert "alpha_echo:again" in _text(out)
    assert client2.opened == ["alpha"]


async def test_client_without_session_api_falls_back_to_stateless(
    manager, monkeypatch, tmp_path
):
    class LegacyClient:
        def __init__(self, connections):
            self.connections = connections

        async def get_tools(self, server_name=None):
            return [SimpleNamespace(name=f"{server_name}_legacy")]

    config_file = tmp_path / "mcp.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp, "MultiServerMCPClient", LegacyClient)
    monkeypatch.setattr(
        mcp, "_resolve_mcp_config_path", lambda username=None: config_file
    )
    monkeypatch.setattr(
        mcp, "load_mcp_config", lambda username=None: {"alpha": _stdio_conn()}
    )

    tools = await mcp.get_mcp_tools()
    assert [tool.name for tool in tools] == ["alpha_legacy"]
    assert manager.snapshot() == {}


async def test_broken_server_does_not_break_other_servers(
    manager, monkeypatch, tmp_path
):
    class PartiallyBrokenClient(FakeClient):
        @asynccontextmanager
        async def session(self, server_name: str, *, auto_initialize: bool = True):
            if server_name == "broken":
                raise RuntimeError("server unavailable")
            async with super().session(
                server_name, auto_initialize=auto_initialize
            ) as session:
                yield session

    monkeypatch.setattr(mcp, "MultiServerMCPClient", PartiallyBrokenClient)
    monkeypatch.setattr(
        mcp,
        "load_mcp_config",
        lambda username=None: {"alpha": _stdio_conn(), "broken": _stdio_conn()},
    )
    config_file = tmp_path / "mcp.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        mcp, "_resolve_mcp_config_path", lambda username=None: config_file
    )

    tools = await mcp.get_mcp_tools()
    names = [tool.name for tool in tools]
    assert names == ["alpha_echo"]
