"""集中式智能体日志中间件（占位）。

说明：每步的「推理过程 / 工具执行 / 正文 / 结束统计」日志已由
``stream_processor.StreamProcessor`` 统一输出（以 ``stepN`` 开头、编号与前端展示
一致），此处不再重复打印，避免两套计数器（step 与 round_seq 差一）造成日志混淆。

本中间件仅作为 AgentMiddleware 的占位钩子保留，不影响工具执行逻辑
（工具的实际调用由 ``handler`` 完成，本类不做任何副作用）。
"""

from langchain.agents.middleware import AgentMiddleware


class LoggingMiddleware(AgentMiddleware):
    """AgentMiddleware 占位钩子，不输出日志（step 日志见 StreamProcessor）。"""

    def __init__(self, session_id: str = "", result_log_truncate: int = 500):
        self.sid = session_id[-5:] if session_id else "agent"
        self._truncate_len = result_log_truncate or 500

    def wrap_tool_call(self, request, handler):
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        return await handler(request)

    async def aafter_model(self, state, runtime) -> None:
        return None
