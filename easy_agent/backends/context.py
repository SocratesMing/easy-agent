"""DeepAgents 0.7 兼容：按运行期 SessionContext 路由的 CompositeBackend（D2 共享图）。

0.7 移除了 ``backend=callable`` 工厂，同一张编译图要服务多个会话，只能交给框架一个
**实例**，在运行期经 LangGraph 公开的 ``get_runtime()`` 读出当前 context 再路由。

两层结构（DA-05），两层都是真正的 ``CompositeBackend``：

- :class:`SessionCompositeBackend`：``default`` / ``routes`` / ``sorted_routes`` 每次调用时解析成
  本会话的 CompositeBackend（由 builder 构建、按会话缓存）。路由逻辑直接继承框架实现；框架的
  symlink 感知权限校验（``_resolve_real_path``）会递归进来，拿到真实的 ``FilesystemBackend``
  叶子——改造前这里是占位对象，校验静默失效。
- :class:`ContextBackend`：交给 ``create_deep_agent`` 的顶层对象。``default`` 是上面的会话
  composite；``routes`` 只登记前缀——框架要求「带 execute 的 backend 配 permissions 时，权限路径
  必须落在 route 前缀下」；``sorted_routes`` 置空，完整虚拟路径原样交给会话 composite，框架也就
  不会生成 route→宿主机路径的提示：沙箱里的命令直接用虚拟路径（bwrap 按虚拟路径挂载），
  宿主机路径既不可用，也不该暴露给模型。

改造前的做法是 ``CompositeBackend.register(ContextBackend)`` 冒充子类，并返回占位 default /
routes，框架依赖 composite 内省的逻辑拿到的都是占位对象。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol

from easy_agent.backends.artifacts import AGENT_ARTIFACTS_ROOT

_MISSING_CONTEXT = (
    "ContextBackend 缺少运行期 SessionContext：请使用 "
    "astream(..., context=agent.session_context)"
)


class _UnresolvedBackend(BackendProtocol):
    """没有运行期 context 时会话 composite 的 default。

    只出现在建图期的静态检查里（如「是否用 StateBackend」——答案是否）；真被调用说明调用方
    漏传了 context，直接给出明确的错误。
    """

    def _fail(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError(_MISSING_CONTEXT)

    ls = read = write = edit = delete = glob = grep = _fail
    upload_files = download_files = _fail


_UNRESOLVED = _UnresolvedBackend()


class SessionCompositeBackend(CompositeBackend, SandboxBackendProtocol):
    """按运行期 context 解析成本会话 CompositeBackend 的 composite（路由逻辑沿用框架实现）。"""

    # 不调用 CompositeBackend.__init__：default / routes / sorted_routes 都是按 context 解析的属性。
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
        self.artifacts_root = AGENT_ARTIFACTS_ROOT

    # ── context 解析 ───────────────────────────────────────────
    def _runtime_context(self) -> Any:
        try:
            from langgraph.runtime import get_runtime

            ctx = getattr(get_runtime(), "context", None)
        except (RuntimeError, KeyError, AttributeError):
            ctx = None
        if ctx is None or not hasattr(ctx, "workspace_dir"):
            ctx = self._fallback_context
        return ctx

    def resolve(self, ctx: Any) -> Any:
        """按显式 context 解析本会话的 CompositeBackend（供运行期 / 测试使用）。"""
        if ctx is None or not hasattr(ctx, "workspace_dir"):
            if self._fallback_context is None:
                raise RuntimeError(_MISSING_CONTEXT)
            ctx = self._fallback_context
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
            ctx_user,
            # DA-13 S2：公共技能的启用集合变了（启停后重建 agent）要换一份 backend
            str(getattr(ctx, "builtin_skills_dir", "") or ""),
            ",".join(getattr(ctx, "builtin_skills", ()) or ()),
        )
        backend = self._cache.get(key)
        if backend is None:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)), None)
            backend = self._builder(ctx)
            self._cache[key] = backend
        return backend

    def _current(self) -> Any | None:
        ctx = self._runtime_context()
        return None if ctx is None else self.resolve(ctx)

    def _require_current(self) -> Any:
        current = self._current()
        if current is None:
            raise RuntimeError(_MISSING_CONTEXT)
        return current

    # ── 路由表：本会话的真实对象（建图期没有 context 时为空视图） ──
    @property
    def default(self) -> Any:
        current = self._current()
        return _UNRESOLVED if current is None else current.default

    @property
    def routes(self) -> dict[str, Any]:
        current = self._current()
        return {} if current is None else current.routes

    @property
    def sorted_routes(self) -> list[tuple[str, Any]]:
        current = self._current()
        return [] if current is None else current.sorted_routes

    # ── 命令执行：转给本会话 composite（其 default 再转给会话 shell） ──
    @property
    def id(self) -> str:
        current = self._require_current()
        remote_id = getattr(getattr(current, "default", None), "id", None) or getattr(
            current, "id", None
        )
        return str(remote_id) if remote_id else f"context-{id(current):x}"

    def execute(self, command: str, *, timeout: int | None = None):
        return self._require_current().execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None):
        return await self._require_current().aexecute(command, timeout=timeout)


class ContextBackend(CompositeBackend, SandboxBackendProtocol):
    """交给 ``create_deep_agent`` 的顶层 backend：实例化一次，运行期按 context 路由。"""

    def __init__(
        self,
        builder: Callable[[Any], Any],
        *,
        expected_user: str = "",
        fallback_context: Any = None,
        cache_size: int = 16,
        route_prefixes: Iterable[str] | None = None,
    ) -> None:
        session = SessionCompositeBackend(
            builder,
            expected_user=expected_user,
            fallback_context=fallback_context,
            cache_size=cache_size,
        )
        prefixes = sorted({prefix for prefix in (route_prefixes or ()) if prefix})
        super().__init__(
            default=session,
            routes={prefix: session for prefix in prefixes},
            artifacts_root=AGENT_ARTIFACTS_ROOT,
        )
        # 见模块说明：顶层不按前缀路由（完整路径交给会话 composite），框架也就不会
        # 生成与沙箱虚拟路径语义相矛盾的 route→宿主机路径提示。
        self.sorted_routes = []
        self.session_backend = session

    def resolve(self, ctx: Any) -> Any:
        """按显式 context 解析本会话的 CompositeBackend（兼容旧调用 / 测试）。"""
        return self.session_backend.resolve(ctx)

    @property
    def id(self) -> str:
        return self.session_backend.id
