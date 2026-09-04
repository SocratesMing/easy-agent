from __future__ import annotations

import asyncio

import pytest
import uvicorn

from easy_mcp_server.app import create_app

from support import FakeKeyStore, build_hello


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
