"""DA-03 P0：记忆每次运行都重新加载；主代理装配用会话记忆中间件与渲染本轮上下文的摘要中间件。

框架的 MemoryMiddleware 每个 thread 只读一次记忆。每条消息一个 thread 时这不成问题；
改成会话级 thread 后，会话记忆（每轮结束后改写）、长期记忆、上传清单都会停在第一轮。
"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from easy_agent.agent.memory import SessionMemoryMiddleware
from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware

CFG = {"configurable": {"thread_id": "s"}}


class _SystemPrompts(BaseChatModel):
    prompts: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "system-prompts"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(str(messages[0].content))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


async def _two_runs_with_a_memory_edit(tmp_path, *, session_memory: bool) -> list[str]:
    (tmp_path / "AGENTS.md").write_text("MEMORY-V1", encoding="utf-8")
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    model = _SystemPrompts()
    middleware = (
        [SessionMemoryMiddleware(backend=backend, sources=["/AGENTS.md"], add_cache_control=True)]
        if session_memory
        else []
    )
    agent = create_deep_agent(
        model=model,
        backend=backend,
        memory=["/AGENTS.md"],
        middleware=middleware,
        checkpointer=InMemorySaver(),
    )
    await agent.ainvoke({"messages": [HumanMessage("q0")]}, CFG)
    (tmp_path / "AGENTS.md").write_text("MEMORY-V2", encoding="utf-8")
    await agent.ainvoke({"messages": [HumanMessage("q1")]}, CFG)
    return model.prompts


async def test_memory_reloads_on_every_run_of_the_same_thread(tmp_path):
    first, second = await _two_runs_with_a_memory_edit(tmp_path, session_memory=True)
    assert "MEMORY-V1" in first
    assert "MEMORY-V2" in second and "MEMORY-V1" not in second


async def test_framework_memory_loads_once_per_thread(tmp_path):
    """对照：框架默认行为。若上游改成每次运行都重读，这条会失败，届时可去掉覆盖。"""
    _, second = await _two_runs_with_a_memory_edit(tmp_path, session_memory=False)
    assert "MEMORY-V1" in second


def test_main_agent_stack_uses_session_memory_and_turn_aware_summarization(build_easy_agent, compiled_agents):
    agent = build_easy_agent()

    main = next(call for call in compiled_agents if call["name"] == "easy-agent")
    memory = [m for m in main["middleware"] if m.name == "MemoryMiddleware"]
    summarization = [m for m in main["middleware"] if m.name == "SummarizationMiddleware"]
    assert len(memory) == 1 and isinstance(memory[0], SessionMemoryMiddleware)
    assert len(summarization) == 1 and isinstance(summarization[0], TurnAwareSummarizationMiddleware)

    subagents = [call for call in compiled_agents if call["name"] != "easy-agent"]
    assert subagents
    for call in subagents:
        assert not any(isinstance(m, TurnAwareSummarizationMiddleware) for m in call["middleware"]), call["name"]

    # turn_context 必须是图的输入通道，否则编排器传入的本轮渲染内容会被静默丢弃。
    # （skills_metadata 只在配置了技能源时才是输入通道；没有技能时传了也无影响。）
    graph = agent.agent
    assert {"messages", "turn_context"} <= set(graph.builder.schemas[graph.builder.input_schema])
