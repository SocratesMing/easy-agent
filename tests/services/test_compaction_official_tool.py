"""通过官方 BaseTool.ainvoke 入口评估迁移；不直接调用官方私有工具实现。"""
import asyncio

import pytest
from deepagents.middleware.summarization import SummarizationMiddleware, SummarizationToolMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.agent.manual_compaction import prepare_compaction
from easy_agent.agent.summarization import summarization_settings
from easy_agent.agent.token_counting import count_context_tokens
from tests.services.test_manual_compaction import Model, agent


async def invoke_tool(summarization, messages, **state):
    tool = SummarizationToolMiddleware(summarization).tools[0]
    runtime = ToolRuntime(
        state={"messages": messages, **state}, context=None, config={},
        stream_writer=lambda chunk: None, tool_call_id="explicit-compact", store=None,
    )
    return await tool.ainvoke({"runtime": runtime})


@pytest.mark.parametrize("scenario", ["auto_disabled", "no_usage", "below_threshold"])
async def test_public_tool_gate_differs_from_explicit_manual_action(tmp_path, scenario):
    a = agent(None, tmp_path)
    messages = [HumanMessage("old facts " * 700), AIMessage("old answer"),
                HumanMessage("recent"), AIMessage("answer")]
    if scenario == "auto_disabled":
        settings = summarization_settings(a.config, "small")
    else:
        settings = {"trigger": ("tokens", 800), "keep": ("tokens", 100)}
    if scenario == "below_threshold":
        messages[-1].usage_metadata = {"input_tokens": 100, "output_tokens": 1, "total_tokens": 101}
    middleware = SummarizationMiddleware(a.model, backend=a._build_backend([]),
                                        token_counter=count_context_tokens, **settings)
    result = await invoke_tool(middleware, messages)
    assert "_summarization_event" not in result.update
    assert "Nothing to compact" in result.update["messages"][0].content
    assert not a.model.inputs
    assert await prepare_compaction(a, messages, {}) is not None
    assert len(a.model.inputs) == 1


@pytest.mark.parametrize("scenario", ["empty_summary", "sentinel", "archive_failure"])
async def test_public_tool_does_not_enforce_project_failure_contract(tmp_path, monkeypatch, scenario):
    a = agent(None, tmp_path)
    middleware = SummarizationMiddleware(a.model, backend=a._build_backend([]),
                                        trigger=("messages", 2), keep=("messages", 1))
    messages = [HumanMessage("facts " * 100), AIMessage("answer"), HumanMessage("latest")]
    if scenario == "archive_failure":
        async def archive(*args):
            return None
        monkeypatch.setattr(SummarizationMiddleware, "_aoffload_to_backend", archive)
    else:
        async def summary(*args):
            return "" if scenario == "empty_summary" else "Previous conversation was too long to summarize."
        monkeypatch.setattr(SummarizationMiddleware, "_acreate_summary", summary)
    result = await invoke_tool(middleware, messages)
    # 官方工具将这些情况报告为成功，不能直接替换项目的失败原子性检查。
    assert "_summarization_event" in result.update
    assert isinstance(result.update["messages"][0], ToolMessage)
    assert "Conversation compacted" in result.update["messages"][0].content
    with pytest.raises(RuntimeError, match="归档失败|有效摘要"):
        await prepare_compaction(a, messages, {})


async def test_public_tool_can_run_without_main_answer_but_reuses_archive(tmp_path):
    model = Model()
    a = agent(None, tmp_path, model)
    middleware = SummarizationMiddleware(model, backend=a._build_backend([]),
                                        trigger=("messages", 2), keep=("messages", 1))
    messages = [HumanMessage("old facts"), AIMessage("old answer"), HumanMessage("latest")]
    result = await invoke_tool(middleware, messages)
    assert len(model.inputs) == 1
    assert set(result.update) == {"messages", "_summarization_event", "_summarization_session_id"}
    path = result.update["_summarization_event"]["file_path"]
    original_archive = (tmp_path / path.lstrip("/")).read_text()
    messages.extend([AIMessage("latest answer"), HumanMessage("newer")])
    again = await invoke_tool(middleware, messages,
                              _summarization_event=result.update["_summarization_event"],
                              _summarization_session_id=result.update["_summarization_session_id"])
    assert len(model.inputs) == 2
    assert again.update["_summarization_event"]["cutoff_index"] > result.update["_summarization_event"]["cutoff_index"]
    assert again.update["_summarization_event"]["file_path"] == path
    assert (tmp_path / path.lstrip("/")).read_text() != original_archive
    assert len(await asyncio.to_thread(lambda: list(tmp_path.rglob("*.md")))) == 1
