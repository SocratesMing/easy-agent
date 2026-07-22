"""接口 /api/chat 的测试：流式对话与取消。"""
import pytest


def test_chat_stream_returns_tokens(client):
    resp = client.post("/api/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200
    # SSE 流：至少应返回 data: 事件行
    assert "data:" in resp.text


def test_stream_empty_message_422(client):
    # 空消息在 pydantic 模型层即被 min_length=1 拒绝
    resp = client.post("/api/chat/stream", json={"message": ""})
    assert resp.status_code == 422


def test_stream_missing_message_422(client):
    resp = client.post("/api/chat/stream", json={})
    assert resp.status_code == 422


def test_cancel_no_active_stream(client):
    # cancel 的 session_id 为查询参数
    resp = client.post("/api/chat/cancel", params={"session_id": "no-such-session"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_resume_missing_body_422(client):
    resp = client.post("/api/chat/resume", json={})
    assert resp.status_code == 422


def test_resume_missing_fields_422(client):
    resp = client.post("/api/chat/resume", json={"session_id": "s1"})
    assert resp.status_code == 422
