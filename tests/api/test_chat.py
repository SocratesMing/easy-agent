"""接口 /agent/chat 的测试：流式对话与取消。"""
from types import SimpleNamespace

import pytest

import easy_agent.api.chat as chat_api


def test_chat_stream_returns_tokens(client):
    resp = client.post("/agent/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200
    # SSE 流：至少应返回 data: 事件行
    assert "data:" in resp.text


def test_stream_empty_message_422(client):
    # 空消息在 pydantic 模型层即被 min_length=1 拒绝
    resp = client.post("/agent/chat/stream", json={"message": ""})
    assert resp.status_code == 422


def test_stream_missing_message_422(client):
    resp = client.post("/agent/chat/stream", json={})
    assert resp.status_code == 422


def test_stream_passes_web_search_flag(client, monkeypatch):
    """enable_web_search=true 时，chat 路由需把开关透传给 Agent 创建逻辑。"""
    from easy_agent.models.api import ChatRequest

    captured = {}

    async def fake_get_agent(session_id, username, workspace_name, **kwargs):
        captured["agent_kwargs"] = kwargs
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    async def fake_stream(**kwargs):
        captured["request"] = kwargs["request"]
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "今天有什么新闻", "enable_web_search": True},
    )

    assert resp.status_code == 200
    assert isinstance(captured["request"], ChatRequest)
    assert captured["request"].enable_web_search is True
    assert captured["agent_kwargs"]["enable_web_search"] is True


def test_stream_web_search_defaults_off(client):
    """未传 enable_web_search 时默认关闭（不注入搜索工具）。"""
    from easy_agent.models.api import ChatRequest

    assert ChatRequest(message="hi").enable_web_search is False


def test_cancel_no_active_stream(client):
    # cancel 的 session_id 为查询参数
    resp = client.post("/agent/chat/cancel", params={"session_id": "no-such-session"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_resume_missing_body_422(client):
    resp = client.post("/agent/chat/resume", json={})
    assert resp.status_code == 422


def test_resume_missing_fields_422(client):
    resp = client.post("/agent/chat/resume", json={"session_id": "s1"})
    assert resp.status_code == 422
