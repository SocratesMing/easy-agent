"""SSE 生产统一入口（C3）。

四个后端流式生产者（chat 编排器、`api/chat.py` 的孤立帧、专家拒答流、知识适配器）
历史上各自手写 ``format_sse({"type": ...})``。本模块把它们收敛到一个入口：

- :class:`SSEEmitter`：绑定默认 ``session_id``，提供 ``emit`` / 快捷事件方法；
- :func:`sse_stream`：把「事件 dict 的同步/异步可迭代对象」转成 frame 流。

约束（与 ``services/sse.py`` 的契约一致）：

- **不改变 wire 格式**：``frame`` 委托 ``format_sse``，逐字节等价；
- **不改变字段顺序**：dict 插入序即序列化序，快捷方法按迁移前字面量键序构造；
- **不自动补终态**：``done`` / ``error`` / ``approval_required`` 由生产者决定何时发送，
  自动补帧会改变知识适配器透传的事件序列；
- **默认不校验**：``validate`` 仅测试/调试开启，生产路径不因新校验抛异常。
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from typing import Any

from .sse import SSEEvent, format_sse, validate_event


class SSEEmitter:
    """事件 frame 生产器：一个流一个实例（或复用无状态默认值）。"""

    __slots__ = ("_session_id", "_validate")

    def __init__(
        self, session_id: str | None = None, *, validate: bool = False
    ) -> None:
        self._session_id = session_id
        self._validate = validate

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def frame(self, payload: Mapping[str, Any]) -> str:
        """序列化任意事件 dict（processor 等模块产出的 payload 走这里）。"""
        data = dict(payload)
        if self._validate:
            validate_event(data)
        return format_sse(data)

    def emit(self, event_type: str, /, **fields: Any) -> str:
        """统一入口：``type`` 固定在前，``fields`` 保持调用方给出的顺序。"""
        return self.frame({"type": event_type, **fields})

    # ── 高频事件快捷方法（键序与迁移前字面量逐键一致）──────────────────

    def start(self, session_id: str | None = None) -> str:
        return self.emit(
            SSEEvent.START, session_id=self._require_session_id(session_id)
        )

    def content(self, content: str, *, step: int | None = None) -> str:
        fields: dict[str, Any] = {"content": content}
        if step is not None:
            fields["step"] = step
        return self.emit(SSEEvent.CONTENT, **fields)

    def content_end(self, content: str = "") -> str:
        return self.emit(SSEEvent.CONTENT_END, content=content)

    def error(self, content: str) -> str:
        return self.emit(SSEEvent.ERROR, content=content)

    def done(self, *, session_id: str | None = None, **fields: Any) -> str:
        return self.emit(
            SSEEvent.DONE,
            session_id=self._require_session_id(session_id),
            **fields,
        )

    def approval_required(
        self, *, thread_id: str, action_requests: Any, **fields: Any
    ) -> str:
        """HITL 中断帧：两个必填字段在 extra 之前；特殊顺序用 ``emit`` 显式传参。"""
        return self.emit(
            SSEEvent.APPROVAL_REQUIRED,
            thread_id=thread_id,
            action_requests=action_requests,
            **fields,
        )

    def knowledge_evidence(
        self, evidence: Any = None, warnings: Any = None
    ) -> str | None:
        """知识证据帧；证据与告警都为空时不发（与 ``KnowledgeChatContext`` 一致）。"""
        if not evidence and not warnings:
            return None
        return self.emit(
            SSEEvent.KNOWLEDGE_EVIDENCE,
            evidence=evidence or [],
            warnings=warnings or [],
        )

    def _require_session_id(self, session_id: str | None) -> str:
        resolved = self._session_id if session_id is None else session_id
        if resolved is None:
            raise ValueError(
                "start/done 事件需要 session_id：请在构造 SSEEmitter 时或调用参数中提供"
            )
        return resolved


async def sse_stream(
    events: AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    emitter: SSEEmitter | None = None,
) -> AsyncIterator[str]:
    """把事件 dict 流转成 SSE frame 流；不补终态帧、不吞异常。"""
    active = emitter or SSEEmitter()
    if isinstance(events, (str, bytes, bytearray)):
        raise TypeError("sse_stream 需要事件 dict 的可迭代对象，不能传字符串/字节")
    if isinstance(events, AsyncIterable):
        async for payload in events:
            yield active.frame(payload)
        return
    for payload in events:
        yield active.frame(payload)


__all__ = ["SSEEmitter", "sse_stream"]
