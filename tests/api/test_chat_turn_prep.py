"""本轮请求准备函数测试：聊天与专家执行共用的空请求校验、技能解析、取会话和上传文件上下文。"""

import pytest
from fastapi import HTTPException

import easy_agent.api.chat as chat_api
from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.db.repositories.expert_repository import ExpertRepository


@pytest.fixture(autouse=True)
def _no_user_skills(monkeypatch):
    """标题生成会查用户技能目录：固定为空，避免用例依赖本机安装的技能。"""
    monkeypatch.setattr(chat_api.skill_library, "list_user_skill_dirs", lambda username: [])


def _session(repo, username="alice", message="第一条消息"):
    session_id = f"sess-{username}"
    chat_api.create_new_session(repo, session_id, username, message, None)
    return session_id


# ── require_turn_content ─────────────────────────────────────────────


@pytest.mark.parametrize("message", ["", "   ", None])
def test_empty_turn_is_rejected(message):
    with pytest.raises(HTTPException) as exc:
        chat_api.require_turn_content(message, None, None)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "message, files, skills",
    [("你好", None, None), ("", [{"filename": "a.txt"}], None), ("", None, ["docx"])],
)
def test_turn_with_text_files_or_skills_is_accepted(message, files, skills):
    chat_api.require_turn_content(message, files, skills)


# ── resolve_request_skills ───────────────────────────────────────────


def test_request_skills_pin_only_enabled_skills(monkeypatch):
    items = [
        {"dir_name": "docx", "name": "Word 文档", "enabled": True},
        {"dir_name": "pdf", "name": "PDF", "enabled": False},
    ]
    monkeypatch.setattr(
        chat_api.skill_library,
        "resolve_turn_skills",
        lambda skills, message, username: (items, ["missing"]),
    )

    turn = chat_api.resolve_request_skills(["docx", "pdf", "missing"], "", "alice", "s1")

    assert turn.dirs == ["docx", "pdf"]
    assert turn.pinned == ["Word 文档"]
    assert turn.fallback_name == "Word 文档"


def test_request_without_skills(monkeypatch):
    monkeypatch.setattr(
        chat_api.skill_library, "resolve_turn_skills", lambda skills, message, username: ([], [])
    )

    turn = chat_api.resolve_request_skills(None, "你好", "alice", "s1")

    assert turn == chat_api.TurnSkills(dirs=[], pinned=[], fallback_name="")


# ── open_turn_session ────────────────────────────────────────────────


@pytest.mark.parametrize("repo_cls", [ChatRepository, ExpertRepository])
def test_new_session_when_id_missing_or_unknown(db, repo_cls):
    repo = repo_cls(db)
    for requested in (None, "no-such-session"):
        session_id, workspace_name = chat_api.open_turn_session(
            repo, requested, "alice", "帮我写周报", None
        )
        assert session_id != requested
        session = repo.get_session(session_id)
        assert session.username == "alice"
        assert session.title == "帮我写周报"
        assert workspace_name and session.workspace_name == workspace_name


def test_session_of_another_user_is_404(db):
    repo = ChatRepository(db)
    session_id = _session(repo, "alice")

    with pytest.raises(HTTPException) as exc:
        chat_api.open_turn_session(repo, session_id, "bob", "看看", None)
    assert exc.value.status_code == 404
    assert exc.value.detail == "会话不存在"


def test_empty_session_takes_the_first_message_as_title(db):
    repo = ChatRepository(db)
    session_id = _session(repo, "alice", message="旧标题")

    opened, workspace_name = chat_api.open_turn_session(
        repo, session_id, "alice", "", None, fallback_name="Word 文档"
    )

    assert opened == session_id
    assert workspace_name == repo.get_session(session_id).workspace_name
    assert repo.get_session(session_id).title == "Word 文档"


def test_title_is_kept_once_the_session_has_messages(db):
    repo = ChatRepository(db)
    session_id = _session(repo, "alice", message="旧标题")
    repo.add_message(session_id, {"role": "user", "content": "旧标题", "timestamp": "t0"})

    chat_api.open_turn_session(repo, session_id, "alice", "新问题", None)

    assert repo.get_session(session_id).title == "旧标题"


def test_knowledge_panel_title_is_never_regenerated(db):
    repo = ChatRepository(db)
    session_id = _session(repo, "alice")
    repo.update_session_meta(session_id, "[知识库问答] 制度库", "t1", "alice")

    chat_api.open_turn_session(repo, session_id, "alice", "报销流程是什么", None)

    assert repo.get_session(session_id).title == "[知识库问答] 制度库"


# ── build_turn_uploads ───────────────────────────────────────────────

FILES = [
    {"filename": "a.txt", "file_path": "/uploads/a.txt"},
    {"filename": "空.txt", "file_path": "/uploads/empty.txt"},
    {"filename": "没有路径.txt"},
]


def _fallback_mode(monkeypatch):
    monkeypatch.setattr(chat_api.file_context, "is_enabled", lambda: False)
    contents = {"/uploads/a.txt": "AAA", "/uploads/empty.txt": ""}
    monkeypatch.setattr(chat_api, "parse_file_content", lambda path: contents[path])


def test_no_files_keeps_the_message(db):
    uploads = chat_api.build_turn_uploads(ChatRepository(db), "sess-x", "alice", None, "你好")
    assert uploads == chat_api.TurnUploads("你好", [], 0)


def test_default_mode_prepends_the_turn_preview(db, monkeypatch):
    monkeypatch.setattr(chat_api.file_context, "is_enabled", lambda: True)
    monkeypatch.setattr(
        chat_api.file_context,
        "build_turn_preview",
        lambda session_id, username, files, repo: "【预览】a.txt 开头",
    )

    uploads = chat_api.build_turn_uploads(ChatRepository(db), "sess-x", "alice", FILES, "总结一下")

    assert uploads == chat_api.TurnUploads("【预览】a.txt 开头\n\n总结一下", [], 0)


def test_default_mode_without_preview_keeps_the_message(db, monkeypatch):
    monkeypatch.setattr(chat_api.file_context, "is_enabled", lambda: True)
    monkeypatch.setattr(
        chat_api.file_context, "build_turn_preview", lambda session_id, username, files, repo: ""
    )

    uploads = chat_api.build_turn_uploads(ChatRepository(db), "sess-x", "alice", FILES, "总结一下")

    assert uploads == chat_api.TurnUploads("总结一下", [], 0)


def test_fallback_first_turn_puts_files_into_the_system_prompt(db, monkeypatch):
    _fallback_mode(monkeypatch)
    repo = ChatRepository(db)
    session_id = _session(repo)

    uploads = chat_api.build_turn_uploads(repo, session_id, "alice", FILES, "总结一下")

    assert uploads.model_input == "总结一下"
    assert uploads.parsed_files == 1
    assert uploads.prompt_parts == [
        "## 用户上传的文件内容（已为你解析，请基于这些内容回应用户）\n\n### 文件: a.txt\nAAA"
    ]


def test_fallback_later_turn_prepends_files_to_the_message(db, monkeypatch):
    _fallback_mode(monkeypatch)
    repo = ChatRepository(db)
    session_id = _session(repo)
    repo.add_message(session_id, {"role": "user", "content": "第一条消息", "timestamp": "t0"})

    uploads = chat_api.build_turn_uploads(repo, session_id, "alice", FILES, "总结一下")

    assert uploads == chat_api.TurnUploads("[文件: a.txt]\nAAA\n\n总结一下", [], 1)


def test_fallback_with_nothing_parsed_keeps_the_message(db, monkeypatch):
    _fallback_mode(monkeypatch)
    repo = ChatRepository(db)

    uploads = chat_api.build_turn_uploads(repo, _session(repo), "alice", FILES[1:], "总结一下")

    assert uploads == chat_api.TurnUploads("总结一下", [], 0)
