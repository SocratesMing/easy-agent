"""Rowkey 格式：`region(2) + time(13) + symbolHash(13) + frequency + side`。

举例（region=00, time=1677583200000, hash=0001234567890, freq=1N, side=MID）::

    00 1677583200000 0001234567890 1N MID
    └┘ └───────────┘ └───────────┘ └┘ └─┘
     2        13           13       2   3   = 33 字符

## 为什么必须显式建模

**时间戳不在 rowkey 开头**，因此：

- 读取时绝不能拿 `str(开始毫秒)` 当 `startRow`（那样一条都扫不到，而且**静默返回空**，
  因为 `"1677..." > "0016..."`）。必须先拼 region 前缀，见 `guard.row_ranges`。
- 写入时必须按同一规则拼，否则造出来的数据查询侧看不到。

`build()` 给写入方（`seed.py`）用，`parse()` 给诊断与校验用。
"""

from __future__ import annotations

import re
import zlib

# 各段的固定宽度（改动即破坏兼容，与真实数据字典保持一致）
REGION_WIDTH = 2
TIME_WIDTH = 13
SYMBOL_HASH_WIDTH = 13
TIME_OFFSET = REGION_WIDTH
SYMBOL_HASH_OFFSET = TIME_OFFSET + TIME_WIDTH
MIN_LENGTH = SYMBOL_HASH_OFFSET + SYMBOL_HASH_WIDTH

_ROWKEY_RE = re.compile(r"^[A-Za-z0-9]{2}\d{13}[A-Za-z0-9]{" + str(SYMBOL_HASH_WIDTH) + r"}")


def symbol_hash(symbol: str) -> str:
    """合约代码 → 13 位数字 hash。

    真实集群用的是哪种 hash 未知，这里用 crc32 补零到 13 位：只要**同一个合约每次
    得到同一个值**就够用了（这一段只用于把不同合约的 K 线分散到不同 key 段，
    查询侧不依赖它的具体取值）。绝对不能用 `hash()` —— 那个带进程随机盐。
    """
    return f"{zlib.crc32(symbol.encode('utf-8')):0{SYMBOL_HASH_WIDTH}d}"


def time_prefix(region: str, ts_ms: int) -> str:
    """rowkey 的时间段前缀：`{region}{毫秒}`。

    这是范围扫描能生效的前提——`[prefix(region,start), prefix(region,end+1))`
    正好覆盖该 region 下这段时间窗的全部 key。
    """
    return f"{region}{int(ts_ms):0{TIME_WIDTH}d}"


def build(
    ts_ms: int,
    symbol: str,
    side: str,
    region: str = "00",
    frequency: str = "1N",
) -> str:
    """按真实格式拼一个 rowkey。"""
    if len(region) != REGION_WIDTH:
        raise ValueError(f"region 必须是 {REGION_WIDTH} 位，收到 {region!r}")
    if not side:
        raise ValueError("side 不能为空")
    return f"{time_prefix(region, ts_ms)}{symbol_hash(symbol)}{frequency}{side}"


def parse(rowkey: str) -> dict[str, object] | None:
    """尽力把 rowkey 拆回各段；不是这个格式时返回 None。

    用于诊断（比如把 `sample_rows` 的 rowkey 丢进来看格式对不对），
    解析不出来只能说明"格式与约定不符"，不代表数据有问题。
    """
    if len(rowkey) < MIN_LENGTH or not _ROWKEY_RE.match(rowkey):
        return None

    tail = rowkey[SYMBOL_HASH_OFFSET + SYMBOL_HASH_WIDTH:]
    side, frequency = _split_tail(tail)
    return {
        "region": rowkey[:REGION_WIDTH],
        "ts_ms": int(rowkey[TIME_OFFSET:SYMBOL_HASH_OFFSET]),
        "symbol_hash": rowkey[SYMBOL_HASH_OFFSET:SYMBOL_HASH_OFFSET + SYMBOL_HASH_WIDTH],
        "frequency": frequency,
        "side": side,
    }


def _split_tail(tail: str) -> tuple[str, str]:
    """`frequency + side` 无法按固定宽度切，用已知 side 取值从右往左匹配。"""
    from . import config

    for side in sorted(config.buy_sell_values(), key=len, reverse=True):
        if tail.endswith(side):
            return side, tail[: -len(side)]
    return tail, ""
