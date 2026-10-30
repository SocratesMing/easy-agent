"""PersistentMemorySaver 持久化裁剪测试（PERF-03 / DA-03）。"""

import logging
import tempfile

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from easy_agent.persistence import checkpoints as checkpoints_module
from easy_agent.persistence.checkpoints import _PERSIST_MAX_THREADS, PersistentMemorySaver


def test_prune_keeps_the_latest_threads_with_all_their_checkpoints():
    saver = PersistentMemorySaver("sess-prune", db=None)
    for t in range(5):
        saver.storage[f"thread-{t}"]["ns"] = {
            f"cp-{i:02d}": (b"", b"", None) for i in range(6)
        }
    saver._prune_for_persistence()

    assert len(saver.storage) == _PERSIST_MAX_THREADS
    # 保留的是最新 thread（thread-4/3/2），thread 内的 checkpoint 一个不删
    assert set(saver.storage) == {"thread-2", "thread-3", "thread-4"}
    for ns_map in saver.storage.values():
        assert len(ns_map["ns"]) == 6


class _FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class _SnapshotStore:
    """业务库里存 checkpoint 快照的两个方法。"""

    def __init__(self):
        self.blob = None

    def save_agent_checkpoint(self, session_id, blob):
        self.blob = blob

    def load_agent_checkpoint(self, session_id):
        return self.blob


def test_oversized_snapshot_is_not_replayed(monkeypatch, caplog):
    monkeypatch.setattr(checkpoints_module, "_PERSIST_MAX_BYTES", 1024)
    store = _SnapshotStore()
    store.blob = b"x" * 2048

    with caplog.at_level(logging.WARNING):
        saver = PersistentMemorySaver("sess-oversize-load", db=store)

    assert not saver.storage
    assert "跳过回放" in caplog.text


def test_oversized_snapshot_skips_persistence_and_warns_once(
    monkeypatch, caplog
):
    """超过单包上限的快照不再反复写库/刷日志（大 PDF base64 场景）。"""

    monkeypatch.setattr(checkpoints_module, "_PERSIST_MAX_BYTES", 1024)
    store = _SnapshotStore()
    saver = PersistentMemorySaver("sess-oversize", db=store)
    saver.storage["thread"]["ns"]["cp"] = (b"", b"", b"x" * 4096)

    with caplog.at_level(logging.ERROR):
        saver._flush()
        saver._flush()

    assert store.blob is None
    assert caplog.text.count("checkpoint 快照超过上限") == 1


async def test_messages_survive_pruning_and_reload():
    """deepagents 的 messages 是 DeltaChannel（只存增量）。

    改造前按 thread 只留 2 个 checkpoint，运行刚结束 messages 就重建成空列表，HITL 恢复时
    模型看不到对话。
    """
    store = _SnapshotStore()
    config = {"configurable": {"thread_id": "t"}}
    with tempfile.TemporaryDirectory() as tmp:
        backend = FilesystemBackend(root_dir=tmp, virtual_mode=True)
        model = _FakeModel(messages=iter([AIMessage("a0"), AIMessage("a1")]))
        agent = create_deep_agent(model=model, backend=backend, checkpointer=PersistentMemorySaver("s", db=store))
        await agent.ainvoke({"messages": [HumanMessage("q0")]}, config)
        await agent.ainvoke({"messages": [HumanMessage("q1")]}, config)

        live = await agent.aget_state(config)
        reloaded = create_deep_agent(model=model, backend=backend, checkpointer=PersistentMemorySaver("s", db=store))
        restored = await reloaded.aget_state(config)

    expected = ["q0", "a0", "q1", "a1"]
    assert [m.content for m in live.values["messages"]] == expected
    assert [m.content for m in restored.values["messages"]] == expected
