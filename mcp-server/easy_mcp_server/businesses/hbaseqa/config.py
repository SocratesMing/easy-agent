"""hbaseqa 配置：Thrift 连接、扫描边界、Kerberos 预留与外汇行情口径常量。

风格与 `db.mysql_config()` 保持一致：全部读环境变量（可由 mcp-server/.env 提供），
未配置时取保守默认值。布尔型只认显式的真值，避免空串被当成 True。
"""

from __future__ import annotations

import os

from ...env import load_env

DEFAULT_MAX_ROWS = 500
DEFAULT_MAX_SCANNED_ROWS = 50_000
# 时间止损与单次 RPC 取行数（详见 scan_budget_ms / scan_batch_rows）
DEFAULT_SCAN_BUDGET_MS = 8_000
DEFAULT_SCAN_BATCH_ROWS = 5_000
DEFAULT_THRIFT_HOST = "127.0.0.1"
DEFAULT_THRIFT_PORT = 9090
DEFAULT_TIMEOUT_MS = 10_000
# Thrift 传输/协议：必须与服务端一致，配错时服务端会直接掐断连接
# （Windows 上表现为 WinError 10053/10054）。
DEFAULT_THRIFT_TRANSPORT = "buffered"
DEFAULT_THRIFT_PROTOCOL = "binary"
THRIFT_TRANSPORTS = ("buffered", "framed")
THRIFT_PROTOCOLS = ("binary", "compact")
# 连接类错误的重试次数（Thrift 会回收空闲连接，长连接单例容易打空）
DEFAULT_CONNECT_ATTEMPTS = 2
DEFAULT_COLUMN_FAMILY = "CF"
# 真实 rowkey 以 region 开头，所以默认不是 ts（写错的代价是"静默返回空"）
DEFAULT_ROWKEY_LAYOUT = "region_ts"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_WINDOW_HOURS = 6
DEFAULT_TABLE_PREFIX = "HSDC_HDS2_"

# ── 取数粒度与扫描预算 ────────────────────────────────────────────────
#
# 一个月 × 3 合约 × 1 分钟的明细是 13 万行，塞进模型上下文既慢又没用
# （模型只会看前几十行就开始总结）。所以默认按"粒度"降采样：
#
# - auto（缺省）：窗口超过 AUTO_COARSEN_HOURS 自动降到 daily，否则 raw 明细
# - raw / latest / hourly / daily：显式指定
#
# 降采样不是"读完再筛"（那会被 MAX_SCANNED_ROWS 截断 → 日线静默缺一半），
# 而是**按桶就近取值**：反向扫桶尾一小段拿各合约最后一根，不够再逐级放大。
GRANULARITIES = ("auto", "raw", "latest", "hourly", "daily")
DEFAULT_GRANULARITY = "auto"
AUTO_COARSEN_HOURS = 48
BUCKET_MS_BY_GRANULARITY = {"hourly": 3_600_000, "daily": 86_400_000}
# 桶内探查起始行数 = 期望合约数 × 方向数 × 该系数
BUCKET_LIMIT_FACTOR = 4
# 探查上限阶梯（行）：不够就按这个加码，避免单桶把全局预算吃光
BUCKET_LIMIT_LADDER = (512, 4096)
# 合约数未知时的假设值（只影响探查起点，不影响正确性）
ASSUMED_CONTRACT_COUNT = 8
# list_contracts 的扫描预算（行）：它只需"有哪些合约"，没必要读满整个窗口
DEFAULT_CONTRACT_SCAN_BUDGET = 5_000

# 反向扫描：Thrift2 的 `TScan.reversed` 原生支持（Thrift1 时代才需要探测驱动能力）
DEFAULT_REVERSE_SCAN = True

# rowkey 形态。真实格式是 `region(2)+time(13)+symbolHash(13)+frequency+side`，
# 所以时间戳**不在开头**：拿毫秒当 startRow 会一条都扫不到且不报错。
# - region_ts：按 `{region}{毫秒}` 拼范围，每 region 一段（缺省）
# - ts：rowkey 以纯毫秒时间戳开头（单机简化表可能这样）
# - unknown：格式未知，退回 CF:TIME 列过滤（正确但慢）
ROWKEY_LAYOUTS = ("region_ts", "ts", "unknown")
# region 前缀（rowkey 前 2 位）。单机无分区时通常是 00
DEFAULT_REGION_PREFIXES = ("00",)

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

# 从表名反解维度时使用的已知产品类型（用于在 token 流里定位 INSTRUMENT 锚点）
KNOWN_INSTRUMENTS: tuple[str, ...] = (
    "FXSPOT",
    "FXSWAP",
    "FXFWD",
    "FXNDF",
    "METAL",
    "CRYPTO",
    "BOND",
    "IRS",
)
# 已知数据类型，仅用于向模型展示口径（解析靠"锚点右侧全部 token"，
# 所以 BAR_DEPTH / BAR_BEST 这类复合值不需要再往这里加）
KNOWN_DATATYPES: tuple[str, ...] = (
    "BAR_DEPTH",
    "BAR_BEST",
    "TICK_DEPTH",
    "BAR",
    "TICK",
    "DEPTH",
    "SNAPSHOT",
    "ORDERBOOK",
)

# 频率别名：模型常说 1m / 1min，库里存的是 1N（1 分钟）
FREQUENCY_ALIASES: dict[str, str] = {
    "1M": "1N",
    "M1": "1N",
    "MIN1": "1N",
    "1MIN": "1N",
    "1MINUTE": "1N",
}
DEFAULT_FREQUENCY = "1N"

# 买卖方向取值（`HBASEQA_BUYSELL_VALUES` 可覆盖）
DEFAULT_BUYSELL_VALUES: tuple[str, ...] = ("BID", "ASK", "MID")

# 行情字段：(英文限定符, 中文语义, 是否数值)。
# 扫描时只请求这些限定符，其余列族一概不读，顺带降低了误取内部列的风险。
FIELD_DICT: tuple[tuple[str, str, bool], ...] = (
    ("CHANNEL", "渠道来源，如 UBS", False),
    ("DATATYPESTR", "数据类型，如 BAR_DEPTH", False),
    ("CONTRACTCODE", "合约代码，如 EURUSDSP", False),
    ("INSTRUMENT", "产品类型，如 FXSPOT", False),
    ("FREQUENCY", "K 线频率，1N=1 分钟", False),
    ("BUYSELL", "买卖方向，MID=中间价 / BID=买价 / ASK=卖价", False),
    ("TIME", "K 线时间戳（毫秒），主时间过滤列", True),
    ("SYSTIME", "系统落库时间（毫秒），晚于 TIME，用于排查延迟", True),
    ("STARTTIMESTAMP", "K 线起始时间戳（毫秒）", True),
    ("ENDTIMESTAMP", "K 线结束时间戳（毫秒）", True),
    ("OPEN", "开盘价", True),
    ("CLOSE", "收盘价", True),
    ("HIGH", "最高价", True),
    ("LOW", "最低价", True),
    ("TRADEVOLUME", "成交量", True),
    ("TRADEAMT", "成交额", True),
)

FIELD_NAMES: tuple[str, ...] = tuple(name for name, _, _ in FIELD_DICT)
NUMERIC_FIELDS = frozenset(name for name, _, numeric in FIELD_DICT if numeric)
# 时间基准列：按优先级尝试，任一解析成功即可参与时间过滤与校验
TIME_FIELDS: tuple[str, ...] = ("TIME", "STARTTIMESTAMP", "ENDTIMESTAMP")
# 数值列中属于价格、可用于汇总的特征
PRICE_FIELDS: tuple[str, ...] = ("OPEN", "CLOSE", "HIGH", "LOW")


def _int_env(key: str, default: int) -> int:
    load_env()
    raw = os.environ.get(key, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _str_env(key: str, default: str) -> str:
    load_env()
    return os.environ.get(key, "").strip() or default


def _bool_env(key: str, default: bool = False) -> bool:
    load_env()
    return os.environ.get(key, "").strip().lower() in TRUE_VALUES


# ── 连接 ──────────────────────────────────────────────────────────────


def thrift_host() -> str:
    """Thrift Server 主机（`HBASEQA_THRIFT_HOST`）。"""
    return _str_env("HBASEQA_THRIFT_HOST", DEFAULT_THRIFT_HOST)


def thrift_port() -> int:
    """Thrift Server 端口（`HBASEQA_THRIFT_PORT`，缺省 9090）。"""
    return _int_env("HBASEQA_THRIFT_PORT", DEFAULT_THRIFT_PORT)


def timeout_ms() -> int:
    """Thrift 单次调用超时毫秒数（`HBASEQA_TIMEOUT_MS`，缺省 10000）。"""
    return _int_env("HBASEQA_TIMEOUT_MS", DEFAULT_TIMEOUT_MS)


def thrift_transport() -> str:
    """Thrift 传输层（`HBASEQA_THRIFT_TRANSPORT`，buffered / framed）。

    HBase Thrift Server 两种都支持，但**必须与服务端配置匹配**：不匹配时
    服务端解析握手失败会直接 RST，本地看到的是 WinError 10053/10054。
    """
    value = _str_env("HBASEQA_THRIFT_TRANSPORT", DEFAULT_THRIFT_TRANSPORT).lower()
    return value if value in THRIFT_TRANSPORTS else DEFAULT_THRIFT_TRANSPORT


def thrift_protocol() -> str:
    """Thrift 协议（`HBASEQA_THRIFT_PROTOCOL`，binary / compact）。"""
    value = _str_env("HBASEQA_THRIFT_PROTOCOL", DEFAULT_THRIFT_PROTOCOL).lower()
    return value if value in THRIFT_PROTOCOLS else DEFAULT_THRIFT_PROTOCOL


def connect_attempts() -> int:
    """连接类错误的重试次数（`HBASEQA_CONNECT_ATTEMPTS`，缺省 2 = 失败后重试 1 次）。"""
    return _int_env("HBASEQA_CONNECT_ATTEMPTS", DEFAULT_CONNECT_ATTEMPTS)


# ── 扫描边界 ──────────────────────────────────────────────────────────


def max_rows() -> int:
    """单次工具返回的最大行数（`HBASEQA_MAX_ROWS`，缺省 500）。"""
    return _int_env("HBASEQA_MAX_ROWS", DEFAULT_MAX_ROWS)


def buy_sell_values() -> tuple[str, ...]:
    """允许的买卖方向（`HBASEQA_BUYSELL_VALUES`，缺省 BID/ASK/MID）。"""
    load_env()
    raw = os.environ.get("HBASEQA_BUYSELL_VALUES", "").strip()
    values = tuple(v.strip().upper() for v in raw.split(",") if v.strip())
    return values or DEFAULT_BUYSELL_VALUES


def normalise_frequency(value: str | None) -> str | None:
    """把频率别名归一化成库里存的写法（1m / 1min / M1 → 1N）。"""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    return FREQUENCY_ALIASES.get(text, text)


def max_scanned_rows() -> int:
    """单次 scan 最多读多少行（`HBASEQA_MAX_SCANNED_ROWS`，缺省 50000）。

    这是 Thrift 通道的硬止损。HBase 没有 LIMIT，MacBook 侧的默认值保证
    一次失控的宽时间窗不会把进程内存打爆；结果 rows 再由 :func:`max_rows` 裁剪。
    """
    return _int_env("HBASEQA_MAX_SCANNED_ROWS", DEFAULT_MAX_SCANNED_ROWS)


def scan_budget_ms() -> int:
    """单次取数的**时间**预算（`HBASEQA_SCAN_BUDGET_MS`，缺省 8000）。

    行数预算（:func:`max_scanned_rows`）挡的是内存，挡不住耗时：5 万行在慢通道
    上能跑几分钟，而 MCP 客户端等响应头是有超时的（streamable_http 用 read 超时，
    缺省 300s），超时就抛 httpx.ReadTimeout，被 anyio 包成 ExceptionGroup——
    前端只看到"unhandled errors in a TaskGroup"，白等好几分钟还没有结果。

    所以再加一道时间止损：到点就带着已经扫到的行返回，并在 notes 里说明。
    """
    return max(0, _int_env("HBASEQA_SCAN_BUDGET_MS", DEFAULT_SCAN_BUDGET_MS))


def scan_batch_rows() -> int:
    """一次 getScannerRows 取多少行（`HBASEQA_SCAN_BATCH_ROWS`，缺省 5000）。

    Thrift2 的 numRows 就是"每次 RPC 取多少行"，是扫描耗时的主要来源：
    5 万行按 1000/次要 50 次串行 RPC，按 5000/次只要 10 次。
    """
    return max(1, _int_env("HBASEQA_SCAN_BATCH_ROWS", DEFAULT_SCAN_BATCH_ROWS))


def reverse_scan_enabled() -> bool:
    return _bool_env("HBASEQA_REVERSE_SCAN", DEFAULT_REVERSE_SCAN)


# ── 表与列 ────────────────────────────────────────────────────────────


def table_prefix() -> str:
    """业务表前缀（`HBASEQA_TABLE_PREFIX`，缺省 `HSDC_HDS2_`）。"""
    return _str_env("HBASEQA_TABLE_PREFIX", DEFAULT_TABLE_PREFIX)


def list_all_tables() -> bool:
    """是否绕过前缀过滤展示集群全部表（`HBASEQA_LIST_ALL_TABLES`）。"""
    return _bool_env("HBASEQA_LIST_ALL_TABLES")


def allowed_tables() -> list[str] | None:
    """表白名单（`HBASEQA_ALLOWED_TABLES`，逗号分隔）；未配置返回 None。"""
    load_env()
    tables = [t.strip() for t in os.environ.get("HBASEQA_ALLOWED_TABLES", "").split(",")]
    visible = [t for t in tables if t]
    return visible or None


def denied_tables() -> tuple[str, ...]:
    """表黑名单（`HBASEQA_DENY_TABLES`，逗号分隔）。"""
    load_env()
    extra = [t.strip() for t in os.environ.get("HBASEQA_DENY_TABLES", "").split(",")]
    return tuple(t for t in extra if t)


def column_family() -> str:
    """列族名（`HBASEQA_COLUMN_FAMILY`，缺省 CF）。"""
    return _str_env("HBASEQA_COLUMN_FAMILY", DEFAULT_COLUMN_FAMILY)


def rowkey_layout() -> str:
    """rowkey 形态（`HBASEQA_ROWKEY_LAYOUT`，`region_ts` / `ts` / `unknown`）。"""
    value = _str_env("HBASEQA_ROWKEY_LAYOUT", DEFAULT_ROWKEY_LAYOUT)
    return value if value in ROWKEY_LAYOUTS else DEFAULT_ROWKEY_LAYOUT


def region_prefixes() -> tuple[str, ...]:
    """rowkey 开头的 region 前缀（`HBASEQA_REGION_PREFIXES`，逗号分隔，缺省 00）。

    范围扫描是"每个 region 一段"，所以多 region 时会对每段各扫一次再合并。
    """
    load_env()
    raw = os.environ.get("HBASEQA_REGION_PREFIXES", "").strip()
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or DEFAULT_REGION_PREFIXES


def use_time_column_filter() -> bool:
    """是否在 rowkey 之外额外加 `CF:TIME` 列过滤（`HBASEQA_TIME_COLUMN_FILTER`）。"""
    return _bool_env("HBASEQA_TIME_COLUMN_FILTER")


# ── 时间 ──────────────────────────────────────────────────────────────


def timezone_name() -> str:
    return _str_env("HBASEQA_TZ", DEFAULT_TIMEZONE)


def default_window_hours() -> int:
    """未显式给时间时的默认回看窗口（`HBASEQA_DEFAULT_WINDOW_HOURS`，缺省 6）。"""
    return _int_env("HBASEQA_DEFAULT_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)


def granularity() -> str:
    """默认取数粒度（`HBASEQA_GRANULARITY`，缺省 auto）。"""
    value = _str_env("HBASEQA_GRANULARITY", DEFAULT_GRANULARITY).lower()
    return value if value in GRANULARITIES else DEFAULT_GRANULARITY


def auto_coarsen_hours() -> int:
    """auto 粒度下，窗口超过多少小时就降采样（`HBASEQA_AUTO_COARSEN_HOURS`）。"""
    return _int_env("HBASEQA_AUTO_COARSEN_HOURS", AUTO_COARSEN_HOURS)


def contract_scan_budget() -> int:
    """list_contracts 的扫描行数预算（`HBASEQA_CONTRACT_SCAN_BUDGET`，缺省 5000）。"""
    return _int_env("HBASEQA_CONTRACT_SCAN_BUDGET", DEFAULT_CONTRACT_SCAN_BUDGET)


# ── Kerberos 预留 ─────────────────────────────────────────────────────
#
# 当前集群未启用 Kerberos，配置口径先定下来（ principal / keytab / service / realm ），
# 真实接入点集中在 client 里一处：切换到 SASL 传输只需改那里，配置不用再动。


def kerberos_config() -> dict[str, str]:
    """Kerberos 相关配置快照（供排障与启动自检打印）。"""
    load_env()
    return {
        "enabled": str(_bool_env("HBASEQA_KERBEROS_ENABLED")),
        "principal": os.environ.get("HBASEQA_KERBEROS_PRINCIPAL", "").strip(),
        "keytab": os.environ.get("HBASEQA_KERBEROS_KEYTAB", "").strip(),
        "service": os.environ.get("HBASEQA_KERBEROS_SERVICE", "").strip() or "hbase",
        "realm": os.environ.get("HBASEQA_KERBEROS_REALM", "").strip(),
        "kinit_cmd": os.environ.get("HBASEQA_KERBEROS_KINIT", "").strip() or "kinit",
    }


def kerberos_enabled() -> bool:
    return _bool_env("HBASEQA_KERBEROS_ENABLED")
