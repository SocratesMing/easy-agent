#!/usr/bin/env python3
"""技能单一安装源：仓库根 ``skills/``（canonical）→ 包内 ``easy_agent/skills/``（生成副本）。

设计（A3，见 ``docs/archive/design/2026-10-18-a3-skills-single-source.md``）：

* 唯一可编辑源是仓库根 ``skills/``；包内树由本脚本**单向生成**，禁止反向同步。
* 判定基于 sha256 全量清单（office 三格式共享的 12 个脚本、schema、LICENSE、
  SKILL.md 等全部受管文件），不做抽样校验。
* ``.agents/skills/`` 是开发期工具技能，不参与运行时装配，也不在受管范围内。

用法::

    python scripts/skills/sync_skills.py --check        # 默认动作：校验，漂移 exit 1
    python scripts/skills/sync_skills.py --write        # 单向生成包内副本（会删除包内多余文件）
    python scripts/skills/sync_skills.py --manifest     # 输出确定性 sha256 清单（JSON）

    # 测试/特殊布局（脚本本身只依赖标准库）
    python scripts/skills/sync_skills.py --check --canonical /tmp/skills --packaged /tmp/pkg

退出码：0 一致；1 漂移或 canonical 自身不一致；2 用法/安全保护错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANONICAL = ROOT / "skills"
DEFAULT_PACKAGED = ROOT / "easy_agent" / "skills"
MANIFEST_VERSION = 1
FORMATS = ("docx", "pptx", "xlsx")

# office 三格式共享的 12 个 Python 脚本（相对 ``<format>/scripts/office/``）。
# 三份必须逐字节一致；测试同时冻结这个集合，防止“只改一个格式”的静默漂移。
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

# 生成物/OS 垃圾不进入清单，也不参与漂移判定（与 MANIFEST.in 的
# ``global-exclude __pycache__ *.py[cod]`` 对齐）。
IGNORED_DIR_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache"}
)
IGNORED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


class SkillSourceError(RuntimeError):
    """受管目录缺失、为空，或 canonical/packaged 布局不安全。"""


@dataclass(frozen=True)
class Manifest:
    """受管技能文件的确定性快照（相对路径 → sha256）。"""

    skills: tuple[str, ...]
    files: dict[str, str] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """对排序后的 ``path\\0sha256`` 行做 sha256，跨平台稳定。"""
        payload = "".join(f"{rel}\0{self.files[rel]}\n" for rel in sorted(self.files))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": MANIFEST_VERSION,
            "skills": list(self.skills),
            "count": len(self.files),
            "digest": self.digest,
            "files": {rel: self.files[rel] for rel in sorted(self.files)},
        }


@dataclass(frozen=True)
class Report:
    """canonical 与 packaged 的差异报告（以及 canonical 自身的 office 一致性）。"""

    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    drift: tuple[str, ...] = ()
    office: tuple[str, ...] = ()
    orphan_skills: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or self.drift or self.office)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "ok" if self.ok else "drift",
            "missing": list(self.missing),
            "extra": list(self.extra),
            "drift": list(self.drift),
            "office": list(self.office),
            "orphan_skills": list(self.orphan_skills),
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_ignored(rel: Path) -> bool:
    return (
        any(part in IGNORED_DIR_NAMES for part in rel.parts[:-1])
        or rel.name in IGNORED_FILE_NAMES
        or rel.suffix in IGNORED_SUFFIXES
    )


def _iter_files(base: Path):
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if _is_ignored(rel):
            continue
        yield rel, path


def _skill_dirs(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(path.name for path in root.iterdir() if path.is_dir() and (path / "SKILL.md").is_file())
    )


def build_manifest(root: Path, *, require_skills: bool = True) -> Manifest:
    """对 ``root`` 下每个含 ``SKILL.md`` 的技能目录做全量 sha256 清单。

    ``require_skills=False`` 用于 packaged 树（可能还没生成，或存在多余技能，
    需要把它们也纳入比对而不是直接报错）。
    """
    root = Path(root)
    if not root.is_dir():
        raise SkillSourceError(f"技能目录不存在: {root}")
    skills = _skill_dirs(root)
    if require_skills and not skills:
        raise SkillSourceError(f"{root} 下没有包含 SKILL.md 的技能目录，拒绝操作")
    files: dict[str, str] = {}
    for name in skills:
        base = root / name
        for rel, path in _iter_files(base):
            files[f"{name}/{rel.as_posix()}"] = _sha256(path)
    return Manifest(skills=skills, files=files)


def office_problems(root: Path) -> tuple[str, ...]:
    """校验 office 三格式共享的 12 个脚本是否逐字节一致。

    只在三格式目录都存在时生效；三格式缺一会直接报错（技能集合测试会兜底）。
    """
    office_dirs = {fmt: root / fmt / "scripts" / "office" for fmt in FORMATS}
    present = {fmt: path.is_dir() for fmt, path in office_dirs.items()}
    if not any(present.values()):
        return ()
    if not all(present.values()):
        missing = [fmt for fmt, ok in present.items() if not ok]
        return (f"office 三格式目录不完整（缺 {', '.join(missing)}）；docx/pptx/xlsx 必须同时存在",)

    problems: list[str] = []
    for rel in OFFICE_SCRIPT_FILES:
        hashes: dict[str, str] = {}
        for fmt in FORMATS:
            path = office_dirs[fmt] / rel
            if not path.is_file():
                problems.append(f"{fmt}/scripts/office/{rel}: 缺失")
            else:
                hashes[fmt] = _sha256(path)
        if len(set(hashes.values())) > 1:
            problems.append(f"office/{rel}: docx/pptx/xlsx 内容不一致（全量 hash 不同）")
    return tuple(problems)


def compare(canonical: Manifest, packaged: Manifest, canonical_root: Path) -> Report:
    canonical_keys = set(canonical.files)
    packaged_keys = set(packaged.files)
    return Report(
        missing=tuple(sorted(canonical_keys - packaged_keys)),
        extra=tuple(sorted(packaged_keys - canonical_keys)),
        drift=tuple(
            sorted(
                rel
                for rel in canonical_keys & packaged_keys
                if canonical.files[rel] != packaged.files[rel]
            )
        ),
        office=office_problems(canonical_root),
        orphan_skills=tuple(sorted(set(packaged.skills) - set(canonical.skills))),
    )


def _assert_safe_layout(canonical_root: Path, packaged_root: Path) -> None:
    canonical = canonical_root.resolve()
    packaged = packaged_root.resolve()
    if canonical == packaged:
        raise SkillSourceError("canonical 与 packaged 指向同一目录，拒绝执行")
    if canonical in packaged.parents:
        raise SkillSourceError("packaged 不能位于 canonical 之内，拒绝执行")
    if packaged in canonical.parents:
        raise SkillSourceError("canonical 不能位于 packaged 之内，拒绝执行")


def _print_report(report: Report, canonical_root: Path, packaged_root: Path) -> None:
    print(
        "技能副本与 canonical 不一致"
        f"（canonical={canonical_root}, packaged={packaged_root}）"
    )
    print("  修复：python scripts/skills/sync_skills.py --write（只允许 canonical → 包内）")
    sections = (
        ("missing（canonical 有、副本缺）", report.missing),
        ("drift（内容不同）", report.drift),
        ("extra（副本多余）", report.extra),
        ("office（canonical 三格式共享脚本不一致）", report.office),
    )
    for label, items in sections:
        if not items:
            continue
        print(f"  {label}: {len(items)}")
        for item in items[:20]:
            print(f"    - {item}")
        if len(items) > 20:
            print(f"    ... 其余 {len(items) - 20} 项")
    if report.orphan_skills:
        print(f"  包内多余技能（--write 会删除）: {', '.join(report.orphan_skills)}")


def run_check(canonical_root: Path, packaged_root: Path, as_json: bool) -> int:
    canonical = build_manifest(canonical_root)
    if packaged_root.is_dir():
        packaged = build_manifest(packaged_root, require_skills=False)
    else:
        packaged = Manifest(skills=())
    report = compare(canonical, packaged, canonical_root)

    if as_json:
        payload = {
            "canonical": str(canonical_root),
            "packaged": str(packaged_root),
            "canonical_digest": canonical.digest,
            **report.to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif report.ok:
        print(
            "技能副本一致："
            f"{len(canonical.skills)} skills, {len(canonical.files)} files, "
            f"digest {canonical.digest[:16]}…"
        )
    else:
        _print_report(report, canonical_root, packaged_root)
    return 0 if report.ok else 1


def run_manifest(canonical_root: Path) -> int:
    manifest = build_manifest(canonical_root)
    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _purge_generated_artifacts(root: Path, skills: tuple[str, ...]) -> int:
    removed = 0
    for name in skills:
        base = root / name
        if not base.is_dir():
            continue
        for pycache in sorted(
            base.rglob("__pycache__"), key=lambda path: len(path.parts), reverse=True
        ):
            if pycache.is_dir():
                shutil.rmtree(pycache)
                removed += 1
        for pattern in ("*.pyc", "*.pyo"):
            for path in sorted(base.rglob(pattern)):
                if path.is_file():
                    path.unlink()
    return removed


def _prune_empty_dirs(root: Path, skills: tuple[str, ...]) -> None:
    for name in skills:
        base = root / name
        if not base.is_dir():
            continue
        dirs = sorted((path for path in base.rglob("*") if path.is_dir()), key=lambda p: len(p.parts), reverse=True)
        for path in dirs:
            try:
                path.rmdir()
            except OSError:
                pass


def run_write(canonical_root: Path, packaged_root: Path) -> int:
    canonical = build_manifest(canonical_root)
    _assert_safe_layout(canonical_root, packaged_root)
    packaged_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    for rel in sorted(canonical.files):
        src = canonical_root / rel
        dst = packaged_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file() and _sha256(dst) == canonical.files[rel]:
            continue
        shutil.copy2(src, dst)
        copied += 1

    current = build_manifest(packaged_root, require_skills=False)
    pruned = 0
    for rel in sorted(set(current.files) - set(canonical.files)):
        path = packaged_root / rel
        if path.is_file() or path.is_symlink():
            path.unlink()
            pruned += 1

    removed_skills: list[str] = []
    for name in sorted(set(current.skills) - set(canonical.skills)):
        shutil.rmtree(packaged_root / name)
        removed_skills.append(name)

    caches = _purge_generated_artifacts(packaged_root, canonical.skills)
    _prune_empty_dirs(packaged_root, canonical.skills)

    report = compare(canonical, build_manifest(packaged_root, require_skills=False), canonical_root)
    print(
        f"canonical: {len(canonical.skills)} skills / {len(canonical.files)} files / "
        f"digest {canonical.digest[:16]}…"
    )
    print(
        f"written: {copied} updated, {pruned} stale files pruned, "
        f"{len(removed_skills)} stale skills removed, {caches} __pycache__ dirs removed"
    )
    if removed_skills:
        print(f"removed packaged-only skills: {', '.join(removed_skills)}")
    if report.ok:
        print("技能副本已与 canonical 一致（单向生成完成）")
        return 0
    _print_report(report, canonical_root, packaged_root)
    if report.office:
        print("  注意：--write 不会修改 canonical；请先修 canonical 的三格式共享脚本。")
    return 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="技能单一安装源：canonical skills/ → packaged easy_agent/skills/（单向）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python scripts/skills/sync_skills.py --check\n"
            "  python scripts/skills/sync_skills.py --write\n"
            "  python scripts/skills/sync_skills.py --manifest\n"
        ),
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check",
        action="store_true",
        help="只校验（默认动作）；canonical 与包内不一致时 exit 1",
    )
    action.add_argument(
        "--write",
        action="store_true",
        help="单向生成包内副本：canonical → packaged，并清理包内多余文件",
    )
    action.add_argument(
        "--manifest",
        action="store_true",
        help="输出 canonical 的确定性 sha256 清单（JSON）",
    )
    parser.add_argument(
        "--canonical",
        type=Path,
        default=DEFAULT_CANONICAL,
        help=f"canonical 技能根（默认 {DEFAULT_CANONICAL}）",
    )
    parser.add_argument(
        "--packaged",
        type=Path,
        default=DEFAULT_PACKAGED,
        help=f"包内副本根（默认 {DEFAULT_PACKAGED}）",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="--check 时输出机器可读 JSON",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        if args.manifest:
            return run_manifest(args.canonical)
        if args.write:
            return run_write(args.canonical, args.packaged)
        return run_check(args.canonical, args.packaged, args.as_json)
    except SkillSourceError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"错误: I/O 失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
