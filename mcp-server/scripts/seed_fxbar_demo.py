"""fxbar 演示数据：建外汇 K 线表并写入演示数据（本地联调用）。

为什么需要它：`HSDC_HDS2_BAR_FX` 本地没有，没有数据时所有工具都只返回空，
无法验证时间窗收敛、渠道过滤与单位换算是否正确。

**时间基准是"脚本执行时刻"**：数据生成在 `now - 25h ~ now` 之间（小时整点），
这样"缺省最近 24 小时"的窗口一定能查到数据。因此**隔天再测需要重跑本脚本**
（数据会滑出默认窗口）。

用法（在 mcp-server 目录下）::

    uv run python -m scripts.seed_fxbar_demo          # 建表 + 写入演示数据
    uv run python -m scripts.seed_fxbar_demo --drop   # 只清理数据
    uv run python -m scripts.seed_fxbar_demo --force  # 表里已有数据时强制重写
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from easy_mcp_server.businesses.fxbar import fxbar
from easy_mcp_server.db import connection

TABLE_KEY = "fxbar"

DDL = """
CREATE TABLE IF NOT EXISTS HSDC_HDS2_BAR_FX (
    ROW_KEY          VARCHAR(512) COMMENT '复合主键 ENTITY+INSTRUMENT+CONTRACT_CODE+FREQUENCY+TIME',
    START_TIMESTAMP  BIGINT       COMMENT 'K线开始时间戳（毫秒 UTC）',
    END_TIMESTAMP    BIGINT       COMMENT 'K线结束时间戳（毫秒 UTC）',
    CHANNEL          VARCHAR(24)  COMMENT '交易渠道 ECBS/Reuters/Bloomberg/内部交易系统/BEST/FXALL',
    ENTITY           VARCHAR(16)  COMMENT '机构实体代码',
    INSTRUMENT       VARCHAR(16)  COMMENT '工具类型 SPOT/SWAP/FWD',
    DATA_TYPE        VARCHAR(16)  COMMENT '数据类型 OHLC/TRADE/MID',
    CONTRACT_CODE    VARCHAR(16)  COMMENT '合约代码 如 EURUSD/USDCNY',
    FREQUENCY        VARCHAR(16)  COMMENT '频率 1M/5M/15M/1H/4H/1D',
    SIDE             VARCHAR(16)  COMMENT '方向 BID/ASK/MID',
    TIME             BIGINT       COMMENT 'K线时间戳（毫秒 UTC）',
    CLOSE            DECIMAL(32,10),
    HIGH             DECIMAL(32,10),
    LOW              DECIMAL(32,10),
    OPEN             DECIMAL(32,10),
    TRADE_VOLUME     INT          COMMENT '交易量（基础货币）',
    TRADE_AMT        INT          COMMENT '交易金额（计价货币）',
    KEY idx_fx (CONTRACT_CODE, FREQUENCY, TIME)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='外汇市场K线数据表（演示）'
"""

# (合约, 基准汇率) —— 两个 G7 货币对
CONTRACTS: tuple[tuple[str, float], ...] = (
    ("EURUSD", 1.0850),
    ("USDJPY", 150.20),
)

# 需求指定的两个渠道；额外给一个 EBS 用于验证"渠道过滤真的生效"
CHANNELS: tuple[str, ...] = ("BEST", "FXALL", "EBS")

SIDES: tuple[str, ...] = ("BID", "ASK")

FREQUENCY = "1H"
HOURS = 25  # 覆盖"缺省最近 24 小时"窗口
HOUR_MS = 3_600_000


def _price(base: float, hour_index: int, side: str) -> tuple[float, float, float, float]:
    """生成一组 OHLC（围绕基准做确定的日周期性波动，不依赖随机数）。"""
    # 用小时序号做正弦式波动，保证可复现
    swing = ((hour_index % 12) - 6) / 1000.0
    mid = base * (1 + swing)
    spread = base * 0.0002
    if side == "ASK":
        mid += spread
    o = round(mid, 6)
    c = round(mid * 1.0003, 6)
    h = round(max(o, c) + base * 0.0001, 6)
    low = round(min(o, c) - base * 0.0001, 6)
    return o, h, low, c


def _rows() -> list[tuple[Any, ...]]:
    now_ms = int(time.time() * 1000)
    # 对齐到整点，再往前推 HOURS-1 个小时
    end = (now_ms // HOUR_MS) * HOUR_MS
    start = end - (HOURS - 1) * HOUR_MS

    rows: list[tuple[Any, ...]] = []
    for hour_index in range(HOURS):
        bar_time = start + hour_index * HOUR_MS
        for contract, base in CONTRACTS:
            for channel in CHANNELS:
                for side in SIDES:
                    o, h, low, c = _price(base, hour_index, side)
                    volume = 1_000_000 + hour_index * 1_000
                    amount = int(volume * c)
                    row_key = f"E01SPOT{contract}{FREQUENCY}{bar_time}{side}"
                    rows.append(
                        (
                            row_key,
                            bar_time,
                            bar_time + HOUR_MS,
                            channel,
                            "E01",
                            "SPOT",
                            "OHLC",
                            contract,
                            FREQUENCY,
                            side,
                            bar_time,
                            c,
                            h,
                            low,
                            o,
                            volume,
                            amount,
                        )
                    )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop", action="store_true", help="只清理数据，不写入")
    parser.add_argument(
        "--force",
        action="store_true",
        help="表里已有数据时也继续（会先清空！对正式库务必不要加）",
    )
    args = parser.parse_args(argv)

    table = fxbar.table_ref()
    print(f"目标表: {table}")
    print(f"频率={FREQUENCY} 合约={[c for c, _ in CONTRACTS]} 渠道={list(CHANNELS)}")

    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(DDL)
            print("已确保表存在")

            cursor.execute(f"SELECT COUNT(*) AS c FROM {table}")
            existing = int(cursor.fetchone()["c"])
            if existing and not (args.force or args.drop):
                print(f"\n拒绝执行：{table} 里已有 {existing} 行数据。", file=sys.stderr)
                print(
                    "本脚本会先清空该表再写入演示数据。"
                    "\n确认这是测试库（不是正式库）后，加 --force 重新执行。",
                    file=sys.stderr,
                )
                return 2

            cursor.execute(f"DELETE FROM {table}")
            print(f"已清空（原有 {existing} 行）")

            if args.drop:
                print("\n--drop 已生效，仅清理不写入。")
                return 0

            rows = _rows()
            placeholders = ", ".join(["%s"] * len(rows[0]))
            cursor.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
            print(f"已写入 {len(rows)} 行 K 线")

    print("\n演示数据就绪。可用以下方式验证：")
    print("  1) 冒烟：uv run python -m scripts.smoke_fxbar")
    print("  2) 服务：uv run uvicorn easy_mcp_server.main:app --port 8174")
    print("  3) 提问：EURUSD 最近一天的一小时 K 线")
    print("\n注意：数据以“执行时刻”为基准生成，隔天需重跑（会滑出默认 24 小时窗口）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
