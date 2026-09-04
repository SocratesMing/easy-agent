"""Bearer API-key authentication for mounted MCP apps.

The middleware protects everything under ``/mcp/<business>/`` and leaves every
other path (notably ``/health``) open. Verification is delegated to an
injectable verifier so tests can run without a database:

    verifier(business, api_key) -> username | None
"""

from __future__ import annotations

import inspect
import logging
import re
from typing import Awaitable, Callable, Optional, Union

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from . import context

logger = logging.getLogger("easy-mcp-server")

MCP_PREFIX = "/mcp/"
_BUSINESS_RE = re.compile(r"^/mcp/([^/]+)")

# (business, api_key) -> username, or None when the key is not usable
ApiKeyVerifier = Callable[[str, str], Union[Optional[str], Awaitable[Optional[str]]]]


def extract_business(path: str) -> str | None:
    """Return the business name encoded in an MCP request path."""
    match = _BUSINESS_RE.match(path)
    return match.group(1) if match else None


def _unauthorized(detail: str) -> JSONResponse:
    # 不区分"key 不存在"与"业务不匹配"，避免被探测
    logger.warning(f"MCP 鉴权失败: {detail}")
    return JSONResponse(
        status_code=401,
        content={"error": "invalid_api_key"},
        headers={"WWW-Authenticate": "Bearer"},
    )


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
        if not header.lower().startswith("bearer "):
            return _unauthorized("缺少 Authorization 头")
        api_key = header[7:].strip()
        if not api_key:
            return _unauthorized("API Key 为空")

        username = self._verifier(business, api_key)
        if inspect.isawaitable(username):
            username = await username
        if not username:
            return _unauthorized("API Key 无效")

        context.set_identity(username, business)
        try:
            response = await call_next(request)
        finally:
            context.clear_identity()
        return response
