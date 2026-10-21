"""限流中间件契约测试：真实客户端 IP、429 JSON、可插拔计数、多副本告警。"""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

import easy_agent.utils.client_ip as client_ip_mod
from easy_agent.middleware import rate_limit as rate_limit_mod
from easy_agent.middleware.rate_limit import (
    InMemorySlidingWindowCounter,
    RateLimitMiddleware,
)


class _DummyApp:
    async def __call__(self, scope, receive, send):  # pragma: no cover - 不会被调用
        return None


def _build_client(
    monkeypatch,
    rules,
    counter=None,
    ip="203.0.113.10",
    client=("testclient", 50000),
):
    # pytest 运行期会设置 PYTEST_CURRENT_TEST，中间件默认在该变量存在时跳过限流；
    # 这里显式删除以便测试真实分支。
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(client_ip_mod, "get_client_ip", lambda request: ip)

    app = FastAPI()

    @app.get("/limited")
    async def limited():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, rules=rules, counter=counter)
    return TestClient(app, client=client)


def test_sliding_window_blocks_after_limit():
    limiter = InMemorySlidingWindowCounter()
    assert limiter.allow("k", 2, 60.0)
    assert limiter.allow("k", 2, 60.0)
    assert limiter.allow("k", 2, 60.0) is False


def test_in_memory_counter_is_not_shared():
    assert InMemorySlidingWindowCounter.is_shared is False


def test_rate_limit_returns_json_429(monkeypatch):
    client = _build_client(monkeypatch, [("/limited", 2, 60.0)])
    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200

    response = client.get("/limited")

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": "请求过于频繁，请稍后再试",
        "code": "RATE_LIMITED",
    }


def test_rate_limit_uses_client_ip_not_socket_peer(monkeypatch):
    # 同一 app / 同一计数器，两个不同 socket 对端；get_client_ip 固定返回同一 IP。
    app = FastAPI()

    @app.get("/limited")
    async def limited():
        return {"ok": True}

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(client_ip_mod, "get_client_ip", lambda request: "198.51.100.7")
    app.add_middleware(RateLimitMiddleware, rules=[("/limited", 1, 60.0)])

    first = TestClient(app, client=("10.0.0.1", 1111))
    second = TestClient(app, client=("10.0.0.2", 2222))

    assert first.get("/limited").status_code == 200
    # 若实现裸用 request.client.host，这里会是 200；按真实 IP 计数才是 429。
    assert second.get("/limited").status_code == 429


def test_rate_limit_separates_different_client_ips(monkeypatch):
    current = {"ip": "198.51.100.1"}
    client = _build_client(monkeypatch, [("/limited", 1, 60.0)])
    monkeypatch.setattr(client_ip_mod, "get_client_ip", lambda request: current["ip"])

    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 429

    current["ip"] = "198.51.100.2"
    assert client.get("/limited").status_code == 200


def test_custom_shared_counter_is_used(monkeypatch):
    class _SharedCounter:
        is_shared = True

        def __init__(self):
            self.calls = []

        def allow(self, key, limit, window_seconds):
            self.calls.append((key, limit, window_seconds))
            return True

    counter = _SharedCounter()
    client = _build_client(monkeypatch, [("/limited", 1, 30.0)], counter=counter)

    assert client.get("/limited").status_code == 200
    assert counter.calls == [("/limited:203.0.113.10", 1, 30.0)]


class _LoopRecordingCounter:
    def __init__(self, is_shared, allowed=True):
        self.is_shared = is_shared
        self.allowed = allowed
        self.on_loop: list[bool] = []

    def allow(self, key, limit, window_seconds):
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:  # 线程池里的线程没有运行中的事件循环
            self.on_loop.append(False)
        return self.allowed


def test_shared_counter_runs_off_the_event_loop(monkeypatch):
    """共享计数器要访问外部存储（DB / Redis）：allow 放进线程，不占事件循环。

    进程内计数器只改内存，留在事件循环上。
    """
    shared = _LoopRecordingCounter(is_shared=True)
    assert _build_client(monkeypatch, [("/limited", 1, 30.0)], counter=shared).get("/limited").status_code == 200
    assert shared.on_loop == [False]

    full = _LoopRecordingCounter(is_shared=True, allowed=False)
    response = _build_client(monkeypatch, [("/limited", 1, 30.0)], counter=full).get("/limited")
    assert response.status_code == 429
    assert full.on_loop == [False]

    local = _LoopRecordingCounter(is_shared=False)
    assert _build_client(monkeypatch, [("/limited", 1, 30.0)], counter=local).get("/limited").status_code == 200
    assert local.on_loop == [True]


def test_multiworker_warns_once_for_process_local_counter(monkeypatch, caplog):
    monkeypatch.setattr(rate_limit_mod, "detect_worker_count", lambda: 3)
    monkeypatch.setattr(client_ip_mod, "get_client_ip", lambda request: "203.0.113.11")

    with caplog.at_level(logging.WARNING, logger=rate_limit_mod.__name__):
        # auto 在 multiworker 下会选 DB 计数器；这里显式传内存计数器验证告警文案。
        middleware = RateLimitMiddleware(
            _DummyApp(),
            rules=[("/limited", 1, 60.0)],
            counter=rate_limit_mod.InMemorySlidingWindowCounter(),
        )
        middleware._warn_if_process_local_counter()

    warnings = [r for r in caplog.records if "worker/副本" in r.getMessage()]
    assert len(warnings) == 1
    assert "进程内内存实现" in warnings[0].getMessage()
    assert "Redis/DB" in warnings[0].getMessage()
    assert getattr(middleware, "counter").is_shared is False


def test_multiworker_does_not_warn_for_shared_counter(monkeypatch, caplog):
    class _SharedCounter:
        is_shared = True

        def allow(self, key, limit, window_seconds):
            return True

    monkeypatch.setattr(rate_limit_mod, "detect_worker_count", lambda: 4)

    with caplog.at_level(logging.WARNING, logger=rate_limit_mod.__name__):
        RateLimitMiddleware(
            _DummyApp(), rules=[("/limited", 1, 60.0)], counter=_SharedCounter()
        )

    assert not [r for r in caplog.records if "worker/副本" in r.getMessage()]
