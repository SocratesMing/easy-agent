"""接口 /agent/chat 的测试：流式对话与取消。"""
from pathlib import Path
from types import SimpleNamespace

import easy_agent.api.chat as chat_api


def test_chat_stream_returns_tokens(client):
    resp = client.post("/agent/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200
    # SSE 流：至少应返回 data: 事件行
    assert "data:" in resp.text


def test_stream_empty_message_400(client):
    # 正文允许为空（只挂技能、只上传文件时本来就没有正文），但两者全空不算有效请求 → 400。
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


# ── 连接器不再随每轮消息提交 ─────────────────────────────────────────────
# 输入框「+」→ 连接器面板的连接器开关改为「会话级」：由 /agent/connectors/session
# 写入 agent_manager，Agent 创建时按会话读取（见 tests/api/test_connectors.py）。


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


def test_stream_does_not_pass_connectors_to_agent(client, monkeypatch):
    """chat 路由不再向 Agent 创建逻辑透传任何连接器参数。"""
    captured = _capture_agent(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "你好"})

    assert resp.status_code == 200
    assert "connector_servers" not in captured["agent_kwargs"]


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
    """请求体 skills 字段（新前端）：选中的技能 pin 进本轮（DA-13），模型输入、落库正文与标题都是用户原话。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post(
        "/agent/chat/stream", json={"message": "帮我排个版", "skills": ["docx"]}
    )

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "帮我排个版"
    assert captured["stream_kwargs"]["pinned_skills"] == ["Word 文档"]
    session, user_message = _stored_user_message(db, captured)
    assert user_message["content"] == "帮我排个版"
    assert user_message["skills"] == ["docx"]
    assert session.title == "帮我排个版"


def test_stream_records_turn_id_on_the_user_message(client, db, monkeypatch):
    """DA-03：用户消息落库时带上本轮 id（= message_id），与 thread 里本轮消息的 id 一致。"""
    captured = _patch_stream(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "你好", "message_id": "m-42"})

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["message_id"] == "m-42"
    _, user_message = _stored_user_message(db, captured)
    assert user_message["turn_id"] == "m-42"


def test_stream_skills_only_without_text_uses_display_name(client, db, monkeypatch):
    """只挂技能、没写正文：正文为空也放行，标题用技能展示名兜底。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "", "skills": ["docx"]})

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == ""
    assert captured["stream_kwargs"]["pinned_skills"] == ["Word 文档"]
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
    assert captured["stream_kwargs"]["parsed_content"] == "/pdf 帮我看看"
    assert captured["stream_kwargs"]["pinned_skills"] == ["Word 文档", "PDF 文档"]
    _session, user_message = _stored_user_message(db, captured)
    assert user_message["skills"] == ["docx", "pdf"]


def test_stream_legacy_skill_prefix_still_works(client, db, monkeypatch):
    """旧前端（或手打 `/技能名`）：文本解析回退，落库正文保持原样，标题照旧剥前缀。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "/docx 帮我排个版"})

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "/docx 帮我排个版"
    assert captured["stream_kwargs"]["pinned_skills"] == ["Word 文档"]
    session, user_message = _stored_user_message(db, captured)
    assert user_message["content"] == "/docx 帮我排个版"
    assert user_message["skills"] == ["docx"]
    assert session.title == "帮我排个版"


def test_stream_skills_field_unknown_dir_ignored(client, db, monkeypatch):
    """字段里不属于该用户的目录名：只记日志，不 pin、不落 skills 字段。"""
    _patch_skills(monkeypatch)
    captured = _patch_stream(monkeypatch)

    resp = client.post(
        "/agent/chat/stream", json={"message": "你好", "skills": ["根本没装"]}
    )

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["parsed_content"] == "你好"
    assert captured["stream_kwargs"]["pinned_skills"] == []
    _session, user_message = _stored_user_message(db, captured)
    assert "skills" not in user_message


def test_stream_disabled_skill_is_not_pinned(client, db, monkeypatch):
    """已停用的技能不在技能源里，pin 不到：只记日志，落库仍记下用户选了它。"""
    from easy_agent.services import skill_library

    _patch_skills(monkeypatch)
    disabled = [{**_SKILL_DIRS[0], "enabled": False}, _SKILL_DIRS[1]]
    monkeypatch.setattr(skill_library, "list_user_skill_dirs", lambda username: list(disabled))
    captured = _patch_stream(monkeypatch)

    resp = client.post("/agent/chat/stream", json={"message": "看看", "skills": ["docx", "pdf"]})

    assert resp.status_code == 200
    assert captured["stream_kwargs"]["pinned_skills"] == ["PDF 文档"]
    _session, user_message = _stored_user_message(db, captured)
    assert user_message["skills"] == ["docx", "pdf"]


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
