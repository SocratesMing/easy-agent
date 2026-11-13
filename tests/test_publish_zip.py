"""发布包收集边界：Git 忽略的本地状态不能进入 zip。"""

import subprocess
from pathlib import Path

import pytest

from scripts.release import publish_zip


def _write(root: Path, relative: str, content: str = "x") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_collect_files_uses_tracked_sources_and_allows_frontend_dist(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    tracked = [
        _write(tmp_path, "easy_agent/app.py"),
        _write(tmp_path, "easy_agent/config/config-example.yaml"),
    ]
    subprocess.run(
        ["git", "add", *[str(path.relative_to(tmp_path)) for path in tracked]],
        cwd=tmp_path,
        check=True,
    )

    _write(tmp_path, "easy_agent/config/config.yaml", "password: local")
    _write(tmp_path, "easy_agent/mcp.json", "{}")
    _write(tmp_path, "easy_agent/.ruff_cache/probe", "cache")
    _write(tmp_path, "easy_agent/untracked.py", "local source")
    generated = _write(tmp_path, "frontend-vue2/dist/index.html", "built")
    _write(tmp_path, "frontend-vue2/dist/.ruff_cache/probe", "cache")

    monkeypatch.setattr(publish_zip, "ROOT", tmp_path)
    monkeypatch.setattr(publish_zip, "INCLUDE_PATHS", ["easy_agent", "frontend-vue2/dist"])
    monkeypatch.setattr(publish_zip, "SKIP_INSIDE", {})

    files = publish_zip.collect_files()
    relative = {path.relative_to(tmp_path).as_posix() for path in files}

    assert relative == {
        "easy_agent/app.py",
        "easy_agent/config/config-example.yaml",
        generated.relative_to(tmp_path).as_posix(),
    }


def test_validate_package_files_rejects_local_only_path(tmp_path, monkeypatch):
    local_config = _write(tmp_path, "easy_agent/config/config.yaml")
    monkeypatch.setattr(publish_zip, "ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="config/config.yaml"):
        publish_zip.validate_package_files([local_config])
