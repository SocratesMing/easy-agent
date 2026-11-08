"""跨副本会话运行态测试：流占用锁的互斥与释放、会话连接器的存取往返。"""

from easy_agent.db.database import Database
from easy_agent.services.sessions import runtime as runtime
from easy_agent.utils import deployment


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


def test_hostname_change_does_not_strand_stream_lock(tmp_path, monkeypatch):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "rt.db")}})
    db.init_tables()
    repo = _runtime(db)
    deployment._owner_for_pid.cache_clear()
    try:
        monkeypatch.setattr(deployment.socket, "gethostname", lambda: "bogon")
        owner = runtime.acquire_stream(repo, "s1")
        monkeypatch.setattr(deployment.socket, "gethostname", lambda: "mac.local")
        assert runtime.renew_stream(db, "s1") is True
        assert repo.get_session_runtime("s1")["stream_owner"] == owner
        runtime.release_stream(repo, "s1")
        assert runtime.is_stream_active(repo, "s1") is False
        runtime.acquire_stream(repo, "s1")
    finally:
        deployment._owner_for_pid.cache_clear()
