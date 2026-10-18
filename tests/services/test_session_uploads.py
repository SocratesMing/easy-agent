"""``session_uploads`` 本地路径行为回归（store 模式见 test_session_uploads_store.py）。

这里是 services 层的聚焦用例：直接用假 DB + 临时工作区，不经过 FastAPI /
lifespan，验证「解析落盘 → 清单 → 按需重解析 → 本轮预览」的本地链路没有被对象
存储改造影响。
"""

from pathlib import Path

import pytest

from easy_agent.bootstrap.config import Config
from easy_agent.services import session_uploads as su


class _FakeDB:
    """只实现 session_uploads 用到的最小会话数据端口。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.session = None

    def get_session_files(self, session_id):
        return self.rows

    def get_session(self, session_id):
        return self.session


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """工作区落到 tmp_path；file_context 配置固定为默认值。"""
    monkeypatch.setattr(
        Config,
        "get_user_workspace_dir",
        staticmethod(lambda username: tmp_path / "ws" / username),
    )
    monkeypatch.setattr(su, "is_enabled", lambda: True)
    overrides = {}
    monkeypatch.setattr(
        su, "_cfg_int", lambda name, default: overrides.get(name, default)
    )
    return overrides


def _upload(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _uploads_dir(tmp_path: Path, session_id: str = "s1") -> Path:
    return tmp_path / "ws" / "testuser" / "session" / session_id / "uploads"


def test_store_parsed_content_writes_body_and_entry(tmp_path):
    src = _upload(tmp_path, "abc12345_data.txt", "第一行\n第二行\n".encode("utf-8"))

    entry = su.store_parsed_content(
        "s1", "testuser", str(src), "data.txt", src.stat().st_size, "txt"
    )

    assert entry is not None
    assert entry["stored_path"] == "uploads/data.txt"
    assert entry["status"].startswith("已解析")
    stored = _uploads_dir(tmp_path) / "data.txt"
    body = stored.read_text(encoding="utf-8")
    assert "源文件: data.txt" in body
    assert "第一行" in body


def test_store_parsed_content_marks_empty_parse_as_failure(tmp_path):
    src = _upload(tmp_path, "abc12345_empty.txt", b"")

    entry = su.store_parsed_content(
        "s1", "testuser", str(src), "empty.txt", 0, "txt"
    )

    assert entry is not None
    assert entry["status"] == "解析失败(无可提取文本)"
    assert "解析失败" in entry["line"]


def test_store_parsed_content_skipped_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "is_enabled", lambda: False)
    src = _upload(tmp_path, "abc12345_data.txt", b"body")

    assert (
        su.store_parsed_content("s1", "testuser", str(src), "data.txt", 4, "txt")
        is None
    )
    assert not _uploads_dir(tmp_path).exists()


def test_ensure_parsed_text_reuses_stored_body(tmp_path):
    src = _upload(tmp_path, "abc12345_data.txt", b"reusable-body")
    su.store_parsed_content("s1", "testuser", str(src), "data.txt", 13, "txt")

    parsed = su.ensure_parsed_text("s1", "testuser", "data.txt", _FakeDB())

    assert parsed["text"] == "reusable-body"
    assert parsed["status"] == "已解析"
    assert parsed["reparsed"] is False


def test_find_source_path_uses_db_record(tmp_path):
    src = _upload(tmp_path, "uploadfiles/abc12345_data.txt", b"local-source")
    db = _FakeDB([{"filename": "data.txt", "file_path": str(src)}])

    assert su._find_source_path("s1", "testuser", "data.txt", db) == src


def test_find_source_path_scans_uploadfiles_fallback(tmp_path):
    uploadfiles = tmp_path / "ws" / "testuser" / "uploadfiles"
    uploadfiles.mkdir(parents=True)
    src = uploadfiles / "deadbeef_data.txt"
    src.write_bytes(b"scanned")

    assert su._find_source_path("s1", "testuser", "data.txt", _FakeDB()) == src


def test_find_source_path_returns_none_when_missing(tmp_path):
    assert su._find_source_path("s1", "testuser", "nope.txt", _FakeDB()) is None


def test_append_manifest_drops_oldest_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MANIFEST_HEADER", "HEADER\n")
    entries = [
        {"line": f"- file{i}.txt | 1 B | 00:00 | 已解析 | 正文: uploads/file{i}.txt"}
        for i in range(3)
    ]
    # manifest_max_chars 只能放下最新 2 条
    budget = len("HEADER\n") + sum(len(e["line"]) + 1 for e in entries[:2]) + 2
    monkeypatch.setattr(su, "_cfg_int", lambda name, default: budget if name == "manifest_max_chars" else default)

    kept = su.append_manifest("s1", "testuser", entries, _FakeDB())

    text = su.read_manifest("s1", "testuser", _FakeDB())
    assert kept == 2
    assert "file2.txt" in text and "file0.txt" not in text
    assert "较早的 1 个文件条目已省略" in text


def test_build_turn_preview_has_header_and_body(tmp_path):
    src = _upload(tmp_path, "abc12345_data.txt", b"preview-body")
    su.store_parsed_content("s1", "testuser", str(src), "data.txt", 12, "txt")

    preview = su.build_turn_preview("s1", "testuser", [{"filename": "data.txt"}], _FakeDB())

    assert "用户本轮上传的文件" in preview
    assert "preview-body" in preview
