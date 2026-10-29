"""SqlStore filter/分页语义测试（FW-11）。"""

from easy_agent.db.database import Database
from easy_agent.persistence.stores import SqlStore


def _store(tmp_path) -> SqlStore:
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "store.db")}})
    db.init_tables()
    return SqlStore(db)


def test_filter_equality_and_operators(tmp_path):
    store = _store(tmp_path)
    ns = ("memories", "u1")
    store.put(ns, "a", {"score": 3, "name": "x", "tag": "keep"})
    store.put(ns, "b", {"score": 1, "name": "y", "tag": "drop"})
    store.put(ns, "c", {"score": 5, "name": "z", "tag": "keep"})

    assert {i.key for i in store.search(ns, filter={"tag": "keep"})} == {"a", "c"}
    assert {i.key for i in store.search(ns, filter={"score": {"$gte": 3}})} == {"a", "c"}
    assert {i.key for i in store.search(ns, filter={"score": {"$lt": 3}})} == {"b"}
    assert {i.key for i in store.search(ns, filter={"name": {"$in": ["x", "z"]}})} == {"a", "c"}
    assert {i.key for i in store.search(ns, filter={"name": {"$nin": ["y"]}})} == {"a", "c"}
    assert store.search(ns, filter={"score": {"$eq": 99}}) == []


def test_filter_then_pagination(tmp_path):
    store = _store(tmp_path)
    ns = ("memories", "u2")
    for i in range(10):
        store.put(ns, f"k{i}", {"even": i % 2 == 0, "i": i})

    first = store.search(ns, filter={"even": True}, limit=2, offset=0)
    second = store.search(ns, filter={"even": True}, limit=2, offset=2)
    assert [i.key for i in first] == ["k0", "k2"]
    assert [i.key for i in second] == ["k4", "k6"]


def test_get_and_delete(tmp_path):
    store = _store(tmp_path)
    ns = ("memories", "u3")
    store.put(ns, "x", {"v": 1})
    assert store.get(ns, "x").value == {"v": 1}
    store.delete(ns, "x")
    assert store.get(ns, "x") is None
