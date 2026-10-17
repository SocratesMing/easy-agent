"""HITL 中断状态解析（ARCH-03 Phase 3 第一步抽取）。

这些函数只做纯数据解析，不依赖 DB/SSE/Agent 实例，从 streaming.py 拆出后
便于独立单测，也是后续「编排器 + 纯处理器 + 持久化适配器」拆分的第一块。
"""

from __future__ import annotations


def _hitl_interrupt_items(state) -> list[tuple[str | None, dict]]:
    """提取 LangGraph 状态中的 HITL interrupt ID 与请求负载。"""
    items: list[tuple[str | None, dict]] = []
    for task_item in getattr(state, "tasks", None) or []:
        for interrupt_item in getattr(task_item, "interrupts", None) or []:
            interrupt_id = (
                getattr(interrupt_item, "id", None)
                or getattr(interrupt_item, "interrupt_id", None)
            )
            value = getattr(interrupt_item, "value", None)
            if isinstance(value, dict):
                items.append((interrupt_id, value))
    return items


def _extract_hitl_request(graph_state) -> dict | None:
    """取出待审批的 HITL 中断负载；无中断时返回 None。

    **兼容层**：``state.tasks[*].interrupts[0].value`` 及其内部键
    （``action_requests`` / ``review_configs`` / ``action_name``）属于
    deepagents / langchain 的**内部结构**，不在公开契约内——官方对该中间件只承诺
    ``Command(resume={"decisions": ...})``。业务层统一经本函数取值，
    框架升级时只需调整这一处，而不是散落在两条流式链路里各改一遍。
    """
    if not getattr(graph_state, "next", None):
        return None
    items = _hitl_interrupt_items(graph_state)
    return items[0][1] if items else None


def _hitl_action_names(hitl_request: dict) -> set[str]:
    """待审批的工具名集合（兼容 ActionRequest 为 dict 或对象两种形态）。"""
    return {
        (ar.get("name", "") if isinstance(ar, dict) else getattr(ar, "name", ""))
        for ar in (hitl_request.get("action_requests") or [])
    }


def _hitl_review_config_map(hitl_request: dict) -> dict:
    """把中断负载里的 ``review_configs`` 转为 ``{action_name: config}``。"""
    return {
        cfg.get("action_name"): cfg
        for cfg in (hitl_request.get("review_configs") or [])
    }


def hanging_hitl_tool_count(state) -> int:
    """统计当前所有 HITL interrupt 中挂起的工具调用数量。"""
    return sum(
        len(value.get("action_requests") or [])
        for _, value in _hitl_interrupt_items(state)
    )


def normalize_resume_decisions(
    decisions: list[dict], *, hanging_tool_count: int
) -> list[dict]:
    """将前端的全局审批决策扩展为与挂起工具数一致的决策列表。

    前端目前一次点击只发送一个 decision。LangChain HITL 要求 decision 数量
    必须与 action_requests 数量一致；数量已匹配或语义不明确时不做猜测。
    """
    if hanging_tool_count <= 0 or len(decisions) != 1:
        return decisions
    return [dict(decisions[0]) for _ in range(hanging_tool_count)]
