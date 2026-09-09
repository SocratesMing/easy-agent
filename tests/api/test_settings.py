"""接口 /agent/settings 的测试：模型列表、MCP 服务器查询与增删。"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from easy_agent.api.settings import (
    AddMarketMcpRequest,
    add_mcp_from_market,
    generate_mcp_api_key,
    get_mcp_market,
    get_mcp_servers,
    router,
)
import easy_agent.api.settings as settings_api

from easy_agent.config import Config
from easy_agent.services import mcp as mcp_mod


def test_models(client):
    resp = client.get("/agent/settings/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "active_model" in data
    # models 为模型配置列表，至少包含一个名为 deepseek 的模型
    models = data["models"]
    assert isinstance(models, list)
    names = [m.get("name") for m in models]
    assert "deepseek" in names
    assert data["active_model"] == "deepseek"


def test_mcp_list(client):
    resp = client.get("/agent/settings/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert "servers" in data
    assert "source" in data
    assert "user_mcp_path" in data
    assert isinstance(data["servers"], list)


def test_mcp_update_and_server_crud(client):
    # 1) 清空所有 MCP server（servers 为 dict，不是 list）
    r = client.put("/agent/settings/mcp", json={"servers": {}})
    assert r.status_code == 200

    # 2) 新增一个本地 stdio 测试服务（body 字段为 config）
    add = client.post(
        "/agent/settings/mcp/server",
        json={
            "config": {
                "name": "unit-test-server",
                "transport": "stdio",
                "command": "echo",
                "args": [],
            }
        },
    )
    assert add.status_code == 200
    body = add.json()
    assert "unit-test-server" in body["added"]

    # 3) 删除该服务
    d = client.delete("/agent/settings/mcp/server/unit-test-server")
    assert d.status_code == 200


def test_mcp_market_adds_global_server_to_user_config(tmp_path, monkeypatch):
    global_path = tmp_path / "market-mcp.json"
    user_path = tmp_path / "user-mcp.json"
    global_path.write_text(
        json.dumps(
            {
                "servers": {
                    "market-server": {
                        "type": "sse",
                        "url": "https://mcp.example.com/sse",
                        "env": {"MCP_TOKEN": "secret-token"},
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        Config,
        "get_user_mcp_path",
        classmethod(lambda cls, username, config=None: user_path),
    )
    monkeypatch.setattr(mcp_mod, "_find_mcp_config", lambda: global_path)

    market_data = asyncio.run(get_mcp_market("testuser"))
    assert market_data["source"] == "global"
    assert len(market_data["servers"]) == 1
    server = market_data["servers"][0]
    assert server["name"] == "market-server"
    assert server["transport"] == "sse"
    assert server["added"] is False
    assert server["env_keys"] == ["MCP_TOKEN"]
    assert server["_raw"]["env"] == {"MCP_TOKEN": "***"}

    added = asyncio.run(
        add_mcp_from_market(AddMarketMcpRequest(name="market-server"), "testuser")
    )
    assert added["added"] == ["market-server"]

    market_data = asyncio.run(get_mcp_market("testuser"))
    assert market_data["servers"][0]["added"] is True

    current = asyncio.run(get_mcp_servers("testuser"))
    assert current["source"] == "user"
    assert current["servers"][0]["origin"] == "user"
    assert current["servers"][0]["_raw"]["env"] == {"MCP_TOKEN": "***"}

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            add_mcp_from_market(AddMarketMcpRequest(name="market-server"), "testuser")
        )
    assert exc_info.value.status_code == 409

    saved = json.loads(user_path.read_text(encoding="utf-8"))
    assert saved["servers"]["market-server"]["env"]["MCP_TOKEN"] == "secret-token"

    market_routes = {route.path: getattr(route, "methods", set()) for route in router.routes}
    assert "/agent/settings/mcp/market" in market_routes
    assert "/agent/settings/mcp/market/add" in market_routes


def test_mcp_api_key_generation_uses_current_user(monkeypatch):
    import easy_agent.services.mcp_api_keys as keys_service
    from easy_agent.api.settings import IssueMcpApiKeyRequest

    captured = {}

    def fake_issue(username, business):
        captured["username"] = username
        captured["business"] = business
        return "generated-api-key"

    monkeypatch.setattr(keys_service, "issue_api_key", fake_issue)

    result = asyncio.run(
        generate_mcp_api_key(IssueMcpApiKeyRequest(business="market"), "testuser")
    )

    assert result == {"status": "ok", "api_key": "generated-api-key", "business": "market"}
    assert captured["username"] == "testuser"
    assert captured["business"] == "market"


def test_mcp_api_key_rejects_unknown_business():
    from easy_agent.api.settings import IssueMcpApiKeyRequest

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            generate_mcp_api_key(IssueMcpApiKeyRequest(business="nope"), "testuser")
        )

    assert exc_info.value.status_code == 400


def test_mcp_api_key_generation_maps_database_failure(monkeypatch):
    import easy_agent.services.mcp_api_keys as keys_service
    from easy_agent.api.settings import IssueMcpApiKeyRequest

    def fake_issue(username, business):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(keys_service, "issue_api_key", fake_issue)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            generate_mcp_api_key(IssueMcpApiKeyRequest(business="market"), "testuser")
        )

    assert exc_info.value.status_code == 503
    assert "数据库" in exc_info.value.detail


def test_mcp_api_key_routes_are_registered():
    paths = {route.path for route in router.routes}

    assert "/agent/settings/mcp/api-key" in paths
    assert "/agent/settings/mcp/api-keys" in paths


def test_main_app_never_imports_mcp_server_subproject():
    """导入隔离：主应用代码不得 import mcp-server 子项目（只通过 HTTP/数据库耦合）。"""
    import re

    banned_patterns = (
        re.compile(r"^\s*(from|import)\s+easy_mcp_server", re.MULTILINE),
        re.compile(r"^\s*(from|import)\s+easy_agent\.mcp_servers", re.MULTILINE),
    )
    src_root = Path(__file__).resolve().parents[2] / "easy_agent"
    offenders = [
        str(p)
        for p in src_root.rglob("*.py")
        if "__pycache__" not in p.parts
        and any(pat.search(p.read_text(encoding="utf-8")) for pat in banned_patterns)
    ]
    assert offenders == [], f"主应用 import 了 MCP 子项目: {offenders}"


def test_mcp_config_preserves_environment_placeholders(tmp_path, monkeypatch):
    global_path = tmp_path / "mcp.json"
    global_path.write_text(
        json.dumps(
            {
                "servers": {
                    "env-server": {
                        "transport": "stdio",
                        "command": "echo",
                        "env": {
                            "MCP_PASSWORD": "${MCP_TEST_PASSWORD}",
                            "MCP_HOST": "${MCP_TEST_HOST:-127.0.0.1}",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_find_mcp_config", lambda: global_path)
    monkeypatch.setenv("MCP_TEST_PASSWORD", "test-password")
    monkeypatch.delenv("MCP_TEST_HOST", raising=False)

    config = mcp_mod.load_mcp_config(None)

    assert config["env-server"]["env"] == {
        "MCP_PASSWORD": "${MCP_TEST_PASSWORD}",
        "MCP_HOST": "${MCP_TEST_HOST:-127.0.0.1}",
    }
