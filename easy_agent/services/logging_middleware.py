"""集中式智能体日志中间件 + 工具异常兜底。

说明：每步的「推理过程 / 工具执行 / 正文 / 结束统计」日志已由
``stream_processor.StreamProcessor`` 统一输出（以 ``stepN`` 开头、编号与前端展示
一致），此处不再重复打印，避免两套计数器（step 与 round_seq 差一）造成日志混淆。

本中间件另承担一项职责：**工具异常兜底**。工具执行抛出的异常默认会被 langgraph
``ToolNode`` 的 ``_default_handle_tool_errors`` 原样重新抛出（``langchain.agents.factory``
构造 ``ToolNode`` 时未传 ``handle_tool_errors``，其默认实现只把
``ToolInvocationError`` 转成文本、其余 ``raise``），于是整轮 ``astream`` 中断、
前端只看到「会话错误」。典型触发：MCP 连接器返回 ``isError`` →
``langchain_mcp_adapters`` 抛 ``ToolException``（例如只读的 SQLite 连接器被要求写入）。

这里把工具异常转成 ``status="error"`` 的 ``ToolMessage`` 回灌给模型：本轮对话继续，
模型可以据此解释失败原因、调整参数或换工具，前端也会把该工具标成失败（❌）。

放行规则：``GraphBubbleUp``（HITL 人工审批中断等图控制流信号）必须继续向上冒泡，
绝不能被吞掉；``BaseException``（``asyncio.CancelledError`` 等）不在此捕获范围。
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

logger = logging.getLogger(__name__)

_ERROR_HINT = (
    "（工具 {name} 未返回结果，请勿假设它已成功。可据此调整参数、改用其它工具，"
    "或直接向用户说明失败原因。）"
)


class LoggingMiddleware(AgentMiddleware):
    """AgentMiddleware 钩子：step 日志归 StreamProcessor；此处只做工具异常兜底。"""

    def __init__(self, session_id: str = "", result_log_truncate: int = 500):
        self.sid = session_id[-5:] if session_id else "agent"
        self._truncate_len = result_log_truncate or 500

    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as e:  # noqa: BLE001 - 兜底即本方法职责
            return self._tool_error_message(request, e)

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as e:  # noqa: BLE001 - 兜底即本方法职责
            return self._tool_error_message(request, e)

    async def aafter_model(self, state, runtime) -> None:
        return None

    # ── 内部 ────────────────────────────────────────────────────────────

    def _tool_error_message(self, request, error: Exception) -> ToolMessage:
        """把工具异常转成回灌模型的错误 ToolMessage（不让整轮对话中断）。"""
        tool_call = getattr(request, "tool_call", None) or {}
        name = (
            tool_call.get("name")
            or getattr(getattr(request, "tool", None), "name", None)
            or "tool"
        )
        call_id = tool_call.get("id") or ""
        detail = str(error) or error.__class__.__name__
        # 原始文本已以 Error 开头（如 MCP 适配器的 "Error executing tool ..."）时不重复加前缀
        prefix = "" if detail.lstrip().startswith("Error") else "Error: "
        content = f"{prefix}{detail}\n" + _ERROR_HINT.format(name=name)

        logger.error(
            "[%s] 🔧❌ 工具异常（已回灌模型，对话继续） | 工具: %s | 调用: %s | %s: %s",
            self.sid,
            name,
            call_id,
            type(error).__name__,
            detail[: self._truncate_len],
        )
        return ToolMessage(
            content=content,
            tool_call_id=call_id,
            name=name,
            status="error",
            additional_kwargs={"is_error": True},
        )
