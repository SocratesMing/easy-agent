"""MySQL checkpoint 快照跨 saver 实例恢复，官方 SQLite saver 在会话间共享。"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from easy_agent.persistence import checkpoints
from easy_agent.persistence.checkpoints import (
    PersistentMemorySaver,
    get_session_checkpointer,
    refresh_session_checkpointer,
    release_session_checkpointer,
)
from easy_agent.db.database import Database


async def test_business_db_checkpoint_snapshot_survives_saver_recreation(tmp_path, monkeypatch):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "ckpt.db")}})
    db.init_tables()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row[0] for row in cursor.fetchall()}
    assert "agent_checkpoints" in names

    from easy_agent.db.repositories.agent_state import AgentStateRepository

    class ApprovalState(TypedDict, total=False):
        request: str
        decision: dict

    def wait_for_approval(state):
        decision = interrupt({"request": state["request"]})
        return {"decision": decision}

    builder = StateGraph(ApprovalState)
    builder.add_node("approval", wait_for_approval)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    repository = AgentStateRepository(db)
    first = PersistentMemorySaver("sess-a", repository)
    config = {"configurable": {"thread_id": "sess-a-m1", "checkpoint_ns": ""}}
    graph = builder.compile(checkpointer=first)
    await graph.ainvoke({"request": "delete the file?"}, config)
    assert (await graph.aget_state(config)).tasks[0].interrupts

    restored = PersistentMemorySaver("sess-a", repository)
    restored_graph = builder.compile(checkpointer=restored)
    restored_state = await restored_graph.aget_state(config)
    assert restored_state.tasks[0].interrupts
    result = await restored_graph.ainvoke(Command(resume={"approved": True}), config)
    assert result["decision"] == {"approved": True}

    monkeypatch.setattr(checkpoints, "_mysql_snapshots_active", True)
    monkeypatch.setattr(checkpoints, "_CHECKPOINTERS", {"sess-a": first})
    monkeypatch.setattr(
        "easy_agent.db.providers.get_agent_state_repository",
        lambda: repository,
    )
    # Refreshing a stale, already-cached saver observes a write made by another pod.
    second = PersistentMemorySaver("sess-a", repository)
    second.storage["sess-a-m2"][""] = {"cp-2": (b"", b"", None)}
    second._flush()
    await refresh_session_checkpointer("sess-a")
    assert "sess-a-m2" in first.storage

    await checkpoints.delete_session_threads("sess-a")
    assert repository.load_checkpoint("sess-a") is None
    assert "sess-a" not in checkpoints._CHECKPOINTERS

    # legacy mode remains process-local.
    monkeypatch.setattr(checkpoints, "_sqlite_saver", None)
    monkeypatch.setattr(checkpoints, "_mysql_snapshots_active", False)
    cached = get_session_checkpointer("sess-a")
    assert isinstance(cached, PersistentMemorySaver)
    assert get_session_checkpointer("sess-a") is cached
    cached.storage["thread-1"] = {"checkpoint": {"id": "c1"}}
    release_session_checkpointer("sess-a")
    try:
        assert "thread-1" not in get_session_checkpointer("sess-a").storage
    finally:
        release_session_checkpointer("sess-a")


def test_official_saver_is_shared(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(checkpoints, "_sqlite_saver", sentinel)
    assert get_session_checkpointer("sess-a") is sentinel
    assert get_session_checkpointer("sess-b") is sentinel
