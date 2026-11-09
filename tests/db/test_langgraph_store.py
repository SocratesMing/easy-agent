"""LangGraph store 测试：SqlStore 在业务库上的 put / get / search / delete 往返。"""

from easy_agent.db.database import Database
from easy_agent.persistence.stores import SqlStore


def test_sql_store_roundtrip(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "store.db")}})
    db.init_tables()
    store = SqlStore(db)
    store.put(("users", "u1"), "prefs", {"theme": "dark"})
    item = store.get(("users", "u1"), "prefs")
    assert item is not None
    assert item.value["theme"] == "dark"
    found = store.search(("users",))
    assert any(i.key == "prefs" for i in found)
    store.delete(("users", "u1"), "prefs")
    assert store.get(("users", "u1"), "prefs") is None
