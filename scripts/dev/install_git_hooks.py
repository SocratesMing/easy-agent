"""启用仓库自带的 git hooks（.githooks/）：每次提交、切分支、合并、rebase 后刷新 version.json。

用法（Linux / Windows 通用，每个 clone 执行一次）: python scripts/dev/install_git_hooks.py
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from easy_agent.utils.version_info import write_version_file  # noqa: E402

HOOKS_DIR = ROOT / ".githooks"


def main() -> int:
    if os.name != "nt":
        for hook in HOOKS_DIR.iterdir():
            if hook.is_file():
                hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=ROOT, check=True)
    info = write_version_file()
    print("已启用 .githooks（core.hooksPath=.githooks）")
    if info is not None:
        print(f"version.json: {info['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
