"""langgraph v3 事件流的适配器：复用 StreamProcessor 的既定逻辑，不重复实现。

v3 把「token 流」与「工具执行」拆到两个投影，本类并发消费并翻译：
  - ``run.messages`` → 每个 LLM 调用的 text/reasoning 增量，逐条转成
    ``AIMessageChunk`` 交给 ``StreamProcessor.handle("messages", ...)``；
  - ``run.updates``  → 节点更新（权威 ``AIMessage.tool_calls`` / usage、
    ``ToolMessage`` 工具结果），原样交给 ``handle("updates", ...)``。

这样 step / 思考 / 工具 / 用量 的语义全部沿用 StreamProcessor，本类只做投影
到 ``(mode, data)`` 的翻译。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessageChunk
from langgraph.stream.transformers import UpdatesTransformer

from .stream_processor import StreamProcessor

_DONE = object()


class V3StreamRunner:
    """驱动 v3 run 并把两个投影合流成 StreamProcessor 的事件。"""

    def __init__(self, proc: StreamProcessor):
        self.proc = proc

    async def events(self, graph, graph_input, config) -> AsyncIterator[dict]:
        """异步生成器：产出 ``proc.handle`` 的事件（与旧链路完全一致）。"""
        # v3 默认只注册 values/messages/lifecycle/subgraph；工具结果需要 updates。
        async with await graph.astream_events(
            graph_input, config, version="v3", transformers=[UpdatesTransformer]
        ) as run:
            queue: asyncio.Queue = asyncio.Queue()
            producers = [
                asyncio.create_task(self._pump_messages(run, queue)),
                asyncio.create_task(self._pump_updates(run, queue)),
            ]
            pending = len(producers)
            try:
                while pending:
                    item = await queue.get()
                    if item is _DONE:
                        pending -= 1
                    else:
                        yield item
            finally:
                for task in producers:
                    task.cancel()
                await asyncio.gather(*producers, return_exceptions=True)

    async def _pump_messages(self, run, queue: asyncio.Queue) -> None:
        try:
            async for stream in run.messages:
                async for event in stream:
                    for ev in self._translate(event):
                        await queue.put(ev)
        finally:
            await queue.put(_DONE)

    async def _pump_updates(self, run, queue: asyncio.Queue) -> None:
        try:
            async for update in run.updates:
                for ev in self.proc.handle("updates", update):
                    await queue.put(ev)
        finally:
            await queue.put(_DONE)

    def _translate(self, event: Any) -> list[dict]:
        """把一条 v3 内容块增量翻译成 token 级 ``messages`` 事件。"""
        if not isinstance(event, dict) or event.get("event") != "content-block-delta":
            return []
        delta = event.get("delta")
        if not isinstance(delta, dict):
            delta = self._legacy_delta(event.get("content_block"))
        if not delta:
            return []
        kind = delta.get("type")
        if kind == "text-delta":
            piece = delta.get("text") or ""
            chunk = AIMessageChunk(content=piece)
        elif kind == "reasoning-delta":
            piece = delta.get("reasoning") or ""
            chunk = AIMessageChunk(
                content="", additional_kwargs={"reasoning_content": piece}
            )
        else:
            return []
        return self.proc.handle("messages", (chunk, {})) if piece else []

    @staticmethod
    def _legacy_delta(block: Any) -> dict | None:
        """兼容旧 ``content_block`` 形状（text / reasoning）。"""
        if not isinstance(block, dict):
            return None
        if block.get("type") == "text":
            return {"type": "text-delta", "text": block.get("text", "")}
        if block.get("type") == "reasoning":
            return {"type": "reasoning-delta", "reasoning": block.get("reasoning", "")}
        return None
