"""接口 /agent/chat 的测试：流式对话与取消。"""
import json
from types import SimpleNamespace

import pytest

import easy_agent.api.chat as chat_api
import easy_agent.services.connectors as connectors_service
from easy_agent.config import Config


@pytest.fixture(autouse=True)
def _clean_user_mcp():
    """连接器用例会写用户 mcp.json，用例前后都清掉，避免相互污染。"""
    path = Config.get_user_mcp_path("testuser")
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


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


# ── 「使用连接器[X]」指令（输入框连接器胶囊） ──────────────────────────


_MCP_CONTENT = json.dumps(
    {
        "servers": {
            "demo-connector": {"transport": "stdio", "command": "echo"},
            "内部知识检索": {"transport": "stdio", "command": "echo"},
            "停用的连接器": {"transport": "stdio", "command": "echo", "enabled": False},
        }
    },
    ensure_ascii=False,
)


def _patch_catalog(monkeypatch):
    """假目录只提供「演示连接器」：其 id 就是用户 mcp.json 里的 server 名。"""
    monkeypatch.setattr(
        connectors_service,
        "load_catalog",
        lambda path=None: [{"id": "demo-connector", "name": "演示连接器", "config": {}}],
    )


def _capture_agent(monkeypatch):
    captured = {}

    async def fake_get_agent(session_id, username, workspace_name, **kwargs):
        captured["agent_kwargs"] = kwargs
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    async def fake_stream(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)
    return captured


def test_stream_parses_connector_directive(client, monkeypatch):
    """消息里的「使用连接器[X]」要解析成 mcp.json 的 server 名，并透传给 Agent 创建。"""
    _patch_catalog(monkeypatch)
    # 用户已装：一个目录连接器（消息里用展示名）+ 一个自定义 server + 一个停用项
    assert client.put(
        "/agent/connectors/mcp/raw", json={"content": _MCP_CONTENT}
    ).status_code == 200
    captured = _capture_agent(monkeypatch)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "使用连接器[演示连接器] 使用连接器[内部知识检索] 使用连接器[停用的连接器] 帮我看看"},
    )

    assert resp.status_code == 200
    # 展示名映射为目录 id；自定义 server 名原样；停用项也解析出来（由 mcp 加载层跳过并提示）
    assert captured["agent_kwargs"]["connector_servers"] == [
        "demo-connector",
        "内部知识检索",
        "停用的连接器",
    ]


def test_stream_connector_directive_unknown_name_ignored(client, monkeypatch):
    """未安装的连接器名只记日志，不影响其余连接器，也不阻断对话。"""
    _patch_catalog(monkeypatch)
    assert client.put(
        "/agent/connectors/mcp/raw", json={"content": _MCP_CONTENT}
    ).status_code == 200
    captured = _capture_agent(monkeypatch)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "使用连接器[根本没装] 使用连接器[演示连接器] 你好"},
    )

    assert resp.status_code == 200
    assert captured["agent_kwargs"]["connector_servers"] == ["demo-connector"]


def test_stream_without_directive_passes_empty_connectors(client, monkeypatch):
    """没选连接器时传空列表（= 注入全部启用连接器），与改动前行为一致。"""
    captured = _capture_agent(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "你好"})

    assert resp.status_code == 200
    assert captured["agent_kwargs"]["connector_servers"] == []


def test_resume_missing_body_422(client):
    resp = client.post("/agent/chat/resume", json={})
    assert resp.status_code == 422


def test_resume_missing_fields_422(client):
    resp = client.post("/agent/chat/resume", json={"session_id": "s1"})
    assert resp.status_code == 422
