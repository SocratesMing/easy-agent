"""接口 /api/prompts 的测试：读取、更新、查询业务提示词。"""
import pytest


def test_read_prompt_default(client):
    # read 接受 path 作为查询参数，缺省时使用默认路径
    resp = client.post("/api/prompts/read")
    assert resp.status_code == 200
    body = resp.json()
    assert "path" in body
    assert "content" in body


def test_update_and_read_prompt(client):
    name = "unit_test_prompt"
    content = "你好，这是单元测试写入的提示词。"
    up = client.post("/api/prompts/update", json={"path": name, "content": content})
    assert up.status_code == 200  # 返回字符串消息

    resp = client.post("/api/prompts/read", params={"path": name})
    assert resp.status_code == 200
    assert content in resp.json()["content"]


def test_query_prompts_list(client):
    resp = client.post("/api/prompts/query")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert isinstance(data["data"], list)
