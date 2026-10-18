"""对象存储 A：上传原始文件 Store 适配层测试。

覆盖：开关解析（local 默认 / store / 非法值 fail-closed）、StoreBackend 往返、
materialize（原子写 / 0600 权限 / 缓存命中）、跨用户拒绝、长文件名压到 Store
主键上限、删除、Store 不可用抛错（不静默丢文件）、local 模式行为不变。
"""

from pathlib import Path

import pytest
from langgraph.store.memory import InMemoryStore

from easy_agent.services import upload_storage as us

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"  # 非 UTF-8 二进制
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
)


@pytest.fixture()
def store():
    return InMemoryStore()


@pytest.fixture()
def store_mode(monkeypatch, store, tmp_path):
    """store 模式：InMemoryStore 替身 + 独立缓存目录。"""
    monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, us.BACKEND_STORE)
    monkeypatch.setattr(us, "get_agent_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(us, "BASE_CACHE_DIR", tmp_path / "uploads_cache")
    return store


@pytest.fixture()
def local_mode(monkeypatch, tmp_path):
    monkeypatch.delenv(us.ENV_UPLOAD_BACKEND, raising=False)
    monkeypatch.setattr(us, "BASE_CACHE_DIR", tmp_path / "uploads_cache")


def _tmp_upload(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _boom(*args, **kwargs):
    raise RuntimeError("store down")


# ── 开关 ────────────────────────────────────────────────────────────────


class TestSwitch:
    def test_default_is_local(self, local_mode):
        assert us.upload_backend_kind() == us.BACKEND_LOCAL
        assert us.store_mode_enabled() is False
        assert us.upload_backend("alice") is None

    def test_store_mode_binds_namespace_to_user(self, store_mode, tmp_path):
        backend = us.upload_backend("alice")
        assert backend is not None
        backend.upload_files([("/x.txt", b"hi")])
        items = store_mode.search(("uploads", "alice"))
        assert [item.key for item in items] == ["/x.txt"]

    def test_unknown_value_fails_closed(self, monkeypatch):
        monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, "bogus")
        with pytest.raises(us.UploadStorageError) as exc:
            us.upload_backend_kind()
        assert "bogus" in str(exc.value)

    def test_store_mode_requires_username(self, store_mode):
        with pytest.raises(us.UploadStoreAccessDenied):
            us.upload_backend("")

    def test_non_ascii_username_gets_ascii_alias(self, local_mode):
        alias = us.safe_username("张三")
        assert us._NAMESPACE_SAFE_RE.match(alias)
        assert alias == us.safe_username("张三")  # 稳定（跨进程一致）
        assert us.safe_username("alice") == "alice"


# ── 写入 / 读取 ─────────────────────────────────────────────────────────


class TestSaveAndMaterialize:
    def test_text_roundtrip(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "abc12345_report.txt", b"hello-store")

        uri = us.save_upload_to_store(src, "alice", "abc12345_report.txt")
        assert uri == "store://uploads/alice/abc12345_report.txt"

        cached = us.materialize_upload(uri, "alice")
        assert cached.read_bytes() == b"hello-store"
        assert cached.is_relative_to(Path(us.BASE_CACHE_DIR))
        assert cached.stat().st_mode & 0o777 == 0o600
        # 落库口径：Store 的 item key 带前导斜杠
        assert [i.key for i in store_mode.search(("uploads", "alice"))] == [
            "/abc12345_report.txt"
        ]

    def test_binary_roundtrip(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "deadbeef_logo.png", PNG_BYTES)

        uri = us.save_upload_to_store(src, "alice", "deadbeef_logo.png")
        assert us.materialize_upload(uri, "alice").read_bytes() == PNG_BYTES

    def test_binary_keeps_base64_encoding(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "deadbeef_logo.png", PNG_BYTES)
        us.save_upload_to_store(src, "alice", "deadbeef_logo.png")
        item = store_mode.get(("uploads", "alice"), "/deadbeef_logo.png")
        assert item.value["encoding"] == "base64"

    def test_unicode_filename_roundtrip(self, store_mode, tmp_path):
        name = "0a1b2c3d_季度报告.txt"
        src = _tmp_upload(tmp_path, name, "中文内容".encode("utf-8"))

        uri = us.save_upload_to_store(src, "alice", name)
        assert us.materialize_upload(uri, "alice").read_text(encoding="utf-8") == "中文内容"

    def test_empty_file_roundtrip_and_cache_hit(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "abc12345_empty.txt", b"")

        uri = us.save_upload_to_store(src, "alice", "abc12345_empty.txt")
        cached = us.materialize_upload(uri, "alice")
        assert cached.read_bytes() == b""
        # 0 字节文件同样命中缓存（不再访问 Store）
        us.upload_backend("alice").delete("/abc12345_empty.txt")
        assert us.materialize_upload(uri, "alice") == cached

    def test_materialize_cache_hit_skips_store(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "abc12345_report.txt", b"cached-body")
        uri = us.save_upload_to_store(src, "alice", "abc12345_report.txt")

        first = us.materialize_upload(uri, "alice")
        # 对象被删掉后仍能命中缓存（上传名带 file_id 前缀，实际不可变）
        us.upload_backend("alice").delete("/abc12345_report.txt")
        second = us.materialize_upload(uri, "alice")
        assert first == second
        assert second.read_bytes() == b"cached-body"

    def test_materialize_missing_object_is_not_found(self, store_mode, tmp_path):
        with pytest.raises(us.UploadStoreNotFound):
            us.materialize_upload("store://uploads/alice/nosuch.txt", "alice")

    def test_cross_user_materialize_denied(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "abc12345_secret.txt", b"alice-only")
        uri = us.save_upload_to_store(src, "alice", "abc12345_secret.txt")

        with pytest.raises(us.UploadStoreAccessDenied):
            us.materialize_upload(uri, "bob")
        assert not (Path(us.BASE_CACHE_DIR) / "bob").exists()

    def test_invalid_uri_denied(self, store_mode, tmp_path):
        for bad in (
            "store://other/alice/x.txt",
            "store://uploads/alice/../x.txt",
            "store://uploads/alice/sub/x.txt",
            "store://uploads//x.txt",
            "/tmp/local/x.txt",
        ):
            with pytest.raises(us.UploadStoreAccessDenied):
                us.materialize_upload(bad, "alice")

    def test_is_store_uri(self):
        assert us.is_store_uri("store://uploads/alice/x.txt") is True
        assert us.is_store_uri("store:/uploads/alice/x.txt") is True
        assert us.is_store_uri("/data/uploads/alice/x.txt") is False
        assert us.is_store_uri("") is False
        assert us.is_store_uri(None) is False

    def test_single_slash_uri_tolerated(self, store_mode, tmp_path):
        """代理/网关折叠 ``//`` 后（store:/uploads/...）仍然可用。"""
        src = _tmp_upload(tmp_path, "abc12345_report.txt", b"slash-tolerant")
        uri = us.save_upload_to_store(src, "alice", "abc12345_report.txt")

        degraded = uri.replace("store://", "store:/")
        assert us.materialize_upload(degraded, "alice").read_bytes() == b"slash-tolerant"

    def test_long_name_bounded_and_keeps_extension(self, store_mode, tmp_path):
        name = "abc12345_" + "x" * 400 + ".pdf"
        src = _tmp_upload(tmp_path, "bounded.bin", b"%PDF-1.4 fake")

        uri = us.save_upload_to_store(src, "alice", name)
        stored = uri.rsplit("/", 1)[-1]
        assert len(stored) == us.MAX_STORE_KEY_LEN - 1
        assert stored.endswith(".pdf")
        assert stored.startswith("abc12345_")
        assert us.materialize_upload(uri, "alice").read_bytes() == b"%PDF-1.4 fake"

    def test_store_key_rejects_path_components(self, local_mode):
        assert us.store_key("../../etc/passwd") == "/passwd"
        assert us.store_key("a\\b.txt") == "/b.txt"


# ── 删除 ────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_removes_object_and_cache(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "abc12345_report.txt", b"bye")
        uri = us.save_upload_to_store(src, "alice", "abc12345_report.txt")
        cached = us.materialize_upload(uri, "alice")

        assert us.delete_stored_upload(uri, "alice") is True
        assert store_mode.search(("uploads", "alice")) == []
        assert not cached.exists()

    def test_delete_missing_object_returns_false(self, store_mode):
        assert us.delete_stored_upload("store://uploads/alice/nosuch.txt", "alice") is False

    def test_delete_cross_user_denied(self, store_mode, tmp_path):
        src = _tmp_upload(tmp_path, "abc12345_report.txt", b"alice-only")
        uri = us.save_upload_to_store(src, "alice", "abc12345_report.txt")

        with pytest.raises(us.UploadStoreAccessDenied):
            us.delete_stored_upload(uri, "bob")
        assert store_mode.search(("uploads", "alice"))


# ── fail-closed ─────────────────────────────────────────────────────────


class TestFailClosed:
    def test_save_when_store_unavailable_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, us.BACKEND_STORE)
        monkeypatch.setattr(us, "get_agent_store", _boom)
        src = _tmp_upload(tmp_path, "abc12345_a.txt", b"data")

        with pytest.raises(us.UploadStoreUnavailable) as exc:
            us.save_upload_to_store(src, "alice", "abc12345_a.txt")
        assert "store down" in str(exc.value)

    def test_materialize_when_store_unavailable_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, us.BACKEND_STORE)
        monkeypatch.setattr(us, "get_agent_store", _boom)
        monkeypatch.setattr(us, "BASE_CACHE_DIR", tmp_path / "cache")

        with pytest.raises(us.UploadStoreUnavailable):
            us.materialize_upload("store://uploads/alice/x.txt", "alice")

    def test_preflight_is_noop_in_local_mode(self, local_mode):
        assert us.preflight_upload_store() == us.BACKEND_LOCAL

    def test_preflight_passes_when_store_available(self, store_mode):
        assert us.preflight_upload_store() == us.BACKEND_STORE

    def test_preflight_fails_closed_when_store_missing(self, monkeypatch):
        monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, us.BACKEND_STORE)
        monkeypatch.setattr(us, "get_agent_store", _boom)

        with pytest.raises(us.UploadStoreUnavailable):
            us.preflight_upload_store()

    def test_save_rejected_in_local_mode(self, local_mode, tmp_path):
        src = _tmp_upload(tmp_path, "abc12345_a.txt", b"data")
        with pytest.raises(us.UploadStorageError):
            us.save_upload_to_store(src, "alice", "abc12345_a.txt")
        # local 模式绝不写 Store / 缓存
        assert not (Path(us.BASE_CACHE_DIR)).exists()
