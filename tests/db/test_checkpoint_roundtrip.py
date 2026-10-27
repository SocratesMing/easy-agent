"""checkpointer 测试：业务库不再保存快照；官方 SQLite saver 在会话间共享同一实例。"""

from easy_agent.persistence import checkpoints
from easy_agent.persistence.checkpoints import PersistentMemorySaver, get_session_checkpointer
from easy_agent.db.database import Database


def test_business_db_does_not_store_checkpoints(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "ckpt.db")}})
    db.init_tables()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row[0] for row in cursor.fetchall()}
    assert "agent_checkpoints" not in names

    saver = PersistentMemorySaver("sess-a", db=db)
    saver.storage["thread-1"] = {"checkpoint": {"id": "c1"}}
    saver._flush()

    restored = PersistentMemorySaver("sess-a", db=db)
    assert "thread-1" not in restored.storage

    cached = get_session_checkpointer("sess-a", db=db)
    assert isinstance(cached, PersistentMemorySaver)


def test_official_saver_is_shared(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(checkpoints, "_sqlite_saver", sentinel)
    assert get_session_checkpointer("sess-a") is sentinel
    assert get_session_checkpointer("sess-b") is sentinel
