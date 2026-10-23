"""checkpointer 测试：业务库快照 saver 写入后能重新加载；官方 SQLite saver 在会话间共享同一实例。"""

from easy_agent.persistence import checkpoints
from easy_agent.persistence.checkpoints import PersistentMemorySaver, get_session_checkpointer
from easy_agent.db.database import Database


def test_checkpoint_roundtrip(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "ckpt.db")}})
    db.init_tables()
    saver = PersistentMemorySaver("sess-a", db=db)
    saver.storage["thread-1"] = {"checkpoint": {"id": "c1"}}
    saver._flush()

    restored = PersistentMemorySaver("sess-a", db=db)
    assert restored.storage["thread-1"]["checkpoint"]["id"] == "c1"

    cached = get_session_checkpointer("sess-a", db=db)
    assert cached is not None


def test_official_saver_is_shared(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(checkpoints, "_sqlite_saver", sentinel)
    assert get_session_checkpointer("sess-a") is sentinel
    assert get_session_checkpointer("sess-b") is sentinel
