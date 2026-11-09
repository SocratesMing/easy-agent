"""DA-03 实验：每条消息一个 thread（现状）vs 一个会话一个 thread。

真实 ``create_deep_agent`` + 记录每次调用的假模型 + ``InMemorySaver`` + 磁盘 backend，测：

1. 越过摘要阈值后，每轮是否都重新摘要整段历史；``/conversation_history/`` 写了几个文件；
2. 两轮之间改记忆文件、新增技能，第二轮 system prompt 能否看到（以及会话 thread 下怎么刷新）；
3. 会话 thread 上有待审批中断时直接发新消息：能否继续、悬空的工具调用怎么收尾；
4. 工具执行中途取消后再发新消息；下一轮换一张图（切专家 / 换模型）续用同一 thread；
5. 经 ``turn_context`` 状态通道只在本轮模型请求里注入的上下文（知识库 / 页面上下文 / 工作区前缀）
   是否会写进 thread、下一轮是否还在；
6. 审批中断后恢复执行，恢复的那次运行是否仍使用本轮注入。

运行：python docs/audit/evidence-2026-10-19-deepagents/exp_session_thread.py
"""

import asyncio
import tempfile
import warnings
from pathlib import Path
from typing import Annotated, NotRequired

warnings.filterwarnings("ignore")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.summarization import DEFAULT_SUMMARY_PROMPT
from langchain.agents.middleware.types import OmitFromOutput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

SUMMARY_MARKER = DEFAULT_SUMMARY_PROMPT[:40]
TURNS = 8


class Recorder(BaseChatModel):
    """按输入分辨摘要调用 / 主代理调用并记录；最后一条用户消息含 WRITE 时发起 write_file。"""

    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        metadata = getattr(run_manager, "metadata", None) or {}
        text = "\n".join(str(m.content) for m in messages)
        if metadata.get("lc_source") == "summarization" or SUMMARY_MARKER in text:
            self.calls.append(("summary", len(text), messages))
            reply = AIMessage(content=f"SUMMARY#{sum(c[0] == 'summary' for c in self.calls)}")
        else:
            self.calls.append(("agent", len(messages), messages))
            last = messages[-1]
            if last.type == "human" and "WRITE" in str(last.content):
                reply = AIMessage(
                    content="",
                    tool_calls=[{"name": "write_file", "args": {"file_path": "/x.txt", "content": "x"}, "id": "call-w"}],
                )
            elif last.type == "human" and "SLOW" in str(last.content):
                reply = AIMessage(content="", tool_calls=[{"name": "slow", "args": {"x": "1"}, "id": "call-s"}])
            else:
                reply = AIMessage(content=f"answer[{str(last.content)[:12]}]")
        return ChatResult(generations=[ChatGeneration(message=reply)])


class RefreshingMemoryMiddleware(MemoryMiddleware):
    """每次运行都重新读记忆源（框架默认只在 thread 第一次运行时读）。"""

    @property
    def name(self) -> str:
        return "MemoryMiddleware"

    def before_agent(self, state, runtime, config):
        return super().before_agent({k: v for k, v in state.items() if k != "memory_contents"}, runtime, config)

    async def abefore_agent(self, state, runtime, config):
        return await super().abefore_agent(
            {k: v for k, v in state.items() if k != "memory_contents"}, runtime, config
        )


def setup(root: Path) -> FilesystemBackend:
    (root / "memories").mkdir(parents=True)
    (root / "memories" / "AGENTS.md").write_text("MEMORY-V1", encoding="utf-8")
    _skill(root, "alpha")
    return FilesystemBackend(root_dir=root, virtual_mode=True)


def _skill(root: Path, name: str) -> None:
    (root / "skills" / name).mkdir(parents=True)
    (root / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\nbody\n", encoding="utf-8"
    )


def build(model, backend, saver, *, refresh_memory=False, interrupt_on=None):
    middleware = [SummarizationMiddleware(model, backend=backend, trigger=("messages", 8), keep=("messages", 4))]
    if refresh_memory:
        middleware.append(RefreshingMemoryMiddleware(backend=backend, sources=["/memories/AGENTS.md"]))
    return create_deep_agent(
        model=model,
        backend=backend,
        middleware=middleware,
        memory=["/memories/AGENTS.md"],
        skills=["/skills/"],
        checkpointer=saver,
        interrupt_on=interrupt_on,
    )


async def run_mode(mode: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backend, model, saver = setup(root), Recorder(), InMemorySaver()
        agent = build(model, backend, saver, refresh_memory=mode == "session+refresh")
        db: list[tuple[str, str]] = []  # 现状模式下的 DB 投影（只存正文）
        print(f"\n== {mode} ==")
        print("turn | summary calls (prompt chars) | agent input msgs | memory V2 seen | beta skill seen")
        for turn in range(TURNS):
            if turn == 1:  # 第一轮之后：改记忆、加技能
                (root / "memories" / "AGENTS.md").write_text("MEMORY-V2", encoding="utf-8")
                _skill(root, "beta")
            question = f"q{turn}"
            start = len(model.calls)
            if mode == "per-message":
                history = [HumanMessage(t) if r == "user" else AIMessage(t) for r, t in db]
                payload = {"messages": [*history, HumanMessage(question)]}
                thread = f"s-{turn}"
            else:
                payload = {"messages": [HumanMessage(question)]}
                if mode == "session+refresh":
                    payload["skills_metadata"] = None
                thread = "s"
            result = await agent.ainvoke(payload, config={"configurable": {"thread_id": thread}})
            db += [("user", question), ("assistant", str(result["messages"][-1].content))]
            calls = model.calls[start:]
            summaries = [c[1] for c in calls if c[0] == "summary"]
            agent_call = next(c for c in calls if c[0] == "agent")
            system = str(agent_call[2][0].content)
            print(
                f"{turn:>4} | {len(summaries)} {summaries} | {agent_call[1]:>16} | "
                f"{'MEMORY-V2' in system!s:>14} | {'beta' in system!s:>15}"
            )
        history_files = sorted(p.name for p in root.rglob("conversation_history/*.md"))
        print(f"threads in checkpointer: {len(saver.storage)} | conversation_history files: {len(history_files)}")


async def pending_interrupt_then_new_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backend, model, saver = setup(root), Recorder(), InMemorySaver()
        agent = build(model, backend, saver, interrupt_on={"write_file": True})
        cfg = {"configurable": {"thread_id": "s"}}
        print("\n== session thread: pending approval, then a new message instead of resume ==")
        first = await agent.ainvoke({"messages": [HumanMessage("please WRITE a file")]}, config=cfg)
        print("interrupted:", bool(first.get("__interrupt__")))
        start = len(model.calls)
        second = await agent.ainvoke({"messages": [HumanMessage("never mind")]}, config=cfg)
        agent_input = next(c for c in model.calls[start:] if c[0] == "agent")[2]
        patched = [m for m in agent_input if m.type == "tool" and m.tool_call_id == "call-w"]
        state = await agent.aget_state(cfg)
        print("second reply:", second["messages"][-1].content)
        print("patched tool message:", patched[0].content if patched else None)
        print("pending interrupts after:", len(state.interrupts), "| /x.txt written:", (root / "x.txt").exists())


TOOL_STARTED = asyncio.Event()


@tool
async def slow(x: str) -> str:
    """A slow tool."""
    TOOL_STARTED.set()
    await asyncio.sleep(30)
    return "done"


class TurnContextState(AgentState):
    turn_context: Annotated[NotRequired[dict | None], OmitFromOutput]


class TurnContextMiddleware(AgentMiddleware):
    """模型请求里把本轮用户消息换成渲染后的完整内容；thread 的 messages 只存原话。"""

    state_schema = TurnContextState

    def _apply(self, request):
        turn = request.state.get("turn_context") or {}
        target, content = turn.get("message_id"), turn.get("content")
        if not target or content is None:
            return request
        messages = [
            HumanMessage(content=content, id=m.id) if m.type == "human" and m.id == target else m
            for m in request.messages
        ]
        return request.override(messages=messages)

    def wrap_model_call(self, request, handler):
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._apply(request))


async def cancel_switch_and_inject() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backend, model, saver = setup(root), Recorder(), InMemorySaver()
        injector = TurnContextMiddleware()  # 无实例状态，可在多张图之间复用
        agent = create_deep_agent(
            model=model, backend=backend, tools=[slow], middleware=[injector], checkpointer=saver
        )
        cfg = {"configurable": {"thread_id": "s"}}

        print("\n== session thread: cancel while a tool runs, then a new message ==")
        task = asyncio.create_task(agent.ainvoke({"messages": [HumanMessage("run SLOW")]}, config=cfg))
        await TOOL_STARTED.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        state = await agent.aget_state(cfg)
        print("after cancel: next =", state.next, "| last message =", state.values["messages"][-1].type)
        start = len(model.calls)
        reply = await agent.ainvoke({"messages": [HumanMessage("after cancel")]}, config=cfg)
        agent_input = next(c for c in model.calls[start:] if c[0] == "agent")[2]
        print("reply:", reply["messages"][-1].content, "| patched:", [m.content[:40] for m in agent_input if m.type == "tool"])

        print("\n== session thread: next turn on a different graph (expert switch / model switch) ==")
        expert = create_deep_agent(
            model=model, backend=backend, system_prompt="EXPERT-B", middleware=[injector], checkpointer=saver
        )
        start = len(model.calls)
        await expert.ainvoke({"messages": [HumanMessage("hello expert")]}, config=cfg)
        agent_input = next(c for c in model.calls[start:] if c[0] == "agent")[2]
        print(
            "system has EXPERT-B:", "EXPERT-B" in str(agent_input[0].content),
            "| sees earlier turns:", [m.content for m in agent_input if m.type == "human"],
        )

        print("\n== transient per-turn context (knowledge / page context / workspace prefix) ==")
        start = len(model.calls)
        await expert.ainvoke(
            {
                "messages": [HumanMessage("question with context", id="turn-9")],
                "turn_context": {"message_id": "turn-9", "content": "[KNOWLEDGE CONTEXT]\nquestion with context"},
            },
            config=cfg,
        )
        agent_input = next(c for c in model.calls[start:] if c[0] == "agent")[2]
        stored = [m for m in (await expert.aget_state(cfg)).values["messages"] if m.id == "turn-9"][0]
        print("model saw:", repr(agent_input[-1].content), "| stored in thread:", repr(stored.content))

        start = len(model.calls)
        await expert.ainvoke({"messages": [HumanMessage("next turn", id="turn-10")], "turn_context": None}, config=cfg)
        agent_input = next(c for c in model.calls[start:] if c[0] == "agent")[2]
        print("next turn sees turn-9 as:", repr([m.content for m in agent_input if m.id == "turn-9"][0]))


async def turn_context_survives_resume() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backend, model, saver = setup(root), Recorder(), InMemorySaver()
        agent = create_deep_agent(
            model=model,
            backend=backend,
            middleware=[TurnContextMiddleware()],
            checkpointer=saver,
            interrupt_on={"write_file": True},
        )
        cfg = {"configurable": {"thread_id": "s"}}
        print("\n== turn_context after an approval: the resumed run still renders the turn ==")
        first = await agent.ainvoke(
            {
                "messages": [HumanMessage("please WRITE", id="turn-1")],
                "turn_context": {"message_id": "turn-1", "content": "[KNOWLEDGE CONTEXT]\nplease WRITE"},
            },
            config=cfg,
        )
        start = len(model.calls)
        await agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config=cfg)
        agent_input = next(c for c in model.calls[start:] if c[0] == "agent")[2]
        print(
            "interrupted:", bool(first.get("__interrupt__")),
            "| resumed model call saw:", repr([m.content for m in agent_input if m.id == "turn-1"][0]),
            "| /x.txt written:", (root / "x.txt").exists(),
        )


async def main() -> None:
    for mode in ("per-message", "session", "session+refresh"):
        await run_mode(mode)
    await pending_interrupt_then_new_message()
    await cancel_switch_and_inject()
    await turn_context_survives_resume()


asyncio.run(main())
