"""D2：流式编排把 SessionContext 传给共享编译图的 astream。"""

from __future__ import annotations

from types import SimpleNamespace

from easy_agent.agent_graph_cache import SessionContext
from easy_agent.services.streaming.orchestrator import pump_stream


class _FakeGraph:
    def __init__(self) -> None:
        self.kwargs = None

    async def astream(self, payload, **kwargs):
        self.kwargs = kwargs
        if False:  # pragma: no cover - 让函数成为 async generator
            yield None


class _FakeProc:
    def handle(self, mode, data):
        return []


class _FakePersister:
    def maybe_persist(self, proc):
        return None


async def _drain(agent, *, context):
    return [
        event
        async for event in pump_stream(
            agent=agent,
            stream_input={"messages": []},
            stream_config={"configurable": {"thread_id": "s-m"}},
            proc=_FakeProc(),
            persister=_FakePersister(),
            db=None,
            session_id="s",
            sid="s",
            message_id="m",
            session_logger=None,
            context=context,
        )
    ]


async def test_pump_stream_passes_session_context():
    context = SessionContext(
        session_id="s1",
        username="u1",
        safe_username="u1",
        workspace_dir="/w/s1",
        workspace_virtual_path="/workspace",
        user_skills_dir="/w/u1/skills",
        memory_file="/w/s1/memory.md",
        long_term_memory_file="/memories/u1/AGENTS.md",
    )
    graph = _FakeGraph()
    agent = SimpleNamespace(agent=graph, session_context=context)

    assert await _drain(agent, context=agent.session_context) == []
    assert graph.kwargs["context"] is context
    assert graph.kwargs["stream_mode"] == ["messages", "updates"]


async def test_pump_stream_keeps_legacy_call_shape_without_context():
    graph = _FakeGraph()
    agent = SimpleNamespace(agent=graph)

    assert await _drain(agent, context=getattr(agent, "session_context", None)) == []
    assert "context" not in graph.kwargs
    assert graph.kwargs["stream_mode"] == ["messages", "updates"]
