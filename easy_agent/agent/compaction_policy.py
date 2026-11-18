"""自动/手动压缩共用的摘要输入预算及失败判定。"""

from langchain_core.messages import HumanMessage, get_buffer_string

from easy_agent.agent.token_counting import count_context_tokens


TOO_LONG_SUMMARY = "Previous conversation was too long to summarize."
INVALID_SUMMARIES = frozenset({"", "No previous conversation history.", TOO_LONG_SUMMARY})


def require_summary(summary: str) -> str:
    if summary.strip() in INVALID_SUMMARIES:
        raise RuntimeError("摘要模型未返回有效摘要，原上下文未修改")
    return summary


def usable_event(event):
    """旧失败事件不再遮住原文；只检查摘要正文，避免误判正常摘要的引用。"""
    if not isinstance(event, dict):
        return None
    message = event.get("summary_message")
    cutoff = event.get("cutoff_index")
    if not isinstance(message, HumanMessage) or type(cutoff) is not int or cutoff < 0:
        return None
    text = message.text.strip()
    if "<summary>" in text and text.endswith("</summary>"):
        text = text.rsplit("<summary>", 1)[1].removesuffix("</summary>").strip()
    elif text.startswith("Here is a summary"):
        text = text.partition(":")[2].strip()
    return None if text in INVALID_SUMMARIES else event


def _clip_text(text: str, budget: int, *, tail: bool = False) -> str:
    """按实际 counter 二分裁剪，支持没有换行的长工具结果。"""
    low, high = 0, len(text)
    while low < high:
        length = (low + high + 1) // 2
        part = text[-length:] if tail else text[:length]
        if count_context_tokens([HumanMessage(part)]) <= budget:
            low = length
        else:
            high = length - 1
    return (text[-low:] if tail else text[:low]) if low else ""


def summary_input(messages: list, budget: int | None) -> list:
    """先保留用户要求和已有摘要，再把预算给执行记录；不裁改 checkpoint/归档。"""
    full = HumanMessage(get_buffer_string(messages, format="xml"))
    if budget is None or count_context_tokens([full]) <= budget:
        return [full]
    required = [m for m in messages if isinstance(m, HumanMessage)]
    prefix = "User requests and previous summary (verbatim, chronological):\n"
    prefix += get_buffer_string(required, format="xml")
    prefix += "\n\nExecution history (middle may be omitted):\n"
    marker = "\n[... middle of execution history omitted ...]\n"
    reserve = count_context_tokens([HumanMessage(prefix + marker)])
    if reserve >= budget:
        raise RuntimeError("用户问题及已有摘要超过摘要输入预算，原上下文未修改；请增大摘要预算")
    history = get_buffer_string([m for m in messages if not isinstance(m, HumanMessage)], format="xml")
    remaining = budget - reserve
    head = _clip_text(history, remaining // 4)
    tail = _clip_text(history, remaining * 3 // 4, tail=True)
    view = HumanMessage(prefix + head + marker + tail)
    if count_context_tokens([view]) > budget:
        view = HumanMessage(prefix + marker + _clip_text(history, remaining - 32, tail=True))
    if count_context_tokens([view]) > budget:
        raise RuntimeError("摘要输入无法安全限定在预算内，原上下文未修改")
    return [view]
