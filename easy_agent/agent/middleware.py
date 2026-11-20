"""Agent 自定义中间件装配。

顺序：TodoList → ToolProgress → ThinkingLoop → ToolError →
ModelCallLimit → ToolCallLimit → ApprovalAudit。
摘要中间件需绑定建图时的 model/backend，在 EasyAgent._create_agent 中按同名原位替换。
"""

import logging
from functools import lru_cache

logger = logging.getLogger("easy_agent.agent")


@lru_cache(maxsize=1)
def framework_provides_todolist() -> bool:
    """检查官方默认栈是否已内置 TodoListMiddleware，避免重复挂载。"""
    try:
        import deepagents.graph as graph_module

        return hasattr(graph_module, "TodoListMiddleware")
    except Exception:  # noqa: BLE001
        return False


def build_agent_middleware(config, max_steps, *, approval_mode: str | None = None) -> list:
    """按固定顺序构建 Agent 中间件列表。"""
    from langchain.agents.middleware import TodoListMiddleware

    from easy_agent.agent.tool_errors import build_tool_error_middleware

    middleware = []
    if not framework_provides_todolist():
        middleware.append(TodoListMiddleware())
    # 置于 ToolError 之前（更外层），以便对 ToolError 转换后的空结果补充终止提示。
    from easy_agent.agent.tool_progress import ToolProgressMiddleware
    from easy_agent.bootstrap.config_models import (
        DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT,
        DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT,
        DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT,
        DEFAULT_TOOL_PROGRESS_FUSE_LIMIT,
        DEFAULT_TOOL_PROGRESS_LOW_VALUE_LIMIT,
        DEFAULT_TOOL_PROGRESS_LOW_VALUE_ROWS,
        DEFAULT_TOOL_PROGRESS_SAME_ARGS_TOTAL_LIMIT,
        DEFAULT_TOOL_PROGRESS_STOP_LIMIT,
        DEFAULT_TOOL_PROGRESS_TOOL_CALL_BUDGET,
    )

    agent_cfg = getattr(config, "agent", None)
    middleware.append(
        ToolProgressMiddleware(
            enabled=bool(getattr(agent_cfg, "tool_progress_enabled", True)),
            empty_limit=int(
                getattr(agent_cfg, "tool_progress_empty_limit", None)
                or DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT
            ),
            converge_limit=int(
                getattr(agent_cfg, "tool_progress_converge_limit", None)
                or DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT
            ),
            stop_limit=int(
                getattr(agent_cfg, "tool_progress_stop_limit", None)
                or DEFAULT_TOOL_PROGRESS_STOP_LIMIT
            ),
            empty_hard_limit=int(
                getattr(agent_cfg, "tool_progress_empty_hard_limit", None)
                or DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT
            ),
            fuse_limit=int(
                getattr(agent_cfg, "tool_progress_fuse_limit", None)
                or DEFAULT_TOOL_PROGRESS_FUSE_LIMIT
            ),
            tool_call_budget=int(
                getattr(agent_cfg, "tool_progress_tool_call_budget", None)
                or DEFAULT_TOOL_PROGRESS_TOOL_CALL_BUDGET
            ),
            low_value_rows=int(
                getattr(agent_cfg, "tool_progress_low_value_rows", None)
                or DEFAULT_TOOL_PROGRESS_LOW_VALUE_ROWS
            ),
            low_value_limit=int(
                getattr(agent_cfg, "tool_progress_low_value_limit", None)
                or DEFAULT_TOOL_PROGRESS_LOW_VALUE_LIMIT
            ),
            same_args_total_limit=int(
                getattr(agent_cfg, "tool_progress_same_args_total_limit", None)
                or DEFAULT_TOOL_PROGRESS_SAME_ARGS_TOTAL_LIMIT
            ),
        )
    )
    from easy_agent.agent.thinking_loop import ThinkingLoopMiddleware
    from easy_agent.bootstrap.config_models import (
        DEFAULT_THINKING_LOOP_LIMIT,
        DEFAULT_THINKING_LOOP_MIN_CHARS,
    )

    middleware.append(
        ThinkingLoopMiddleware(
            enabled=bool(getattr(agent_cfg, "thinking_loop_enabled", True)),
            limit=int(
                getattr(agent_cfg, "thinking_loop_limit", None)
                or DEFAULT_THINKING_LOOP_LIMIT
            ),
            min_chars=int(
                getattr(agent_cfg, "thinking_loop_min_chars", None)
                or DEFAULT_THINKING_LOOP_MIN_CHARS
            ),
        )
    )
    middleware.append(
        build_tool_error_middleware(
            log_truncate=getattr(config.tools, "result_log_truncate", 500)
        )
    )
    middleware.extend(build_call_limit_middleware(max_steps, exit_behavior="end"))
    from easy_agent.agent.approval_audit import ApprovalAuditMiddleware

    hitl_cfg = getattr(config, "hitl", None)
    middleware.append(
        ApprovalAuditMiddleware(
            approval_mode=approval_mode or "",
            approval_tools=getattr(hitl_cfg, "tools", None),
        )
    )
    return middleware


def build_call_limit_middleware(max_steps, *, exit_behavior: str) -> list:
    """构建模型（max_steps）与工具（4 * max_steps）的单轮调用上限中间件。"""
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
