#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
jmfx 经营分析 —— 命令行调用入口（不依赖 MCP 连接器）

背景：WorkBuddy 桌面端 5.7.x 以「严格 MCP 模式」启动 Agent 运行时，
      USER/PROJECT/LOCAL/PLUGIN 文件作用域被屏蔽 —— 即 ~/.workbuddy/mcp.json 不会生效。
      本脚本让同一套查询逻辑可以绕过 MCP 通道，用命令行直接调用。

用法：
    python jmfx_cli.py list                                  # 列出可用工具
    python jmfx_cli.py rank_teams --metric month_cum_pl      # 调用工具，输出 JSON
    python jmfx_cli.py rank_teams --metric month_cum_pl --top_n 3 --text
    python jmfx_cli.py get_team_pl --team_name 外汇交易团队
    python jmfx_cli.py run_readonly_sql --sql "SELECT * FROM v_team_pl LIMIT 5"

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
import jmfx_mcp_server as M  # noqa: E402

# 工具签名：参数名 -> 类型（与 MCP 工具一致）
SIGS = {
    "list_dimensions": {},
    "rank_teams": {"metric": "str", "report_source": "str", "top_n": "int", "order": "str"},
    "get_team_pl": {"team_name": "str", "report_source": "str"},
    "get_portfolio_pl": {"team_name": "str", "metric": "str", "top_n": "int"},
    "get_indicator": {"indicator_group": "str", "indicator_name": "str", "include_total": "bool"},
    "get_composition": {"indicator_group": "str"},
    "get_liability": {},
    "get_revenue_vs_noninterest": {},
    "run_readonly_sql": {"sql": "str", "max_rows": "int"},
}

DESC = {
    "list_dimensions": "列出可查询范围（团队/中心、数据来源、期次、指标大类）",
    "rank_teams": "各团队业绩排名（表格 + 条形图）",
    "get_team_pl": "单团队整体经营情况",
    "get_portfolio_pl": "团队下各组合损益明细",
    "get_indicator": "经营指标（亿元）",
    "get_composition": "某指标大类下各中心构成占比（饼图）",
    "get_liability": "负债规模指标与同比（对比柱状图）",
    "get_revenue_vs_noninterest": "各中心营业收入 vs 非息收入（分组柱状图）",
    "run_readonly_sql": "兜底：只读 SQL（白名单 + 强制 LIMIT）",
}


def coerce(kind, raw):
    if kind == "int":
        return int(raw)
    if kind == "bool":
        return str(raw).lower() in ("1", "true", "yes", "y", "是")
    return raw


def dispatch(tool, kv):
    """把命令行参数转成核心函数调用，默认值与 MCP 工具保持一致。"""
    missing = [k for k in ()]
    if missing:
        raise SystemExit(f"缺少必填参数：{', '.join(missing)}")

    if tool == "list_dimensions":
        return M.list_dimensions()
    if tool == "rank_teams":
        return M.rank_teams(kv.get("metric", "month_cum_pl"),
                            kv.get("report_source") or None,
                            kv.get("top_n") or None,
                            kv.get("order", "desc"))
    if tool == "get_team_pl":
        if not kv.get("team_name"):
            raise SystemExit("get_team_pl 需要 --team_name")
        return M.get_team_pl(kv["team_name"], kv.get("report_source") or None)
    if tool == "get_portfolio_pl":
        if not kv.get("team_name"):
            raise SystemExit("get_portfolio_pl 需要 --team_name")
        return M.get_portfolio_pl(kv["team_name"], kv.get("metric", "month_cum_pl"),
                                  kv.get("top_n") or None)
    if tool == "get_indicator":
        return M.get_indicator(kv.get("indicator_group") or None,
                               kv.get("indicator_name") or None,
                               kv.get("include_total", True))
    if tool == "get_composition":
        return M.get_composition(kv.get("indicator_group", "非息收入"))
    if tool == "get_liability":
        return M.get_liability()
    if tool == "get_revenue_vs_noninterest":
        return M.get_revenue_vs_noninterest()
    if tool == "run_readonly_sql":
        if not kv.get("sql"):
            raise SystemExit("run_readonly_sql 需要 --sql")
        return M.run_readonly_sql(kv["sql"], kv.get("max_rows", 200))
    raise SystemExit(f"未知工具：{tool}")


def render_text(o):
    out = []
    out.append(f"【{o.get('title', '')}】")
    if o.get("unit") and o["unit"] != "—":
        out.append(f"单位：{o['unit']}" + (f"    期次：{o['stat_date']}" if o.get("stat_date") else ""))
    out.append("")
    if o.get("summary"):
        out.append(o["summary"])
        out.append("")
    t = o.get("table")
    if t and t.get("rows"):
        cols = t["columns"]
        out.append(" | ".join(str(c) for c in cols))
        out.append("-" * min(80, max(20, sum(len(str(c)) + 3 for c in cols))))
        for r in t["rows"]:
            out.append(" | ".join("" if v is None else str(v) for v in r))
        out.append("")
    ch = o.get("chart")
    if ch:
        types = [s.get("type") for s in (ch.get("series") or [])]
        out.append(f"（图表：{', '.join(t for t in types if t)}，共 {len(ch.get('series') or [])} 个系列）")
        out.append("")
    for n in o.get("notes") or []:
        out.append(f"· {n}")
    return "\n".join(out)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        print("可用工具：")
        for k in SIGS:
            ps = " ".join(f"--{p}" for p in SIGS[k]) or "（无参数）"
            print(f"  {k:<28} {DESC[k]}")
            print(f"  {'':<28} 参数：{ps}")
        return 0

    tool = sys.argv[1]
    if tool == "list":
        for k in SIGS:
            ps = " ".join(f"--{p}" for p in SIGS[k]) or "（无参数）"
            print(f"{k:<28} {DESC[k]}\n{'':<28} 参数：{ps}")
        return 0

    if tool not in SIGS:
        print(f"未知工具：{tool}\n运行 `python jmfx_cli.py list` 查看可用工具", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(prog=f"jmfx_cli.py {tool}", add_help=False)
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--raw", action="store_true")
    for name, kind in SIGS[tool].items():
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
