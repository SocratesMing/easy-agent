"""A3 技能单一安装源守卫。

canonical = 仓库根 ``skills/``；packaged = ``easy_agent/skills/``（单向生成副本）。
本文件守护：

* 运行时 7 个技能在两侧都存在，且全部受管文件逐字节一致（全量 sha256，不做抽样）；
* ``--check`` 确定性、漂移时 exit 1，``--write`` 只做 canonical → packaged；
* ``--write`` 清理包内多余文件/技能，但 canonical 为空时拒绝破坏副本；
* ``--manifest`` 哈希清单稳定、可复算，且忽略 ``__pycache__`` 等生成物。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = ROOT / "skills"
PACKAGED = ROOT / "easy_agent" / "skills"
SYNC_SCRIPT = ROOT / "scripts" / "skills" / "sync_skills.py"
EXPECTED_SKILLS = (
    "docx",
    "expert-creator",
    "mcp-builder",
    "pdf",
    "pptx",
    "skill-creator",
    "xlsx",
)
# 选 pdf/SKILL.md：格式独有文件，改动它不会触发 office 三格式一致性门禁。
DRIFT_TARGET = Path("pdf") / "SKILL.md"
OFFICE_DRIFT_TARGET = Path("docx") / "scripts" / "office" / "validators" / "base.py"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("a3_sync_skills", SYNC_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses 依赖 sys.modules[cls.__module__]，需先注册再执行。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_skills = _load_sync_module()


def _run(canonical: Path, packaged: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SYNC_SCRIPT),
            *args,
            "--canonical",
            str(canonical),
            "--packaged",
            str(packaged),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture()
def trees(tmp_path: Path) -> tuple[Path, Path]:
    canonical = tmp_path / "canonical_skills"
    packaged = tmp_path / "packaged_skills"
    shutil.copytree(CANONICAL, canonical, ignore=IGNORE)
    shutil.copytree(PACKAGED, packaged, ignore=IGNORE)
    return canonical, packaged


def test_runtime_skill_set_is_frozen():
    canonical = sync_skills.build_manifest(CANONICAL)
    packaged = sync_skills.build_manifest(PACKAGED, require_skills=False)
    assert canonical.skills == EXPECTED_SKILLS
    assert packaged.skills == EXPECTED_SKILLS


def test_packaged_tree_is_byte_identical_to_canonical():
    canonical = sync_skills.build_manifest(CANONICAL)
    packaged = sync_skills.build_manifest(PACKAGED, require_skills=False)
    assert packaged.files.keys() == canonical.files.keys()
    mismatched = sorted(
        rel for rel in canonical.files if canonical.files[rel] != packaged.files[rel]
    )
    assert not mismatched, (
        f"以下文件与 canonical 漂移：{mismatched[:5]}；"
        "运行 python scripts/skills/sync_skills.py --write"
    )
    assert canonical.digest == packaged.digest


def test_check_passes_on_repo():
    result = _run(CANONICAL, PACKAGED, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "一致" in result.stdout


def test_manifest_is_deterministic_and_recomputable():
    first = _run(CANONICAL, PACKAGED, "--manifest")
    second = _run(CANONICAL, PACKAGED, "--manifest")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout

    data = json.loads(first.stdout)
    manifest = sync_skills.build_manifest(CANONICAL)
    assert data["version"] == 1
    assert tuple(data["skills"]) == EXPECTED_SKILLS
    assert data["count"] == len(manifest.files)
    assert data["digest"] == manifest.digest
    assert data["files"] == manifest.files
    for rel, digest in data["files"].items():
        payload = (CANONICAL / rel).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == digest


def test_single_canonical_edit_breaks_check_and_write_repairs(trees):
    canonical, packaged = trees
    target = canonical / DRIFT_TARGET
    target.write_text(
        target.read_text(encoding="utf-8") + "\n<!-- a3 drift probe -->\n", encoding="utf-8"
    )

    check = _run(canonical, packaged, "--check")
    assert check.returncode == 1, check.stdout
    assert DRIFT_TARGET.as_posix() in check.stdout

    write = _run(canonical, packaged, "--write")
    assert write.returncode == 0, write.stdout + write.stderr

    after = _run(canonical, packaged, "--check")
    assert after.returncode == 0, after.stdout
    assert (packaged / DRIFT_TARGET).read_bytes() == target.read_bytes()
    assert "a3 drift probe" in (packaged / DRIFT_TARGET).read_text(encoding="utf-8")


def test_shared_office_script_drift_is_detected_and_not_hidden_by_write(trees):
    canonical, packaged = trees
    target = canonical / OFFICE_DRIFT_TARGET
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# a3 office drift probe\n", encoding="utf-8"
    )

    check = _run(canonical, packaged, "--check")
    assert check.returncode == 1, check.stdout
    assert "office" in check.stdout.lower()

    # --write 只从 canonical 复制，不会修改 canonical，因此不能“修好”三格式漂移。
    write = _run(canonical, packaged, "--write")
    assert write.returncode == 1, write.stdout
    assert "office" in write.stdout.lower()


def test_write_prunes_packaged_only_files_and_skills(trees):
    canonical, packaged = trees
    stale = packaged / "pdf" / "STALE.md"
    stale.write_text("stale", encoding="utf-8")
    orphan = packaged / "orphan-skill"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text(
        "---\nname: orphan-skill\ndescription: probe\n---\n", encoding="utf-8"
    )

    before = _run(canonical, packaged, "--check")
    assert before.returncode == 1
    assert "orphan-skill" in before.stdout

    write = _run(canonical, packaged, "--write")
    assert write.returncode == 0, write.stdout + write.stderr
    assert not stale.exists()
    assert not orphan.exists()
    assert not (canonical / "orphan-skill").exists()
    assert _run(canonical, packaged, "--check").returncode == 0


def test_write_refuses_empty_canonical(tmp_path):
    canonical = tmp_path / "empty_skills"
    canonical.mkdir()
    packaged = tmp_path / "packaged_skills"
    packaged.mkdir()
    marker = packaged / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    result = _run(canonical, packaged, "--write")
    assert result.returncode == 2
    assert marker.exists()


def test_manifest_ignores_generated_artifacts(trees):
    canonical, packaged = trees
    baseline = _run(canonical, packaged, "--manifest").stdout

    pycache = canonical / "pdf" / "scripts" / "__pycache__"
    pycache.mkdir(parents=True, exist_ok=True)
    (pycache / "helper.cpython-312.pyc").write_bytes(b"junk")
    (canonical / ".DS_Store").write_bytes(b"junk")
    (canonical / "pdf" / ".DS_Store").write_bytes(b"junk")

    assert _run(canonical, packaged, "--manifest").stdout == baseline
    assert _run(canonical, packaged, "--check").returncode == 0


def test_write_purges_packaged_pycache(trees):
    canonical, packaged = trees
    pycache = packaged / "docx" / "scripts" / "__pycache__"
    pycache.mkdir(parents=True, exist_ok=True)
    (pycache / "helper.cpython-312.pyc").write_bytes(b"stale")

    assert _run(canonical, packaged, "--check").returncode == 0

    write = _run(canonical, packaged, "--write")
    assert write.returncode == 0, write.stdout + write.stderr
    assert not pycache.exists()
