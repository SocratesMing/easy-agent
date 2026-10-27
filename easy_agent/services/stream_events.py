"""SSE 事件持久化写入器（多副本 Phase 2A）。

``_StreamHub.broadcast`` 仍然同步更新内存历史/订阅者，保证同 Pod 低延迟；
持久化走本模块的 ``StreamEventWriter``：``append`` 只做非阻塞入队，后台任务
按「最多 100 条或 100ms」批量写 ``stream_events`` 表。这样 SSE 主链路不会因为
数据库慢/抖动而卡住，任一 Pod 的 ``/stream/live`` 也能按 ``event_id`` 顺序回放。

设计要点：

- **终态永不丢弃**：队列满时普通事件丢弃并告警，``done``/``error``/
  ``approval_required`` 会挤掉最旧一条后入队，保证客户端最终能收到收尾帧；
- **异常不上抛**：DB 写入/连接失败只记 error 日志，SSE 流继续；
- **可安全用于同步上下文**：没有运行中的事件循环（如直接构造 ``_StreamHub``
  的单元测试）时不启动后台任务，``append`` 退化为 no-op；
- ``flush_all_stream_events()`` 供应用 shutdown 调用，避免尾部事件丢失。
"""

from __future__ import annotations

import asyncio
import json
import logging

from easy_agent.db.repositories.stream_events import append_stream_event
from easy_agent.services.sse import is_terminal_event

logger = logging.getLogger(__name__)

# 批量写上限与 flush 间隔：两者先到先触发。
MAX_BATCH_SIZE = 100
FLUSH_INTERVAL_SECONDS = 0.1
# 队列按事件条数计。长思考场景单个事件很小，1 万条足够；满时丢弃普通事件，
# 绝不让 ``put`` 阻塞事件循环（否则整个 SSE 流会停住）。
QUEUE_MAXSIZE = 10_000
ACLOSE_TIMEOUT_SECONDS = 5.0

_SENTINEL = object()
# 已启动后台任务的 writer 注册表（shutdown 时统一 flush）。
_active_writers: "set[StreamEventWriter]" = set()


def _looks_terminal(payload: str) -> bool:
    try:
        return is_terminal_event(payload)
    except Exception:  # noqa: BLE001 - 非 JSON / 非法 frame：按普通事件处理
        return False


def _is_tool_call_delta(payload: str) -> bool:
    """工具参数增量可以丢；正文和思考要留下来给其它 Pod 回放。"""
    try:
        body = payload[6:] if payload.startswith("data: ") else payload
        return json.loads(body).get("type") == "tool_call_delta"
    except Exception:  # noqa: BLE001
        return False


class StreamEventWriter:
    """单会话 SSE frame 的异步批量写入器。"""

    def __init__(self, session_id: str, db=None):
        self.session_id = session_id
        self._db = db
        self._resolved_db = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False
        self._dropped = 0
        self._db_missing_warned = False

    @property
    def dropped(self) -> int:
        """因队列满被丢弃的普通事件条数（可观测指标）。"""
        return self._dropped

    # ── 写入端（同步、非阻塞）─────────────────────────────────────────

    def append(self, payload: str) -> None:
        """非阻塞入队；无事件循环/已关闭时静默忽略。"""
        if not payload or self._closing:
            return
        if not self._ensure_task():
            return
        try:
            self._queue.put_nowait(payload)
            return
        except asyncio.QueueFull:
            pass

        if self._evict_tool_deltas():
            try:
                self._queue.put_nowait(payload)
                return
            except asyncio.QueueFull:
                pass

        if _looks_terminal(payload):
            # 终态必须落库：挤掉最旧一条后重试；仍失败只告警（绝不阻塞）。
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(payload)
                logger.warning(
                    f"[{self.session_id[-5:]}] stream_events 队列满，已挤掉最旧事件以保留终态"
                )
            except asyncio.QueueFull:
                logger.error(
                    f"[{self.session_id[-5:]}] stream_events 队列满且无法入队终态事件（已丢弃）"
                )
            return

        self._dropped += 1
        # 只在前几次与每 1000 条告警，避免刷爆日志。
        if self._dropped <= 3 or self._dropped % 1000 == 0:
            logger.warning(
                f"[{self.session_id[-5:]}] stream_events 队列满，丢弃普通事件 "
                f"(累计 {self._dropped} 条)"
            )

    def _evict_tool_deltas(self) -> bool:
        """腾出一格：优先丢掉已在队列里的 tool_call_delta，正文帧留着。"""
        kept: list = []
        evicted = False
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not evicted and isinstance(item, str) and _is_tool_call_delta(item):
                evicted = True
                self._dropped += 1
                continue
            kept.append(item)
        for item in kept:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                break
        return evicted

    # ── 生命周期 ─────────────────────────────────────────────────────

    def request_close(self) -> None:
        """同步请求收尾：把 sentinel 入队，后台任务 flush 完剩余批次后退出。

        不阻塞事件循环；应用 shutdown / hub.close 都可以直接调用。
        """
        if self._closing:
            return
        self._closing = True
        task = self._task
        if task is None or task.done():
            return
        try:
            self._queue.put_nowait(_SENTINEL)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                logger.warning(
                    f"[{self.session_id[-5:]}] stream_events 队列满，无法入队收尾 sentinel"
                )

    async def aclose(self, timeout: float = ACLOSE_TIMEOUT_SECONDS) -> None:
        """等待后台任务把剩余事件写完后退出（shutdown / 测试用）。"""
        self.request_close()
        task = self._task
        if task is None:
            return
        if task.done():
            # 后台任务异常退出时，队列里可能还留着事件：尽力补写一次。
            await self._flush_remaining()
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"[{self.session_id[-5:]}] stream_events flush 超时 {timeout}s，取消后台任务"
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 关闭阶段不再上抛
            logger.error(f"[{self.session_id[-5:]}] stream_events 关闭异常: {exc}")

    # ── 后台批量写 ───────────────────────────────────────────────────

    def _ensure_task(self) -> bool:
        task = self._task
        if task is not None:
            return not task.done()
        if self._closing:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 同步上下文（单测直接构造 hub）：不持久化，保持主流程零副作用。
            return False
        self._loop = loop
        self._task = loop.create_task(self._run(), name=f"stream-events-{self.session_id[-8:]}")
        self._task.add_done_callback(self._on_done)
        _active_writers.add(self)
        return True

    def _on_done(self, task: asyncio.Task) -> None:
        _active_writers.discard(self)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"[{self.session_id[-5:]}] stream_events 后台任务异常退出: "
                f"{type(exc).__name__}: {exc}"
            )

    async def _run(self) -> None:
        try:
            while True:
                first = await self._queue.get()
                if first is _SENTINEL:
                    break
                batch = [first]
                sentinel_seen = False
                deadline = self._loop.time() + FLUSH_INTERVAL_SECONDS
                while len(batch) < MAX_BATCH_SIZE:
                    remaining = deadline - self._loop.time()
                    if remaining <= 0:
                        break
                    try:
                        item = await asyncio.wait_for(self._queue.get(), remaining)
                    except asyncio.TimeoutError:
                        break
                    if item is _SENTINEL:
                        sentinel_seen = True
                        break
                    batch.append(item)
                await self._flush(batch)
                if sentinel_seen:
                    break
        except asyncio.CancelledError:
            # 关闭进程时尽力把已入队事件写完（不保证成功，失败只告警）。
            await self._flush_remaining()
            raise
        finally:
            _active_writers.discard(self)

    async def _flush_remaining(self) -> None:
        while True:
            batch: list[str] = []
            while len(batch) < MAX_BATCH_SIZE:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is _SENTINEL:
                    continue
                batch.append(item)
            if not batch:
                return
            await self._flush(batch)

    async def _flush(self, batch: list[str]) -> None:
        if not batch:
            return
        db = self._resolve_db()
        if db is None:
            if not self._db_missing_warned:
                self._db_missing_warned = True
                logger.error(
                    f"[{self.session_id[-5:]}] stream_events 持久化跳过：数据库未初始化"
                    f"（本批 {len(batch)} 条）"
                )
            return
        try:
            # DB 是同步驱动（SQLite/MySQL），放线程池避免阻塞事件循环。
            await asyncio.to_thread(self._write_batch, db, batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 持久化失败不得中断 SSE 主链路
            logger.error(
                f"[{self.session_id[-5:]}] stream_events 批量写入失败（{len(batch)} 条）: "
                f"{type(exc).__name__}: {exc}"
            )

    def _write_batch(self, db, batch: list[str]) -> None:
        for payload in batch:
            append_stream_event(db, self.session_id, payload)

    def _resolve_db(self):
        if self._resolved_db is not None:
            return self._resolved_db
        db = self._db
        if db is None:
            # 延迟解析进程级 Database；不主动 init，避免测试/工具导入时产生副作用。
            from easy_agent.db import database as database_module

            db = database_module._db_instance
        self._resolved_db = db
        return db


async def flush_all_stream_events(timeout: float = ACLOSE_TIMEOUT_SECONDS) -> None:
    """shutdown 钩子：flush 所有仍在运行的 stream event writer。"""
    # aclose 幂等：已经 request_close 的 writer 只需等待后台任务把批次写完。
    writers = list(_active_writers)
    if not writers:
        return
    logger.info("关闭前 flush %s 个 SSE 事件写入器", len(writers))
    await asyncio.gather(
        *(writer.aclose(timeout) for writer in writers), return_exceptions=True
    )


__all__ = [
    "ACLOSE_TIMEOUT_SECONDS",
    "FLUSH_INTERVAL_SECONDS",
    "MAX_BATCH_SIZE",
    "QUEUE_MAXSIZE",
    "StreamEventWriter",
    "flush_all_stream_events",
]
