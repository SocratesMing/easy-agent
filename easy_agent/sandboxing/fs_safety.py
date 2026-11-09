"""Workspace mount safety: refuse external_dirs that expose other users."""

from __future__ import annotations

from pathlib import Path


def _is_within(child: Path, ancestor: Path) -> bool:
    try:
        child.relative_to(ancestor)
        return True
    except ValueError:
        return False


def _check_external_dir_safety(
    real_path: Path, workspace_root: Path, safe_username: str
) -> tuple[bool, str]:
    try:
        ws_root = workspace_root.resolve()
        user_root = (ws_root / safe_username).resolve()
        real = real_path.resolve()
    except Exception:
        return True, ""
    if _is_within(ws_root, real):
        return False, (
            f"目标路径 {real} 是工作区根目录 {ws_root} 本身或其祖先，"
            "挂载后将暴露所有用户的会话目录"
        )
    if _is_within(real, ws_root) and not _is_within(real, user_root):
        return False, (
            f"目标路径 {real} 位于工作区根目录 {ws_root} 下但不在当前用户"
            f"({safe_username})的子树内，挂载后将暴露其他用户数据"
        )
    return True, ""
