"""DA-13 S1：输入框选中的技能经 ``pinned_skills`` 加载。

改造前在本轮模型输入前拼 ``/目录名`` 指令，模型要自己 ``read_file`` 读一次 ``SKILL.md``；
现在框架在第一次模型调用前就把技能全文作为一条消息加进对话。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from easy_agent.agent.turn_context import turn_input
from easy_agent.models.api import ChatRequest
from easy_agent.services.streaming import chat_stream_generator

SKILL_BODY = "STEP-BY-STEP DOCX INSTRUCTIONS"


class _Recorder(BaseChatModel):
    inputs: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.inputs.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self._generate(messages, stop, **kwargs).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(content=reply.content))


class _FakeDB:
    def __init__(self, messages):
        self.messages = messages

    def get_session(self, session_id):
        return SimpleNamespace(messages=self.messages)

    def update_last_assistant_message(self, session_id, message):
        if self.messages and self.messages[-1].get("role") == "assistant":
            self.messages[-1] = message
        else:
            self.messages.append(message)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _skills_agent(tmp_path: Path):
    skill = tmp_path / "skills" / "docx"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: docx\ndescription: Word documents\n---\n{SKILL_BODY}\n", encoding="utf-8")
    model = _Recorder()
    graph = create_deep_agent(
        model=model,
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        skills=["/skills/"],
        checkpointer=InMemorySaver(),
    )
    return graph, model


def test_turn_input_carries_pinned_skills_only_when_given():
    assert turn_input(["m"], message_id="t", rendered="r", pinned_skills=["docx"])["pinned_skills"] == ["docx"]
    assert "pinned_skills" not in turn_input(["m"], message_id="t", rendered="r", pinned_skills=[])


async def test_pinned_skill_reaches_the_first_model_call(tmp_path):
    graph, model = _skills_agent(tmp_path)
    payload = turn_input([HumanMessage("写个文档", id="t1")], message_id="t1", rendered="写个文档", pinned_skills=["docx"])

    await graph.ainvoke(payload, {"configurable": {"thread_id": "s"}})

    first = model.inputs[0]
    pinned = [m for m in first if m.type == "human" and SKILL_BODY in str(m.content)]
    assert pinned and pinned[0].additional_kwargs.get("lc_source") == "pinned_skill"
    assert len(model.inputs) == 1  # 没有先 read_file 再回答


async def test_pinned_skill_text_is_not_streamed_or_stored_as_the_reply(tmp_path):
    graph, model = _skills_agent(tmp_path)
    agent = SimpleNamespace(
        agent=graph,
        config=SimpleNamespace(llm=SimpleNamespace(provider="openai")),
        workspace_dir=None,
        workspace_virtual_path="/workspace",
        max_steps=10,
        session_context=None,
        memory_file=None,
        long_term_memory_file=None,
    )
    db = _FakeDB([{"role": "user", "content": "写个文档", "turn_id": "t1"}])

    frames = [
        frame
        async for frame in chat_stream_generator(
            ChatRequest(message="写个文档"), db, agent, "s1", "t1", "tester", pinned_skills=["docx"]
        )
    ]

    events = [json.loads(f.removeprefix("data: ").strip()) for f in frames]
    assert any(SKILL_BODY in str(m.content) for m in model.inputs[0])
    assert not [e for e in events if SKILL_BODY in json.dumps(e, ensure_ascii=False)]
    assert db.messages[-1]["role"] == "assistant" and db.messages[-1]["content"] == "done"
