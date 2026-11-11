"""Business ownership survives delayed tasks, thread queues, and failure cleanup."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from easy_agent.services import usage
from easy_agent.services.streaming import side_effects
from easy_agent.utils.usage_context import UsageIdentity, get_usage_identity, usage_scope


@pytest.mark.parametrize("failure", ["missing", "database"])
async def test_failed_lookup_never_substitutes_username_or_inherits_outer_user(monkeypatch, failure):
    def lookup(username):
        if failure == "database":
            raise RuntimeError("unavailable")
        return None

    monkeypatch.setattr(usage, "get_user_repository", lambda: SimpleNamespace(get_user_by_username=lookup))
    with usage_scope("outer-user", "outer-session"):
        identity = await usage.resolve_usage_identity("alice", "session")
        assert identity is None
        with usage_scope(identity.user_id if identity else None):
            assert get_usage_identity() is None
        assert get_usage_identity() == UsageIdentity("outer-user", "outer-session")
    assert get_usage_identity() is None


def test_memory_queue_freezes_each_job_identity_before_previous_worker_submits_it(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(side_effects, "_MEMORY_UPDATE_EXECUTOR", executor)
    started, release = threading.Event(), threading.Event()
    observed = []

    def first():
        observed.append(get_usage_identity())
        started.set()
        assert release.wait(5)

    try:
        with usage_scope("alice-id", "session-a"):
            first_done = side_effects._submit_memory_job("shared-memory", first)
        assert started.wait(5)
        with usage_scope("bob-id", "session-b"):
            second_done = side_effects._submit_memory_job(
                "shared-memory", lambda: observed.append(get_usage_identity()),
            )
        # Also prove an explicit empty identity cannot inherit the previous worker.
        empty_done = side_effects._submit_memory_job(
            "shared-memory", lambda: observed.append(get_usage_identity()),
        )
        release.set()
        first_done.result(5)
        second_done.result(5)
        empty_done.result(5)
        assert observed == [UsageIdentity("alice-id", "session-a"), UsageIdentity("bob-id", "session-b"), None]
        assert executor.submit(get_usage_identity).result(5) is None
    finally:
        release.set()
        executor.shutdown(wait=True)


async def test_completion_uses_real_id_and_resets_scope_on_model_error(db, monkeypatch):
    from easy_agent.api import completion
    from easy_agent.models.api import ChatCompletionMessage, ChatCompletionRequest

    user = db.register_user("scope-alice", "password")
    seen = []

    async def invoke(messages):
        seen.append(get_usage_identity())
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(completion, "_get_llm", lambda model: SimpleNamespace(ainvoke=invoke))
    with usage_scope("outer"):
        with pytest.raises(Exception, match="大模型调用失败"):
            await completion.chat_completion(
                ChatCompletionRequest(messages=[ChatCompletionMessage(role="user", content="hi")]),
                username=user.username,
            )
        assert get_usage_identity() == UsageIdentity("outer")
    assert seen == [UsageIdentity(user.user_id)]


async def test_scheduler_uses_owner_session_and_preserves_pause_callback(db):
    from easy_agent.services import scheduler

    owner = db.register_user("task-owner", "password")
    task = SimpleNamespace(username=owner.username, task_id="task-usage", task_prompt="run", enabled=True)
    observed = []

    async def invoke(payload, config=None):
        observed.append(get_usage_identity())
        assert len(config["callbacks"]) == 1
        callback = config["callbacks"][0]
        assert isinstance(callback, scheduler._PauseAwareCallback)
        scheduler.stopping_tasks.add(task.task_id)
        try:
            with pytest.raises(scheduler._TaskAborted):
                callback.on_llm_start({}, [])
        finally:
            scheduler.stopping_tasks.discard(task.task_id)
        return {"messages": []}

    async def create(*args, **kwargs):
        return SimpleNamespace(agent=SimpleNamespace(ainvoke=invoke))

    execution = SimpleNamespace(session_id="task-session", workspace_name="workspace", run_id="run")
    with usage_scope("unrelated-parent"):
        await scheduler._invoke_task_agent(
            task=task, execution=execution, get_or_create_agent_for_session=create,
            scheduled_repo=SimpleNamespace(get_scheduled_task=lambda task_id: task),
        )
        assert get_usage_identity() == UsageIdentity("unrelated-parent")
    assert observed == [UsageIdentity(owner.user_id, "task-session")]


async def test_knowledge_ask_uses_trusted_principal_without_username_lookup(monkeypatch):
    from easy_agent.knowledges import api
    from easy_agent.knowledges.core.models import AskRequest, KnowledgePrincipal

    observed = []

    async def answer(question, evidence):
        observed.append(get_usage_identity())
        return "answer", []

    async def retrieve(**kwargs):
        return SimpleNamespace(evidence=[], warnings=[])

    monkeypatch.setattr(api, "_answer", answer)
    fake_service = SimpleNamespace(
        _retrieve_base_evidence=retrieve,
        config=SimpleNamespace(audit=SimpleNamespace(enabled=False)),
    )
    principal = KnowledgePrincipal(user_id="principal-real-id", username="display-name", department_id=None)
    with usage_scope("unrelated-parent", "other-session"):
        result = await api.ask(
            base_id="base", payload=AskRequest(question="question"),
            request=SimpleNamespace(state=SimpleNamespace(request_id="request")),
            service=fake_service, principal=principal,
        )
        assert get_usage_identity() == UsageIdentity("unrelated-parent", "other-session")
    assert result.answer == "answer"
    assert observed == [UsageIdentity("principal-real-id")]


async def test_delayed_chat_generator_scopes_actual_execution(db, monkeypatch):
    from easy_agent.models.api import ChatRequest
    from easy_agent.services.streaming import orchestrator

    alice = db.register_user("stream-alice", "password")
    bob = db.register_user("stream-bob", "password")
    seen = []
    entered, release = asyncio.Event(), asyncio.Event()

    # Stop after the actual generator begins executing. This requires no model or
    # checkpoint and proves the scope is established at iteration, in its task.
    async def lookup_session(sid):
        seen.append(get_usage_identity())
        entered.set()
        await release.wait()
        raise RuntimeError("stop preparation")

    async def blocking(func, *args, **kwargs):
        return await lookup_session(*args)

    monkeypatch.setattr(orchestrator, "run_blocking", blocking)
    monkeypatch.setattr(orchestrator, "resolve_chat_context", lambda agent: (None, None, 200))
    fake_agent = SimpleNamespace(workspace_dir=None, config=None)
    streams = [orchestrator.chat_stream_generator(
        request=ChatRequest(message="hello"), db=SimpleNamespace(get_session=lambda sid: None),
        agent=fake_agent, session_id=f"session-{user.username}", message_id="turn", username=user.username,
    ) for user in [alice, bob]]

    async def consume(stream):
        return [frame async for frame in stream]

    with usage_scope("unrelated-request"):
        tasks = [asyncio.create_task(consume(stream)) for stream in streams]
    await asyncio.wait_for(entered.wait(), 5)
    release.set()
    await asyncio.gather(*tasks)
    assert sorted(identity.user_id for identity in seen) == sorted([alice.user_id, bob.user_id])
    assert get_usage_identity() is None
