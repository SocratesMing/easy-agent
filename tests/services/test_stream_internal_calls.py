"""DA-01：中间件内部模型调用（摘要等）的 token 不能混进回复正文。

deepagents 的 SummarizationMiddleware 会在 agent 运行中另起一次模型调用，旧
``astream(stream_mode=["messages", "updates"])`` 会把这些 token 和回复一起推出来，
只能靠 ``metadata["lc_source"] == "summarization"``（或 ``lc_internal_call``）区分。
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from easy_agent.agent.model import is_internal_model_call
from easy_agent.services.stream_processor import StreamProcessor


def _chunk(content="", **kwargs):
    return AIMessageChunk(content=content, **kwargs)


def test_is_internal_model_call_recognises_langchain_markers():
    assert is_internal_model_call({"lc_source": "summarization"})
    assert is_internal_model_call({"lc_source": "tool_selection"})
    assert is_internal_model_call({"lc_internal_call": "token"})
    assert not is_internal_model_call({"langgraph_node": "model"})
    assert not is_internal_model_call({"lc_source": "pinned_skill"})
    assert not is_internal_model_call(None)


def test_summarization_tokens_are_not_emitted_or_accumulated():
    proc = StreamProcessor(sid="t")
    assert proc.handle("messages", (_chunk("SUMMARY of the chat"), {"lc_source": "summarization"})) == []
    assert proc.handle("messages", (_chunk("internal"), {"lc_internal_call": "x"})) == []
    assert proc.accumulated_response == ""
    assert proc.blocks == []

    events = proc.handle("messages", (_chunk("real answer"), {"langgraph_node": "model"}))
    assert [e["type"] for e in events] == ["content"]
    assert proc.accumulated_response == "real answer"


def test_summarizer_reasoning_does_not_open_a_thinking_card():
    proc = StreamProcessor(sid="t")
    chunk = _chunk("", additional_kwargs={"reasoning_content": "thinking about the summary"})
    assert proc.handle("messages", (chunk, {"lc_source": "summarization"})) == []
    assert proc.accumulated_thinking == ""
    assert not proc.is_in_thinking


class _FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_real_summarization_stream_keeps_reply_clean():
    """用真实 create_deep_agent + 摘要中间件跑一遍，锁住上游 metadata 形态。"""
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from deepagents.middleware.summarization import SummarizationMiddleware
    from langgraph.checkpoint.memory import MemorySaver

    def replies():
        # 第一次调用是摘要器，第二次才是回答
        yield AIMessage(content="SUMMARY-TEXT the user asked about apples")
        while True:
            yield AIMessage(content="FINAL-ANSWER")

    model = _FakeModel(messages=replies())
    backend = StateBackend()
    agent = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[
            SummarizationMiddleware(
                model, backend=backend, trigger=("messages", 4), keep=("messages", 2)
            )
        ],
        checkpointer=MemorySaver(),
    )
    history = []
    for i in range(4):
        history += [HumanMessage(content=f"q{i}"), AIMessage(content=f"a{i}")]
    history.append(HumanMessage(content="now?"))

    proc = StreamProcessor(sid="t")
    saw_summary_chunk = False
    async for mode, data in agent.astream(
        {"messages": history},
        stream_mode=["messages", "updates"],
        config={"configurable": {"thread_id": "t1"}},
    ):
        if mode == "messages" and is_internal_model_call(data[1]):
            saw_summary_chunk = True
        for event in proc.handle(mode, data):
            assert "SUMMARY" not in str(event.get("content", ""))

    assert saw_summary_chunk, "摘要应已触发（否则本用例没有覆盖到 DA-01）"
    assert proc.accumulated_response == "FINAL-ANSWER"
