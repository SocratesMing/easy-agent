#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
baop 经营分析 —— 命令行调用入口（不依赖 MCP 连接器）

背景：WorkBuddy 桌面端以「严格 MCP 模式」启动 Agent 运行时，
      USER/PROJECT/LOCAL/PLUGIN 文件作用域被屏蔽 —— 即 ~/.workbuddy/mcp.json 不会生效。
      本脚本让同一套查询逻辑绕过 MCP 通道，用命令行直接调用（Skill 的兜底通道）。

用法：
    python baop_cli.py list                                       # 列出可用工具
    python baop_cli.py rank_income --metric net_opr_incom          # 输出 JSON 信封
    python baop_cli.py rank_income --metric comp_rate --top_n 3 --text
    python baop_cli.py get_income_detail --core 投资中心
    python baop_cli.py get_liability_overview --lib_cls 外币
    python baop_cli.py compare_periods --object income --n_periods 3
    python baop_cli.py run_readonly_sql --sql "SELECT CORE, SUM(NET_OPR_INCOM) FROM fpp_fin_mkt_bizline_income_sum_t1 GROUP BY CORE"

参数：
    --text    输出人类可读格式（标题 / 结论 / 表格 / 说明），默认输出 JSON 信封
    --raw     只输出 table.rows 的原始数据（便于二次处理）

退出码：0 成功，1 参数错误或执行失败
"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baop_mcp_server as M  # noqa: E402

SIGS = {
    "list_dimensions": {"dimension": "str"},
    "get_income_overview": {"period": "str", "top_n": "int"},
    "rank_income": {"metric": "str", "by": "str", "period": "str",
                    "top_n": "int", "order": "str"},
    "get_income_detail": {"core": "str", "biz_line": "str", "period": "str"},
    "get_capital_reserve": {"core": "str", "period": "str", "basis": "str"},
    "get_liability_overview": {"lib_cls": "str", "cur": "str", "period": "str", "basis": "str"},
    "rank_liability_products": {"lib_cls": "str", "metric": "str", "period": "str",
                                "top_n": "int", "order": "str"},
    "compare_periods": {"object": "str", "keys": "str", "n_periods": "int", "period_to": "str"},
    "run_readonly_sql": {"sql": "str", "max_rows": "int"},
}

DESC = {
    "list_dimensions": "可查询范围与数据字典（中心/业务线/负债大类/期次/指标）",
    "get_income_overview": "各中心收入总览（营业净收入/非息/息差，分组柱状图）",
    "rank_income": "收入指标排名（横向条形图）；by=core|biz_line",
    "get_income_detail": "某中心或某业务线的收入明细（表）",
    "get_capital_reserve": "资本公积（按中心，柱状图）；basis 选对比列",
    "get_liability_overview": "负债规模与构成（饼图+表）；lib_cls 可传「外币」「同业」",
    "rank_liability_products": "负债各业务类别排名（横向条形图）",
    "compare_periods": "多期对比与趋势（折线图）；object=income|liability|capital",
    "run_readonly_sql": "兜底：只读 SQL（白名单 + 强制 LIMIT）",
}


def coerce(kind, raw):
    if kind == "int":
        return int(raw)
    return raw


def dispatch(tool, kv):
    if tool == "list_dimensions":
        return M.list_dimensions(kv.get("dimension", "all"))
    if tool == "get_income_overview":
        return M.get_income_overview(kv.get("period") or None, kv.get("top_n") or 0)
    if tool == "rank_income":
        return M.rank_income(kv.get("metric", "net_opr_incom"), kv.get("by", "core"),
                             kv.get("period") or None, kv.get("top_n", 10), kv.get("order", "desc"))
    if tool == "get_income_detail":
        return M.get_income_detail(kv.get("core") or None, kv.get("biz_line") or None,
                                   kv.get("period") or None)
    if tool == "get_capital_reserve":
        return M.get_capital_reserve(kv.get("core") or None, kv.get("period") or None,
                                     kv.get("basis", "none"))
    if tool == "get_liability_overview":
        return M.get_liability_overview(kv.get("lib_cls", "合计"), kv.get("cur", ""),
                                        kv.get("period") or None, kv.get("basis", "none"))
    if tool == "rank_liability_products":
        return M.rank_liability_products(kv.get("lib_cls", "同业负债"),
                                         kv.get("metric", "acct_bal"),
                                         kv.get("period") or None, kv.get("top_n", 10),
                                         kv.get("order", "desc"))
    if tool == "compare_periods":
        ks = [x.strip() for x in (kv.get("keys") or "").split(",") if x.strip()]
        return M.compare_periods(kv.get("object", "income"), ks or None,
                                 kv.get("n_periods", 3), kv.get("period_to") or None)
    if tool == "run_readonly_sql":
        if not kv.get("sql"):
            raise SystemExit("run_readonly_sql 需要 --sql")
        return M.run_readonly_sql(kv["sql"], kv.get("max_rows", 200))
    raise SystemExit(f"未知工具：{tool}")


def render_text(o):
    out = [f"【{o.get('title', '')}】"]
    if o.get("unit") and o["unit"] != "—":
        out.append(f"单位：{o['unit']}" + (f"    期次：{o['stat_date']}" if o.get("stat_date") else ""))
    if o.get("result_state") and o["result_state"] != "OK":
        out.append(f"状态：{o['result_state']}")
    out.append("")
    if o.get("summary"):
        out += [o["summary"], ""]
    t = o.get("table")
    if t and t.get("rows"):
        cols = t["columns"]
        out.append(" | ".join(str(c) for c in cols))
        out.append("-" * min(90, max(20, sum(len(str(c)) + 3 for c in cols))))
        for r in t["rows"]:
            out.append(" | ".join("" if v is None else str(v) for v in r))
        out.append("")
    ch = o.get("chart")
    if ch:
        types = [s.get("type") for s in (ch.get("series") or [])]
        out.append(f"（图表：{', '.join(t for t in types if t)}，共 {len(ch.get('series') or [])} 个系列）")
        out.append("")
    if o.get("candidates"):
        for k, v in o["candidates"].items():
            out.append(f"候选 {k}：{'、'.join(str(x) for x in v)}")
    for n in o.get("notes") or []:
        out.append(f"· {n}")
    return "\n".join(out)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        print("可用工具：")
        for k in SIGS:
            print(f"  {k:<26} {DESC[k]}")
        return 0

    tool = sys.argv[1]
    if tool == "list":
        for k in SIGS:
            ps = " ".join(f"--{p}" for p in SIGS[k]) or "（无参数）"
            print(f"{k:<26} {DESC[k]}\n{'':<26} 参数：{ps}")
        return 0

    if tool not in SIGS:
        print(f"未知工具：{tool}\n运行 `python baop_cli.py list` 查看可用工具", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(prog=f"baop_cli.py {tool}", add_help=False)
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--raw", action="store_true")
    for name in SIGS[tool]:
        ap.add_argument(f"--{name}", type=str, default=None)
    args, unknown = ap.parse_known_args(sys.argv[2:])
    if unknown:
        print(f"忽略未知参数：{unknown}", file=sys.stderr)

    kv = {}
    for name, kind in SIGS[tool].items():
        v = getattr(args, name.replace("-", "_"))
        if v is not None:
            try:
                kv[name] = coerce(kind, v)
            except ValueError:
                print(f"参数 --{name} 需要 {kind} 类型，收到：{v}", file=sys.stderr)
                return 1

    try:
        obj = dispatch(tool, kv)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"执行失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if args.raw:
        print(json.dumps((obj.get("table") or {}).get("rows", []), ensure_ascii=False, indent=2))
    elif args.text:
        print(render_text(obj))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
