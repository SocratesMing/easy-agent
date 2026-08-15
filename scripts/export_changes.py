#!/usr/bin/env python3
"""导出 git 变更文件（相比上次提交 HEAD），保持原目录层级复制到 scripts/changed_files/。

变更类型：
  - 新增(A) / 未跟踪(??) / 修改(M) / 重命名(R)新名：复制工作区当前内容
  - 删除(D)：从上次提交(HEAD)取出删除前的内容
  - 重命名(R)旧名：从 HEAD 取出旧名内容

用法：
  python scripts/export_changes.py

输出：
  scripts/changed_files/        # 保持原层级的变更文件副本
  scripts/changed_files/_changes_manifest.txt  # 变更清单（状态/路径/来源）

顶层目录为项目根，例如变更 easy_agent/api/auth.py 会复制到
scripts/changed_files/easy_agent/api/auth.py。
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 重命名/复制状态码首字母（git status --porcelain）
_RENAME_CODES = {"R", "C"}


def run_git(args, cwd):
    """运行 git 命令，返回 stdout（bytes）。"""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
    )
    return result.stdout


def get_project_root():
    """获取 git 仓库根目录。"""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    root = result.stdout.strip()
    if not root:
        print("错误：当前不在 git 仓库中", file=sys.stderr)
        sys.exit(1)
    return Path(root)


def get_changed_files(root):
    """获取变更文件列表，返回 [(xy, path, old_path), ...]。

    使用 git status --porcelain -z 以正确处理含空格/特殊字符的路径。
    重命名(R)/复制(C)格式：'XY old_path\\0new_path\\0'。
    """
    raw = run_git(["status", "--porcelain", "-z"], str(root))
    # -z 模式以 NUL 分隔记录；重命名/复制占两段（旧名、新名）
    parts = raw.decode("utf-8", errors="replace").split("\0")
    changes = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if not part:
            i += 1
            continue
        xy = part[:2]          # 状态码，如 ' M' 'A ' 'D ' '??' 'R '
        rest = part[3:]        # 路径（重命名时为旧路径）
        old_path = None
        path = rest
        if xy[0] in _RENAME_CODES:
            # 下一段是新路径
            old_path = rest
            i += 1
            if i < len(parts):
                path = parts[i]
        changes.append((xy, path, old_path))
        i += 1
    return changes


def read_from_head(path, root):
    """从上次提交(HEAD)读取文件内容（bytes），不存在返回 None。"""
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=str(root),
        capture_output=True,
    )
    if result.returncode == 0:
        return result.stdout
    return None


def main():
    root = get_project_root()
    script_dir = Path(__file__).resolve().parent
    output_dir = script_dir / "changed_files"

    changes = get_changed_files(root)
    if not changes:
        print("没有变更文件（工作区与上次提交一致）")
        return

    # 清空旧输出目录，避免残留已恢复的文件
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    manifest = []
    copied = 0
    skipped = 0

    for xy, path, old_path in changes:
        status_code = xy.strip() if xy.strip() else "??"
        src = root / path
        dst = output_dir / path
        content = None
        source = None

        if src.exists() and src.is_file():
            # 工作区存在（新增/修改/未跟踪/重命名新名）：复制当前内容
            content = src.read_bytes()
            source = "working"
        else:
            # 工作区不存在（删除）：从 HEAD 取删除前内容
            content = read_from_head(path, root)
            source = "HEAD"

        if content is None:
            manifest.append(f"{status_code}\t{path}\t(skipped: 无法获取内容)")
            skipped += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
        manifest.append(f"{status_code}\t{path}\t(from {source})")
        copied += 1

        # 重命名/复制的旧路径：从 HEAD 取旧名内容
        if old_path:
            old_content = read_from_head(old_path, root)
            if old_content is not None:
                old_dst = output_dir / old_path
                old_dst.parent.mkdir(parents=True, exist_ok=True)
                old_dst.write_bytes(old_content)
                manifest.append(f"{status_code}\t{old_path}\t(renamed-from, from HEAD)")
                copied += 1
            else:
                manifest.append(f"{status_code}\t{old_path}\t(renamed-from, skipped: HEAD 无此文件)")

    # 写变更清单
    manifest_file = output_dir / "_changes_manifest.txt"
    manifest_file.write_text(
        f"# 变更文件清单（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）\n"
        f"# 格式：状态\\t路径\\t来源\n"
        f"# 状态：A=新增 M=修改 D=删除 R=重命名 C=复制 ??=未跟踪\n"
        f"# 来源：working=工作区当前内容  HEAD=上次提交内容（用于删除/重命名旧名）\n"
        + "\n".join(manifest) + "\n",
        encoding="utf-8",
    )

    print(f"完成：复制 {copied} 个文件，跳过 {skipped} 个")
    print(f"输出目录：{output_dir}")
    print(f"变更清单：{manifest_file}")


if __name__ == "__main__":
    main()
