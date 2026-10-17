import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FORMATS = ("docx", "pptx", "xlsx")


def _files(base: Path):
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        yield path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", FORMATS)
def test_packaged_skill_tree_matches_canonical(name):
    canonical = ROOT / "skills" / name
    packaged = ROOT / "easy_agent" / "skills" / name
    assert canonical.is_dir(), canonical
    assert packaged.is_dir(), packaged

    canon_files = {str(p.relative_to(canonical)): p for p in _files(canonical)}
    pkg_files = {str(p.relative_to(packaged)): p for p in _files(packaged)}
    assert canon_files.keys() == pkg_files.keys(), (
        f"{name} 技能文件集合不一致；请运行 python scripts/sync_skills.py"
    )
    for rel, src in canon_files.items():
        assert _sha256(src) == _sha256(pkg_files[rel]), (
            f"{name}/{rel} 内容漂移；请运行 python scripts/sync_skills.py"
        )


def test_sync_script_check_mode_passes():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_skills.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
