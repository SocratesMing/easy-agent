"""流式消息重建与工具记录累积（纯逻辑，无 DB/SSE/Agent 依赖）。

从 ``services/streaming.py`` 抽出的纯函数，供流式编排器与单测共用：

- 工具调用记录 → 可序列化 dict / 持久化 tuple；
- 持久化会话历史 → LangChain 消息链（旧会话回退到 ``blocks``）；
- 当前流式状态 → 进行中/最终的 assistant 消息字典。

本模块只依赖标准库与 ``langchain_core.messages``；``streaming.py`` 通过
``from .stream_messages import ...`` 重导出，既有导入路径保持不变。
"""

from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def tool_call_records_to_dicts(records: list, result_limit: int | None = None) -> list[dict]:
    """将工具调用记录转为可序列化 dict 列表。

    records 中每个元素为 7 元组：
        [0]=tool_name, [1]=tool_call_id, [2]=arguments, [3]=result,
        [4]=success, [5]=duration, [6]=step
    result_limit 控制 result 字段保留的字符数（None 表示不截断，默认不截断：
    截断会导致下一轮重建上下文时 token 明显缩水）。
    """
    dicts = []
    for tc in records:
        result = tc[3]
        if result_limit is None:
            result = str(result)
        elif isinstance(result, str):
            result = result[:result_limit]
        else:
            result = str(result)[:result_limit]
        dicts.append(
            {
                "tool_name": tc[0],
                "tool_call_id": tc[1],
                "arguments": tc[2],
                "result": result,
                "success": tc[4],
                "duration": tc[5],
                "step": tc[6],
                "approval_status": tc[7] if len(tc) > 7 else None,
            }
        )
    return dicts


def _historical_tool_records(message: dict) -> list[dict]:
    records = message.get("tool_calls") or []
    if records:
        return [record for record in records if isinstance(record, dict)]
    return [
        block
        for block in (message.get("blocks") or [])
        if isinstance(block, dict) and block.get("type") == "tool_call"
    ]


def _tool_call_tuples(blocks: list, *, with_approval: bool = False) -> list[tuple]:
    """从 StreamProcessor 的 blocks 中提取工具调用元组（供持久化使用）。

    chat / resume 两条流式链路原本各自定义了一份完全相同的闭包，现统一到此处。
    ``with_approval=True`` 时在末尾追加第 8 项 ``approval_status``（HITL 恢复流程需要）。
    """
    tuples = []
    for b in blocks:
        if b.get("type") != "tool_call":
            continue
        t = (
            b.get("tool_name", ""), b.get("tool_call_id", ""),
            b.get("arguments", {}), b.get("result", ""),
            b.get("success", True), b.get("duration"), b.get("step", 0),
        )
        tuples.append(t + (b.get("approval_status"),) if with_approval else t)
    return tuples


def _reasoning_kwargs(reasoning: str) -> dict:
    return {"reasoning_content": reasoning} if reasoning else {}


def _thinking_by_step(message: dict) -> dict:
    """DB 按 step 存的思考块：``{step: 思考原文}``，同一 step 的多段按块顺序拼接。"""
    by_step: dict = {}
    for block in message.get("blocks") or []:
        if isinstance(block, dict) and block.get("type") == "thinking" and block.get("content"):
            step = block.get("step") or 0
            by_step[step] = by_step.get(step, "") + str(block["content"])
    return by_step


def _assistant_content_message(message: dict, reasoning: str = "") -> AIMessage:
    """重建历史 assistant 正文消息，思考放在 ``additional_kwargs["reasoning_content"]``。

    思考不拼进正文（``<think>…</think>``）：那样会被当成正文，思考里出现 ``</think>``
    字面量时还会破坏标签配对。出站时由 :class:`ReasoningChatOpenAI` 写成请求里的
    ``reasoning_content`` 字段（见 ``inject_reasoning_content``）。
    """
    return AIMessage(
        content=str(message.get("content") or ""),
        additional_kwargs=_reasoning_kwargs(reasoning),
    )


def turn_message_text(message: str, files: list | None = None, skills: list | None = None) -> str:
    """本轮存进 thread 的用户原话；只传文件 / 只选技能时给一个简短占位，避免空的用户消息。"""
    if (message or "").strip():
        return message
    parts = []
    names = [str(f.get("filename")) for f in files or [] if isinstance(f, dict) and f.get("filename")]
    if names:
        parts.append(f"[上传文件: {', '.join(names)}]")
    if skills:
        parts.append(f"[使用技能: {', '.join(str(s) for s in skills)}]")
    return " ".join(parts) or (message or "")


def build_context_messages(
    history_messages: list[dict],
    current_message: str,
    provider: str = "",
    *,
    current_message_id: str | None = None,
) -> list:
    """将持久化会话历史重建为完整的 LangChain 消息链。

    旧实现只恢复用户/助手正文，导致下一轮模型输入不包含上一轮工具调用与结果。
    这里按 step 恢复 AIMessage(tool_calls=...) 与对应 ToolMessage；无工具记录的
    旧会话回退为正文消息。

    历史思考也随消息回传：每个 step 的思考块放到该 step 的 AIMessage 上，其余 step
    （作答那一步）的思考跟着正文消息；没有分步思考块的旧消息，整段 ``thinking`` 跟着正文。
    ``provider`` 参数不再影响结果，保留以兼容既有调用方。

    ``current_message_id`` 作为本轮 ``HumanMessage`` 的 id，供 ``turn_context`` 定位本轮消息
    （见 ``agent/turn_context.py``）。
    """
    context_messages: list = []

    for message in history_messages:
        if message.get("role") == "user":
            context_messages.append(
                HumanMessage(content=str(message.get("content") or ""))
            )
            continue
        if message.get("role") != "assistant":
            continue

        records = _historical_tool_records(message)
        grouped_records: dict[int, list[dict]] = {}
        step_order: list[int] = []
        for index, record in enumerate(records):
            step = record.get("step") or 0
            if step not in grouped_records:
                grouped_records[step] = []
                step_order.append(step)
            tool_call_id = str(record.get("tool_call_id") or f"historical-{index}")
            tool_name = str(record.get("tool_name") or "tool")
            grouped_records[step].append(
                {
                    "name": tool_name,
                    "args": record.get("arguments") or {},
                    "id": tool_call_id,
                    "record": record,
                }
            )

        thinking_by_step = _thinking_by_step(message)
        has_step_thinking = bool(thinking_by_step)
        for step in step_order:
            tool_calls = grouped_records[step]
            context_messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": item["name"], "args": item["args"], "id": item["id"]}
                        for item in tool_calls
                    ],
                    additional_kwargs=_reasoning_kwargs(thinking_by_step.pop(step, "")),
                )
            )
            for item in tool_calls:
                context_messages.append(
                    ToolMessage(
                        content=str(item["record"].get("result") or ""),
                        tool_call_id=item["id"],
                        name=item["name"],
                    )
                )

        final_reasoning = (
            "".join(thinking_by_step.values())
            if has_step_thinking
            else str(message.get("thinking") or "")
        )
        if str(message.get("content") or "").strip() or message.get("thinking") or final_reasoning:
            context_messages.append(_assistant_content_message(message, final_reasoning))

    context_messages.append(HumanMessage(content=current_message, id=current_message_id))
    return context_messages


def build_assistant_message_dict(
    *,
    content: str,
    thinking: str,
    thinking_duration,
    tool_call_records: list,
    blocks: list,
    input_tokens: int,
    output_tokens: int,
    context_tokens: int,
    elapsed_time: float,
    step_count: int,
    result_limit: int | None = None,
    reasoning_tokens: int = 0,
    context_length: int | None = None,
    fallback_note: str | None = None,
) -> dict:
    """构建用于持久化/返回的 assistant 消息字典（常规结束、HITL 中断、取消等场景共用）。

    ``context_length`` 是本轮所用模型的上下文窗口：会话不记录所用模型（输入框可以逐轮
    切换），会话接口靠它还原占用率的分母；未知时不写这个键。

    ``fallback_note`` 是「这一轮没跑完」的兜底说明（异常兜底会带：超时与其他故障的文案
    不同）。它作为消息的**额外顶层键**落库：消息表只固定 role/content/timestamp，其余键进
    ``extra_data`` JSON，读回时原样 update 回来，所以**不需要改表结构**。默认 ``None`` 时
    不写这个键，正常消息与改动前的 JSON 完全一致。
    """
    _raw_content = content or ""
    # 过滤空/纯空白的 content block，避免持久化与历史会话渲染空正文
    _clean_blocks = [
        b for b in (blocks or [])
        if not (b.get("type") == "content" and not (b.get("content", "") or "").strip())
    ]
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        # 思考 token（output_tokens 的子集，仅用于拆分展示）
        "reasoning_tokens": reasoning_tokens or 0,
        "context_tokens": context_tokens if context_tokens > 0 else input_tokens,
        "elapsed_time": round(elapsed_time, 2),
        "step_count": step_count,
    }
    if context_length:
        usage["context_length"] = int(context_length)
    message = {
        "role": "assistant",
        "content": _raw_content if _raw_content.strip() else "",
        "timestamp": datetime.now().isoformat(),
        "thinking": thinking or None,
        "thinking_duration": thinking_duration or None,
        "tool_calls": tool_call_records_to_dicts(tool_call_records, result_limit) or None,
        "blocks": _clean_blocks or None,
        "usage": usage,
    }
    if fallback_note:
        message["fallback_note"] = fallback_note
    return message


def _partial_message_dict(
    proc,
    elapsed_time: float,
    *,
    with_approval: bool = False,
    fallback_note: str | None = None,
) -> dict:
    """从当前 StreamProcessor 构建「进行中」的 assistant 消息（增量/HITL/取消/异常共用）。

    ``fallback_note`` 仅异常兜底传值：截断时把兜底文案一起落库，前端刷新/切会话后
    仍能显示（前端 ``syncSessionFromServer`` 是整表替换，不落库就会丢）。
    """
    return build_assistant_message_dict(
        content=proc.accumulated_response,
        thinking=proc.accumulated_thinking,
        thinking_duration=None,
        tool_call_records=_tool_call_tuples(proc.blocks, with_approval=with_approval),
        blocks=proc.blocks,
        input_tokens=proc.last_usage.get("input_tokens", 0),
        output_tokens=proc.last_usage.get("output_tokens", 0),
        reasoning_tokens=proc.last_usage.get("reasoning_tokens", 0),
        context_tokens=proc.last_context_tokens,
        elapsed_time=elapsed_time,
        step_count=proc.current_step,
        context_length=getattr(proc, "context_length", None),
        fallback_note=fallback_note,
    )
