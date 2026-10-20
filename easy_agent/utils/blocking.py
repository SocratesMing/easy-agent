"""在线程里执行阻塞调用（同步数据库 / 文件 IO），不占事件循环。

``asyncio.to_thread`` 的薄封装，只多一条：调用方被取消时，先等线程里的这次调用执行完，
再把取消抛出去。线程里的写入中途停不下来；如果立刻抛出取消，调用方随后的收尾写入
（例如流被取消后保存部分回复）会和这次写入同时进行，后写完的会覆盖先写的。改成在线程里
执行之前是同步调用，不存在这个问题，这里保持原来的顺序。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def run_blocking(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """在默认线程池里执行 ``func(*args, **kwargs)`` 并返回结果；异常原样抛出。"""
    future = asyncio.ensure_future(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        if not future.done():
            try:
                await future
            except BaseException:  # noqa: BLE001 - 只为等它结束；取消优先于这次调用的结果
                pass
        raise


__all__ = ["run_blocking"]
