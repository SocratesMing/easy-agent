"""时间窗解析：把模型给出的时间表达统一收敛成毫秒区间。

HBase 里的时间戳都是毫秒整数（如 `1677583200000`）。模型既可能传 ISO 字符串，
也可能直接传毫秒，这里做归一化；时间给不全时套用一个保守的默认回看窗口，
避免"问上个月行情"变成全表扫描把 Thrift 连接拖死。

时区：毫秒本身是 UTC 绝对时刻，但模型给出的 **朴素字符串**（如 `2026-09-14 09:30`）
必须按业务时区解释，默认 `Asia/Shanghai`（`HBASEQA_TZ` 可调）。
带偏移量的字符串（含 `Z` / `+08:00`）按自身时区解释，忽略配置。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import config

_MS_PER_HOUR = 3_600_000
_MS_PER_DAY = 86_400_000


def timezone_of():
    name = config.timezone_name()
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - 取决于系统 tzdata
        return timezone.utc


def _strip_utc_marker(text: str) -> str:
    return text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text


def parse_ms(value: Any) -> int:
    """把入参转成毫秒时间戳；无法解析时抛 ValueError。

    接受：毫秒 int/float、纯数字字符串（按毫秒）、ISO 字符串
    （`2026-09-14` / `2026-09-14 09:30` / `2026-09-14T09:30:00+08:00` / `...Z`）。
    """
    if value is None:
        raise ValueError("时间参数为空")
    if isinstance(value, bool):
        raise ValueError(f"无法解析时间: {value!r}")

    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text:
        raise ValueError("时间参数为空")

    if text.isdigit() or (text.lstrip("-").isdigit() and len(text) > 1):
        return int(text)

    candidate = _strip_utc_marker(text)
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError as e:
        raise ValueError(
            f"无法解析时间: {text!r}（支持毫秒整数或 ISO 时间，如 2026-09-14 09:30）"
        ) from e

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone_of())
    return int(moment.timestamp() * 1000)


def to_iso(ms: int | None) -> str | None:
    """毫秒 → 业务时区的可读字符串（工具返回给模型的展示字段）。"""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone_of()).isoformat(timespec="seconds")


def resolve_window(
    start: Any = None,
    end: Any = None,
    hours: Any = None,
    days: Any = None,
) -> tuple[int, int]:
    """解析查询窗口，返回闭区间 `[start_ms, end_ms]`。

    优先级：`end` + (`hours`|`days`) > `start` + `end` > 最近 N 小时（默认窗口）。
    `hours` / `days` 同时给出时以 `hours` 为准并忽略 `days`，避免二义。
    """
    end_ms = parse_ms(end) if end is not None else None
    start_ms = parse_ms(start) if start is not None else None

    if hours is not None:
        anchor = end_ms if end_ms is not None else start_ms
        span_hours = _positive_span(hours, "hours")
        if anchor is None:
            anchor = int(datetime.now(tz=timezone_of()).timestamp() * 1000)
        return (anchor - span_hours * _MS_PER_HOUR, anchor)

    if days is not None:
        anchor = end_ms if end_ms is not None else start_ms
        span_days = _positive_span(days, "days")
        if anchor is None:
            anchor = int(datetime.now(tz=timezone_of()).timestamp() * 1000)
        return (anchor - span_days * _MS_PER_DAY, anchor)

    if start_ms is not None and end_ms is not None:
        return (min(start_ms, end_ms), max(start_ms, end_ms))

    if end_ms is not None:
        return (end_ms - config.default_window_hours() * _MS_PER_HOUR, end_ms)

    if start_ms is not None:
        return (start_ms, start_ms + config.default_window_hours() * _MS_PER_HOUR)

    now = int(datetime.now(tz=timezone_of()).timestamp() * 1000)
    return (now - config.default_window_hours() * _MS_PER_HOUR, now)


def _positive_span(value: Any, label: str) -> int:
    try:
        span = int(float(value))
    except (TypeError, ValueError) as e:
        raise ValueError(f"{label} 必须是数字，收到 {value!r}") from e
    if span <= 0:
        raise ValueError(f"{label} 必须大于 0，收到 {value!r}")
    return span


def describe_window(start_ms: int, end_ms: int) -> str:
    """给模型的窗口说明（含毫秒原值，方便对照工具返回）。"""
    span = end_ms - start_ms
    days, remainder = divmod(span, _MS_PER_DAY)
    hours = remainder // _MS_PER_HOUR
    span_text = f"{days} 天 {hours} 小时" if days else f"{hours} 小时"
    return (
        f"{to_iso(start_ms)} ~ {to_iso(end_ms)}（{span_text}，"
        f"毫秒区间 [{start_ms}, {end_ms}]）"
    )
