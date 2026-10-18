"""HTTP RED 指标中间件（纯 ASGI，避免 BaseHTTPMiddleware 干扰 SSE 流）。

每完成一个业务请求，把 ``method/path/status/duration`` 交给
:mod:`easy_agent.services.metrics`。使用纯 ASGI 包装 ``send`` 获取最终状态码，
响应流结束（包括 SSE）后才落一次指标，因此 HTTP 延迟包含流式响应时长；
SSE 进程内的独立活跃数/时长由 ``record_sse_*`` 另外提供。

排除项：存活/就绪探针、指标端点自身、OpenAPI 文档、SPA 入口、静态资源，
以及 CORS ``OPTIONS`` 预检（不是业务请求）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from easy_agent.services.metrics import record_http_request

logger = logging.getLogger(__name__)

#: 精确匹配的排除路径（探针、指标自身、文档）。
EXCLUDED_EXACT_PATHS = frozenset(
    {
        "/",
        "/health",
        "/api/health",
        "/agent/health",
        "/api/live",
        "/agent/live",
        "/ready",
        "/api/ready",
        "/agent/ready",
        "/agent/metrics",
        "/api/metrics",
        "/openapi.json",
        "/docs",
        "/redoc",
    }
)

#: 静态资源/文档前缀。
EXCLUDED_PREFIXES = (
    "/static",
    "/assets",
    "/js/",
    "/css/",
    "/img/",
    "/fonts/",
    "/docs/",
    "/redoc/",
)

#: 静态资源后缀。
EXCLUDED_SUFFIXES = (
    ".js",
    ".mjs",
    ".css",
    ".map",
    ".svg",
    ".ico",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".html",
)

#: 预检请求不记入 RED。
EXCLUDED_METHODS = frozenset({"OPTIONS"})


def should_record_request(path: str, method: str = "GET") -> bool:
    """判断一个 HTTP 请求是否纳入 RED 指标。"""
    if str(method or "").upper() in EXCLUDED_METHODS:
        return False
    value = str(path or "/")
    if value in EXCLUDED_EXACT_PATHS:
        return False
    if value.startswith(EXCLUDED_PREFIXES):
        return False
    if value.endswith(EXCLUDED_SUFFIXES):
        return False
    return True


class MetricsMiddleware:
    """记录所有业务 HTTP 请求的 RED 指标（线程安全的进程内计数器）。"""

    def __init__(
        self,
        app: Any,
        *,
        recorder: Callable[[str, str, int, float], None] | None = None,
    ) -> None:
        self.app = app
        self._recorder = recorder or record_http_request

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not should_record_request(
            scope.get("path", ""), scope.get("method", "GET")
        ):
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "UNKNOWN")
        path = str(scope.get("path") or "/")
        started = time.perf_counter()
        status = 500

        async def _send(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                try:
                    status = int(message.get("status", 500))
                except (TypeError, ValueError):
                    status = 500
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            status = 500
            raise
        finally:
            duration = time.perf_counter() - started
            try:
                self._recorder(method, path, status, duration)
            except Exception:  # 指标失败不得影响请求结果
                logger.warning("记录 HTTP 指标失败", exc_info=True)


__all__ = [
    "EXCLUDED_EXACT_PATHS",
    "EXCLUDED_METHODS",
    "EXCLUDED_PREFIXES",
    "EXCLUDED_SUFFIXES",
    "MetricsMiddleware",
    "should_record_request",
]
