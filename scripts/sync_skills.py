#!/usr/bin/env python3
"""把根目录 `skills/`（canonical）同步到包内 `easy_agent/skills/`（打包回退副本）。

用法：
    python scripts/sync_skills.py          # 同步（覆盖包内副本）
    python scripts/sync_skills.py --check  # 仅校验，有漂移时退出码 1（CI 用）

审计项 ARCH-06：office 脚本在 3 个格式 × 2 棵树出现 6 份副本，手工同步易漂移。
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "skills"
PACKAGED = ROOT / "easy_agent" / "skills"
FORMATS = ("docx", "pptx", "xlsx")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_files(base: Path):
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        yield path


def _pairs():
    for fmt in FORMATS:
        canon_dir = CANONICAL / fmt
        if not canon_dir.is_dir():
            continue
        for src in _iter_files(canon_dir):
            rel = src.relative_to(canon_dir)
            yield src, PACKAGED / fmt / rel


def main() -> int:
    check = "--check" in sys.argv[1:]
    drift: list[str] = []
    copied = 0
    for src, dst in _pairs():
        if check:
            if not dst.is_file() or _sha256(src) != _sha256(dst):
                drift.append(str(dst.relative_to(ROOT)))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    if check:
        if drift:
            print("技能副本与 canonical 不一致（请运行 python scripts/sync_skills.py）:")
            for item in drift:
                print(f"  - {item}")
            return 1
        print("技能副本一致")
        return 0
    print(f"已同步 {copied} 个技能文件: {CANONICAL} -> {PACKAGED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
