"""多副本 Phase 2A：``StreamEventWriter`` 批量/终态 flush 与故障隔离。"""

import asyncio
import json
import logging

import pytest

from easy_agent.db.repositories.stream_events import list_stream_events
from easy_agent.services import stream_events as stream_events_module
from easy_agent.services.stream_events import MAX_BATCH_SIZE, StreamEventWriter


def _frame(event_type: str, **fields) -> str:
    return f"data: {json.dumps({'type': event_type, **fields}, ensure_ascii=False)}\n\n"


async def test_writer_flushes_all_queued_frames_in_order_on_close(db):
    writer = StreamEventWriter("sess-writer", db=db)
    frames = [
        _frame("start", session_id="sess-writer"),
        _frame("content", content="第一段"),
        _frame("content", content="第二段"),
        _frame("done", session_id="sess-writer"),
    ]
    for frame in frames:
        writer.append(frame)

    await writer.aclose()

    rows = list_stream_events(db, "sess-writer")
    assert [row["payload"] for row in rows] == frames
    assert [row["event_id"] for row in rows] == sorted(row["event_id"] for row in rows)


async def test_writer_flushes_batches_at_most_max_batch_size(db):
    writer = StreamEventWriter("sess-batch", db=db)
    batches: list[list[str]] = []
    original = writer._write_batch

    def _record(db_arg, batch):
        batches.append(list(batch))
        original(db_arg, batch)

    writer._write_batch = _record  # type: ignore[method-assign]
    for index in range(MAX_BATCH_SIZE * 2 + 37):
        writer.append(_frame("content", content=str(index)))

    await writer.aclose()

    assert sum(len(batch) for batch in batches) == MAX_BATCH_SIZE * 2 + 37
    assert all(0 < len(batch) <= MAX_BATCH_SIZE for batch in batches)
    assert len(batches) <= 4
    assert len(list_stream_events(db, "sess-batch")) == MAX_BATCH_SIZE * 2 + 37


async def test_terminal_frame_survives_full_queue(db, monkeypatch):
    monkeypatch.setattr(stream_events_module, "QUEUE_MAXSIZE", 2)
    writer = StreamEventWriter("sess-full", db=db)
    loop = asyncio.get_running_loop()
    # 占住后台任务，让队列真的堆满（否则消费太快无法触发 QueueFull）。
    writer._loop = loop
    writer._task = loop.create_task(asyncio.sleep(60))
    try:
        writer.append(_frame("content", content="a"))
        writer.append(_frame("content", content="b"))
        writer.append(_frame("content", content="dropped"))
        terminal = _frame("done", session_id="sess-full")
        writer.append(terminal)

        queued = []
        while not writer._queue.empty():
            queued.append(writer._queue.get_nowait())

        assert writer.dropped == 1
        assert terminal in queued, "终态事件绝不能因队列满被丢弃"
    finally:
        writer._task.cancel()
        await asyncio.gather(writer._task, return_exceptions=True)


async def test_db_failure_does_not_raise_into_sse_path(caplog):
    class _BrokenDB:
        def get_connection(self):
            raise RuntimeError("database is down")

    writer = StreamEventWriter("sess-broken", db=_BrokenDB())
    with caplog.at_level(logging.ERROR):
        writer.append(_frame("content", content="hi"))
        writer.append(_frame("done", session_id="sess-broken"))
        await writer.aclose()  # 不应抛异常

    assert "批量写入失败" in caplog.text


@pytest.mark.parametrize("payload", ["", None])
async def test_empty_payload_is_ignored(db, payload):
    writer = StreamEventWriter("sess-empty", db=db)
    writer.append(payload)
    await writer.aclose()
    assert list_stream_events(db, "sess-empty") == []
