"""引用通过真实 DeepAgent 文件工具读取；本地确定性模型避免外部 LLM 依赖。"""

import json

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware, turn_input


class ReferenceReader(BaseChatModel):
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "reference-reader"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        if isinstance(messages[-1], ToolMessage):
            reply = AIMessage(content=str(messages[-1].content))
        else:
            content = str(next(m.content for m in reversed(messages) if m.type == "human"))
            refs = json.loads(content.split("REFERENCES\n", 1)[1]) if "REFERENCES\n" in content else []
            reply = AIMessage(content="done", tool_calls=[
                {"name": "ls" if ref["type"] == "directory" else "read_file",
                 "args": {"path" if ref["type"] == "directory" else "file_path": ref["path"]},
                 "id": f"read-{index}"}
                for index, ref in enumerate(refs)
            ])
        return ChatResult(generations=[ChatGeneration(message=reply)])


@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("approval", [False, True])
async def test_reference_tools_use_target_session_and_survive_approval(tmp_path, kind, approval):
    root = tmp_path / "session-A"
    other = tmp_path / "session-B"
    for directory, marker in [(root, "MARKER_A"), (other, "MARKER_B")]:
        (directory / "output").mkdir(parents=True)
        (directory / "output" / f"{marker}.txt").write_text(marker, encoding="utf-8")
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=tmp_path / "hidden", virtual_mode=True),
        routes={"/workspace/": FilesystemBackend(root_dir=root, virtual_mode=True)},
    )
    model = ReferenceReader()
    summarizer = TurnAwareSummarizationMiddleware(
        model, backend=backend, trigger=("tokens", 100_000), keep=("messages", 2),
    )
    agent = create_deep_agent(
        model=model, backend=backend, middleware=[summarizer], checkpointer=InMemorySaver(),
        interrupt_on={"ls" if kind == "directory" else "read_file": True} if approval else None,
    )
    path = "/workspace/output" + ("/MARKER_A.txt" if kind == "file" else "")
    rendered = "REFERENCES\n" + json.dumps([{"path": path, "type": kind}])
    cfg = {"configurable": {"thread_id": "reference-session"}}
    result = await agent.ainvoke(turn_input(
        [HumanMessage("查看引用对象", id="t1")], message_id="t1", rendered=rendered,
    ), cfg)
    if approval:
        assert result.get("__interrupt__")
        result = await agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    tools = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tools
    assert "MARKER_A" in str(tools[-1].content)
    assert "MARKER_B" not in str(tools[-1].content)
    assert result["messages"][0].content == "查看引用对象"
    # 下一轮覆盖 turn_context；历史工具结果保留，旧引用说明不会再主动注入。
    await agent.ainvoke(turn_input(
        [HumanMessage("下一轮", id="t2")], message_id="t2", rendered="下一轮",
    ), cfg)
    humans = [m.content for m in model.calls[-1] if m.type == "human"]
    assert humans == ["查看引用对象", "下一轮"]
