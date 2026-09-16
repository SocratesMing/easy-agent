"""向 HBase 写入合成外汇行情（仅用于测试/演示）。

用法::

    cd mcp-server
    uv run python -m easy_mcp_server.businesses.hbaseqa.seed            # 预览，不写
    uv run python -m easy_mcp_server.businesses.hbaseqa.seed --yes      # 真写
    uv run python -m easy_mcp_server.businesses.hbaseqa.seed --hours 6 --sides BID,ASK,MID --yes

为什么是独立 CLI 而不是 MCP 工具：问数链路是**只读**的。写权限只存在于命令行，
模型永远碰不到（`server.py` 不 import 本模块）。

安全设计（往真实集群写数据，宁可多问一句）：

1. **默认 dry-run**：不加 `--yes` 只打印计划与 rowkey 样例
2. **写前自检**：先探测第一个 rowkey 是否已存在；若已存在且 `CONTRACTCODE` 不同，
   说明 rowkey 拼法与真实数据不一致（继续写会**覆盖别人的数据**），直接中止
3. **只写不删**：不提供任何删除路径，也不动时间窗之外的数据
4. **可复现**：价格随机游走的种子由合约代码决定，重复执行得到同一批数据
"""

from __future__ import annotations

import argparse
import random
import time
import zlib
from datetime import datetime, timezone
from typing import Iterator

from . import client, config, metadata, rowkey as rowkey_fmt

MINUTE_MS = 60_000
DEFAULT_TABLE = "HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH"
DEFAULT_CONTRACTS = ("EURUSDSP", "USDJPYSP", "GBPUSDSP")
# 基准价与 1 分钟波动幅度（相对值），只是让数据看起来合理
BASE_PRICES = {"EURUSDSP": 1.0856, "USDJPYSP": 136.20, "GBPUSDSP": 1.2650}
DEFAULT_VOLATILITY = 0.0002
PRICE_DIGITS = {"USDJPYSP": 3}


def parse_ms(value: str | None, fallback: int) -> int:
    if not value:
        return fallback
    text = value.strip()
    if text.isdigit():
        return int(text)
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def base_price(contract: str) -> float:
    return BASE_PRICES.get(contract, 1.1000)


def price_digits(contract: str) -> int:
    return PRICE_DIGITS.get(contract, 5)


def generate_rows(
    table: str,
    contract: str,
    start_ms: int,
    count: int,
    step_ms: int,
    frequency: str,
    sides: tuple[str, ...],
    region: str,
) -> Iterator[tuple[str, dict[str, bytes]]]:
    """按真实字段口径生成 K 线。

    价格用随机游走，并保证 `LOW <= min(OPEN,CLOSE) <= max(OPEN,CLOSE) <= HIGH`——
    否则聚合类的问法（比如高低点）会得出自相矛盾的结果。
    """
    dims = metadata.parse_table_name(table)
    rng = random.Random(zlib.crc32(contract.encode("utf-8")))
    digits = price_digits(contract)
    price = base_price(contract)
    # 提到循环外：这些 getter 每次都走一遍配置读取，放内层循环会成倍放大开销
    family = config.column_family()

    for index in range(count):
        ts = start_ms + index * step_ms
        open_price = price
        close_price = max(1e-6, open_price * (1 + rng.uniform(-DEFAULT_VOLATILITY, DEFAULT_VOLATILITY)))
        high = max(open_price, close_price) * (1 + abs(rng.uniform(0, DEFAULT_VOLATILITY / 2)))
        low = min(open_price, close_price) * (1 - abs(rng.uniform(0, DEFAULT_VOLATILITY / 2)))
        price = close_price

        shared = {
            "CHANNEL": dims.get("channel") or "",
            "DATATYPESTR": dims.get("datatype") or "",
            "INSTRUMENT": dims.get("instrument") or "",
            "CONTRACTCODE": contract,
            "FREQUENCY": frequency,
            "TIME": ts,
            "SYSTIME": ts + 120,
            "STARTTIMESTAMP": ts,
            "ENDTIMESTAMP": ts + step_ms,
            "OPEN": round(open_price, digits),
            "CLOSE": round(close_price, digits),
            "HIGH": round(high, digits),
            "LOW": round(low, digits),
            "TRADEVOLUME": rng.randint(1000, 5_000_000),
            "TRADEAMT": 0,
        }
        for side in sides:
            key = rowkey_fmt.build(ts, contract, side, region=region, frequency=frequency)
            values = dict(shared, BUYSELL=side)
            yield key, {f"{family}:{k}": str(v).encode() for k, v in values.items()}


def _describe_rowkey(key: str) -> str:
    parsed = rowkey_fmt.parse(key)
    if not parsed:
        return "无法解析（与约定格式不符）"
    return (
        f"region={parsed['region']} ts={parsed['ts_ms']} "
        f"hash={parsed['symbol_hash']} freq={parsed['frequency']} side={parsed['side']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", action="append", default=None, help=f"可重复，默认 {DEFAULT_TABLE}")
    parser.add_argument("--contracts", default=",".join(DEFAULT_CONTRACTS))
    parser.add_argument("--sides", default="MID", help="逗号分隔，如 MID 或 BID,ASK,MID")
    parser.add_argument("--hours", type=float, default=72.0, help="回看小时数，默认 72")
    parser.add_argument("--start", default=None, help="起始时间（毫秒或 ISO），默认 now-hours")
    parser.add_argument("--end", default=None, help="结束时间（毫秒或 ISO），默认 now")
    parser.add_argument("--frequency", default=config.DEFAULT_FREQUENCY)
    parser.add_argument("--region", default=config.region_prefixes()[0])
    parser.add_argument("--yes", action="store_true", help="确认写入（不加则只预览）")
    args = parser.parse_args()

    tables = args.table or [DEFAULT_TABLE]
    contracts = [item.strip() for item in args.contracts.split(",") if item.strip()]
    sides = tuple(item.strip().upper() for item in args.sides.split(",") if item.strip())
    step_ms = MINUTE_MS
    end_ms = parse_ms(args.end, int(datetime.now(tz=timezone.utc).timestamp() * 1000))
    start_ms = parse_ms(args.start, end_ms - int(args.hours * 3_600_000))
    count = max(1, (end_ms - start_ms) // step_ms)

    print("=" * 62)
    print(f"目标表      : {', '.join(tables)}")
    print(f"合约        : {', '.join(contracts)}")
    print(f"买卖方向    : {', '.join(sides)}")
    print(f"时间窗      : {_iso(start_ms)} ~ {_iso(end_ms)}（{count} 根/合约/方向）")
    print(f"频率/region : {args.frequency} / {args.region}")
    total = count * len(contracts) * len(sides) * len(tables)
    print(f"预计写入    : {total} 行")
    print("=" * 62)

    for table in tables:
        sample = generate_rows(
            table, contracts[0], start_ms, 3, step_ms, args.frequency, sides, args.region
        )
        first_key, first_cells = next(sample)
        print(f"\n[{table}]")
        print(f"  rowkey 样例: {first_key}")
        print(f"  {_describe_rowkey(first_key)}")
        print(f"  字段数: {len(first_cells)}")

        if not args.yes:
            continue
        _guard_against_overwrite(table, first_key, contracts[0])
        rows = (
            row
            for contract in contracts
            for row in generate_rows(
                table, contract, start_ms, count, step_ms, args.frequency, sides, args.region
            )
        )
        expected = count * len(contracts) * len(sides)
        started = time.perf_counter()
        written = client.put_rows(
            table, rows, on_progress=lambda done: _progress(done, expected)
        )
        elapsed = time.perf_counter() - started
        print(
            f"  ✅ 已写入 {written} 行，耗时 {elapsed:.1f}s"
            f"（{written / elapsed:,.0f} 行/秒）"
        )

    if not args.yes:
        print("\n这是预览（dry-run）。确认无误后加 --yes 真正写入。")


def _guard_against_overwrite(table: str, first_key: str, contract: str) -> None:
    """写前自检：同一个 rowkey 上已有**别的合约**，说明拼法与真实数据不一致。"""
    existing = client.row_exists(table, first_key)
    if existing is None:
        return
    cell = existing.get(f"{config.column_family()}:CONTRACTCODE")
    existing_contract = cell.decode("utf-8", "replace") if isinstance(cell, bytes) else str(cell)
    if existing_contract != contract:
        raise SystemExit(
            f"中止：{table} 的 rowkey {first_key} 已存在，且 CONTRACTCODE="
            f"{existing_contract!r}（本次要写 {contract!r}）。\n"
            "这说明 rowkey 拼法（region/时间/hash/频率/方向）与表里的真实数据不一致，"
            "继续写会覆盖别人的数据。请先用 sample_rows 核对真实 rowkey 格式。"
        )
    print(f"  （该 rowkey 已存在且合约一致，属重复写入，将覆盖同值数据）")


def _progress(done: int, expected: int) -> None:
    """每批打印一次进度：上万行期间没有输出会让人以为卡死了。"""
    print(f"    写入进度 {done:,}/{expected:,}", flush=True)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
