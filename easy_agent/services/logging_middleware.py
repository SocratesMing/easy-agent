"""集中式智能体日志中间件。

通过 DeepAgents (LangGraph) 中间件钩子统一记录每轮工具执行与模型调用日志，
避免 chat_stream_generator / resume_stream_generator 两套生成器中重复且格式
不一致的日志打印。

说明：面向前端的 SSE 流式事件、block 构建与 DB 持久化仍由 streaming.py 的
生成器负责（它们依赖流式 chunk 上下文与 SSE 响应），本中间件仅承担服务端日志。
"""

import json
import logging
import time

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger("easy-agent.middleware")

_ARG_VALUE_TRUNCATE = 100


def _truncate(text: str, limit: int) -> str:
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _format_args(args) -> str:
    if not args:
        return "{}"
    try:
        if isinstance(args, dict):
            log_args = {}
            for k, v in args.items():
                sv = str(v)
                log_args[k] = _truncate(sv, _ARG_VALUE_TRUNCATE) if len(sv) > _ARG_VALUE_TRUNCATE else v
            return json.dumps(log_args, ensure_ascii=False)
        return _truncate(str(args), 200)
    except Exception:
        return _truncate(str(args), 200)


def _result_text(result) -> str:
    content = getattr(result, "content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", str(item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


class LoggingMiddleware(AgentMiddleware):
    """记录每轮工具执行与模型调用的服务端日志。

    - ``awrap_tool_call`` / ``wrap_tool_call``：记录工具名称、参数、结果、
      是否成功与耗时（与原 chat_stream_generator 的日志格式一致）。
    - ``aafter_model``：记录每轮模型调用的思考长度、正文长度、工具调用数。
    """

    def __init__(self, session_id: str = "", result_log_truncate: int = 500):
        self.sid = session_id[-5:] if session_id else "agent"
        self._truncate_len = result_log_truncate or 500

    def _log_tool(self, request, result, duration: float) -> None:
        call = getattr(request, "tool_call", None)
        tool_name = ""
        args = {}
        if isinstance(call, dict):
            tool_name = call.get("name", "")
            args = call.get("args", {}) or {}
        if not tool_name:
            tool_name = getattr(result, "name", "") or "tool"
        text = _result_text(result)
        status = getattr(result, "status", "") or ""
        success = status != "error" and not text.lstrip().lower().startswith("error")
        logger.info(
            f"[{self.sid}] {'✅' if success else '❌'} {tool_name} | "
            f"参数: {_format_args(args)} | 结果: {_truncate(text, self._truncate_len)} | "
            f"耗时: {duration:.2f}s"
        )

    def wrap_tool_call(self, request, handler):
        t0 = time.time()
        result = handler(request)
        try:
            self._log_tool(request, result, time.time() - t0)
        except Exception as e:
            logger.warning(f"[{self.sid}] 工具日志记录异常: {e}")
        return result

    async def awrap_tool_call(self, request, handler):
        t0 = time.time()
        result = await handler(request)
        try:
            self._log_tool(request, result, time.time() - t0)
        except Exception as e:
            logger.warning(f"[{self.sid}] 工具日志记录异常: {e}")
        return result

    async def aafter_model(self, state, runtime) -> None:
        try:
            messages = state.get("messages", []) if hasattr(state, "get") else []
            if not messages:
                return None
            last = messages[-1]
            content = getattr(last, "content", "") or ""
            if isinstance(content, list):
                content = " ".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            add_kw = getattr(last, "additional_kwargs", {}) or {}
            reasoning = add_kw.get("reasoning_content", "") or ""
            tool_calls = getattr(last, "tool_calls", None) or []
            logger.info(
                f"[{self.sid}] 🧠 模型轮次 | 思考: {len(str(reasoning))} 字符 | "
                f"正文: {len(str(content))} 字符 | 工具调用: {len(tool_calls)}"
            )
        except Exception as e:
            logger.warning(f"[{self.sid}] 模型轮次日志记录异常: {e}")
        return None
