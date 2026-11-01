"""T14.3：legacy ``PersistentMemorySaver`` 的快照在事件循环、写库在线程。

``_flush`` 以前在事件循环上直接 pickle + 写库；现在异步路径改成
「事件循环上做快照（复制 dict）+ 线程里 pickle / 写库」，并用写入序号保证
线程池乱序时更旧的快照不会覆盖新状态。
"""

from __future__ import annotations

import asyncio
import pickle
import threading

import pytest

from easy_agent.persistence.checkpoints import PersistentMemorySaver


class _RecordingStore:
    """记录每次写库的线程与反序列化后的快照。"""

    def __init__(self) -> None:
        self.writes: list[tuple[int, dict]] = []

    def save_agent_checkpoint(self, session_id: str, blob: bytes) -> None:
        self.writes.append((threading.get_ident(), pickle.loads(blob)))

    def load_agent_checkpoint(self, session_id: str) -> None:
        return None


def _make_saver(store: _RecordingStore) -> PersistentMemorySaver:
    saver = PersistentMemorySaver("session-1", db=store)
    saver.storage["thread-1"]["ns"] = {"cp-1": (b"", b"", None)}
    return saver


@pytest.mark.asyncio
async def test_aflush_writes_in_worker_thread():
    """异步落库：pickle / 写库不在事件循环线程上执行。"""
    loop_thread = threading.get_ident()
    store = _RecordingStore()
    saver = _make_saver(store)

    await saver._aflush()

    assert len(store.writes) == 1
    write_thread, payload = store.writes[0]
    assert write_thread != loop_thread
    assert set(payload["storage"]) == {"thread-1"}


@pytest.mark.asyncio
async def test_snapshot_is_taken_before_thread_write():
    """写库线程开始后事件循环继续 put，只影响下一次快照，不会混进本次。"""
    started = threading.Event()
    release = threading.Event()

    class _BlockingStore(_RecordingStore):
        def save_agent_checkpoint(self, session_id, blob):
            started.set()
            assert release.wait(5), "写库线程等待超时"
            super().save_agent_checkpoint(session_id, blob)

    store = _BlockingStore()
    saver = _make_saver(store)
    task = asyncio.create_task(saver._aflush())
    try:
        assert await asyncio.to_thread(started.wait, 5), "写库线程未启动"
        # 快照已生成、写库还没结束：此时事件循环继续写入新的 thread
        saver.storage["thread-2"]["ns"] = {"cp-2": (b"", b"", None)}
    finally:
        release.set()
    await task

    assert set(store.writes[0][1]["storage"]) == {"thread-1"}

    await saver._aflush()
    assert set(store.writes[-1][1]["storage"]) == {"thread-1", "thread-2"}


def test_older_snapshot_never_overwrites_newer():
    """线程池乱序时，序号更旧的快照直接丢弃。"""
    store = _RecordingStore()
    saver = _make_saver(store)
    old_seq, old_payload = saver._snapshot()

    saver.storage["thread-1"]["ns"]["cp-2"] = (b"", b"", None)
    saver._flush()

    # 旧快照晚到：不能覆盖已经落库的新状态
    saver._write_snapshot(old_seq, old_payload)

    assert len(store.writes) == 1
    assert set(store.writes[0][1]["storage"]["thread-1"]["ns"]) == {"cp-1", "cp-2"}


def test_sync_flush_still_persists():
    """sync saver 方法（put / put_writes）走原来的同步落库路径。"""
    store = _RecordingStore()
    saver = _make_saver(store)

    saver._flush()

    assert len(store.writes) == 1
    assert set(store.writes[0][1]["storage"]) == {"thread-1"}
