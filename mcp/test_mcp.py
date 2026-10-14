# -*- coding: utf-8 -*-
"""
MCP 端到端回归测试

作用：按 ~/.workbuddy/mcp.json 里的**真实配置**拉起 jmfx-analysis 服务，
      走完整 MCP 协议（initialize → tools/list → tools/call），
      逐条调用 9 个工具并校验关键结果，最后打印通过/失败汇总。

用法：
    python test_mcp.py            # 用 mcp.json 里的配置
    python test_mcp.py --bare     # 不读 mcp.json，直接用当前解释器 + 脚本路径

退出码：0 = 全部通过，1 = 有失败项
"""
import argparse
import asyncio
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

MCP_JSON = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")
SERVER = r"E:\中信银行\经营分析\mcp\jmfx_mcp_server.py"
SERVER_KEY = "jmfx-analysis"

PASS, FAIL = [], []


def load_config(bare: bool):
    """返回 (command, args, env)。默认按 mcp.json 真实配置测试。"""
    if not bare and os.path.isfile(MCP_JSON):
        with open(MCP_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
        srv = cfg.get("mcpServers", {}).get(SERVER_KEY)
        if srv:
            return srv["command"], srv.get("args", [SERVER]), srv.get("env", {})
        print(f"!! mcp.json 中未找到 {SERVER_KEY}，回退到 bare 模式")
    py = sys.executable
    return py, [SERVER], {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


async def run(command, args, env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    merged = dict(os.environ)
    merged.update(env or {})
    params = StdioServerParameters(command=command, args=args, env=merged)

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            si = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            ver = getattr(init, "protocol_version", None) or getattr(init, "protocolVersion", "?")
            print(f"① 握手成功 —— 服务器: {si.name if si else '?'} | 协议版本: {ver}\n")

            tools = (await s.list_tools()).tools
            names = sorted(t.name for t in tools)
            print(f"② 工具列表 —— 共 {len(tools)} 个")
            for t in tools:
                print(f"     · {t.name}")
            print()
            check("工具数量为 9", len(tools) == 9, f"实际 {len(tools)}")
            for want in ("rank_teams_tool", "get_liability_tool", "run_readonly_sql_tool"):
                check(f"包含 {want}", want in names)

            async def call(tool, payload):
                res = await s.call_tool(tool, payload)
                return json.loads(res.content[0].text)

            print("\n③ 逐工具调用\n")

            # 1) 元数据
            o = await call("list_dimensions_tool", {})
            check("list_dimensions_tool 有返回", bool(o.get("table")),
                  f"团队 {len(o.get('table', {}).get('rows', []))} 项")

            # 2) 团队排名（全量）
            o = await call("rank_teams_tool", {"metric": "month_cum_pl"})
            n = len(o["table"]["rows"])
            check("rank_teams 全量返回 8 个团队", n == 8, f"实际 {n}")
            check("rank_teams 返回图表", (o.get("chart") or {}).get("series"),
                  f"类型 {(o.get('chart') or {}).get('series', [{}])[0].get('type')}")
            check("rank_teams 首位为贵金属", "贵金属" in o["summary"], o["summary"][:40])

            # 3) top_n 摘要措辞（之前的修复点）
            o = await call("rank_teams_tool", {"metric": "month_cum_pl", "top_n": 3})
            check("top_n=3 时摘要区分总数与展示数",
                  "8" in o["summary"] and ("前 3" in o["summary"] or "前3" in o["summary"]),
                  o["summary"][:56])

            # 4) 单团队
            o = await call("get_team_pl_tool", {"team_name": "贵金属及商品交易团队"})
            check("get_team_pl 返回该团队数据", o.get("table") and len(o["table"]["rows"]) > 0,
                  o["summary"][:40])

            # 5) 组合明细
            o = await call("get_portfolio_pl_tool", {"team_name": "债券做市交易团队"})
            n = len((o.get("table") or {}).get("rows", []))
            check("get_portfolio_pl 返回组合明细", n > 0, f"{n} 个组合")

            # 5b) 组合明细 top_n 截断时，盈亏个数仍须为**全量**口径（回归点）
            full = await call("get_portfolio_pl_tool", {"team_name": "外汇交易团队"})
            part = await call("get_portfolio_pl_tool", {"team_name": "外汇交易团队", "top_n": 3})
            check("top_n 截断只影响展示行数，不影响盈亏个数统计",
                  len(part["table"]["rows"]) == 3
                  and "共 17 个组合" in part["summary"]
                  and "盈利 6 个" in part["summary"] and "亏损 11 个" in part["summary"],
                  part["summary"][:72])
            check("组合明细摘要不以分号结尾（盈亏段落未丢失）",
                  part["summary"].rstrip().endswith("。"), part["summary"][-24:])
            check("全量与截断两种取法的盈亏个数一致",
                  full["summary"].split("其中")[-1] == part["summary"].split("其中")[-1],
                  full["summary"].split("其中")[-1][:40])

            # 6) 经营指标
            o = await call("get_indicator_tool", {})
            n = len((o.get("table") or {}).get("rows", []))
            check("get_indicator 返回 22 条指标", n == 22, f"实际 {n}")

            # 7) 非息收入构成
            o = await call("get_composition_tool", {"indicator_group": "非息收入"})
            check("get_composition 非息收入 = 230.02 亿元", "230.02" in o["summary"], o["summary"][:60])

            # 8) 负债规模
            o = await call("get_liability_tool", {})
            check("get_liability 负债合计 = 21,477.00 亿元", "21,477.00" in o["summary"],
                  o["summary"][:60])

            # 9) 营收 vs 非息
            o = await call("get_revenue_vs_noninterest_tool", {})
            check("revenue_vs_noninterest 含 239.92 与 230.02",
                  "239.92" in o["summary"] and "230.02" in o["summary"], o["summary"][:60])

            # 10) 只读 SQL 兜底（正常）
            o = await call("run_readonly_sql_tool",
                           {"sql": "SELECT team_name, month_cum_pl_wan FROM v_team_pl "
                                   "WHERE month_cum_pl_wan < 0 ORDER BY month_cum_pl_wan"})
            check("run_readonly_sql 正常查询", o["shape"] == "table" and
                  len((o.get("table") or {}).get("rows", [])) > 0, o["summary"][:40])

            # 11) 越权：写操作
            o = await call("run_readonly_sql_tool", {"sql": "DELETE FROM fact_indicator"})
            check("拒绝 DELETE", "SELECT" in o["summary"] or "仅支持" in o["summary"],
                  o["summary"][:40])

            # 12) 越权：系统表
            o = await call("run_readonly_sql_tool", {"sql": "SELECT * FROM mysql.user"})
            check("拒绝 mysql.user", "禁止" in o["summary"] or "拦截" in o["summary"] or
                  "不支持" in o["summary"], o["summary"][:40])

    print("\n" + "=" * 52)
    print(f"结果：通过 {len(PASS)} / 共 {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    print("=" * 52)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bare", action="store_true", help="忽略 mcp.json，用当前解释器直跑")
    a = ap.parse_args()

    cmd, args, env = load_config(a.bare)
    print(f"命令 : {cmd}\n脚本 : {args[0]}\nenv  : {sorted((env or {}).keys())}\n")
    if not os.path.isfile(args[0]):
        print(f"!! 找不到服务脚本：{args[0]}")
        sys.exit(1)

    try:
        asyncio.run(run(cmd, args, env))
    except Exception as e:
        print(f"\n!! 测试异常：{type(e).__name__}: {e}")
        sys.exit(1)

    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
