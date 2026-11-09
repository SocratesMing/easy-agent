"""DA-12：工具异常走官方 ``ToolErrorMiddleware``，只回灌可恢复的错误，并脱敏。

背景：``langchain.agents.factory`` 构造 ``ToolNode`` 时不传 ``handle_tool_errors``，
工具抛出的异常默认会让整轮 ``astream`` 中断。改造前的 ``LoggingMiddleware`` 走向另一个
极端：捕获全部异常并把 ``str(e)`` 原样回灌，宿主机路径、SQL 等细节进了模型上下文，
真正的程序错误也被模型"圆过去"。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import ToolException, tool
from langgraph.errors import GraphInterrupt
from pydantic import Field

from easy_agent.agent.tool_errors import (
    build_tool_error_middleware,
    is_recoverable,
    sanitize_detail,
    tool_error_content,
)

READONLY_MSG = "Error executing tool execute: This database is in READ-ONLY mode."
FINAL_ANSWER = "这个连接器是只读的，我没有写权限。"


class _ScriptedChatModel(BaseChatModel):
    """按脚本回话的假模型：依次吐出预设消息（首条带工具调用）。"""

    responses: list[Any] = Field(default_factory=list)
    seen: list[list[Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-chat"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001 - 假模型忽略绑定
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        msg = self.responses[min(len(self.seen) - 1, len(self.responses) - 1)]
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _agent(exc: Exception, *, guarded: bool = True):
    @tool("execute")
    def execute(sql: str = "insert into t values (1)") -> str:
        """在数据库上执行语句。"""
        raise exc

    model = _ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "execute", "args": {"sql": "insert into t values (1)"}, "id": "call-1"}],
            ),
            AIMessage(content=FINAL_ANSWER),
        ]
    )
    agent = create_agent(
        model=model,
        tools=[execute],
        middleware=[build_tool_error_middleware()] if guarded else [],
    )
    return agent, model


async def _ask(agent):
    return await agent.ainvoke({"messages": [{"role": "user", "content": "把这条记录写进数据库"}]})


async def test_tool_exception_is_returned_and_the_turn_continues():
    agent, model = _agent(ToolException(READONLY_MSG))

    result = await _ask(agent)

    err = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert err.status == "error"
    assert (err.tool_call_id, err.name) == ("call-1", "execute")
    assert err.content.startswith(READONLY_MSG)  # 工具自己的说明原样保留
    assert any(READONLY_MSG in str(m.content) for m in model.seen[1])
    assert result["messages"][-1].content == FINAL_ANSWER


async def test_without_the_middleware_the_turn_blows_up():
    agent, _ = _agent(ToolException(READONLY_MSG), guarded=False)
    with pytest.raises(ToolException):
        await _ask(agent)


async def test_recoverable_os_error_is_returned_with_its_type():
    agent, _ = _agent(PermissionError("permission denied"))

    result = await _ask(agent)

    err = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert err.status == "error"
    assert "PermissionError: permission denied" in err.content


@pytest.mark.parametrize("exc", [KeyError("config"), TypeError("bad call"), RuntimeError("boom")])
async def test_unexpected_errors_propagate(exc):
    """程序错误不再被模型圆过去：本轮中断，由流式层报错（改造前会被吞掉）。"""
    agent, _ = _agent(exc)
    with pytest.raises(type(exc)):
        await _ask(agent)


async def test_successful_tool_result_untouched():
    @tool("ping")
    def ping() -> str:
        """回显。"""
        return "pong:real"

    model = _ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": "c9"}]),
            AIMessage(content="好了"),
        ]
    )
    agent = create_agent(model=model, tools=[ping], middleware=[build_tool_error_middleware()])

    result = await agent.ainvoke({"messages": [{"role": "user", "content": "ping"}]})

    msg = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert msg.content == "pong:real"
    assert msg.status != "error"


def test_host_paths_are_redacted_and_long_details_truncated():
    from easy_agent.agent import tool_errors

    project_root = tool_errors._REDACTIONS[0][0]
    exc = FileNotFoundError(f"No such file: {project_root}/data/easy_agent.db")
    content = tool_error_content(exc, "read_file")
    assert project_root not in content
    assert "<app>/data/easy_agent.db" in content

    long_detail = sanitize_detail("x" * 5000)
    assert len(long_detail) < 600
    assert long_detail.endswith("(已截断)")


def test_exception_groups_are_recoverable_only_when_every_leaf_is():
    assert is_recoverable(ExceptionGroup("io", [TimeoutError(), ConnectionResetError()]))
    assert not is_recoverable(ExceptionGroup("mixed", [TimeoutError(), KeyError("bug")]))


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": "execute", "args": {}, "id": "call-9"},
        tool=SimpleNamespace(name="execute"),
    )


def test_graph_interrupt_propagates_sync():
    """HITL 中断等图控制流信号必须继续冒泡。"""

    def handler(_request):
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        build_tool_error_middleware().wrap_tool_call(_request(), handler)


async def test_graph_interrupt_propagates_async():
    async def handler(_request):
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        await build_tool_error_middleware().awrap_tool_call(_request(), handler)
