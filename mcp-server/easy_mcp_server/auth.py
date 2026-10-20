"""Bearer API-key identity resolution for mounted MCP apps.

The middleware covers everything under ``/mcp/<business>/`` and leaves every
other path (notably ``/health``) untouched. Verification is delegated to an
injectable verifier so tests can run without a database:

    verifier(business, api_key) -> username | None

Token 校验已跳过：缺少或无效的 Key 不再返回 401，请求以 ``ANONYMOUS_USER``
身份放行；携带有效 Key 时仍解析为对应用户（行级业务如 strategyqa 依赖它）。
"""

from __future__ import annotations

import inspect
import logging
import re
from typing import Awaitable, Callable, Optional, Union

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from . import context

logger = logging.getLogger("easy-mcp-server")

MCP_PREFIX = "/mcp/"
_BUSINESS_RE = re.compile(r"^/mcp/([^/]+)")

ANONYMOUS_USER = "anonymous"

# (business, api_key) -> username, or None when the key is not usable
ApiKeyVerifier = Callable[[str, str], Union[Optional[str], Awaitable[Optional[str]]]]


def extract_business(path: str) -> str | None:
    """Return the business name encoded in an MCP request path."""
    match = _BUSINESS_RE.match(path)
    return match.group(1) if match else None


class BearerApiKeyMiddleware(BaseHTTPMiddleware):
    """Resolve ``Authorization: Bearer <key>`` to a username per request."""

    def __init__(self, app: ASGIApp, verifier: ApiKeyVerifier) -> None:
        super().__init__(app)
        self._verifier = verifier

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        business = extract_business(request.url.path)
        if business is None:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        api_key = header[7:].strip() if header.lower().startswith("bearer ") else ""

        username = None
        if api_key:
            username = self._verifier(business, api_key)
            if inspect.isawaitable(username):
                username = await username
        username = username or ANONYMOUS_USER

        context.set_identity(username, business)
        try:
            response = await call_next(request)
        finally:
            context.clear_identity()
        return response
