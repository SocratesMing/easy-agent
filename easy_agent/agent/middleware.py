"""Agent 自定义中间件装配。

从 ``EasyAgent._build_middleware`` 抽出的纯函数：只依赖 ``config`` 与
``max_steps``，不读 ``self``。中间件顺序与拆分前一致：
TodoList → Logging → ModelCallLimit → ToolCallLimit。
"""

import logging

# 纯移动：沿用拆分前的 logger 名称，保持日志路由/过滤行为不变。
logger = logging.getLogger("easy_agent.agent")


def build_agent_middleware(config, max_steps) -> list:
    """构建自定义中间件列表。

    SummarizationMiddleware 由 create_deep_agent 自动添加（使用模型感知的
    默认值 trigger=0.85, keep=0.10），此处无需重复添加，
    否则会导致 AssertionError。

    Args:
        config: 应用配置对象（读取 ``tools.result_log_truncate``）。
        max_steps: Agent 最大步数，用于推导 Model/Tool 调用上限。

    Returns:
        按固定顺序排列的中间件列表。
    """
    # SummarizationMiddleware is automatically added by create_deep_agent
    # with model-aware defaults (trigger=0.85, keep=0.10).
    # No need to add it here - duplicate middleware causes AssertionError.
    from easy_agent.services.logging_middleware import LoggingMiddleware
    from langchain.agents.middleware import TodoListMiddleware

    middleware = [
        # 0.7 起 write_todos 需要显式 TodoListMiddleware（0.6.8 是默认内置）。
        TodoListMiddleware(),
        # D2：middleware 进共享编译图，不能再绑定 session_id（否则同图跨会话
        # 复用会把日志前缀写成首个会话的）。工具异常兜底本身与会话无关。
        LoggingMiddleware(
            result_log_truncate=getattr(
                config.tools, "result_log_truncate", 500
            ),
        )
    ]
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
