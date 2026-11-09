"""DA-03 P2：清理官方 saver 里存量的每消息 thread 与定时任务 thread。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from scripts.db.prune_checkpoint_threads import (
    apply_prune,
    collect_threads,
    format_report,
    plan_prune,
    thread_kind,
)

SID = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 10, 20, tzinfo=UTC)
DAY = timedelta(hours=24)


async def _seed(saver, thread_id, *, ts="2026-01-01T00:00:00+00:00", ns=""):
    checkpoint = empty_checkpoint()
    checkpoint["ts"] = ts
    await saver.aput({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}, checkpoint, {}, {})


def test_thread_kind():
    assert thread_kind(SID) == "session"
    assert thread_kind(f"{SID}-m1") == "per-message"
    assert thread_kind("sched-task-1-run-1") == "scheduled"
    assert thread_kind("__graph_cache__") == "other"


async def test_old_per_message_and_scheduled_threads_are_pruned():
    saver = InMemorySaver()
    for thread_id in (SID, f"{SID}-m1", "sched-t1-r1", "custom"):
        await _seed(saver, thread_id)
    await _seed(saver, f"{SID}-m1", ns="tools:abc")  # 子代理命名空间，随 thread 一起删
    await _seed(saver, f"{SID}-m3", ts="2026-10-19T23:00:00+00:00")  # 一小时前还在写

    plan = plan_prune(await collect_threads(saver), now=NOW, min_age=DAY)

    assert sorted(info.thread_id for info in plan.delete) == [f"{SID}-m1", "sched-t1-r1"]
    assert plan.kept_because == {
        ("session", "保留"): 1,
        ("other", "保留"): 1,
        ("per-message", "近期仍有写入"): 1,
    }
    assert await apply_prune(saver, plan) == 2
    assert set(saver.storage) == {SID, "custom", f"{SID}-m3"}
    assert "每条消息一个的 thread: 2（删除 1；近期仍有写入 1）" in format_report(plan)


class _WriteModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_threads_with_a_pending_approval_are_kept_unless_requested(tmp_path):
    saver = InMemorySaver()
    write = AIMessage(
        content="", tool_calls=[{"name": "write_file", "args": {"file_path": "/x.txt", "content": "x"}, "id": "w1"}]
    )
    agent = create_deep_agent(
        model=_WriteModel(messages=iter([write])),
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        checkpointer=saver,
        interrupt_on={"write_file": True},
    )
    result = await agent.ainvoke({"messages": [HumanMessage("WRITE")]}, {"configurable": {"thread_id": f"{SID}-m2"}})
    assert result.get("__interrupt__")

    threads = await collect_threads(saver)
    later = datetime.now(UTC) + timedelta(seconds=1)

    assert [info.pending for info in threads] == [True]
    kept = plan_prune(threads, now=later, min_age=timedelta(0))
    assert kept.delete == [] and kept.kept_because == {("per-message", "挂着待审批"): 1}
    forced = plan_prune(threads, now=later, min_age=timedelta(0), include_pending=True)
    assert [info.thread_id for info in forced.delete] == [f"{SID}-m2"]
