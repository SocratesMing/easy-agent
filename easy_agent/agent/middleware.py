"""Agent 自定义中间件装配。

从 ``EasyAgent._build_middleware`` 抽出的纯函数：只依赖 ``config`` 与
``max_steps``，不读 ``self``。顺序：TodoList → ToolProgress → ToolError →
ModelCallLimit → ToolCallLimit。
摘要中间件不在这里：它要绑定建图时的 model / backend，由 ``EasyAgent._create_agent``
按同名原位替换框架默认项（DA-06，见 ``agent/summarization.py``）。
"""

import inspect
import logging
from functools import lru_cache

# 纯移动：沿用拆分前的 logger 名称，保持日志路由/过滤行为不变。
logger = logging.getLogger("easy_agent.agent")


@lru_cache(maxsize=1)
def framework_provides_todolist() -> bool:
    """官方默认栈是否已经带 ``TodoListMiddleware``。

    deepagents 0.7.x 不再默认内置 ``write_todos``，需要显式添加；如果后续版本
    重新放回默认栈，同一实例会被框架按 name 原位替换，看似无害，但两份
    ``TodoListMiddleware`` 的 state/提示词语义可能随版本漂移。这里以默认栈源码
    是否引用该中间件为准，源码不可读时保持 0.7 行为（继续添加）。
    """
    try:
        import deepagents.graph as graph_module

        return "TodoListMiddleware" in inspect.getsource(graph_module)
    except Exception:  # noqa: BLE001 - 打包环境无源码时按 0.7 行为处理
        return False


def build_agent_middleware(config, max_steps) -> list:
    """构建自定义中间件列表。

    Args:
        config: 应用配置对象（读取 ``tools.result_log_truncate``）。
        max_steps: Agent 最大步数，用于推导 Model/Tool 调用上限。

    Returns:
        按固定顺序排列的中间件列表。
    """
    from langchain.agents.middleware import TodoListMiddleware

    from easy_agent.agent.tool_errors import build_tool_error_middleware

    middleware = []
    # 0.7 起 write_todos 需要显式 TodoListMiddleware（0.6.8 是默认内置）；
    # 官方默认栈若已提供则不再重复添加。
    if not framework_provides_todolist():
        middleware.append(TodoListMiddleware())
    # 放在 ToolError **之前**：先定义的中间件在最外层，只有这样才能拿到
    # ToolErrorMiddleware 转换后的 ToolMessage（异常已变成错误文本），
    # 从而对「空结果」补终止信号。错误文本自带重试提示，这里会跳过不重复加。
    from easy_agent.agent.tool_progress import ToolProgressMiddleware

    middleware.append(
        ToolProgressMiddleware(
            enabled=bool(
                getattr(getattr(config, "agent", None), "tool_progress_enabled", True)
            )
        )
    )
    middleware.append(
        # DA-12：官方 ToolErrorMiddleware，只把可恢复的工具错误回灌模型。
        # D2：middleware 进共享编译图，不绑定 session_id。
        build_tool_error_middleware(
            log_truncate=getattr(config.tools, "result_log_truncate", 500)
        )
    )
    # 超限时显式报错，避免「模型步数用尽但前端以为正常结束」；
    # 错误会经 streaming 的异常分支转成 SSE error。
    middleware.extend(build_call_limit_middleware(max_steps, exit_behavior="error"))
    return middleware


def build_call_limit_middleware(max_steps, *, exit_behavior: str) -> list:
    """模型 / 工具调用上限：模型 ``max_steps`` 次、工具 ``4 * max_steps`` 次（每次 run）。

    主代理用 ``exit_behavior="error"``；子代理用 ``"end"``——超限时子代理以一条
    「已达调用上限」的消息收尾，``task`` 工具把它作为结果交回主代理，而不是让异常
    打断整轮对话（DA-10）。每次调用都返回新实例，各代理栈互不共享。
    """
    try:
        from langchain.agents.middleware import (
            ModelCallLimitMiddleware,
            ToolCallLimitMiddleware,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("未加载 call-limit 中间件: %s", exc)
        return []
    run_limit = max(1, int(max_steps or 50))
    return [
        ModelCallLimitMiddleware(run_limit=run_limit, exit_behavior=exit_behavior),
        ToolCallLimitMiddleware(run_limit=run_limit * 4, exit_behavior=exit_behavior),
    ]
