"""Request-scoped identity for MCP tool calls.

The auth middleware resolves ``Authorization: Bearer <key>`` to a username and
stores it in a ``ContextVar``. Business tools read it through
:func:`current_username` so they never touch HTTP or database concerns.
"""

from __future__ import annotations

from contextvars import ContextVar

_username: ContextVar[str | None] = ContextVar("mcp_username", default=None)
_business: ContextVar[str | None] = ContextVar("mcp_business", default=None)


def set_identity(username: str, business: str) -> None:
    """Bind the current request to ``username`` for the ``business`` domain."""
    _username.set(username)
    _business.set(business)


def clear_identity() -> None:
    _username.set(None)
    _business.set(None)


def current_username() -> str:
    """Return the username resolved from the request's API key.

    Raises:
        PermissionError: if called outside an authenticated MCP request.
    """
    username = _username.get()
    if not username:
        raise PermissionError("未认证的请求：缺少有效的 MCP API Key")
    return username


def current_business() -> str:
    business = _business.get()
    if not business:
        raise PermissionError("未认证的请求：无法确定业务类型")
    return business
