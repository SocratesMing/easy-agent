"""按技能白名单过滤的用户技能视图（专家技能硬隔离，2026-10）。

为什么要单独一层：``SkillsMiddleware`` 的语义是「每个 source 是一个**技能父目录**」，
它自己 ``ls(source)`` 取一级子目录再找 ``SKILL.md``，因此没法用
``sources=["/user-skills/技能A/"]`` 表达白名单（那样扫的是技能 A 内部，等于 0 个技能）。
白名单只能在**发现结果**这一层过滤——也就是这里。

同时它也是「读得到」的兜底：只过滤技能清单，模型仍可 ``read_file`` 直接打开未选中
技能的 ``SKILL.md``（技能全文、脚本路径都写在里面），所以 ``read`` / ``grep`` /
``glob`` / ``download_files`` 一并过滤，未选中的技能一律当作不存在。

与 ``BuiltinSkillsBackend`` 的区别：用户技能库是可写的（``agent.skill_write`` 默认
``new-only``，需要能新建技能），因此本类继承 ``LocalShellBackend``，**只覆盖读操作**，
写 / edit / delete / execute 语义完全不变。

异步方法由框架基类经 ``asyncio.to_thread`` 调到同步方法，因此只重写同步版本。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    FileDownloadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)


class ScopedSkillsBackend(LocalShellBackend):
    """只暴露白名单内技能目录的用户技能库。

    ``allowed`` 为技能目录名集合（一级子目录名）；``None`` 表示不限制（= 改造前行为），
    空集合表示一个技能都不可见。白名单之外的一级目录：``ls`` 不列出、
    ``read`` / ``download`` 报不存在、``grep`` / ``glob`` 结果里过滤掉；
    技能根目录下的散落文件（非目录）原样保留——它们不是技能，不影响发现。
    """

    def __init__(self, root_dir: str | Path, allowed: Iterable[str] | None, **kwargs) -> None:
        super().__init__(root_dir=str(root_dir), virtual_mode=True, **kwargs)
        self._allowed = None if allowed is None else frozenset(allowed)

    # ── 可见性 ────────────────────────────────────────────────────────
    def _first_segment(self, path: str | None) -> str:
        parts = [part for part in str(path or "/").split("/") if part]
        # CompositeBackend 交给路由的是去掉挂载前缀的路径（``/`` = 技能根）
        return parts[0] if parts else ""

    def _visible(self, path: str | None) -> bool:
        if self._allowed is None:
            return True
        segment = self._first_segment(path)
        return not segment or segment in self._allowed

    def _visible_entries(self, entries):
        if self._allowed is None:
            return entries
        kept = []
        for entry in entries:
            segment = self._first_segment(entry.get("path"))
            # 一级**目录**必须在白名单内；根下散落文件（非目录）不是技能，原样保留
            if entry.get("is_dir") and segment and segment not in self._allowed:
                continue
            kept.append(entry)
        return kept

    # ── 读：按白名单过滤 ──────────────────────────────────────────────
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
        if result.matches is not None and self._allowed is not None:
            result = GrepResult(
                error=result.error,
                matches=self._visible_entries(result.matches),
                truncated=result.truncated,
            )
        return result

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if path is not None and not self._visible(path):
            return GlobResult(matches=[])
        result = super().glob(pattern, path)
        if result.matches is not None and self._allowed is not None:
            result.matches = self._visible_entries(result.matches)
        return result

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        if self._allowed is None:
            return super().download_files(paths)
        allowed = [path for path in paths if self._visible(path)]
        downloaded = iter(super().download_files(allowed) if allowed else [])
        return [
            next(downloaded) if self._visible(path) else FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
            for path in paths
        ]
