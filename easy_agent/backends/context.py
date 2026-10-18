"""DeepAgents 0.7 兼容：按运行期 context 解析的 BackendProtocol 实例。

0.7 移除了 `backend=callable` 工厂；这里把「按 SessionContext 构建 CompositeBackend」
包成**实例**，内部通过 LangGraph 公开的 `get_runtime()` 读取当前 context，再按会话
缓存真实 backend。这样同一张编译图可服务多个 session，同时符合 0.7 的实例要求。
"""

from __future__ import annotations

from typing import Any, Callable


class ContextBackend:
    """BackendProtocol 适配器：实例化一次，运行期按 context 路由。"""

    def __init__(
        self,
        builder: Callable[[Any], Any],
        *,
        expected_user: str = "",
        fallback_context: Any = None,
        cache_size: int = 16,
    ) -> None:
        self._builder = builder
        self._expected_user = expected_user or ""
        self._fallback_context = fallback_context
        self._cache: dict[tuple[str, ...], Any] = {}
        self._cache_size = max(1, cache_size)

    def _current(self) -> Any:
        try:
            from langgraph.runtime import get_runtime

            runtime = get_runtime()
            ctx = getattr(runtime, "context", None)
        except (RuntimeError, KeyError, AttributeError):
            ctx = None
        return self.resolve(ctx)

    def resolve(self, ctx: Any) -> Any:
        """按显式 context 解析真实 backend（供运行期/测试使用）。"""
        if ctx is None or not hasattr(ctx, "workspace_dir"):
            if self._fallback_context is not None:
                ctx = self._fallback_context
            else:
                raise RuntimeError(
                    "ContextBackend 缺少运行期 SessionContext：请使用 "
                    "astream(..., context=agent.session_context)"
                )
        ctx_user = str(getattr(ctx, "safe_username", "") or "")
        if self._expected_user and ctx_user != self._expected_user:
            raise RuntimeError(
                "ContextBackend 的 SessionContext 用户与建图用户不一致："
                f"expected={self._expected_user!r} got={ctx_user!r}"
            )
        key = (
            str(getattr(ctx, "session_id", "") or ""),
            str(getattr(ctx, "workspace_dir", "") or ""),
            str(getattr(ctx, "user_skills_dir", "") or ""),
            str(getattr(ctx, "long_term_memory_file", "") or ""),
            str(getattr(ctx, "safe_username", "") or ""),
        )
        backend = self._cache.get(key)
        if backend is None:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)), None)
            backend = self._builder(ctx)
            self._cache[key] = backend
        return backend

    # ── 同步协议 ──────────────────────────────────────────────
    def ls(self, path: str):
        return self._current().ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000):
        return self._current().read(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str):
        return self._current().write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        return self._current().edit(
            file_path, old_string, new_string, replace_all=replace_all
        )

    def delete(self, file_path: str):
        return self._current().delete(file_path)

    def glob(self, pattern: str, path: str | None = None):
        return self._current().glob(pattern, path=path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ):
        return self._current().grep(
            pattern, path=path, glob=glob, max_count=max_count
        )

    def upload_files(self, files: list[tuple[str, bytes]]):
        return self._current().upload_files(files)

    def download_files(self, paths: list[str]):
        return self._current().download_files(paths)

    # ── 异步协议 ──────────────────────────────────────────────
    async def als(self, path: str):
        return await self._current().als(path)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000):
        return await self._current().aread(file_path, offset=offset, limit=limit)

    async def awrite(self, file_path: str, content: str):
        return await self._current().awrite(file_path, content)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        return await self._current().aedit(
            file_path, old_string, new_string, replace_all=replace_all
        )

    async def adelete(self, file_path: str):
        return await self._current().adelete(file_path)

    async def aglob(self, pattern: str, path: str | None = None):
        return await self._current().aglob(pattern, path=path)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ):
        return await self._current().agrep(
            pattern, path=path, glob=glob, max_count=max_count
        )

    async def aupload_files(self, files: list[tuple[str, bytes]]):
        return await self._current().aupload_files(files)

    async def adownload_files(self, paths: list[str]):
        return await self._current().adownload_files(paths)
