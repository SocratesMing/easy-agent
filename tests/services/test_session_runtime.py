from easy_agent.db.database import Database
from easy_agent.services import session_runtime as runtime


def test_stream_lock_busy_and_release(tmp_path, monkeypatch):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "rt.db")}})
    db.init_tables()
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-a:1")
    runtime.acquire_stream(db, "s1")
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-b:2")
    try:
        runtime.acquire_stream(db, "s1")
        raise AssertionError("expected SessionBusyError")
    except runtime.SessionBusyError:
        pass
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-a:1")
    runtime.release_stream(db, "s1")
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-b:2")
    runtime.acquire_stream(db, "s1")


def test_connectors_roundtrip(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "rt.db")}})
    db.init_tables()
    assert runtime.load_connectors(db, "s1") is None
    runtime.save_connectors(db, "s1", ["mysql", "web"])
    assert runtime.load_connectors(db, "s1") == ("mysql", "web")
