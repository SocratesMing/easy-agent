"""checkpointer 测试：业务库不再保存快照；官方 SQLite saver 在会话间共享同一实例。"""

from easy_agent.persistence import checkpoints
from easy_agent.persistence.checkpoints import (
    PersistentMemorySaver,
    get_session_checkpointer,
    release_session_checkpointer,
)
from easy_agent.db.database import Database


def test_business_db_does_not_store_checkpoints(tmp_path, monkeypatch):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "ckpt.db")}})
    db.init_tables()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row[0] for row in cursor.fetchall()}
    assert "agent_checkpoints" not in names
    # 业务库也不再提供 checkpoint 读写入口（原来是三个空操作）
    for name in ("load_agent_checkpoint", "save_agent_checkpoint", "delete_agent_checkpoint"):
        assert not hasattr(db, name), name

    # legacy 回退只在进程内：同一会话复用同一个实例，释放后重新拿到的是空的
    monkeypatch.setattr(checkpoints, "_sqlite_saver", None)
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
