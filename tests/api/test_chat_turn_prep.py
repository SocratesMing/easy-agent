"""本轮请求准备测试：聊天与专家执行共用的准备函数，以及不经 HTTP 的 ``prepare_turn``。

T4 之后准备逻辑在 ``services/agents/chat_turn.py``（路由只剩「参数校验 → prepare → SSE 响应」），
因此这里直接调用服务函数、不经过 HTTP，覆盖：空请求校验、技能解析、取会话、上传文件
上下文，以及 ``prepare_turn`` 的专家拒答 / 并发 409 / 待修改专家越权三种关键场景。
"""

from types import SimpleNamespace
import json

import pytest

import easy_agent.services.agents.chat_turn as chat_turn
from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.knowledges.chat.chat_bridge import KnowledgeChatContext
from easy_agent.models.api import ChatRequest
from easy_agent.models.exceptions import BadRequestError, ConflictError, NotFoundError
from easy_agent.services.skills import provision as skill_provision
from easy_agent.services.experts import runtime as expert_runtime
from easy_agent.services.agents import manager as agent_manager

pytestmark = pytest.mark.usefixtures("finance_classifier")


@pytest.fixture(autouse=True)
def _no_user_skills(monkeypatch):
    """标题生成会查用户技能目录：固定为空，避免用例依赖本机安装的技能。"""
    monkeypatch.setattr(chat_turn.skill_library, "list_user_skill_dirs", lambda username: [])


def _session(repo, username="alice", message="第一条消息"):
    session_id = f"sess-{username}"
    chat_turn.create_new_session(repo, session_id, username, message, None)
    return session_id


# ── require_turn_content ─────────────────────────────────────────────


@pytest.mark.parametrize("message", ["", "   ", None])
def test_empty_turn_is_rejected(message):
    with pytest.raises(BadRequestError) as exc_info:
        chat_turn.require_turn_content(message, None, None)
    assert exc_info.value.message == "消息内容不能为空"


@pytest.mark.parametrize(
    "message, files, skills",
    [("你好", None, None), ("", [{"filename": "a.txt"}], None), ("", None, ["docx"])],
)
def test_turn_with_text_files_or_skills_is_accepted(message, files, skills):
    chat_turn.require_turn_content(message, files, skills)


# ── resolve_request_skills ───────────────────────────────────────────


def test_request_skills_pin_only_enabled_skills(monkeypatch):
    items = [
        {"dir_name": "docx", "name": "Word 文档", "enabled": True},
        {"dir_name": "pdf", "name": "PDF", "enabled": False},
    ]
    monkeypatch.setattr(
        chat_turn.skill_library,
        "resolve_turn_skills",
        lambda skills, message, username: (items, ["missing"]),
    )

    turn = chat_turn.resolve_request_skills(["docx", "pdf", "missing"], "", "alice", "s1")

    assert turn.dirs == ["docx", "pdf"]
    assert turn.pinned == ["Word 文档"]
    assert turn.fallback_name == "Word 文档"


def test_request_without_skills(monkeypatch):
    monkeypatch.setattr(
        chat_turn.skill_library, "resolve_turn_skills", lambda skills, message, username: ([], [])
    )

    turn = chat_turn.resolve_request_skills(None, "你好", "alice", "s1")

    assert turn == chat_turn.TurnSkills(dirs=[], pinned=[], fallback_name="")


# ── open_turn_session ────────────────────────────────────────────────


@pytest.mark.parametrize("repo_cls", [ChatRepository, ExpertRepository])
def test_new_session_when_id_missing_or_unknown(db, repo_cls):
    repo = repo_cls(db)
    for requested in (None, "no-such-session"):
        session_id, workspace_name = chat_turn.open_turn_session(
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

    with pytest.raises(NotFoundError) as exc_info:
        chat_turn.open_turn_session(repo, session_id, "bob", "看看", None)
    assert exc_info.value.message == "会话不存在"


def test_empty_session_takes_the_first_message_as_title(db):
    repo = ChatRepository(db)
    session_id = _session(repo, "alice", message="旧标题")

    opened, workspace_name = chat_turn.open_turn_session(
        repo, session_id, "alice", "", None, fallback_name="Word 文档"
    )

    assert opened == session_id
    assert workspace_name == repo.get_session(session_id).workspace_name
    assert repo.get_session(session_id).title == "Word 文档"


def test_title_is_kept_once_the_session_has_messages(db):
    repo = ChatRepository(db)
    session_id = _session(repo, "alice", message="旧标题")
    repo.add_message(session_id, {"role": "user", "content": "旧标题", "timestamp": "t0"})

    chat_turn.open_turn_session(repo, session_id, "alice", "新问题", None)

    assert repo.get_session(session_id).title == "旧标题"


def test_knowledge_panel_title_is_never_regenerated(db):
    repo = ChatRepository(db)
    session_id = _session(repo, "alice")
    repo.update_session_meta(session_id, "[知识库问答] 制度库", "t1", "alice")

    chat_turn.open_turn_session(repo, session_id, "alice", "报销流程是什么", None)

    assert repo.get_session(session_id).title == "[知识库问答] 制度库"


# ── build_turn_uploads ───────────────────────────────────────────────

FILES = [
    {"filename": "a.txt", "file_path": "/uploads/a.txt"},
    {"filename": "空.txt", "file_path": "/uploads/empty.txt"},
    {"filename": "没有路径.txt"},
]


def _fallback_mode(monkeypatch):
    monkeypatch.setattr(chat_turn.file_context, "is_enabled", lambda: False)
    contents = {"/uploads/a.txt": "AAA", "/uploads/empty.txt": ""}
    monkeypatch.setattr(chat_turn, "parse_file_content", lambda path: contents[path])


def test_no_files_keeps_the_message(db):
    uploads = chat_turn.build_turn_uploads(ChatRepository(db), "sess-x", "alice", None, "你好")
    assert uploads == chat_turn.TurnUploads("你好", [], 0)


def test_default_mode_prepends_the_turn_preview(db, monkeypatch):
    monkeypatch.setattr(chat_turn.file_context, "is_enabled", lambda: True)
    monkeypatch.setattr(
        chat_turn.file_context,
        "build_turn_preview",
        lambda session_id, username, files, repo: "【预览】a.txt 开头",
    )

    uploads = chat_turn.build_turn_uploads(ChatRepository(db), "sess-x", "alice", FILES, "总结一下")

    assert uploads == chat_turn.TurnUploads("【预览】a.txt 开头\n\n总结一下", [], 0)


def test_default_mode_without_preview_keeps_the_message(db, monkeypatch):
    monkeypatch.setattr(chat_turn.file_context, "is_enabled", lambda: True)
    monkeypatch.setattr(
        chat_turn.file_context, "build_turn_preview", lambda session_id, username, files, repo: ""
    )

    uploads = chat_turn.build_turn_uploads(ChatRepository(db), "sess-x", "alice", FILES, "总结一下")

    assert uploads == chat_turn.TurnUploads("总结一下", [], 0)


def test_fallback_first_turn_puts_files_into_the_system_prompt(db, monkeypatch):
    _fallback_mode(monkeypatch)
    repo = ChatRepository(db)
    session_id = _session(repo)

    uploads = chat_turn.build_turn_uploads(repo, session_id, "alice", FILES, "总结一下")

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

    uploads = chat_turn.build_turn_uploads(repo, session_id, "alice", FILES, "总结一下")

    assert uploads == chat_turn.TurnUploads("[文件: a.txt]\nAAA\n\n总结一下", [], 1)


def test_fallback_with_nothing_parsed_keeps_the_message(db, monkeypatch):
    _fallback_mode(monkeypatch)
    repo = ChatRepository(db)

    uploads = chat_turn.build_turn_uploads(
        repo, _session(repo), "alice", FILES[1:], "总结一下"
    )

    assert uploads == chat_turn.TurnUploads("总结一下", [], 0)


# ── prepare_turn（不经 HTTP）──────────────────────────────────────────


def _turn_request(**kwargs) -> ChatRequest:
    return ChatRequest(message=kwargs.pop("message", "你好"), **kwargs)


def _http_request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


def _patch_collaborators(monkeypatch, captured):
    """替换真实 Agent / 知识检索 / 生成器，只验证准备阶段的编排。

    ``chat_stream_generator`` 用「普通函数返回 async generator」的假实现：调用时就把
    参数记下来（async generator 函数体要到首次迭代才执行），便于断言传给生成器的字段。
    """

    def fake_stream(**kwargs):
        captured["stream_kwargs"] = kwargs

        async def _gen():
            yield 'data: {"type": "done"}\n\n'

        return _gen()

    async def fake_knowledge(**kwargs):
        captured["knowledge_kwargs"] = kwargs
        return KnowledgeChatContext(None, [], [])

    def fake_provision(username, expert_id, **kwargs):
        captured["provision_kwargs"] = kwargs
        return {"required": [], "installed": [], "existing": [], "skipped": []}

    async def fake_agent(session_id, username, workspace_name, **kwargs):
        captured["agent_kwargs"] = kwargs
        return SimpleNamespace(workspace_dir=None, workspace_virtual_path="/workspace")

    monkeypatch.setattr(chat_turn, "get_or_create_agent_for_session", fake_agent)
    monkeypatch.setattr(chat_turn, "chat_stream_generator", fake_stream)
    monkeypatch.setattr(chat_turn, "prepare_knowledge_chat", fake_knowledge)
    monkeypatch.setattr(skill_provision, "provision_expert_skills", fake_provision)
    return captured


async def test_prepare_turn_returns_plan_and_persists_user_message(db, monkeypatch):
    repo = ChatRepository(db)
    captured = {}
    _patch_collaborators(monkeypatch, captured)

    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="帮我排个版"),
        http_request=_http_request(),
        repo=repo,
        username="alice",
    )

    assert isinstance(plan, chat_turn.TurnPlan)
    assert plan.session_id == captured["stream_kwargs"]["session_id"]
    assert plan.message_id == captured["stream_kwargs"]["message_id"]
    assert plan.parsed_content == "帮我排个版"
    session = repo.get_session(plan.session_id)
    assert session.messages[0]["content"] == "帮我排个版"
    assert session.messages[0]["turn_id"] == plan.message_id


async def test_prepare_turn_expert_refusal_returns_refusal_plan(db, monkeypatch):
    """专家相关性预检拒答：返回 RefusalPlan，不建 Agent、不登记 run。"""
    repo = ChatRepository(db)
    captured = {}
    _patch_collaborators(monkeypatch, captured)
    monkeypatch.setattr(
        expert_runtime,
        "load_expert_prompt",
        lambda *args, **kwargs: ("preset_x", "专家提示词", "preset"),
    )

    async def refuse(*args, **kwargs):
        return "irrelevant", "这个问题不在本专家的职责范围内。"

    monkeypatch.setattr(expert_runtime, "relevance_gate", refuse)

    async def must_not_build(*args, **kwargs):
        raise AssertionError("拒答时不应创建 Agent")

    monkeypatch.setattr(chat_turn, "get_or_create_agent_for_session", must_not_build)

    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="今天天气如何", expert_agent_id="preset_x"),
        http_request=_http_request(),
        repo=repo,
        username="alice",
    )

    assert isinstance(plan, chat_turn.RefusalPlan)
    assert plan.classification == "irrelevant"
    assert plan.refusal == "这个问题不在本专家的职责范围内。"
    assert plan.event_stream is not None
    # 没有进入 Agent 生成器；依赖技能补装在事件循环外执行（缓存驱逐才回到事件循环）
    assert "stream_kwargs" not in captured
    assert captured["provision_kwargs"]["invalidate_cache"] is False


async def test_prepare_turn_rejects_concurrent_stream_before_persisting(db, monkeypatch):
    """并发 409：在用户消息落库之前返回，会话里不会多出无回复的消息。"""
    repo = ChatRepository(db)
    session_id = _session(repo, "alice", "旧会话")
    monkeypatch.setattr(
        agent_manager, "is_session_streaming", lambda sid: sid == session_id
    )

    async def must_not_build(*args, **kwargs):
        raise AssertionError("会话正忙时不应创建 Agent")

    monkeypatch.setattr(chat_turn, "get_or_create_agent_for_session", must_not_build)

    with pytest.raises(ConflictError) as exc_info:
        await chat_turn.prepare_turn(
            request=_turn_request(message="你好", session_id=session_id),
            http_request=_http_request(),
            repo=repo,
            username="alice",
        )

    assert exc_info.value.message == "本会话正在生成回复，请等待完成或先取消当前生成"
    assert repo.get_session(session_id).messages == []


async def test_prepare_turn_ignores_unauthorized_pending_expert(db, monkeypatch):
    """待修改专家越权 / 不存在：忽略绑定，本轮照常进行（不写入 edit_target）。"""
    from easy_agent.tools import expert_writer

    repo = ChatRepository(db)
    captured = {}
    _patch_collaborators(monkeypatch, captured)
    bound: list[tuple[str, str]] = []

    async def fake_edit_target(session_id, expert_agent_id):
        bound.append((session_id, expert_agent_id))

    monkeypatch.setattr(agent_manager, "aset_session_edit_target", fake_edit_target)
    monkeypatch.setattr(expert_writer, "resolve_owned_expert", lambda *args, **kwargs: None)

    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="你好", pending_expert_id="preset_other"),
        http_request=_http_request(),
        repo=repo,
        username="alice",
    )

    assert isinstance(plan, chat_turn.TurnPlan)
    assert bound == []

async def test_prepare_turn_provisions_expert_skills_off_the_event_loop(db, monkeypatch):
    """T14.2：依赖技能补装（读库 + 清单读改写）进线程；缓存驱逐（进程内状态）回事件循环。"""
    import threading

    repo = ChatRepository(db)
    captured = {}
    _patch_collaborators(monkeypatch, captured)
    loop_thread = threading.get_ident()
    provision_threads: list[int] = []
    provision_kwargs: list[dict] = []
    invalidate_threads: list[int] = []

    def fake_provision(username, expert_id, **kwargs):
        provision_threads.append(threading.get_ident())
        provision_kwargs.append(kwargs)
        return {"required": ["docx"], "installed": ["docx"], "existing": [], "skipped": []}

    def fake_invalidate(username):
        invalidate_threads.append(threading.get_ident())
        return 1

    async def pass_gate(*args, **kwargs):
        return None

    monkeypatch.setattr(skill_provision, "provision_expert_skills", fake_provision)
    monkeypatch.setattr(agent_manager, "invalidate_user_agents", fake_invalidate)
    monkeypatch.setattr(expert_runtime, "relevance_gate", pass_gate)
    monkeypatch.setattr(
        expert_runtime,
        "load_expert_prompt",
        lambda *args, **kwargs: ("preset_x", "专家提示词", "preset"),
    )

    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="帮我看看这组因子", expert_agent_id="preset_x"),
        http_request=_http_request(),
        repo=repo,
        username="alice",
    )

    assert isinstance(plan, chat_turn.TurnPlan)
    # 补装在线程里执行，且不再由 skill_provision 内部驱逐缓存……
    assert provision_threads and provision_threads[0] != loop_thread
    assert provision_kwargs[0]["invalidate_cache"] is False
    # ……缓存驱逐（改进程内状态）回到事件循环上执行
    assert invalidate_threads == [loop_thread]


@pytest.mark.parametrize("result, expected", [
    ('{"is_finance": false}', "FINANCE_REFUSAL"),
    ('{"is_finance": "true"}', "CLASSIFICATION_UNAVAILABLE"),
])
async def test_finance_gate_stops_before_skills_retrieval_uploads_and_agent(
    db, monkeypatch, finance_classifier, result, expected
):
    from easy_agent.services.agents import domain_gate

    finance_classifier.ainvoke.return_value = SimpleNamespace(content=result)
    repo = ChatRepository(db)

    def forbidden(*args, **kwargs):
        raise AssertionError("被拦截时不应准备技能、解析附件、检索或创建 Agent")

    for name in ("prepare_knowledge_chat", "build_turn_uploads",
                 "get_or_create_agent_for_session"):
        monkeypatch.setattr(chat_turn, name, forbidden)
    monkeypatch.setattr(skill_provision, "provision_expert_skills", forbidden)
    monkeypatch.setattr(expert_runtime, "load_expert_prompt", forbidden)
    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="帮我写一首诗", files=FILES),
        http_request=_http_request(), repo=repo, username="alice",
    )
    assert isinstance(plan, chat_turn.RefusalPlan)
    assert plan.refusal == getattr(domain_gate, expected)
    frames = [json.loads(frame.removeprefix("data: ")) async for frame in plan.event_stream]
    assert [frame["type"] for frame in frames] == ["start", "content", "content_end", "done"]
    assert frames[1]["content"] == plan.refusal
    messages = repo.get_session(plan.session_id).messages
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == plan.refusal
    finance_classifier.ainvoke.assert_awaited_once()


@pytest.mark.parametrize("question", [
    "分析国债收益率", "用 Python 计算股票收益率", "证券账户如何防诈骗", "继续分析",
])
async def test_finance_questions_enter_original_flow_once(
    db, monkeypatch, finance_classifier, question
):
    repo = ChatRepository(db)
    session_id = _session(repo)
    repo.add_message(session_id, {"role": "user", "content": "分析国债收益率"})
    _patch_collaborators(monkeypatch, {})
    plan = await chat_turn.prepare_turn(
        request=_turn_request(message=question, session_id=session_id),
        http_request=_http_request(), repo=repo, username="alice",
    )
    assert isinstance(plan, chat_turn.TurnPlan)
    finance_classifier.ainvoke.assert_awaited_once()
    payload = json.loads(finance_classifier.ainvoke.await_args.args[0][1].content)
    assert payload["question"] == question
    assert payload["recent_context"] == [{"role": "user", "content": "分析国债收益率"}]
    await plan.event_stream.aclose()


@pytest.mark.parametrize("skills, message", [
    (["pptx"], "用它生成ppt"), (None, "/pptx 用它生成ppt"),
    (None, "生成一份项目进度汇报PPT"), (["pptx"], ""),
])
async def test_validated_skill_selection_is_visible_to_classifier(
    db, monkeypatch, finance_classifier, skills, message
):
    repo = ChatRepository(db)
    _patch_collaborators(monkeypatch, {})
    monkeypatch.setattr(chat_turn.skill_library, "list_user_skill_dirs", lambda username: [
        {"dir_name": "pptx", "name": "pptx", "path": "/tmp/skills/pptx", "enabled": True},
        {"dir_name": "pdf", "name": "pdf", "path": "/tmp/skills/pdf", "enabled": False},
    ])
    monkeypatch.setattr(chat_turn.skill_library, "skill_metadata", lambda path: {
        "name": str(path).split("/")[-1], "description": "文档生成与转换",
    })
    plan = await chat_turn.prepare_turn(
        request=_turn_request(message=message, skills=skills),
        http_request=_http_request(), repo=repo, username="alice",
    )
    assert isinstance(plan, chat_turn.TurnPlan)
    payload = json.loads(finance_classifier.ainvoke.await_args.args[0][1].content)
    assert payload["selected_skills"] == (["pptx"] if skills or message.startswith("/") else [])
    assert payload["available_skills"] == [{"name": "pptx", "description": "文档生成与转换"}]
    if not message:
        assert repo.get_session(plan.session_id).title == "pptx"
    await plan.event_stream.aclose()


async def test_finance_gate_approval_reaches_the_business_model(db, monkeypatch):
    """模拟截图的旧拒答历史：分类放行必须继续传入实际模型调用。"""
    from easy_agent.services.agents import domain_gate

    repo = ChatRepository(db)
    session_id = _session(repo)
    repo.add_message(session_id, {"role": "assistant", "content": "不支持制作 PPT 等非金融任务。"})
    captured = _patch_collaborators(monkeypatch, {})
    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="生成ppt", session_id=session_id),
        http_request=_http_request(), repo=repo, username="alice",
    )
    assert captured["stream_kwargs"]["system_context"] == domain_gate.APPROVED_TURN_PROMPT
    assert captured["stream_kwargs"]["scope_approved"] is True
    assert repo.get_session(session_id).messages[-1]["content"] == "生成ppt"
    await plan.event_stream.aclose()


@pytest.mark.parametrize("result, expected", [
    ('{"is_finance": false}', "FINANCE_REFUSAL"),
    ('{"is_finance": "true"}', "CLASSIFICATION_UNAVAILABLE"),
])
async def test_enabled_skill_selection_still_checks_the_actual_intent(
    db, monkeypatch, finance_classifier, result, expected
):
    from easy_agent.services.agents import domain_gate

    monkeypatch.setattr(chat_turn.skill_library, "list_user_skill_dirs", lambda username: [
        {"dir_name": "pptx", "path": "/tmp/skills/pptx", "enabled": True},
    ])
    monkeypatch.setattr(chat_turn.skill_library, "skill_metadata", lambda path: {
        "name": "pptx", "description": "创建演示文稿",
    })
    finance_classifier.ainvoke.return_value = SimpleNamespace(content=result)

    async def forbidden(*args, **kwargs):
        raise AssertionError("意图不符或分类失败时不创建 Agent")

    monkeypatch.setattr(chat_turn, "get_or_create_agent_for_session", forbidden)
    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="推荐旅游路线", skills=["pptx"]),
        http_request=_http_request(), repo=ChatRepository(db), username="alice",
    )
    assert isinstance(plan, chat_turn.RefusalPlan)
    assert plan.refusal == getattr(domain_gate, expected)
    payload = json.loads(finance_classifier.ainvoke.await_args.args[0][1].content)
    assert payload["selected_skills"] == ["pptx"]
    await plan.event_stream.aclose()


async def test_fake_or_disabled_skill_cannot_skip_classification(db, monkeypatch, finance_classifier):
    from easy_agent.services.agents import domain_gate

    monkeypatch.setattr(chat_turn.skill_library, "list_user_skill_dirs", lambda username: [
        {"dir_name": "pptx", "path": "/tmp/skills/pptx", "enabled": False},
    ])
    monkeypatch.setattr(chat_turn.skill_library, "skill_metadata", lambda path: {"name": "pptx"})
    finance_classifier.ainvoke.return_value = SimpleNamespace(content='{"is_finance": false}')
    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="帮我写一首诗", skills=["pptx", "fake-skill"]),
        http_request=_http_request(), repo=ChatRepository(db), username="alice",
    )
    assert isinstance(plan, chat_turn.RefusalPlan)
    assert plan.refusal == domain_gate.FINANCE_REFUSAL
    payload = json.loads(finance_classifier.ainvoke.await_args.args[0][1].content)
    assert payload["selected_skills"] == []
    assert payload["available_skills"] == []
    await plan.event_stream.aclose()


async def test_attachment_without_question_requests_finance_purpose(db, finance_classifier):
    from easy_agent.services.agents import domain_gate

    plan = await chat_turn.prepare_turn(
        request=_turn_request(message="", files=FILES),
        http_request=_http_request(), repo=ChatRepository(db), username="alice",
    )
    assert isinstance(plan, chat_turn.RefusalPlan)
    assert plan.refusal == domain_gate.ATTACHMENT_QUESTION_REQUIRED
    finance_classifier.ainvoke.assert_not_awaited()
    await plan.event_stream.aclose()


async def test_session_authorization_precedes_finance_classification(db, finance_classifier):
    repo = ChatRepository(db)
    session_id = _session(repo, username="bob")
    with pytest.raises(NotFoundError):
        await chat_turn.prepare_turn(
            request=_turn_request(message="分析国债收益率", session_id=session_id),
            http_request=_http_request(), repo=repo, username="alice",
        )
    finance_classifier.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("entry", ["expert", "knowledge_panel"])
async def test_non_general_chat_skips_finance_gate(
    db, monkeypatch, finance_classifier, entry
):
    repo = ChatRepository(db)
    session_id = _session(repo)
    captured = _patch_collaborators(monkeypatch, {})
    finance_classifier.ainvoke.side_effect = AssertionError("仅普通聊天执行金融范围判断")
    if entry == "knowledge_panel":
        repo.update_session_meta(session_id, "[知识库问答] 制度库", "t1", "alice")

    async def expert_gate(*args, **kwargs):
        return None

    monkeypatch.setattr(expert_runtime, "relevance_gate", expert_gate)
    monkeypatch.setattr(
        expert_runtime, "load_expert_prompt", lambda *a: ("preset_x", "专家提示词", "preset")
    )
    plan = await chat_turn.prepare_turn(
        request=_turn_request(
            message="帮我写一首诗", session_id=session_id,
            expert_agent_id="preset_x" if entry == "expert" else None,
        ),
        http_request=_http_request(), repo=repo, username="alice",
    )
    assert isinstance(plan, chat_turn.TurnPlan)
    finance_classifier.ainvoke.assert_not_awaited()
    assert captured["stream_kwargs"].get("system_context", "") == ""
    assert captured["stream_kwargs"].get("scope_approved", False) is False
    await plan.event_stream.aclose()
