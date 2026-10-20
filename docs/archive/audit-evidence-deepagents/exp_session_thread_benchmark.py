"""DA-03 P2 基准：每消息 thread vs 会话 thread。

用项目自己的编排器 ``chat_stream_generator``、主代理的 ``TurnAwareSummarizationMiddleware`` 和官方
``AsyncSqliteSaver``（临时文件），按项目的摘要比例（trigger 0.8 窗口、keep 0.1 窗口、
trim max(4000, 0.02 窗口)）缩到 32k 窗口；每轮用户约 300 token、回复约 800 token，跑 60 轮。统计：

- 摘要调用次数（真实环境里每次都在主模型调用之前同步执行，拖慢首 token）；
- 主模型每次调用的输入 token（``count_tokens_approximately`` 近似计数）；
- checkpoint 库大小、thread 数；
- ``/conversation_history/`` 文件个数与总大小。

运行（需要 sqlite-saver extra）：python docs/audit/evidence-2026-10-19-deepagents/exp_session_thread_benchmark.py
"""

import asyncio
import logging
import statistics
import sys
import tempfile
import warnings
from pathlib import Path
from types import SimpleNamespace

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from deepagents import create_deep_agent  # noqa: E402
from deepagents.backends import FilesystemBackend  # noqa: E402
from langchain.agents.middleware.summarization import DEFAULT_SUMMARY_PROMPT  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402
from langchain_core.messages.utils import count_tokens_approximately  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402
from pydantic import Field  # noqa: E402

from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware  # noqa: E402
from easy_agent.models.api import ChatRequest  # noqa: E402
from easy_agent.services.streaming import chat_stream_generator  # noqa: E402

WINDOW = 32_000
TURNS = 60
SUMMARY_MARKER = DEFAULT_SUMMARY_PROMPT[:40]
QUESTION = "question " * 130  # ≈ 300 token
REPLY = "answer " * 400  # ≈ 800 token
SUMMARY = "summary " * 250


class Bench(BaseChatModel):
    agent_inputs: list = Field(default_factory=list)
    summary_inputs: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "bench"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tokens = count_tokens_approximately(messages)
        if SUMMARY_MARKER in "".join(str(m.content) for m in messages):
            self.summary_inputs.append(tokens)
            reply = SUMMARY
        else:
            self.agent_inputs.append(tokens)
            reply = REPLY
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self._generate(messages, stop, **kwargs).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(content=reply.content))


class FakeDB:
    def __init__(self):
        self.messages: list[dict] = []

    def get_session(self, session_id):
        return SimpleNamespace(messages=self.messages)

    def update_last_assistant_message(self, session_id, message):
        if self.messages and self.messages[-1].get("role") == "assistant":
            self.messages[-1] = message
        else:
            self.messages.append(message)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


async def run(mode: str, root: Path) -> dict:
    db_path = root / "checkpoints.db"
    workspace = root / "backend"
    workspace.mkdir()
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        model = Bench()
        backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
        summarizer = TurnAwareSummarizationMiddleware(
            model,
            backend=backend,
            trigger=("tokens", int(WINDOW * 0.8)),
            keep=("tokens", int(WINDOW * 0.1)),
            trim_tokens_to_summarize=max(4000, int(WINDOW * 0.02)),
        )
        graph = create_deep_agent(model=model, backend=backend, middleware=[summarizer], checkpointer=saver)
        agent = SimpleNamespace(
            agent=graph,
            config=SimpleNamespace(
                agent=SimpleNamespace(session_thread=mode == "session"), llm=SimpleNamespace(provider="openai")
            ),
            workspace_dir=None,
            workspace_virtual_path="/workspace",
            max_steps=10,
            session_context=None,
            memory_file=None,
            long_term_memory_file=None,
        )
        db = FakeDB()
        summary_turns = []
        for turn in range(TURNS):
            before = len(model.summary_inputs)
            text = f"q{turn} {QUESTION}"
            db.messages.append({"role": "user", "content": text, "turn_id": f"m{turn}"})
            async for _ in chat_stream_generator(ChatRequest(message=text), db, agent, "s1", f"m{turn}", "bench"):
                pass
            if len(model.summary_inputs) > before:
                summary_turns.append(turn)
        threads = {item.config["configurable"]["thread_id"] async for item in saver.alist(None)}
    size = sum(p.stat().st_size for p in root.glob("checkpoints.db*"))
    history = list(workspace.rglob("conversation_history/*.md"))
    first = summary_turns[0] if summary_turns else TURNS
    after = model.agent_inputs[first:] or [0]
    return {
        "mode": mode,
        "summary_calls": len(model.summary_inputs),
        "first_summary_turn": first,
        "avg_input_after": int(statistics.mean(after)),
        "max_input": max(model.agent_inputs),
        "db_kb": size // 1024,
        "threads": len(threads),
        "history_files": len(history),
        "history_kb": sum(p.stat().st_size for p in history) // 1024,
    }


async def main() -> None:
    logging.disable(logging.WARNING)  # 假 DB 不支持记录本轮起点 checkpoint，告警与基准无关
    rows = []
    for mode in ("per-message", "session"):
        with tempfile.TemporaryDirectory() as tmp:
            rows.append(await run(mode, Path(tmp)))
    print(f"window={WINDOW} turns={TURNS} ~1.1k tokens/turn")
    print("mode        | summary calls | first summary turn | avg agent input after it | max input | checkpoint db | threads | history files")
    for r in rows:
        print(
            f"{r['mode']:<11} | {r['summary_calls']:>13} | {r['first_summary_turn']:>18} | "
            f"{r['avg_input_after']:>24} | {r['max_input']:>9} | {r['db_kb']:>9} KB | {r['threads']:>7} | "
            f"{r['history_files']} ({r['history_kb']} KB)"
        )


asyncio.run(main())
