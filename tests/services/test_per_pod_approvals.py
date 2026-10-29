"""checkpoint 按 pod 隔离时的审批恢复（``docs/design/2026-10-21-per-pod-sqlite-checkpoints.md`` §4.5）。

- 审批记下所在的实例，恢复请求落到别的 pod 时拒绝，提示两个实例名；
- 本进程的 checkpointer 里没有这条审批的 checkpoint 时拒绝，不再把决策直传给空 thread；
- 两个实例名、两个 SQLite 文件模拟两个 pod：各自只能恢复自己的审批，删除会话后各自补删残留。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TypedDict

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from easy_agent.db.database import Database
from easy_agent.models.db import SessionModel
from easy_agent.persistence import checkpoints as ck
from easy_agent.services import session_thread
from easy_agent.services.session_thread import (
    StaleApprovalError,
    ensure_approval_on_this_instance,
    resolve_resume_thread,
)

SID = "33333333-3333-4333-8333-333333333333"
GONE = "44444444-4444-4444-8444-444444444444"
HANDLE = f"{SID}-m1"


class _State(TypedDict, total=False):
    x: int


def _agent_with(saver):
    graph = StateGraph(_State)
    graph.add_node("n", lambda state: {"x": 1})
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    config = SimpleNamespace(agent=SimpleNamespace(session_thread=False))
    return SimpleNamespace(agent=graph.compile(checkpointer=saver), config=config, max_steps=5)


async def _seed(saver, *thread_ids):
    for thread_id in thread_ids:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        await saver.aput(config, empty_checkpoint(), {}, {})


def _pending(instance="pod-a", handle=HANDLE):
    return {"role": "assistant", "content": "", "pending_approval": {"thread_id": handle, "instance": instance}}


class TestInstanceCheck:
    def test_no_check_and_no_db_read_unless_checkpoints_are_per_pod(self, monkeypatch):
        monkeypatch.setattr(session_thread, "per_pod_checkpoints_active", lambda: False)
        monkeypatch.setattr(session_thread, "checkpoint_instance", lambda: "pod-b")

        def must_not_load():
            raise AssertionError("单 pod 不应多读一次库")

        ensure_approval_on_this_instance(must_not_load, HANDLE)

    def test_approval_recorded_on_another_pod_is_rejected(self, monkeypatch):
        monkeypatch.setattr(session_thread, "per_pod_checkpoints_active", lambda: True)
        monkeypatch.setattr(session_thread, "checkpoint_instance", lambda: "pod-b")
        with pytest.raises(StaleApprovalError) as err:
            ensure_approval_on_this_instance(lambda: _pending("pod-a"), HANDLE)
        assert "pod-a" in str(err.value)
        assert "pod-b" in str(err.value)

    def test_same_pod_old_messages_and_other_handles_pass(self, monkeypatch):
        monkeypatch.setattr(session_thread, "per_pod_checkpoints_active", lambda: True)
        monkeypatch.setattr(session_thread, "checkpoint_instance", lambda: "pod-a")
        ensure_approval_on_this_instance(lambda: _pending("pod-a"), HANDLE)
        monkeypatch.setattr(session_thread, "checkpoint_instance", lambda: "pod-b")
        # 升级前的旧消息没有 instance 字段
        ensure_approval_on_this_instance(lambda: {"pending_approval": {"thread_id": HANDLE}}, HANDLE)
        # 句柄对不上最后一条消息：交给 resolve_resume_thread 判断
        ensure_approval_on_this_instance(lambda: _pending("pod-a", handle=f"{SID}-m0"), HANDLE)
        ensure_approval_on_this_instance(lambda: None, HANDLE)


class TestEmptyThread:
    async def test_thread_without_any_checkpoint_is_stale(self):
        with pytest.raises(StaleApprovalError, match="找不到这条审批的状态"):
            await resolve_resume_thread(_agent_with(InMemorySaver()), session_id=SID, handle=HANDLE, last_message=None)

    async def test_thread_with_a_checkpoint_resumes_as_before(self):
        saver = InMemorySaver()
        await _seed(saver, HANDLE)
        assert await resolve_resume_thread(
            _agent_with(saver), session_id=SID, handle=HANDLE, last_message=None
        ) == HANDLE

    async def test_stand_ins_without_state_are_not_blocked(self):
        agent = SimpleNamespace(agent=object(), config=SimpleNamespace(agent=SimpleNamespace(session_thread=False)))
        assert await resolve_resume_thread(agent, session_id=SID, handle=HANDLE, last_message=None) == HANDLE


async def test_two_pods_resume_only_their_own_approvals_and_clean_up_deleted_sessions(tmp_path, monkeypatch):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "biz.db")}})
    db.init_tables()
    db.create_session(SessionModel(session_id=SID, title="t", username="u"))
    template = str(tmp_path / "ckpt" / "{pod}.db")

    async def boot(pod):
        await ck.shutdown_checkpointers()
        monkeypatch.setenv("EASY_POD_NAME", pod)
        selection = ck.resolve_checkpoint_backend(backend="auto", sqlite_path=template, db_type="mysql")
        kind = await ck.start_checkpoint_backend(
            selection, agent_env="prod", replicas=2, workers_per_pod=1, sticky=True, allow_legacy=False
        )
        assert kind == ck.BACKEND_SQLITE
        return ck.get_session_checkpointer("any")

    try:
        # pod-a 上挂着一条审批；两个 pod 都留有已删除会话 GONE 的 thread
        saver = await boot("pod-a")
        await _seed(saver, HANDLE, f"{GONE}-m1")
        saver = await boot("pod-b")
        await _seed(saver, f"{GONE}-m2")

        # 请求落到 pod-b：实例对不上；即使绕过这一步，本 pod 也找不到这条审批的 checkpoint
        with pytest.raises(StaleApprovalError, match="pod-a"):
            ensure_approval_on_this_instance(lambda: _pending("pod-a"), HANDLE)
        with pytest.raises(StaleApprovalError, match="找不到这条审批的状态"):
            await resolve_resume_thread(_agent_with(saver), session_id=SID, handle=HANDLE, last_message=_pending())
        # pod-b 的对账删掉已删除会话的残留
        assert await ck.prune_deleted_session_threads(db) == 1
        assert set(await ck._list_thread_ids(saver)) == set()

        # 回到 pod-a：审批照常恢复；对账同样删掉它文件里的残留，挂着的审批不动
        saver = await boot("pod-a")
        ensure_approval_on_this_instance(lambda: _pending("pod-a"), HANDLE)
        assert await resolve_resume_thread(
            _agent_with(saver), session_id=SID, handle=HANDLE, last_message=_pending()
        ) == HANDLE
        assert await ck.prune_deleted_session_threads(db) == 1
        assert set(await ck._list_thread_ids(saver)) == {HANDLE}
    finally:
        await ck.shutdown_checkpointers()
    assert sorted(path.name for path in (tmp_path / "ckpt").glob("*.db")) == ["pod-a.db", "pod-b.db"]
