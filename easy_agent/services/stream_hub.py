"""会话级流式事件中枢（SSE hub）与进程内注册表。

从 ``easy_agent.api.chat`` 抽出：hub 与订阅者广播不依赖 API 层，
``api.chat`` 仅保留 ``_detached_event_stream`` 与端点参数/鉴权。

约定：

- ``_StreamHub`` 负责单会话事件历史、订阅者广播与 TTL 清理；
- ``_session_stream_hubs`` 为进程内注册表；跨副本正确性由 ``stream_events`` 表提供；
- ``broadcast`` 同步更新内存历史，持久化交给 ``StreamEventWriter``（不阻塞 SSE）。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from .stream_events import StreamEventWriter

logger = logging.getLogger(__name__)

# 会话级流式事件中枢：客户端刷新/重连后可通过 /stream/live 重新订阅，
# 先回放本流已产生的全部事件，再持续接收后续事件（页面刷新不中断流式展示）。
_session_stream_hubs: "dict[str, _StreamHub]" = {}
_STREAM_HUB_TTL_SECONDS = 120.0
# 单会话流式事件历史的字符上限：长思考/长回复时避免 hub 无限占内存
# （历史仍保留足够尾部供重连回放，超出后丢弃最早事件）。
_HUB_HISTORY_MAX_CHARS = 4_000_000

# 订阅者队列的余量：回放必须完整（历史上限 10000 条，见 broadcast），
# 队列容量按「当前历史长度 + 余量」分配，用于容纳回放期间新产生的事件。
_SUBSCRIBER_QUEUE_SLACK = 1000


class _StreamHub:
    """一个会话当前流式任务的事件广播器。

    后台任务把每个 SSE 分片广播给所有订阅者（原始客户端 + 刷新后重连的客户端），
    并保留完整事件历史供新订阅者回放。任务结束后保留 TTL 秒，让迟到重连的
    客户端仍能拿到 done/error 收尾事件。
    """

    def __init__(self, session_id: str, db=None):
        self.session_id = session_id
        # 每个流一个 DB 写入器（懒启动后台任务）：同 Pod 低延迟仍走内存广播，
        # 跨 Pod 正确性由 stream_events 表提供。
        self.writer = StreamEventWriter(session_id, db=db)
        self.history: deque[str] = deque()
        self._history_chars = 0
        self.done = False
        self._subscribers: list[asyncio.Queue] = []
        self._cleanup_handle: asyncio.TimerHandle | None = None

    def subscribe(self) -> asyncio.Queue:
        # 容量必须容纳「整段历史回放 + 运行中新增事件」。早期实现固定
        # maxsize=1000 且满即 break，历史超过 1000 条时会把中间事件静默丢掉——
        # 表现为长流程（多轮工具 + 大量 thinking 增量）刷新页面后，重连的回放里
        # 只剩早期思考、tool_call 卡片消失（工具事件正好落在被丢弃的区间）。
        q: asyncio.Queue = asyncio.Queue(
            maxsize=len(self.history) + _SUBSCRIBER_QUEUE_SLACK
        )
        for item in self.history:
            q.put_nowait(item)
        if self.done:
            q.put_nowait(None)
        else:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def broadcast(self, item: str | None) -> None:
        if item is not None:
            self.history.append(item)
            self._history_chars += len(item)
            # deque.popleft O(1)；list.pop(0) 在大历史下是 O(n)，会造成 O(n²) 卡顿。
            while len(self.history) > 10000:
                self._history_chars -= len(self.history.popleft())
            while self._history_chars > _HUB_HISTORY_MAX_CHARS and self.history:
                self._history_chars -= len(self.history.popleft())
        for q in list(self._subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # 慢消费者：直接断开，避免阻塞后台流式任务；但必须先投一个 EOF，
                # 否则订阅方的 SSE 生成器会永远 await 在一张不再有人写入的队列上
                # （连接挂起、前端"执行中"状态无法收尾）。
                self.unsubscribe(q)
                try:
                    q.get_nowait()  # 腾出一格
                    q.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        if item is not None:
            # 持久化在内存广播之后：写库变慢/失败不影响本 Pod 的实时推送。
            try:
                self.writer.append(item)
            except Exception as exc:  # noqa: BLE001 - 绝不打断 SSE 主链路
                logger.error(
                    f"[{self.session_id[-5:]}] SSE 事件持久化入队失败: "
                    f"{type(exc).__name__}: {exc}"
                )

    def close(self) -> None:
        if self.done:
            return
        self.done = True
        self.broadcast(None)
        # 把 sentinel 入队，后台写入器 flush 完 done/error 后自行退出；
        # request_close 不阻塞事件循环。
        try:
            self.writer.request_close()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"[{self.session_id[-5:]}] SSE 事件写入器关闭失败: "
                f"{type(exc).__name__}: {exc}"
            )
        self._subscribers.clear()
        self._cleanup_handle = asyncio.get_running_loop().call_later(
            _STREAM_HUB_TTL_SECONDS,
            lambda: (
                _session_stream_hubs.pop(self.session_id, None)
                if _session_stream_hubs.get(self.session_id) is self
                else None
            ),
        )


def session_stream_hubs() -> "dict[str, _StreamHub]":
    """返回进程内会话 hub 注册表（测试/诊断用，勿长期持有）。"""
    return _session_stream_hubs


def get_stream_hub(session_id: str, db=None) -> "_StreamHub":
    """取会话当前 hub；不存在或已结束时新建一个。"""
    hub = _session_stream_hubs.get(session_id)
    if hub is None or hub.done:
        hub = _StreamHub(session_id, db=db)
        _session_stream_hubs[session_id] = hub
    return hub


def stream_hub_for(session_id: str) -> "_StreamHub | None":
    """取仍在运行的 hub；已结束（done）返回 None。"""
    hub = _session_stream_hubs.get(session_id)
    return hub if hub is not None and not hub.done else None


def pop_stream_hub(session_id: str) -> "_StreamHub | None":
    """从注册表移除并返回会话 hub；不存在返回 None。"""
    return _session_stream_hubs.pop(session_id, None)


# 兼容旧命名：``StreamHub`` 是公开别名，``_stream_hub_for`` 保留历史私有名。
StreamHub = _StreamHub
_stream_hub_for = stream_hub_for


__all__ = [
    "StreamHub",
    "get_stream_hub",
    "pop_stream_hub",
    "session_stream_hubs",
    "stream_hub_for",
]
