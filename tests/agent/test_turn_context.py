"""DA-03 P0：本轮上下文只进模型请求（turn_context），不写进 thread 的 messages。

改造前本轮注入（工作区前缀、知识库 / 页面上下文、上传预览、技能指令）直接拼进本轮
HumanMessage；会话级 thread 下它们会永久留在历史里。渲染放在主代理的摘要中间件里，
摘要才会按模型实际收到的内容计 token。
"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from easy_agent.agent.turn_context import (
    TurnAwareSummarizationMiddleware,
    TurnContextSummarizationState,
    render_turn_messages,
    render_turn_request,
    turn_input,
)
from easy_agent.services.stream_messages import turn_message_text

RENDERED = "[KNOWLEDGE CONTEXT]\nquestion"


class Recorder(BaseChatModel):
    """记录每次调用；摘要调用（lc_source=summarization）回 SUMMARY，含 WRITE 的本轮发起 write_file。"""

    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        metadata = getattr(run_manager, "metadata", None) or {}
        if metadata.get("lc_source") == "summarization":
            self.calls.append(("summary", messages))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="SUMMARY"))])
        self.calls.append(("agent", messages))
        last = messages[-1]
        if last.type == "human" and "WRITE" in str(last.content):
            reply = AIMessage(
                content="",
                tool_calls=[{"name": "write_file", "args": {"file_path": "/x.txt", "content": "x"}, "id": "w1"}],
            )
        else:
            reply = AIMessage(content="answer")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def of(self, kind: str) -> list:
        return [messages for k, messages in self.calls if k == kind]


class _InnerRenderer(AgentMiddleware):
    """对照组：单独的中间件做同样的渲染（deepagents 会把它排在摘要之后）。"""

    state_schema = TurnContextSummarizationState

    def wrap_model_call(self, request, handler):
        return handler(render_turn_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(render_turn_request(request))


def _summarizer(model, backend, *, turn_aware=True, **settings):
    cls = TurnAwareSummarizationMiddleware if turn_aware else SummarizationMiddleware
    return cls(model, backend=backend, **{"trigger": ("tokens", 100_000), "keep": ("messages", 2), **settings})


def _agent(tmp_path, model, *, turn_aware=True, extra=(), summarization=None, **kwargs):
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    return create_deep_agent(
        model=model,
        backend=backend,
        middleware=[_summarizer(model, backend, turn_aware=turn_aware, **(summarization or {})), *extra],
        checkpointer=InMemorySaver(),
        **kwargs,
    )


CFG = {"configurable": {"thread_id": "s"}}


# ── 纯函数 ────────────────────────────────────────────────────────────


def test_render_replaces_only_the_turn_message_and_strips_earlier_reasoning():
    earlier = AIMessage(
        content=[{"type": "thinking", "thinking": "old", "signature": "sig"}, {"type": "text", "text": "a0"}],
        additional_kwargs={"reasoning_content": "old", "keep": 1},
    )
    current_step = AIMessage(content=[{"type": "thinking", "thinking": "now", "signature": "s2"}])
    messages = [HumanMessage("q0", id="t0"), earlier, HumanMessage("q1", id="t1"), current_step]

    rendered = render_turn_messages(messages, {"message_id": "t1", "content": RENDERED})

    assert rendered[0] is messages[0]
    assert rendered[1].content == [{"type": "text", "text": "a0"}]
    assert rendered[1].additional_kwargs == {"keep": 1}
    assert (rendered[2].content, rendered[2].id) == (RENDERED, "t1")
    assert rendered[3] is current_step  # 本轮工具循环里的推理保持原样
    assert messages[2].content == "q1"  # 不改原消息


def test_render_is_a_no_op_without_turn_context_or_target():
    messages = [HumanMessage("q", id="t1")]
    assert render_turn_messages(messages, None) is messages
    assert render_turn_messages(messages, {"message_id": "gone", "content": RENDERED}) is messages


def test_turn_input_and_placeholder_text():
    assert turn_input(["m"], message_id="t1", rendered="r") == {
        "messages": ["m"],
        "turn_context": {"message_id": "t1", "content": "r"},
        "skills_metadata": None,
    }
    assert turn_message_text("hi", [{"filename": "a.pdf"}]) == "hi"
    assert turn_message_text("", [{"filename": "a.pdf"}], ["docx"]) == "[上传文件: a.pdf] [使用技能: docx]"


# ── 真实 create_deep_agent ───────────────────────────────────────────


async def test_model_sees_rendered_turn_but_thread_stores_the_raw_message(tmp_path):
    model = Recorder()
    agent = _agent(tmp_path, model)

    await agent.ainvoke(turn_input([HumanMessage("question", id="t1")], message_id="t1", rendered=RENDERED), CFG)
    assert model.of("agent")[0][-1].content == RENDERED
    stored = [m for m in (await agent.aget_state(CFG)).values["messages"] if m.id == "t1"]
    assert stored[0].content == "question"

    await agent.ainvoke(turn_input([HumanMessage("next", id="t2")], message_id="t2", rendered="next"), CFG)
    assert [m.content for m in model.of("agent")[1] if m.type == "human"] == ["question", "next"]


async def test_resumed_run_still_renders_the_turn(tmp_path):
    model = Recorder()
    agent = _agent(tmp_path, model, interrupt_on={"write_file": True})

    first = await agent.ainvoke(
        turn_input([HumanMessage("WRITE", id="t1")], message_id="t1", rendered="[CTX]\nWRITE"), CFG
    )
    assert first.get("__interrupt__")
    await agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), CFG)

    resumed = model.of("agent")[-1]
    assert isinstance(resumed[-1], ToolMessage)
    assert [m.content for m in resumed if m.id == "t1"] == ["[CTX]\nWRITE"]
    assert (tmp_path / "x.txt").exists()


async def test_summarization_counts_the_rendered_turn(tmp_path):
    """本轮渲染出大段内容（如回退模式的文件全文）时，摘要要按渲染后的内容判断是否触发。"""
    history = [HumanMessage("q0"), AIMessage("a0"), HumanMessage("q1"), AIMessage("a1")]
    payload = turn_input([*history, HumanMessage("q2", id="t")], message_id="t", rendered="x" * 40_000)

    low_trigger = {"trigger": ("tokens", 5_000)}
    turn_aware = Recorder()
    agent = _agent(tmp_path / "a", turn_aware, summarization=low_trigger)
    await agent.ainvoke(payload, CFG)
    assert len(turn_aware.of("summary")) == 1

    # 对照：单独的渲染中间件排在摘要之后，摘要按原话计数，不触发，模型却收到 4 万字符
    inner = Recorder()
    agent = _agent(
        tmp_path / "b", inner, turn_aware=False, extra=[_InnerRenderer()], summarization=low_trigger
    )
    await agent.ainvoke(payload, CFG)
    assert inner.of("summary") == []
    assert len(inner.of("agent")[0][-1].content) == 40_000
