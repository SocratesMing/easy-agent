# -*- coding: utf-8 -*-
"""
金融市场部经营分析 —— MCP Server（只读）

用途：把 MySQL `jmfx_analysis` 库中的数据，以「业务语义化工具」的形式暴露给
     支持 MCP 的宿主（WorkBuddy / easyagent），供自然语言问答调用。

设计要点
  1. 语义化工具优先：不给模型一个裸 SQL 工具乱写，而是封装成业务问题级别的工具；
  2. 只读：默认使用只读账号，兜底 SQL 工具做关键字校验 + 表白名单 + 强制 LIMIT；
  3. 单位统一：**底层表金额已全部统一为「元」**；本服务在展示层折算——
     损益/营收类按「万元」，经营指标类按「亿元」（1 亿元 = 10000 万元）；
  4. 展示友好：每个工具直接返回「文字结论 + 表格 + ECharts option」，
     宿主拿来即可渲染，无需自己拼图表。
  5. 涨红跌绿：按国内惯例，正值红色、负值绿色。

运行
  python jmfx_mcp_server.py                     # stdio（默认，供本机宿主调用）
  python jmfx_mcp_server.py --transport sse --port 8765      # SSE（供远程宿主调用）
  python jmfx_mcp_server.py --transport streamable-http --port 8765
  python jmfx_mcp_server.py --selftest          # 不依赖 mcp 包，自检所有查询逻辑

环境变量（均有默认值）
  JM_A_HOST / JM_A_PORT / JM_A_USER / JM_A_PASSWORD / JM_A_DB
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
CFG = {
    "host": os.environ.get("JM_A_HOST", "127.0.0.1"),
    "port": int(os.environ.get("JM_A_PORT", "3306")),
    "user": os.environ.get("JM_A_USER", "jmfx_ro"),
    "password": os.environ.get("JM_A_PASSWORD", "Jmfx_RO@2026"),
    "db": os.environ.get("JM_A_DB", "jmfx_analysis"),
}

# 兜底 SQL 允许访问的表 / 视图
ALLOWED_OBJECTS = {
    "fact_portfolio_pl", "fact_indicator",
    "v_team_pl", "v_portfolio_pl", "v_total_pl",
    "v_indicator_std", "v_liability", "v_team_rank_month", "v_team_summary",
}
FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|replace|"
    r"rename|call|execute|handler|load_file|outfile|dumpfile|information_schema|mysql\.)\b",
    re.I,
)

# 配色：国内惯例 涨红跌绿
C_POS = "#C8102E"   # 正 / 上涨
C_NEG = "#1E9E6A"   # 负 / 下跌
PALETTE = ["#C8102E", "#2E5BFF", "#F5A623", "#16A085", "#8E44AD",
           "#D35400", "#0F6E56", "#7F8C8D", "#185FA5", "#993556"]

UNIT_PL = "万元"
UNIT_IND = "亿元"

METRIC_LABEL = {
    "month_cum_pl": "本月累计损益",
    "daily_pl": "当日损益",
    "wow_change": "本周五对上周五",
    "month_cum_vat": "本月累计增值税",
    "daily_vat": "当日增值税",
    "bal_end": "期末值",
    "month_cum_pl_wan": "本月累计损益",
    "daily_pl_wan": "当日损益",
}
METRIC_COL = {  # 逻辑指标 -> v_team_pl / v_portfolio_pl 中的万元列
    "month_cum_pl": "month_cum_pl_wan",
    "daily_pl": "daily_pl_wan",
    "wow_change": "wow_change_wan",
    "month_cum_vat": "month_cum_vat_wan",
    "daily_vat": "daily_vat_wan",
    "bal_end": "bal_end_wan",
}


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
def _connect():
    import pymysql
    return pymysql.connect(
        host=CFG["host"], port=CFG["port"], user=CFG["user"],
        password=CFG["password"], database=CFG["db"],
        charset="utf8mb4", autocommit=True,
        read_timeout=15, connect_timeout=5,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _norm(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _query(sql: str, args=None):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args or ())
        rows = [{k: _norm(v) for k, v in r.items()} for r in cur.fetchall()]
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, rows
    finally:
        conn.close()


def _table(cols, rows, headers=None):
    return {"columns": headers or cols, "rows": [[r[c] for c in cols] for r in rows]}


# --------------------------------------------------------------------------
# ECharts option 构造
# --------------------------------------------------------------------------
def _color_by_sign(v):
    return C_NEG if (isinstance(v, (int, float)) and v < 0) else C_POS


def _opt_hbar(title, cats, vals, unit, sign_color=True):
    """横向条形图：适合排名类问题，第一项在最上方。"""
    data = [{"value": v, "itemStyle": {"color": _color_by_sign(v)}} if sign_color
            else v for v in vals]
    return {
        "title": {"text": title, "left": "center",
                  "textStyle": {"fontSize": 14, "fontWeight": "normal", "color": "#2C2C2A"}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"},
                    "valueFormatter": "{value} " + unit},
        "grid": {"left": "3%", "right": "12%", "bottom": "3%", "top": 46, "containLabel": True},
        "xAxis": {"type": "value", "name": unit,
                  "axisLine": {"lineStyle": {"color": "#B4B2A9"}},
                  "splitLine": {"lineStyle": {"color": "#F1EFE8"}}},
        "yAxis": {"type": "category", "data": cats, "inverse": True,
                  "axisLine": {"lineStyle": {"color": "#B4B2A9"}},
                  "axisLabel": {"color": "#444441"}},
        "series": [{
            "type": "bar", "data": data, "barMaxWidth": 18,
            "label": {"show": True, "position": "right", "color": "#5F5E5A", "fontSize": 11,
                      "formatter": "{c}"},
        }],
    }


def _opt_vbar(title, cats, series, unit, sign_color=False):
    """纵向柱状图：适合对比类问题。series = [{'name':.., 'data':[..], 'color':..}]"""
    s = []
    for i, item in enumerate(series):
        color = item.get("color") or PALETTE[i % len(PALETTE)]
        if sign_color and item.get("sign"):
            data = [{"value": v, "itemStyle": {"color": _color_by_sign(v)}} for v in item["data"]]
        else:
            data = [{"value": v, "itemStyle": {"color": color}} for v in item["data"]]
        s.append({"name": item["name"], "type": "bar", "data": data,
                  "barMaxWidth": 28, "barGap": "10%"})
    return {
        "title": {"text": title, "left": "center",
                  "textStyle": {"fontSize": 14, "fontWeight": "normal", "color": "#2C2C2A"}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"bottom": 0, "textStyle": {"color": "#5F5E5A", "fontSize": 11}},
        "grid": {"left": "3%", "right": "4%", "bottom": 34, "top": 46, "containLabel": True},
        "xAxis": {"type": "category", "data": cats,
                  "axisLabel": {"color": "#444441", "interval": 0, "fontSize": 11},
                  "axisLine": {"lineStyle": {"color": "#B4B2A9"}}},
        "yAxis": {"type": "value", "name": unit,
                  "axisLine": {"lineStyle": {"color": "#B4B2A9"}},
                  "splitLine": {"lineStyle": {"color": "#F1EFE8"}}},
        "series": s,
    }


def _opt_pie(title, items, unit):
    """饼图：适合构成 / 占比类问题。items = [(name, value), ...]"""
    return {
        "title": {"text": title, "left": "center",
                  "textStyle": {"fontSize": 14, "fontWeight": "normal", "color": "#2C2C2A"}},
        "tooltip": {"trigger": "item", "valueFormatter": "{value} " + unit},
        "legend": {"bottom": 0, "textStyle": {"color": "#5F5E5A", "fontSize": 11}},
        "color": PALETTE,
        "series": [{
            "type": "pie", "radius": ["38%", "66%"], "center": ["50%", "48%"],
            "avoidLabelOverlap": True,
            "itemStyle": {"borderColor": "#fff", "borderWidth": 1},
            "label": {"show": True, "formatter": "{b}\n{d}%", "fontSize": 11, "color": "#444441"},
            "data": [{"name": n, "value": v} for n, v in items],
        }],
    }


def _envelope(title, summary, unit, table=None, chart=None, notes=None, stat_date=None,
              shape="text"):
    return {
        "shape": shape,                 # text | table | table+chart
        "title": title,
        "summary": summary,
        "unit": unit,
        "stat_date": stat_date,
        "table": table,
        "chart": chart,
        "notes": notes or [],
    }


def _fmt(v, unit=""):
    if v is None:
        return "无数据"
    if isinstance(v, float):
        return f"{v:,.2f}{unit}"
    return f"{v}{unit}"


# ==========================================================================
# 工具 1：元数据 / 可用维度
# ==========================================================================
def list_dimensions():
    """列出数据里有哪些团队、中心、报表、期次、指标大类，以及本服务提供的工具。"""
    _, teams = _query("""SELECT report_source, center_name, team_name, sort_no
                         FROM v_team_pl ORDER BY report_source, sort_no""")
    _, groups = _query("SELECT DISTINCT indicator_group FROM v_indicator_std ORDER BY indicator_group")
    _, dates = _query("SELECT DISTINCT date_end FROM fact_portfolio_pl ORDER BY date_end")
    _, idates = _query("SELECT DISTINCT stat_date FROM fact_indicator ORDER BY stat_date")

    lines = ["可用团队（部门/中心）："]
    for t in teams:
        lines.append(f"  · {t['team_name']}（{t['center_name']} / 来源：{t['report_source']}）")
    lines.append("")
    lines.append("经营指标大类：" + "、".join(g["indicator_group"] for g in groups))
    lines.append("损益数据期次：" + "、".join(str(d["date_end"]) for d in dates))
    lines.append("指标数据期次：" + "、".join(str(d["stat_date"]) for d in idates))

    return _envelope(
        title="数据可用范围",
        summary="\n".join(lines),
        unit="—",
        table={
            "columns": ["团队", "所属中心", "数据来源"],
            "rows": [[t["team_name"], t["center_name"], t["report_source"]] for t in teams],
        },
        notes=[
            "损益/营收类数据来自「非息损益表」与「外币营收表」，底层单位为元，本服务统一按万元输出。",
            "经营指标类数据来自「2026年经营指标表」，底层单位为元，本服务按亿元输出。",
            "底层表金额已全部统一为「元」，展示层的万元/亿元均为折算值。",
            "跨境服务团队当前无损益/营收数据（原表为“-”）。",
        ],
        shape="table",
    )


# ==========================================================================
# 工具 2：团队损益排名
# ==========================================================================
def rank_teams(metric="month_cum_pl", report_source=None, top_n=None, order="desc"):
    """按指定指标给各团队排名（默认本月累计损益，降序）。适合“哪个团队表现最好/最差”。"""
    col = METRIC_COL.get(metric, "month_cum_pl_wan")
    label = METRIC_LABEL.get(metric, metric)
    sql = f"""SELECT report_source, center_name, team_name, {col} AS val
              FROM v_team_pl WHERE {col} IS NOT NULL"""
    args = []
    if report_source:
        sql += " AND report_source = %s"
        args.append(report_source)
    sql += f" ORDER BY {col} {'ASC' if order == 'asc' else 'DESC'}"

    # 先取全量、再在内存里截断：这样 summary 既能给出「参与排名的团队总数」，
    # 也能说明「实际展示了多少条」，避免 top_n 生效时把“只展示 3 条”误说成“只有 3 个团队”。
    _, all_rows = _query(sql, args)
    if not all_rows:
        return _envelope("团队排名", "未查询到符合条件的数据。", UNIT_PL, notes=["请确认指标名或来源筛选是否正确。"])

    total = len(all_rows)
    rows = all_rows[:int(top_n)] if top_n else all_rows

    cats = [r["team_name"] for r in rows]
    vals = [r["val"] for r in rows]
    best, worst = rows[0], rows[-1]
    if top_n and total > len(rows):
        scope = (f"共 {total} 个团队参与排名，按{label}从"
                 f"{'高到低' if order != 'asc' else '低到高'}取前 {len(rows)} 位：")
    else:
        scope = f"共 {total} 个团队。"
    summary = (
        f"{scope}{label}最高的为「{best['team_name']}」"
        f"（{_fmt(best['val'], UNIT_PL)}），最低的为「{worst['team_name']}」"
        f"（{_fmt(worst['val'], UNIT_PL)}）。"
    )
    neg = [r for r in rows if r["val"] is not None and r["val"] < 0]
    if neg:
        summary += f"其中 {len(neg)} 个团队为负：{'、'.join(r['team_name'] for r in neg)}。"

    return _envelope(
        title=f"各团队{label}排名（{UNIT_PL}）",
        summary=summary,
        unit=UNIT_PL,
        table={
            "columns": ["排名", "团队", "所属中心", "数据来源", label + f"（{UNIT_PL}）"],
            "rows": [[i + 1, r["team_name"], r["center_name"], r["report_source"], r["val"]]
                     for i, r in enumerate(rows)],
        },
        chart=_opt_hbar(f"各团队{label}", cats, vals, UNIT_PL),
        notes=["正值红色、负值绿色（国内惯例）。",
               "金额已由「元」折算为「万元」。",
               "已剔除该指标无数据的团队（如跨境服务团队损益为空），故行数可能少于团队总数。"],
        shape="table+chart",
    )


# ==========================================================================
# 工具 3：单个团队的整体情况
# ==========================================================================
def get_team_pl(team_name, report_source=None):
    """查询某个团队的全部损益/营收指标（期末值、当日损益、本周、本月累计、增值税）。"""
    sql = """SELECT report_source, center_name, team_name, stat_date,
                    bal_end_wan, daily_pl_wan, wow_change_wan, month_cum_pl_wan,
                    daily_vat_wan, month_cum_vat_wan
             FROM v_team_pl WHERE team_name = %s"""
    args = [team_name]
    if report_source:
        sql += " AND report_source = %s"
        args.append(report_source)
    _, rows = _query(sql, args)
    if not rows:
        return _envelope(f"{team_name}", f"未查询到团队「{team_name}」的数据。", UNIT_PL,
                         notes=["可用团队名单见工具 list_dimensions。"])

    r = rows[0]
    cols = ["month_cum_pl_wan", "daily_pl_wan", "wow_change_wan", "month_cum_vat_wan", "bal_end_wan"]
    heads = ["本月累计损益", "当日损益", "本周五对上周五", "本月累计增值税", "期末值"]
    summary = (
        f"「{team_name}」（{r['center_name']}）截至 {r['stat_date']}："
        f"本月累计损益 {_fmt(r['month_cum_pl_wan'], UNIT_PL)}，"
        f"当日损益 {_fmt(r['daily_pl_wan'], UNIT_PL)}，"
        f"本周五对上周五 {_fmt(r['wow_change_wan'], UNIT_PL)}，"
        f"本月累计增值税 {_fmt(r['month_cum_vat_wan'], UNIT_PL)}。"
    )
    return _envelope(
        title=f"{team_name} 经营情况（{UNIT_PL}）",
        summary=summary,
        unit=UNIT_PL,
        stat_date=str(r["stat_date"]),
        table={"columns": ["指标"] + heads,
               "rows": [[r["team_name"]] + [r[c] for c in cols]]},
        notes=["金额已由「元」折算为「万元」。", "“本周五对上周五”为周度环比变化。"],
        shape="table",
    )


# ==========================================================================
# 工具 4：团队下的组合明细
# ==========================================================================
def get_portfolio_pl(team_name, metric="month_cum_pl", top_n=None):
    """查询某团队下各组合的损益明细，用于定位“是哪个组合贡献/拖累了业绩”。"""
    col = METRIC_COL.get(metric, "month_cum_pl_wan")
    label = METRIC_LABEL.get(metric, metric)
    sql = f"""SELECT portfolio_code, {col} AS val, bal_end_wan, daily_pl_wan, month_cum_vat_wan
              FROM v_portfolio_pl WHERE team_name = %s AND {col} IS NOT NULL
              ORDER BY {col} DESC"""
    args = [team_name]
    _, all_rows = _query(sql, args)
    if not all_rows:
        return _envelope(f"{team_name} 组合明细",
                         f"团队「{team_name}」下未查询到有 {label} 数据的组合。", UNIT_PL,
                         notes=["该团队可能只有合计行、或组合损益均为空。"])
    total = len(all_rows)
    rows = all_rows[:int(top_n)] if top_n else all_rows

    cats = [r["portfolio_code"] for r in rows]
    vals = [r["val"] for r in rows]

    # 盈亏个数必须基于**全量** all_rows 统计，不能用截断后的 rows：
    # 否则 top_n 生效时会把“前 N 名里有 3 个盈利”误说成“一共 3 个盈利”，
    # 且当截断范围恰好没覆盖到亏损组合时，亏损那段会整段丢失。
    pos = [r for r in all_rows if r["val"] is not None and r["val"] > 0]
    neg = [r for r in all_rows if r["val"] is not None and r["val"] < 0]
    zero = total - len(pos) - len(neg)

    if total > len(rows):
        head_txt = f"团队「{team_name}」共 {total} 个组合有数据，此处仅列出前 {len(rows)} 个。"
    else:
        head_txt = f"团队「{team_name}」共 {total} 个组合有数据。"
    parts = []
    if pos:
        parts.append(f"盈利 {len(pos)} 个，最大为 {pos[0]['portfolio_code']}"
                     f"（{_fmt(pos[0]['val'], UNIT_PL)}）")
    else:
        parts.append("无盈利组合")
    if neg:
        parts.append(f"亏损 {len(neg)} 个，亏损最大为 {neg[-1]['portfolio_code']}"
                     f"（{_fmt(neg[-1]['val'], UNIT_PL)}）")
    else:
        parts.append("无亏损组合")
    if zero:
        parts.append(f"盈亏为 0 的 {zero} 个")
    summary = head_txt + "其中" + "；".join(parts) + "。"

    return _envelope(
        title=f"{team_name} 各组合{label}（{UNIT_PL}）",
        summary=summary,
        unit=UNIT_PL,
        table={
            "columns": ["组合代码", label + f"（{UNIT_PL}）", "期末值", "当日损益", "本月累计增值税"],
            "rows": [[r["portfolio_code"], r["val"], r["bal_end_wan"],
                      r["daily_pl_wan"], r["month_cum_vat_wan"]] for r in rows],
        },
        chart=_opt_hbar(f"{team_name} 各组合{label}", cats, vals, UNIT_PL),
        notes=["组合代码为原表口径，可结合业务台账对照组合名称。"],
        shape="table+chart",
    )


# ==========================================================================
# 工具 5：经营指标查询
# ==========================================================================
def get_indicator(indicator_group=None, indicator_name=None, include_total=True):
    """查询经营指标（营业收入 / 非息收入 / 可供出售账户资本公积变动 / 负债规模指标），按亿元呈现。"""
    sql = """SELECT indicator_group, indicator_name, parent_name, is_total,
                    current_period_yi, week_completed_yi, mom_increment_yi,
                    last_year_end_yi, yoy_change_yi, yoy_growth_rate_text, sort_no
             FROM v_indicator_std WHERE 1=1"""
    args = []
    if indicator_group:
        sql += " AND indicator_group = %s"
        args.append(indicator_group)
    if indicator_name:
        sql += " AND indicator_name LIKE %s"
        args.append(f"%{indicator_name}%")
    if not include_total:
        sql += " AND is_total = 0"
    sql += " ORDER BY sort_no"
    _, rows = _query(sql, args)
    if not rows:
        return _envelope("经营指标", "未查询到符合条件的指标。", UNIT_IND,
                         notes=["可用大类：营业收入、非息收入、可供出售账户资本公积变动、负债规模指标。"])

    groups = sorted({r["indicator_group"] for r in rows})
    table_rows = []
    for r in rows:
        table_rows.append([
            r["indicator_group"], r["indicator_name"],
            r["current_period_yi"], r["week_completed_yi"], r["mom_increment_yi"],
            r["last_year_end_yi"], r["yoy_change_yi"], r["yoy_growth_rate_text"],
        ])
    total = [r for r in rows if r["is_total"]]
    summary = f"共查询到 {len(rows)} 条指标，涉及：{'、'.join(groups)}。"
    for t in total:
        summary += (f" {t['indicator_name']}当期末 {_fmt(t['current_period_yi'], UNIT_IND)}，"
                    f"较上年 {_fmt(t['yoy_change_yi'], UNIT_IND)}。")

    return _envelope(
        title="经营指标（亿元）",
        summary=summary,
        unit=UNIT_IND,
        table={
            "columns": ["指标大类", "指标名称", "当期末", "当周完成", "较上月增量",
                        "上年末", "比上年", "比上年增幅"],
            "rows": table_rows,
        },
        chart=_opt_hbar(
            "当期末值对比（亿元）",
            [r["indicator_name"] for r in rows if not r["is_total"]],
            [r["current_period_yi"] for r in rows if not r["is_total"]],
            UNIT_IND,
        ) if len(rows) > 1 else None,
        notes=["指标底层已统一为「元」，此处按亿元呈现（1 亿元 = 10000 万元）。",
               "“所属父级”体现指标归属（如活期存款属于同业负债）。"],
        shape="table+chart" if len(rows) > 1 else "table",
    )


# ==========================================================================
# 工具 6：构成占比（饼图）
# ==========================================================================
def get_composition(indicator_group="非息收入"):
    """查看某指标大类下各中心的构成占比，适合“非息收入主要由谁贡献”这类问题。"""
    _, rows = _query("""SELECT indicator_name, parent_name, current_period_yi
                        FROM v_indicator_std
                        WHERE indicator_group = %s AND is_total = 0
                        ORDER BY current_period_yi DESC""", [indicator_group])
    if not rows:
        return _envelope(f"{indicator_group}构成", "未查询到该指标大类的明细。", UNIT_IND,
                         notes=["可用大类：营业收入、非息收入、负债规模指标等。"])
    _, tot = _query("""SELECT current_period_yi FROM v_indicator_std
                       WHERE indicator_group = %s AND is_total = 1""", [indicator_group])
    total = tot[0]["current_period_yi"] if tot else None

    items = [(r["indicator_name"], r["current_period_yi"]) for r in rows]
    s = sum(v for _, v in items if v)
    top = items[0]
    summary = (f"{indicator_group}合计 {_fmt(total, UNIT_IND)}，"
               f"主要由「{top[0]}」贡献（{_fmt(top[1], UNIT_IND)}，"
               f"占 {top[1] / s * 100:.1f}%）。")
    summary += "各中心占比：" + "、".join(
        f"{n} {v / s * 100:.1f}%" for n, v in items if v is not None)

    return _envelope(
        title=f"{indicator_group}构成（{UNIT_IND}）",
        summary=summary,
        unit=UNIT_IND,
        table={
            "columns": ["中心", f"{indicator_group}（{UNIT_IND}）", "占比"],
            "rows": [[n, v, round(v / s * 100, 2) if v is not None else None] for n, v in items]
                     + [["合计", total, 100.0]],
        },
        chart=_opt_pie(f"{indicator_group}构成", items, UNIT_IND),
        notes=["各中心数据保留一位小数的四舍五入，子项之和可能与合计略有差异。",
               "指标底层已统一为「元」，此处按亿元呈现。"],
        shape="table+chart",
    )


# ==========================================================================
# 工具 7：负债规模
# ==========================================================================
def get_liability():
    """查询负债规模指标（本币司库、外币司库、同业负债及其明细、负债规模合计）及同比变化。"""
    _, rows = _query("""SELECT indicator_name, parent_name, current_period_wan,
                               week_completed_wan, last_year_end_wan, yoy_change_wan,
                               yoy_growth_rate_text, sort_no
                        FROM v_liability ORDER BY sort_no""")
    if not rows:
        return _envelope("负债规模", "未查询到负债规模数据。", UNIT_IND)

    # 亿元呈现（万元 / 10000）
    def yi(v):
        return round(v / 10000, 2) if v is not None else None

    items = [(r["indicator_name"], yi(r["current_period_wan"])) for r in rows
             if r["indicator_name"] != "负债规模合计"]
    total = next((r for r in rows if r["indicator_name"] == "负债规模合计"), None)

    summary = ""
    if total:
        summary = (f"负债规模合计当期末 {_fmt(yi(total['current_period_wan']), UNIT_IND)}，"
                   f"上年末 {_fmt(yi(total['last_year_end_wan']), UNIT_IND)}，"
                   f"比上年 {_fmt(yi(total['yoy_change_wan']), UNIT_IND)}"
                   f"（{total['yoy_growth_rate_text']}）。")
    detail = [r for r in rows if r["indicator_name"] in ("本币司库", "外币司库", "同业负债")]
    summary += " 主要构成：" + "、".join(
        f"{r['indicator_name']} {_fmt(yi(r['current_period_wan']), UNIT_IND)}"
        f"（{r['yoy_growth_rate_text']}）" for r in detail)

    return _envelope(
        title="负债规模指标（亿元）",
        summary=summary,
        unit=UNIT_IND,
        table={
            "columns": ["指标名称", "所属", "当期末（亿元）", "当周完成（亿元）",
                        "上年末（亿元）", "比上年（亿元）", "比上年增幅"],
            "rows": [[r["indicator_name"], r["parent_name"] or "—",
                      yi(r["current_period_wan"]), yi(r["week_completed_wan"]),
                      yi(r["last_year_end_wan"]), yi(r["yoy_change_wan"]),
                      r["yoy_growth_rate_text"]] for r in rows],
        },
        chart=_opt_vbar(
            "负债规模：当期末 vs 上年末（亿元）",
            [n for n, _ in items],
            [
                {"name": "当期末", "data": [v for _, v in items], "color": "#C8102E"},
                {"name": "上年末", "data": [yi(r["last_year_end_wan"]) for r in rows
                                            if r["indicator_name"] != "负债规模合计"],
                 "color": "#B4B2A9"},
            ],
            UNIT_IND,
        ),
        notes=["指标底层已统一为「元」，此处按亿元呈现（由视图 *_wan 列折算）。",
               "“同业负债”为小计，其下含活期存款、定期存款、票据正回购。"],
        shape="table+chart",
    )


# ==========================================================================
# 工具 8：营业收入 vs 非息收入（按中心）
# ==========================================================================
def get_revenue_vs_noninterest():
    """对比各中心的营业收入与非息收入（当期末），并给出非息收入占营业收入的比重。"""
    _, rows = _query("""SELECT indicator_name, indicator_group, current_period_yi, is_total
                        FROM v_indicator_std
                        WHERE indicator_group IN ('营业收入','非息收入')
                        ORDER BY is_total DESC, indicator_name""")
    if not rows:
        return _envelope("营业收入与非息收入对比", "未查询到数据。", UNIT_IND)

    rev = {r["indicator_name"]: r["current_period_yi"] for r in rows if r["indicator_group"] == "营业收入"}
    non = {r["indicator_name"]: r["current_period_yi"] for r in rows if r["indicator_group"] == "非息收入"}
    total_rev, total_non = rev.get("营业收入"), non.get("非息收入")

    centers = [r["indicator_name"] for r in rows
               if r["indicator_group"] == "营业收入" and not r["is_total"]]
    summary = ""
    if total_rev and total_non:
        summary = (f"当期末营业收入 {_fmt(total_rev, UNIT_IND)}，非息收入 {_fmt(total_non, UNIT_IND)}，"
                   f"非息收入占比 {total_non / total_rev * 100:.1f}%。")

    trows = []
    for c in centers:
        a, b = rev.get(c), non.get(c)
        trows.append([c, a, b, round(b / a * 100, 2) if (a and b is not None) else None])
    trows.append(["合计", total_rev, total_non,
                  round(total_non / total_rev * 100, 2) if (total_rev and total_non) else None])

    return _envelope(
        title="营业收入与非息收入对比（亿元）",
        summary=summary,
        unit=UNIT_IND,
        table={"columns": ["中心", "营业收入", "非息收入", "非息占比(%)"], "rows": trows},
        chart=_opt_vbar(
            "各中心营业收入与非息收入（亿元）",
            centers,
            [
                {"name": "营业收入", "data": [rev.get(c) for c in centers], "color": "#C8102E"},
                {"name": "非息收入", "data": [non.get(c) for c in centers], "color": "#2E5BFF"},
            ],
            UNIT_IND,
        ),
        notes=["“合计”行来自原表合计口径，因亿元四舍五入，可能与子项加总略有差异。"],
        shape="table+chart",
    )


# ==========================================================================
# 工具 9：受控只读 SQL（兜底）
# ==========================================================================
def run_readonly_sql(sql, max_rows=200):
    """兜底工具：执行一条只读 SELECT 语句（仅允许白名单表/视图，自动追加 LIMIT）。"""
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        return _envelope("SQL 查询", "SQL 为空。", "—")
    if not re.match(r"^(select|with)\b", s, re.I):
        return _envelope("SQL 查询", "仅支持 SELECT / WITH 查询。", "—",
                         notes=["本工具为只读兜底，禁止任何写操作。"])
    if ";" in s:
        return _envelope("SQL 查询", "不允许多条语句。", "—")
    bad = FORBIDDEN_SQL.search(s)
    if bad:
        return _envelope("SQL 查询", f"SQL 中包含被禁止的关键字：{bad.group(0)}", "—")

    objs = set(re.findall(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", s, re.I))
    unknown = {o.lower() for o in objs} - ALLOWED_OBJECTS
    if unknown:
        return _envelope("SQL 查询", f"仅允许访问这些对象：{', '.join(sorted(ALLOWED_OBJECTS))}",
                         "—", notes=[f"检测到未授权对象：{', '.join(sorted(unknown))}"])

    max_rows = max(1, min(int(max_rows or 200), 1000))
    if not re.search(r"\blimit\b", s, re.I):
        s = f"{s} LIMIT {max_rows}"

    try:
        cols, rows = _query(s)
    except Exception as e:  # noqa: BLE001
        return _envelope("SQL 查询", f"执行失败：{e}", "—")

    return _envelope(
        title="自定义查询结果",
        summary=f"共返回 {len(rows)} 行。",
        unit="元 / 万元 / 亿元（取决于所查对象，详见 notes）",
        table=_table(cols, rows),
        notes=["结果直接来自底层对象，单位沿用原始口径——底层表金额已统一为【元】：",
               "· fact_portfolio_pl、fact_indicator 均为【元】；",
               "· v_team_pl / v_portfolio_pl / v_total_pl / v_team_rank_month 的金额列已折算为"
               "【万元】（列名以 _wan 结尾）；",
               "· v_indicator_std 同时提供 *_wan（万元）与 *_yi（亿元）两套折算列；",
               "· v_liability 为【万元】。",
               "本工具不做图表渲染；若需要图表，请优先使用语义化工具。",
               f"最多返回 {max_rows} 行。"],
        shape="table",
    )


# ==========================================================================
# MCP 注册
# ==========================================================================
def _json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _safe(fn):
    """统一异常兜底，避免工具报错直接把宿主打挂。

    注意：必须用 functools.wraps 保留原函数签名——MCP 靠 inspect.signature 生成
    工具的 JSON Schema，若签名退化成 (*args, **kwargs)，宿主调用时会被参数校验拒绝。
    """
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return _json(fn(*a, **kw))
        except Exception as e:  # noqa: BLE001
            return _json({"shape": "text", "title": "查询失败",
                          "summary": f"工具执行异常：{type(e).__name__}: {e}",
                          "unit": "—", "table": None, "chart": None,
                          "notes": ["请检查数据库连接配置（JM_A_* 环境变量）或参数是否合法。"]})
    return wrapper


def _new_server(name: str):
    """兼容 mcp 1.x（FastMCP）与 2.x（MCPServer）两种 SDK。

    mcp 2.0 起 FastMCP 更名为 MCPServer，且 host/port 从构造参数改为 run() 参数。
    返回 (server, major_version)。
    """
    try:
        from mcp.server.mcpserver import MCPServer          # mcp >= 2
        return MCPServer(name, instructions=INSTRUCTIONS), 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP              # mcp 1.x
        return FastMCP(name, instructions=INSTRUCTIONS), 1


INSTRUCTIONS = (
    "金融市场部经营分析问答服务（只读）。数据来自 MySQL 库 jmfx_analysis，"
    "覆盖非息损益表、外币营收表、2026年经营指标表。"
    "底层表金额已全部统一为「元」；本服务在展示层折算输出："
    "损益/营收类按「万元」，经营指标类按「亿元」（1 亿元 = 10000 万元）。"
    "每个工具返回统一的 JSON 信封：shape(text|table|table+chart)、title、summary、"
    "unit、stat_date、table{columns,rows}、chart(ECharts option)、notes。"
    "请优先使用语义化工具；仅在语义化工具无法覆盖时才用 run_readonly_sql_tool。"
    "原表中的“-”已存为 NULL，请表述为“无数据”，不要当作 0。"
)


def _register_tools(mcp):
    """把全部工具注册到给定的 server 实例上（1.x / 2.x 的装饰器用法一致）。"""

    @mcp.tool()
    @_safe
    def list_dimensions_tool() -> str:
        """列出本数据服务可查询的范围：团队/中心、数据来源、期次、指标大类，以及使用说明。
        当用户问“有哪些团队”“有哪些数据”“数据截止到什么时候”时使用。"""
        return list_dimensions()

    @mcp.tool()
    @_safe
    def rank_teams_tool(metric: str = "month_cum_pl", report_source: str = "",
                        top_n: int = 0, order: str = "desc") -> str:
        """各团队业绩排名。metric 可选：month_cum_pl(本月累计损益，默认)、daily_pl(当日损益)、
        wow_change(本周五对上周五)、month_cum_vat(本月累计增值税)、bal_end(期末值)。
        report_source 可选填「非息损益表」或「外币营收表」；top_n>0 时只取前 N 名；
        order 为 desc(默认，从高到低) 或 asc。返回表格 + 横向条形图。"""
        return rank_teams(metric, report_source or None, top_n or None, order)

    @mcp.tool()
    @_safe
    def get_team_pl_tool(team_name: str, report_source: str = "") -> str:
        """查询单个团队的整体经营情况（本月累计损益、当日损益、本周环比、增值税、期末值）。
        team_name 需用全称，如「外汇交易团队」「贵金属及商品交易团队」。"""
        return get_team_pl(team_name, report_source or None)

    @mcp.tool()
    @_safe
    def get_portfolio_pl_tool(team_name: str, metric: str = "month_cum_pl",
                              top_n: int = 0) -> str:
        """查询某团队下各「组合」的损益明细，用于定位是哪个组合贡献或拖累了业绩。
        metric 同 rank_teams；top_n>0 时只取前 N 个组合。"""
        return get_portfolio_pl(team_name, metric, top_n or None)

    @mcp.tool()
    @_safe
    def get_indicator_tool(indicator_group: str = "", indicator_name: str = "",
                           include_total: bool = True) -> str:
        """查询经营指标，按亿元呈现。indicator_group 可选：营业收入、非息收入、
        可供出售账户资本公积变动、负债规模指标；indicator_name 支持模糊匹配。"""
        return get_indicator(indicator_group or None, indicator_name or None, include_total)

    @mcp.tool()
    @_safe
    def get_composition_tool(indicator_group: str = "非息收入") -> str:
        """查看某指标大类下各中心的构成占比，返回饼图。适合“XX主要由谁贡献/各中心占比”。"""
        return get_composition(indicator_group)

    @mcp.tool()
    @_safe
    def get_liability_tool() -> str:
        """查询负债规模指标：本币司库、外币司库、同业负债及其明细、负债规模合计，
        含当期末、当周完成、上年末、比上年及增幅，返回对比柱状图。"""
        return get_liability()

    @mcp.tool()
    @_safe
    def get_revenue_vs_noninterest_tool() -> str:
        """对比各中心的营业收入与非息收入（当期末），并计算非息收入占比，返回分组柱状图。"""
        return get_revenue_vs_noninterest()

    @mcp.tool()
    @_safe
    def run_readonly_sql_tool(sql: str, max_rows: int = 200) -> str:
        """兜底工具：当上述语义化工具都无法覆盖问题时，执行一条只读 SELECT 语句。
        仅允许访问白名单表/视图，自动追加 LIMIT，禁止任何写操作。
        可用对象：fact_portfolio_pl、fact_indicator、v_team_pl、v_portfolio_pl、
        v_total_pl、v_indicator_std、v_liability、v_team_rank_month、v_team_summary。"""
        return run_readonly_sql(sql, max_rows)

    return mcp


def build_server():
    """兼容入口：构造 server 并注册全部工具，返回 server 实例。"""
    server, _ = _new_server("jmfx-analysis")
    return _register_tools(server)


# ==========================================================================
# 自检（不依赖 mcp 包）
# ==========================================================================
def selftest():
    cases = [
        ("list_dimensions", lambda: list_dimensions()),
        ("rank_teams(month_cum_pl)", lambda: rank_teams("month_cum_pl")),
        ("rank_teams(daily_pl, 非息损益表)", lambda: rank_teams("daily_pl", "非息损益表")),
        ("get_team_pl(贵金属及商品交易团队)", lambda: get_team_pl("贵金属及商品交易团队")),
        ("get_team_pl(不存在的团队)", lambda: get_team_pl("不存在的团队")),
        ("get_portfolio_pl(利率做市交易团队)", lambda: get_portfolio_pl("利率做市交易团队")),
        ("get_indicator()", lambda: get_indicator()),
        ("get_composition(非息收入)", lambda: get_composition("非息收入")),
        ("get_liability()", lambda: get_liability()),
        ("get_revenue_vs_noninterest()", lambda: get_revenue_vs_noninterest()),
        ("run_readonly_sql(select)", lambda: run_readonly_sql("SELECT * FROM v_team_pl")),
        ("run_readonly_sql(drop)", lambda: run_readonly_sql("DROP TABLE fact_indicator")),
        ("run_readonly_sql(mysql.user)", lambda: run_readonly_sql("SELECT * FROM mysql.user")),
    ]
    ok = 0
    for name, fn in cases:
        try:
            r = fn()
            shape = r.get("shape")
            rows = len(r["table"]["rows"]) if r.get("table") else 0
            chart = "有图" if r.get("chart") else "无图"
            print(f"[OK]   {name:42s} shape={shape:12s} 行数={rows:<4d} {chart}")
            print(f"       摘要: {(r.get('summary') or '')[:78]}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name:42s} {type(e).__name__}: {e}")
    print(f"\n自检完成：{ok}/{len(cases)} 通过")


def main():
    ap = argparse.ArgumentParser(description="金融市场部经营分析 MCP Server（只读）")
    ap.add_argument("--transport", default="stdio",
                    choices=["stdio", "sse", "streamable-http"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--selftest", action="store_true", help="自检所有查询逻辑")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    server, major = _new_server("jmfx-analysis")
    server = _register_tools(server)
    if args.transport == "stdio":
        server.run()
    elif major >= 2:                                  # mcp 2.x：host/port 传给 run()
        server.run(args.transport, host=args.host, port=args.port)
    else:                                             # mcp 1.x：host/port 走 settings
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
