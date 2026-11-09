"""RED 指标注册表单元测试：计数、分位、基数保护与并发安全。"""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from easy_agent.services.metrics import (
    LOOP_LAG_INTERVAL_SECONDS,
    OTHER_ROUTE,
    RedMetrics,
    ensure_loop_lag_monitor,
    monitor_event_loop_lag,
    normalize_path,
    record_hitl_wait_end,
    record_hitl_wait_start,
    record_http_request,
    record_sse_end,
    record_sse_start,
    record_tool_call,
    reset_metrics,
    snapshot_metrics,
)


@pytest.fixture(autouse=True)
def _clean_singleton():
    reset_metrics()
    yield
    reset_metrics()


def test_snapshot_has_required_sections_and_is_json_serializable():
    record_http_request("GET", "/agent/config", 200, 0.01)
    record_sse_start()
    record_sse_end(duration_seconds=0.1)
    record_tool_call("execute", True, 0.2)
    record_hitl_wait_start()
    record_hitl_wait_end(0.3, "approved")

    data = snapshot_metrics()

    assert set(data) >= {
        "generated_at",
        "uptime_seconds",
        "process",
        "http",
        "sse",
        "tools",
        "hitl",
    }
    assert set(data["http"]) >= {
        "total",
        "errors",
        "client_errors",
        "error_rate",
        "qps",
        "latency_ms",
        "status_codes",
        "routes",
    }
    assert set(data["sse"]) >= {"active", "total", "failed", "failure_rate", "duration_ms"}
    assert set(data["tools"]) >= {"total", "failures", "failure_rate", "by_tool"}
    assert set(data["hitl"]) >= {"waiting", "total", "outcomes", "wait_ms"}
    assert json.loads(json.dumps(data)) == data


def test_http_counts_errors_and_percentiles():
    metrics = RedMetrics()
    metrics.observe_http("get", "/agent/sessions/123", 200, 0.010)
    metrics.observe_http("GET", "/agent/sessions/123", 500, 0.020)
    metrics.observe_http("GET", "/agent/sessions/123", 404, 0.030)

    http = metrics.snapshot()["http"]

    assert http["total"] == 3
    assert http["errors"] == 1
    assert http["client_errors"] == 1
    assert http["error_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert http["status_codes"] == {"200": 1, "404": 1, "500": 1}
    route = http["routes"][0]
    assert route["method"] == "GET"
    assert route["route"] == "/agent/sessions/{id}"
    assert route["count"] == 3
    assert route["errors"] == 1
    assert route["client_errors"] == 1
    assert route["latency_ms"]["mean"] == pytest.approx(20.0, abs=1e-3)
    assert route["latency_ms"]["max"] == pytest.approx(30.0, abs=1e-3)


def test_latency_percentiles_on_known_distribution():
    metrics = RedMetrics()
    for value in range(1, 101):
        metrics.observe_http("GET", "/agent/chat/stream", 200, value / 1000.0)

    latency = metrics.snapshot()["http"]["latency_ms"]

    assert latency["count"] == 100
    assert latency["mean"] == pytest.approx(50.5, abs=1e-3)
    assert latency["p50"] == pytest.approx(50.5, abs=0.1)
    assert latency["p95"] == pytest.approx(95.05, abs=0.1)
    assert latency["p99"] == pytest.approx(99.01, abs=0.1)
    assert latency["max"] == pytest.approx(100.0, abs=1e-3)


def test_normalize_path_replaces_ids_and_truncates():
    uid = "123e4567-e89b-12d3-a456-426614174000"
    assert (
        normalize_path(f"/agent/sessions/{uid}/messages/42?token=secret")
        == "/agent/sessions/{id}/messages/{id}"
    )
    assert normalize_path("") == "/"
    assert len(normalize_path("/x" + "a" * 500)) == 200


def test_route_series_is_capped_with_overflow_bucket():
    metrics = RedMetrics(max_route_series=2)
    for index in range(4):
        metrics.observe_http("GET", f"/agent/route-{index}", 200, 0.001)

    routes = metrics.snapshot()["http"]["routes"]
    by_route = {item["route"]: item["count"] for item in routes}

    assert len(routes) == 3  # 2 条真实路由 + 1 个溢出桶
    assert by_route[OTHER_ROUTE] == 2
    assert metrics.snapshot()["http"]["total"] == 4


def test_http_qps_is_exposed():
    metrics = RedMetrics()
    for _ in range(5):
        metrics.observe_http("GET", "/agent/config", 200, 0.001)

    qps = metrics.snapshot()["http"]["qps"]

    assert qps["window_seconds"] > 0
    assert qps["current"] > 0
    assert qps["average"] > 0


def test_sse_active_gauge_duration_and_failure_rate():
    metrics = RedMetrics()
    metrics.record_sse_start()
    metrics.record_sse_start()
    metrics.record_sse_end(duration_seconds=0.5, failed=True)

    sse = metrics.snapshot()["sse"]

    assert sse["active"] == 1
    assert sse["total"] == 2
    assert sse["failed"] == 1
    assert sse["failure_rate"] == pytest.approx(0.5)
    assert sse["duration_ms"]["count"] == 1
    assert sse["duration_ms"]["mean"] == pytest.approx(500.0, abs=1e-3)


def test_sse_active_route_is_visible_before_stream_ends():
    metrics = RedMetrics()
    metrics.record_sse_start("/agent/chat/stream")

    sse = metrics.snapshot()["sse"]

    assert sse["active"] == 1
    assert sse["total"] == 1
    assert len(sse["routes"]) == 1
    route = sse["routes"][0]
    assert route["route"] == "/agent/chat/stream"
    assert route["active"] == 1
    assert route["duration_ms"]["count"] == 0


def test_sse_end_without_start_never_goes_negative():
    metrics = RedMetrics()
    metrics.record_sse_end(duration_seconds=0.01)
    metrics.record_sse_end(duration_seconds=0.02)

    sse = metrics.snapshot()["sse"]

    assert sse["active"] == 0
    assert sse["total"] == 0
    assert sse["duration_ms"]["count"] == 2


def test_tool_failure_rate_and_duration():
    metrics = RedMetrics()
    metrics.record_tool_call("execute", True, 0.2)
    metrics.record_tool_call("execute", False)
    metrics.record_tool_call("web_search", True, 0.1)

    tools = metrics.snapshot()["tools"]
    by_tool = {item["tool"]: item for item in tools["by_tool"]}

    assert tools["total"] == 3
    assert tools["failures"] == 1
    assert tools["failure_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert by_tool["execute"]["total"] == 2
    assert by_tool["execute"]["failures"] == 1
    assert by_tool["execute"]["duration_ms"]["count"] == 1
    assert by_tool["web_search"]["failure_rate"] == 0.0


def test_hitl_waiting_gauge_outcomes_and_underflow_guard():
    metrics = RedMetrics()
    metrics.record_hitl_wait_start()
    metrics.record_hitl_wait_start()
    metrics.record_hitl_wait_end(1.5, "approved")
    metrics.record_hitl_wait_end(0.5, "rejected")
    metrics.record_hitl_wait_end(0.1, "approved")  # 多减一次，gauge 不能为负

    hitl = metrics.snapshot()["hitl"]

    assert hitl["waiting"] == 0
    assert hitl["total"] == 3
    assert hitl["outcomes"] == {"approved": 2, "rejected": 1}
    assert hitl["wait_ms"]["count"] == 3
    assert hitl["wait_ms"]["max"] == pytest.approx(1500.0, abs=1e-3)


def test_concurrent_http_counters_are_thread_safe():
    workers = 8
    per_worker = 250

    def _write() -> None:
        for _ in range(per_worker):
            record_http_request("GET", "/agent/chat/stream", 200, 0.001)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda _: _write(), range(workers)))

    http = snapshot_metrics()["http"]

    assert http["total"] == workers * per_worker
    assert http["routes"][0]["count"] == workers * per_worker
    assert http["latency_ms"]["count"] == workers * per_worker


def test_concurrent_mixed_metrics_are_thread_safe():
    workers = 4
    per_worker = 100

    def _write(index: int) -> None:
        for _ in range(per_worker):
            record_tool_call("execute", index % 2 == 0, 0.001)
            record_hitl_wait_start()
            record_hitl_wait_end(0.001, "approved")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_write, range(workers)))

    data = snapshot_metrics()
    expected = workers * per_worker

    assert data["tools"]["total"] == expected
    assert data["hitl"]["total"] == expected
    assert data["hitl"]["waiting"] == 0


# ── 事件循环延迟 ──────────────────────────────────────────────────────


def test_loop_lag_is_part_of_the_snapshot():
    m = RedMetrics()
    m.record_loop_lag(0.05)
    m.record_loop_lag(0.01)

    data = m.snapshot()["event_loop"]
    assert data["interval_ms"] == LOOP_LAG_INTERVAL_SECONDS * 1000
    assert data["last_ms"] == 10.0
    assert data["lag_ms"]["count"] == 2 and data["lag_ms"]["max"] == 50.0

    m.reset()
    assert m.snapshot()["event_loop"]["lag_ms"]["count"] == 0


async def test_monitor_records_how_long_the_loop_was_blocked():
    samples: list[float] = []
    monitor = asyncio.create_task(monitor_event_loop_lag(0.02, samples.append))
    await asyncio.sleep(0.05)
    time.sleep(0.2)  # 同步阻塞事件循环
    await asyncio.sleep(0.05)
    monitor.cancel()

    assert max(samples) >= 0.15


async def test_monitor_is_started_once_per_loop():
    import easy_agent.services.metrics as metrics_mod

    loop = asyncio.get_running_loop()
    ensure_loop_lag_monitor()
    first = metrics_mod._lag_monitors[id(loop)]
    ensure_loop_lag_monitor()

    assert metrics_mod._lag_monitors[id(loop)] is first
    first.cancel()


def test_monitor_needs_a_running_loop():
    import easy_agent.services.metrics as metrics_mod

    before = dict(metrics_mod._lag_monitors)
    ensure_loop_lag_monitor()
    assert metrics_mod._lag_monitors == before


async def test_metrics_middleware_starts_the_monitor_at_lifespan():
    """监测任务由 HTTP 指标中间件在应用启动（lifespan）时拉起，无需改动应用装配代码。"""
    import easy_agent.services.metrics as metrics_mod
    from easy_agent.middleware.metrics import MetricsMiddleware

    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    loop = asyncio.get_running_loop()
    metrics_mod._lag_monitors.pop(id(loop), None)
    await MetricsMiddleware(app)({"type": "lifespan"}, None, None)

    task = metrics_mod._lag_monitors.get(id(loop))
    assert task is not None and not task.done()
    assert seen == ["lifespan"]
    task.cancel()
