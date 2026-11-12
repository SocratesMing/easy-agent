import json
import shutil
import subprocess

import pytest

from easy_agent.utils import version_info


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    _git(tmp_path, "init", "-b", "release/x")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "tester")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "first commit")
    _git(tmp_path, "remote", "add", "origin", "https://user:secret@example.com/org/repo.git")
    return tmp_path


def test_write_version_file_records_branch_and_commit(repo):
    path = repo / "version.json"
    info = version_info.write_version_file(path, source="test")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == info
    assert saved["branch"] == "release/x"
    assert saved["commit"] == head
    assert saved["commit_short"] == head[:8]
    assert saved["commit_subject"] == "first commit"
    assert saved["dirty"] is False
    assert saved["source"] == "test"
    assert "secret" not in saved["remote"]


def test_collect_from_git_returns_none_outside_repo(tmp_path):
    assert version_info.collect_from_git(tmp_path) is None


def test_get_version_info_prefers_version_file(tmp_path, monkeypatch):
    path = tmp_path / "version.json"
    path.write_text(json.dumps({"branch": "main", "commit": "abc", "commit_short": "abc"}), encoding="utf-8")
    monkeypatch.setattr(version_info, "VERSION_FILE", path)
    version_info.get_version_info.cache_clear()
    try:
        info = version_info.get_version_info()
        assert info["branch"] == "main"
        assert "分支 `main`" in version_info.describe_version(info)
    finally:
        version_info.get_version_info.cache_clear()


def test_get_version_info_without_file_or_git(tmp_path, monkeypatch):
    monkeypatch.setattr(version_info, "VERSION_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(version_info, "collect_from_git", lambda **_: None)
    version_info.get_version_info.cache_clear()
    try:
        assert version_info.get_version_info()["commit"] == version_info.UNKNOWN
    finally:
        version_info.get_version_info.cache_clear()
