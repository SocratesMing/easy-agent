"""删除会话后的 checkpoint 对账：补删本进程 SQLite 文件里「会话已不存在」的 thread。

删除会话时只有处理请求的进程能当场删；多副本下其余 pod 文件里的残留由后台对账补删
（``docs/design/2026-10-21-per-pod-sqlite-checkpoints.md`` §4.9）。
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from easy_agent.db.database import Database
from easy_agent.db.repositories import sessions as sessions_repo
from easy_agent.models.db import SessionModel
from easy_agent.persistence import checkpoints as ck

KEPT = "11111111-1111-4111-8111-111111111111"
GONE = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
async def saver(tmp_path):
    await ck.shutdown_checkpointers()
    saver = await ck.open_sqlite_checkpointer(str(tmp_path / "ckpt.db"))
    yield saver
    await ck.shutdown_checkpointers()


@pytest.fixture
def business_db(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "biz.db")}})
    db.init_tables()
    db.create_session(SessionModel(session_id=KEPT, title="t", username="u"))
    return db


async def _seed(saver, *thread_ids):
    for thread_id in thread_ids:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        await saver.aput(config, empty_checkpoint(), {}, {})


async def _threads(saver):
    return set(await ck._list_thread_ids(saver))


def test_existing_session_ids_queries_in_batches(business_db):
    ids = [KEPT, GONE, "", KEPT, *(f"{i:08d}-0000-4000-8000-000000000000" for i in range(1200))]
    assert sessions_repo.existing_session_ids(business_db, ids) == {KEPT}


async def test_deletes_only_threads_of_sessions_that_no_longer_exist(saver, business_db):
    await _seed(saver, KEPT, f"{KEPT}-m1", GONE, f"{GONE}-m1", f"{GONE}-m2", "sched-t1-r1", "__graph_cache__")

    deleted = await ck.prune_deleted_session_threads(business_db)

    assert deleted == 3
    # 还存在的会话、定时任务 thread、认不出的 thread 都保留
    assert await _threads(saver) == {KEPT, f"{KEPT}-m1", "sched-t1-r1", "__graph_cache__"}


async def test_skips_the_round_when_the_session_lookup_fails(saver, business_db, monkeypatch):
    await _seed(saver, GONE)

    def boom(db, ids):
        raise RuntimeError("mysql down")

    monkeypatch.setattr(sessions_repo, "existing_session_ids", boom)

    assert await ck.prune_deleted_session_threads(business_db) == 0
    assert await _threads(saver) == {GONE}


async def test_no_official_saver_means_nothing_to_do(business_db):
    await ck.shutdown_checkpointers()
    assert await ck.prune_deleted_session_threads(business_db) == 0


async def test_background_task_runs_at_startup_and_stops_on_shutdown(saver, business_db):
    await _seed(saver, GONE)

    task = ck.start_checkpoint_gc(interval=3600, db=business_db)
    assert task is not None
    for _ in range(200):
        if not await _threads(saver):
            break
        await asyncio.sleep(0.01)
    assert await _threads(saver) == set()
    # 已在跑时不重复启动
    assert ck.start_checkpoint_gc(interval=3600, db=business_db) is task

    await ck.shutdown_checkpointers()
    assert task.done()


async def test_background_task_is_off_with_zero_interval_or_without_a_saver(saver):
    assert ck.start_checkpoint_gc(interval=0) is None
    await ck.shutdown_checkpointers()
    assert ck.start_checkpoint_gc(interval=600) is None
