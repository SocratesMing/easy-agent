from easy_agent import checkpoints
from easy_agent.checkpoints import PersistentMemorySaver, get_session_checkpointer
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


def test_postgres_saver_is_shared(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(checkpoints, "_postgres_saver", sentinel)
    assert get_session_checkpointer("sess-a") is sentinel
    assert get_session_checkpointer("sess-b") is sentinel
