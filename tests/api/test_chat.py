"""接口 /agent/chat 的测试：流式对话与取消。"""
import json
from pathlib import Path
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


def test_stream_empty_message_400(client):
    # 正文允许为空（只挂技能/连接器、只上传文件时本来就没有正文），
    # 但三者全空不算有效请求 → 400。
    resp = client.post("/agent/chat/stream", json={"message": ""})
    assert resp.status_code == 400


def test_stream_empty_message_with_skills_ok(client, monkeypatch):
    """只挂技能、没写正文：正文可以为空，请求照常执行。"""
    _patch_skills(monkeypatch)
    _patch_stream(monkeypatch)
    resp = client.post("/agent/chat/stream", json={"message": "", "skills": ["docx"]})
    assert resp.status_code == 200


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


def test_stream_uses_structured_connectors_field(client, monkeypatch):
    """请求体 connectors 字段（新前端）：消息文本里没有暗号，也照样只注入选中的连接器。"""
    _patch_catalog(monkeypatch)
    assert client.put(
        "/agent/connectors/mcp/raw", json={"content": _MCP_CONTENT}
    ).status_code == 200
    captured = _capture_agent(monkeypatch)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "帮我看看", "connectors": ["演示连接器", "内部知识检索"]},
    )

    assert resp.status_code == 200
    assert captured["agent_kwargs"]["connector_servers"] == [
        "demo-connector",
        "内部知识检索",
    ]


def test_stream_merges_structured_field_and_text_directive(client, monkeypatch):
    """字段与消息文本指令同时存在时合并去重（字段顺序在前），手打指令不受影响。"""
    _patch_catalog(monkeypatch)
    assert client.put(
        "/agent/connectors/mcp/raw", json={"content": _MCP_CONTENT}
    ).status_code == 200
    captured = _capture_agent(monkeypatch)

    resp = client.post(
        "/agent/chat/stream",
        json={
            "message": "使用连接器[演示连接器] 顺便看看",
            "connectors": ["内部知识检索", "演示连接器"],
        },
    )

    assert resp.status_code == 200
    assert captured["agent_kwargs"]["connector_servers"] == [
        "内部知识检索",
        "demo-connector",
    ]


def test_stream_empty_connectors_field_falls_back_to_text(client, monkeypatch):
    """显式传空数组（新前端没选连接器）时行为不变：仍按文本解析，无指令则为空。"""
    captured = _capture_agent(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "你好", "connectors": []})

    assert resp.status_code == 200
    assert captured["agent_kwargs"]["connector_servers"] == []


def test_stream_connectors_field_unknown_name_ignored(client, monkeypatch):
    """字段里未安装的名字：只记日志，不阻断对话，也不影响其他连接器。"""
    _patch_catalog(monkeypatch)
    assert client.put(
        "/agent/connectors/mcp/raw", json={"content": _MCP_CONTENT}
    ).status_code == 200
    captured = _capture_agent(monkeypatch)

    resp = client.post(
        "/agent/chat/stream",
        json={"message": "你好", "connectors": ["根本没装", "演示连接器"]},
    )

    assert resp.status_code == 200
    assert captured["agent_kwargs"]["connector_servers"] == ["demo-connector"]


# ── 「/技能目录名」指令（输入框技能胶囊）────────────────────────────────

_SKILL_DIRS = [
    {"dir_name": "docx", "path": "/tmp/skills/docx", "enabled": True},
    {"dir_name": "pdf", "path": "/tmp/skills/pdf", "enabled": True},
]
_SKILL_DISPLAY_NAMES = {"docx": "Word 文档", "pdf": "PDF 文档"}


def _patch_skills(monkeypatch):
    """把用户技能目录固定为 docx / pdf，避免用例依赖本机实际安装的技能。"""
    from easy_agent.services import skill_library

    monkeypatch.setattr(
        skill_library, "list_user_skill_dirs", lambda username: list(_SKILL_DIRS)
    )
    monkeypatch.setattr(
        skill_library,
        "skill_metadata",
        lambda path: {"name": _SKILL_DISPLAY_NAMES.get(Path(str(path)).name, "")},
    )


def _patch_stream(monkeypatch):
    """捕获交给生成器的 parsed_content / session_id（假生成器，不真跑 Agent）。"""
    captured = {}

    async def fake_get_agent(session_id, username, workspace_name, **kwargs):
        captured["agent_kwargs"] = kwargs
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    async def fake_stream(**kwargs):
        captured["stream_kwargs"] = kwargs
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(chat_api, "chat_stream_generator", fake_stream)
    return captured


def _stored_user_message(db, captured):
    session = db.get_session(captured["stream_kwargs"]["session_id"])
    return session, session.messages[0]


def test_stream_uses_structured_skills_field(client, db, monkeypatch):
    """请求体 skills 字段（新前端）：`/目录名` 只进模型输入，落库正文与标题都是用户原话。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post(
        "/agent/chat/stream", json={"message": "帮我排个版", "skills": ["docx"]}
    )

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "/docx 帮我排个版"
    session, user_message = _stored_user_message(db, captured)
    assert user_message["content"] == "帮我排个版"
    assert user_message["skills"] == ["docx"]
    assert session.title == "帮我排个版"


def test_stream_skills_only_without_text_uses_display_name(client, db, monkeypatch):
    """只挂技能、没写正文：正文为空也放行，标题用技能展示名兜底。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "", "skills": ["docx"]})

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "/docx"
    session, user_message = _stored_user_message(db, captured)
    assert user_message["content"] == ""
    assert user_message["skills"] == ["docx"]
    assert session.title == "Word 文档"


def test_stream_merges_skills_field_and_text_directive(client, db, monkeypatch):
    """字段与消息文本指令同时存在时合并去重（字段顺序在前），手打指令不受影响。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post(
        "/agent/chat/stream", json={"message": "/pdf 帮我看看", "skills": ["docx"]}
    )

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "/docx /pdf 帮我看看"
    _session, user_message = _stored_user_message(db, captured)
    assert user_message["skills"] == ["docx", "pdf"]


def test_stream_legacy_skill_prefix_still_works(client, db, monkeypatch):
    """旧前端（或手打 `/技能名`）：文本解析回退，落库正文保持原样，标题照旧剥前缀。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "/docx 帮我排个版"})

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "/docx 帮我排个版"
    session, user_message = _stored_user_message(db, captured)
    assert user_message["content"] == "/docx 帮我排个版"
    assert user_message["skills"] == ["docx"]
    assert session.title == "帮我排个版"


def test_stream_skills_field_unknown_dir_ignored(client, db, monkeypatch):
    """字段里不属于该用户的目录名：只记日志，不拼指令、不落 skills 字段。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post(
        "/agent/chat/stream", json={"message": "你好", "skills": ["根本没装"]}
    )

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "你好"
    _session, user_message = _stored_user_message(db, captured)
    assert "skills" not in user_message


def test_stream_path_like_text_not_treated_as_skill(client, db, monkeypatch):
    """路径开头的正文（/workspace、/usr/bin）不被当成技能指令。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post(
        "/agent/chat/stream", json={"message": "/workspace 下的文件帮我看看"}
    )

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "/workspace 下的文件帮我看看"
    _session, user_message = _stored_user_message(db, captured)
    assert "skills" not in user_message


def test_resume_missing_body_422(client):
    resp = client.post("/agent/chat/resume", json={})
    assert resp.status_code == 422


def test_resume_missing_fields_422(client):
    resp = client.post("/agent/chat/resume", json={"session_id": "s1"})
    assert resp.status_code == 422
