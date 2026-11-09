"""公共技能的只读视图（DA-13 S2）：路由 ``/skills/builtin/`` → canonical 公共技能目录。

- 只看得到本会话用户「已添加且启用」的公共技能：其余顶层目录一律当作不存在
  （``ls`` 不列出、``read`` / ``download`` 报不存在、``grep`` / ``glob`` 结果里过滤掉）；
- 所有写操作返回权限错误：公共技能对所有用户共享，要改请复制到 ``/user-skills/`` 再改
  （同名时用户技能覆盖公共技能）。

异步方法由框架基类经 ``asyncio.to_thread`` 调到这里的同步方法，因此只重写同步版本。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    PERMISSION_DENIED,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

_READ_ONLY = (
    "Error: {path} 是公共技能（只读，所有用户共享）。"
    "如需修改，请复制到 /user-skills/ 下再改，同名时用户技能会覆盖公共技能。"
)


class BuiltinSkillsBackend(FilesystemBackend):
    """只读、按启用集合过滤的公共技能目录。"""

    def __init__(self, root_dir: str | Path, enabled: Iterable[str], virtual_root: str = "") -> None:
        """``virtual_root``：挂载点（如 ``/skills/builtin``），只用于报错信息里还原完整路径——
        CompositeBackend 交给路由的是去掉挂载前缀的路径。"""
        super().__init__(root_dir=root_dir, virtual_mode=True)
        self._enabled = frozenset(enabled)
        self._virtual_root = virtual_root.rstrip("/")

    def _visible(self, path: str | None) -> bool:
        """路径的第一段必须是已启用的公共技能；根目录本身可见。"""
        parts = [part for part in str(path or "/").split("/") if part]
        return not parts or parts[0] in self._enabled

    def _visible_entries(self, entries):
        return [entry for entry in entries if self._visible(entry.get("path"))]

    # ── 读 ────────────────────────────────────────────────────────────
    def ls(self, path: str) -> LsResult:
        if not self._visible(path):
            return LsResult(error=f"Path '{path}': path_not_found")
        result = super().ls(path)
        if result.entries is not None:
            result = LsResult(error=result.error, entries=self._visible_entries(result.entries))
        return result

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if not self._visible(file_path):
            return ReadResult(error=f"File '{file_path}' not found")
        return super().read(file_path, offset=offset, limit=limit)

    def grep(self, pattern, path=None, glob=None, *, max_count=None, context_lines=0) -> GrepResult:
        if path is not None and not self._visible(path):
            return GrepResult(matches=[])
        result = super().grep(pattern, path, glob, max_count=max_count, context_lines=context_lines)
        if result.matches is not None:
            result = GrepResult(
                error=result.error, matches=self._visible_entries(result.matches), truncated=result.truncated
            )
        return result

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if path is not None and not self._visible(path):
            return GlobResult(matches=[])
        result = super().glob(pattern, path)
        if result.matches is not None:
            result.matches = self._visible_entries(result.matches)
        return result

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        allowed = [path for path in paths if self._visible(path)]
        downloaded = iter(super().download_files(allowed) if allowed else [])
        return [
            next(downloaded) if self._visible(path) else FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
            for path in paths
        ]

    # ── 写：一律拒绝 ─────────────────────────────────────────────────
    def _read_only(self, file_path: str) -> str:
        return _READ_ONLY.format(path=f"{self._virtual_root}{file_path}")

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=self._read_only(file_path))

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return EditResult(error=self._read_only(file_path))

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=self._read_only(file_path))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=PERMISSION_DENIED) for path, _ in files]
