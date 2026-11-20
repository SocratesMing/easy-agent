"""前端 SPA 入口与静态资源 Gzip 托管路由。"""

from __future__ import annotations

import gzip
import mimetypes
import os
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

_STATIC_GZIP_EXTS = {".js", ".mjs", ".css", ".svg", ".json", ".woff", ".woff2", ".ttf"}
_STATIC_GZIP_MIN_BYTES = 1024
_STATIC_GZIP_CACHE_MAX = 64
_static_gzip_cache: dict[str, tuple[int, bytes]] = {}


def _is_within_dir(root: str, path: str) -> bool:
    """``path`` 是否在 ``root`` 目录内（两者都应是 realpath）；跨盘符视为不在。"""
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _accepts_gzip(request: Request) -> bool:
    encoding = (request.headers.get("accept-encoding") or "").lower()
    return "gzip" in encoding or "*" in encoding


def _gzip_static_file(path: str) -> bytes | None:
    """读取并压缩静态文件；按 (路径, mtime) 缓存，避免每次请求重复压缩。"""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    cached = _static_gzip_cache.get(path)
    if cached and cached[0] == stat.st_mtime_ns:
        return cached[1]
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < _STATIC_GZIP_MIN_BYTES:
        return None
    body = gzip.compress(raw, compresslevel=6)
    if len(_static_gzip_cache) >= _STATIC_GZIP_CACHE_MAX:
        _static_gzip_cache.clear()
    _static_gzip_cache[path] = (stat.st_mtime_ns, body)
    return body


def build_static_router(*, get_frontend_dist: Callable[[], str]) -> APIRouter:
    """构建 SPA 入口与静态资源兜底路由（须在所有 API 路由之后注册）。"""
    router = APIRouter()

    @router.get("/", response_class=FileResponse)
    async def serve_frontend():
        dist = get_frontend_dist()
        index_html = os.path.join(dist, "index.html")
        if os.path.exists(index_html):  # noqa: ASYNC240 - 单次 stat，切线程开销更大
            return FileResponse(
                index_html,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return FileResponse(index_html)

    @router.get("/{full_path:path}", response_class=FileResponse)
    async def serve_static(full_path: str, request: Request):
        # /agent/* 是接口命名空间，未匹配到任何路由时必须返回 JSON 404，避免落到 SPA 兜底
        if full_path == "agent" or full_path.startswith("agent/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        dist = get_frontend_dist()
        dist_root = os.path.realpath(dist)  # noqa: ASYNC240 - 单次路径解析，切线程开销更大
        static_file = os.path.realpath(os.path.join(dist_root, full_path))  # noqa: ASYNC240 - 单次路径解析
        if not _is_within_dir(dist_root, static_file):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if os.path.isfile(static_file):  # noqa: ASYNC240 - 单次 stat，切线程开销更大
            if (
                os.path.splitext(static_file)[1].lower() in _STATIC_GZIP_EXTS
                and _accepts_gzip(request)
            ):
                compressed = await run_in_threadpool(_gzip_static_file, static_file)
                if compressed is not None:
                    etag = f'W/"{os.stat(static_file).st_mtime_ns}-{len(compressed)}"'
                    if request.headers.get("if-none-match") == etag:
                        return Response(
                            status_code=304,
                            headers={"ETag": etag, "Vary": "Accept-Encoding", "Cache-Control": "no-cache"},
                        )
                    media_type = mimetypes.guess_type(static_file)[0] or "application/octet-stream"
                    return Response(
                        content=compressed,
                        media_type=media_type,
                        headers={
                            "Content-Encoding": "gzip",
                            "Vary": "Accept-Encoding",
                            "Cache-Control": "no-cache",
                            "ETag": etag,
                        },
                    )
            return FileResponse(static_file, headers={"Cache-Control": "no-cache"})

        index_html = os.path.join(dist, "index.html")
        if os.path.exists(index_html):  # noqa: ASYNC240 - 单次 stat，切线程开销更大
            return FileResponse(
                index_html,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        return JSONResponse({"error": "Not found"}, status_code=404)

    return router
