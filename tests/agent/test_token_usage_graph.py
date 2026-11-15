"""Real DeepAgents tool/subagent/HITL execution against a local HTTP transport."""

import asyncio
import json

import httpx
import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from easy_agent.agent import model as model_module
from easy_agent.agent import token_usage
from easy_agent.bootstrap.config import Config
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.services.streaming.processor import StreamProcessor
from easy_agent.services import usage as usage_service
from easy_agent.services.streaming.sse import format_sse, parse_sse_frame
from easy_agent.utils.usage_context import get_usage_identity, usage_scope


@tool
def measured_echo(text: str) -> str:
    """Return the supplied text without performing IO."""
    return text


def call(name, arguments, identifier):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def records(db):
    with db.get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute("SELECT * FROM token_usage ORDER BY completed_at_ms, event_id")
        ]


@pytest.fixture
async def graph_model(db, monkeypatch):
    """Use the production model factory/callback; replace only HTTP transport."""
    repo = TokenUsageRepository(db)
    monkeypatch.setattr(token_usage, "get_token_usage_repository", lambda: repo)
    clients = []

    def build(script):
        replies = iter(script)
        requests = []

        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            message = next(replies)
            index = len(requests)
            usage = {"prompt_tokens": index * 100, "completion_tokens": index * 10}
            finish = "tool_calls" if message.get("tool_calls") else "stop"
            common = {"id": f"completion-{index}", "created": 1, "model": "provider-alias"}
            if payload.get("stream"):
                assert payload["stream_options"]["include_usage"] is True
                delta = dict(message)
                if delta.get("tool_calls"):
                    delta["tool_calls"] = [
                        {**item, "index": i} for i, item in enumerate(delta["tool_calls"])
                    ]
                chunks = [
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": delta}],
                    },
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    },
                    {**common, "object": "chat.completion.chunk", "choices": [], "usage": usage},
                ]
                body = (
                    "".join(f"data: {json.dumps(item)}\n\n" for item in chunks) + "data: [DONE]\n\n"
                )
                return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
            return httpx.Response(
                200,
                json={
                    **common,
                    "object": "chat.completion",
                    "usage": usage,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                },
            )

        transport = httpx.MockTransport(handle)
        sync_client = httpx.Client(transport=transport)
        async_client = httpx.AsyncClient(transport=transport)
        clients.append((sync_client, async_client))

        def provider(llm_config, **kwargs):
            return model_module.ReasoningChatOpenAI(
                model=llm_config.model,
                api_key="fake",
                base_url="https://fake.invalid/v1",
                http_client=sync_client,
                http_async_client=async_client,
                stream_usage=True,
                max_retries=0,
            )

        monkeypatch.setattr(model_module, "_create_openai_compatible", provider)
        cfg = Config.load()
        llm = model_module.create_model(cfg)
        graph = create_deep_agent(
            model=llm,
            tools=[measured_echo],
            backend=StateBackend(),
            checkpointer=InMemorySaver(),
            interrupt_on={"measured_echo": True},
        )
        return graph, requests, llm, cfg

    yield build
    for sync_client, async_client in clients:
        sync_client.close()
        await async_client.aclose()


async def test_real_tool_loop_hitl_resume_and_checkpoint_read_are_counted_once(db, graph_model):
    graph, requests, llm, cfg = graph_model(
        [
            call("measured_echo", {"text": "approved"}, "echo-1"),
            {"role": "assistant", "content": "finished"},
        ]
    )
    config = {"configurable": {"thread_id": "hitl-session"}}
    with usage_scope("alice-id", "hitl-session"):
        first = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "Use the tool"}]}, config
        )
    assert first.get("__interrupt__")
    assert len(requests) == len(records(db)) == 1
    assert not llm.callbacks[0]._pending
    assert get_usage_identity() is None

    # A new execution scope is required after the original request has returned.
    with usage_scope("alice-id", "hitl-session"):
        final = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert final["messages"][-1].content == "finished"
    assert len(requests) == len(records(db)) == 2
    snapshot = await graph.aget_state(config)
    assert len(requests) == len(records(db)) == 2
    assert all(
        token_usage.USAGE_KEY not in msg.response_metadata for msg in snapshot.values["messages"]
    )
    ledger = records(db)
    assert {(row["user_id"], row["session_id"]) for row in ledger} == {("alice-id", "hitl-session")}
    assert {(row["model_key"], row["model_name"]) for row in ledger} == {
        (cfg.active_model, cfg.llm.model)
    }
    assert sorted((row["input_tokens"], row["output_tokens"]) for row in ledger) == [
        (100, 10),
        (200, 20),
    ]
    assert len({row["event_id"] for row in ledger}) == 2


async def test_real_subagent_and_parent_calls_each_reach_same_ledger(db, graph_model):
    graph, requests, llm, _ = graph_model(
        [
            call(
                "task",
                {"description": "Return the word child", "subagent_type": "general-purpose"},
                "task-1",
            ),
            {"role": "assistant", "content": "child"},
            {"role": "assistant", "content": "parent finished"},
        ]
    )
    with usage_scope("bob-id", "subagent-session"):
        final = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "Delegate once"}]},
            {"configurable": {"thread_id": "subagent-session"}},
        )
    assert final["messages"][-1].content == "parent finished"
    assert any(msg.type == "tool" and msg.tool_call_id == "task-1" for msg in final["messages"])
    assert len(requests) == len(records(db)) == 3
    assert {(row["user_id"], row["session_id"]) for row in records(db)} == {
        ("bob-id", "subagent-session")
    }
    assert sum(row["input_tokens"] + row["output_tokens"] for row in records(db)) == 660
    assert not llm.callbacks[0]._pending


async def test_real_graph_stream_records_usage_but_checkpoint_has_no_internal_metadata(
    db, graph_model
):
    graph, requests, llm, _ = graph_model([{"role": "assistant", "content": "streamed answer"}])
    config = {"configurable": {"thread_id": "stream-session"}}
    processor = StreamProcessor(sid="stream-session", session_id="stream-session")
    emitted = processor.start()
    messages = []
    with usage_scope("alice-id", "stream-session"):
        async for mode, item in graph.astream(
            {"messages": [{"role": "user", "content": "Answer"}]},
            config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                messages.append(item)
            emitted.extend(processor.handle(mode, item))
    emitted.extend(processor.finalize(session_id="stream-session", elapsed_time=1))
    assert any(getattr(item[0], "content", None) == "streamed answer" for item in messages)
    assert len(requests) == len(records(db)) == 1
    assert records(db)[0]["input_tokens"] == 100
    assert any(
        event["type"] == "content" and event["content"] == "streamed answer" for event in emitted
    )
    assert any(
        event["type"] == "token_usage"
        and event["input_tokens"] == 100
        and event["output_tokens"] == 10
        for event in emitted
    )
    assert token_usage.USAGE_KEY not in json.dumps(emitted)
    snapshot = await graph.aget_state(config)
    assert all(
        token_usage.USAGE_KEY not in msg.response_metadata for msg in snapshot.values["messages"]
    )
    assert not llm.callbacks[0]._pending


async def test_stream_latest_call_usage_and_personal_ledger_have_distinct_scopes(db, graph_model):
    """同一轮两次调用：聊天展示最后一次快照，个人账本保留并累计两次消耗。"""
    _, requests, llm, _ = graph_model([
        call("measured_echo", {"text": "echo"}, "echo-1"),
        {"role": "assistant", "content": "finished"},
    ])
    graph = create_deep_agent(model=llm, tools=[measured_echo], backend=StateBackend())
    processor = StreamProcessor(sid="two-calls", session_id="two-calls")
    emitted = processor.start()
    with usage_scope("alice-id", "two-calls"):
        async for mode, item in graph.astream(
            {"messages": [{"role": "user", "content": "Echo then answer"}]},
            stream_mode=["messages", "updates"],
        ):
            emitted.extend(processor.handle(mode, item))
    emitted.extend(processor.finalize(session_id="two-calls", elapsed_time=1))

    assert len(requests) == len(records(db)) == 2
    assert sum(row["input_tokens"] + row["output_tokens"] for row in records(db)) == 330
    snapshots = [event for event in emitted if event["type"] == "token_usage"]
    assert [(event["input_tokens"], event["output_tokens"]) for event in snapshots] == [
        (100, 10), (200, 20),
    ]
    done = next(event for event in emitted if event["type"] == "done")
    assert done["usage"]["input_tokens"] == 200
    assert done["usage"]["output_tokens"] == 20
    assert done["usage"]["context_tokens"] == 200


async def test_public_chat_counts_all_calls_and_turns_while_context_stays_current(db, graph_model, monkeypatch):
    user = db.register_user("graph-user", "secret123")
    repo = TokenUsageRepository(db)
    monkeypatch.setattr(usage_service, "get_token_usage_repository", lambda: repo)
    _, requests, llm, _ = graph_model([
        call("measured_echo", {"text": "echo"}, "echo-1"),
        {"role": "assistant", "content": "first answer"},
        {"role": "assistant", "content": "second answer"},
    ])
    graph = create_deep_agent(model=llm, tools=[measured_echo], backend=StateBackend())

    async def turn():
        processor = StreamProcessor(sid="chat", session_id="chat")
        with usage_scope(user.user_id, "chat"):
            async for mode, item in graph.astream(
                {"messages": [{"role": "user", "content": "question"}]},
                stream_mode=["messages", "updates"],
            ):
                for event in processor.handle(mode, item):
                    yield format_sse(event)
        for event in processor.finalize(session_id="chat", elapsed_time=1):
            yield format_sse(event)

    first = [parse_sse_frame(frame) async for frame in usage_service.with_session_token_totals(
        turn(), "graph-user", "chat",
    )]
    assert [(event["input_tokens"], event["output_tokens"]) for event in first
            if event["type"] == "token_usage"] == [(100, 10), (300, 30)]
    done = next(event for event in first if event["type"] == "done")
    assert (done["usage"]["input_tokens"], done["usage"]["output_tokens"]) == (300, 30)
    assert done["usage"]["context_tokens"] == 200

    second = [parse_sse_frame(frame) async for frame in usage_service.with_session_token_totals(
        turn(), "graph-user", "chat",
    )]
    done = next(event for event in second if event["type"] == "done")
    assert len(requests) == 3
    assert (done["usage"]["input_tokens"], done["usage"]["output_tokens"]) == (600, 60)
    assert done["usage"]["context_tokens"] == 300


@pytest.mark.parametrize("after_usage", [False, True])
async def test_real_stream_close_only_records_when_complete_final_evidence_exists(
    db, graph_model, after_usage
):
    _, requests, llm, _ = graph_model([{"role": "assistant", "content": "cancelled answer"}])
    with usage_scope("alice-id", "cancelled-session"):
        stream = llm.astream("Question")
        if after_usage:
            async for chunk in stream:
                if chunk.usage_metadata:
                    break
        else:
            await anext(stream)
        await stream.aclose()
    assert len(requests) == 1
    assert len(records(db)) == (1 if after_usage else 0)
    if after_usage:
        assert records(db)[0]["input_tokens"] == 100
    assert not llm.callbacks[0]._pending


async def test_shared_graph_concurrent_users_keep_separate_identity_snapshots(db, graph_model):
    graph, requests, llm, _ = graph_model(
        [
            {"role": "assistant", "content": "answer one"},
            {"role": "assistant", "content": "answer two"},
        ]
    )

    async def invoke(user):
        with usage_scope(user, user + "-session"):
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": user}]},
                {"configurable": {"thread_id": user + "-session"}},
            )

    await asyncio.gather(invoke("alice-id"), invoke("bob-id"))
    assert len(requests) == len(records(db)) == 2
    assert {(row["user_id"], row["session_id"]) for row in records(db)} == {
        ("alice-id", "alice-id-session"),
        ("bob-id", "bob-id-session"),
    }
    assert not llm.callbacks[0]._pending
    assert get_usage_identity() is None
