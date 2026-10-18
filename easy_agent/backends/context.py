"""DeepAgents 0.7 兼容：按运行期 context 解析的 BackendProtocol 实例。

0.7 移除了 `backend=callable` 工厂；这里把「按 SessionContext 构建 CompositeBackend」
包成**实例**，内部通过 LangGraph 公开的 `get_runtime()` 读取当前 context，再按会话
缓存真实 backend。这样同一张编译图可服务多个 session，同时符合 0.7 的实例要求。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol


class _PlaceholderBackend:
    """仅用于满足框架对 CompositeBackend.routes/default 的静态检查。"""


class _PlaceholderSandbox(SandboxBackendProtocol):
    """占位 default：让 supports_execution() 为真，但不会被真正调用。"""

    @property
    def id(self) -> str:
        return "context-backend-placeholder"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise RuntimeError("ContextBackend 占位 backend 不可执行")


class ContextBackend(SandboxBackendProtocol):
    """BackendProtocol 适配器：实例化一次，运行期按 context 路由。"""

    def __init__(
        self,
        builder: Callable[[Any], Any],
        *,
        expected_user: str = "",
        fallback_context: Any = None,
        cache_size: int = 16,
        route_prefixes: Iterable[str] | None = None,
    ) -> None:
        self._builder = builder
        self._expected_user = expected_user or ""
        self._fallback_context = fallback_context
        self._cache: dict[tuple[str, ...], Any] = {}
        self._cache_size = max(1, cache_size)
        # 框架在“带 execute + permissions”场景要求 backend 是 CompositeBackend
        # 且所有 permission 路径都落在某个 route 前缀下。真实的 per-context
        # CompositeBackend 要到运行期才能构建，这里只提供静态前缀用于启动期检查，
        # 不参与实际文件操作（文件操作仍由本类按 context 代理）。
        self._route_prefixes = tuple(sorted({p for p in (route_prefixes or ()) if p}))
        self.artifacts_root = "/"

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

    # ── CompositeBackend 静态视图（仅供框架启动期检查） ───────
    # FilesystemMiddleware 在“backend 支持 execute 且配置了 permissions”时
    # 要求 backend 是 CompositeBackend、且 permission 路径都在 route 前缀下。
    # 真实路由在运行期由 _current() 解析；这里返回占位对象/静态前缀，避免
    # 启用 execute 时改动现有的权限与系统提示词语义。
    @property
    def default(self) -> Any:
        return _PlaceholderSandbox()

    @property
    def routes(self) -> dict[str, Any]:
        return {prefix: _PlaceholderBackend() for prefix in self._route_prefixes}

    @property
    def sorted_routes(self) -> list[tuple[str, Any]]:
        # 保持 _route_host_path_prompt() 为空：当前 ContextBackend 不是真正的
        # CompositeBackend，启动期提示词不包含 route -> host 映射。
        return []

    # ── 命令执行协议（SandboxBackendProtocol） ─────────────────
    @property
    def id(self) -> str:
        """当前 context 对应 backend 的稳定标识（供框架/日志使用）。"""
        current = self._current()
        remote_id = getattr(current, "id", None)
        if remote_id:
            return str(remote_id)
        return f"context-{id(current):x}"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """把命令转发给当前 context 的 CompositeBackend.default。"""
        return self._current().execute(command, timeout=timeout)

    async def aexecute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        return await self._current().aexecute(command, timeout=timeout)

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


# ContextBackend 代理的是运行期 CompositeBackend，但没有静态 route 表。
# 注册为 CompositeBackend 的虚子类，仅让框架的 isinstance() 门禁（权限 +
# execute）认它；方法解析仍走 ContextBackend 自己的代理实现。
CompositeBackend.register(ContextBackend)
