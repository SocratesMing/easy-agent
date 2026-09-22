from __future__ import annotations

import asyncio
import os

import pytest
import uvicorn

from easy_mcp_server.app import create_app

from support import FakeKeyStore, build_hello


@pytest.fixture(autouse=True, scope="session")
def bypass_loopback_proxy():
    """测试全部打回环地址，必须绕开本机代理。

    开发者开了代理（Clash / Charles / 公司透明代理）时，127.0.0.1 的请求会被
    接管：本该返回 421 的 Host 校验用例拿到 502，表现为"测试结果取决于本机
    开没开代理"。httpx 只在创建 client 时读一次 NO_PROXY，session 级设够用。
    """
    existing = os.environ.get("NO_PROXY", "")
    merged = ",".join(filter(None, [existing, "127.0.0.1", "localhost", "::1"]))
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


# 单测必须只取决于测试自身的设定，不能受开发者本地 .env 影响。
# 注意是"设值"而不是"删除"：删除后 load_env() 会重新把 .env 的值注入回来。
# 留空表示"未配置"，取代码默认值；表名类给显式默认值，避免空串被当成表名。
_ISOLATED_ENV = {
    "MCP_SHUTDOWN_TIMEOUT": "",
    "QUERYKIT_MAX_ROWS": "",
    "QUERYKIT_TIMEOUT_MS": "",
    "QUERYKIT_POOL_SIZE": "",
    "STRATEGY_TABLE": "fmut2_strategy_manage",
    "STRATEGY_ALLOWED_TABLES": "",
    "STRATEGY_DENY_TABLES": "",
    "FX_TABLE": "HSDC_HDS2_BAR_FX",
    "FX_SCHEMA": "",
    "FX_CHANNELS": "",
    "FX_DEFAULT_FREQUENCY": "",
    "FX_DEFAULT_WINDOW_HOURS": "",
    "FX_MAX_WINDOW_DAYS": "",
    "FX_ALLOWED_TABLES": "",
    "FX_DENY_TABLES": "",
    "IDATA_SCHEMA": "",
    "IDATA_ALLOWED_TABLES": "",
    "IDATA_DENY_TABLES": "",
}


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """屏蔽本地 .env，保证测试结果与开发环境无关。"""
    for key, value in _ISOLATED_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def key_store() -> FakeKeyStore:
    store = FakeKeyStore()
    store.issue("hello", "key-alice", "alice")
    store.issue("hello", "key-bob", "bob")
    store.issue("second", "key-alice", "alice")
    return store


@pytest.fixture
async def server(key_store):
    """启动真实 uvicorn 服务，返回 base url（随机端口）。"""
    app = create_app(
        key_store.verify,
        businesses_override=[("hello", build_hello())],
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uvicorn_server = uvicorn.Server(config)
    task = asyncio.create_task(uvicorn_server.serve())
    while not uvicorn_server.started:
        await asyncio.sleep(0.02)
    port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        uvicorn_server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
