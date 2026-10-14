"""iData 演示数据：建四张表并写入**自洽**的演示数据（本地联调用）。

为什么需要它：`fmqt_*` 四张表不在任何环境里预置，而没有数据时所有工具都只返回
空结果，无法验证 SQL 语法、维度映射与金额口径是否正确。

**数据自洽性**是这里的关键设计：机构当日表的「合计行」严格等于「明细行之和」，
因此可以用 ``include_total=true`` 与 ``false`` 两种口径各查一次，
按机构汇总的净买入应当完全一致——这是验证金额口径没写反的最直接方式。

用法（在 mcp-server 目录下）::

    uv run python -m scripts.seed_idata_demo          # 建表 + 写入演示数据
    uv run python -m scripts.seed_idata_demo --drop   # 只清理演示数据

脚本幂等：重复执行会先清空这四张表的演示数据再重新写入。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from easy_mcp_server.businesses.idata import idata
from easy_mcp_server.db import connection

# ── 建表语句（字段与顺序照 iData数据分析看板.md） ───────────────────────

DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS fmqt_bond_exec_part_data (
        STATISTIC_REPORT_RANGE            VARCHAR(8)  NOT NULL COMMENT '交易日 YYYYMMDD',
        STATISTIC_DETAIL_NAME             VARCHAR(64) NOT NULL COMMENT '机构名称',
        STATISTIC_SUBDETAIL_NAME          VARCHAR(64) NOT NULL COMMENT '期限',
        RELATED_STATISTIC_SUBDETAIL_NAME  VARCHAR(64) NOT NULL COMMENT '券种',
        SIDE                              VARCHAR(2)  NOT NULL COMMENT '1=买入 2=卖出',
        TRADE_CASHAMT                     VARCHAR(32)          COMMENT '成交金额（元，varchar 需 CAST）',
        KEY idx_daily (STATISTIC_REPORT_RANGE, STATISTIC_DETAIL_NAME)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现券成交分机构统计（演示）'
    """,
    """
    CREATE TABLE IF NOT EXISTS fmqt_bond_trade_counterpart_analysis (
        TRANSACT_DATE                     VARCHAR(8)  NOT NULL COMMENT '交易日 YYYYMMDD',
        STATISTIC_DETAIL_ID               VARCHAR(32) NOT NULL COMMENT '债券代码',
        STATISTIC_DETAIL_NAME             VARCHAR(64)          COMMENT '债券简称',
        STATISTIC_SUBDETAIL_NAME          VARCHAR(64) NOT NULL COMMENT '机构名称',
        RELATED_STATISTIC_SUBDETAIL_NAME  VARCHAR(32) NOT NULL COMMENT '固定 全部',
        BID_SIZE                          DECIMAL(20,2)        COMMENT '净买卖金额（元）',
        TRADE_QTY_RATIO                   DECIMAL(10,4)        COMMENT '成交量占比 %',
        KEY idx_bond (TRANSACT_DATE, STATISTIC_DETAIL_ID)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个债交易对手分析（演示）'
    """,
    """
    CREATE TABLE IF NOT EXISTS fmqt_bond_lending_analysis (
        TRANSACT_DATE             VARCHAR(8)  NOT NULL COMMENT '交易日 YYYYMMDD',
        STATISTIC_DETAIL_ID       VARCHAR(32) NOT NULL COMMENT '债券代码',
        STATISTIC_DETAIL_NAME     VARCHAR(64)          COMMENT '债券简称',
        STATISTIC_AMT_TYPE        VARCHAR(8)           COMMENT '19=借贷余额',
        STATISTIC_AMT_SIDE        VARCHAR(2)           COMMENT '1=融入',
        STATISTIC_AMT_VALUE       VARCHAR(32)          COMMENT '金额（元，varchar 需 CAST）',
        BOND_CONCENTRATION        VARCHAR(32)          COMMENT '集中度 %（可能为 NULL）',
        KEY idx_lending (TRANSACT_DATE, STATISTIC_DETAIL_ID)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个债债券借贷分析（演示）'
    """,
    """
    CREATE TABLE IF NOT EXISTS fmqt_idata_llm_analysis (
        SCENE          VARCHAR(64) NOT NULL COMMENT 'institution_daily / bond_institution / bond_lending',
        SCENE_NAME     VARCHAR(64)          COMMENT '场景中文名',
        ANALYSIS_DATE  VARCHAR(8)           COMMENT '分析日期 YYYYMMDD',
        CONTENT        TEXT                 COMMENT '点评正文',
        PROMPT         TEXT                 COMMENT '使用的提示词',
        MODEL          VARCHAR(64)          COMMENT '模型名称',
        STATUS         VARCHAR(16)          COMMENT 'success / failed / pending',
        ERROR_MSG      VARCHAR(255)         COMMENT '失败原因',
        KEY idx_llm (SCENE, ANALYSIS_DATE)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大模型分析结果（演示）'
    """,
)

# 表 key（与 idata.TABLES 的键一致），统一用 table_ref() 拼引用，
# 这样配了 IDATA_SCHEMA 时才会带上库名前缀
TABLES: tuple[str, ...] = ("daily", "bond", "lending", "llm")

# ── 演示数据 ────────────────────────────────────────────────────────────

# 两个交易日：便于验证"上一交易日"与环比。
# 后一天比前一天放大约 8%，这样环比会出现明显的上升组。
DATES: tuple[tuple[str, float], ...] = (("20260827", 1.0), ("20260828", 1.08))

# (机构, 买入金额(元), 卖出金额(元)) —— 5e10 = 500 亿
INSTITUTIONS: tuple[tuple[str, int, int], ...] = (
    ("大型银行", 50_000_000_000, 30_000_000_000),
    ("证券公司", 20_000_000_000, 35_000_000_000),
    ("基金公司", 40_000_000_000, 15_000_000_000),
)

# 明细拆分（占比之和必须为 1.0，保证「明细行之和 == 合计行」）
DETAIL_SPLIT: tuple[tuple[str, str, float], ...] = (
    ("1-3年", "国债-新债", 0.30),
    ("3-5年", "国债-老债", 0.25),
    ("7-10年", "中期票据", 0.25),
    ("10年以上", "地方政府债", 0.20),
)

# (债券代码, 简称)
BONDS: tuple[tuple[str, str], ...] = (
    ("260205", "26国开05"),
    ("260206", "26农发06"),
)

# (机构, 净买卖(元), 成交量占比(%))
COUNTERPARTIES: tuple[tuple[str, int, float], ...] = (
    ("大型银行", 1_200_000_000, 18.50),
    ("证券公司", -800_000_000, 12.30),
    ("基金公司", 500_000_000, 9.80),
    ("农商行", -300_000_000, 6.40),
)

# 借贷：4 个交易日让"持续更新券"判定有意义
LENDING_DATES: tuple[str, ...] = ("20260825", "20260826", "20260827", "20260828")

# (债券代码, 简称, 借贷余额(元), 集中度(% 或 None))
LENDING_BONDS: tuple[tuple[str, str, int, float | None], ...] = (
    ("260205", "26国开05", 8_000_000_000, 24.53),
    ("260206", "26农发06", 5_000_000_000, None),  # 故意留 NULL，验证工具层兜底
    # 只在最后一天出现的券：不应进入"核心跟踪券"
    ("260207", "26进出07", 2_000_000_000, 11.20),
)

LLM_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "institution_daily",
        "机构当日",
        "20260828",
        "【机构当日点评】大型银行净买入 200 亿元居首，主要增持国债与地方政府债；"
        "证券公司转为净卖出 150 亿元，反映其做市与自营的仓位调整。",
    ),
    (
        "bond_institution",
        "个券机构",
        "20260828",
        "【个券机构点评】26国开05 当日成交活跃，大型银行为主力净买入方，"
        "证券公司为主要净卖出方，机构分歧加大。",
    ),
    (
        "bond_lending",
        "债券借贷",
        "20260828",
        "【债券借贷点评】26国开05 借贷余额继续上升，集中度 24.53% 处于高位，"
        "需关注单一机构占用的集中度风险。",
    ),
)


def _yi(amount: float) -> int:
    """亿元 → 元（四舍五入到元）。"""
    return int(round(amount * 100_000_000))


def _daily_rows() -> list[tuple[Any, ...]]:
    """机构当日：每个机构都写入「4 条明细 + 1 条合计」，且两者金额严格相等。"""
    rows: list[tuple[Any, ...]] = []
    for date, factor in DATES:
        for institution, buy, sell in INSTITUTIONS:
            # 明细行：期限与券种都是具体值
            for term, bond_type, ratio in DETAIL_SPLIT:
                rows.append(
                    (
                        date,
                        institution,
                        term,
                        bond_type,
                        "1",
                        str(_yi(buy * ratio * factor / 100_000_000)),
                    )
                )
                rows.append(
                    (
                        date,
                        institution,
                        term,
                        bond_type,
                        "2",
                        str(_yi(sell * ratio * factor / 100_000_000)),
                    )
                )
            # 合计行：期限与券种都取 '合计'
            rows.append((date, institution, "合计", "合计", "1", str(_yi(buy * factor / 100_000_000))))
            rows.append((date, institution, "合计", "合计", "2", str(_yi(sell * factor / 100_000_000))))
    return rows


def _bond_rows() -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for date, factor in DATES:
        for bond_id, bond_name in BONDS:
            for institution, net, ratio in COUNTERPARTIES:
                rows.append(
                    (
                        date,
                        bond_id,
                        bond_name,
                        institution,
                        "全部",
                        round(net * factor, 2),
                        ratio,
                    )
                )
    return rows


def _lending_rows() -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for index, date in enumerate(LENDING_DATES):
        for bond_id, bond_name, balance, concentration in LENDING_BONDS:
            # 260207 只在最后一个交易日出现，用来验证"持续更新"筛选
            if bond_id == "260207" and date != LENDING_DATES[-1]:
                continue
            # 余额逐日小幅增长，便于观察走势
            grown = balance * (1 + 0.02 * index)
            rows.append(
                (
                    date,
                    bond_id,
                    bond_name,
                    "19",
                    "1",
                    str(_yi(grown / 100_000_000)),
                    None if concentration is None else str(concentration),
                )
            )
    return rows


def _llm_rows() -> list[tuple[Any, ...]]:
    return [
        (scene, scene_name, date, content, "（演示提示词）", "demo-model", "success", None)
        for scene, scene_name, date, content in LLM_ROWS
    ]


CURATORS = (
    ("daily", _daily_rows),
    ("bond", _bond_rows),
    ("lending", _lending_rows),
    ("llm", _llm_rows),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop", action="store_true", help="只清理演示数据，不重新写入")
    parser.add_argument(
        "--force",
        action="store_true",
        help="表里已有数据时也继续（会先清空！对正式库务必不要加）",
    )
    args = parser.parse_args(argv)

    target = idata.schema_name() or "（当前连接的库）"
    print(f"目标库: {target}")
    print(f"表引用: {idata.table_ref('daily')}\n")

    with connection() as conn:
        with conn.cursor() as cursor:
            for statement in DDL:
                cursor.execute(statement)
            print(f"已确保 {len(DDL)} 张表存在")

            # 防呆：这几张表在正式环境里存的是真实业务数据，而本脚本会先清空再写入。
            # 默认宁可拒绝执行，也不能把真实数据当演示数据删掉。
            existing: dict[str, int] = {}
            for key in TABLES:
                cursor.execute(f"SELECT COUNT(*) AS c FROM {idata.table_ref(key)}")
                existing[key] = int(cursor.fetchone()["c"])
            occupied = {k: n for k, n in existing.items() if n}
            if occupied and not (args.force or args.drop):
                print("\n拒绝执行：以下表里已有数据——", file=sys.stderr)
                for key, count in occupied.items():
                    print(f"  {idata.table_ref(key)}: {count} 行", file=sys.stderr)
                print(
                    "\n本脚本会先清空这些表再写入演示数据。"
                    "\n确认这是测试库（不是正式库）后，加 --force 重新执行。",
                    file=sys.stderr,
                )
                return 2

            for key in TABLES:
                cursor.execute(f"DELETE FROM {idata.table_ref(key)}")
            print(f"已清空 {len(TABLES)} 张表的旧演示数据")

            if args.drop:
                print("\n--drop 已生效，仅清理不写入。")
                return 0

            for key, builder in CURATORS:
                rows = builder()
                if not rows:
                    continue
                table = idata.table_ref(key)
                placeholders = ", ".join(["%s"] * len(rows[0]))
                cursor.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
                print(f"  {table:<52} 写入 {len(rows)} 行")

    print("\n演示数据就绪。可用以下方式验证：")
    print("  1) 启动服务：uv run uvicorn easy_mcp_server.main:app --port 8174")
    print("  2) 调用工具：get_institution_flow（含合计/明细两种口径）")
    print("     - include_total=true  → 按机构汇总的净买入")
    print("     - include_total=false → 明细行汇总，两者应完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
