"""扫描合并冲突：列出每块「我方侧」的内容摘要。

用途：判断「全部取对方」会丢掉我方的哪些改动。
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(r"d:\代码编写\MCP")
PATTERN = re.compile(r"<<<<<<< [^\n]*\n(.*?)\n?=======\n(.*?)\n?>>>>>>> [^\n]*", re.S)


def conflicted_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only", "--diff-filter=U"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> None:
    for rel in conflicted_files():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = PATTERN.findall(text)

        if not blocks:
            print(f"\n{'=' * 78}\n{rel}\n  （无冲突标记：整文件选定，未在我方侧留内容）")
            continue

        print(f"\n{'=' * 78}\n{rel}   [{len(blocks)} 块]")
        for idx, (ours, theirs) in enumerate(blocks, 1):
            our_lines = [ln.strip() for ln in ours.splitlines() if ln.strip()]
            their_lines = [ln.strip() for ln in theirs.splitlines() if ln.strip()]
            print(f"  ── 块 {idx}: 我方 {len(our_lines)} 行 / 对方 {len(their_lines)} 行")
            if not our_lines:
                print("     O| （空 —— 取对方不丢东西）")
            for ln in our_lines[:5]:
                print(f"     O| {ln[:88]}")
            if len(our_lines) > 5:
                print(f"     O| … 其余 {len(our_lines) - 5} 行")


if __name__ == "__main__":
    main()
