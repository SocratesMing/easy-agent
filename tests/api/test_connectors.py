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
REQUIRED_DEMO_IDS = {"mysql"}
VALID_TRANSPORTS = {"stdio", "sse", "streamable_http", "streamable-http", "http", "websocket"}


def test_shipped_catalog_is_valid():
    """随包 config/mcp.json 必须可解析、id 唯一、字段完整、transport 与必需项匹配。"""
    entries = connectors_service.load_catalog()
    assert len(entries) >= 1, f"内置目录条目过少: {len(entries)}"

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


# ── 开关 / fields 字段填充 / 配置修改 / 工具列表 ──────────────────────

AUTH_CONNECTOR = {
    "id": "auth-connector",
    "name": "需要鉴权的连接器",
    "description": "测试 fields 字段填充",
    "icon": "auth",
    "fields": [
        {"key": "headers.Authorization", "label": "Authorization", "placeholder": "Bearer ...", "required": True},
        {"key": "args.1", "label": "数据路径", "placeholder": "/data", "required": False},
    ],
    "config": {
        "transport": "streamable_http",
        "url": "http://127.0.0.1:9/mcp",
        "headers": {"Authorization": "Bearer <KEY>"},
    },
}


@pytest.fixture()
def auth_catalog(monkeypatch):
    """目录替换为带 fields 声明的演示连接器，并跳过真实 MCP 校验。"""

    async def _no_validate(names, username):
        return []

    monkeypatch.setattr(
        connectors_service, "load_catalog", lambda path=None: [dict(AUTH_CONNECTOR)]
    )
    monkeypatch.setattr(connectors_api, "_validate_servers", _no_validate)
    return AUTH_CONNECTOR


def test_install_missing_required_fields_rejected(client, auth_catalog):
    """必填字段未填时安装被 400 拒绝，detail 列出缺失字段的 label。"""
    resp = client.post("/agent/connectors/install", json={"id": "auth-connector", "validate": False})
    assert resp.status_code == 400
    assert "Authorization" in resp.json()["detail"]


def test_install_fills_values_and_defaults_enabled(client, auth_catalog):
    """values 按路径填充进 config，写入的 server 默认 enabled=True。"""
    resp = client.post(
        "/agent/connectors/install",
        json={
            "id": "auth-connector",
            "validate": False,
            "values": {"headers.Authorization": "Bearer sk-123", "args.1": "/data"},
        },
    )
    assert resp.status_code == 200, resp.text

    saved = json.loads(_user_mcp_path().read_text(encoding="utf-8"))
    cfg = saved["servers"]["auth-connector"]
    assert cfg["headers"]["Authorization"] == "Bearer sk-123"
    assert cfg["args"] == [None, "/data"]  # args.1 索引写入，前位用 None 占位
    assert cfg["enabled"] is True

    # 未填的可选字段不应覆盖目录默认值
    assert cfg["url"] == "http://127.0.0.1:9/mcp"


def test_toggle_persists_and_filters_agent_loading(client, auth_catalog):
    """开关状态持久化到用户 mcp.json；停用后 Agent 加载排除该 server。"""
    import easy_agent.services.mcp as mcp_service

    client.post(
        "/agent/connectors/install",
        json={
            "id": "auth-connector",
            "validate": False,
            "values": {"headers.Authorization": "Bearer sk-123"},
        },
    )

    resp = client.post(
        "/agent/connectors/toggle", json={"id": "auth-connector", "enabled": False}
    )
    assert resp.status_code == 200
    saved = json.loads(_user_mcp_path().read_text(encoding="utf-8"))
    assert saved["servers"]["auth-connector"]["enabled"] is False
    # Agent 工具加载过滤停用项
    assert "auth-connector" not in mcp_service.load_mcp_config("testuser")

    resp = client.post(
        "/agent/connectors/toggle", json={"id": "auth-connector", "enabled": True}
    )
    assert resp.status_code == 200
    servers = mcp_service.load_mcp_config("testuser")
    assert "auth-connector" in servers
    # 加载给 Agent 的配置剥离 enabled 标记
    assert "enabled" not in servers["auth-connector"]


def test_toggle_unknown_connector_404(client):
    resp = client.post("/agent/connectors/toggle", json={"id": "nope", "enabled": False})
    assert resp.status_code == 404


def test_update_connector_config_allowed_and_denied(client, auth_catalog):
    """已安装项按 fields 白名单修改；白名单外的字段 400 拒绝。"""
    client.post(
        "/agent/connectors/install",
        json={
            "id": "auth-connector",
            "validate": False,
            "values": {"headers.Authorization": "Bearer old"},
        },
    )

    # 白名单内：改 key
    resp = client.put(
        "/agent/connectors/config",
        json={"id": "auth-connector", "values": {"headers.Authorization": "Bearer new"}},
    )
    assert resp.status_code == 200
    saved = json.loads(_user_mcp_path().read_text(encoding="utf-8"))
    assert saved["servers"]["auth-connector"]["headers"]["Authorization"] == "Bearer new"

    # 空值跳过（不覆盖已有值）
    resp = client.put(
        "/agent/connectors/config",
        json={"id": "auth-connector", "values": {"headers.Authorization": "  "}},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == []
    saved = json.loads(_user_mcp_path().read_text(encoding="utf-8"))
    assert saved["servers"]["auth-connector"]["headers"]["Authorization"] == "Bearer new"

    # 白名单外：改 transport 被拒
    resp = client.put(
        "/agent/connectors/config",
        json={"id": "auth-connector", "values": {"transport": "sse"}},
    )
    assert resp.status_code == 400


def test_list_connector_tools(client, auth_catalog, monkeypatch):
    """工具列表端点连接单个 server，返回工具名与描述；连接失败返回 error。"""

    class _FakeTool:
        name = "query_market"
        description = "查询行情数据"

    class _FakeClient:
        def __init__(self, servers):
            assert "auth-connector" in servers

        async def get_tools(self):
            return [_FakeTool()]

    monkeypatch.setattr(connectors_api, "MultiServerMCPClient", _FakeClient)

    client.post(
        "/agent/connectors/install",
        json={
            "id": "auth-connector",
            "validate": False,
            "values": {"headers.Authorization": "Bearer sk-123"},
        },
    )
    resp = client.get("/agent/connectors/tools", params={"name": "auth-connector"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["error"] == ""
    assert data["tools"] == [{"name": "query_market", "description": "查询行情数据"}]


def test_catalog_entry_exposes_enabled_and_fields(client, auth_catalog):
    """目录接口返回 fields 声明；安装后 enabled 状态与用户实际配置一并返回。"""
    data = client.get("/agent/connectors").json()
    entry = data["connectors"][0]
    assert entry["installed"] is False
    assert entry["enabled"] is True
    assert entry["fields"][0]["key"] == "headers.Authorization"

    client.post(
        "/agent/connectors/install",
        json={
            "id": "auth-connector",
            "validate": False,
            "values": {"headers.Authorization": "Bearer sk-123"},
        },
    )
    data = client.get("/agent/connectors").json()
    entry = data["connectors"][0]
    assert entry["installed"] is True
    assert entry["enabled"] is True
    # 已安装项回填用户实际配置，供详情页编辑展示
    assert entry["user_config"]["headers"]["Authorization"] == "Bearer sk-123"


def test_connectors_status_endpoint(client, auth_catalog, monkeypatch):
    """状态端点并发校验全部已安装 server，逐条返回 ok/error 与工具数。"""

    class _FakeTool:
        name = "t1"
        description = ""

    class _FakeClient:
        def __init__(self, servers):
            self.servers = servers

        async def get_tools(self):
            # 只有填了正确 Key 的才连得上
            cfg = next(iter(self.servers.values()))
            if cfg.get("headers", {}).get("Authorization") != "Bearer good":
                raise RuntimeError("403 Forbidden")
            return [_FakeTool()]

    monkeypatch.setattr(connectors_api, "MultiServerMCPClient", _FakeClient)

    client.post(
        "/agent/connectors/install",
        json={
            "id": "auth-connector",
            "validate": False,
            "values": {"headers.Authorization": "Bearer good"},
        },
    )

    resp = client.get("/agent/connectors/status")
    assert resp.status_code == 200
    statuses = resp.json()["statuses"]
    assert len(statuses) == 1
    assert statuses[0]["name"] == "auth-connector"
    assert statuses[0]["status"] == "ok"
    assert statuses[0]["tools_count"] == 1

    # 改错 Key 后状态变为 error
    client.put(
        "/agent/connectors/config",
        json={"id": "auth-connector", "values": {"headers.Authorization": "Bearer bad"}},
    )
    statuses = client.get("/agent/connectors/status").json()["statuses"]
    assert statuses[0]["status"] == "error"
    assert "403" in statuses[0]["error"]


# ── 会话级连接器开关（输入框「+」→ 连接器面板的开关） ──────────────────


def _install_auth_connector(client) -> None:
    client.post(
        "/agent/connectors/install",
        json={
            "id": "auth-connector",
            "validate": False,
            "values": {"headers.Authorization": "Bearer sk-123"},
        },
    )


def test_session_switches_default_to_null(client):
    """没动过开关的会话返回 null（= 全部启用），与「空数组 = 全部关闭」是两种状态。"""
    from easy_agent.services import agent_manager as am

    resp = client.get("/agent/connectors/session", params={"session_id": "sess-none"})
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "sess-none", "connectors": None}
    assert am.get_session_connectors("sess-none") is None


def test_session_switches_write_and_read_back(client, auth_catalog):
    """写入会话开关：映射成 server 名落到 agent_manager，查询接口原样返回。"""
    from easy_agent.services import agent_manager as am

    _install_auth_connector(client)
    sid = "sess-switch"
    try:
        resp = client.post(
            "/agent/connectors/session",
            json={"session_id": sid, "connectors": ["auth-connector"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["connectors"] == ["auth-connector"]
        assert resp.json()["unknown"] == []
        assert am.get_session_connectors(sid) == ("auth-connector",)

        got = client.get("/agent/connectors/session", params={"session_id": sid})
        assert got.status_code == 200
        assert got.json()["connectors"] == ["auth-connector"]

        # 全部关闭：提交空数组，查询仍是空数组（不能退化成 null/全部启用）
        resp = client.post(
            "/agent/connectors/session", json={"session_id": sid, "connectors": []}
        )
        assert resp.status_code == 200
        assert resp.json()["connectors"] == []
        assert am.get_session_connectors(sid) == ()
        assert client.get(
            "/agent/connectors/session", params={"session_id": sid}
        ).json()["connectors"] == []
    finally:
        am.clear_session_connectors(sid)


def test_session_switches_map_display_name_and_ignore_unknown(client, auth_catalog):
    """展示名映射为目录 id（server 名）；未安装的名字进 unknown，不影响其余项。"""
    from easy_agent.services import agent_manager as am

    _install_auth_connector(client)
    sid = "sess-unknown"
    try:
        resp = client.post(
            "/agent/connectors/session",
            json={"session_id": sid, "connectors": ["需要鉴权的连接器", "根本没装"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["connectors"] == ["auth-connector"]
        assert resp.json()["unknown"] == ["根本没装"]
        assert am.get_session_connectors(sid) == ("auth-connector",)
    finally:
        am.clear_session_connectors(sid)
