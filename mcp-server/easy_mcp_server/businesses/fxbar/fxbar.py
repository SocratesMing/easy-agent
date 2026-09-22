"""外汇 K 线问数领域层（表 `HSDC_HDS2_BAR_FX`）。

**这是一张大表**——高频 K 线（6 档频率）× 多币对 × 多渠道 × 双边报价。
所以每次查询都必须先收敛到「一个合约 + 一个频率 + 一段有限时间窗」，
否则会退化成全表扫描：

1. ``CONTRACT_CODE`` 必填——没有它等于整表扫描
2. ``FREQUENCY`` 必填——1M 与 1D 的数据量差 1440 倍
3. 时间窗：缺省「最近 N 小时」（``FX_DEFAULT_WINDOW_HOURS``，缺省 24）；
   显式指定时跨度也**不得超过** ``FX_MAX_WINDOW_DAYS``（缺省 31 天），
   超限时从结束时间往前自动收敛，并在返回里标记 ``clamped``，
   避免模型反复试错（报错会更啰嗦且消耗轮次）
4. 结果强制 ``LIMIT``（缺省 500），按 ``TIME DESC``（最新在前）

其他口径（都写进 :func:`schema_help` 给模型看）：

- ``TIME`` / ``START_TIMESTAMP`` / ``END_TIMESTAMP`` 均为**毫秒级 UTC 时间戳**
  （如 ``1789952000000``），用整数比较，不要转成字符串
- 缺省只看 ``FX_CHANNELS``（缺省 ``BEST,FXALL``）两个渠道
- ``OPEN`` / ``HIGH`` / ``LOW`` / ``CLOSE`` 是**汇率**（decimal(32,10)），不是涨跌幅
- ``TRADE_VOLUME`` 以**基础货币**计、``TRADE_AMT`` 以**计价货币**计，量纲不同，不可相加
- ``ROW_KEY`` 是 ENTITY+INSTRUMENT+CONTRACT_CODE+FREQUENCY+TIME 拼接的复合主键，
  信息完全冗余，本服务不返回
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ...env import load_env
from ...querykit import assert_identifier, env_int, env_list, json_safe, max_rows

DEFAULT_TABLE = "HSDC_HDS2_BAR_FX"
DEFAULT_FREQUENCY = "1H"
DEFAULT_WINDOW_HOURS = 24
DEFAULT_MAX_WINDOW_DAYS = 31

# 缺省渠道：需求明确为 BEST / FXALL 两个
DEFAULT_CHANNELS: tuple[str, ...] = ("BEST", "FXALL")

# 源库枚举（表注释）
FREQUENCIES: tuple[str, ...] = ("1M", "5M", "15M", "1H", "4H", "1D")
INSTRUMENTS: tuple[str, ...] = ("SPOT", "SWAP", "FWD")
SIDES: tuple[str, ...] = ("BID", "ASK", "MID")
DATA_TYPES: tuple[str, ...] = ("OHLC", "TRADE", "MID")

# 查询返回的列（不含冗余的 ROW_KEY）；OPEN/CLOSE 等在部分引擎里是关键字，统一加反引号
SELECT_COLUMNS: tuple[str, ...] = (
    "TIME",
    "START_TIMESTAMP",
    "END_TIMESTAMP",
    "CHANNEL",
    "ENTITY",
    "INSTRUMENT",
    "DATA_TYPE",
    "CONTRACT_CODE",
    "FREQUENCY",
    "SIDE",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "TRADE_VOLUME",
    "TRADE_AMT",
)

DEFAULT_ROW_LIMIT = 500


def _columns() -> str:
    return ", ".join(f"`{name}`" for name in SELECT_COLUMNS)


# ── 配置 ────────────────────────────────────────────────────────────────


def table_name() -> str:
    load_env()
    return os.environ.get("FX_TABLE", "").strip() or DEFAULT_TABLE


def schema_name() -> str:
    """表所在库；留空表示"当前连接指向的库"。

    外汇表通常与主库不在同一个 database（可能是列存引擎），
    表在别的库时把库名填进 ``FX_SCHEMA`` 即可，无需改代码。
    """
    load_env()
    return os.environ.get("FX_SCHEMA", "").strip()


def table_ref() -> str:
    """SQL 里的表引用（含可选库名前缀），标识符全部经校验。"""
    table = assert_identifier(table_name())
    schema = schema_name()
    if schema:
        return f"`{assert_identifier(schema)}`.`{table}`"
    return f"`{table}`"


def default_channels() -> tuple[str, ...]:
    return env_list("FX_CHANNELS") or DEFAULT_CHANNELS


def default_frequency() -> str:
    load_env()
    raw = os.environ.get("FX_DEFAULT_FREQUENCY", "").strip().upper()
    return raw if raw in FREQUENCIES else DEFAULT_FREQUENCY


def default_window_hours() -> int:
    return env_int("FX_DEFAULT_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)


def max_window_days() -> int:
    return env_int("FX_MAX_WINDOW_DAYS", DEFAULT_MAX_WINDOW_DAYS)


def row_limit(limit: int | None = None, default: int = DEFAULT_ROW_LIMIT) -> int:
    return max(1, min(int(limit) if limit else default, max_rows()))


# ── 时间（毫秒 UTC 时间戳） ─────────────────────────────────────────────


def now_millis() -> int:
    return int(time.time() * 1000)


def format_millis(value: Any) -> str:
    """毫秒时间戳 → ``YYYY-MM-DD HH:MM:SS``（UTC）。"""
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    # 兼容误传秒级时间戳（小于 1e11 视为秒）
    seconds = millis / 1000 if millis > 100_000_000_000 else millis
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_time(value: str) -> int:
    """``YYYY-MM-DD`` 或 ``YYYY-MM-DD HH:MM:SS`` → 毫秒 UTC 时间戳。

    表里存的是 UTC 毫秒，所以这里也按 UTC 解释，不做本地时区换算
    （否则同一句话在北京和伦敦会查出不同结果）。
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("时间不能为空")
    # 允许 ISO 的 T 分隔与末尾的 Z
    normalized = text.replace("T", " ").rstrip("Z").strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(normalized, pattern)
            return int(parsed.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"无法解析的时间: {value!r}（期望 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS，UTC）")


@dataclass(frozen=True)
class Window:
    """已收敛的时间窗。``clamped`` 为真表示因超过上限被缩短过。"""

    start_ms: int
    end_ms: int
    days: float
    clamped: bool
    explicit: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": format_millis(self.start_ms),
            "end": format_millis(self.end_ms),
            "days": round(self.days, 3),
            "clamped": self.clamped,
            "explicit_range": self.explicit,
        }


def resolve_window(
    days: float | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    *,
    hours: int | None = None,
    now: int | None = None,
) -> Window:
    """把「最近 N 天/小时」或显式区间统一收敛为一个合法时间窗。

    - 显式 ``start_time`` / ``end_time`` 优先
    - 只给其中一端时，另一端用"现在"补齐
    - 跨度超过 ``FX_MAX_WINDOW_DAYS`` 时，**以结束时间为锚点往前收敛**
      （结束时间通常是用户真正关心的边界），并置 ``clamped=True``
    """
    current = now if now is not None else now_millis()
    limit_ms = max_window_days() * 86_400_000

    explicit = bool(start_time or end_time)
    if explicit:
        end_ms = parse_time(end_time) if end_time else current
        if start_time:
            start_ms = parse_time(start_time)
        else:
            start_ms = end_ms - (int(hours) if hours else default_window_hours()) * 3_600_000
        if start_ms > end_ms:
            raise ValueError("起始时间晚于结束时间")
    else:
        end_ms = current
        span_ms = (
            int(hours) * 3_600_000
            if hours
            else int(float(days) * 86_400_000)
            if days
            else default_window_hours() * 3_600_000
        )
        span_ms = max(span_ms, 60_000)  # 至少 1 分钟，避免空窗
        start_ms = end_ms - span_ms

    clamped = False
    if end_ms - start_ms > limit_ms:
        start_ms = end_ms - limit_ms
        clamped = True

    return Window(
        start_ms=start_ms,
        end_ms=end_ms,
        days=(end_ms - start_ms) / 86_400_000,
        clamped=clamped,
        explicit=explicit,
    )


# ── 枚举校验 ────────────────────────────────────────────────────────────


def _validate(value: str | None, allowed: Iterable[str], label: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text not in allowed:
        raise ValueError(f"{label} 只支持: {', '.join(allowed)}（收到 {value!r}）")
    return text


def normalize_frequency(frequency: str | None) -> str:
    text = str(frequency or "").strip().upper() or default_frequency()
    if text not in FREQUENCIES:
        raise ValueError(f"frequency 只支持: {', '.join(FREQUENCIES)}（收到 {frequency!r}）")
    return text


def resolve_channels(channel: str | None) -> tuple[str, ...]:
    """把 channel 参数解析为渠道列表。

    - 不传 / 空 → 用配置的默认渠道（``FX_CHANNELS``，缺省 BEST,FXALL）
    - ``ALL`` / ``*`` → 不过滤渠道
    - 其他值 → 精确匹配单个渠道
    """
    text = str(channel or "").strip().upper()
    if not text:
        return default_channels()
    if text in ("ALL", "*"):
        return ()
    return (text,)


# ── SQL 构造（全部强制带时间窗 + LIMIT） ────────────────────────────────


def _window_condition(window: Window) -> tuple[str, list[Any]]:
    # TIME 与 START/END_TIMESTAMP 是同源的毫秒时间戳；用 TIME 过滤（与主键语义一致）
    return "`TIME` >= %s AND `TIME` <= %s", [window.start_ms, window.end_ms]


def bars_sql(
    contract_code: str,
    frequency: str | None = None,
    window: Window | None = None,
    *,
    channel: str | None = None,
    side: str | None = None,
    data_type: str | None = None,
    instrument: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """K 线明细查询：一个合约 + 一个频率 + 一段有限时间窗。"""
    code = str(contract_code or "").strip()
    if not code:
        raise ValueError("contract_code 必填（可先用 list_fx_instruments 查看可用合约）")

    win = window or resolve_window()
    conditions, params = _window_condition(win)
    conditions = f"`CONTRACT_CODE` = %s AND `FREQUENCY` = %s AND {conditions}"
    params = [code, normalize_frequency(frequency)] + params

    channels = resolve_channels(channel)
    if channels:
        placeholders = ", ".join(["%s"] * len(channels))
        conditions += f" AND `CHANNEL` IN ({placeholders})"
        params.extend(channels)

    for value, allowed, column in (
        (side, SIDES, "`SIDE`"),
        (data_type, DATA_TYPES, "`DATA_TYPE`"),
        (instrument, INSTRUMENTS, "`INSTRUMENT`"),
    ):
        validated = _validate(value, allowed, column)
        if validated:
            conditions += f" AND {column} = %s"
            params.append(validated)

    sql = (
        f"SELECT {_columns()} FROM {table_ref()} "
        f"WHERE {conditions} ORDER BY `TIME` DESC LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def latest_sql(
    contract_code: str,
    frequency: str | None = None,
    *,
    channel: str | None = None,
    side: str | None = None,
    data_type: str | None = None,
    lookback_days: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """最新一条 K 线。

    刻意**不**写成 ``ORDER BY TIME DESC LIMIT 1`` 全表取最新——主键 ROW_KEY
    是拼接串（ENTITY+INSTRUMENT+CONTRACT_CODE+FREQUENCY+TIME），单靠
    CONTRACT_CODE 过滤未必能走索引。这里仍加一个较宽的回顾窗口，
    兼顾"取最新"与"不拖垮大表"。
    """
    days = int(lookback_days) if lookback_days else min(max_window_days(), 7)
    window = resolve_window(hours=days * 24)
    conditions, params = _window_condition(window)

    code = str(contract_code or "").strip()
    if not code:
        raise ValueError("contract_code 必填（可先用 list_fx_instruments 查看可用合约）")
    conditions = f"`CONTRACT_CODE` = %s AND `FREQUENCY` = %s AND {conditions}"
    params = [code, normalize_frequency(frequency)] + params

    channels = resolve_channels(channel)
    if channels:
        placeholders = ", ".join(["%s"] * len(channels))
        conditions += f" AND `CHANNEL` IN ({placeholders})"
        params.extend(channels)

    for value, allowed, column in ((side, SIDES, "`SIDE`"), (data_type, DATA_TYPES, "`DATA_TYPE`")):
        validated = _validate(value, allowed, column)
        if validated:
            conditions += f" AND {column} = %s"
            params.append(validated)

    sql = (
        f"SELECT {_columns()} FROM {table_ref()} "
        f"WHERE {conditions} ORDER BY `TIME` DESC LIMIT 1"
    )
    return sql, tuple(params)


def instruments_sql(
    *,
    channel: str | None = None,
    days: int | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """可查询的合约清单（含渠道、K 线根数与时间覆盖范围）。

    只扫最近 ``days`` 天（缺省 7 天）后的数据——大表上对全历史做 GROUP BY
    代价不可控，而"当前有哪些合约可查"这个问题本来也只需要近期的数据。
    """
    lookback = int(days) if days else 7
    window = resolve_window(hours=lookback * 24)
    conditions, params = _window_condition(window)

    channels = resolve_channels(channel)
    if channels:
        placeholders = ", ".join(["%s"] * len(channels))
        conditions += f" AND `CHANNEL` IN ({placeholders})"
        params.extend(channels)

    size = max(1, min(int(limit) if limit else 200, max_rows()))
    sql = (
        "SELECT `CONTRACT_CODE`, `INSTRUMENT`, `CHANNEL`, "
        "COUNT(*) AS bar_count, MIN(`TIME`) AS first_time, MAX(`TIME`) AS last_time "
        f"FROM {table_ref()} WHERE {conditions} "
        "GROUP BY `CONTRACT_CODE`, `INSTRUMENT`, `CHANNEL` "
        f"ORDER BY `CONTRACT_CODE`, `CHANNEL` LIMIT {size}"
    )
    return sql, tuple(params)


# ── 结果整形 ────────────────────────────────────────────────────────────


def decorate(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """补可读时间（UTC），并把 Decimal 转成 JSON 可序列化类型。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        item = json_safe(dict(row))
        raw_ms = row.get("TIME")
        item["time_text"] = format_millis(raw_ms)
        item["start_time_text"] = format_millis(row.get("START_TIMESTAMP"))
        item["end_time_text"] = format_millis(row.get("END_TIMESTAMP"))
        # 价格类字段保持数值，但给模型一个明确的"这是汇率"的信号
        for key in ("OPEN", "HIGH", "LOW", "CLOSE"):
            item[key.lower()] = item.pop(key, None)
        for key in ("TRADE_VOLUME", "TRADE_AMT"):
            item[key.lower()] = item.pop(key, None)
        for key in ("CONTRACT_CODE", "CHANNEL", "INSTRUMENT", "DATA_TYPE", "FREQUENCY", "SIDE", "ENTITY"):
            item[key.lower()] = item.pop(key, None)
        item.pop("TIME", None)
        item.pop("START_TIMESTAMP", None)
        item.pop("END_TIMESTAMP", None)
        items.append(item)
    return items


def summarize_bars(items: list[dict[str, Any]], contract_code: str, frequency: str, window: Window) -> str:
    """给模型的一句话摘要：行数、时间覆盖、区间高低收。"""
    if not items:
        return (
            f"{contract_code} {frequency} 在 {format_millis(window.start_ms)} ~ "
            f"{format_millis(window.end_ms)}（UTC）之间没有数据。"
            "可能原因：该合约/频率/渠道组合无数据，或时间窗内确实为空——"
            "可用 list_fx_instruments 确认合约与时间覆盖范围。"
        )

    highs = [float(x["high"]) for x in items if x.get("high") is not None]
    lows = [float(x["low"]) for x in items if x.get("low") is not None]
    newest = items[0].get("time_text") or ""
    oldest = items[-1].get("time_text") or ""
    span = f"{oldest} ~ {newest}"
    price = ""
    if highs and lows:
        price = f"，区间最高 {max(highs)}、最低 {min(lows)}"
    return (
        f"{contract_code} {frequency}：{len(items)} 根 K 线（{span} UTC）{price}。"
    )


def summarize_instruments(items: list[dict[str, Any]], days: int) -> str:
    if not items:
        return f"最近 {days} 天内没有查到任何合约数据（请确认渠道配置与数据是否同步）。"
    codes = sorted({str(x.get("contract_code") or "") for x in items})
    channels = sorted({str(x.get("channel") or "") for x in items})
    return (
        f"最近 {days} 天内共 {len(codes)} 个合约、{len(channels)} 个渠道："
        f"合约 {'、'.join(codes[:20])}{' 等' if len(codes) > 20 else ''}；"
        f"渠道 {'、'.join(channels)}。"
    )


def schema_help() -> dict[str, Any]:
    """给模型看的口径说明。**第一次提问时应先调用它**。"""
    return {
        "table": table_ref(),
        "table_note": "外汇市场高频 K 线大表；每次查询必须限定合约 + 频率 + 有限时间窗",
        "grain": "一行 = 一个合约 × 一个频率 × 一个渠道 × 一个时间点 × 一个买卖方向",
        "columns": {
            "TIME": "K 线时间戳（毫秒 UTC，如 1789952000000）",
            "START_TIMESTAMP / END_TIMESTAMP": "K 线起止时间戳（毫秒 UTC，与 TIME 同源）",
            "CHANNEL": "交易渠道（缺省只看 BEST / FXALL，可用 channel='ALL' 放开）",
            "ENTITY": "机构实体代码（交易柜台编号）",
            "INSTRUMENT": "外汇工具类型：SPOT 即期 / SWAP 掉期 / FWD 远期",
            "DATA_TYPE": "数据类型：OHLC 开高低收 / TRADE 交易 / MID 中间价",
            "CONTRACT_CODE": "外汇合约代码（如 EURUSD、USDCNY）",
            "FREQUENCY": "K 线频率：" + " / ".join(FREQUENCIES),
            "SIDE": "买卖方向：BID 买价 / ASK 卖价 / MID 中间价",
            "OPEN/HIGH/LOW/CLOSE": "该 K 线的开高低收**汇率**（decimal(32,10)），不是涨跌幅",
            "TRADE_VOLUME": "交易量，**基础货币**单位（如 USDCNY 时为美元）",
            "TRADE_AMT": "交易金额，**计价货币**单位（如 USDCNY 时为人民币）",
            "ROW_KEY": "复合主键拼接串（ENTITY+INSTRUMENT+CONTRACT_CODE+FREQUENCY+TIME），本服务不返回",
        },
        "enums": {
            "frequency": list(FREQUENCIES),
            "instrument": list(INSTRUMENTS),
            "side": list(SIDES),
            "data_type": list(DATA_TYPES),
            "default_channels": list(default_channels()),
        },
        "time": {
            "format": "毫秒级 UTC 时间戳（整数比较，不要转字符串）",
            "default_window_hours": default_window_hours(),
            "max_window_days": max_window_days(),
            "note": "不传时间参数时默认查最近 "
            f"{default_window_hours()} 小时；显式范围超过 {max_window_days()} 天会被自动收敛并标记 clamped",
        },
        "performance": [
            "这是一张大表，请务必带上 contract_code 与 frequency",
            "需要更早的数据时显式传 start_time / end_time（UTC），或 days=N",
            "结果按时间倒序，可能被 LIMIT 截断——截断时用更小的时间窗分批取",
        ],
        "notes": [
            "TRADE_VOLUME 与 TRADE_AMT 量纲不同，不能相加",
            "OPEN/HIGH/LOW/CLOSE 是汇率，没有直接可比性（不同币对量级差异很大）",
            "缺省只返回 BEST / FXALL 两个渠道；要全部渠道传 channel='ALL'",
        ],
        "typical_questions": {
            "有哪些可查的货币对": "list_fx_instruments()",
            "EURUSD 最近一天的 1 小时 K 线": "get_fx_bars(contract_code='EURUSD', frequency='1H', days=1)",
            "美元兑人民币上周的日线": "get_fx_bars(contract_code='USDCNY', frequency='1D', start_time='2026-10-05', end_time='2026-10-11')",
            "某合约最新报价": "get_latest_fx_quote(contract_code='EURUSD', frequency='1H')",
        },
    }
