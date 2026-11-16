"""HITL 中断状态解析（ARCH-03 Phase 3 第一步抽取）。

这些函数只做纯数据解析，不依赖 DB/SSE/Agent 实例，从 streaming.py 拆出后
便于独立单测，也是后续「编排器 + 纯处理器 + 持久化适配器」拆分的第一块。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
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


def action_name(action: Mapping) -> str:
    """审批载荷里的工具名。

    两套形状并存：HITL 中断原始结构用 ``name`` / ``args``，落库并回放的
    ``pending_action_requests`` 用 ``tool_name`` / ``arguments``（见 ``projector``）。
    """
    return str(action.get("name") or action.get("tool_name") or "")


def action_args(action: Mapping) -> dict:
    """审批载荷里的工具参数（兼容 ``args`` / ``arguments``，取不到返回空字典）。"""
    raw = action.get("args")
    if raw is None:
        raw = action.get("arguments")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _hitl_action_names(hitl_request: dict) -> set[str]:
    """待审批的工具名集合（兼容 ActionRequest 为 dict 或对象两种形态）。"""
    return {
        action_name(ar) if isinstance(ar, Mapping) else str(getattr(ar, "name", ""))
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


#: 旧前端写死的拒绝说明，只适合删除场景；收到它或没带说明时按工具生成（DA-15）。
_LEGACY_REJECT_MESSAGE = "用户拒绝了此操作，请勿重试此删除命令。"


def reject_message(action: dict) -> str:
    """按工具生成回给模型的拒绝说明：说清拒绝了什么，并要求不要换个方式重试。"""
    name = action_name(action)
    args = action_args(action)
    if name == "delete":
        target = args.get("file_path") or args.get("path") or ""
        what = f"删除 {target}" if target else "这次删除"
    elif name == "execute":
        command = str(args.get("command") or "")[:200]
        what = f"执行命令 `{command}`" if command else "执行这条命令"
    elif name == "create_scheduled_task":
        what = "创建这个定时任务"
    elif name == "save_expert":
        what = "保存这个专家"
    elif name == "access_knowledge_original":
        what = "导出知识库原文"
    else:
        what = f"调用 {name}" if name else "这个操作"
    return f"用户拒绝了{what}。不要重试，也不要换别的方式达成同样的效果；如有需要，向用户说明情况或询问下一步。"


def _with_reject_message(action: dict, decision: dict) -> dict:
    if decision.get("type") != "reject":
        return decision
    message = str(decision.get("message") or "").strip()
    if message and message != _LEGACY_REJECT_MESSAGE:
        return decision
    return {**decision, "message": reject_message(action)}


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
    return [
        _with_reject_message(action, decision)
        for action, decision in zip(interrupt.action_requests, decisions)
    ]


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


# ── 人工审批审计（日志用纯数据整形） ──────────────────────────────────────

#: 决策类型 → 日志文案。与 ``_DECISION_TYPES`` 一一对应，新增类型时同步。
_DECISION_LABELS = {
    "approve": "批准执行",
    "edit": "修改参数后执行",
    "reject": "拒绝执行",
    "respond": "直接回复",
}


def decision_label(decision: dict) -> str:
    """决策类型的中文说明（日志用）；未知类型原样返回，不猜。"""
    kind = str(decision.get("type") or "")
    return _DECISION_LABELS.get(kind, kind or "未知决策")


def answered_actions(
    answered: Mapping[str | None, list[dict]] | None,
    pending_actions: Iterable[dict] | None,
) -> list[tuple[dict, dict]]:
    """把「待审批的操作」与「它收到的决策」配对（人工审批日志用）。

    ``pending_actions`` 是审批载荷里的 ``pending_action_requests``（含 ``name`` /
    ``args`` / ``interrupt_id`` / ``action_index``）。按 ``interrupt_id`` 分组后依
    ``action_index`` 与决策对齐；旧前端没有 ``interrupt_id``（``answered`` 的键为
    ``None``）时退回全量操作。决策数少于操作数时只配前 N 个，多出的视为未作答。
    """
    actions = [a for a in (pending_actions or []) if isinstance(a, dict)]
    pairs: list[tuple[dict, dict]] = []
    for interrupt_id, decisions in (answered or {}).items():
        scoped = (
            [a for a in actions if a.get("interrupt_id") == interrupt_id]
            if interrupt_id
            else actions
        )
        scoped.sort(key=lambda a: a.get("action_index") or 0)
        for action, decision in zip(scoped, decisions or []):
            if isinstance(decision, dict):
                pairs.append((action, decision))
    return pairs


def _response_payload(decision: Mapping) -> tuple[dict, str]:
    """Parse one ``respond`` decision into its answers and optional note."""
    raw = decision.get("message")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError("ask_user 回复不是合法 JSON") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("ask_user 回复必须是对象")
    answers = raw.get("answers")
    if not isinstance(answers, Mapping):
        raise ValueError("ask_user 回复缺少 answers 对象")
    note = str(raw.get("note") or "").strip()[:2000]
    return dict(answers), note


def _selected_labels(question: Mapping, answer) -> list[str]:
    """Validate one answer against its question and return display labels."""
    multiple = question.get("multiple") is True
    required = question.get("required") is not False
    options = question.get("options")
    if not isinstance(options, list) or len(options) < 2:
        raise ValueError("ask_user 问题缺少有效候选项")
    labels = {
        str(option.get("id") or ""): str(option.get("label") or "").strip()
        for option in options
        if isinstance(option, Mapping)
    }
    labels.pop("", None)
    if not labels or any(not label for label in labels.values()):
        raise ValueError("ask_user 候选项缺少有效 ID 或名称")

    if multiple:
        if answer is None:
            selected = []
        elif isinstance(answer, list):
            selected = [str(item) for item in answer]
        else:
            raise ValueError("多选问题必须提交选项数组")
        if len(selected) != len(set(selected)):
            raise ValueError("多选答案不能包含重复选项")
    else:
        if isinstance(answer, list):
            raise ValueError("单选问题不能提交选项数组")
        selected = [] if answer in (None, "") else [str(answer)]

    if required and not selected:
        raise ValueError("必选确认项尚未作答")
    unknown = [option_id for option_id in selected if option_id not in labels]
    if unknown:
        raise ValueError("ask_user 回复包含未知选项")
    return [labels[option_id] for option_id in selected]


def _render_ask_user_response(
    arguments: Mapping,
    decision: Mapping,
    start: int,
) -> tuple[list[str], str]:
    """Render one answered ``ask_user`` call using the persisted question contract."""
    answers, note = _response_payload(decision)
    questions = arguments.get("questions")
    if not isinstance(questions, list) or not questions:
        fallback = f"补充说明：{note}" if note else "确认项格式不完整，已提交用户反馈。"
        return [fallback], ""

    question_ids = {
        str(question.get("id") or "")
        for question in questions
        if isinstance(question, Mapping)
    }
    question_ids.discard("")
    unknown_questions = set(answers) - question_ids
    if unknown_questions:
        raise ValueError("ask_user 回复包含未知确认项")

    lines: list[str] = []
    for question in questions:
        if not isinstance(question, Mapping):
            raise ValueError("ask_user 问题结构无效")
        question_id = str(question.get("id") or "")
        title = str(question.get("title") or "").strip()[:120]
        if not question_id or not title:
            raise ValueError("ask_user 问题缺少 ID 或标题")
        labels = _selected_labels(question, answers.get(question_id))
        lines.append(
            f"{start + len(lines)}. {title}：{'、'.join(labels) if labels else '未选择'}"
        )
    return lines, note


def build_plan_confirmation(
    message: Mapping | None,
    decisions: Iterable[Mapping] | None,
) -> str | None:
    """Build the user-visible plan confirmation from server-side pending actions.

    Client-provided confirmation text is intentionally not accepted as evidence.
    The display bubble must be derived from the persisted ``ask_user`` arguments
    and the ``respond`` decisions that will actually resume the graph.
    """
    decision_items = [d for d in (decisions or []) if isinstance(d, Mapping)]
    respond_decisions = [d for d in decision_items if d.get("type") == "respond"]
    if not respond_decisions:
        return None

    pending = message.get("pending_approval") if isinstance(message, Mapping) else None
    actions = pending.get("action_requests") if isinstance(pending, Mapping) else []
    actions = [dict(action) for action in (actions or []) if isinstance(action, Mapping)]
    tagged = any(d.get("interrupt_id") for d in respond_decisions)

    by_slot = {
        (str(d.get("interrupt_id") or ""), int(d.get("action_index") or 0)): d
        for d in respond_decisions
    }
    lines: list[str] = []
    notes: list[str] = []
    matched = False
    for action_index, action in enumerate(actions):
        if action_name(action) != "ask_user":
            continue
        key = (
            str(action.get("interrupt_id") or ""),
            int(action.get("action_index") or action_index),
        )
        decision = by_slot.get(key) if tagged else respond_decisions[0]
        if decision is None:
            continue
        matched = True
        rendered, note = _render_ask_user_response(
            action_args(action), decision, len(lines) + 1
        )
        lines.extend(rendered)
        if note and note not in notes:
            notes.append(note)

    if not matched:
        # Older approvals may not have persisted action arguments. A generic
        # server-owned summary is still safer than trusting client display text.
        for decision in respond_decisions:
            _, note = _response_payload(decision)
            if note and note not in notes:
                notes.append(note)
        lines.append("已提交规划确认，继续执行。")

    lines.extend(f"补充说明：{note}" for note in notes)
    return "\n".join(lines) or None
