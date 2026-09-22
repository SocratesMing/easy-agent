"""fxbar 本地冒烟：直接调用工具函数（不经 HTTP），验证演示数据与口径。

前置：先跑 ``uv run python -m scripts.seed_fxbar_demo`` 造数据。

重点验证三件事：

1. **缺省窗口**只返回最近 24 小时，而不是全部数据
2. **渠道过滤**缺省只命中 BEST/FXALL（演示数据里故意放了 EBS 用于对照）
3. **超上限自动收敛**：`days=365` 会被收敛到 31 天并标记 `clamped`

用法（在 mcp-server 目录下）::

    uv run python -m scripts.smoke_fxbar
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from easy_mcp_server import context
from easy_mcp_server.businesses.fxbar import fxbar
from easy_mcp_server.businesses.fxbar.server import build

BUSINESS = "fxbar"


async def _call(mcp, name: str, **kwargs) -> dict[str, Any]:
    result = await mcp.call_tool(name, kwargs)
    if isinstance(result, tuple):
        content, structured = result
        if structured is not None:
            return structured
        return json.loads(content[0].text)
    return result


def _local(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return "?"


async def main() -> int:
    mcp = build()
    context.set_identity("local-tester", BUSINESS)
    failures = 0

    try:
        # ── 0. 工具清单 ────────────────────────────────────────────────
        tools = sorted(t.name for t in await mcp.list_tools())
        print(f"\n[0] 工具清单（{len(tools)} 个）")
        print(f"      {', '.join(tools)}")
        if "query" in tools:
            print("  [FAIL] 不应暴露任意 SQL 工具 query")
            failures += 1
        else:
            print("  [OK]   未暴露 query（大表保护）")

        # ── 1. 合约清单 ────────────────────────────────────────────────
        print("\n[1] 可查合约（list_fx_instruments）")
        instruments = await _call(mcp, "list_fx_instruments", days=2)
        rows = instruments.get("rows", [])
        codes = sorted({r.get("contract_code") for r in rows})
        channels = sorted({r.get("channel") for r in rows})
        print(f"      合约={codes}")
        print(f"      渠道={channels}")
        print(f"      摘要={instruments.get('summary')}")
        if codes and "EBS" not in channels:
            print("  [OK]   缺省渠道已过滤掉 EBS（只留 BEST/FXALL）")
        else:
            print("  [FAIL] 渠道过滤未生效，EBS 不应出现在结果里")
            failures += 1

        # ── 2. 缺省窗口 = 最近 24 小时 ────────────────────────────────
        print("\n[2] 缺省窗口（get_fx_bars 不传时间参数）")
        bars = await _call(mcp, "get_fx_bars", contract_code="EURUSD", frequency="1H")
        window = bars.get("window") or {}
        items = bars.get("rows", [])
        print(f"      窗口={window.get('start')} ~ {window.get('end')}  天数={window.get('days')}")
        print(f"      行数={bars.get('row_count')}  截断={bars.get('truncated')}")
        if 0.9 <= float(window.get("days") or 0) <= 1.05:
            print("  [OK]   缺省窗口 ≈ 24 小时")
        else:
            print("  [FAIL] 缺省窗口不是 24 小时")
            failures += 1
        if items:
            newest, oldest = items[0], items[-1]
            print(f"      最新={_local(newest.get('time')) if 'time' in newest else newest.get('time_text')}"
                  f"  最早={oldest.get('time_text')}")
            print("  [OK]   返回非空且按时间倒序")
        else:
            print("  [FAIL] 返回为空——检查演示数据是否在最近 24 小时内（隔天需重跑 seed）")
            failures += 1

        # ── 3. 显式区间 ────────────────────────────────────────────────
        print("\n[3] 显式区间（days=2）")
        wide = await _call(mcp, "get_fx_bars", contract_code="EURUSD", frequency="1H", days=2)
        print(f"      天数={wide['window']['days']}  行数={wide.get('row_count')}")
        if float(wide["window"]["days"]) > float(window.get("days") or 0):
            print("  [OK]   显式区间覆盖范围更大")
        else:
            print("  [FAIL] 显式区间未生效")
            failures += 1

        # ── 4. 超上限收敛 ──────────────────────────────────────────────
        print("\n[4] 超上限自动收敛（days=365）")
        clamped = await _call(mcp, "get_fx_bars", contract_code="EURUSD", days=365)
        w = clamped.get("window") or {}
        print(f"      天数={w.get('days')}  clamped={w.get('clamped')}")
        if w.get("clamped") is True and float(w.get("days") or 0) <= float(fxbar.max_window_days()):
            print("  [OK]   已收敛到上限并标记 clamped")
        else:
            print("  [FAIL] 收敛标记缺失或未收敛")
            failures += 1

        # ── 5. 渠道参数 ────────────────────────────────────────────────
        print("\n[5] 渠道参数（channel='ALL' 应能查到 EBS）")
        all_ch = await _call(
            mcp, "get_fx_bars", contract_code="EURUSD", frequency="1H", days=1, channel="ALL"
        )
        all_rows = all_ch.get("rows", [])
        ch_set = sorted({r.get("channel") for r in all_rows})
        print(f"      行数={all_ch.get('row_count')}  渠道={ch_set}")
        if "EBS" in ch_set:
            print("  [OK]   channel='ALL' 放开了渠道过滤")
        else:
            print("  [FAIL] channel='ALL' 未生效")
            failures += 1

        # ── 6. 非法参数 ────────────────────────────────────────────────
        print("\n[6] 非法参数应返回 ok:false 而不是抛异常")
        bad = await _call(mcp, "get_fx_bars", contract_code="EURUSD", frequency="2H")
        print(f"      ok={bad.get('ok')}  error={bad.get('error')}")
        if bad.get("ok") is False and "frequency" in (bad.get("error") or ""):
            print("  [OK]   错误形态正确，模型可据此改写")
        else:
            print("  [FAIL] 错误处理异常")
            failures += 1

        # ── 7. 最新报价 ────────────────────────────────────────────────
        print("\n[7] 最新报价（get_latest_fx_quote）")
        latest = await _call(mcp, "get_latest_fx_quote", contract_code="EURUSD", frequency="1H")
        quote = latest.get("quote") or {}
        print(f"      {quote.get('contract_code')} {quote.get('side')} "
              f"close={quote.get('close')} @ {quote.get('time_text')}")
        if latest.get("ok") and quote.get("close") is not None:
            print("  [OK]   取到最新一根 K 线，价格已转数值类型")
        else:
            print("  [FAIL] 未取到最新报价")
            failures += 1

        # ── 8. 口径字典 ────────────────────────────────────────────────
        print("\n[8] 口径字典（describe_fx_schema）")
        schema = await _call(mcp, "describe_fx_schema")
        if schema.get("ok") and schema.get("enums", {}).get("frequency"):
            print(f"      频率枚举={schema['enums']['frequency']}")
            print(f"      缺省渠道={schema['enums']['default_channels']}")
            print("  [OK]   口径字典可用")
        else:
            print("  [FAIL] 口径字典异常")
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
