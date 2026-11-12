"""Chat 只读流式端点：``/stream/status`` 与 ``/stream/live``。

从 ``api/chat.py`` 原样拆出，保持 URL、状态码、SSE 与 DB-first 回放语义不变。
本模块不 import ``api.chat``；事件只从 ``stream_events`` 表读取，因此请求
落到任一 Pod 都能得到一致的流状态与回放结果。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends

from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.api.sse_response import sse_response
from easy_agent.api.deps import (
    as_chat_repository as _as_chat_repository,
    get_chat_repository,
)
from easy_agent.middleware import get_current_username
from easy_agent.services.sessions.runtime import is_stream_active
from easy_agent.services.streaming.sse import (
    SSE_HEARTBEAT_FRAME,
    heartbeat_interval_seconds,
    is_terminal_event,
    parse_sse_frame,
)
from easy_agent.services.streaming.emitter import SSEEmitter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/chat", tags=["Chat"])

# /stream/live 的 DB-first 回放节奏：无新事件时 150ms 轮询一次；
# 流已结束（stream_owner 失效）后再等 2 轮（约 300ms）才收尾，给批量写入器
# 留出「最后一批事件尚未落库」的窗口，避免重连客户端丢掉 done/error。
_LIVE_POLL_SECONDS = 0.15
_LIVE_IDLE_ROUNDS = 2
_LIVE_PAGE_LIMIT = 500


# 批次 4：provider 统一从 ``api.deps`` 导入，本模块不再复制一份同形实现。


def _frame_is_terminal(frame: str) -> bool:
    """容错版 is_terminal_event：非法 frame 一律当普通事件。"""
    try:
        return is_terminal_event(frame)
    except Exception:  # noqa: BLE001 - 脏数据不应中断回放
        return False


# 「这一轮彻底收尾」的事件类型。注意不能直接用 ``is_terminal_event``：
# 它把 ``approval_required`` 也算终态（推送角度看是对的），但业务上这一轮还停在
# 待审批，过程事件依然比落库消息新，必须继续算「未完成」。
_FINISHED_EVENT_TYPES = frozenset({"done", "error"})


def _frame_is_finished(frame: str) -> bool:
    """该轮是否已经彻底收尾（只有 done/error 算）。"""
    return _frame_type(frame) in _FINISHED_EVENT_TYPES


def _frame_type(frame: str) -> str:
    """取 SSE frame 的 ``type``，脏数据返回空串。"""
    try:
        return str(parse_sse_frame(frame).get("type", ""))
    except Exception:  # noqa: BLE001 - 脏数据按「未知事件」处理
        return ""


@router.get(
    "/stream/status",
    summary="查询会话是否正在流式输出",
    description="刷新页面后用于判断是否需要重新挂载到进行中的流式任务。",
)
async def chat_stream_status(
    session_id: str,
    db: Annotated[ChatRepository, Depends(get_chat_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    # 校验会话归属（不存在的会话同样 404，避免泄露会话是否存在）
    repo = _as_chat_repository(db)
    await asyncio.to_thread(repo.get_owned_session_meta, session_id, username)
    # 以 DB 的 session_runtime.stream_owner/stream_until 为准：请求落到任一 Pod
    # 都能得到一致答案（本 Pod 的 hub 只代表本进程状态，不能用于跨 Pod 判断）。
    try:
        active = await asyncio.to_thread(
            is_stream_active, repo.runtime, session_id
        )
    except Exception as exc:  # noqa: BLE001 - 状态查询失败按“未活跃”处理
        logger.error(f"[{session_id[-5:]}] 查询流状态失败: {exc}")
        active = False
    # 没在跑不等于没有过程可看：HITL 待审批、进程崩溃、写入器异常都会让这一轮
    # 停在半路——落库的只有半截消息，思考过程与工具结果还留在 stream_events 里。
    # 末尾不是 done/error 就说明「事件比消息新」，打开会话时值得挂一次 /stream/live
    # 回放（回放端点在没有新事件时会自己补 done 收尾，不会挂住）。
    replayable = False
    if not active:
        try:
            last = await asyncio.to_thread(
                repo.stream_events.latest_stream_event, session_id
            )
            frame = str((last or {}).get("payload") or "")
            replayable = bool(frame) and not _frame_is_finished(frame)
        except Exception as exc:  # noqa: BLE001 - 回放判据失败按“不可回放”处理
            logger.warning(f"[{session_id[-5:]}] 查询可回放事件失败: {exc}")
            replayable = False
    return {"session_id": session_id, "active": active, "replayable": replayable}


@router.get(
    "/stream/live",
    summary="挂载到进行中的流式输出",
    description=(
        "按 stream_events 表顺序回放当前流式任务已产生的事件"
        "（thinking/tool_call/content 等），然后持续轮询后续事件直到 done/error。"
        "若任务已结束，回放完整历史后立即结束；不依赖本 Pod 的进程内 hub。"
    ),
)
async def chat_stream_live(
    session_id: str,
    db: Annotated[ChatRepository, Depends(get_chat_repository)],
    username: Annotated[str, Depends(get_current_username)],
    last_event_id: int = 0,
):
    # 校验会话归属
    repo = _as_chat_repository(db)
    await asyncio.to_thread(repo.get_owned_session_meta, session_id, username)
    event_store = repo.stream_events
    emitter = SSEEmitter(session_id)

    async def _fetch(after_id: int) -> list[dict]:
        return await asyncio.to_thread(
            event_store.list_stream_events,
            session_id,
            after_id,
            _LIVE_PAGE_LIMIT,
        )

    async def event_source():
        # DB-first：cursor 即 stream_events.event_id，任一 Pod 都能从任意位置续播。
        # last_event_id 由客户端可选传入（当前前端不传，默认从头回放）。
        try:
            cursor = max(0, int(last_event_id or 0))
        except (TypeError, ValueError):
            cursor = 0
        idle_rounds = 0
        # 空闲心跳：回放端点长时间没有新事件时同样会被反向代理判空闲超时。
        heartbeat_seconds = heartbeat_interval_seconds()
        last_emit = time.monotonic()
        # 回放到的最后一个事件类型：收尾时据此决定要不要补 done。
        last_type = ""

        while True:
            try:
                rows = await _fetch(cursor)
            except Exception as exc:  # noqa: BLE001 - 回放查询失败不直接断流
                logger.error(f"[{session_id[-5:]}] 回放 stream_events 失败: {exc}")
                rows = []

            if rows:
                idle_rounds = 0
                for row in rows:
                    frame = str(row.get("payload") or "")
                    try:
                        event_id = int(row.get("event_id") or 0)
                    except (TypeError, ValueError):
                        event_id = 0
                    if event_id > cursor:
                        cursor = event_id
                    if not frame:
                        continue
                    ftype = _frame_type(frame)
                    if ftype:
                        last_type = ftype
                    yield frame
                    last_emit = time.monotonic()
                    # 只有 done/error 才是「这一轮真的结束」；approval_required 之后
                    # 用户一批准就会有新事件，回放必须继续往下走，否则跨 Pod 打开
                    # 一个已恢复过的会话只能看到中断前那一段。
                    if ftype in _FINISHED_EVENT_TYPES:
                        return
                # 可能还有下一页，立即继续拉取（不 sleep）。
                continue

            try:
                active = await asyncio.to_thread(
                    is_stream_active, repo.runtime, session_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{session_id[-5:]}] 查询流活跃状态失败: {exc}")
                active = False

            if active:
                idle_rounds = 0
            else:
                idle_rounds += 1
            # 空闲超过心跳间隔就补一帧注释行，避免代理按「上游静默」掐断长思考流。
            if heartbeat_seconds > 0 and (
                time.monotonic() - last_emit >= heartbeat_seconds
            ):
                yield SSE_HEARTBEAT_FRAME
                last_emit = time.monotonic()
            # 先等一轮再判断是否收尾：流结束后仍给写入器留出 2 个轮询周期
            # （约 300ms）把最后一批事件落库。
            await asyncio.sleep(_LIVE_POLL_SECONDS)
            if idle_rounds >= _LIVE_IDLE_ROUNDS:
                break

        # 收尾前再查一次：覆盖「writer 最后一批刚写入、owner 已释放」的窗口，
        # 避免跨 Pod 重连时丢掉 done/error。
        try:
            rows = await _fetch(cursor)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{session_id[-5:]}] 收尾回放 stream_events 失败: {exc}")
            rows = []
        for row in rows:
            frame = str(row.get("payload") or "")
            if not frame:
                continue
            try:
                event_id = int(row.get("event_id") or 0)
            except (TypeError, ValueError):
                event_id = 0
            if event_id > cursor:
                cursor = event_id
            ftype = _frame_type(frame)
            if ftype:
                last_type = ftype
            yield frame
            if ftype in _FINISHED_EVENT_TYPES:
                return
        # 停在 approval_required：这一轮在等用户审批，**不能补 done**。
        # done 会让前端清掉 pendingApproval（chunkHandler 的 done 分支），
        # 审批卡片随即消失——这正是「换 Pod 打开会话后点不了批准」的成因。
        if last_type == "approval_required":
            return
        # 流已结束但事件表里没有终态（写入器异常/进程崩溃）：补 done 关闭连接，
        # 避免前端一直停在“执行中”。
        yield emitter.done(already_completed=True)

    return sse_response(event_source())
