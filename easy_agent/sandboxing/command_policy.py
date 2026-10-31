"""Shell 命令策略：识别删除类命令与目录删除。

``agent/hitl.py``（删除类命令进人工审批）和 ``backends/shell.py``（目录删除执行前直接拒绝）
都用这里的规则；放在基础设施层，避免 backends 反向依赖 agent。
"""

from __future__ import annotations

import re

DESTRUCTIVE_CMD_PATTERNS = [
    r"\brm\b",
    r"\brmdir\b",
    r"\bunlink\b",
    r"\bshred\b",
    r"\bfind\s+.*\s-delete\b",
    r"\bdel\b",
    r"\berase\b",
    r"\brd\b",
    r"\bRemove-Item\b",
    r"sudo\s+rm\b",
    r"/\s*bin\s*/\s*rm\b",
]

DIR_DELETION_PATTERNS = [
    r"\brmdir\b",
    r"\brm\b\s+(-\w*[rRd])",
    r"\bfind\s+.*\s-delete\b",
    r"\bshred\b\s+(-\w*[rR])",
    r"\brd\b(?:\s+-\w*s)?",
    r"\bdel\b(?:\s+[^&|;]*)\s/-s\b",
    r"\bRemove-Item\b[^;|&]*-Recurse\b",
]


def is_deletion_command(command: str) -> bool:
    """命令是否命中删除类模式（rm / unlink / shred / del / Remove-Item 等，含目录删除）。"""
    if not command:
        return False
    return any(re.search(pat, command) for pat in DESTRUCTIVE_CMD_PATTERNS)


def is_directory_deletion(command: str) -> bool:
    """命令是否命中目录删除模式（``backends/shell.py`` 执行前直接拒绝，不进审批）。"""
    if not command:
        return False
    return any(re.search(pat, command) for pat in DIR_DELETION_PATTERNS)
