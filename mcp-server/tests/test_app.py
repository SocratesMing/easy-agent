"""组合根测试：业务发现、挂载路径、健康检查与 URL 形态。"""

from __future__ import annotations

import asyncio

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from easy_mcp_server.app import create_app, discover_businesses

from support import build_hello


def test_discover_businesses_finds_market():
    names = [name for name, _ in discover_businesses()]
    assert "market" in names


async def test_mount_url_with_trailing_slash(server):
    """方案 A：/mcp/<business>/ 可被真实 MCP 客户端直连。"""
    headers = {"Authorization": "Bearer key-alice"}
    async with streamablehttp_client(f"{server}/mcp/hello/", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("echo", {"text": "ping"})
    assert "ping" in result.content[0].text


async def test_mount_url_without_trailing_slash_returns_307(server):
    """记录无尾斜杠行为：307 重定向，而 MCP 客户端不跟随 → 配置 URL 必须带尾斜杠。"""
    async with httpx.AsyncClient(follow_redirects=False) as client:
        resp = await client.post(
            f"{server}/mcp/hello",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer key-alice"},
        )
    assert resp.status_code == 307


async def test_health_is_public(server):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{server}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "hello" in body["businesses"]


async def test_unknown_business_returns_401(server):
    """未知业务先过鉴权（key 对其无效 → 401），刻意不返回 404 以避免暴露业务存在性。"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{server}/mcp/does-not-exist/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer key-alice"},
        )
    assert resp.status_code == 401


async def test_unexpected_host_header_is_rejected(server):
    """FastMCP 对 127.0.0.1 默认启用 DNS rebinding 保护：非白名单 Host → 421。

    部署时通过域名/反代访问必须显式配置 MCP_ALLOWED_HOSTS，否则全部 421。
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{server}/mcp/hello/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer key-alice", "Host": "evil.example.com"},
        )
    assert resp.status_code == 421


async def test_transport_security_can_be_opened_via_env(monkeypatch, key_store):
    """部署约束验证：配置 MCP_ALLOWED_HOSTS 后非本地 Host 放行。"""
    # 注意：mcp 库的 "host:*" 模式只匹配带端口的 Host；无端口 Host 需精确放行，
    # 因此生产配置必须两种都写（见 .env.example 注释）。
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com,mcp.example.com:*")
    app = create_app(
        key_store.verify,
        businesses_override=[("hello", build_hello())],
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        # 请求发到 127.0.0.1，但 Host 头伪装为白名单域名
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/mcp/hello/",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Authorization": "Bearer key-alice", "Host": "mcp.example.com"},
            )
        assert resp.status_code != 421
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
