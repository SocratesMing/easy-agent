"""生成项目根的 version.json（name + version，version 末尾拼来源 commit id 后 6 位），供离线部署后查看。

用法: python scripts/release/write_version.py [--quiet]
git hook（.githooks/）在提交 / 切分支 / 合并 / rebase 后自动调用；publish_zip.py 打包前也会调用。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from easy_agent.utils.version_info import VERSION_FILE, write_version_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="成功时不输出")
    args = parser.parse_args()

    info = write_version_file()
    if info is None:
        print("[version] 不是 git 仓库或找不到 git，未生成 version.json", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"[version] {VERSION_FILE.name}: {info['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
