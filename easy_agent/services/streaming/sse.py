"""SSE 事件注册表与序列化（唯一事实来源）。

所有后端流式生产者（chat / expert / knowledge）与前端消费者都应引用这里的
事件名；新增事件先加注册表，再改生产/消费端。`format_sse` 是全仓唯一的
SSE 序列化实现，统一生产入口见 ``services/streaming/emitter.py`` 的 ``SSEEmitter``。

对应审计项：ARCH-05（4 套生产/3 套消费、事件契约漂移）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from easy_agent.bootstrap.env import env_str

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
    # canonical 字段是 `evidence`/`warnings`（见 Emitter.knowledge_evidence）；
    # `items`/`session_id` 仅为旧消费者向后兼容登记，不保证下发。
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


# ---- 心跳 ---------------------------------------------------------------
# 反向代理（nginx 等）按「上游多久没数据」判空闲超时，默认 proxy_read_timeout
# 只有 60s。模型长思考 / 长工具调用期间流上可能几十秒没有任何事件，代理会直接
# 掐断 TCP：响应头已发出（200），body 却是残缺的 chunked，浏览器报
# ``ERR_INCOMPLETE_CHUNKED_ENCODING 200``。
#
# 心跳用 SSE 注释行（以 ``:`` 开头），前端解析器只处理 ``data: `` 行因此会忽略
# 它；心跳不产生事件、不进 hub 历史、不落 stream_events 表，只在订阅者连接上发。
SSE_HEARTBEAT_ENV = "EASY_SSE_HEARTBEAT_SECONDS"
DEFAULT_SSE_HEARTBEAT_SECONDS = 15.0
SSE_HEARTBEAT_FRAME = ": keep-alive\n\n"


def _configured_heartbeat_seconds() -> float | None:
    """config.yaml ``streaming.sse_heartbeat_seconds``；运行期配置不可用时返回 None。

    惰性 import：本模块被 API 层热路径导入，不能反向依赖 bootstrap 包。
    """
    try:
        from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

        runtime = get_runtime_agent_config() or {}
        streaming = getattr(runtime.get("config"), "streaming", None)
        value = getattr(streaming, "sse_heartbeat_seconds", None)
        return None if value is None else float(value)
    except Exception:  # noqa: BLE001 - 配置不可用（未初始化/测试替身）时回退默认
        return None


def heartbeat_interval_seconds() -> float:
    """心跳间隔（秒）：``EASY_SSE_HEARTBEAT_SECONDS`` > config.yaml > 15 秒；<= 0 关闭。

    环境变量优先，与其它 ``EASY_*`` 开关一致；模板里该键本身就引用同名环境变量，
    因此两种写法结果相同。
    """
    raw = env_str(SSE_HEARTBEAT_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    configured = _configured_heartbeat_seconds()
    if configured is not None:
        return configured
    return DEFAULT_SSE_HEARTBEAT_SECONDS


def format_sse(data: dict[str, Any]) -> str:
    """把一个事件 dict 序列化为 SSE frame（`data: {...}\n\n`）。

    这是全仓唯一实现；其它模块必须 `from .sse import format_sse`，或使用
    ``services.streaming.emitter.SSEmitter``（内部委托本函数）。
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
    "DEFAULT_SSE_HEARTBEAT_SECONDS",
    "SSE_HEARTBEAT_ENV",
    "SSE_HEARTBEAT_FRAME",
    "format_sse",
    "heartbeat_interval_seconds",
    "is_terminal_event",
    "parse_sse_frame",
    "validate_event",
]
