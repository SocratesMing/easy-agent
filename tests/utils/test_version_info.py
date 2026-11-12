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
    return tmp_path


def test_write_version_file_keeps_only_name_and_version(repo):
    path = repo / "version.json"
    info = version_info.write_version_file(path)

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == info
    assert saved == {"name": "easy-agent", "version": f"{version_info.package_version()}.{head[-6:]}"}


def test_build_version_appends_last_six_chars_of_commit():
    commit = "6f9557d191badd1d14809489bc0f8e5910c64f43"
    assert version_info.build_version(commit) == f"{version_info.package_version()}.c64f43"
    assert version_info.build_version(None) == version_info.package_version()


def test_collect_from_git_returns_none_outside_repo(tmp_path):
    assert version_info.collect_from_git(tmp_path) is None


def test_get_version_info_drops_extra_fields_from_file(tmp_path, monkeypatch):
    path = tmp_path / "version.json"
    path.write_text(
        json.dumps({"name": "easy-agent", "version": "0.1.0.abcdef", "branch": "main", "commit": "abc"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(version_info, "VERSION_FILE", path)
    version_info.get_version_info.cache_clear()
    try:
        assert version_info.get_version_info() == {"name": "easy-agent", "version": "0.1.0.abcdef"}
    finally:
        version_info.get_version_info.cache_clear()


def test_get_version_info_without_file_or_git(tmp_path, monkeypatch):
    monkeypatch.setattr(version_info, "VERSION_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(version_info, "collect_from_git", lambda *_: None)
    version_info.get_version_info.cache_clear()
    try:
        assert version_info.get_version_info() == {"name": "easy-agent", "version": version_info.package_version()}
    finally:
        version_info.get_version_info.cache_clear()
