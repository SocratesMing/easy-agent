"""跨副本会话运行态测试：流占用锁的互斥与释放、会话连接器的存取往返。"""

from easy_agent.db.database import Database
from easy_agent.services.sessions import runtime as runtime


def _runtime(db):
    """T3c：会话运行期状态走领域 repository（Database 转发方法已删除）。"""
    from easy_agent.db.repositories.session_runtime import SessionRuntimeRepository

    return SessionRuntimeRepository(db)


def test_stream_lock_busy_and_release(tmp_path, monkeypatch):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "rt.db")}})
    db.init_tables()
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-a:1")
    runtime.acquire_stream(_runtime(db), "s1")
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-b:2")
    try:
        runtime.acquire_stream(_runtime(db), "s1")
        raise AssertionError("expected SessionBusyError")
    except runtime.SessionBusyError:
        pass
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-a:1")
    runtime.release_stream(_runtime(db), "s1")
    monkeypatch.setattr(runtime, "instance_owner", lambda: "pod-b:2")
    runtime.acquire_stream(_runtime(db), "s1")


def test_connectors_roundtrip(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "rt.db")}})
    db.init_tables()
    assert runtime.load_connectors(_runtime(db), "s1") is None
    runtime.save_connectors(_runtime(db), "s1", ["mysql", "web"])
    assert runtime.load_connectors(_runtime(db), "s1") == ("mysql", "web")
