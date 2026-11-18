"""压缩不得在摘要前裁掉用户目标，也不得将失败占位句提交成摘要。"""

import asyncio
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from easy_agent.agent.manual_compaction import prepare_compaction, terminal_update_node
from easy_agent.agent.summarization import build_summarization_middleware
from easy_agent.agent.token_counting import count_context_tokens
from easy_agent.agent.turn_context import turn_input


SENTINEL = "Previous conversation was too long to summarize."
GOAL = "ACTIVE_USER_GOAL: 查询2026年10月金市营收，按区域分组"
CFG = {"configurable": {"thread_id": "context-preservation"}}


class Recorder(BaseChatModel):
    """故意不在摘要输出里复述目标，验证原问题保护不依赖模型照做。"""

    calls: list = Field(default_factory=list)
    summary_text: str = "SUMMARY: completed exploratory reads; analysis remains pending"
    summary_overflows: bool = False

    @property
    def _llm_type(self):
        return "context-preservation-recorder"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        metadata = getattr(run_manager, "metadata", None) or {}
        summary = metadata.get("lc_source") == "summarization"
        self.calls.append(("summary" if summary else "main", messages))
        if summary and self.summary_overflows:
            self.summary_overflows = False
            raise ContextOverflowError("test summary provider context overflow")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=self.summary_text if summary else "done",
        ))])

    def sent(self, kind):
        return [messages for label, messages in self.calls if label == kind]


def _agent(tmp_path, *, summary_text=None, render_turn=True):
    model = Recorder() if summary_text is None else Recorder(summary_text=summary_text)
    llm = SimpleNamespace(
        context_length=10000, retry=None, api_key="test", api_base=None,
        model="test", provider="openai", protocol="openai", headers={},
    )
    config = SimpleNamespace(
        llm=llm, models={"test": llm},
        summarization=SimpleNamespace(
            enabled=True, compression_threshold=.8, compression_target=.1,
        ),
    )
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    middleware = build_summarization_middleware(
        config, model, backend, model_key="test", render_turn=render_turn,
    )
    graph = create_deep_agent(
        model=model, backend=backend, checkpointer=InMemorySaver(), middleware=[middleware],
    )
    return SimpleNamespace(
        agent=graph, model=model, model_name="test", config=config,
        system_prompt="", _build_backend=lambda _: backend,
    )


def _tool_chain(offset=0):
    return [message for index in range(5) for message in (
        AIMessage("", tool_calls=[{
            "name": "read_file", "args": {"file_path": f"/result-{index + offset}"},
            "id": f"call-{index + offset}",
        }]),
        ToolMessage("工具返回大段正文" * 1200, tool_call_id=f"call-{index + offset}"),
    )]


def _text(messages):
    return "\n".join(str(message.content) for message in messages)


async def test_automatic_long_tool_chain_keeps_active_question_without_summary_echo(tmp_path):
    agent = _agent(tmp_path)
    messages = [HumanMessage(GOAL, id="active"), *_tool_chain()]

    await agent.agent.ainvoke(
        turn_input(messages, message_id="active", rendered=GOAL), CFG,
    )

    state = (await agent.agent.aget_state(CFG)).values
    event = state["_summarization_event"]
    assert SENTINEL not in event["summary_message"].content
    assert agent.model.sent("summary")
    assert GOAL in _text(agent.model.sent("main")[-1])
    assert GOAL in _text(state["messages"])
    assert len([m for m in state["messages"] if isinstance(m, ToolMessage)]) == 5
    assert all(
        any(
            call["id"] == message.tool_call_id
            for candidate in state["messages"] if isinstance(candidate, AIMessage)
            for call in candidate.tool_calls
        )
        for message in state["messages"] if isinstance(message, ToolMessage)
    )
    assert any(GOAL in path.read_text() for path in tmp_path.rglob("*.md"))


async def test_manual_nonempty_tail_trim_includes_original_question(tmp_path):
    agent = _agent(tmp_path)
    messages = [
        HumanMessage(GOAL), AIMessage("旧数据" * 3000),
        HumanMessage("继续，并保留之前的要求"), AIMessage("近期数据" * 500),
        HumanMessage("latest question"),
    ]

    result = await prepare_compaction(agent, messages, {})

    assert result is not None
    summary_requests = agent.model.sent("summary")
    assert summary_requests
    assert GOAL in _text(summary_requests[-1])
    assert "继续，并保留之前的要求" in _text(summary_requests[-1])
    assert count_context_tokens(summary_requests[-1]) < 6000


async def test_manual_long_history_includes_current_question_from_middle(tmp_path):
    agent = _agent(tmp_path)
    messages = [
        HumanMessage("OLD_UNRELATED_GOAL"), AIMessage("旧数据" * 3000),
        HumanMessage(GOAL), *_tool_chain(), HumanMessage("latest question"),
    ]

    result = await prepare_compaction(agent, messages, {})

    assert result is not None
    summary_requests = agent.model.sent("summary")
    assert summary_requests
    assert GOAL in _text(summary_requests[-1])
    assert count_context_tokens(summary_requests[-1]) < 6000


@pytest.mark.parametrize("summary_text", ["", SENTINEL])
async def test_automatic_invalid_summary_does_not_commit_or_resume_main(tmp_path, summary_text):
    agent = _agent(tmp_path, summary_text=summary_text)
    messages = [
        HumanMessage(GOAL, id="active"), AIMessage("旧数据" * 3000),
        HumanMessage("继续，并保留之前的要求"), AIMessage("近期数据" * 500),
        HumanMessage("latest question"),
    ]

    try:
        await agent.agent.ainvoke(
            turn_input(messages, message_id="active", rendered=GOAL), CFG,
        )
    except RuntimeError:
        pass

    state = (await agent.agent.aget_state(CFG)).values
    assert not state.get("_summarization_event")
    assert not agent.model.sent("main")
    assert GOAL in _text(state["messages"])


async def test_automatic_archive_failure_does_not_commit_or_resume_main(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    messages = [
        HumanMessage(GOAL, id="active"), AIMessage("旧数据" * 3000),
        HumanMessage("继续，并保留之前的要求"), AIMessage("近期数据" * 500),
        HumanMessage("latest question"),
    ]

    async def failed_archive(*args, **kwargs):
        return None

    monkeypatch.setattr(SummarizationMiddleware, "_aoffload_to_backend", failed_archive)
    try:
        await agent.agent.ainvoke(
            turn_input(messages, message_id="active", rendered=GOAL), CFG,
        )
    except RuntimeError:
        pass

    state = (await agent.agent.aget_state(CFG)).values
    assert not state.get("_summarization_event")
    assert not agent.model.sent("main")
    assert GOAL in _text(state["messages"])


async def test_automatic_repairs_existing_sentinel_from_raw_checkpoint(tmp_path):
    agent = _agent(tmp_path)
    messages = [HumanMessage(GOAL, id="active"), *_tool_chain()]
    bad_event = {
        "cutoff_index": 9,
        "summary_message": HumanMessage(f"Here is a summary:\n{SENTINEL}"),
        "file_path": "/conversation_history/old.md",
    }
    await agent.agent.aupdate_state(
        CFG, {
            "messages": messages, "_summarization_event": bad_event,
            "turn_context": {"message_id": "active", "content": GOAL},
        }, as_node=terminal_update_node(agent.agent),
    )

    await agent.agent.ainvoke({"messages": []}, CFG)

    state = (await agent.agent.aget_state(CFG)).values
    assert SENTINEL not in state["_summarization_event"]["summary_message"].content
    assert GOAL in _text(agent.model.sent("main")[-1])
    assert GOAL in _text(state["messages"])


async def test_manual_repairs_existing_sentinel_using_hidden_raw_question(tmp_path):
    agent = _agent(tmp_path)
    messages = [HumanMessage(GOAL, id="active"), *_tool_chain(), HumanMessage("latest question")]
    bad_event = {
        "cutoff_index": 9,
        "summary_message": HumanMessage(f"Here is a summary:\n{SENTINEL}"),
        "file_path": "/conversation_history/old.md",
    }

    result = await prepare_compaction(agent, messages, {"_summarization_event": bad_event})

    assert result is not None
    assert GOAL in _text(agent.model.sent("summary")[-1])
    assert SENTINEL not in result["event"]["summary_message"].content


def test_sync_automatic_compression_keeps_current_question(tmp_path):
    agent = _agent(tmp_path)
    messages = [HumanMessage(GOAL, id="active"), *_tool_chain()]

    agent.agent.invoke(turn_input(messages, message_id="active", rendered=GOAL), CFG)

    state = agent.agent.get_state(CFG).values
    assert SENTINEL not in state["_summarization_event"]["summary_message"].content
    assert GOAL in _text(agent.model.sent("main")[-1])
    assert agent.model.sent("summary")
    assert GOAL in _text(state["messages"])


async def test_repeated_automatic_compression_maps_cutoff_and_preserves_raw_question(tmp_path):
    agent = _agent(tmp_path)
    dynamic = "DYNAMIC_KNOWLEDGE_REFERENCE: 当前页面与工作区内容"
    await agent.agent.ainvoke(turn_input(
        [HumanMessage(GOAL, id="active"), *_tool_chain()],
        message_id="active", rendered=dynamic + "\n" + GOAL,
    ), CFG)
    first = (await agent.agent.aget_state(CFG)).values
    first_cutoff = first["_summarization_event"]["cutoff_index"]
    assert dynamic in _text(agent.model.sent("summary")[0])

    await agent.agent.ainvoke({"messages": _tool_chain(offset=5)}, CFG)

    state = (await agent.agent.aget_state(CFG)).values
    event = state["_summarization_event"]
    assert len(agent.model.sent("summary")) == 2
    assert first_cutoff < event["cutoff_index"] <= len(state["messages"])
    assert all(GOAL in _text(sent) for sent in agent.model.sent("main"))
    raw = [m for m in state["messages"] if m.id == "active"]
    assert len(raw) == 1 and raw[0].content == GOAL
    assert dynamic not in _text(state["messages"])
    # 工具调用/返回在实际模型请求中必须仍然成对，绝对 cutoff 不能切断二者。
    sent = agent.model.sent("main")[-1]
    call_ids = {
        call["id"] for m in sent if isinstance(m, AIMessage) for call in m.tool_calls
    }
    result_ids = {m.tool_call_id for m in sent if isinstance(m, ToolMessage)}
    assert call_ids == result_ids and call_ids
    assert len([m for m in state["messages"] if isinstance(m, ToolMessage)]) == 10


async def test_bad_event_is_cleared_even_when_recovered_history_is_below_threshold(tmp_path):
    agent = _agent(tmp_path)
    bad = {
        "cutoff_index": 1, "file_path": None,
        "summary_message": HumanMessage(f"Here is a summary of the conversation to date:\n\n{SENTINEL}"),
    }
    await agent.agent.aupdate_state(CFG, {
        "messages": [HumanMessage(GOAL, id="active"), AIMessage("short result")],
        "_summarization_event": bad,
        "turn_context": {"message_id": "active", "content": GOAL},
    }, as_node=terminal_update_node(agent.agent))

    await agent.agent.ainvoke({"messages": []}, CFG)

    state = (await agent.agent.aget_state(CFG)).values
    assert state.get("_summarization_event") is None
    assert GOAL in _text(agent.model.sent("main")[-1])
    assert not agent.model.sent("summary")


async def test_required_user_content_exceeding_summary_budget_fails_without_checkpoint_event(tmp_path):
    agent = _agent(tmp_path)
    long_goal = GOAL + "用户不可省略的约束" * 1000
    messages = [HumanMessage(long_goal, id="active"), *_tool_chain()]

    with pytest.raises(RuntimeError, match="用户问题.*预算"):
        await agent.agent.ainvoke(turn_input(
            messages, message_id="active", rendered=long_goal,
        ), CFG)

    state = (await agent.agent.aget_state(CFG)).values
    assert not state.get("_summarization_event")
    assert next(m for m in state["messages"] if m.id == "active").content == long_goal
    assert not agent.model.sent("main")
    assert not agent.model.sent("summary")


async def test_media_upload_failure_preserves_original_media_and_does_not_commit(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    backend = agent._build_backend([])
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}}
    messages = [HumanMessage([
        {"type": "text", "text": GOAL}, image,
    ], id="active"), *_tool_chain()]

    async def fail_upload(files):
        raise OSError("test media storage failure")

    monkeypatch.setattr(backend, "aupload_files", fail_upload)
    with pytest.raises(RuntimeError, match="媒体归档失败"):
        # 不经 turn rendering，把原始多模态问题交给自动中间件。
        await agent.agent.ainvoke({"messages": messages}, CFG)

    state = (await agent.agent.aget_state(CFG)).values
    assert not state.get("_summarization_event")
    assert next(m for m in state["messages"] if m.id == "active").content == messages[0].content
    assert not agent.model.sent("main")
    assert not agent.model.sent("summary")


async def test_subagent_automatic_compression_without_turn_context_keeps_original_task(tmp_path):
    agent = _agent(tmp_path, render_turn=False)
    messages = [HumanMessage(GOAL, id="delegated-task"), *_tool_chain()]

    await agent.agent.ainvoke({"messages": messages}, CFG)

    state = (await agent.agent.aget_state(CFG)).values
    assert GOAL in _text(agent.model.sent("summary")[-1])
    assert GOAL in _text(agent.model.sent("main")[-1])
    assert SENTINEL not in state["_summarization_event"]["summary_message"].content
    assert GOAL in _text(state["messages"])


async def test_failed_second_summary_preserves_previous_good_event_and_raw_tools(tmp_path):
    agent = _agent(tmp_path)
    await agent.agent.ainvoke(turn_input(
        [HumanMessage(GOAL, id="active"), *_tool_chain()],
        message_id="active", rendered=GOAL,
    ), CFG)
    previous = (await agent.agent.aget_state(CFG)).values["_summarization_event"]
    agent.model.summary_text = ""

    with pytest.raises(RuntimeError, match="有效摘要"):
        await agent.agent.ainvoke({"messages": _tool_chain(offset=5)}, CFG)

    state = (await agent.agent.aget_state(CFG)).values
    assert state["_summarization_event"] == previous
    assert len(agent.model.sent("main")) == 1
    assert len([m for m in state["messages"] if isinstance(m, ToolMessage)]) == 10
    assert next(m for m in state["messages"] if m.id == "active").content == GOAL


async def test_shared_compiled_graph_does_not_mix_concurrent_thread_questions(tmp_path):
    agent = _agent(tmp_path)
    goals = ["THREAD_ONE_TASK: 查询金市营收", "THREAD_TWO_TASK: 查询债券持仓"]
    configs = [{"configurable": {"thread_id": f"parallel-{i}"}} for i in range(2)]

    await asyncio.gather(*[
        agent.agent.ainvoke(turn_input(
            [HumanMessage(goal, id=f"active-{index}"), *_tool_chain(offset=index * 10)],
            message_id=f"active-{index}", rendered=goal,
        ), configs[index])
        for index, goal in enumerate(goals)
    ])

    main_requests = agent.model.sent("main")
    assert len(main_requests) == 2
    for goal in goals:
        matching = [sent for sent in main_requests if goal in _text(sent)]
        assert len(matching) == 1
        assert next(other for other in goals if other != goal) not in _text(matching[0])
    for index, config in enumerate(configs):
        state = (await agent.agent.aget_state(config)).values
        assert goals[index] in _text(state["messages"])
        assert goals[1 - index] not in _text(state["messages"])


@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
async def test_summary_provider_overflow_does_not_retry_by_discarding_middle_user_goal(tmp_path, sync):
    agent = _agent(tmp_path)
    agent.model.summary_overflows = True
    # 必保的当前目标位于首个较长用户要求之后，框架旧首尾降级会把它剪掉。
    messages = [
        HumanMessage("EARLY_USER_REQUEST: " + "旧要求" * 500),
        HumanMessage(GOAL, id="active"), *_tool_chain(), HumanMessage("latest question"),
    ]
    payload = turn_input(messages, message_id="active", rendered=GOAL)

    with pytest.raises(ContextOverflowError):
        if sync:
            agent.agent.invoke(payload, CFG)
        else:
            await agent.agent.ainvoke(payload, CFG)

    state = (await agent.agent.aget_state(CFG)).values
    summary_requests = agent.model.sent("summary")
    assert len(summary_requests) == 1
    assert GOAL in _text(summary_requests[0])
    assert "middle of conversation omitted" not in _text(summary_requests[0])
    assert not agent.model.sent("main")
    assert not state.get("_summarization_event")
    assert next(m for m in state["messages"] if m.id == "active").content == GOAL
