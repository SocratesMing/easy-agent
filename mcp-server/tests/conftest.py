from __future__ import annotations

import asyncio

import pytest
import uvicorn

from easy_mcp_server.app import create_app

from support import FakeKeyStore, build_hello

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
