"""真实编排、业务租约与 SQLite 图在故障/竞争下的行为。"""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from easy_agent.services.agents import manager
from easy_agent.services.sessions import compaction
from easy_agent.services.sessions.runtime import SessionBusyError, acquire_stream, release_stream
from tests.services.test_manual_compaction import agent, history, repo, runtime


def configure(monkeypatch, rt, a):
    rt.release_session_stream("s1", "operation", datetime.now(UTC).isoformat())
    monkeypatch.setattr(manager, "get_agent_config", lambda: {"config": a.config})
    monkeypatch.setattr(compaction, "get_session_runtime_repository", lambda: rt)
    monkeypatch.setattr(compaction, "get_agent_run_repository", lambda: SimpleNamespace(has_other_live_run=lambda *args: False))
    monkeypatch.setattr(compaction, "get_session_checkpointer", lambda sid: a.agent.checkpointer)
    monkeypatch.setattr(compaction, "shared_checkpointer_active", lambda: True)
    monkeypatch.setattr(compaction, "is_session_streaming", lambda sid: False)
    async def create(*args, **kwargs):
        return a
    monkeypatch.setattr(compaction, "get_or_create_agent_for_session", create)


@pytest.mark.parametrize("failure", ["heartbeat", "takeover", "timeout", "cancel"])
async def test_prepare_failure_never_commits_and_preserves_new_owner(tmp_path, monkeypatch, failure):
    rt = runtime(tmp_path)
    rows = history()
    repository = repo(rows)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        a = agent(saver, tmp_path)
        configure(monkeypatch, rt, a)
        started, proceed, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_prepare = compaction.prepare_compaction
        async def prepare(*args):
            started.set()
            try:
                await proceed.wait()
                return await original_prepare(*args)
            finally:
                stopped.set()
        monkeypatch.setattr(compaction, "prepare_compaction", prepare)
        monkeypatch.setattr(compaction, "HEARTBEAT_SECONDS", .01 if failure in ("heartbeat", "takeover") else 60)
        monkeypatch.setattr(compaction, "TIMEOUT_SECONDS", 1 if failure == "timeout" else 10)
        if failure == "heartbeat":
            monkeypatch.setattr(rt, "renew_stream_lease", lambda *args, **kwargs: False)
        task = asyncio.create_task(compaction._execute(repository, repository.get_owned_session("s1", "tester"),
                                                      "tester", "small", failure, None))
        try:
            await asyncio.wait_for(started.wait(), 5)
            if failure == "takeover":
                rt.upsert_session_runtime("s1", stream_owner="new-owner")
            if failure == "cancel":
                rt.upsert_session_runtime("s1", cancel_requested=True)
                proceed.set()
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(task, 5)
            assert error.value.status_code == (504 if failure == "timeout" else 502)
            assert stopped.is_set()
            assert not (await a.agent.aget_state({"configurable": {"thread_id": "s1"}})).values
            assert rows == history()
            owner = rt.get_session_runtime("s1")["stream_owner"]
            assert owner == ("new-owner" if failure == "takeover" else "")
            if failure != "cancel":
                assert not a.model.inputs
        finally:
            proceed.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_timeout_during_commit_waits_for_save_then_retry_recovers(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    repository = repo(history())
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        a = agent(saver, tmp_path)
        configure(monkeypatch, rt, a)
        monkeypatch.setattr(compaction, "TIMEOUT_SECONDS", 1)
        monkeypatch.setattr(compaction, "HEARTBEAT_SECONDS", 60)
        entered, finish, cancellation_received = asyncio.Event(), asyncio.Event(), asyncio.Event()
        releases = []
        original_release = rt.release_session_stream
        def release(*args):
            releases.append(args)
            return original_release(*args)
        monkeypatch.setattr(rt, "release_session_stream", release)
        original_update, original_shield = a.agent.aupdate_state, compaction.asyncio.shield
        async def update(*args, **kwargs):
            entered.set()
            await finish.wait()
            return await original_update(*args, **kwargs)
        async def shield_commit(task):
            try:
                return await original_shield(task)
            except asyncio.CancelledError:
                cancellation_received.set()
                raise
        monkeypatch.setattr(a.agent, "aupdate_state", update)
        # 只观测实际 timeout 对 commit 等待者的取消，不替代 wait/提交逻辑。
        monkeypatch.setattr(compaction.asyncio, "shield", shield_commit)
        task = asyncio.create_task(compaction._execute(repository, repository.get_owned_session("s1", "tester"),
                                                      "tester", "small", "commit-timeout", None))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(cancellation_received.wait(), 5)
            assert not task.done()
            assert releases == []  # 围栏仍持有业务库锁，不能在事件循环同步读库。
            finish.set()
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(task, 5)
            assert error.value.status_code == 504
            assert not rt.get_session_runtime("s1")["stream_owner"]
            saved = await compaction.records("s1")
            assert saved[0]["id"] == "commit-timeout"
            monkeypatch.setattr(compaction.asyncio, "shield", original_shield)
            recovered = await compaction.compact(repository, "s1", "tester", "small", "commit-timeout")
            assert recovered["compaction"] == saved[0]
            assert len(a.model.inputs) == 1
        finally:
            finish.set()
            await asyncio.gather(task, return_exceptions=True)


async def test_chat_resume_and_different_compactions_share_lease(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    repository = repo(history())
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        a = agent(saver, tmp_path)
        configure(monkeypatch, rt, a)
        entered, finish = asyncio.Event(), asyncio.Event()
        original = compaction.prepare_compaction
        async def prepare(*args):
            entered.set()
            await finish.wait()
            return await original(*args)
        monkeypatch.setattr(compaction, "prepare_compaction", prepare)
        async def execute(request_id):
            return await compaction._execute(repository, repository.get_owned_session("s1", "tester"),
                                             "tester", "small", request_id, None)
        # 聊天/resume 共用 acquire_stream；先占用时压缩不能进入摘要。
        acquire_stream(rt, "s1")
        with pytest.raises(HTTPException) as error:
            await execute("blocked-by-chat")
        assert error.value.status_code == 409
        release_stream(rt, "s1")
        first = asyncio.create_task(execute("first"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            with pytest.raises(SessionBusyError):
                acquire_stream(rt, "s1")
            with pytest.raises(HTTPException) as error:
                await execute("second")
            assert error.value.status_code == 409
            finish.set()
            assert (await asyncio.wait_for(first, 5))["status"] == "compacted"
            assert len(a.model.inputs) == 1
            acquire_stream(rt, "s1")
            release_stream(rt, "s1")
        finally:
            finish.set()
            await asyncio.gather(first, return_exceptions=True)
