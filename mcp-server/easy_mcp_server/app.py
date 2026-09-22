"""Composition root: discover businesses, mount them, wire authentication."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Iterable

from fastapi import FastAPI, Request
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__, businesses, contract, db, registry
from .auth import BearerApiKeyMiddleware, MCP_PREFIX, ApiKeyVerifier

logger = logging.getLogger("easy-mcp-server")

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5


def shutdown_timeout_seconds() -> int:
    """关闭时等待进行中请求的上限（MCP_SHUTDOWN_TIMEOUT，缺省 5 秒）。

    uvicorn 的 `--timeout-graceful-shutdown` 只覆盖连接与后台任务，
    **不覆盖 lifespan 的收尾**，所以这里必须自己兜一层，否则 Ctrl+C 会卡住。
    """
    raw = os.environ.get("MCP_SHUTDOWN_TIMEOUT", "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_SHUTDOWN_TIMEOUT_SECONDS


def discover_businesses() -> list[tuple[str, FastMCP]]:
    """Import every business package and collect ``(name, FastMCP)`` pairs.

    A business is a package under ``businesses/`` exposing ``build() -> FastMCP``.
    The package name becomes the URL suffix.

    业务清单由 ``contract.business_names()`` 提供（唯一事实来源），这里只负责
    把它们 import 起来并调用工厂——发现规则不允许在别处再抄一份。
    """
    found: list[tuple[str, FastMCP]] = []
    for name in contract.business_names():
        module = importlib.import_module(f"{businesses.__name__}.{name}")
        build = getattr(module, "build", None)
        if not callable(build):
            logger.warning(f"业务包 {name} 缺少 build()，已跳过")
            continue
        found.append((name, build()))
    return found


def _apply_transport_security(mcp: FastMCP) -> None:
    """按环境变量放行 Host/Origin。

    FastMCP 仅对 127.0.0.1/localhost 默认放行；通过域名或反代访问时若不放行，
    所有请求会被拒绝（实测返回 421）。
    """
    hosts = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    origins = os.environ.get("MCP_ALLOWED_ORIGINS", "").strip()
    if not (hosts or origins):
        return
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in hosts.split(",") if h.strip()],
        allowed_origins=[o.strip() for o in origins.split(",") if o.strip()],
    )
    logger.info(f"[mcp-server] 传输安全白名单已启用: hosts={hosts} origins={origins or '-'}")


def _mount_businesses(app: FastAPI, items: Iterable[tuple[str, FastMCP]]) -> list[str]:
    mounted: list[str] = []
    for name, mcp in items:
        # 必须在 streamable_http_app() 之前设置：设置项在构造 session manager 时读取
        _apply_transport_security(mcp)
        path = f"{MCP_PREFIX}{name}"
        app.mount(path, mcp.streamable_http_app())
        mounted.append(name)
        logger.info(f"[mcp-server] 已挂载业务: {name} -> {path}/")
    return mounted


def create_app(verifier: ApiKeyVerifier, businesses_override=None) -> FastAPI:
    """Build the FastAPI app that hosts every MCP business.

    Args:
        verifier: callable resolving ``(business, api_key)`` to a username.
        businesses_override: optional list of ``(name, FastMCP)`` for tests.
            传入它还**关闭**业务注册表发布——测试不该写共享库。
    """
    use_discovery = businesses_override is None
    items = list(
        businesses_override if businesses_override is not None else discover_businesses()
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 把业务清单发布到共享库 mcp_businesses，主应用据此渲染设置页并签发 Key。
        # 放在 lifespan 而不是模块导入期：只有当服务真的要对外提供业务时才登记。
        if use_discovery:
            # 数据库目标先打出来：库名与主应用不一致时症状是"签了 key 却一律 401"，
            # 没有任何报错指向配置。配置非法也不阻塞启动（describe_target 自己兜住）。
            logger.info(f"[mcp-server] MySQL 目标: {db.describe_target()}")
            registry.publish(name for name, _ in items)

        # 关键：mount() 不会执行子应用的 lifespan，而 MCP session manager 必须
        # 在 lifespan 中启动（否则每个请求都会报 "Task group is not initialized"）。
        # 这里手动驱动每个业务的 session manager。
        #
        # 不用 `async with AsyncExitStack()`：session manager 退出时会等待进行中的
        # 请求（例如一次仍在跑的慢查询），业务代码一慢就把 lifespan 卡住，
        # 表现为 Ctrl+C 后一直停在 "Shutting down"。改成显式 aclose + 超时兜底。
        stack = AsyncExitStack()
        for _, mcp in items:
            await stack.enter_async_context(mcp.session_manager.run())
        try:
            yield
        finally:
            # 阶段② 打点：uvicorn 的 --timeout-graceful-shutdown 覆盖不到这里，
            # 卡在哪一步只能靠日志区分。
            timeout = shutdown_timeout_seconds()
            started = time.perf_counter()
            logger.info("[mcp-server] 开始应用收尾（阶段②）...")
            try:
                await asyncio.wait_for(stack.aclose(), timeout=timeout)
                logger.info(
                    f"[mcp-server] 会话管理器已关闭"
                    f"（耗时 {time.perf_counter() - started:.2f}s）"
                )
            except (asyncio.TimeoutError, TimeoutError):
                # 超时说明有请求没退干净；此时进程本来就要结束，直接放行
                logger.warning(
                    f"[mcp-server] 会话管理器关闭超时（>{timeout}s），已放弃等待"
                )

    app = FastAPI(title="Easy MCP Server", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BearerApiKeyMiddleware, verifier=verifier)

    mounted = _mount_businesses(app, items)

    @app.get("/health", summary="健康检查（免鉴权）")
    async def health():
        return {"status": "ok", "businesses": mounted}

    @app.get("/manifest", summary="对外契约：业务清单与 URL 形态（免鉴权）")
    async def manifest(request: Request):
        """本模块对外承诺的机器可读版本，见 ``contract.py``。

        不依赖共享库：主应用若不想读 ``mcp_businesses`` 表，也可以直接拉这个
        端点来发现业务清单（两种方式等价，任选其一）。
        """
        base = str(request.base_url).rstrip("/")
        return {
            "module": "easy-mcp-server",
            "version": __version__,
            "auth": {
                "scheme": "Bearer",
                "key_prefix": contract.API_KEY_PREFIX,
            },
            "businesses": [
                {"name": name, "url": contract.business_url(base, name)}
                for name in mounted
            ],
        }

    if not mounted:
        logger.warning("[mcp-server] 未发现任何业务包")
    return app
