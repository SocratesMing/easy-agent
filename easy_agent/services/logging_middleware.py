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
        self._run_start = None  # 本轮对话（run）首次模型调用时间，用于累计总耗时
        self._prev_time = None  # 上一轮模型调用时间，用于检测新 run
        self._pending_round = None  # 待打印的模型轮次摘要（延迟到本轮工具执行完后打印）
        self._executed_tools = 0  # 本轮已执行的工具数
        self._round_seq = 0  # 模型轮次序号，用于 "Step N end" 日志

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
        # 本轮模型轮次日志延迟到所有工具执行完后打印：每执行一个工具计数，
        # 达到本轮工具数（来自 aafter_model 缓存的 round_info）时再打印模型轮次。
        self._executed_tools += 1
        if self._pending_round is not None and self._executed_tools >= self._pending_round["tool_count"]:
            self._flush_pending_round()

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
            tool_calls = getattr(last, "tool_calls", None) or []
            # 当前轮 token 用量（模型消息自带的 usage_metadata）
            usage = getattr(last, "usage_metadata", None) or {}
            in_tok = usage.get("input_tokens", 0) or 0
            out_tok = usage.get("output_tokens", 0) or 0
            tot_tok = usage.get("total_tokens", 0) or (in_tok + out_tok)
            # 本轮对话总耗时基准：以首次模型调用为起点；若与上轮间隔过大（>120s）
            # 视为新一轮对话，自动重置起点，避免 middleware 实例跨请求复用导致计时累积。
            now = time.time()
            if self._run_start is None or (self._prev_time and now - self._prev_time > 120):
                self._run_start = now
            self._prev_time = now
            # 模型轮次摘要延迟到本轮所有工具执行完后打印（见 _log_tool / _flush_pending_round），
            # 使“模型轮次”出现在工具日志之后，更符合阅读顺序。
            if self._pending_round is not None:
                self._flush_pending_round()
            self._round_seq += 1
            round_info = {
                "seq": self._round_seq,
                "content_len": len(str(content)),
                "tool_count": len(tool_calls),
                "in_tok": in_tok,
                "out_tok": out_tok,
                "tot_tok": tot_tok,
            }
            if len(tool_calls) == 0:
                self._print_round(round_info)
            else:
                self._pending_round = round_info
                self._executed_tools = 0
        except Exception as e:
            logger.warning(f"[{self.sid}] 模型轮次日志记录异常: {e}")
        return None

    def _print_round(self, round_info: dict) -> None:
        total_elapsed = round(time.time() - self._run_start, 2) if self._run_start else 0
        logger.info(
            f"[{self.sid}] Step {round_info['seq']} end | 正文: {round_info['content_len']} 字符 | "
            f"工具调用: {round_info['tool_count']} | "
            f"Token(in/out/total): {round_info['in_tok']}/{round_info['out_tok']}/{round_info['tot_tok']} | "
            f"总耗时: {total_elapsed}s"
        )

    def _flush_pending_round(self) -> None:
        if self._pending_round is not None:
            self._print_round(self._pending_round)
            self._pending_round = None
            self._executed_tools = 0
