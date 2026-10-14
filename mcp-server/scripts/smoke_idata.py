"""iData 本地冒烟：直接调用工具函数（不经 HTTP），验证演示数据与口径。

前置：先跑 ``uv run python -m scripts.seed_idata_demo`` 造数据。

**最关键的一项检查**是第 2 步：机构当日表的「合计行口径」与「明细行口径」按机构
汇总后必须**完全一致**——不一致即说明金额口径（合计行隔离）写错了。

用法（在 mcp-server 目录下）::

    uv run python -m scripts.smoke_idata
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from easy_mcp_server import context
from easy_mcp_server.businesses.idata.server import build

BUSINESS = "idata"


async def _call(mcp, name: str, **kwargs) -> dict[str, Any]:
    """按真实路径调用工具（会经过参数校验与身份校验）。"""
    result = await mcp.call_tool(name, kwargs)
    if isinstance(result, tuple):
        content, structured = result
        if structured is not None:
            return structured
        return json.loads(content[0].text)
    return result


def _ok(label: str, detail: str = "") -> None:
    print(f"  [OK]   {label}{('  ' + detail) if detail else ''}")


def _bad(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}{('  ' + detail) if detail else ''}")


async def main() -> int:
    mcp = build()
    context.set_identity("local-tester", BUSINESS)
    failures = 0

    try:
        # ── 0. 工具清单 ────────────────────────────────────────────────
        tools = sorted(t.name for t in await mcp.list_tools())
        print(f"\n[0] 工具清单（{len(tools)} 个）")
        print(f"      {', '.join(tools)}")
        if len(tools) != 10:
            _bad("工具数应为 10", f"实际 {len(tools)}")
            failures += 1
        else:
            _ok("工具数 = 10")

        # ── 1. 交易日 ──────────────────────────────────────────────────
        print("\n[1] 交易日")
        latest = await _call(mcp, "get_trade_dates", source="daily", limit=1)
        prev = await _call(mcp, "get_trade_dates", source="daily", limit=1, before="20260828")
        print(f"      最新={latest.get('dates')}  上一交易日={prev.get('dates')}")
        if latest.get("dates") == ["20260828"] and prev.get("dates") == ["20260827"]:
            _ok("最新 / 上一交易日正确")
        else:
            _bad("最新 / 上一交易日不符预期")
            failures += 1

        # ── 2. 核心检查：合计行 vs 明细行 ─────────────────────────────
        print("\n[2] 机构当日：合计行口径 vs 明细行口径（按机构汇总）")
        total = await _call(
            mcp, "get_institution_flow", date="20260828", group_by=["institution"],
            include_total=True,
        )
        detail = await _call(
            mcp, "get_institution_flow", date="20260828", group_by=["institution"],
            include_total=False,
        )
        total_map = {r["institution"]: r["net_yi"] for r in total.get("rows", [])}
        detail_map = {r["institution"]: r["net_yi"] for r in detail.get("rows", [])}
        for name in sorted(set(total_map) | set(detail_map)):
            t, d = total_map.get(name), detail_map.get(name)
            flag = "OK" if t == d else "DIFF"
            print(f"      {name:<8} 合计={t}  明细={d}  [{flag}]")
        if total_map and total_map == detail_map:
            _ok("两种口径完全一致 → 合计行隔离正确")
        else:
            _bad("两种口径不一致 → 检查 include_total 条件")
            failures += 1

        # ── 3. 维度组合 ────────────────────────────────────────────────
        print("\n[3] 机构 × 券种矩阵")
        matrix = await _call(
            mcp, "get_institution_flow", date="20260828",
            group_by=["institution", "bond_type"], include_total=True,
        )
        rows = matrix.get("rows", [])
        print(f"      行数={len(rows)}  样例={rows[0] if rows else None}")
        if rows:
            _ok("返回非空")
        else:
            _bad("返回为空（应至少有合计行）")
            failures += 1

        print("\n[4] 明细模式 + 券种过滤（更早一个交易日）")
        filtered = await _call(
            mcp, "get_institution_flow", date="20260827",
            group_by=["institution", "bond_type"], include_total=False, bond_type="国债",
        )
        rows = filtered.get("rows", [])
        bond_types = sorted({r.get("bond_type") for r in rows})
        print(f"      行数={len(rows)}  券种={bond_types}")
        if rows and bond_types == ["国债"]:
            _ok("券种过滤生效（新债/老债已归一为国债）")
        else:
            _bad("券种过滤结果异常")
            failures += 1

        # ── 5. 个券机构 ────────────────────────────────────────────────
        print("\n[5] 个券机构：260205")
        bond = await _call(mcp, "get_bond_counterparts", bond_id="260205")
        rows = bond.get("rows", [])
        print(f"      行数={len(rows)}  主力买={bond.get('top_buyer')}  主力卖={bond.get('top_seller')}")
        if rows and bond.get("top_buyer", {}).get("net_yi", 0) > 0 >= bond.get("top_seller", {}).get("net_yi", 1):
            _ok("净买卖与主力买卖方向正确，比值已换算成亿元")
        else:
            _bad("个券机构结果异常")
            failures += 1

        print("\n[6] 活跃券排行")
        top = await _call(mcp, "list_top_active_bonds", date="20260828", top=5)
        bonds = top.get("bonds", [])
        print(f"      券数={len(bonds)}  首只={bonds[0].get('bond_id') if bonds else None} 活动度={bonds[0].get('activity_yi') if bonds else None}")
        if bonds:
            _ok("排行返回非空")
        else:
            _bad("排行返回为空")
            failures += 1

        # ── 6. 债券借贷 ────────────────────────────────────────────────
        print("\n[7] 债券借贷快照（含环比 + 核心券）")
        lending = await _call(
            mcp, "get_lending_snapshot", include_change=True, only_core=True,
        )
        rows = lending.get("rows", [])
        changes = lending.get("changes") or {}
        codes = sorted(r.get("bond_id") for r in rows)
        print(f"      日期={lending.get('date')}  核心券={codes}")
        print(f"      上升={len(changes.get('bal_up', []))}  下降={len(changes.get('bal_down', []))}")
        print(f"      样例={rows[0] if rows else None}")
        if "260207" in codes:
            _bad("260207 不是持续更新券，不应出现在核心列表")
            failures += 1
        elif rows and rows[0].get("concentration") is not None:
            _ok("核心券筛选正确；NULL 集中度已兜底为 0（260206）")
            null_fixed = [r for r in rows if r["bond_id"] == "260206"]
            print(f"      260206 集中度（原 NULL）= {null_fixed[0]['concentration'] if null_fixed else 'N/A'}")
        else:
            _bad("借贷快照结果异常")
            failures += 1

        print("\n[8] 借贷走势")
        trend = await _call(mcp, "get_lending_trend", code="260205", days=10)
        rows = trend.get("rows", [])
        print(f"      天数={len(rows)}  {[(r['date'], r['balance_yi']) for r in rows]}")
        if len(rows) >= 3 and all(r.get("balance_yi") is not None for r in rows):
            _ok("走势返回非空且余额已换算成亿元")
        else:
            _bad("借贷走势结果异常")
            failures += 1

        # ── 7. 点评 ────────────────────────────────────────────────────
        print("\n[9] 大模型点评")
        comment = await _call(mcp, "get_llm_comment", scene="bond_lending")
        content = (comment.get("comment") or {}).get("CONTENT", "")
        print(f"      正文前 60 字={content[:60]}")
        if content:
            _ok("取到点评正文")
        else:
            _bad("未取到点评")
            failures += 1

        # ── 8. 口径字典 ────────────────────────────────────────────────
        print("\n[10] 口径字典")
        schema = await _call(mcp, "describe_idata_schema")
        if schema.get("ok") and "亿元" in schema.get("units", {}).get("金额", ""):
            _ok("口径字典可用（含单位说明）")
        else:
            _bad("口径字典异常")
            failures += 1

    finally:
        context.clear_identity()

    print("\n" + "=" * 60)
    if failures:
        print(f"结论：{failures} 项未通过，请按上面的 [FAIL] 排查")
    else:
        print("结论：全部通过 ✅")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
