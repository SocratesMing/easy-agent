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
    "DATAQA_MAX_ROWS": "",
    "DATAQA_QUERY_TIMEOUT_MS": "",
    "DATAQA_ALLOWED_TABLES": "",
    "DATAQA_DENY_TABLES": "",
    "XBOND_TABLE": "fmut_mkt_bonddpanyhis",
    "XBOND_VIEW": "v_xbond_depth",
    "XBOND_DEFAULT_DAYS": "",
    # hbaseqa：同样必须显式设定，避免开发者本地 .env 影响 hbaseqa 的单测
    "HBASEQA_THRIFT_HOST": "127.0.0.1",
    "HBASEQA_THRIFT_PORT": "9090",
    "HBASEQA_TIMEOUT_MS": "",
    "HBASEQA_THRIFT_TRANSPORT": "",
    "HBASEQA_THRIFT_PROTOCOL": "",
    "HBASEQA_CONNECT_ATTEMPTS": "",
    "HBASEQA_MAX_ROWS": "",
    "HBASEQA_MAX_SCANNED_ROWS": "",
    # 时间预算/批大小也必须隔离：本地 .env 若把它们设得很小，
    # 单测会因为"到点截断"而随机失败
    "HBASEQA_SCAN_BUDGET_MS": "",
    "HBASEQA_SCAN_BATCH_ROWS": "",
    "HBASEQA_DEFAULT_WINDOW_HOURS": "",
    "HBASEQA_TZ": "",
    "HBASEQA_TABLE_PREFIX": "HSDC_HDS2_",
    "HBASEQA_LIST_ALL_TABLES": "",
    "HBASEQA_ALLOWED_TABLES": "",
    "HBASEQA_DENY_TABLES": "",
    "HBASEQA_COLUMN_FAMILY": "",
    "HBASEQA_BUYSELL_VALUES": "",
    # 真实 rowkey 是 region(2)+time(13)+...，时间戳不在开头
    "HBASEQA_ROWKEY_LAYOUT": "region_ts",
    "HBASEQA_REGION_PREFIXES": "00",
    "HBASEQA_TIME_COLUMN_FILTER": "",
    # Thrift2 原生支持 TScan.reversed，测试走真实反向扫描路径（与生产默认一致）
    "HBASEQA_REVERSE_SCAN": "1",
    "HBASEQA_KERBEROS_ENABLED": "",
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
