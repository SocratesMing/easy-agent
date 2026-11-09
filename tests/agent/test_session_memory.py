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

from easy_agent.agent.memory import SESSION_MEMORY_PROMPT, SessionMemoryMiddleware
from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware

CFG = {"configurable": {"thread_id": "s"}}


def _text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)


class _SystemPrompts(BaseChatModel):
    """记录每次请求：``prompts`` 是整份请求的文本，``systems`` / ``humans`` 分别是系统提示词与最后一条用户消息。"""

    prompts: list = Field(default_factory=list)
    systems: list = Field(default_factory=list)
    humans: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "system-prompts"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append("\n".join(_text(message) for message in messages))
        self.systems.append(_text(messages[0]))
        self.humans.append(next(_text(m) for m in reversed(messages) if m.type == "human"))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


async def _two_runs_with_a_memory_edit(tmp_path, *, session_memory: bool, model=None) -> list[str]:
    (tmp_path / "AGENTS.md").write_text("MEMORY-V1", encoding="utf-8")
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    model = model or _SystemPrompts()
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


async def test_session_memory_prompt_forbids_editing_memory_files(tmp_path):
    """记忆由系统每轮自动整理：框架默认段要求模型「及时用 edit_file 写记忆」，必须换掉。"""
    first, _ = await _two_runs_with_a_memory_edit(tmp_path, session_memory=True)

    assert "MEMORY-V1" in first
    assert "不要用 `edit_file` / `write_file` 改写这些文件" in first
    assert "top priority" not in first
    assert "update memory promptly" not in first


async def test_framework_memory_prompt_still_asks_to_edit_memory(tmp_path):
    """对照：框架默认段仍要求模型自己写记忆。上游改掉这段后，这条会失败，届时可重新评估覆盖。"""
    first, _ = await _two_runs_with_a_memory_edit(tmp_path, session_memory=False)
    assert "update memory promptly" in first


async def test_memory_goes_into_the_current_user_message_not_the_system_prompt(tmp_path):
    """记忆一改，系统提示词也不变：后面的工具定义与历史才保得住前缀缓存。"""
    model = _SystemPrompts()
    await _two_runs_with_a_memory_edit(tmp_path, session_memory=True, model=model)

    first_system, second_system = model.systems
    assert "MEMORY-V1" not in first_system and "<memory_guidelines>" in first_system
    assert first_system == second_system  # 记忆从 V1 改成 V2，系统提示词一字不变
    assert model.humans[0].startswith("<agent_memory>\n/AGENTS.md\n\nMEMORY-V1")
    assert model.humans[0].endswith("q0")
    assert "MEMORY-V2" in model.humans[1] and model.humans[1].endswith("q1")
    # 历史里的上一轮用户消息是原文：记忆只放进本次请求的本轮消息，不写回 thread
    assert "MEMORY-V1" not in model.prompts[1]


async def test_no_memory_block_without_memory_files(tmp_path):
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    model = _SystemPrompts()
    agent = create_deep_agent(
        model=model,
        backend=backend,
        memory=["/AGENTS.md"],
        middleware=[SessionMemoryMiddleware(backend=backend, sources=["/AGENTS.md"])],
        checkpointer=InMemorySaver(),
    )

    await agent.ainvoke({"messages": [HumanMessage("q0")]}, CFG)

    assert "<agent_memory>" not in model.humans[0] and model.humans[0] == "q0"
    assert "<memory_guidelines>" in model.systems[0]


def test_memory_is_prepended_to_the_rendered_turn_content(build_easy_agent, monkeypatch, tmp_path):
    """主代理里本轮上下文先渲染（外层摘要中间件），记忆块再放到渲染结果开头（内层记忆中间件）。"""
    import asyncio

    import easy_agent.agent.core as agent_mod
    from easy_agent.agent.run_config import build_run_config
    from easy_agent.agent.turn_context import turn_input

    model = _SystemPrompts()
    monkeypatch.setattr(agent_mod, "create_model", lambda *a, **k: model)
    agent = build_easy_agent()
    agent.long_term_memory_file.parent.mkdir(parents=True, exist_ok=True)
    agent.long_term_memory_file.write_text("# 用户长期记忆\n\n## 工作背景\n- 固收研究员\n", encoding="utf-8")

    payload = turn_input(
        [HumanMessage(content="原话", id="m1")], message_id="m1", rendered="【本轮上下文】\n原话", pinned_skills=None
    )
    asyncio.run(agent.agent.ainvoke(payload, config=build_run_config(agent, "s1"), context=agent.session_context))

    human = model.humans[0]
    assert human.startswith("<agent_memory>") and "固收研究员" in human
    assert human.endswith("【本轮上下文】\n原话")
    assert "固收研究员" not in model.systems[0]


def test_main_agent_stack_uses_session_memory_and_turn_aware_summarization(build_easy_agent, compiled_agents):
    agent = build_easy_agent()

    main = next(call for call in compiled_agents if call["name"] == "easy-agent")
    memory = [m for m in main["middleware"] if m.name == "MemoryMiddleware"]
    summarization = [m for m in main["middleware"] if m.name == "SummarizationMiddleware"]
    assert len(memory) == 1 and isinstance(memory[0], SessionMemoryMiddleware)
    assert memory[0].system_prompt == SESSION_MEMORY_PROMPT
    assert len(summarization) == 1 and isinstance(summarization[0], TurnAwareSummarizationMiddleware)

    subagents = [call for call in compiled_agents if call["name"] != "easy-agent"]
    assert subagents
    for call in subagents:
        assert not any(isinstance(m, TurnAwareSummarizationMiddleware) for m in call["middleware"]), call["name"]

    # turn_context 必须是图的输入通道，否则编排器传入的本轮渲染内容会被静默丢弃。
    # （skills_metadata 只在配置了技能源时才是输入通道；没有技能时传了也无影响。）
    graph = agent.agent
    assert {"messages", "turn_context"} <= set(graph.builder.schemas[graph.builder.input_schema])
