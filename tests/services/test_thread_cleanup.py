"""DA-03 P0：官方 checkpoint saver 里的 thread 随会话删除。

官方 SQLite saver 全局共享，删除会话不会顺带删掉它的 thread。改造前只清
legacy 快照，每条消息一个的 ``{session_id}-{message_id}`` thread 永远留在库里。
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

import easy_agent.persistence.checkpoints as checkpoints
from easy_agent.db.repositories.agent_runs import list_session_message_ids, start_run


async def _seed(saver, *thread_ids):
    for thread_id in thread_ids:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        await saver.aput(config, empty_checkpoint(), {}, {})


async def test_delete_session_threads_removes_session_and_per_message_threads(monkeypatch):
    saver = InMemorySaver()
    await _seed(saver, "s1", "s1-m1", "s1-m2", "s2-m1")
    monkeypatch.setattr(checkpoints, "_sqlite_saver", saver)
    released = []
    monkeypatch.setattr(checkpoints, "release_session_checkpointer", released.append)

    deleted = await checkpoints.delete_session_threads("s1", ["m1", "m2", "", "m1"])

    assert deleted == 3
    assert set(saver.storage) == {"s2-m1"}
    assert released == ["s1"]


async def test_adelete_threads_without_an_official_saver_is_a_no_op(monkeypatch):
    monkeypatch.setattr(checkpoints, "_sqlite_saver", None)
    assert await checkpoints.adelete_threads(["s1"]) == 0


async def test_adelete_threads_keeps_going_after_a_failure(monkeypatch):
    class _Flaky(InMemorySaver):
        async def adelete_thread(self, thread_id):
            if thread_id == "bad":
                raise RuntimeError("db down")
            await super().adelete_thread(thread_id)

    saver = _Flaky()
    await _seed(saver, "ok")
    monkeypatch.setattr(checkpoints, "_sqlite_saver", saver)

    assert await checkpoints.adelete_threads(["bad", "ok"]) == 1
    assert "ok" not in saver.storage


def test_list_session_message_ids(db):
    lease = "2099-01-01T00:00:00+00:00"
    start_run(db, "s1:m1", "s1", "u", "m1", "{}", "pod", lease)
    start_run(db, "s1:m2", "s1", "u", "m2", "{}", "pod", lease)
    start_run(db, "s1:resume", "s1", "u", "", "{}", "pod", lease)
    start_run(db, "s2:m9", "s2", "u", "m9", "{}", "pod", lease)

    assert sorted(list_session_message_ids(db, "s1")) == ["m1", "m2"]


async def test_delete_session_threads_with_the_official_sqlite_saver(tmp_path, monkeypatch):
    """真实 AsyncSqliteSaver（需 sqlite-saver extra，缺依赖时跳过）。"""
    aio = pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    monkeypatch.setattr(checkpoints, "release_session_checkpointer", lambda session_id: None)

    async with aio.AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        await _seed(saver, "s1", "s1-m1", "s2-m1")
        monkeypatch.setattr(checkpoints, "_sqlite_saver", saver)

        await checkpoints.delete_session_threads("s1", ["m1"])

        remaining = {item.config["configurable"]["thread_id"] async for item in saver.alist(None)}
        assert remaining == {"s2-m1"}
