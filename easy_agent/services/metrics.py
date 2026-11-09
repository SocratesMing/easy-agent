"""进程内 RED 运行指标注册表（仅 stdlib，无 Prometheus 依赖）。

E3 设计目标：在**不引入 Prometheus 客户端**、不改业务链路的前提下，为
HTTP / SSE / 工具调用 / HITL 提供进程内计数与延迟分位，供 ``/agent/metrics``
以 JSON 暴露。事件循环延迟（``event_loop``）衡量循环被同步代码占住的时长：
定时器本该每 ``LOOP_LAG_INTERVAL_SECONDS`` 醒一次，实际多等的时间就是延迟。

约定：

- **线程安全**：所有状态由一把 ``threading.RLock`` 保护；事件循环线程、线程池
  与测试线程可以并发写入。
- **有界内存**：每个时间序列只保留最近 ``MAX_SAMPLES`` 个延迟样本用于分位，
  ``count`` / ``sum`` / ``max`` 仍然是精确值；HTTP 路由序列上限
  ``MAX_ROUTE_SERIES``，溢出统一落到 ``"(other)"``。
- **进程内**：多 worker / 多副本时各进程独立计数，跨实例聚合由外部完成。
- **HTTP 错误定义**：``errors`` 仅统计 5xx；4xx 单列 ``client_errors``，
  ``error_rate = errors / total``。
- **延迟单位**：内部使用秒，快照统一输出毫秒。
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

#: 单个时间序列保留的延迟样本数（分位精度与内存的折中）。
MAX_SAMPLES = 2048
#: 进程内最多保留的路由序列数；超出后归入 ``OTHER_ROUTE``。
MAX_ROUTE_SERIES = 256
#: QPS 滑动窗口（秒）。
QPS_WINDOW_SECONDS = 60.0
#: 路由基数溢出桶。
OTHER_ROUTE = "(other)"
#: SSE 默认路由标签。
DEFAULT_SSE_ROUTE = "/agent/chat/stream"
#: 事件循环延迟的采样间隔（秒）。
LOOP_LAG_INTERVAL_SECONDS = 0.25

_UUID_RE = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NUMBER_RE = re.compile(r"/\d+(?=/|$)")
_MAX_PATH_LENGTH = 200
_MAX_LABEL_LENGTH = 80
_MAX_STATUS = 599


def _percentile(sorted_values: list[float], quantile: float) -> float:
    """线性插值分位；空样本返回 0。"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _empty_summary() -> dict[str, int | float]:
    """无样本时的零值 summary（每次返回新 dict，避免快照被调用方改动）。"""
    return {
        "count": 0,
        "samples": 0,
        "mean": 0.0,
        "p50": 0.0,
        "p95": 0.0,
        "p99": 0.0,
        "max": 0.0,
    }


def normalize_path(path: str) -> str:
    """归一化 HTTP 路径，避免 UUID / 数字 ID 造成标签基数膨胀。"""
    value = str(path or "/").split("?", 1)[0]
    value = _UUID_RE.sub("/{id}", value)
    value = _NUMBER_RE.sub("/{id}", value)
    if len(value) > _MAX_PATH_LENGTH:
        value = value[:_MAX_PATH_LENGTH]
    return value or "/"


def _normalize_label(value: str, *, fallback: str, limit: int = _MAX_LABEL_LENGTH) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:limit]


class _TimedSeries:
    """单序列的延迟聚合：精确 count/sum/max + 有界样本分位。"""

    __slots__ = ("count", "total_seconds", "max_seconds", "_samples")

    def __init__(self, max_samples: int = MAX_SAMPLES) -> None:
        self.count = 0
        self.total_seconds = 0.0
        self.max_seconds = 0.0
        self._samples: deque[float] = deque(maxlen=max_samples)

    def observe(self, seconds: float) -> None:
        value = max(0.0, float(seconds))
        self.count += 1
        self.total_seconds += value
        if value > self.max_seconds:
            self.max_seconds = value
        self._samples.append(value)

    def summary_ms(self) -> dict[str, int | float]:
        """输出毫秒统计：count/samples/mean/p50/p95/p99/max。"""
        samples = sorted(self._samples)
        mean = self.total_seconds / self.count if self.count else 0.0
        return {
            "count": self.count,
            "samples": len(samples),
            "mean": round(mean * 1000.0, 3),
            "p50": round(_percentile(samples, 0.50) * 1000.0, 3),
            "p95": round(_percentile(samples, 0.95) * 1000.0, 3),
            "p99": round(_percentile(samples, 0.99) * 1000.0, 3),
            "max": round(self.max_seconds * 1000.0, 3),
        }


class RedMetrics:
    """RED 指标容器；生产使用模块级单例 :data:`metrics`，测试可独立实例化。"""

    def __init__(
        self,
        *,
        max_samples: int = MAX_SAMPLES,
        max_route_series: int = MAX_ROUTE_SERIES,
        qps_window_seconds: float = QPS_WINDOW_SECONDS,
    ) -> None:
        self._max_samples = max(1, int(max_samples))
        self._max_route_series = max(1, int(max_route_series))
        self._qps_window_seconds = max(1.0, float(qps_window_seconds))
        self._lock = threading.RLock()

        self._http_counts: dict[tuple[str, str, int], int] = {}
        self._http_series: dict[tuple[str, str], _TimedSeries] = {}
        self._http_overall = _TimedSeries(self._max_samples)

        self._sse_active: dict[str, int] = {}
        self._sse_total: dict[str, int] = {}
        self._sse_failed: dict[str, int] = {}
        self._sse_series: dict[str, _TimedSeries] = {}
        self._sse_overall = _TimedSeries(self._max_samples)

        self._tool_total: dict[str, int] = {}
        self._tool_failures: dict[str, int] = {}
        self._tool_series: dict[str, _TimedSeries] = {}
        self._tool_overall = _TimedSeries(self._max_samples)

        self._hitl_waiting = 0
        self._hitl_total = 0
        self._hitl_outcomes: dict[str, int] = {}
        self._hitl_series = _TimedSeries(self._max_samples)

        self._loop_lag = _TimedSeries(self._max_samples)
        self._loop_lag_last = 0.0

        now = time.monotonic()
        self._started_monotonic = now
        self._started_at = datetime.now(timezone.utc)
        self._http_qps_buckets: dict[int, int] = {}

    # -- reset ---------------------------------------------------------------

    def reset(self) -> None:
        """清空全部指标并重置起始时间（测试隔离用）。"""
        with self._lock:
            self._http_counts.clear()
            self._http_series.clear()
            self._http_overall = _TimedSeries(self._max_samples)
            self._http_qps_buckets.clear()

            self._sse_active.clear()
            self._sse_total.clear()
            self._sse_failed.clear()
            self._sse_series.clear()
            self._sse_overall = _TimedSeries(self._max_samples)

            self._tool_total.clear()
            self._tool_failures.clear()
            self._tool_series.clear()
            self._tool_overall = _TimedSeries(self._max_samples)

            self._hitl_waiting = 0
            self._hitl_total = 0
            self._hitl_outcomes.clear()
            self._hitl_series = _TimedSeries(self._max_samples)

            self._loop_lag = _TimedSeries(self._max_samples)
            self._loop_lag_last = 0.0

            self._started_monotonic = time.monotonic()
            self._started_at = datetime.now(timezone.utc)

    # -- HTTP ----------------------------------------------------------------

    def _series_for(
        self,
        series: dict[Any, _TimedSeries],
        key: Any,
        *,
        collapsed_key: Any,
    ) -> tuple[Any, _TimedSeries]:
        """返回（实际 key, 序列），必要时把新路由折叠到溢出桶。"""
        if key not in series and len(series) >= self._max_route_series:
            key = collapsed_key
        instance = series.get(key)
        if instance is None:
            instance = _TimedSeries(self._max_samples)
            series[key] = instance
        return key, instance

    def _prune_qps(self, now: float) -> None:
        cutoff = int(now - self._qps_window_seconds)
        expired = [bucket for bucket in self._http_qps_buckets if bucket <= cutoff]
        for bucket in expired:
            del self._http_qps_buckets[bucket]

    def observe_http(
        self,
        method: str,
        path: str,
        status: int,
        duration_seconds: float,
    ) -> None:
        """记录一次 HTTP 请求（method/path/status/duration）。"""
        method_key = str(method or "UNKNOWN").upper()[:16]
        route = normalize_path(path)
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            status_code = 500
        status_code = min(max(status_code, 100), _MAX_STATUS)
        seconds = max(0.0, float(duration_seconds))

        with self._lock:
            route_key, series = self._series_for(
                self._http_series,
                (method_key, route),
                collapsed_key=(method_key, OTHER_ROUTE),
            )
            series.observe(seconds)
            self._http_overall.observe(seconds)

            count_key = (method_key, route_key[1], status_code)
            self._http_counts[count_key] = self._http_counts.get(count_key, 0) + 1

            now = time.monotonic()
            bucket = int(now)
            self._http_qps_buckets[bucket] = self._http_qps_buckets.get(bucket, 0) + 1
            self._prune_qps(now)

    def _http_snapshot(self, uptime: float) -> dict[str, Any]:
        total = sum(self._http_counts.values())
        errors = sum(
            count for (_, _, status), count in self._http_counts.items() if status >= 500
        )
        client_errors = sum(
            count
            for (_, _, status), count in self._http_counts.items()
            if 400 <= status < 500
        )

        route_totals: dict[tuple[str, str], int] = {}
        route_errors: dict[tuple[str, str], int] = {}
        route_client_errors: dict[tuple[str, str], int] = {}
        for (method_key, route, status), count in self._http_counts.items():
            key = (method_key, route)
            route_totals[key] = route_totals.get(key, 0) + count
            if status >= 500:
                route_errors[key] = route_errors.get(key, 0) + count
            elif 400 <= status < 500:
                route_client_errors[key] = route_client_errors.get(key, 0) + count

        routes: list[dict[str, Any]] = []
        for (method_key, route), series in sorted(self._http_series.items()):
            key = (method_key, route)
            route_count = route_totals.get(key, 0)
            route_error_count = route_errors.get(key, 0)
            routes.append(
                {
                    "method": method_key,
                    "route": route,
                    "count": route_count,
                    "errors": route_error_count,
                    "client_errors": route_client_errors.get(key, 0),
                    "error_rate": round(route_error_count / route_count, 6)
                    if route_count
                    else 0.0,
                    "latency_ms": series.summary_ms(),
                }
            )

        # 分母至少 1s：进程刚启动时避免把个位数请求放大成极端 QPS。
        if uptime > 0:
            window = max(1.0, min(self._qps_window_seconds, uptime))
        else:
            window = self._qps_window_seconds
        window_requests = sum(self._http_qps_buckets.values())
        current_qps = window_requests / window
        average_qps = total / uptime if uptime > 0 else 0.0

        status_codes: dict[str, int] = {}
        for (_, _, status), count in self._http_counts.items():
            status_key = str(status)
            status_codes[status_key] = status_codes.get(status_key, 0) + count
        status_codes = {
            status: status_codes[status]
            for status in sorted(status_codes, key=int)
        }

        return {
            "total": total,
            "errors": errors,
            "client_errors": client_errors,
            "error_rate": round(errors / total, 6) if total else 0.0,
            "qps": {
                "window_seconds": round(window, 3),
                "current": round(current_qps, 3),
                "average": round(average_qps, 3),
            },
            "latency_ms": self._http_overall.summary_ms(),
            "status_codes": status_codes,
            "routes": routes,
        }

    # -- SSE -----------------------------------------------------------------

    def record_sse_start(self, route: str = DEFAULT_SSE_ROUTE) -> None:
        """SSE 流开始：活跃 gauge +1，累计启动数 +1。"""
        route_key = _normalize_label(route, fallback=DEFAULT_SSE_ROUTE)
        with self._lock:
            self._sse_active[route_key] = self._sse_active.get(route_key, 0) + 1
            self._sse_total[route_key] = self._sse_total.get(route_key, 0) + 1

    def record_sse_end(
        self,
        route: str = DEFAULT_SSE_ROUTE,
        duration_seconds: float = 0.0,
        *,
        failed: bool = False,
    ) -> None:
        """SSE 流结束：活跃 gauge -1（不落负）、记录时长与失败计数。"""
        route_key = _normalize_label(route, fallback=DEFAULT_SSE_ROUTE)
        seconds = max(0.0, float(duration_seconds))
        with self._lock:
            active = self._sse_active.get(route_key, 0)
            self._sse_active[route_key] = max(0, active - 1)
            self._sse_total.setdefault(route_key, 0)
            series = self._sse_series.get(route_key)
            if series is None:
                series = _TimedSeries(self._max_samples)
                self._sse_series[route_key] = series
            series.observe(seconds)
            self._sse_overall.observe(seconds)
            if failed:
                self._sse_failed[route_key] = self._sse_failed.get(route_key, 0) + 1

    def _sse_snapshot(self) -> dict[str, Any]:
        total = sum(self._sse_total.values())
        failed = sum(self._sse_failed.values())
        routes = []
        route_keys = sorted(
            set(self._sse_active) | set(self._sse_total) | set(self._sse_series)
        )
        for route in route_keys:
            route_total = self._sse_total.get(route, 0)
            route_failed = self._sse_failed.get(route, 0)
            series = self._sse_series.get(route)
            routes.append(
                {
                    "route": route,
                    "active": self._sse_active.get(route, 0),
                    "total": route_total,
                    "failed": route_failed,
                    "failure_rate": round(route_failed / route_total, 6)
                    if route_total
                    else 0.0,
                    "duration_ms": series.summary_ms() if series else _empty_summary(),
                }
            )
        return {
            "active": sum(self._sse_active.values()),
            "total": total,
            "failed": failed,
            "failure_rate": round(failed / total, 6) if total else 0.0,
            "duration_ms": self._sse_overall.summary_ms(),
            "routes": routes,
        }

    # -- tools ---------------------------------------------------------------

    def record_tool_call(
        self,
        tool_name: str,
        success: bool,
        duration_seconds: float | None = None,
    ) -> None:
        """记录一次工具调用；``duration_seconds=None`` 时只计数不记时长。"""
        name = _normalize_label(tool_name, fallback="unknown")
        with self._lock:
            self._tool_total[name] = self._tool_total.get(name, 0) + 1
            if not success:
                self._tool_failures[name] = self._tool_failures.get(name, 0) + 1
            if duration_seconds is not None:
                seconds = max(0.0, float(duration_seconds))
                series = self._tool_series.get(name)
                if series is None:
                    series = _TimedSeries(self._max_samples)
                    self._tool_series[name] = series
                series.observe(seconds)
                self._tool_overall.observe(seconds)

    def _tool_snapshot(self) -> dict[str, Any]:
        total = sum(self._tool_total.values())
        failures = sum(self._tool_failures.values())
        by_tool = []
        for name, count in sorted(self._tool_total.items()):
            failure_count = self._tool_failures.get(name, 0)
            series = self._tool_series.get(name)
            by_tool.append(
                {
                    "tool": name,
                    "total": count,
                    "failures": failure_count,
                    "failure_rate": round(failure_count / count, 6) if count else 0.0,
                    "duration_ms": series.summary_ms()
                    if series is not None
                    else _empty_summary(),
                }
            )
        return {
            "total": total,
            "failures": failures,
            "failure_rate": round(failures / total, 6) if total else 0.0,
            "duration_ms": self._tool_overall.summary_ms(),
            "by_tool": by_tool,
        }

    # -- HITL ----------------------------------------------------------------

    def record_hitl_wait_start(self) -> None:
        """进入 HITL 等待：等待 gauge +1。"""
        with self._lock:
            self._hitl_waiting += 1

    def record_hitl_wait_end(
        self,
        duration_seconds: float,
        outcome: str = "approved",
    ) -> None:
        """HITL 等待结束：gauge -1（不落负）、累计耗时与审批结果。"""
        outcome_key = _normalize_label(outcome, fallback="unknown", limit=32)
        seconds = max(0.0, float(duration_seconds))
        with self._lock:
            self._hitl_waiting = max(0, self._hitl_waiting - 1)
            self._hitl_total += 1
            self._hitl_outcomes[outcome_key] = self._hitl_outcomes.get(outcome_key, 0) + 1
            self._hitl_series.observe(seconds)

    def _hitl_snapshot(self) -> dict[str, Any]:
        return {
            "waiting": self._hitl_waiting,
            "total": self._hitl_total,
            "outcomes": dict(sorted(self._hitl_outcomes.items())),
            "wait_ms": self._hitl_series.summary_ms(),
        }

    # -- event loop ----------------------------------------------------------

    def record_loop_lag(self, seconds: float) -> None:
        """记录一次事件循环延迟采样（定时器实际醒来比预期晚的秒数）。"""
        value = max(0.0, float(seconds))
        with self._lock:
            self._loop_lag.observe(value)
            self._loop_lag_last = value

    def _loop_snapshot(self) -> dict[str, Any]:
        return {
            "interval_ms": round(LOOP_LAG_INTERVAL_SECONDS * 1000.0, 3),
            "last_ms": round(self._loop_lag_last * 1000.0, 3),
            "lag_ms": self._loop_lag.summary_ms(),
        }

    # -- snapshot ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """返回可直接 JSON 序列化的指标快照。"""
        with self._lock:
            now = time.monotonic()
            self._prune_qps(now)
            uptime = max(0.0, now - self._started_monotonic)
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": round(uptime, 3),
                "process": {
                    "pid": os.getpid(),
                    "started_at": self._started_at.isoformat(),
                },
                "http": self._http_snapshot(uptime),
                "sse": self._sse_snapshot(),
                "tools": self._tool_snapshot(),
                "hitl": self._hitl_snapshot(),
                "event_loop": self._loop_snapshot(),
            }


#: 生产进程内单例；``/agent/metrics`` 与中间件共用同一实例。
metrics = RedMetrics()


def record_http_request(
    method: str,
    path: str,
    status: int,
    duration_seconds: float,
) -> None:
    """记录一次 HTTP 请求（供 ASGI 中间件调用）。"""
    metrics.observe_http(method, path, status, duration_seconds)


def record_sse_start(route: str = DEFAULT_SSE_ROUTE) -> None:
    """记录一次 SSE 流开始（后续由 streaming 层接入）。"""
    metrics.record_sse_start(route)


def record_sse_end(
    route: str = DEFAULT_SSE_ROUTE,
    duration_seconds: float = 0.0,
    *,
    failed: bool = False,
) -> None:
    """记录一次 SSE 流结束（后续由 streaming 层接入）。"""
    metrics.record_sse_end(route, duration_seconds, failed=failed)


def record_tool_call(
    tool_name: str,
    success: bool,
    duration_seconds: float | None = None,
) -> None:
    """记录一次工具调用（后续由工具执行层接入）。"""
    metrics.record_tool_call(tool_name, success, duration_seconds)


def record_hitl_wait_start() -> None:
    """记录进入 HITL 等待（后续由 interrupt 处理路径接入）。"""
    metrics.record_hitl_wait_start()


def record_hitl_wait_end(duration_seconds: float, outcome: str = "approved") -> None:
    """记录 HITL 等待结束（后续由 resume 处理路径接入）。"""
    metrics.record_hitl_wait_end(duration_seconds, outcome)


def record_loop_lag(seconds: float) -> None:
    metrics.record_loop_lag(seconds)


async def monitor_event_loop_lag(
    interval: float = LOOP_LAG_INTERVAL_SECONDS,
    recorder: Any = None,
) -> None:
    """一直运行：每隔 ``interval`` 秒醒一次，把多等的时间记成事件循环延迟。"""
    loop = asyncio.get_running_loop()
    record = recorder or record_loop_lag
    while True:
        started = loop.time()
        await asyncio.sleep(interval)
        record(max(0.0, loop.time() - started - interval))


#: 每个事件循环一个监测任务（键是 ``id(loop)``：uvloop 的循环不支持弱引用）。
_lag_monitors: dict[int, asyncio.Task[None]] = {}


def ensure_loop_lag_monitor() -> None:
    """在当前事件循环上启动延迟监测；同一个循环只启动一次。没有运行中的循环时不做事。

    由 ``middleware/metrics.py`` 在应用启动（lifespan）和每个请求进来时调用。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with metrics._lock:
        for key in [k for k, task in _lag_monitors.items() if task.done()]:
            del _lag_monitors[key]
        if id(loop) not in _lag_monitors:
            _lag_monitors[id(loop)] = loop.create_task(
                monitor_event_loop_lag(), name="event-loop-lag-monitor"
            )


def snapshot_metrics() -> dict[str, Any]:
    """返回模块级单例的指标快照。"""
    return metrics.snapshot()


def reset_metrics() -> None:
    """清空模块级单例（仅测试隔离使用）。"""
    metrics.reset()


__all__ = [
    "DEFAULT_SSE_ROUTE",
    "LOOP_LAG_INTERVAL_SECONDS",
    "MAX_ROUTE_SERIES",
    "MAX_SAMPLES",
    "OTHER_ROUTE",
    "QPS_WINDOW_SECONDS",
    "RedMetrics",
    "metrics",
    "ensure_loop_lag_monitor",
    "monitor_event_loop_lag",
    "normalize_path",
    "record_hitl_wait_end",
    "record_hitl_wait_start",
    "record_http_request",
    "record_loop_lag",
    "record_sse_end",
    "record_sse_start",
    "record_tool_call",
    "reset_metrics",
    "snapshot_metrics",
]
