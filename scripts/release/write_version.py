"""生成项目根的 version.json（分支、commit id、提交时间等），供离线部署后查看来源版本。

用法: python scripts/release/write_version.py [--source git-hook] [--quiet]
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
    parser.add_argument("--source", default="manual", help="写入 version.json 的 source 字段")
    parser.add_argument("--quiet", action="store_true", help="成功时不输出")
    args = parser.parse_args()

    info = write_version_file(source=args.source)
    if info is None:
        print("[version] 不是 git 仓库或找不到 git，未生成 version.json", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"[version] {VERSION_FILE.name}: {info['branch']} @ {info['commit_short']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
