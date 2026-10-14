"""核对各业务的表是否存在于当前连接的库里。

内网部署、换库、改表名之后先跑这个，比手工敲 SQL 省事。
**只读**脚本，不改任何数据，也不建表。

用法::

    uv run python -m scripts.check_tables            # 核对全部业务
    uv run python -m scripts.check_tables --quiet    # 只输出小结与缺失项

退出码：全部存在为 0，有缺失为 1（便于接进部署流程）。

表名来源：各业务模块自己的 ``TABLES`` 常量（strategyqa / fxbar 走环境变量，
与运行时一致），不在这里维护第二份清单——否则改了代码忘了改脚本，
核对结果就骗人了。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from easy_mcp_server.db import describe_target, fetch_all, mysql_config
from easy_mcp_server.env import load_env


@dataclass(frozen=True)
class Scene:
    """一个待核对的场景：业务名 + 场景名 + 表名 + 库名。"""

    business: str
    scene: str
    tables: tuple[str, ...]
    schema_var: str

    @property
    def schema(self) -> str:
        """该场景生效的库名：``*_SCHEMA`` 有值用它，否则用 MYSQL_DATABASE。"""
        return os.environ.get(self.schema_var, "").strip() or str(
            mysql_config().get("database") or ""
        )


def _research_scenes() -> list[Scene]:
    """投资研究相关的五个业务包（表名取自各领域模块）。

    原先这四个（+黄金）场景同属 ``investment_research`` 一个包，现已拆为独立业务，
    各自一个 MCP 服务与 API Key。
    """
    from easy_mcp_server.businesses.gold_analysis import gold_analysis
    from easy_mcp_server.businesses.idata import idata
    from easy_mcp_server.businesses.macro_forecast import domestic_macro
    from easy_mcp_server.businesses.rates_timing import rates_timing
    from easy_mcp_server.businesses.weekly_research import weekly_research

    return [
        Scene("idata", "iData 数据分析看板", tuple(idata.TABLES.values()), "IDATA_SCHEMA"),
        Scene(
            "macro_forecast",
            "国内宏观经济指标预测",
            tuple(domestic_macro.TABLES.values()),
            "FORECAST_SCHEMA",
        ),
        Scene("rates_timing", "利率择时", tuple(rates_timing.TABLES.values()), "RATES_SCHEMA"),
        Scene(
            "weekly_research",
            "研报观点矩阵与宏观预测",
            tuple(weekly_research.TABLES.values()),
            "WEEKLY_RESEARCH_SCHEMA",
        ),
        Scene(
            "gold_analysis",
            "黄金价格多因子归因",
            tuple(gold_analysis.TABLES.values()),
            "GOLD_SCHEMA",
        ),
    ]


def _strategyqa_scene() -> Scene:
    table = os.environ.get("STRATEGY_TABLE", "fmut2_strategy_manage").strip()
    return Scene("strategyqa", "策略问数", (table,), "STRATEGY_SCHEMA")


def _fxbar_scene() -> Scene | None:
    """fxbar 不是每个环境都部署，缺失时返回 None 而不是报错。"""
    try:
        from easy_mcp_server.businesses import fxbar
    except ImportError:
        return None

    tables = tuple(getattr(fxbar, "TABLES", {}).values()) or (
        os.environ.get("FX_TABLE", "HSDC_HDS2_BAR_FX"),
    )
    return Scene("fxbar", "外汇 K 线", tables, "FX_SCHEMA")


def collect_scenes() -> list[Scene]:
    scenes = [_strategyqa_scene()]
    fxbar = _fxbar_scene()
    if fxbar is not None:
        scenes.append(fxbar)
    try:
        scenes.extend(_research_scenes())
    except ImportError as exc:  # 内网可能没带部分业务包
        print(f"[skip] 部分业务包未加载: {exc}", file=sys.stderr)
    return scenes


def existing_tables(schema: str, tables: tuple[str, ...]) -> set[str]:
    """查 information_schema 取实际存在的表名。"""
    placeholders = ",".join(["%s"] * len(tables))
    rows = fetch_all(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({placeholders})",
        (schema, *tables),
    )
    return {row["TABLE_NAME"] for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="核对各业务的表是否存在于当前库")
    parser.add_argument("--quiet", action="store_true", help="只输出缺失项与小结")
    args = parser.parse_args(argv)

    load_env()
    print(f"连接目标: {describe_target()}")

    missing: list[tuple[str, str]] = []
    total = 0

    for scene in collect_scenes():
        existed = existing_tables(scene.schema, scene.tables)
        total += len(scene.tables)

        if not args.quiet:
            print(f"\n=== {scene.business} · {scene.scene}（库: {scene.schema}）===")

        for table in scene.tables:
            if table in existed:
                if not args.quiet:
                    print(f"  OK    {table}")
            else:
                missing.append((scene.scene, table))
                print(f"  MISS  {table}   ← {scene.business} / {scene.scene}")

    found = total - len(missing)
    print(f"\n小结: {found}/{total} 张表存在")

    if missing:
        print("\n缺失清单：")
        for scene_name, table in missing:
            print(f"  - {scene_name}: {table}")
        print(
            "\n排查方向：① 表名拼写与业务库不一致（改对应模块顶部的 TABLES 常量）"
            "\n          ② 表在别的 database（配对应的 *_SCHEMA 环境变量）"
            "\n          ③ 该场景的表还没建"
        )
        return 1

    print("全部存在。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
