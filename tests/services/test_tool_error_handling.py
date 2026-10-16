"""工具异常兜底：单个工具报错不得中断整轮对话。

背景：``langchain.agents.factory`` 构造 ``ToolNode`` 时不传 ``handle_tool_errors``，
而 langgraph 的默认实现 ``_default_handle_tool_errors`` 只把 ``ToolInvocationError``
转成文本、其余异常原样 ``raise``。MCP 连接器返回 ``isError`` 时
``langchain_mcp_adapters`` 抛的是 ``ToolException``（现场例子：只读的 SQLite 连接器被
要求写入），于是整轮 ``astream`` 崩掉，前端只看到「会话错误」。

``LoggingMiddleware`` 负责把它转成 ``status="error"`` 的 ``ToolMessage`` 回灌模型：
对话继续，模型可以解释失败原因或换工具。
"""
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

from easy_agent.services.logging_middleware import LoggingMiddleware

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


def _failing_tool(exc: Exception):
    @tool("execute")
    def execute(sql: str = "insert into t values (1)") -> str:
        """在数据库上执行语句。"""
        raise exc

    return execute


def _scripted_model() -> _ScriptedChatModel:
    return _ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "execute", "args": {"sql": "insert into t values (1)"}, "id": "call-1"}],
            ),
            AIMessage(content=FINAL_ANSWER),
        ]
    )


def _agent(exc: Exception, *, guarded: bool = True):
    model = _scripted_model()
    agent = create_agent(
        model=model,
        tools=[_failing_tool(exc)],
        middleware=[LoggingMiddleware(session_id="session-12345")] if guarded else [],
    )
    return agent, model


async def test_tool_error_no_longer_breaks_the_turn():
    """工具报错 → 整轮对话跑完，错误作为工具结果回灌模型（现场 bug 的修复）。"""
    agent, model = _agent(ToolException(READONLY_MSG))

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "把这条记录写进数据库"}]}
    )

    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    err = tool_msgs[0]
    assert err.status == "error"
    assert err.tool_call_id == "call-1"
    assert err.name == "execute"
    assert READONLY_MSG in err.content  # 原始错误文本不能丢
    assert err.content.startswith("Error")  # 项目既有失败约定（前端 ❌）
    assert err.additional_kwargs.get("is_error") is True
    # 模型确实收到了错误文本，并给出了收尾回答
    assert any(READONLY_MSG in str(m.content) for m in model.seen[1])
    assert result["messages"][-1].content == FINAL_ANSWER


async def test_without_the_guard_the_turn_still_blows_up():
    """对照：没有兜底时整轮对话崩掉 —— 说明这个中间件是必需的。"""
    agent, _ = _agent(ToolException(READONLY_MSG), guarded=False)

    with pytest.raises(ToolException):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "把这条记录写进数据库"}]}
        )


async def test_generic_exception_also_contained():
    """普通异常（如权限/文件系统错误）同样兜住。"""
    agent, _ = _agent(PermissionError("permission denied"))

    result = await agent.ainvoke({"messages": [{"role": "user", "content": "写文件"}]})

    err = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert err.status == "error"
    assert "permission denied" in err.content


async def test_successful_tool_result_untouched():
    """正常工具结果原样透传，不被包装成错误。"""

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
    agent = create_agent(
        model=model, tools=[ping], middleware=[LoggingMiddleware(session_id="s-1")]
    )

    result = await agent.ainvoke({"messages": [{"role": "user", "content": "ping"}]})

    msg = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert msg.content == "pong:real"
    assert msg.status != "error"
    assert not msg.additional_kwargs.get("is_error")


def _request(tool_call: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call=tool_call or {"name": "execute", "args": {}, "id": "call-9"},
        tool=SimpleNamespace(name="execute"),
    )


def test_graph_bubble_up_propagates_sync():
    """HITL 中断等图控制流信号必须继续冒泡，不能被兜底吞掉。"""
    mw = LoggingMiddleware(session_id="s-1")

    def handler(_request):
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        mw.wrap_tool_call(_request(), handler)


async def test_graph_bubble_up_propagates_async():
    mw = LoggingMiddleware(session_id="s-1")

    async def handler(_request):
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        await mw.awrap_tool_call(_request(), handler)


async def test_wrap_tool_call_shape_and_passthrough():
    """同步/异步钩子的返回形状一致：错误消息带 tool_call_id 与工具名。"""
    mw = LoggingMiddleware(session_id="s-1")

    async def failing(_request):
        raise ToolException(READONLY_MSG)

    msg = await mw.awrap_tool_call(_request(), failing)
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call-9"
    assert msg.name == "execute"
    assert READONLY_MSG in msg.content

    async def ok(_request):
        return "pong"

    assert await mw.awrap_tool_call(_request(), ok) == "pong"
