"""基准：对比 raw 与各降采样粒度的实际读取量。

不是测试（文件名不带 test_，pytest 不收集），手动跑：

    cd mcp-server
    uv run python tests/_bench_granularity.py            # 1 天数据，秒级
    uv run python tests/_bench_granularity.py --days 5   # 5 天
    uv run python tests/_bench_granularity.py --naive    # 额外跑一遍"读完再筛"（很慢）

`--naive` 那一行是反面对比：把 MAX_ROWS 放到全局预算后取全量明细，
既慢又会撞上 MAX_SCANNED_ROWS 截断——这正是要避免的做法。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easy_mcp_server.businesses.hbaseqa import client, config, fxspot, seed  # noqa: E402
from thrift2_stub import TABLE_DEPTH, T0, stub_server  # noqa: E402

CONTRACTS = ("EURUSDSP", "USDJPYSP", "GBPUSDSP")
MINUTE_MS = 60_000


def _measure(label: str, granularity: str, contracts=None) -> None:
    client.reset_connection()
    started = time.perf_counter()
    payload = fxspot.collect(
        [TABLE_DEPTH],
        *WINDOW,
        granularity=granularity,
        buysell="MID",
        contract_codes=list(contracts) if contracts else None,
    )
    elapsed = (time.perf_counter() - started) * 1000
    print(
        f"{label:<22}{payload['row_count']:>10,}"
        f"{payload['scanned_rows']:>12,}{elapsed:>10.0f}ms"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--naive", action="store_true", help="额外测一遍读完再筛（慢）")
    args = parser.parse_args()

    bars = args.days * 24 * 60
    global WINDOW
    with stub_server({TABLE_DEPTH: {}}) as (module, handler, port):
        os.environ["HBASEQA_THRIFT_HOST"] = "127.0.0.1"
        os.environ["HBASEQA_THRIFT_PORT"] = str(port)
        client.reset_connection()

        # 直接灌进 stub 的内存表：这里要测的是**读**性能，
        # 走 put_rows 反而会被纯 Python 的 thrift 往返拖慢几十秒
        started = time.perf_counter()
        for contract in CONTRACTS:
            for key, values in seed.generate_rows(
                TABLE_DEPTH, contract, T0, bars, MINUTE_MS, "1N", ("MID",), "00"
            ):
                handler.tables[TABLE_DEPTH][key.encode()] = dict(values)
        seeded = time.perf_counter() - started

        WINDOW = (T0, T0 + (bars - 1) * MINUTE_MS)
        print(
            f"灌数据 {bars * len(CONTRACTS):,} 行"
            f"（{args.days} 天 × {len(CONTRACTS)} 合约 × 1 分钟）"
            f" 耗时 {seeded:.1f}s"
        )
        print(
            f"MAX_ROWS={config.max_rows()}  MAX_SCANNED_ROWS={config.max_scanned_rows()}\n"
        )
        print(f"{'场景':<22}{'返回行数':>10}{'实际读取':>12}{'耗时':>10}")
        print("-" * 54)

        for granularity in ("raw", "latest", "hourly", "daily"):
            _measure(granularity, granularity)
        print("-" * 54)
        _measure("daily(指定合约)", "daily", contracts=["EURUSDSP"])

        if args.naive:
            os.environ["HBASEQA_MAX_ROWS"] = str(config.max_scanned_rows())
            print("-" * 54)
            _measure("raw(读完再筛)", "raw")
            print("  ↑ 慢，且会撞 MAX_SCANNED_ROWS 截断（静默不完整）")


if __name__ == "__main__":
    main()
