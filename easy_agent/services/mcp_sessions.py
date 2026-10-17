"""Persistent MCP session reuse (D3).

``langchain-mcp-adapters`` 0.2.2 exposes two public building blocks:

* :meth:`MultiServerMCPClient.session` -- an async context manager that opens
  and initializes one ``ClientSession`` for a server;
* :func:`langchain_mcp_adapters.tools.load_mcp_tools` -- when called with a live
  session, the returned tools call ``session.call_tool()`` directly instead of
  creating a new session per invocation.

There is no public client-level session pool or ``close()`` API. This module
builds a minimal per-server registry on top of the two public APIs only; no
private attributes of the adapter or MCP SDK are touched.

Two lifecycle details matter:

* An anyio cancel scope must be entered and exited in the same asyncio task,
  so each session is owned by a dedicated task that holds the ``async with``
  block open until shutdown.
* A session belongs to the event loop that created it. Entries from another
  loop are discarded rather than reused.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, ToolException

try:  # pragma: no cover - dependency is always present in production
    from langchain_mcp_adapters.tools import load_mcp_tools as _load_mcp_tools
except ImportError:  # pragma: no cover - optional fallback path
    _load_mcp_tools = None

logger = logging.getLogger("easy-agent")

# Transport-level errors that mean "this session is unusable, rebuild it".
_RETRYABLE_TYPES: tuple[type[BaseException], ...] = ()
try:  # anyio is a hard dependency of mcp, but keep the import defensive
    from anyio import BrokenResourceError, ClosedResourceError, EndOfStream

    _RETRYABLE_TYPES = (BrokenResourceError, ClosedResourceError, EndOfStream)
except ImportError:  # pragma: no cover
    pass

try:  # httpx is a hard dependency of the HTTP transports
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:
    from mcp.shared.exceptions import McpError
except ImportError:  # pragma: no cover
    McpError = None  # type: ignore[assignment, misc]


_TRANSPORT_ALIASES = {
    "streamable-http": "streamable_http",
    "streamablehttp": "streamable_http",
    "http": "streamable_http",
    "sse": "sse",
    "stdio": "stdio",
    "websocket": "websocket",
}

# stdio 子进程占用更明显，TTL 更短；HTTP 会话保留更久。
_DEFAULT_IDLE_SECONDS = {
    "stdio": 300.0,
    "streamable_http": 600.0,
    "sse": 600.0,
    "websocket": 600.0,
    "unknown": 300.0,
}

DEFAULT_CONNECT_TIMEOUT_SECONDS = 60.0
DEFAULT_CLOSE_TIMEOUT_SECONDS = 10.0
# 0 = disabled: keep the previous "transport controls its own timeout" semantics.
DEFAULT_CALL_TIMEOUT_SECONDS = 0.0

_SESSION_ERROR_MARKERS = (
    "connection closed",
    "connection reset",
    "connection lost",
    "broken pipe",
    "transport closed",
    "session closed",
    "session terminated",
    "session is closed",
    "not connected",
    "disconnected",
    "stream closed",
    "end of stream",
    "eof",
)


class SessionAPIUnavailable(RuntimeError):
    """Raised when the installed adapter cannot provide persistent sessions."""


def _env_optional_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r 不是数字，使用默认值", name, raw)
        return None
    if value < 0:
        logger.warning("%s=%s 不能为负数，使用默认值", name, value)
        return None
    return value


def _env_float(name: str, default: float) -> float:
    value = _env_optional_float(name)
    return default if value is None else value


def _transport_of(connection: Any) -> str:
    """Best-effort transport name for TTL / stats bucketing."""
    if not isinstance(connection, dict):
        return "unknown"
    raw = connection.get("transport") or connection.get("type")
    if raw:
        text = str(raw).strip().lower()
        return _TRANSPORT_ALIASES.get(text, text)
    if "command" in connection:
        return "stdio"
    if "url" in connection:
        return "streamable_http"
    return "unknown"


def _connection_key(connection: Any) -> str:
    """Stable key for a connection config; falls back to repr for exotic values."""
    import json

    try:
        return json.dumps(
            connection,
            sort_keys=True,
            ensure_ascii=False,
            default=repr,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return repr(connection)


def _iter_exceptions(exc: BaseException):
    yield exc
    group = getattr(exc, "exceptions", None)
    if group:
        for item in group:
            yield from _iter_exceptions(item)


def _has_cancelled(exc: BaseException) -> bool:
    return any(
        isinstance(item, asyncio.CancelledError) for item in _iter_exceptions(exc)
    )


def _is_timeout_error(exc: BaseException) -> bool:
    return any(isinstance(item, TimeoutError) for item in _iter_exceptions(exc))


def _is_retryable_session_error(exc: BaseException) -> bool:
    """Only transport/session failures are retryable; tool errors are not."""
    if _has_cancelled(exc):
        return False
    for item in _iter_exceptions(exc):
        if isinstance(item, ToolException):
            continue
        if _RETRYABLE_TYPES and isinstance(item, _RETRYABLE_TYPES):
            return True
        if isinstance(item, (ConnectionError, BrokenPipeError, EOFError)):
            return True
        if isinstance(item, OSError) and item.errno in (
            32,  # EPIPE
            53,  # ENOTCONN (Linux/macOS)
            104,  # ECONNRESET
            107,  # ENOTCONN (Linux)
            9,  # EBADF
        ):
            return True
        if httpx is not None and isinstance(item, httpx.TransportError):
            if not isinstance(item, httpx.TimeoutException):
                return True
        message = str(item).lower()
        if McpError is not None and isinstance(item, McpError):
            if any(marker in message for marker in _SESSION_ERROR_MARKERS):
                return True
        if isinstance(item, RuntimeError):
            if any(marker in message for marker in _SESSION_ERROR_MARKERS):
                return True
    return False


def _supports_session_api(client: Any) -> bool:
    return callable(getattr(client, "session", None))


def supports_persistent_sessions(client: Any) -> bool:
    """True when the adapter and this client can provide persistent sessions."""
    return _load_mcp_tools is not None and _supports_session_api(client)


@dataclass
class _SessionEntry:
    manager: "MCPSessionManager"
    scope: str
    server: str
    transport: str
    connection: dict[str, Any]
    client: Any
    loop: asyncio.AbstractEventLoop
    session: Any = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    close_task: asyncio.Task | None = None
    error: BaseException | None = None
    alive: bool = False
    retired: bool = False
    in_flight: int = 0
    last_used: float = 0.0
    idle_handle: asyncio.TimerHandle | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.scope, self.server, _connection_key(self.connection))

    async def open(self) -> None:
        self.last_used = self.loop.time()
        self.task = self.loop.create_task(
            self._run(), name=f"mcp-session:{self.server}"
        )
        timeout = self.manager.connect_timeout
        try:
            if timeout and timeout > 0:
                await asyncio.wait_for(self.ready.wait(), timeout)
            else:
                await self.ready.wait()
        except TimeoutError:
            self.error = TimeoutError(
                f"MCP session open timed out after {timeout:.1f}s "
                f"(server={self.server})"
            )
            await self._cancel_owner()
            raise self.error from None
        if self.error is not None:
            raise self.error
        if not self.alive:
            raise RuntimeError(f"MCP session for {self.server!r} closed during open")

    async def _run(self) -> None:
        try:
            async with self.client.session(self.server) as session:
                self.session = session
                self.alive = True
                self.manager._mark_created(self.transport)
                logger.info(
                    "MCP 会话已建立 | server=%s transport=%s scope=%s",
                    self.server,
                    self.transport,
                    self.scope,
                )
                self.ready.set()
                await self.stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - surface via ready/error
            self.error = exc
            self.manager._mark_failed(self.transport)
            logger.warning(
                "MCP 会话异常退出 | server=%s transport=%s: %s",
                self.server,
                self.transport,
                exc,
            )
        finally:
            self.alive = False
            self.ready.set()

    async def _cancel_owner(self) -> None:
        task = self.task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        self.retired = True
        self._cancel_idle()
        if self.close_task is not None:
            await asyncio.shield(self.close_task)
            return
        if self.task is None:
            return
        self.close_task = self.loop.create_task(self._close())
        await asyncio.shield(self.close_task)

    async def _close(self) -> None:
        task = self.task
        if task is None:
            return
        self.stop.set()
        try:
            timeout = self.manager.close_timeout
            if timeout and timeout > 0:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            else:
                await task
        except TimeoutError:
            logger.warning(
                "MCP 会话关闭超时（%.1fs），取消 owner task | server=%s",
                self.manager.close_timeout,
                self.server,
            )
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            if task.cancelled():
                logger.debug("MCP owner task 已取消 | server=%s", self.server)
            else:
                raise
        except BaseException as exc:  # noqa: BLE001 - close must not leak
            logger.warning(
                "MCP 会话关闭异常 | server=%s: %s", self.server, exc
            )
        finally:
            self.alive = False
            self.manager._mark_closed(self.transport)

    def touch(self) -> None:
        self.last_used = self.loop.time()
        self._schedule_idle()

    def _schedule_idle(self) -> None:
        self._cancel_idle()
        if self.retired:
            return
        ttl = self.manager.idle_seconds_for(self.transport)
        if ttl and ttl > 0:
            self.idle_handle = self.loop.call_later(ttl, self.manager._expire, self)

    def _cancel_idle(self) -> None:
        if self.idle_handle is not None:
            self.idle_handle.cancel()
            self.idle_handle = None


class MCPManagedSession:
    """Duck-typed ``ClientSession`` facade bound to a managed server session.

    Only the two public methods used by ``load_mcp_tools`` are implemented.
    Every call asks the manager for the currently healthy entry, so a cached
    LangChain tool keeps working after idle expiry, invalidation or a rebuild.
    """

    def __init__(
        self,
        manager: "MCPSessionManager",
        *,
        scope: str,
        server: str,
        client: Any,
        connection: dict[str, Any],
    ) -> None:
        self._manager = manager
        self._scope = scope
        self._server = server
        self._client = client
        self._connection = connection

    @property
    def key(self) -> tuple[str, str, str]:
        return (self._scope, self._server, _connection_key(self._connection))

    async def list_tools(self, cursor: str | None = None, **kwargs: Any):
        return await self._manager.list_tools(
            self._scope,
            self._server,
            self._client,
            self._connection,
            cursor=cursor,
            **kwargs,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        progress_callback: Callable[..., Any] | None = None,
        **kwargs: Any,
    ):
        return await self._manager.call_tool(
            self._scope,
            self._server,
            self._client,
            self._connection,
            name,
            arguments,
            progress_callback=progress_callback,
            **kwargs,
        )


class MCPSessionManager:
    """Per-server persistent MCP session registry.

    All state is local to one event loop/process. Tests can construct an
    isolated instance with explicit timeouts; production uses the module-level
    default manager.
    """

    def __init__(
        self,
        *,
        idle_seconds: float | None = None,
        connect_timeout: float | None = None,
        close_timeout: float | None = None,
        call_timeout: float | None = None,
    ) -> None:
        env_idle = _env_optional_float("EASY_MCP_SESSION_IDLE_SECONDS")
        self._idle_override = idle_seconds if idle_seconds is not None else env_idle
        self.connect_timeout = (
            connect_timeout
            if connect_timeout is not None
            else _env_float(
                "EASY_MCP_SESSION_CONNECT_TIMEOUT_SECONDS",
                DEFAULT_CONNECT_TIMEOUT_SECONDS,
            )
        )
        self.close_timeout = (
            close_timeout
            if close_timeout is not None
            else _env_float(
                "EASY_MCP_SESSION_CLOSE_TIMEOUT_SECONDS",
                DEFAULT_CLOSE_TIMEOUT_SECONDS,
            )
        )
        self.call_timeout = (
            call_timeout
            if call_timeout is not None
            else _env_float(
                "EASY_MCP_SESSION_CALL_TIMEOUT_SECONDS",
                DEFAULT_CALL_TIMEOUT_SECONDS,
            )
        )
        self._entries: dict[tuple[str, str, str], _SessionEntry] = {}
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._close_tasks: set[asyncio.Task] = set()
        self._stats: dict[str, dict[str, int]] = {}

    # -- stats ---------------------------------------------------------

    def _stat(self, transport: str) -> dict[str, int]:
        return self._stats.setdefault(
            transport,
            {"created": 0, "reused": 0, "closed": 0, "failed": 0, "retried": 0},
        )

    def _mark_created(self, transport: str) -> None:
        self._stat(transport)["created"] += 1

    def _mark_reused(self, transport: str) -> None:
        self._stat(transport)["reused"] += 1

    def _mark_closed(self, transport: str) -> None:
        self._stat(transport)["closed"] += 1

    def _mark_failed(self, transport: str) -> None:
        self._stat(transport)["failed"] += 1

    def _mark_retried(self, transport: str) -> None:
        self._stat(transport)["retried"] += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Per-transport counters (active + lifecycle) for diagnostics/tests."""
        active: dict[str, int] = {}
        for entry in self._entries.values():
            if entry.alive:
                active[entry.transport] = active.get(entry.transport, 0) + 1
        transports = set(self._stats) | set(active)
        return {
            transport: {
                **self._stat(transport),
                "active": active.get(transport, 0),
            }
            for transport in sorted(transports)
        }

    def idle_seconds_for(self, transport: str) -> float:
        if self._idle_override is not None:
            return self._idle_override
        return _DEFAULT_IDLE_SECONDS.get(transport, _DEFAULT_IDLE_SECONDS["unknown"])

    # -- lifecycle -----------------------------------------------------

    def _is_expired(self, entry: _SessionEntry) -> bool:
        ttl = self.idle_seconds_for(entry.transport)
        return bool(ttl and ttl > 0 and (entry.loop.time() - entry.last_used) > ttl)

    def _lock_for(self, key: tuple[str, str, str]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _track_close(self, task: asyncio.Task) -> None:
        self._close_tasks.add(task)

        def _done(done: asyncio.Task) -> None:
            self._close_tasks.discard(done)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.debug("MCP session close task failed: %s", exc)

        task.add_done_callback(_done)

    def _schedule_close(self, entry: _SessionEntry) -> None:
        loop = entry.loop
        if loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._track_close(loop.create_task(entry.close()))
            return
        try:
            loop.call_soon_threadsafe(self._spawn_close, entry)
        except RuntimeError:  # pragma: no cover - loop shutting down
            return

    def _spawn_close(self, entry: _SessionEntry) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover
            return
        self._track_close(loop.create_task(entry.close()))

    def _retire_nowait(self, entry: _SessionEntry, reason: str) -> None:
        if entry.retired:
            return
        entry.retired = True
        entry._cancel_idle()
        self._entries.pop(entry.key, None)
        logger.info(
            "MCP 会话回收 | server=%s transport=%s reason=%s",
            entry.server,
            entry.transport,
            reason,
        )
        self._schedule_close(entry)

    def _expire(self, entry: _SessionEntry) -> None:
        if entry.retired:
            return
        if entry.in_flight > 0:
            entry._schedule_idle()
            return
        if self._is_expired(entry):
            self._retire_nowait(entry, "idle")
        else:
            entry._schedule_idle()

    def _usable_entry(
        self, key: tuple[str, str, str], loop: asyncio.AbstractEventLoop
    ) -> _SessionEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.loop is not loop:
            self._retire_nowait(entry, "event-loop-changed")
            return None
        if entry.retired or not entry.alive or entry.error is not None:
            self._retire_nowait(entry, "dead")
            return None
        if self._is_expired(entry):
            self._retire_nowait(entry, "idle-expired")
            return None
        return entry

    async def _ensure_entry(
        self,
        scope: str,
        server: str,
        client: Any,
        connection: dict[str, Any],
    ) -> _SessionEntry:
        loop = asyncio.get_running_loop()
        key = (scope, server, _connection_key(connection))
        async with self._lock_for(key):
            entry = self._usable_entry(key, loop)
            if entry is not None:
                entry.client = client
                entry.connection = connection
                entry.touch()
                self._mark_reused(entry.transport)
                return entry

            entry = _SessionEntry(
                manager=self,
                scope=scope,
                server=server,
                transport=_transport_of(connection),
                connection=dict(connection),
                client=client,
                loop=loop,
            )
            self._entries[key] = entry
            try:
                await entry.open()
            except BaseException:
                self._entries.pop(key, None)
                entry.retired = True
                entry._cancel_idle()
                await asyncio.gather(entry.close(), return_exceptions=True)
                raise
            if entry.retired:  # invalidate raced with open
                await asyncio.gather(entry.close(), return_exceptions=True)
                raise SessionAPIUnavailable(
                    f"MCP session for {server!r} was retired during open"
                )
            entry.touch()
            return entry

    # -- public operations --------------------------------------------

    def session_proxy(
        self,
        *,
        scope: str,
        server: str,
        client: Any,
        connection: dict[str, Any],
    ) -> MCPManagedSession:
        return MCPManagedSession(
            self,
            scope=scope,
            server=server,
            client=client,
            connection=connection,
        )

    async def _attempt(
        self,
        entry: _SessionEntry,
        operation: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        entry.in_flight += 1
        try:
            timeout = self.call_timeout
            if timeout and timeout > 0:
                result = await asyncio.wait_for(
                    operation(entry.session), timeout
                )
            else:
                result = await operation(entry.session)
        finally:
            entry.in_flight -= 1
        if not entry.retired:
            entry.touch()
        return result

    async def _invoke_with_retry(
        self,
        scope: str,
        server: str,
        client: Any,
        connection: dict[str, Any],
        operation: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        entry = await self._ensure_entry(scope, server, client, connection)
        try:
            return await self._attempt(entry, operation)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            if _is_timeout_error(exc):
                self._retire_nowait(entry, "call-timeout")
                raise
            if not _is_retryable_session_error(exc):
                raise
            self._mark_retried(entry.transport)
            logger.warning(
                "MCP 会话调用失败，重建一次后重试 | server=%s: %s",
                server,
                exc,
            )
            self._retire_nowait(entry, "transport-error")
            entry = await self._ensure_entry(scope, server, client, connection)
            try:
                return await self._attempt(entry, operation)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except BaseException as retry_exc:
                if _is_timeout_error(retry_exc) or _is_retryable_session_error(
                    retry_exc
                ):
                    self._retire_nowait(entry, "retry-failed")
                raise

    async def list_tools(
        self,
        scope: str,
        server: str,
        client: Any,
        connection: dict[str, Any],
        *,
        cursor: str | None = None,
        **kwargs: Any,
    ):
        return await self._invoke_with_retry(
            scope,
            server,
            client,
            connection,
            lambda session: session.list_tools(cursor=cursor, **kwargs),
        )

    async def call_tool(
        self,
        scope: str,
        server: str,
        client: Any,
        connection: dict[str, Any],
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        progress_callback: Callable[..., Any] | None = None,
        **kwargs: Any,
    ):
        return await self._invoke_with_retry(
            scope,
            server,
            client,
            connection,
            lambda session: session.call_tool(
                name,
                arguments,
                progress_callback=progress_callback,
                **kwargs,
            ),
        )

    async def load_server_tools(
        self,
        client: Any,
        server: str,
        connection: dict[str, Any],
        *,
        scope: str = "",
    ) -> list[BaseTool]:
        """Load LangChain tools bound to a managed persistent session."""
        if not supports_persistent_sessions(client):
            raise SessionAPIUnavailable(
                "客户端或适配器不支持 client.session() / load_mcp_tools()"
            )
        proxy = self.session_proxy(
            scope=scope,
            server=server,
            client=client,
            connection=connection,
        )
        try:
            return await _load_mcp_tools(  # type: ignore[misc]
                proxy,
                server_name=server,
                callbacks=getattr(client, "callbacks", None),
                tool_interceptors=getattr(client, "tool_interceptors", None),
                tool_name_prefix=bool(getattr(client, "tool_name_prefix", False)),
            )
        except BaseException:
            entry = self._entries.get(proxy.key)
            if entry is not None:
                self._retire_nowait(entry, "tool-load-failed")
            raise

    # -- shutdown ------------------------------------------------------

    async def aclose(self) -> None:
        """Close all sessions and wait for close tasks on this loop."""
        for entry in list(self._entries.values()):
            self._retire_nowait(entry, "shutdown")
        await asyncio.sleep(0)
        pending = [task for task in self._close_tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._locks.clear()

    def close_all_sync(self) -> None:
        for entry in list(self._entries.values()):
            self._retire_nowait(entry, "invalidate")
        self._locks.clear()

    def close_scopes_sync(self, scopes: Iterable[str]) -> None:
        targets = {str(scope) for scope in scopes}
        for entry in list(self._entries.values()):
            if entry.scope in targets:
                self._retire_nowait(entry, "scope-invalidated")


# Module-level default manager used by ``services/mcp.py``.
_manager = MCPSessionManager()


def get_session_manager() -> MCPSessionManager:
    return _manager


async def load_server_tools(
    client: Any,
    server: str,
    connection: dict[str, Any],
    *,
    scope: str = "",
) -> list[BaseTool]:
    return await _manager.load_server_tools(
        client, server, connection, scope=scope
    )


async def aclose_mcp_sessions() -> None:
    """Explicitly close all persistent sessions (tests / future lifespan hook)."""
    await _manager.aclose()


def close_mcp_sessions_sync() -> None:
    _manager.close_all_sync()


def close_mcp_scope_sync(scope: str) -> None:
    _manager.close_scopes_sync({scope})


def close_mcp_scopes_sync(scopes: Iterable[str]) -> None:
    _manager.close_scopes_sync(scopes)


def session_stats() -> dict[str, dict[str, int]]:
    return _manager.snapshot()


def _shutdown_at_exit() -> None:  # pragma: no cover - process teardown
    try:
        _manager.close_all_sync()
    except Exception:  # noqa: BLE001 - best effort at interpreter exit
        pass


atexit.register(_shutdown_at_exit)

__all__ = [
    "MCPSessionManager",
    "MCPManagedSession",
    "SessionAPIUnavailable",
    "aclose_mcp_sessions",
    "close_mcp_scope_sync",
    "close_mcp_scopes_sync",
    "close_mcp_sessions_sync",
    "get_session_manager",
    "load_server_tools",
    "session_stats",
    "supports_persistent_sessions",
]
