"""run_blocking：阻塞调用放进线程；调用方被取消时先等线程里的调用执行完，再抛出取消。"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from easy_agent.utils.blocking import run_blocking


async def test_runs_in_a_worker_thread_and_returns_the_result():
    loop_thread = threading.get_ident()

    worker_thread, total = await run_blocking(lambda x, y=0: (threading.get_ident(), x + y), 1, y=2)

    assert total == 3
    assert worker_thread != loop_thread


async def test_exceptions_propagate():
    def boom():
        raise ValueError("db down")

    with pytest.raises(ValueError, match="db down"):
        await run_blocking(boom)


async def test_cancel_waits_for_the_running_call_before_raising():
    """线程里的写入停不下来：取消要等它写完再生效，调用方随后的收尾写入才不会和它乱序。"""
    order: list[str] = []
    started = threading.Event()

    def slow_write():
        started.set()
        time.sleep(0.2)
        order.append("write done")

    async def caller():
        try:
            await run_blocking(slow_write)
        except asyncio.CancelledError:
            order.append("cancel observed")
            raise

    task = asyncio.create_task(caller())
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert order == ["write done", "cancel observed"]


async def test_repeated_cancel_still_waits_for_the_running_call():
    """连点两次停止会取消两次：第二次取消也不能让调用方提前离开。"""
    order: list[str] = []
    started = threading.Event()

    def slow_write():
        started.set()
        time.sleep(0.3)
        order.append("write done")

    async def caller():
        try:
            await run_blocking(slow_write)
        except asyncio.CancelledError:
            order.append("cancel observed")
            raise

    task = asyncio.create_task(caller())
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert order == ["write done", "cancel observed"]
