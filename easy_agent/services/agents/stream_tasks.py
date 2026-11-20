"""会话流式任务注册表与跨副本流锁生命周期（从 ``services/agents/manager.py`` 拆出）。"""

from __future__ import annotations

import asyncio
import logging
import time as _time

from easy_agent.db.providers import get_session_runtime_repository
from easy_agent.services.agents.state import (
    _prune_if_empty,
    _state_for,
    sessions_store,
)
from easy_agent.utils.blocking import run_blocking

logger = logging.getLogger("easy_agent.services.agents.manager")


def register_stream_task(session_id: str, task: asyncio.Task[None]) -> None:
    """注册会话的流式输出任务，使其可被取消。"""
    _acquire_stream_lock(session_id)
    _remember_stream_task(session_id, task)


async def aregister_stream_task(session_id: str, task: asyncio.Task[None]) -> None:
    """:func:`register_stream_task` 的异步版本：获取跨实例流锁的写库放进线程。"""
    await run_blocking(_acquire_stream_lock, session_id)
    _remember_stream_task(session_id, task)


def _acquire_stream_lock(session_id: str) -> None:
    """获取 DB 流锁；会话已被占用时抛 ``SessionBusyError``，其它错误只记日志。"""
    from easy_agent.services.sessions.runtime import SessionBusyError, acquire_stream

    try:
        acquire_stream(get_session_runtime_repository(), session_id)
    except SessionBusyError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 会话流锁获取失败: {exc}")


def _remember_stream_task(session_id: str, task: asyncio.Task[None]) -> None:
    from easy_agent.services.sessions.runtime import SessionBusyError

    state = _state_for(session_id)
    old = state.stream_task
    if old and not old.done() and old is not task:
        raise SessionBusyError(
            f"会话 {session_id} 正在生成回复，请等待完成或先取消当前生成"
        )
    state.stream_task = task


def is_session_streaming(session_id: str) -> bool:
    """该会话当前是否有本进程内运行中的流式任务（用于并发互斥/409）。"""
    state = sessions_store().get(session_id)
    task = state.stream_task if state is not None else None
    return bool(task and not task.done())


def unregister_stream_task(
    session_id: str, task: asyncio.Task[None] | None = None
) -> None:
    """流式任务结束后注销自身。"""
    _forget_stream_task(session_id, task)
    _release_stream_lock(session_id)


async def aunregister_stream_task(
    session_id: str, task: asyncio.Task[None] | None = None
) -> None:
    """:func:`unregister_stream_task` 的异步版本：释放跨实例流锁的写库放进线程。"""
    _forget_stream_task(session_id, task)
    await run_blocking(_release_stream_lock, session_id)


def _forget_stream_task(session_id: str, task: asyncio.Task[None] | None) -> None:
    state = sessions_store().get(session_id)
    if state is None:
        return
    if task is None or state.stream_task is task:
        state.stream_task = None
        _prune_if_empty(session_id, state)


def _release_stream_lock(session_id: str) -> None:
    try:
        from easy_agent.services.sessions.runtime import release_stream

        release_stream(get_session_runtime_repository(), session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 会话流锁释放失败: {exc}")


async def cancel_stream_task(session_id: str) -> bool:
    """取消会话正在运行的流式任务（同时写入跨实例取消标记）。"""
    try:
        from easy_agent.services.sessions.runtime import request_cancel

        await run_blocking(
            request_cancel, get_session_runtime_repository(), session_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 写入跨实例取消标记失败: {exc}")

    state = sessions_store().get(session_id)
    task = state.stream_task if state is not None else None
    if state is not None:
        state.stream_task = None
        _prune_if_empty(session_id, state)
    if not task:
        logger.warning(
            f"[{session_id[-5:]}] 取消流式任务 | 未找到注册的任务（可能已结束或未注册）"
        )
        return False
    if task.done():
        logger.info(
            f"[{session_id[-5:]}] 取消流式任务 | 任务已完成（done=True），无需取消"
        )
        return False

    task_name = task.get_name() if hasattr(task, "get_name") else "?"
    logger.info(
        f"[{session_id[-5:]}] 取消流式任务 | 正在取消 task={task_name} | "
        f"cancelled={task.cancelled()} | "
        f"当前事件循环任务数={len(asyncio.all_tasks())}"
    )

    cancelled_flag = task.cancel()
    logger.info(
        f"[{session_id[-5:]}] 取消流式任务 | task.cancel() 返回 {cancelled_flag}（True=成功请求取消）"
    )

    t0 = _time.time()
    try:
        await asyncio.wait_for(task, timeout=3.0)
        logger.info(
            f"[{session_id[-5:]}] 取消流式任务 | 任务已终止 | 耗时 {_time.time() - t0:.2f}s"
        )
    except asyncio.CancelledError:
        logger.info(
            f"[{session_id[-5:]}] 取消流式任务 | 任务收到 CancelledError 已终止 | "
            f"耗时 {_time.time() - t0:.2f}s"
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[{session_id[-5:]}] 取消流式任务 | 等待任务终止超时（3s），任务可能仍在后台运行"
        )
    except Exception as e:
        logger.warning(
            f"[{session_id[-5:]}] 取消流式任务 | 任务终止时抛出异常: {type(e).__name__}: {e}"
        )
    return True


async def drain_stream_tasks(
    timeout: float = 20.0,  # noqa: ASYNC109 - timeout 传给 asyncio.wait，不阻塞
) -> None:
    """Wait for in-flight SSE/astream tasks on shutdown, then cancel leftovers."""
    tasks = [
        state.stream_task
        for state in list(sessions_store().values())
        if state.stream_task is not None and not state.stream_task.done()
    ]
    if not tasks:
        return
    logger.info("关闭前等待 %s 个流式任务结束（最多 %.0fs）", len(tasks), timeout)
    _done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
