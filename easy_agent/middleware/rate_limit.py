"""HTTP 限流中间件（可插拔计数 + 单机内存实现）。

计数接口
--------
``RateLimitCounter`` 定义 ``allow(key, limit, window_seconds) -> bool``，并提供
``is_shared`` 标记是否跨进程共享。默认 ``InMemorySlidingWindowCounter`` 是
**进程内** 滑动窗口：

- 单机 / 单 worker：阈值精确；
- 多 worker / 多副本：每个进程独立计数，实际放行量约为配置阈值的 N 倍；
  真正的全局限流需要共享实现（Redis / DB），见
  ``docs/archive/design/2026-10-18-b3-rate-limit-jwt.md``。

检测到多 worker/副本且计数器非共享时，初始化会打印明确 warning（不阻断）。
限流键使用 ``utils.client_ip.get_client_ip(request)``（仅当 ``EASY_TRUST_PROXY=1``
才信任 ``X-Forwarded-For`` / ``X-Real-IP``），不再裸用 ``request.client.host``。

注意：``BaseHTTPMiddleware`` 内直接 ``raise HTTPException`` 会被外层异常处理
转换成 500，因此超限必须返回 ``JSONResponse(status_code=429)``。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Protocol, runtime_checkable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from easy_agent.bootstrap.env import env_str
from easy_agent.utils.deployment import detect_worker_count
from easy_agent.utils.blocking import run_blocking
from easy_agent.db.providers import get_rate_limit_repository

logger = logging.getLogger(__name__)

RATE_LIMIT_DETAIL = "请求过于频繁，请稍后再试"
RATE_LIMIT_CODE = "RATE_LIMITED"

#: 只读的流式端点：与「发起流」共用同一路径前缀，但**必须**单独放行。
#:
#: 限流规则按前缀匹配（``path == prefix or path.startswith(prefix + "/")``），于是
#: ``/chat/stream/status`` 与 ``/chat/stream/live`` 也被算进了 ``/chat/stream`` 的
#: 30 次/分 配额。刷新续接每 1.5 秒探一次状态，45 秒就能把额度耗光；此后
#: ``/stream/live`` 挂载被 429 拒绝，表现正是「刷新后再也接不回来、只能等回答落库」。
#: 这两个端点是幂等只读（不触发模型、不写库），放行不会带来刷额度风险。
_READ_ONLY_STREAM_PATHS = (
    "/chat/stream/status",
    "/chat/stream/live",
)

_DEFAULT_RULES: list[tuple[str, int, float]] = [
    ("/agent/auth/login", 20, 60.0),
    ("/api/auth/login", 20, 60.0),
    ("/agent/auth/login-passwordless", 20, 60.0),
    ("/api/auth/login-passwordless", 20, 60.0),
    ("/agent/chat/stream", 30, 60.0),
    ("/api/chat/stream", 30, 60.0),
    ("/agent/chat/resume", 30, 60.0),
    ("/api/chat/resume", 30, 60.0),
    # SEC-09：公共对话补全直接透传大模型（可指定 system/模型），按 IP 限流避免被刷额度
    ("/agent/completion/chat", 30, 60.0),
    ("/api/completion/chat", 30, 60.0),
]


@runtime_checkable
class RateLimitCounter(Protocol):
    """限流计数器接口。

    自定义实现（如 Redis / DB）只需提供 ``allow``，并把 ``is_shared`` 设为
    ``True``，中间件就不会再打印「进程内计数」的多副本告警。``is_shared`` 为 True
    时中间件在线程里调用 ``allow``（共享计数要访问外部存储，会阻塞），实现需要线程安全。
    """

    is_shared: bool

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        """在 ``window_seconds`` 内第 ``limit + 1`` 次调用返回 False。"""
        ...


class InMemorySlidingWindowCounter:
    """单进程滑动窗口计数器（默认实现，非共享）。"""

    is_shared = False

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        # DBRateLimitCounter 降级时会在线程里调用：检查与记录必须一起完成
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        with self._lock:
            now = time.monotonic()
            bucket = self._hits[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


class DBRateLimitCounter:
    """多副本共享限流计数（业务库表 ``rate_limit_counters``，固定窗口）。

    - SQLite / MySQL 共用 UPDATE→INSERT 的原子条件写：先尝试
      ``UPDATE ... SET count=count+1 WHERE count < limit``，rowcount=1 放行；
      否则尝试 INSERT 当前窗口；INSERT 冲突说明已被并发写满/刚创建，重试一次
      UPDATE 处理「对方 INSERT 成功但尚未计数」的竞态。
    - 计数器不可用时降级为进程内实现并告警一次（不阻断业务请求）。
    """

    is_shared = True

    def __init__(self, db_provider=None) -> None:
        self._db_provider = db_provider
        self._fallback = InMemorySlidingWindowCounter()
        self._db_failed = False

    def _repo(self):
        if self._db_provider is not None:
            return self._db_provider()

        return get_rate_limit_repository()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.time()
        try:
            port = self._repo()
            try_allow = getattr(port, "try_allow", None)
            if callable(try_allow):
                return try_allow(key, limit, window_seconds, now)
            # 迁移期兼容：直接传进来的 Database / 旧测试替身走模块级 SQL 入口
            from easy_agent.db.repositories.rate_limits import try_allow  # 延迟导入：局部变量遮蔽：函数内重新导入以取得模块级 SQL 入口，避开同名局部变量
            return try_allow(port, key, limit, window_seconds, now)
        except Exception as exc:  # 计数器故障不阻断业务
            if not self._db_failed:
                self._db_failed = True
                logger.warning(
                    "共享限流计数器不可用，降级为进程内计数（多副本阈值会放大）: %s", exc
                )
            return self._fallback.allow(key, limit, window_seconds)


def build_rate_limit_counter():
    """按 ``EASY_RATE_LIMIT_BACKEND`` 选择计数器。

    - ``db``：强制共享 DB 计数；
    - ``memory``：强制进程内；
    - ``auto``（默认）：多 worker/副本时用 DB，否则用内存。
    """
    backend = env_str("EASY_RATE_LIMIT_BACKEND", "auto").strip().lower()
    if backend == "db":
        return DBRateLimitCounter()
    if backend == "memory":
        return InMemorySlidingWindowCounter()
    try:
        workers = detect_worker_count()
    except Exception:
        workers = 1
    if workers > 1:
        return DBRateLimitCounter()
    return InMemorySlidingWindowCounter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        rules: list[tuple[str, int, float]] | None = None,
        counter: RateLimitCounter | None = None,
    ):
        super().__init__(app)
        self.rules = rules or _DEFAULT_RULES
        self.counter: RateLimitCounter = (
            counter if counter is not None else build_rate_limit_counter()
        )
        self._warned_multiworker = False
        self._warn_if_process_local_counter()

    def _warn_if_process_local_counter(self) -> None:
        """多副本 + 进程内计数时告警一次；不阻断启动。"""
        if self._warned_multiworker:
            return
        self._warned_multiworker = True
        try:
            workers = detect_worker_count()
        except Exception:  # 防御：环境变量异常不应影响限流本身
            workers = 1
        if workers > 1 and not getattr(self.counter, "is_shared", False):
            logger.warning(
                "检测到 %s 个 worker/副本，但限流计数为进程内内存实现（非共享）："
                "各实例独立计数，实际放行量约为配置阈值的 %s 倍；"
                "多副本部署请提供共享计数器（Redis/DB），或在 Ingress/API Gateway "
                "统一限流。参考 docs/archive/design/2026-10-18-b3-rate-limit-jwt.md",
                workers,
                workers,
            )

    async def dispatch(self, request: Request, call_next):
        if env_str("PYTEST_CURRENT_TEST"):
            return await call_next(request)
        path = request.url.path
        # 只读的流探测 / 回放不吃「发起流」的配额（见 _READ_ONLY_STREAM_PATHS 注释）。
        if request.method in ("GET", "HEAD") and path.endswith(_READ_ONLY_STREAM_PATHS):
            return await call_next(request)
        # 延迟 import：utils 包会导入 auth（bcrypt/jose），放在模块顶层会拖慢
        # app 启动早期 import，也更容易形成 middleware <-> utils 的环。
        from easy_agent.utils.client_ip import get_client_ip  # 延迟导入：测试 monkeypatch 语义：按模块属性替换，函数内导入才能取到替换后的对象

        client = get_client_ip(request)
        for prefix, limit, window in self.rules:
            if path == prefix or path.startswith(prefix + "/"):
                key = f"{prefix}:{client}"
                if not await self._allow(key, limit, window):
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": RATE_LIMIT_DETAIL,
                            "code": RATE_LIMIT_CODE,
                        },
                    )
                break
        return await call_next(request)

    async def _allow(self, key: str, limit: int, window: float) -> bool:
        """共享计数器要访问外部存储（DB / Redis），放进线程执行，不占事件循环。

        进程内计数器只改内存，留在事件循环上。
        """
        if not getattr(self.counter, "is_shared", False):
            return self.counter.allow(key, limit, window)
        # 延迟 import，原因同 dispatch 里的 get_client_ip

        return await run_blocking(self.counter.allow, key, limit, window)
