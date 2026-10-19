"""Agent 图调用参数的唯一入口：thread_id、recursion_limit、运行期 context（DA-10）。

聊天流、HITL 恢复流、定时任务都经这里构造参数，避免三条路径各写一份而漂移
（改造前只有恢复流设了 ``recursion_limit``，定时任务漏传了 ``context``）。

``recursion_limit`` 只是兜底：主代理的步数预算由 ``ModelCallLimitMiddleware``
（``run_limit = max_steps``）负责，兜底值必须**大于**跑满预算所需的 super-step 数，
否则会抢在调用上限之前抛 ``GraphRecursionError``。一次 model→tools 循环要经过
``model``、``tools`` 以及每个 ``before_model`` / ``after_model`` 钩子节点各一步
（当前装配实测 8 步），所以按编译图的实际节点数计算，而不是写死成 ``2 * max_steps``。
"""

from __future__ import annotations

import weakref
from typing import Any

DEFAULT_MAX_STEPS = 50

#: 读不到编译图节点时（测试替身等）按每轮 10 步估算。
_DEFAULT_STEPS_PER_TURN = 10
#: before_agent / after_agent 钩子、入口与最后一轮模型调用的余量。
_RECURSION_MARGIN = 25

_steps_cache: "weakref.WeakKeyDictionary[Any, int]" = weakref.WeakKeyDictionary()


def steps_per_turn(graph: Any) -> int:
    """一次 model→tools 循环经过的 super-step 数（model + tools + 模型前后钩子节点）。"""
    try:
        cached = _steps_cache.get(graph)
    except TypeError:  # 不可弱引用/不可哈希的替身
        cached = None
    if cached is not None:
        return cached
    try:
        nodes = graph.get_graph().nodes
    except Exception:  # noqa: BLE001 - 替身或上游接口变化时退回估算值
        return _DEFAULT_STEPS_PER_TURN
    hooks = sum(
        1 for name in nodes if str(name).endswith((".before_model", ".after_model"))
    )
    value = 2 + hooks
    try:
        _steps_cache[graph] = value
    except TypeError:
        pass
    return value


def recursion_limit_for(agent: Any) -> int:
    """能容纳 ``max_steps`` 轮完整循环再加余量的递归上限。"""
    max_steps = int(getattr(agent, "max_steps", DEFAULT_MAX_STEPS) or DEFAULT_MAX_STEPS)
    per_turn = steps_per_turn(getattr(agent, "agent", None))
    return per_turn * (max_steps + 1) + _RECURSION_MARGIN


def build_run_config(agent: Any, thread_id: str, **extra: Any) -> dict[str, Any]:
    """``astream`` / ``ainvoke`` 的 config：固定 thread_id 与递归兜底，``extra`` 原样合并。"""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit_for(agent),
        **extra,
    }


def run_context_kwargs(agent: Any) -> dict[str, Any]:
    """共享编译图必须带运行期 ``SessionContext``；没有时（旧调用/替身）不传该关键字。"""
    context = getattr(agent, "session_context", None)
    return {"context": context} if context is not None else {}
