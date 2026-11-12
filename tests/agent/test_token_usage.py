"""Real SDK + LangChain dispatcher contracts, without contacting a provider."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, LLMResult
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion

from easy_agent.agent import token_usage
from easy_agent.agent.model import ReasoningChatOpenAI, create_model
from easy_agent.agent.token_usage import TokenUsageCallback, USAGE_KEY
from easy_agent.bootstrap.config import Config
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.utils.usage_context import get_usage_identity, usage_scope


@pytest.fixture
def ledger(db, monkeypatch):
    repo = TokenUsageRepository(db)
    monkeypatch.setattr(token_usage, "get_token_usage_repository", lambda: repo)
    return db


def rows(db):
    with db.get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM token_usage ORDER BY event_id")]


def response(usage):
    return {"id": "provider-id", "object": "chat.completion", "created": 1, "model": "alias",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}], "usage": usage}


def stream_chunks(usage, *, duplicate=False, body_only=False):
    yield {"id": "chunk", "object": "chat.completion.chunk", "created": 1, "model": "alias",
           "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}}],
           **({"usage": usage} if body_only else {})}
    if not body_only:
        for _ in range(2 if duplicate else 1):
            yield {"id": "chunk", "object": "chat.completion.chunk", "created": 1,
                   "model": "alias", "choices": [], "usage": usage}


def model(usage, *, duplicate=False, body_only=False, callbacks=None):
    def handle(request):
        payload = json.loads(request.content)
        if payload.get("stream"):
            assert payload["stream_options"]["include_usage"] is True
            parts = stream_chunks(usage, duplicate=duplicate, body_only=body_only)
            content = "".join(f"data: {json.dumps(item)}\n\n" for item in parts) + "data: [DONE]\n\n"
            return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=response(usage))
    transport = httpx.MockTransport(handle)
    return ReasoningChatOpenAI(
        model="actual-request-name", api_key="fake", base_url="https://fake.invalid/v1",
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport), stream_usage=True,
        callbacks=callbacks or [TokenUsageCallback("configured-key", "actual-request-name")],
    )


@pytest.mark.parametrize("stream", [False, True])
async def test_real_sdk_dispatch_records_once(ledger, stream):
    llm = model({"prompt_tokens": 1000, "completion_tokens": 200,
                 "completion_tokens_details": {"reasoning_tokens": 100},
                 "prompt_tokens_details": {"cached_tokens": 500}}, duplicate=True)
    with usage_scope("alice-id", "session"):
        if stream:
            chunks = [chunk async for chunk in llm.astream("question")]
            assert "".join(chunk.content for chunk in chunks) == "ok"
        else:
            msg = await llm.ainvoke("question")
            assert USAGE_KEY not in msg.response_metadata
    records = rows(ledger)
    assert len(records) == 1
    record = records[0]
    assert (record["input_tokens"], record["output_tokens"]) == (1000, 200)
    assert (record["user_id"], record["session_id"]) == ("alice-id", "session")
    assert (record["model_key"], record["model_name"]) == ("configured-key", "actual-request-name")
    assert record["event_id"].startswith("lc:")
    assert not llm.callbacks[0]._pending


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 1}, {"completion_tokens": 2},
                                   {"prompt_tokens": None, "completion_tokens": 2},
                                   {"prompt_tokens": True, "completion_tokens": 2},
                                   {"prompt_tokens": 1, "completion_tokens": -1}])
async def test_raw_incomplete_usage_not_repaired_by_langchain(ledger, stream, usage):
    llm = model(usage)
    with usage_scope("alice-id"):
        if stream:
            _ = [chunk async for chunk in llm.astream("question")]
        else:
            await llm.ainvoke("question")
    assert rows(ledger) == []
    assert not llm.callbacks[0]._pending


async def test_nonfinal_body_usage_is_not_a_final_snapshot(ledger):
    with usage_scope("alice-id"):
        _ = [chunk async for chunk in model({"prompt_tokens": 100, "completion_tokens": 20},
                                          body_only=True).astream("question")]
    assert rows(ledger) == []


async def test_zero_is_real_and_unscoped_probe_is_not_billed(ledger):
    llm = model({"prompt_tokens": 0, "completion_tokens": 0})
    await llm.ainvoke("startup")
    with usage_scope("alice-id"):
        await llm.ainvoke("question")
        with usage_scope(None):
            await llm.ainvoke("maintenance")
    assert len(rows(ledger)) == 1
    assert rows(ledger)[0]["input_tokens"] == 0
    assert get_usage_identity() is None


async def test_shared_model_concurrent_users_and_successive_calls(ledger):
    llm = model({"prompt_tokens": 1000, "completion_tokens": 200})
    async def invoke(user):
        with usage_scope(user, user + "-session"):
            await llm.ainvoke("first")
            await llm.ainvoke("tool or child call")
    await asyncio.gather(invoke("alice"), invoke("bob"))
    records = rows(ledger)
    assert len(records) == 4
    assert sorted((r["user_id"], r["session_id"]) for r in records) == [
        ("alice", "alice-session"), ("alice", "alice-session"),
        ("bob", "bob-session"), ("bob", "bob-session")]


def test_callback_freezes_identity_and_cleans_error_and_duplicate(ledger):
    cb = TokenUsageCallback("key", "name")
    run = uuid4()
    with usage_scope("alice"):
        cb.on_chat_model_start({}, [[]], run_id=run)
    gen = ChatGeneration(message=AIMessage(content=""), generation_info={USAGE_KEY: [
        {"input_tokens": 1000, "output_tokens": 200, "completed_at_ms": 1793289599000}]})
    result = LLMResult(generations=[[gen]])
    with usage_scope("bob"):
        cb.on_llm_error(ValueError(), run_id=run, response=result)
        cb.on_llm_end(result, run_id=run)
    assert len(rows(ledger)) == 1
    assert rows(ledger)[0]["user_id"] == "alice"
    run = uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run)
    cb.on_llm_error(ValueError(), run_id=run)
    assert not cb._pending


def test_snapshot_merge_keeps_counts_and_completion_time(ledger):
    snapshot = {"input_tokens": 1000, "output_tokens": 200,
                "completed_at_ms": 1793375999000}  # Shanghai 2026-10-30 23:59:59
    chunk = ChatGenerationChunk(message=AIMessageChunk(content=""),
                                generation_info={USAGE_KEY: [snapshot]})
    combined = chunk + chunk
    assert combined.generation_info[USAGE_KEY] == [snapshot, snapshot]
    cb = TokenUsageCallback("key", "name")
    run = uuid4()
    with usage_scope("alice"):
        cb.on_chat_model_start({}, [[]], run_id=run)
    cb.on_llm_end(LLMResult(generations=[[combined]]), run_id=run)
    record = rows(ledger)[0]
    assert record["input_tokens"] == 1000
    assert record["completed_at_ms"] == snapshot["completed_at_ms"]
    assert record["usage_date"] == "2026-10-30"


def test_sdk_defaults_are_not_explicit_usage(ledger):
    usage = CompletionUsage.model_construct(prompt_tokens=10, completion_tokens=0,
                                            _fields_set={"prompt_tokens"})
    # openai.BaseModel.model_construct adds supplied keys to _fields_set.
    # Represent a defaulted field whose value exists but was not reported.
    usage.__pydantic_fields_set__ = {"prompt_tokens"}
    completion = ChatCompletion.model_construct(usage=usage)
    with usage_scope("alice"):
        event = token_usage.capture_sdk_usage(completion, model_key="sdk", model_name="name")
    assert event is None


async def test_sdk_and_langchain_share_ledger_without_double_counting(ledger):
    with usage_scope("alice"):
        event = token_usage.capture_sdk_usage(
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            model_key="sdk", model_name="sdk-name")
        token_usage.write_usage_event(event)
        token_usage.write_usage_event(event)
        await model({"prompt_tokens": 1000, "completion_tokens": 200}).ainvoke("q")
    records = rows(ledger)
    assert len(records) == 2
    assert sum(r["input_tokens"] + r["output_tokens"] for r in records) == 1350


def test_database_failure_does_not_break_business(monkeypatch, caplog):
    def fail():
        raise RuntimeError("sensitive error payload")
    monkeypatch.setattr(token_usage, "get_token_usage_repository", fail)
    with usage_scope("alice"):
        event = token_usage.capture_sdk_usage({"usage": {"prompt_tokens": 1, "completion_tokens": 2}},
                                              model_key="key", model_name="name")
    token_usage.write_usage_event(event)
    assert "database_error" in caplog.text
    assert "sensitive error payload" not in caplog.text


def test_factory_fallback_identity_and_preserves_callbacks(monkeypatch):
    cfg = Config.load()
    original = BaseCallbackHandler()
    fake = SimpleNamespace(callbacks=[original], profile={})
    import easy_agent.agent.model as module
    monkeypatch.setattr(module, "_create_openai_compatible", lambda *a, **k: fake)
    monkeypatch.setattr(module, "install_request_logging", lambda *a: None)
    result = create_model(cfg, model_name="nonexistent-key")
    result = create_model(cfg, model_name="nonexistent-key")
    assert result.callbacks[0] is original
    assert len(result.callbacks) == 2
    assert result.callbacks[1].model_key == cfg.active_model
    assert result.callbacks[1].model_name == cfg.llm.model
