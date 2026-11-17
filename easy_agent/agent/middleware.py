"""Agent 自定义中间件装配。

从 ``EasyAgent._build_middleware`` 抽出的纯函数：只依赖 ``config`` 与
``max_steps``，不读 ``self``。顺序：TodoList → ToolProgress → ToolError →
ModelCallLimit → ToolCallLimit → ApprovalAudit。
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


def build_agent_middleware(config, max_steps, *, approval_mode: str | None = None) -> list:
    """构建自定义中间件列表。

    Args:
        config: 应用配置对象（读取 ``tools.result_log_truncate`` 与 ``hitl.tools``）。
        max_steps: Agent 最大步数，用于推导 Model/Tool 调用上限。
        approval_mode: 用户级审批模式（``manual`` / ``auto``）。只给审批审计中间件用；
            ``None`` 按 ``manual`` 处理（不打印「跳过人工审批」）。

    Returns:
        按固定顺序排列的中间件列表。
    """
    from langchain.agents.middleware import TodoListMiddleware

    from easy_agent.agent.tool_errors import build_tool_error_middleware  # 延迟导入：导入期重依赖：避免加载 langchain/deepagents 等重依赖

    middleware = []
    # 0.7 起 write_todos 需要显式 TodoListMiddleware（0.6.8 是默认内置）；
    # 官方默认栈若已提供则不再重复添加。
    if not framework_provides_todolist():
        middleware.append(TodoListMiddleware())
    # 放在 ToolError **之前**：先定义的中间件在最外层，只有这样才能拿到
    # ToolErrorMiddleware 转换后的 ToolMessage（异常已变成错误文本），
    # 从而对「空结果」补终止信号。错误文本自带重试提示，这里会跳过不重复加。
    from easy_agent.agent.tool_progress import ToolProgressMiddleware  # 延迟导入：导入期重依赖：避免加载 langchain/deepagents 等重依赖
    from easy_agent.bootstrap.config_models import (  # 阈值默认值的唯一来源，不在本文件重写数字
        DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT,
        DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT,
        DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT,
        DEFAULT_TOOL_PROGRESS_FUSE_LIMIT,
        DEFAULT_TOOL_PROGRESS_LOW_VALUE_LIMIT,
        DEFAULT_TOOL_PROGRESS_LOW_VALUE_ROWS,
        DEFAULT_TOOL_PROGRESS_STOP_LIMIT,
        DEFAULT_TOOL_PROGRESS_TOOL_CALL_BUDGET,
    )

    agent_cfg = getattr(config, "agent", None)
    middleware.append(
        ToolProgressMiddleware(
            enabled=bool(getattr(agent_cfg, "tool_progress_enabled", True)),
            # 九个阈值都取自 config.yaml 的 agent 段（AgentConfig.tool_progress_*），
            # 这里 getattr 的第二个参数只是「配置对象没有该属性时」的兜底
            # （测试用 SimpleNamespace 构造配置），值来自 config_models 的常量。
            # 全部键都进图缓存指纹（graph_cache.config_fingerprint），
            # 改配置会重建图，不会沿用旧中间件实例。
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
            # 熔断三参数（2026-11-17）：同指纹空结果熔断线、单工具调用预算、
            # 低价值行数阈值与连续线。改值必须进图缓存指纹（graph_cache）。
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
        )
    )
    middleware.append(
        # DA-12：官方 ToolErrorMiddleware，只把可恢复的工具错误回灌模型。
        # D2：middleware 进共享编译图，不绑定 session_id。
        build_tool_error_middleware(
            log_truncate=getattr(config.tools, "result_log_truncate", 500)
        )
    )
    # 步数/调用超限的处理（2026-11-17 由 "error" 改为 "end"）：循环类问题烧满
    # 步数时，用户拿到的是框架注入的「已达上限」说明并正常收尾，前端不再卡
    # 「正在思考」、不再报 SSE error，会话可继续发下一条消息。循环本身已由
    # ToolProgressMiddleware 的熔断在远低于上限处拦截，这里是最后防线。
    middleware.extend(build_call_limit_middleware(max_steps, exit_behavior="end"))
    # 审批审计：auto 模式下框架不装 HumanInTheLoopMiddleware，靠它补
    # 「用户已选择自动审批，跳过人工审批」日志。名单与 build_interrupt_config
    # 同源（config.hitl.tools），名单外的只读工具本来就不进审批，不该记日志。
    from easy_agent.agent.approval_audit import ApprovalAuditMiddleware  # 延迟导入：导入期重依赖：避免加载 langchain/deepagents 等重依赖

    hitl_cfg = getattr(config, "hitl", None)
    middleware.append(
        ApprovalAuditMiddleware(
            approval_mode=approval_mode or "",
            approval_tools=getattr(hitl_cfg, "tools", None),
        )
    )
    return middleware


def build_call_limit_middleware(max_steps, *, exit_behavior: str) -> list:
    """模型 / 工具调用上限：模型 ``max_steps`` 次、工具 ``4 * max_steps`` 次（每次 run）。

    主代理与子代理都用 ``exit_behavior="end"``——超限时注入一条「已达调用上限」
    的消息正常收尾，而不是让异常打断整轮对话；主代理的超限收尾经 SSE 正常 done，
    子代理的超限结果经 ``task`` 工具交回主代理（DA-10）。每次调用都返回新实例，
    各代理栈互不共享。
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
