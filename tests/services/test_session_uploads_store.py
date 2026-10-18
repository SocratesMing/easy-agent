"""对象存储 A：会话解析接入 Store URI（materialize 后解析 / 定位源文件）。

覆盖：
- ``_find_source_path``：DB 里是 ``store://`` URI 时 materialize 后返回本地路径；
  本地绝对路径仍原样返回（store 模式也不改变既有记录语义）；
  Store 故障/跨用户不会被静默吞成"找不到源文件"。
- ``store_parsed_content``：解析前 materialize，正文照旧落 ``uploads/*.txt``。
- ``ensure_parsed_text``：正文缺失时经 Store 重解析源文件。
"""

from pathlib import Path

import pytest
from langgraph.store.memory import InMemoryStore

from easy_agent.config import Config
from easy_agent.services import session_uploads as su
from easy_agent.services import upload_storage as us


class _FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or []

    def get_session_files(self, session_id):
        return self.rows

    def get_session(self, session_id):
        return None


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_user_workspace_dir",
        staticmethod(lambda username: tmp_path / "ws" / username),
    )
    monkeypatch.setattr(su, "is_enabled", lambda: True)
    monkeypatch.setattr(su, "_cfg_int", lambda name, default: default)


@pytest.fixture()
def store(monkeypatch, tmp_path):
    """store 模式 + InMemoryStore 替身 + 独立缓存目录。"""
    mem = InMemoryStore()
    monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, us.BACKEND_STORE)
    monkeypatch.setattr(us, "get_agent_store", lambda *args, **kwargs: mem)
    monkeypatch.setattr(us, "BASE_CACHE_DIR", tmp_path / "uploads_cache")
    return mem


def _put(tmp_path: Path, name: str, data: bytes, username: str = "testuser") -> str:
    src = tmp_path / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return us.save_upload_to_store(src, username, name)


def _uploads_dir(tmp_path: Path, session_id: str = "s1") -> Path:
    return tmp_path / "ws" / "testuser" / "session" / session_id / "uploads"


def test_find_source_path_materializes_store_uri(store, tmp_path):
    uri = _put(tmp_path, "abc12345_data.txt", b"store-source")
    db = _FakeDB([{"filename": "data.txt", "file_path": uri}])

    found = su._find_source_path("s1", "testuser", "data.txt", db)

    assert found is not None
    assert found.read_bytes() == b"store-source"
    assert found.is_relative_to(Path(us.BASE_CACHE_DIR))


def test_find_source_path_keeps_local_path_in_store_mode(store, tmp_path, monkeypatch):
    local = tmp_path / "uploadfiles" / "abc12345_data.txt"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"local-source")
    db = _FakeDB([{"filename": "data.txt", "file_path": str(local)}])

    # 本地路径不需要 Store：把 Store 弄坏也必须能用
    def _boom(*args, **kwargs):
        raise AssertionError("本地路径不应访问 Store")

    monkeypatch.setattr(us, "get_agent_store", _boom)

    assert su._find_source_path("s1", "testuser", "data.txt", db) == local


def test_find_source_path_propagates_store_failure(store, tmp_path, monkeypatch):
    uri = _put(tmp_path, "abc12345_data.txt", b"store-source")
    db = _FakeDB([{"filename": "data.txt", "file_path": uri}])

    def _boom(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(us, "get_agent_store", _boom)

    with pytest.raises(us.UploadStoreUnavailable):
        su._find_source_path("s1", "testuser", "data.txt", db)


def test_find_source_path_rejects_cross_user_uri(store, tmp_path):
    uri = _put(tmp_path, "abc12345_data.txt", b"alice-only", username="alice")
    db = _FakeDB([{"filename": "data.txt", "file_path": uri}])

    with pytest.raises(us.UploadStoreAccessDenied):
        su._find_source_path("s1", "testuser", "data.txt", db)


def test_store_parsed_content_materializes_store_uri(store, tmp_path):
    body = "从对象存储解析出来的正文\n第二行\n".encode("utf-8")
    uri = _put(tmp_path, "abc12345_data.txt", body)

    entry = su.store_parsed_content(
        "s1", "testuser", uri, "data.txt", len(body), "txt"
    )

    assert entry is not None
    assert entry["status"].startswith("已解析")
    stored = _uploads_dir(tmp_path) / "data.txt"
    assert "从对象存储解析出来的正文" in stored.read_text(encoding="utf-8")


def test_store_parsed_content_propagates_store_failure(store, tmp_path, monkeypatch):
    uri = _put(tmp_path, "abc12345_data.txt", b"body")

    def _boom(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(us, "get_agent_store", _boom)

    with pytest.raises(us.UploadStoreUnavailable):
        su.store_parsed_content("s1", "testuser", uri, "data.txt", 4, "txt")
    # 目录可能已创建，但绝不能写出"看起来已解析"的正文
    assert not (_uploads_dir(tmp_path) / "data.txt").exists()


def test_ensure_parsed_text_reparses_from_store_uri(store, tmp_path):
    uri = _put(tmp_path, "abc12345_data.txt", b"reparsed-from-store")
    db = _FakeDB([{"filename": "data.txt", "file_path": uri}])

    parsed = su.ensure_parsed_text("s1", "testuser", "data.txt", db)

    assert parsed["text"] == "reparsed-from-store"
    assert parsed["status"].startswith("已解析")
    assert parsed["reparsed"] is True
    assert (_uploads_dir(tmp_path) / "data.txt").exists()
