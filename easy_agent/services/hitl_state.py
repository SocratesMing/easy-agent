"""HITL 中断状态解析（ARCH-03 Phase 3 第一步抽取）。

这些函数只做纯数据解析，不依赖 DB/SSE/Agent 实例，从 streaming.py 拆出后
便于独立单测，也是后续「编排器 + 纯处理器 + 持久化适配器」拆分的第一块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class HitlDecisionError(ValueError):
    """审批决策与当前待审批中断对不上（interrupt_id / 数量 / 决策类型）。

    批处理 HITL 会先把恢复值存进 checkpoint 再校验，非法决策会让线程卡死在
    「数量不匹配」上；因此在发出 ``Command(resume=...)`` 之前就拒绝。
    """


@dataclass(frozen=True)
class HitlInterrupt:
    """一个待审批的 HumanInTheLoopMiddleware 中断（批处理模式：一次中断可含多个操作）。"""

    interrupt_id: str | None
    action_requests: list[dict] = field(default_factory=list)
    review_configs: list[dict] = field(default_factory=list)


@dataclass
class ResumePlan:
    """``Command(resume=...)`` 的恢复值与每个中断实际收到的决策（已展开、已清理）。"""

    resume: Any
    answered: dict[str | None, list[dict]] = field(default_factory=dict)
    #: 决策未带 interrupt_id（旧前端 / 改造前落库的待审批记录）
    legacy: bool = False


_DECISION_TYPES = frozenset({"approve", "edit", "reject", "respond"})
#: 前端用来路由决策的键，不能原样塞给 HumanInTheLoopMiddleware。
_ROUTING_KEYS = ("interrupt_id", "action_index")


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


def pending_hitl_interrupts(state) -> list[HitlInterrupt]:
    """全部待审批的 HITL 中断（按图状态中的顺序）。

    并行的 ``task`` 子代理各自触发审批时，图里会同时挂着多个中断（每个都有
    自己的 interrupt_id 与 action_requests）；只取 HumanInTheLoopMiddleware
    形态的负载（含 ``action_requests`` 列表），其它类型的 interrupt 不在此列。
    """
    interrupts: list[HitlInterrupt] = []
    for interrupt_id, value in _hitl_interrupt_items(state):
        actions = value.get("action_requests")
        if not isinstance(actions, list):
            continue
        interrupts.append(
            HitlInterrupt(
                interrupt_id=interrupt_id,
                action_requests=[a for a in actions if isinstance(a, dict)],
                review_configs=[
                    c for c in (value.get("review_configs") or []) if isinstance(c, dict)
                ],
            )
        )
    return interrupts


def _clean_decision(decision: dict) -> dict:
    return {k: v for k, v in decision.items() if k not in _ROUTING_KEYS}


def _expand_for_interrupt(interrupt: HitlInterrupt, decisions: list[dict]) -> list[dict]:
    """把决策对齐到该中断的操作数：单个决策作用于整批（与旧 UI 语义一致），否则须一一对应。"""
    count = len(interrupt.action_requests)
    if len(decisions) == 1 and count > 1:
        decisions = [dict(decisions[0]) for _ in range(count)]
    if len(decisions) != count:
        raise HitlDecisionError(
            f"中断 {interrupt.interrupt_id or '-'} 有 {count} 个待审批操作，收到 {len(decisions)} 个决策"
        )
    allowed_by_name = {
        cfg.get("action_name"): cfg.get("allowed_decisions")
        for cfg in interrupt.review_configs
    }
    for action, decision in zip(interrupt.action_requests, decisions):
        decision_type = decision.get("type")
        if decision_type not in _DECISION_TYPES:
            raise HitlDecisionError(f"未知的审批决策类型: {decision_type!r}")
        allowed = allowed_by_name.get(action.get("name"))
        if allowed and decision_type not in allowed:
            raise HitlDecisionError(
                f"工具 {action.get('name')} 不允许决策 {decision_type!r}（允许: {list(allowed)}）"
            )
    return decisions


def plan_resume(interrupts: list[HitlInterrupt], decisions: list[dict]) -> ResumePlan:
    """把前端决策映射到待审批中断（DA-02）。

    - 决策带 ``interrupt_id``：严格按 ID 分组作答；未作答的中断保持挂起，恢复流结束后
      会再次下发 ``approval_required``。未知 ID、数量或类型不符一律报错，不做猜测。
    - 决策不带 ``interrupt_id``（旧前端只展示第一个中断）：只回答第一个中断。改造前
      会把同一组决策套给所有中断，等于替用户批准了界面上没出现过的操作。
    - 没有待审批中断：保持改造前的直传（由框架决定如何处理）。
    """
    if not isinstance(decisions, list) or not decisions or not all(
        isinstance(d, dict) for d in decisions
    ):
        raise HitlDecisionError("decisions 必须是非空的对象列表")

    tagged = [d for d in decisions if d.get("interrupt_id")]
    if tagged and len(tagged) != len(decisions):
        raise HitlDecisionError("decisions 要么全部带 interrupt_id，要么都不带")

    if tagged:
        by_id = {i.interrupt_id: i for i in interrupts if i.interrupt_id}
        groups: dict[str, list[dict]] = {}
        for decision in decisions:
            groups.setdefault(str(decision["interrupt_id"]), []).append(decision)
        resume: dict[str, dict] = {}
        answered: dict[str | None, list[dict]] = {}
        for interrupt_id, group in groups.items():
            interrupt = by_id.get(interrupt_id)
            if interrupt is None:
                raise HitlDecisionError(
                    f"interrupt_id {interrupt_id} 不在待审批列表中（可能已处理或已过期）"
                )
            if all("action_index" in d for d in group):
                group = sorted(group, key=lambda d: d["action_index"])
            expanded = _expand_for_interrupt(interrupt, [_clean_decision(d) for d in group])
            resume[interrupt_id] = {"decisions": expanded}
            answered[interrupt_id] = expanded
        return ResumePlan(resume=resume, answered=answered)

    cleaned = [_clean_decision(d) for d in decisions]
    if not interrupts:
        return ResumePlan(resume={"decisions": cleaned}, legacy=True)
    first = interrupts[0]
    expanded = _expand_for_interrupt(first, cleaned)
    legacy_resume = (
        {first.interrupt_id: {"decisions": expanded}}
        if len(interrupts) > 1 and first.interrupt_id
        else {"decisions": expanded}
    )
    return ResumePlan(
        resume=legacy_resume, answered={first.interrupt_id: expanded}, legacy=True
    )
