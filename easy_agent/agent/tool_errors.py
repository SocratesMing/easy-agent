"""工具异常处理（DA-12）：官方 ``ToolErrorMiddleware`` + 可恢复白名单 + 脱敏。

改造前的 ``services/logging_middleware.LoggingMiddleware`` 捕获除 ``GraphBubbleUp`` 外的
全部异常，把 ``str(e)`` 原样回灌模型：宿主机路径、SQL、配置细节会进入模型上下文和前端，
真正的程序错误也被模型"圆过去"。官方建议（Fault tolerance）：只把已知可恢复的错误转成
错误 ``ToolMessage``，意外异常照常抛出。

可恢复（回灌模型，本轮继续）：

- ``ToolException``：工具主动报告的失败，文案由工具撰写；
- 输入 / 外部条件类：``ValueError``（含 JSON 解析与 pydantic 校验）、``TimeoutError``、
  ``OSError``（文件不存在、无权限、连接失败…）、``httpx.HTTPError``、``McpError``；
- 只由上述异常组成的 ``ExceptionGroup``（anyio / MCP 传输层会包一层）。

其余（``TypeError`` / ``KeyError`` / ``AttributeError`` / ``RuntimeError`` 等程序错误）
向上抛出：本轮中断，由流式层转成 SSE ``error``，这里按 ERROR 记录完整堆栈。
HITL 中断等图控制流信号由 ``ToolErrorMiddleware`` 本身放行，不会到这里。
MCP 工具的失败已在 ``services/mcp_evidence`` 里转成 ``is_error=true`` 的结果，通常不会抛到这一层。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain.agents.middleware import ToolErrorMiddleware
from langchain_core.tools import ToolException

logger = logging.getLogger(__name__)

_MAX_DETAIL = 500
_HINT = (
    "（工具 {name} 未返回结果，请勿假设它已成功。可据此调整参数、改用其它工具，"
    "或直接向用户说明失败原因。）"
)


def _recoverable_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [ToolException, ValueError, TimeoutError, OSError]
    try:
        import httpx

        types.append(httpx.HTTPError)
    except ImportError:  # pragma: no cover - httpx 随 langchain-openai 安装
        pass
    try:
        from mcp.shared.exceptions import McpError

        types.append(McpError)
    except ImportError:  # pragma: no cover
        pass
    return tuple(types)


_RECOVERABLE = _recoverable_types()

# 回灌文本里的宿主机路径换成占位符（先换项目目录：它通常在家目录下）
_REDACTIONS = tuple(
    (prefix, placeholder)
    for prefix, placeholder in (
        (str(Path(__file__).resolve().parents[2]), "<app>"),
        (str(Path.home()), "~"),
    )
    if len(prefix) > 1
)


def is_recoverable(exc: BaseException) -> bool:
    """可以回灌给模型、让本轮继续的工具异常。"""
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(is_recoverable(e) for e in exc.exceptions)
    return isinstance(exc, _RECOVERABLE)


def sanitize_detail(text: str, limit: int = _MAX_DETAIL) -> str:
    for prefix, placeholder in _REDACTIONS:
        text = text.replace(prefix, placeholder)
    if len(text) > limit:
        text = text[:limit] + "…(已截断)"
    return text


def tool_error_content(exc: BaseException, tool_name: str) -> str:
    """回灌模型的错误文本：保留工具自己的失败说明，其余异常只给类型 + 脱敏后的信息。"""
    detail = str(exc).strip()
    if isinstance(exc, ToolException):
        body = detail or "工具执行失败"
    else:
        body = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    body = sanitize_detail(body)
    # 原始文本已以 Error 开头（如 "Error executing tool ..."）时不重复加前缀
    prefix = "" if body.lstrip().startswith("Error") else "Error: "
    return f"{prefix}{body}\n" + _HINT.format(name=tool_name)


def _tool_name(request: Any) -> str:
    tool = getattr(request, "tool", None)
    tool_call = getattr(request, "tool_call", None) or {}
    return getattr(tool, "name", None) or tool_call.get("name") or "tool"


def build_tool_error_middleware(log_truncate: int = 500) -> ToolErrorMiddleware:
    """主代理与子代理各用一份；``log_truncate`` 取自 ``tools.result_log_truncate``。"""

    def on_error(exc: Exception, request: Any) -> str | None:
        name = _tool_name(request)
        call_id = (getattr(request, "tool_call", None) or {}).get("id", "")
        if not is_recoverable(exc):
            logger.error(
                "🔧❌ 工具出现非预期异常，本轮中断 | 工具: %s | 调用: %s | %s",
                name,
                call_id,
                type(exc).__name__,
                exc_info=exc,
            )
            return None
        logger.warning(
            "🔧⚠️ 工具失败（已回灌模型，对话继续） | 工具: %s | 调用: %s | %s: %s",
            name,
            call_id,
            type(exc).__name__,
            str(exc)[: log_truncate or 500],
        )
        return tool_error_content(exc, name)

    return ToolErrorMiddleware(on_error)
