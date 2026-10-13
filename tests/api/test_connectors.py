"""接口 /agent/connectors 的测试：内置目录、安装/卸载、mcp.json 原文读写、服务增删。"""

import json
from pathlib import Path

import pytest

import easy_agent.api.connectors as connectors_api
import easy_agent.services.connectors as connectors_service
from easy_agent.config import Config
from easy_agent.api.connectors import router as connectors_router

DEMO_CONNECTOR = {
    "id": "demo-connector",
    "name": "演示连接器",
    "description": "仅用于测试",
    "icon": "demo",
    "config": {
        "transport": "stdio",
        "command": "echo",
        "args": [],
    },
}


@pytest.fixture(autouse=True)
def _clean_user_mcp():
    """每个用例前清掉用户 mcp.json，避免用例之间互相污染。"""
    path = Config.get_user_mcp_path("testuser")
    path.unlink(missing_ok=True)
    yield


@pytest.fixture()
def demo_catalog(monkeypatch):
    """把内置目录替换为一条演示连接器，并跳过真实 MCP 校验（不拉起子进程）。"""

    async def _no_validate(names, username):
        return []

    monkeypatch.setattr(
        connectors_service, "load_catalog", lambda path=None: [dict(DEMO_CONNECTOR)]
    )
    monkeypatch.setattr(connectors_api, "_validate_servers", _no_validate)
    return DEMO_CONNECTOR


def _user_mcp_path() -> Path:
    return Config.get_user_mcp_path("testuser")


# ── 目录加载（单元） ──────────────────────────────────────────────────


def test_load_catalog_returns_empty_for_missing_file(tmp_path):
    assert connectors_service.load_catalog(tmp_path / "nope.json") == []


def test_public_mcp_path_is_config_dir_mcp_json():
    """公共 MCP 配置固定为 config 目录下的 mcp.json。"""
    path = connectors_service.resolve_public_mcp_path()
    assert path is not None, "仓库里应存在 easy_agent/config/mcp.json"
    assert path.name == "mcp.json"
    assert path.parent.name == "config"
    assert path.exists()


def test_load_catalog_reads_resolved_public_path(tmp_path, monkeypatch):
    """load_catalog() 走 resolve_public_mcp_path()，不再写死旧的 catalog.json 路径。"""
    custom = tmp_path / "mcp.json"
    custom.write_text(
        json.dumps(
            {
                "connectors": [
                    {"id": "from-config", "name": "来自 config", "config": {"transport": "stdio", "command": "echo"}}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(connectors_service, "resolve_public_mcp_path", lambda: custom)

    entries = connectors_service.load_catalog()
    assert [e["id"] for e in entries] == ["from-config"]


def test_load_catalog_returns_empty_when_public_file_absent(monkeypatch):
    """配置文件缺失时降级为空目录，不抛异常。"""
    monkeypatch.setattr(connectors_service, "resolve_public_mcp_path", lambda: None)
    assert connectors_service.load_catalog() == []


def test_load_catalog_returns_empty_for_broken_json(tmp_path):
    broken = tmp_path / "mcp.json"
    broken.write_text("{not json", encoding="utf-8")
    assert connectors_service.load_catalog(broken) == []


def test_load_catalog_skips_invalid_entries(tmp_path):
    catalog = tmp_path / "mcp.json"
    catalog.write_text(
        json.dumps(
            {
                "connectors": [
                    {"id": "ok", "name": "正常", "config": {"transport": "stdio", "command": "echo"}},
                    {"id": "no-name", "config": {"transport": "stdio"}},
                    {"id": "no-config", "name": "缺配置"},
                    {"id": "", "name": "空 id", "config": {"transport": "stdio"}},
                    "not-an-object",
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    entries = connectors_service.load_catalog(catalog)
    assert [e["id"] for e in entries] == ["ok"]
    assert entries[0]["name"] == "正常"


# ── 目录接口 ──────────────────────────────────────────────────────────


def test_connectors_list_empty_catalog(client, monkeypatch):
    """目录为空时接口仍可用（把内置目录指向空文件，不依赖随包 catalog 的内容）。"""
    monkeypatch.setattr(connectors_service, "load_catalog", lambda path=None: [])
    resp = client.get("/agent/connectors")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connectors"] == []
    assert data["custom"] == []
    assert data["path"].endswith("mcp.json")


def test_installed_ignores_global_mcp_fallback(client, demo_catalog, monkeypatch, tmp_path):
    """全局 mcp.json（= config/mcp.json）里的内容不能被当成用户「已安装」。

    config/mcp.json 同时是 Agent 加载用的全局 mcp.json；若有人往里加了 servers，
    连接器页的 installed 判断必须仍只看用户自己的 workspace/{user}/mcp.json。
    """
    import easy_agent.services.mcp as mcp_service

    global_file = tmp_path / "mcp.json"
    global_file.write_text(
        json.dumps(
            {"servers": {"demo-connector": {"transport": "stdio", "command": "echo"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_service, "_find_mcp_config", lambda: global_file)

    # 全局回退确实生效（Agent 侧会加载它）……
    assert "demo-connector" in mcp_service.load_mcp_config("testuser")
    # ……但连接器页仍视为未安装
    data = client.get("/agent/connectors").json()
    assert data["connectors"][0]["installed"] is False
    assert data["custom"] == []


def test_connectors_list_marks_installed_and_collects_custom(client, demo_catalog):
    data = client.get("/agent/connectors").json()
    assert [c["id"] for c in data["connectors"]] == ["demo-connector"]
    assert data["connectors"][0]["installed"] is False
    assert data["connectors"][0]["config"]["command"] == "echo"

    add = client.post(
        "/agent/connectors/mcp",
        json={"config": {"name": "custom-one", "transport": "stdio", "command": "echo"}},
    )
    assert add.status_code == 200

    data = client.get("/agent/connectors").json()
    assert [c["name"] for c in data["custom"]] == ["custom-one"]
    assert data["custom"][0]["config"]["transport"] == "stdio"


# ── 安装 / 卸载 ───────────────────────────────────────────────────────


def test_connector_install_uninstall_roundtrip(client, demo_catalog):
    resp = client.post("/agent/connectors/install", json={"id": "demo-connector"})
    assert resp.status_code == 200
    assert resp.json()["installed"] == "demo-connector"
    assert resp.json()["servers"] == ["demo-connector"]

    saved = json.loads(_user_mcp_path().read_text(encoding="utf-8"))
    assert saved["servers"]["demo-connector"]["command"] == "echo"

    assert client.get("/agent/connectors").json()["connectors"][0]["installed"] is True

    again = client.post("/agent/connectors/install", json={"id": "demo-connector"})
    assert again.status_code == 409

    out = client.post("/agent/connectors/uninstall", json={"id": "demo-connector"})
    assert out.status_code == 200
    assert out.json()["servers"] == []
    assert json.loads(_user_mcp_path().read_text(encoding="utf-8"))["servers"] == {}

    missing = client.post("/agent/connectors/uninstall", json={"id": "demo-connector"})
    assert missing.status_code == 404


def test_connector_install_unknown_id_404(client, demo_catalog):
    resp = client.post("/agent/connectors/install", json={"id": "not-exist"})
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


# ── mcp.json 原文读写 ─────────────────────────────────────────────────


def test_mcp_raw_returns_template_when_absent(client):
    resp = client.get("/agent/connectors/mcp/raw")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"].endswith("mcp.json")
    assert json.loads(data["content"]) == {"servers": {}}


def test_mcp_raw_save_normalizes_and_persists(client, demo_catalog):
    payload = {
        "mcpServers": {
            "s1": {"type": "stdio", "command": "echo", "args": []},
        }
    }
    resp = client.put(
        "/agent/connectors/mcp/raw", json={"content": json.dumps(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["servers"] == ["s1"]

    text = _user_mcp_path().read_text(encoding="utf-8")
    saved = json.loads(text)
    assert list(saved.keys()) == ["servers"]
    assert saved["servers"]["s1"]["transport"] == "stdio"

    fetched = client.get("/agent/connectors/mcp/raw").json()
    assert json.loads(fetched["content"]) == saved


def test_mcp_raw_save_rejects_invalid_json(client):
    resp = client.put("/agent/connectors/mcp/raw", json={"content": "{\"servers\": "})
    assert resp.status_code == 400
    assert "JSON 格式无效" in resp.json()["detail"]

    resp = client.put("/agent/connectors/mcp/raw", json={"content": "[]"})
    assert resp.status_code == 400
    assert "必须是 JSON 对象" in resp.json()["detail"]


# ── 单个服务增删 ──────────────────────────────────────────────────────


def test_add_mcp_config_rejects_duplicate_and_bad_format(client, demo_catalog):
    body = {"config": {"name": "dup-server", "transport": "stdio", "command": "echo"}}
    assert client.post("/agent/connectors/mcp", json=body).status_code == 200

    resp = client.post("/agent/connectors/mcp", json=body)
    assert resp.status_code == 409
    assert "已存在" in resp.json()["detail"]

    bad = client.post("/agent/connectors/mcp", json={"config": {}})
    assert bad.status_code == 400


def test_delete_mcp_server(client, demo_catalog):
    client.post(
        "/agent/connectors/mcp",
        json={"config": {"name": "to-delete", "transport": "stdio", "command": "echo"}},
    )
    assert client.delete("/agent/connectors/mcp/to-delete").status_code == 200
    assert client.delete("/agent/connectors/mcp/to-delete").status_code == 404


# ── 路由迁移 ──────────────────────────────────────────────────────────


def test_mcp_routes_moved_off_settings(client):
    paths = {route.path for route in connectors_router.routes}
    assert "/agent/connectors" in paths
    assert "/agent/connectors/install" in paths
    assert "/agent/connectors/uninstall" in paths
    assert "/agent/connectors/mcp" in paths
    assert "/agent/connectors/mcp/raw" in paths
    assert "/agent/connectors/mcp/{name}" in paths

    from easy_agent.api.settings import router as settings_router

    legacy = {r.path for r in settings_router.routes}
    assert not any(p.startswith("/agent/settings/mcp") for p in legacy)


# ── 随包发布的公共 MCP 配置（防止 config/mcp.json 被改坏） ──────────────

#: 内置目录里的示例连接器（允许增删，但这批 demo 必须齐）
REQUIRED_DEMO_IDS = {"market-data", "filesystem", "fetch", "git", "everything"}
VALID_TRANSPORTS = {"stdio", "sse", "streamable_http", "streamable-http", "http", "websocket"}


def test_shipped_catalog_is_valid():
    """随包 config/mcp.json 必须可解析、id 唯一、字段完整、transport 与必需项匹配。"""
    entries = connectors_service.load_catalog()
    assert len(entries) >= 5, f"内置目录条目过少: {len(entries)}"

    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), f"id 重复: {ids}"
    assert REQUIRED_DEMO_IDS.issubset(set(ids)), f"缺少示例连接器: {REQUIRED_DEMO_IDS - set(ids)}"

    for entry in entries:
        eid = entry["id"]
        assert entry["name"], f"{eid} 缺少 name"
        assert entry["description"], f"{eid} 缺少 description"
        assert isinstance(entry["icon"], str), f"{eid} icon 应为字符串"
        if eid in REQUIRED_DEMO_IDS:
            assert entry["icon"], f"{eid} 缺少 icon"

        cfg = entry["config"]
        transport = cfg.get("transport") or cfg.get("type")
        assert transport in VALID_TRANSPORTS, f"{eid} transport 非法: {transport}"
        if transport == "stdio":
            assert cfg.get("command"), f"{eid} stdio 配置缺少 command"
        else:
            assert cfg.get("url"), f"{eid} {transport} 配置缺少 url"


def test_shipped_catalog_served_by_api(client):
    """GET /agent/connectors 返回公共目录，未安装时 installed=False。"""
    resp = client.get("/agent/connectors")
    assert resp.status_code == 200
    data = resp.json()
    ids = {c["id"] for c in data["connectors"]}
    assert REQUIRED_DEMO_IDS.issubset(ids), f"接口未返回示例连接器: {REQUIRED_DEMO_IDS - ids}"
    assert all(c["installed"] is False for c in data["connectors"])
    assert data["path"].endswith("mcp.json")
