"""T7：知识库域的同步查库 / 写库调用移出事件循环。

覆盖四类改动：

- ``get_knowledge_principal`` 改成同步依赖（FastAPI 把 def 依赖放进线程池）；
- ``knowledges/api.py`` 里定位 / 创建面板会话（同步查库）经 ``run_in_threadpool``；
- ``chat_bridge`` 的 ``has_session_scope`` 与身份解析经 ``asyncio.to_thread``；
- ``chat_stream_generator`` 的证据元数据落库经 ``run_blocking``。

测试用假数据库 / 假服务，记录每次调用的线程，确认同步 IO 不在事件循环线程上执行。
"""

from __future__ import annotations

import inspect
import threading
from types import SimpleNamespace

import pytest

from easy_agent.knowledges import api as knowledge_api
from easy_agent.knowledges.chat import chat_bridge
from easy_agent.knowledges.chat import streaming as knowledge_streaming
from easy_agent.knowledges.core.models import KnowledgePrincipal
from easy_agent.services.streaming.sse import format_sse


def _principal() -> KnowledgePrincipal:
    return KnowledgePrincipal(user_id="u-1", username="alice", department_id=None)


class _RecordingService:
    """只实现测试用到的异步服务方法。"""

    def __init__(self, base) -> None:
        self.base = base
        self.calls: list[str] = []

    async def get_base(self, base_id, principal):
        self.calls.append("get_base")
        return self.base

    async def replace_session_scope(self, **kwargs):
        self.calls.append("replace_session_scope")


def test_get_knowledge_principal_is_a_sync_dependency():
    """鉴权依赖保持同步 def，由 FastAPI 放进线程池执行（不再占事件循环查库）。"""
    assert not inspect.iscoroutinefunction(knowledge_api.get_knowledge_principal)


@pytest.mark.asyncio
async def test_ensure_chat_session_offloads_sync_helpers(monkeypatch):
    """定位 / 创建面板会话是同步查库，必须在线程池里执行。"""
    loop_thread = threading.get_ident()
    helper_calls: list[str] = []
    session = SimpleNamespace(session_id="session-1")

    def fake_current(database, principal, base_id):
        assert threading.get_ident() != loop_thread
        helper_calls.append("current")
        return None

    def fake_create(database, principal, base_id, base):
        assert threading.get_ident() != loop_thread
        helper_calls.append("create")
        return session

    monkeypatch.setattr(knowledge_api, "_current_panel_chat_session", fake_current)
    monkeypatch.setattr(knowledge_api, "_create_panel_chat_session", fake_create)
    service = _RecordingService(base=SimpleNamespace(name="知识库"))

    got_session, got_base = await knowledge_api._ensure_knowledge_chat_session(
        base_id="base-1", db=object(), service=service, principal=_principal()
    )

    assert got_session is session
    assert got_base is service.base
    assert helper_calls == ["current", "create"]
    assert service.calls == ["get_base", "replace_session_scope"]


@pytest.mark.asyncio
async def test_rotate_chat_session_offloads_create_helper(monkeypatch):
    """轮换面板会话时创建会话记录也放进线程。"""
    loop_thread = threading.get_ident()
    recorded: list[bool] = []
    session = SimpleNamespace(session_id="session-2")

    def fake_create(database, principal, base_id, base):
        recorded.append(threading.get_ident() != loop_thread)
        return session

    monkeypatch.setattr(knowledge_api, "_create_panel_chat_session", fake_create)
    service = _RecordingService(base=SimpleNamespace(name="知识库"))

    response = await knowledge_api.rotate_easyagent_chat_session(
        base_id="base-1", db=object(), service=service, principal=_principal()
    )

    assert response.session_id == "session-2"
    assert recorded == [True]


@pytest.mark.asyncio
async def test_cancel_answer_offloads_session_lookup(monkeypatch):
    """停止回答前的面板会话定位放进线程。"""
    loop_thread = threading.get_ident()
    recorded: list[bool] = []

    def fake_current(database, principal, base_id):
        recorded.append(threading.get_ident() != loop_thread)
        return SimpleNamespace(session_id="session-3")

    class _Capabilities:
        async def cancel_stream_task(self, session_id):
            return True

        def remove_session_agent(self, session_id):
            return None

    monkeypatch.setattr(knowledge_api, "_current_panel_chat_session", fake_current)
    monkeypatch.setattr(knowledge_api, "get_host_capabilities", lambda: _Capabilities())
    service = _RecordingService(base=SimpleNamespace(name="知识库"))

    result = await knowledge_api.cancel_easyagent_answer(
        base_id="base-1", db=object(), service=service, principal=_principal()
    )

    assert result == {"status": "cancelled"}
    assert recorded == [True]


@pytest.mark.asyncio
async def test_authorize_scoped_chat_offloads_scope_lookup_and_principal(monkeypatch):
    """宿主聊天主路径上的 scope 查询与身份解析都在线程里执行。"""
    loop_thread = threading.get_ident()
    calls: list[tuple[str, bool]] = []

    class _Repository:
        def __init__(self, database) -> None:
            self.database = database

        def has_session_scope(self, *, session_id):
            calls.append(("has_session_scope", threading.get_ident() != loop_thread))
            return True

    def fake_principal(request, database):
        calls.append(("principal", threading.get_ident() != loop_thread))
        return _principal()

    monkeypatch.setattr(chat_bridge, "KnowledgeRepository", _Repository)
    monkeypatch.setattr(chat_bridge, "get_knowledge_principal", fake_principal)

    username = await chat_bridge.authorize_scoped_chat(
        request=SimpleNamespace(),
        db=object(),
        session_id="session-1",
        username="fallback",
    )

    assert username == "alice"
    assert calls == [
        ("has_session_scope", True),
        ("principal", True),
    ]


@pytest.mark.asyncio
async def test_prepare_knowledge_chat_offloads_scope_lookup(monkeypatch):
    """无知识范围时只查一次 scope，且在线程里执行。"""
    loop_thread = threading.get_ident()
    thread_flags: list[bool] = []

    class _Repository:
        def __init__(self, database) -> None:
            self.database = database

        def has_session_scope(self, *, session_id):
            thread_flags.append(threading.get_ident() != loop_thread)
            return False

    monkeypatch.setattr(chat_bridge, "KnowledgeRepository", _Repository)

    context = await chat_bridge.prepare_knowledge_chat(
        request=SimpleNamespace(),
        db=object(),
        session_id="session-1",
        question="问题",
    )

    assert context.context is None
    assert thread_flags == [True]


class _FakeHostStream:
    """模拟宿主 ``chat_stream_generator`` 的 async generator + ``aclose``。"""

    def __init__(self, frames) -> None:
        self._frames = list(frames)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_chat_stream_generator_persists_metadata_in_thread(monkeypatch):
    """证据元数据落库经 run_blocking 放进线程，不在事件循环上查 / 写库。"""
    loop_thread = threading.get_ident()
    db_calls: list[bool] = []
    done_frame = format_sse({"type": "done", "session_id": "kb-1"})

    def fake_host_generator(**kwargs):
        return _FakeHostStream([done_frame])

    monkeypatch.setattr(
        knowledge_streaming,
        "get_host_capabilities",
        lambda: SimpleNamespace(host_chat_stream_generator=fake_host_generator),
    )

    class _DB:
        def get_session(self, session_id):
            db_calls.append(threading.get_ident() != loop_thread)
            return SimpleNamespace(messages=[{"role": "assistant", "content": "答"}])

        def update_last_assistant_message(self, session_id, message):
            db_calls.append(threading.get_ident() != loop_thread)

        def update_last_assistant_message_row(self, session_id, message):
            db_calls.append(threading.get_ident() != loop_thread)

    frames = [
        frame
        async for frame in knowledge_streaming.chat_stream_generator(
            initial_events=[],
            assistant_metadata={"knowledge_evidence": []},
            request=SimpleNamespace(message="问"),
            db=_DB(),
            session_id="kb-1",
            parsed_content="问",
        )
    ]

    assert frames == [done_frame]
    # 终态时落一次、finally 再落一次：每次 get_session + 两处双写 = 3 次库调用
    assert len(db_calls) == 6
    assert all(db_calls)
