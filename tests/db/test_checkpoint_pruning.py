"""PersistentMemorySaver 持久化裁剪测试（PERF-03）。"""

from easy_agent.persistence.checkpoints import (
    _PERSIST_MAX_CHECKPOINTS_PER_THREAD,
    _PERSIST_MAX_THREADS,
    PersistentMemorySaver,
)


def test_prune_keeps_latest_checkpoints_and_threads():
    saver = PersistentMemorySaver("sess-prune", db=None)
    for t in range(5):
        saver.storage[f"thread-{t}"]["ns"] = {
            f"cp-{i:02d}": (b"", b"", None) for i in range(6)
        }
    saver._prune_for_persistence()

    assert len(saver.storage) == _PERSIST_MAX_THREADS
    # 保留的是最新 thread（thread-4/3/2）
    assert set(saver.storage) == {"thread-2", "thread-3", "thread-4"}
    for ns_map in saver.storage.values():
        kept = ns_map["ns"]
        assert len(kept) == _PERSIST_MAX_CHECKPOINTS_PER_THREAD
        assert set(kept) == {"cp-04", "cp-05"}
