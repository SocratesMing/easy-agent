"""SSE 事件注册表与序列化（唯一事实来源）。

所有后端流式生产者（chat / expert / knowledge）与前端消费者都应引用这里的
事件名；新增事件先加注册表，再改生产/消费端。`format_sse` 是全仓唯一的
SSE 序列化实现。

对应审计项：ARCH-05（4 套生产/3 套消费、事件契约漂移）。
"""

from __future__ import annotations

import json
from typing import Any

# 事件类型（与 docs/sse-events.md 保持一致，CI 的契约测试会校验）。
SSE_EVENT_TYPES: frozenset[str] = frozenset({
    "start",
    "thinking_start",
    "thinking",
    "thinking_end",
    "content",
    "content_end",
    "tool_call",
    "tool_call_delta",
    "tool_result",
    "todo_list",
    "token_usage",
    "knowledge_evidence",
    "approval_required",
    "done",
    "error",
})

class SSEEvent:
    """事件类型常量：生产端必须引用这里，禁止手写字符串字面量。"""

    START = "start"
    THINKING_START = "thinking_start"
    THINKING = "thinking"
    THINKING_END = "thinking_end"
    CONTENT = "content"
    CONTENT_END = "content_end"
    TOOL_CALL = "tool_call"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_RESULT = "tool_result"
    TODO_LIST = "todo_list"
    TOKEN_USAGE = "token_usage"
    KNOWLEDGE_EVIDENCE = "knowledge_evidence"
    APPROVAL_REQUIRED = "approval_required"
    DONE = "done"
    ERROR = "error"


# 必填字段（用于生产端自检；可选字段不在此列）。
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "start": ("session_id",),
    "thinking": ("content",),
    "content": ("content",),
    "tool_call": ("tool_name", "tool_call_id"),
    "tool_result": ("tool_name", "tool_call_id"),
    "done": ("session_id",),
    "error": ("content",),
}


def format_sse(data: dict[str, Any]) -> str:
    """把一个事件 dict 序列化为 SSE frame（`data: {...}\n\n`）。

    这是全仓唯一实现；其它模块必须 `from .sse import format_sse`。
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def validate_event(data: dict[str, Any]) -> None:
    """轻量契约校验：未知事件/缺必填字段时抛 ValueError（仅用于测试与 debug）。"""
    event_type = str(data.get("type", ""))
    if event_type not in SSE_EVENT_TYPES:
        raise ValueError(f"未知 SSE 事件类型: {event_type!r}")
    for field in _REQUIRED_FIELDS.get(event_type, ()):
        if field not in data:
            raise ValueError(f"事件 {event_type!r} 缺少必填字段 {field!r}")


__all__ = ["SSEEvent", "SSE_EVENT_TYPES", "format_sse", "validate_event"]
