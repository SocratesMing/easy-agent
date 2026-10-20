"""流事件投影（C2 拆分第一步）：chunk/状态 → 内部事件与持久化结构。

本模块是**纯函数**集合：只做数据整形，不依赖数据库、Agent 实例或 SSE 序列化，
因此可以直接单测。编排器（``orchestrator``）负责调用顺序，``streaming`` 包
``__init__`` 与同目录 ``streaming.py`` 兼容垫片负责对外重导出。

对应审计项：C2（``services/streaming.py`` 1482 行单体拆分）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage

# 各 provider 常见模型的上下文窗口（仅在 config / model.profile 都取不到时兜底）。
_MODEL_CONTEXT_LIMITS = {
    "deepseek": 131072,
    "gpt-4": 131072,
    "gpt-4o": 131072,
    "gpt-4-turbo": 131072,
    "gpt-3.5": 16385,
    "claude": 200000,
    "qwen": 131072,
    "glm": 131072,
    "minimax": 131072,
    "moonshot": 131072,
    "yi": 131072,
}


def get_model_context_limit(model_instance) -> int | None:
    """按模型名猜上下文窗口；取不到时返回 None（调用方继续兜底）。"""
    if model_instance is None:
        return None
    model_name = ""
    if hasattr(model_instance, "model_name"):
        model_name = model_instance.model_name or ""
    elif hasattr(model_instance, "model"):
        model_name = model_instance.model or ""
    if not model_name:
        return None
    model_lower = model_name.lower()
    for prefix, limit in _MODEL_CONTEXT_LIMITS.items():
        if prefix in model_lower:
            return limit
    return None


def extract_file_paths_from_command(command: str) -> list[str]:
    """从删除命令中提取文件路径列表。

    支持复合命令（如 ``ls ... && touch ... && rm ...``），
    只提取 rm/rmdir/unlink/shred 部分的文件路径。
    """
    if not command:
        return []
    # 按复合命令分隔符拆分
    parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", command)
    paths = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 只处理删除类命令
        rm_match = re.match(r"^(rm|rmdir|unlink|shred)\s+", part)
        if not rm_match:
            continue
        # 移除命令名
        cleaned = part[rm_match.end():]
        # 移除 flags: -rf, -r, -f 等
        cleaned = re.sub(r"\s+-[rRfFilPdV]+\s*", " ", cleaned)
        cleaned = re.sub(r"^-[rRfFilPdV]+\s*", "", cleaned)
        # 提取路径（支持引号）
        for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'|(\S+)', cleaned):
            path = m.group(1) or m.group(2) or m.group(3)
            if path and not path.startswith("-") and path not in (
                "2>&1",
                "2>/dev/null",
                ">/dev/null",
            ):
                paths.append(path)
    return paths


def build_action_requests_payload(
    action_requests: list, pending_tc_ids: list, config_map: dict
) -> list[dict]:
    """把 LangGraph HITL 的 action_requests 投影为前端 ``approval_required`` 载荷。"""
    return [
        {
            "tool_call_id": pending_tc_ids[i] if i < len(pending_tc_ids) else "",
            "tool_name": ar.get("name", ""),
            "arguments": ar.get("args", {}),
            "description": ar.get("description", ""),
            "allowed_decisions": config_map.get(ar.get("name", ""), {}).get(
                "allowed_decisions", ["approve", "edit", "reject"]
            ),
            "file_paths": extract_file_paths_from_command(
                ar.get("args", {}).get("command", "")
            ),
        }
        for i, ar in enumerate(action_requests)
    ]


def mark_hitl_pending(
    partial_msg: dict,
    pending_tc_ids: list,
    action_requests: list,
    config_map: dict,
    thread_id: str,
) -> list:
    """将 HITL 中断触发的工具调用标记为「待审批」并附加可持久化的审批信息。

    在 tool_calls 与 blocks 上同步写入 approval_status / pending_approval /
    file_paths（前端历史渲染优先读 blocks，只标 tool_calls 会导致切回历史会话
    时审批徽章与按钮丢失）。同时在消息上写入 pending_approval（thread_id +
    操作详情），使前端从历史会话恢复时能重建审批 UI 并继续执行。

    Returns:
        action_requests 负载列表（同时用于下发 approval_required 事件）。
    """
    action_requests_payload = build_action_requests_payload(
        action_requests, pending_tc_ids, config_map
    )
    _apply_pending_marks(partial_msg, action_requests_payload, pending_tc_ids, thread_id)
    return action_requests_payload


def _apply_pending_marks(
    partial_msg: dict, payload: list[dict], pending_tc_ids, thread_id: str
) -> None:
    """在 tool_calls / blocks 上标「待审批」，并把完整审批载荷挂到消息上（供历史恢复）。"""
    pending_ids = {tid for tid in pending_tc_ids if tid}
    _pending_file_paths = {
        ar["tool_call_id"]: ar.get("file_paths", [])
        for ar in payload
        if ar.get("tool_call_id")
    }
    for tc in partial_msg.get("tool_calls") or []:
        if tc.get("tool_call_id") in pending_ids:
            tc["approval_status"] = "pending"
            if tc.get("tool_call_id") in _pending_file_paths:
                tc["file_paths"] = _pending_file_paths[tc["tool_call_id"]]
    for b in partial_msg.get("blocks") or []:
        if (
            b.get("type") == "tool_call"
            and b.get("tool_call_id") in pending_ids
        ):
            b["approval_status"] = "pending"
            b["pending_approval"] = True
            if b.get("tool_call_id") in _pending_file_paths:
                b["file_paths"] = _pending_file_paths[b["tool_call_id"]]
    partial_msg["pending_approval"] = {
        "thread_id": thread_id,
        "action_requests": payload,
    }


def match_action_tool_call_ids(
    state_messages: list, action_requests: list, used: set[str] | None = None
) -> list[str]:
    """按「工具名 + 参数」把审批操作对到主代理最后一轮的 tool_call_id，对不上为 ""。

    HumanInTheLoopMiddleware 的 action_requests 直接取自 AIMessage.tool_calls，参数
    原样一致。只按工具名对齐会出错：``when`` 谓词放行的同名调用（如普通 ``ls`` 的
    execute）也会被算进去，按下标错位到别的调用上；子代理内部触发的审批在主代理
    消息里根本没有对应调用，必须返回 ""，交给前端的独立审批面板展示。
    """
    used = set() if used is None else used
    last = next(
        (
            msg
            for msg in reversed(state_messages or [])
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None)
        ),
        None,
    )
    ids: list[str] = []
    for action in action_requests:
        found = ""
        for tc in (last.tool_calls if last is not None else []):
            tid = tc.get("id") or ""
            if (
                tid
                and tid not in used
                and tc.get("name") == action.get("name")
                and (tc.get("args") or {}) == (action.get("args") or {})
            ):
                found = tid
                used.add(tid)
                break
        ids.append(found)
    return ids


def mark_hitl_pending_interrupts(
    partial_msg: dict, interrupts: list, state_messages: list, thread_id: str
) -> list[dict]:
    """多中断版 ``mark_hitl_pending``：下发并持久化**全部**待审批中断（DA-02）。

    每条操作额外带 ``interrupt_id`` 与 ``action_index``（在所属中断里的下标），前端
    据此按中断作答；改造前只取第一个中断，其余中断对用户不可见却会被一并批准。
    """
    used: set[str] = set()
    payload: list[dict] = []
    for interrupt in interrupts:
        config_map = {
            cfg.get("action_name"): cfg for cfg in (interrupt.review_configs or [])
        }
        tc_ids = match_action_tool_call_ids(state_messages, interrupt.action_requests, used)
        entries = build_action_requests_payload(interrupt.action_requests, tc_ids, config_map)
        for index, entry in enumerate(entries):
            entry["interrupt_id"] = interrupt.interrupt_id
            entry["action_index"] = index
        payload.extend(entries)
    _apply_pending_marks(
        partial_msg, payload, [entry["tool_call_id"] for entry in payload], thread_id
    )
    return payload


def pending_tool_call_ids_for_approval(
    state_messages: list, approval_tool_names: set[str]
) -> list[str]:
    """从图状态消息里取「需要审批」的工具调用 id（按工具名匹配）。

    关键：只取需要审批的工具（与 action_requests 按 tool_name 匹配），而非
    AIMessage 的全部 tool_calls。否则同一 AIMessage 里无需审批的工具也会被标
    pending，且 resume 时 decisions 数量对不上 -> 永远显示「待审批」。
    """
    for msg in reversed(state_messages or []):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            return [
                tc.get("id", "")
                for tc in msg.tool_calls
                if tc.get("name", "") in approval_tool_names
            ]
    return []


@dataclass
class ResumeSnapshot:
    """HITL 恢复流的初始状态（从 DB 里最后一条 assistant 消息投影而来）。"""

    accumulated_response: str = ""
    accumulated_thinking: str = ""
    blocks: list = field(default_factory=list)
    tool_call_records: list = field(default_factory=list)
    current_step: int = 0
    pending_tool_call_ids: list[str] = field(default_factory=list)
    #: 持久化的 approval_required 载荷（含 interrupt_id / action_index / tool_call_id）
    pending_action_requests: list[dict] = field(default_factory=list)


def load_resume_snapshot(messages: list[dict] | None) -> ResumeSnapshot:
    """从持久化会话消息中恢复 resume 流的累计状态。"""
    snapshot = ResumeSnapshot()
    for msg in reversed(messages or []):
        if msg.get("role") != "assistant":
            continue
        snapshot.accumulated_response = msg.get("content", "") or ""
        snapshot.accumulated_thinking = msg.get("thinking", "") or ""
        snapshot.blocks = list(msg.get("blocks") or [])
        for tc in msg.get("tool_calls") or []:
            snapshot.tool_call_records.append(
                [
                    tc.get("tool_name", ""),
                    tc.get("tool_call_id", ""),
                    tc.get("arguments", {}),
                    tc.get("result", ""),
                    tc.get("success", True),
                    tc.get("duration", 0),
                    tc.get("step", 0),
                    tc.get("approval_status"),
                ]
            )
        usage = msg.get("usage") or {}
        snapshot.current_step = usage.get("step_count", 0)
        # 收集历史中已被标记为「待审批」的工具调用（HITL 中断），供后续写回最终决策。
        # 关键：HITL 中断发生在工具「决定调用但尚未执行」时，此时 tool_call_records
        # 尚未生成（要等 ToolMessage 即工具执行后才 append），因此 message.tool_calls
        # 不含该中断工具，approval_status="pending" 只落在 blocks 上。
        # 故必须从 blocks 取 pending id，tool_calls 仅作补充，否则 pending_tool_call_ids
        # 恒为空 -> 决策不应用 -> 历史永远显示「待审批」。
        seen_pending: set[str] = set()
        for b in msg.get("blocks") or []:
            if b.get("type") == "tool_call" and b.get("approval_status") == "pending":
                tid = b.get("tool_call_id", "")
                if tid and tid not in seen_pending:
                    seen_pending.add(tid)
                    snapshot.pending_tool_call_ids.append(tid)
        for tc in msg.get("tool_calls") or []:
            if tc.get("approval_status") == "pending":
                tid = tc.get("tool_call_id", "")
                if tid and tid not in seen_pending:
                    seen_pending.add(tid)
                    snapshot.pending_tool_call_ids.append(tid)
        pending = msg.get("pending_approval")
        if isinstance(pending, dict):
            snapshot.pending_action_requests = [
                ar for ar in (pending.get("action_requests") or []) if isinstance(ar, dict)
            ]
        break
    return snapshot


def last_user_message(messages: list[dict] | None) -> str:
    """取会话中最后一条 user 消息的正文（HITL 恢复后生成记忆用）。"""
    result = ""
    for message in messages or []:
        if message.get("role") == "user":
            result = message.get("content", "") or ""
    return result


def decision_status_map(pending_tool_call_ids: list, decisions: list) -> dict[str, str]:
    """把审批决策映射为 ``{tool_call_id: approved|rejected}``。"""
    status_by_id: dict[str, str] = {}
    for index, tc_id in enumerate(pending_tool_call_ids):
        if index >= len(decisions):
            break
        decision_item = decisions[index] or {}
        decision_type = (
            decision_item.get("type") if isinstance(decision_item, dict) else None
        )
        status_by_id[tc_id] = "approved" if decision_type == "approve" else "rejected"
    return status_by_id


def decision_status_by_interrupt(
    answered: dict, pending_action_requests: list[dict]
) -> dict[str, str]:
    """按 ``(interrupt_id, action_index)`` 把实际作答映射回 ``{tool_call_id: approved|rejected}``。

    只覆盖本次作答的中断；仍挂起的中断不改状态。改造前落库的记录没有 interrupt_id，
    返回空 dict，调用方退回 :func:`decision_status_map` 的旧口径。
    """
    tool_call_by_slot = {
        (ar.get("interrupt_id"), ar.get("action_index")): ar.get("tool_call_id")
        for ar in pending_action_requests or []
        if ar.get("tool_call_id")
    }
    status_by_id: dict[str, str] = {}
    for interrupt_id, decisions in (answered or {}).items():
        for index, decision in enumerate(decisions or []):
            tool_call_id = tool_call_by_slot.get((interrupt_id, index))
            if tool_call_id:
                decision_type = decision.get("type") if isinstance(decision, dict) else None
                status_by_id[tool_call_id] = (
                    "approved" if decision_type == "approve" else "rejected"
                )
    return status_by_id


def apply_decision_status_to_records(records: list, status_by_id: dict[str, str]) -> None:
    """把审批决策写回工具调用记录（原地修改，records 元素需为 list）。"""
    if not status_by_id:
        return
    for rec in records:
        rec_id = rec[1] if len(rec) > 1 else ""
        if rec_id in status_by_id:
            while len(rec) < 8:
                rec.append(None)
            rec[7] = status_by_id[rec_id]


def apply_decision_status_to_blocks(blocks: list, status_by_id: dict[str, str]) -> None:
    """把审批决策写回 blocks，并移除 pending_approval 标记（原地修改）。"""
    if not status_by_id:
        return
    for block in blocks:
        if (
            block.get("type") == "tool_call"
            and block.get("tool_call_id") in status_by_id
        ):
            block["approval_status"] = status_by_id[block["tool_call_id"]]
            block.pop("pending_approval", None)


def merge_tool_call_records(records: list) -> list:
    """按 tool_call_id 去重并合并审批状态（保留有结果的记录）。

    恢复流重跑工具时会再次 append 同 tool_call_id 的记录（DB 加载的 + 重跑时
    新增的），去重后避免最终消息出现重复工具卡，且审批标记不被无状态的新记录
    冲掉。
    """
    merged: dict[str, list] = {}
    for rec in records:
        tid = rec[1] if len(rec) > 1 else ""
        base = merged.get(tid)
        if base is None:
            merged[tid] = list(rec)
        else:
            chosen = list(rec) if rec[3] else base
            other = base if rec[3] else rec
            if len(chosen) < 8:
                chosen = chosen + [None] * (8 - len(chosen))
            other_status = other[7] if len(other) > 7 else None
            if chosen[7] is None and other_status is not None:
                chosen[7] = other_status
            merged[tid] = chosen
    return list(merged.values())


def finalize_blocks(blocks: list, status_by_id: dict[str, str] | None = None) -> list:
    """恢复流收尾：重新应用审批决策并按 tool_call_id 去重 blocks。

    astream 重放可能新建同 tool_call_id/同名 block，且内存 blocks 的
    approval_status 可能在重放中被遗漏。这里：
    1) 重新应用审批决策（已批准的 tc_id -> approval_status=approved，移除
       pending_approval）；
    2) 按 tool_call_id 去重 blocks（保留有结果的，继承非空 approval_status），
       避免历史会话出现重复工具卡或残留 pending（误显示「待审批」）。
    """
    if status_by_id:
        apply_decision_status_to_blocks(blocks, status_by_id)
    final_blocks: list = []
    seen_tc_blocks: dict = {}
    for block in blocks:
        if block.get("type") == "tool_call":
            tid = block.get("tool_call_id", "")
            if tid and tid in seen_tc_blocks:
                existing = seen_tc_blocks[tid]
                # 选取有结果的作为基础，并继承非空审批状态
                if block.get("result") and not existing.get("result"):
                    merged = dict(block)
                    if not merged.get("approval_status") and existing.get(
                        "approval_status"
                    ):
                        merged["approval_status"] = existing["approval_status"]
                    seen_tc_blocks[tid] = merged
                    # 替换列表中的占位
                    idx = final_blocks.index(existing)
                    final_blocks[idx] = merged
                else:
                    if not existing.get("approval_status") and block.get(
                        "approval_status"
                    ):
                        existing["approval_status"] = block["approval_status"]
                continue
            elif tid:
                seen_tc_blocks[tid] = block
                final_blocks.append(block)
            else:
                final_blocks.append(block)
        else:
            final_blocks.append(block)
    return final_blocks


def thinking_duration(blocks: list) -> float:
    """累计 thinking block 的展示耗时（按 step 分段求和）。"""
    return round(
        sum(b.get("duration", 0) or 0 for b in blocks if b.get("type") == "thinking"),
        2,
    )


def build_usage_payload(
    *,
    last_usage: dict,
    context_length,
    auto_compress_tokens,
    last_context_tokens: int,
    step_count: int,
    elapsed_time: float | None = None,
) -> dict:
    """构造 ``done`` / ``token_usage`` 共用的用量载荷。

    展示/持久化口径：**最近一次模型调用**的用量（不跨步累加）；日志里的
    「本轮累计」仍用累加值。``elapsed_time`` 仅恢复流需要（主生成器在 done
    事件外单独带 elapsed_time 字段）。
    """
    input_tokens = last_usage["input_tokens"]
    output_tokens = last_usage["output_tokens"]
    reasoning_tokens = last_usage.get("reasoning_tokens", 0)
    payload = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "context_length": context_length,
        "auto_compress_tokens": auto_compress_tokens,
        "context_tokens": last_context_tokens
        if last_context_tokens > 0
        else input_tokens,
    }
    if elapsed_time is not None:
        payload["elapsed_time"] = round(elapsed_time, 2)
    payload["step_count"] = step_count
    return payload


__all__ = [
    "ResumeSnapshot",
    "apply_decision_status_to_blocks",
    "apply_decision_status_to_records",
    "build_action_requests_payload",
    "build_usage_payload",
    "decision_status_map",
    "extract_file_paths_from_command",
    "finalize_blocks",
    "get_model_context_limit",
    "last_user_message",
    "load_resume_snapshot",
    "mark_hitl_pending",
    "merge_tool_call_records",
    "pending_tool_call_ids_for_approval",
    "thinking_duration",
]
