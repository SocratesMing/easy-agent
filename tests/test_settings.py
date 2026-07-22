"""接口 /api/settings 的测试：模型列表、MCP 服务器查询与增删。"""
import pytest


def test_models(client):
    resp = client.get("/api/settings/models")
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
    resp = client.get("/api/settings/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert "servers" in data
    assert "source" in data
    assert "user_mcp_path" in data
    assert isinstance(data["servers"], list)


def test_mcp_update_and_server_crud(client):
    # 1) 清空所有 MCP server（servers 为 dict，不是 list）
    r = client.put("/api/settings/mcp", json={"servers": {}})
    assert r.status_code == 200

    # 2) 新增一个本地 stdio 测试服务（body 字段为 config）
    add = client.post(
        "/api/settings/mcp/server",
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
    d = client.delete("/api/settings/mcp/server/unit-test-server")
    assert d.status_code == 200
