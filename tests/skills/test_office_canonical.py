"""xlsx office 脚本集合冻结 + canonical/副本一致性。

Node 版 ``docx`` / ``pptx`` 已删除，共享的 12 个 office Python 脚本只留在 ``xlsx``。
``test_skills_single_source.py`` 负责全部技能的镜像；这里只冻结 xlsx 的 office 脚本集合。
"""

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FORMATS = ("xlsx",)

# xlsx/scripts/office/ 下冻结的 12 个 Python 脚本。
OFFICE_SCRIPT_FILES = (
    "helpers/__init__.py",
    "helpers/merge_runs.py",
    "helpers/simplify_redlines.py",
    "pack.py",
    "soffice.py",
    "unpack.py",
    "validate.py",
    "validators/__init__.py",
    "validators/base.py",
    "validators/docx.py",
    "validators/pptx.py",
    "validators/redlining.py",
)


def _files(base: Path):
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        yield path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _office_dir(fmt: str) -> Path:
    return ROOT / "skills" / fmt / "scripts" / "office"


@pytest.mark.parametrize("name", FORMATS)
def test_packaged_skill_tree_matches_canonical(name):
    canonical = ROOT / "skills" / name
    packaged = ROOT / "easy_agent" / "skills" / name
    assert canonical.is_dir(), canonical
    assert packaged.is_dir(), packaged

    canon_files = {str(p.relative_to(canonical)): p for p in _files(canonical)}
    pkg_files = {str(p.relative_to(packaged)): p for p in _files(packaged)}
    assert canon_files.keys() == pkg_files.keys(), (
        f"{name} 技能文件集合不一致；请运行 python scripts/skills/sync_skills.py --write"
    )
    for rel, src in canon_files.items():
        assert _sha256(src) == _sha256(pkg_files[rel]), (
            f"{name}/{rel} 内容漂移；请运行 python scripts/skills/sync_skills.py --write"
        )


def test_office_script_file_set_is_frozen():
    for fmt in FORMATS:
        office = _office_dir(fmt)
        actual = {
            path.relative_to(office).as_posix()
            for path in office.rglob("*.py")
            if "__pycache__" not in path.parts
        }
        assert actual == set(OFFICE_SCRIPT_FILES), (
            f"{fmt}/scripts/office 的 Python 脚本集合变化：{sorted(actual)}；"
            "如确需增删，请同步更新 OFFICE_SCRIPT_FILES"
        )


def test_sync_script_check_mode_passes():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "skills" / "sync_skills.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
