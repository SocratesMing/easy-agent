"""SSE 事件注册表与序列化（唯一事实来源）。

所有后端流式生产者（chat / expert / knowledge）与前端消费者都应引用这里的
事件名；新增事件先加注册表，再改生产/消费端。`format_sse` 是全仓唯一的
SSE 序列化实现，统一生产入口见 ``services/sse_emitter.py`` 的 ``SSEEmitter``。

对应审计项：ARCH-05（4 套生产/3 套消费、事件契约漂移）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

# 事件类型（与 docs/sse-events.md 保持一致，CI 的契约测试会校验）。


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


@dataclass(frozen=True, slots=True)
class SSEEventSpec:
    """单个事件的字段契约。

    ``required`` 参与 ``validate_event`` 的运行时校验（在改造前 ``_REQUIRED_FIELDS``
    的 7 项上补登 ``approval_required``；该函数仅测试/调试调用）；``optional``
    只作文档与契约测试依据，不参与强校验；``terminal`` 标记该事件会结束一条流
    （``done`` / ``error`` / ``approval_required``）。
    """

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    terminal: bool = False


# 事件注册表：顺序即文档顺序。新增事件必须先在这里登记。
EVENT_SPECS: dict[str, SSEEventSpec] = {
    SSEEvent.START: SSEEventSpec(
        required=("session_id",), optional=("message_id",)
    ),
    SSEEvent.THINKING_START: SSEEventSpec(optional=("step",)),
    SSEEvent.THINKING: SSEEventSpec(
        required=("content",), optional=("full_content", "step")
    ),
    SSEEvent.THINKING_END: SSEEventSpec(optional=("duration", "step")),
    SSEEvent.CONTENT: SSEEventSpec(required=("content",), optional=("step",)),
    SSEEvent.CONTENT_END: SSEEventSpec(optional=("content", "step")),
    SSEEvent.TOOL_CALL: SSEEventSpec(
        required=("tool_name", "tool_call_id"),
        optional=("arguments", "step"),
    ),
    SSEEvent.TOOL_CALL_DELTA: SSEEventSpec(
        optional=("tool_call_id", "delta", "step")
    ),
    SSEEvent.TOOL_RESULT: SSEEventSpec(
        required=("tool_name", "tool_call_id"),
        optional=("result", "success", "duration", "step"),
    ),
    SSEEvent.TODO_LIST: SSEEventSpec(optional=("todos", "step")),
    SSEEvent.TOKEN_USAGE: SSEEventSpec(
        optional=(
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "context_length",
            "auto_compress_tokens",
            "context_tokens",
            "step_count",
            "step",
            "elapsed_time",
        )
    ),
    # docs 曾用 `items` 描述、实现用 `evidence`/`warnings`：两种口径都登记为可选，
    # 保持现网实现不变，避免契约测试误报。
    SSEEvent.KNOWLEDGE_EVIDENCE: SSEEventSpec(
        optional=("evidence", "warnings", "items", "session_id")
    ),
    SSEEvent.APPROVAL_REQUIRED: SSEEventSpec(
        required=("thread_id", "action_requests"),
        optional=("blocks", "review_configs"),
        terminal=True,
    ),
    SSEEvent.DONE: SSEEventSpec(
        required=("session_id",),
        optional=(
            "message_id",
            "finish_reason",
            "elapsed_time",
            "usage",
            "blocks",
            "already_completed",
        ),
        terminal=True,
    ),
    SSEEvent.ERROR: SSEEventSpec(
        required=("content",), optional=("code",), terminal=True
    ),
}

SSE_EVENT_TYPES: frozenset[str] = frozenset(EVENT_SPECS)

# 合法终态：一条流最多以一个终态事件结束。
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    name for name, spec in EVENT_SPECS.items() if spec.terminal
)

# 必填字段（由注册表派生，用于生产端自检；可选字段不在此列）。
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    name: spec.required for name, spec in EVENT_SPECS.items() if spec.required
}


def format_sse(data: dict[str, Any]) -> str:
    """把一个事件 dict 序列化为 SSE frame（`data: {...}\n\n`）。

    这是全仓唯一实现；其它模块必须 `from .sse import format_sse`，或使用
    ``services.sse_emitter.SSEmitter``（内部委托本函数）。
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def parse_sse_frame(frame: str) -> dict[str, Any]:
    """`format_sse` 的逆操作：剥掉可选的 `data:` 前缀后解析 JSON。

    只给「需要读取事件内容」的消费者使用（如知识适配器判断终态、断线重连
    回放）；生产端禁止拼接 frame。
    """
    payload = frame.strip()
    if payload.startswith("data:"):
        payload = payload[len("data:") :].lstrip()
    return json.loads(payload)


def is_terminal_event(event: Mapping[str, Any] | str) -> bool:
    """判断事件（dict 或原始 frame）是否为终态（done/error/approval_required）。"""
    if isinstance(event, str):
        event = parse_sse_frame(event)
    return str(event.get("type", "")) in TERMINAL_EVENT_TYPES


def validate_event(data: dict[str, Any]) -> None:
    """轻量契约校验：未知事件/缺必填字段时抛 ValueError（仅用于测试与 debug）。"""
    event_type = str(data.get("type", ""))
    if event_type not in SSE_EVENT_TYPES:
        raise ValueError(f"未知 SSE 事件类型: {event_type!r}")
    for field in _REQUIRED_FIELDS.get(event_type, ()):
        if field not in data:
            raise ValueError(f"事件 {event_type!r} 缺少必填字段 {field!r}")


__all__ = [
    "EVENT_SPECS",
    "SSEEvent",
    "SSEEventSpec",
    "SSE_EVENT_TYPES",
    "TERMINAL_EVENT_TYPES",
    "format_sse",
    "is_terminal_event",
    "parse_sse_frame",
    "validate_event",
]
