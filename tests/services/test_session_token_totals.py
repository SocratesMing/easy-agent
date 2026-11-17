"""Only the two conversation totals change; other usage remains a call snapshot."""

from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.services import usage
from easy_agent.services.streaming.sse import format_sse, parse_sse_frame
from easy_agent.utils.usage_context import UsageIdentity, usage_scope


def add(repo, user_id, event, incoming, outgoing):
    repo.record_usage(
        event_id=event, user_id=user_id, session_id="chat", model_key="default",
        model_name="model", completed_at_ms=1, input_tokens=incoming, output_tokens=outgoing,
    )


async def test_stream_totals_survive_turns_resume_and_replay_without_changing_other_fields(db, monkeypatch):
    user = db.register_user("alice", "secret123")
    repo = TokenUsageRepository(db)
    monkeypatch.setattr(usage, "get_token_usage_repository", lambda: repo)
    add(repo, user.user_id, "previous-turn", 100, 10)

    async def stream():
        yield format_sse({"type": "start", "session_id": "chat"})
        add(repo, user.user_id, "tool-call", 200, 20)
        yield format_sse({"type": "token_usage", "input_tokens": 200, "output_tokens": 20,
                          "context_tokens": 200, "reasoning_tokens": 3, "step_count": 1})
        add(repo, user.user_id, "answer", 300, 30)
        yield format_sse({"type": "done", "session_id": "chat", "usage": {
            "input_tokens": 300, "output_tokens": 30, "context_tokens": 300,
            "reasoning_tokens": 4, "step_count": 2, "elapsed_time": 1.5,
        }})

    events = [parse_sse_frame(frame) async for frame in usage.with_session_token_totals(
        stream(), UsageIdentity(user.user_id, "chat"),
    )]
    assert events[0] == {"type": "start", "session_id": "chat"}
    assert events[1] == {"type": "token_usage", "input_tokens": 300, "output_tokens": 30,
                         "context_tokens": 200, "reasoning_tokens": 3, "step_count": 1}
    assert events[2]["usage"] == {
        "input_tokens": 600, "output_tokens": 60, "context_tokens": 300,
        "reasoning_tokens": 4, "step_count": 2, "elapsed_time": 1.5,
    }
    # Resume / repeated terminal frames read absolute totals, never add them again.
    async def replay():
        yield format_sse(events[2])
        yield format_sse(events[2])

    replayed = [parse_sse_frame(frame) async for frame in usage.with_session_token_totals(
        replay(), UsageIdentity(user.user_id, "chat"),
    )]
    assert replayed == [events[2], events[2]]


async def test_stream_wrapper_closes_upstream_on_disconnect():
    closed = []

    async def stream():
        try:
            yield format_sse({"type": "content", "content": "text"})
        finally:
            closed.append(True)

    wrapped = usage.with_session_token_totals(stream(), UsageIdentity("alice-id", "chat"))
    await anext(wrapped)
    await wrapped.aclose()
    assert closed == [True]


async def test_resume_adds_new_calls_but_duplicate_events_do_not_change_totals(db, monkeypatch):
    user = db.register_user("alice", "secret123")
    repo = TokenUsageRepository(db)
    monkeypatch.setattr(usage, "get_token_usage_repository", lambda: repo)
    add(repo, user.user_id, "before-approval", 100, 10)
    add(repo, user.user_id, "after-approval", 200, 20)
    add(repo, user.user_id, "after-approval", 200, 20)

    async def stream():
        for _ in range(2):
            yield format_sse({"type": "token_usage", "input_tokens": 200, "output_tokens": 20,
                              "reasoning_tokens": 4, "context_tokens": 200, "step_count": 2})

    events = [parse_sse_frame(frame) async for frame in usage.with_session_token_totals(
        stream(), UsageIdentity(user.user_id, "chat"),
    )]
    assert events == [{"type": "token_usage", "input_tokens": 300, "output_tokens": 30,
                       "reasoning_tokens": 4, "context_tokens": 200, "step_count": 2}] * 2


async def test_totals_lookup_failure_preserves_last_known_totals_and_stream(monkeypatch):
    from types import SimpleNamespace

    calls = 0

    def totals(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"input_tokens": 300, "output_tokens": 30}
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(usage, "get_token_usage_repository", lambda: SimpleNamespace(session_totals=totals))

    async def stream():
        for _ in range(2):
            yield format_sse({"type": "token_usage", "input_tokens": 200, "output_tokens": 20,
                              "context_tokens": 200, "reasoning_tokens": 4})
        yield format_sse({"type": "error", "content": "original error"})

    events = [parse_sse_frame(frame) async for frame in usage.with_session_token_totals(
        stream(), UsageIdentity("alice-id", "chat"),
    )]
    assert events[:2] == [{"type": "token_usage", "input_tokens": 300, "output_tokens": 30,
                          "context_tokens": 200, "reasoning_tokens": 4}] * 2
    assert events[-1] == {"type": "error", "content": "original error"}


async def test_stream_uses_frozen_identity_after_rename_and_username_reuse(db, monkeypatch):
    users = UserRepository(db)
    user = users.register_user("alice", "secret123")
    identity = await usage.resolve_usage_identity("alice", "chat")
    repo = TokenUsageRepository(db)
    monkeypatch.setattr(usage, "get_token_usage_repository", lambda: repo)
    add(repo, user.user_id, "previous", 100, 10)
    lookups = []

    def unexpected_lookup():
        lookups.append(True)
        raise AssertionError("stream must reuse its resolved identity")

    monkeypatch.setattr(usage, "get_user_repository", unexpected_lookup)

    async def stream():
        yield format_sse({"type": "token_usage", "input_tokens": 1, "output_tokens": 1})
        user.username = "alice-new"
        users.update_user(user)
        replacement = users.register_user("alice", "secret123")
        add(repo, replacement.user_id, "other-user", 900, 90)
        add(repo, user.user_id, "new-call", 200, 20)
        yield format_sse({"type": "done", "usage": {"input_tokens": 1, "output_tokens": 1}})

    events = [parse_sse_frame(frame) async for frame in usage.with_session_token_totals(stream(), identity)]
    assert events[0] == {"type": "token_usage", "input_tokens": 100, "output_tokens": 10}
    assert events[1]["usage"] == {"input_tokens": 300, "output_tokens": 30}
    assert lookups == []
    # Detail/history resolve the current username once, then use the same ID query.
    monkeypatch.setattr(usage, "get_user_repository", lambda: users)
    assert await usage.session_usage_totals(None, "alice-new", "chat") == {
        "input_tokens": 300, "output_tokens": 30,
    }


async def test_missing_stream_identity_never_inherits_parent_or_queries_ledger(monkeypatch):
    queries = []

    def unexpected_query():
        queries.append(True)
        raise AssertionError("missing identity must not query the ledger")

    monkeypatch.setattr(usage, "get_token_usage_repository", unexpected_query)

    async def stream():
        yield format_sse({"type": "done", "usage": {"input_tokens": 900, "output_tokens": 90}})

    with usage_scope("unrelated-user", "chat"):
        events = [parse_sse_frame(frame) async for frame in usage.with_session_token_totals(stream(), None)]
    assert events[0]["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert queries == []
