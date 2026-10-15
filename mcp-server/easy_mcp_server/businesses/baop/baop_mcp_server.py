# -*- coding: utf-8 -*-
"""
金融市场部经营分析（baop 库）—— MCP Server（只读）

用途：把 MySQL `baop` 库中三张 FPP 业务表的数据，以「业务语义化工具」的形式暴露给
     支持 MCP 的宿主（WorkBuddy / easyagent），供自然语言问答调用。

数据底座
  T3  fpp_fin_mkt_bizline_income_sum_t1  营业净收入/非息/息差                      单位：万元
  T1  fpp_core_cap_rsv_t1                资本公积变动                             单位：万元
  T2  fpp_whi_lib_biz_situ_t1            负债规模                                 单位：万元

单位口径（**已锁定，勿再变更**，2026-10-14 业务方最终确认）
  三张源表底层**统一为「万元」**。工具层一律折算为**亿元**（万元 ÷ 1e4）对外呈现。
  百分比列（占比 / 成本率 / 完成率 / 序时进度 / 增速）不参与折算。

三条核心逻辑（本文件是它们的唯一实现处）
  1. 日期/期次缺省：**以库为准、不以时钟为准**。无日期 → MAX(DATA_DT)；
     对比类问题优先读「预计算列」（basis 参数是列选择器，不是时间算术）。
  2. 码值标准化：`_resolve()` 四级流水线（归一化→别名→模糊→仲裁三出口）。
     低置信度**绝不静默取第一个**，返回 candidates 由上层反问。
  3. 范围外兜底：run_readonly_sql 带 7 条护栏（单条 SELECT / 表白名单 / 强制 LIMIT /
     超时 / 结果上限 / 只读账号 / 单位标注）。

统一返回信封
  shape | title | summary | unit | stat_date | table | chart | notes
  + period_resolved | resolved | candidates | result_state
  result_state ∈ OK / ZERO / NULL / NOT_FOUND / AMBIGUOUS

运行
  python baop_mcp_server.py                                  # stdio（默认）
  python baop_mcp_server.py --transport sse --port 8766
  python baop_mcp_server.py --selftest                       # 不依赖 mcp 包自检

环境变量（均有默认值）
  BAOP_HOST / BAOP_PORT / BAOP_USER / BAOP_PASSWORD / BAOP_DB
"""

from __future__ import annotations

import argparse
import difflib
import functools
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
# 部署专属连接配置：直连 baop 只读库，不依赖 BAOP_* 环境变量
# ⚠️ 这是内网部署的实测连接值，改动前请先确认连通（可用 --selftest 自检）：
#   - host / port / user / password：指向内网 baop 只读库
#   - db 必须是 "baop"：三张 fpp_* 表建在 baop 库内。曾误配成 "mysql"（系统库），
#     导致查询报 table doesn't exist；表名都是裸表名、不带库前缀，连错库必失败。
CFG = {
    "host": "28.221.254.13",
    "port": 80,
    "user": "fmmsuser",
    "password": "Fmms@1234!",
    "db": "baop",
}

T_INCOME = "fpp_fin_mkt_bizline_income_sum_t1"   # T3，单位万元
T_CAPITAL = "fpp_core_cap_rsv_t1"                # T1，单位万元
T_LIABILITY = "fpp_whi_lib_biz_situ_t1"          # T2，单位万元

ALLOWED_OBJECTS = {T_INCOME, T_CAPITAL, T_LIABILITY}

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|replace|"
    r"rename|call|execute|handler|load_file|outfile|dumpfile|information_schema|"
    r"performance_schema|sys\.|mysql\.)\b",
    re.I,
)

# 配色：国内惯例 涨红跌绿
C_POS = "#C8102E"
C_NEG = "#1E9E6A"
PALETTE = ["#C8102E", "#2E5BFF", "#F5A623", "#16A085", "#8E44AD",
           "#D35400", "#0F6E56", "#7F8C8D", "#185FA5", "#993556"]

UNIT_YI = "亿元"
UNIT_WAN = "万元"    # 三张源表底层统一单位（2026-10-14 业务方确认）
UNIT_YUAN = "元"     # 兼容分支，当前无表使用

# --------------------------------------------------------------------------
# 维度定义：维度名 -> (来源表, 列名, 中文标签)
# --------------------------------------------------------------------------
DIM_SRC = {
    "CORE": (T_INCOME, "CORE", "中心"),
    "BIZ_LINE_DESC": (T_INCOME, "BIZ_LINE_DESC", "业务线"),
    "LIB_CLS": (T_LIABILITY, "LIB_CLS", "负债大类"),
    "PROD_NM": (T_LIABILITY, "PROD_NM", "业务类别"),
    "CUR_RATE": (T_LIABILITY, "CUR_RATE", "币种类别"),
    "CORE_CAP": (T_CAPITAL, "CORE", "中心（资本公积）"),
}
DIM_ALIAS = {           # list_dimensions 的对外名字 -> 内部维度
    "center": "CORE",
    "biz_line": "BIZ_LINE_DESC",
    "lib_cls": "LIB_CLS",
    "prod": "PROD_NM",
    "cur": "CUR_RATE",
    "period": "PERIOD",
    "metric": "METRIC",
    "meta": "META",
}

CUR_LABEL = {"01": "01（本币）", "02": "02（外币）", "合计": "合计"}

# --------------------------------------------------------------------------
# 指标字典
# --------------------------------------------------------------------------
# T3：逻辑指标 -> (列名, 中文名, 量纲类别)
#   量纲类别 money=金额（万元）；rate=百分比；vol=台数/无量纲
T3_METRIC = {
    "net_opr_incom": ("NET_OPR_INCOM", "营业净收入", "money"),
    "not_int_incom": ("NOT_INT_INCOM", "非息收入", "money"),
    "net_int": ("NET_INT", "息差", "money"),
    "plan": ("NET_OPR_INCOM_PLAN", "营业净收入-经营计划", "money"),
    "comp_rate": ("NET_OPR_INCOM_COMP_RATE", "营业净收入-计划完成率", "rate"),
    "tm_prog": ("NET_INT_TM_PROG", "息差-序时进度", "rate"),
    "curr_week": ("NET_OPR_INCOM_CURR_WEEK", "营业净收入-当周", "money"),
}

# T2：逻辑指标 -> (列名, 中文名, 量纲类别)
T2_METRIC = {
    "acct_bal": ("ACCT_BAL_CNY", "余额", "money"),
    "bal_per": ("ACCT_BAL_CNY_PER", "余额占比", "rate"),
    "avg_bal": ("YR_DAY_AVG_BAL_CNY", "年均余额", "money"),
    "avg_bal_per": ("YR_DAY_AVG_BAL_CNY_PER", "年均余额占比", "rate"),
    "cost_rate": ("AVG_COST_RATE", "成本率", "rate"),
}

# 对比基准 = 预计算列选择器（§5.4：日期逻辑从"算"变成"选"）
T1_BASIS = {
    "none": None,
    "last_day": ("BAL_RMB_VS_LAST_DAY", "较上日人民币变动"),
    "last_fri": ("COMPARE_LAST_FRI_RMB", "较上周人民币变动"),
    "last_mth": ("BAL_RMB_VS_LAST_MTH", "较上月末人民币变动"),
    "last_quar": ("BAL_RMB_VS_LAST_QUAR", "较上季末人民币变动"),
    "last_year_dt": ("BAL_RMB_VS_LAST_YEAR_DT", "较上年同期人民币变动"),
    "yr_bgn": ("COMPARE_BAL_RMB", "较年初人民币变动"),
}
T2_BASIS = {
    "none": None,
    "yr_bgn": ("ACCT_BAL_CNY_VS_YR_BGN", "余额较年初增量"),
    "last_mth": ("ACCT_BAL_CNY_VS_LAST_MTH", "余额较上月增量"),
    "last_mth_end": ("ACCT_BAL_CNY_VS_LAST_MTH_END", "余额较上月末增量"),
    "last_fri": ("ACCT_BAL_CNY_VS_LAST_FRI", "余额较上周五增量"),
    "avg_yr_bgn": ("YR_DAY_AVG_BAL_CNY_VS_YR_BGN", "年均余额较年初增量"),
    "avg_last_mth": ("YR_DAY_AVG_BAL_CNY_VS_LAST_MTH", "年均余额较上月增量"),
    "cost_yr_bgn": ("AVG_COST_RATE_VS_YR_BGN", "成本率较年初"),
    "cost_last_mth": ("AVG_COST_RATE_VS_LAST_MTH", "成本率较上月"),
    "cost_last_fri": ("AVG_COST_RATE_VS_LAST_FRI", "成本率较上周五"),
}

# ⚠️ 量纲陷阱：basis 列并非全是金额。
#   AVG_COST_RATE_VS_* 是「成本率差值」，单位为**百分点**，**绝不能** ÷1e4；
#   其余 *_VS_* 为金额（万元），需 ÷1e4 折亿元。
# 列名后缀、数值处理都必须按此集合分流，否则 1.91 个百分点会被抹成 0.00。
RATE_BASIS_COLS = {
    "AVG_COST_RATE_VS_YR_BGN",
    "AVG_COST_RATE_VS_LAST_MTH",
    "AVG_COST_RATE_VS_LAST_FRI",
    "AVG_COST_RATE_VS_LAST_MTH_END",
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
        read_timeout=8, connect_timeout=5,
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


def _query(sql: str, args=None, timeout=5):
    """执行一条只读查询。timeout 为语句级超时（秒）。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        if timeout:
            try:
                cur.execute("SET SESSION MAX_EXECUTION_TIME=%d" % int(timeout * 1000))
            except Exception:      # 旧版本 / 权限不足时忽略
                pass
        cur.execute(sql, args or ())
        rows = [{k: _norm(v) for k, v in r.items()} for r in cur.fetchall()]
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, rows
    finally:
        conn.close()


def _one(sql, args=None):
    _, rows = _query(sql, args)
    return rows[0] if rows else None


def _scalar(sql, args=None):
    r = _one(sql, args)
    if not r:
        return None
    return list(r.values())[0]


# --------------------------------------------------------------------------
# 单位换算（三张源表底层统一为「万元」，2026-10-14 业务方确认）
# --------------------------------------------------------------------------
def _to_yi(v, src_unit=UNIT_WAN):
    """折算为亿元（保留 2 位）。src_unit ∈ {万元, 元}

    三张表均为「万元」：万元 ÷ 1e4 = 亿元。「元」分支保留仅为兼容其他数据源。
    """
    if v is None:
        return None
    f = float(v)
    if src_unit == UNIT_WAN:
        return round(f / 1e4, 2)
    if src_unit == UNIT_YUAN:
        return round(f / 1e8, 2)
    return round(f, 2)


def _fmt_num(v, unit="", nd=2):
    if v is None:
        return "无数据"
    if isinstance(v, float):
        return f"{v:,.{nd}f}{unit}"
    return f"{v}{unit}"


# --------------------------------------------------------------------------
# ECharts option 构造（4 个工厂函数，改样式只需动这里）
# --------------------------------------------------------------------------
def _color_by_sign(v):
    return C_NEG if (isinstance(v, (int, float)) and v < 0) else C_POS


def _opt_hbar(title, cats, vals, unit, sign_color=True):
    """横向条形图：排名类，第一项在最上方。"""
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
            "label": {"show": True, "position": "right", "color": "#5F5E5A",
                      "fontSize": 11, "formatter": "{c}"},
        }],
    }


def _opt_vbar(title, cats, series, unit, sign_color=False):
    """纵向柱状图：对比类。series = [{'name':.., 'data':[..], 'color':..}]"""
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
    """饼图：构成 / 占比类。items = [(name, value), ...]"""
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


def _opt_line(title, cats, series, unit):
    """折线图：趋势类。series = [{'name':.., 'data':[..]}, ...]"""
    s = []
    for i, item in enumerate(series):
        s.append({"name": item["name"], "type": "line", "smooth": True,
                  "symbolSize": 6, "data": item["data"],
                  "itemStyle": {"color": item.get("color") or PALETTE[i % len(PALETTE)]},
                  "lineStyle": {"width": 2}})
    return {
        "title": {"text": title, "left": "center",
                  "textStyle": {"fontSize": 14, "fontWeight": "normal", "color": "#2C2C2A"}},
        "tooltip": {"trigger": "axis", "valueFormatter": "{value} " + unit},
        "legend": {"bottom": 0, "textStyle": {"color": "#5F5E5A", "fontSize": 11}},
        "grid": {"left": "3%", "right": "4%", "bottom": 34, "top": 46, "containLabel": True},
        "xAxis": {"type": "category", "data": cats, "boundaryGap": False,
                  "axisLabel": {"color": "#444441", "fontSize": 11},
                  "axisLine": {"lineStyle": {"color": "#B4B2A9"}}},
        "yAxis": {"type": "value", "name": unit,
                  "axisLine": {"lineStyle": {"color": "#B4B2A9"}},
                  "splitLine": {"lineStyle": {"color": "#F1EFE8"}}},
        "series": s,
    }


def _env(title, summary, unit, table=None, chart=None, notes=None, stat_date=None,
         shape="text", period_resolved=None, resolved=None, candidates=None,
         result_state="OK"):
    """统一返回信封（含新增的 4 个状态字段）。"""
    return {
        "shape": shape,
        "title": title,
        "summary": summary,
        "unit": unit,
        "stat_date": stat_date,
        "table": table,
        "chart": chart,
        "notes": notes or [],
        "period_resolved": period_resolved,
        "resolved": resolved or {},
        "candidates": candidates or {},
        "result_state": result_state,
    }


# ==========================================================================
# 核心逻辑一：日期与期次
# ==========================================================================
PERIOD_SRC = {
    "income": T_INCOME,
    "capital": T_CAPITAL,
    "liability": T_LIABILITY,
}

# 相对日期关键词 -> 解析策略
#   latest      : 最新期次
#   prev        : 上一期次
#   prev_mth_end: 上一自然月末（落在库内期次）
#   degrade     : 所问日期无数据 -> 回落最近可用期次，必须写 notes
#   boundary    : 库内无对应期次 -> 报边界
_REL_KEYWORDS = [
    (("较上月末", "上月末", "上月", "环比", "较上月", "月环比"), "prev_mth_end"),
    (("较上季末", "上季末", "季末", "季度"), "boundary"),
    (("较年初", "年初", "上年末", "比年初"), "boundary"),
    (("较上年同期", "上年同期", "去年同期", "同比"), "boundary"),
    (("近一周", "近7天", "近七天", "上周", "上周五", "昨天", "上一日", "昨日", "近一月内"), "degrade"),
    (("本月", "当期", "现在", "目前", "最新", "当前"), "latest"),
]


def _periods(table=None):
    """返回全库可用期次（升序）。table 为 None 时取三表交集。"""
    if table:
        _, rows = _query(f"SELECT DISTINCT DATA_DT AS d FROM `{table}` "
                         "WHERE DATA_DT IS NOT NULL ORDER BY DATA_DT")
        return [r["d"] for r in rows]
    sets = [set(_periods(t)) for t in PERIOD_SRC.values()]
    inter = set.intersection(*sets) if sets else set()
    return sorted(inter)


def _latest(table=None):
    ps = _periods(table)
    return ps[-1] if ps else None


def _prev(table=None, period=None):
    ps = _periods(table)
    if not ps:
        return None
    if period and period in ps:
        i = ps.index(period)
        return ps[i - 1] if i > 0 else None
    return ps[-2] if len(ps) >= 2 else None


def _resolve_period(raw, table):
    """期次锚点解析。返回 (period, notes, state)。

    - 无日期       -> MAX(DATA_DT)                             OK
    - 命中期次     -> 原值                                     OK
    - 落在区间内   -> 就近回落到 ≤ 该日的最近期次 + 说明        OK
    - 超范围       -> 报边界 + 给可用期次清单                  NOT_FOUND
    - 无该日数据   -> 回落最近一期 + 明确声明回落原因          OK
    """
    ps = _periods(table)
    if not ps:
        return None, ["该表无任何可用期次。"], "NOT_FOUND"
    latest, notes = ps[-1], []

    if raw is None or str(raw).strip() == "":
        return latest, [f"未指定期次，已按最新可用期次 {latest} 回答。"], "OK"

    s = str(raw).strip()

    # 1) 相对关键词
    for kws, mode in _REL_KEYWORDS:
        if any(k in s for k in kws):
            if mode == "latest":
                return latest, [f"已识别为「当期」，按最新可用期次 {latest} 回答。"], "OK"
            if mode == "prev":
                p = _prev(table)
                return (p, [f"已按上一期次 {p} 回答。"], "OK") if p else \
                       (None, ["库中无上一期次。"], "NOT_FOUND")
            if mode == "prev_mth_end":
                # 上一自然月末：取 ≤ (最新期的上一个月最后一天) 的最近期次
                cands = [p for p in ps if p <= _last_day_of_prev_month(latest)]
                if cands:
                    p = cands[-1]
                    return p, [f"已按「上月末」口径取最近可用期次 {p}。"], "OK"
                return None, [f"库中无「上月末」口径的数据，可用期次：{_join(ps)}"], "NOT_FOUND"
            if mode == "degrade":
                return latest, [
                    f"⚠️ 你问的「{s}」在库中暂无数据，已降级为最近可用期次 {latest} 回答。"
                ], "OK"
            if mode == "boundary":
                return None, [
                    f"库中无「{s}」对应的期次。当前可用期次：{_join(ps)}。"
                ], "NOT_FOUND"

    # 2) 显式日期
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        d = "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d in ps:
            return d, [], "OK"
        if d < ps[0] or d > ps[-1]:
            return None, [f"期次 {d} 超出数据范围。可用期次：{_join(ps)}"], "NOT_FOUND"
        p = max(x for x in ps if x <= d)
        return p, [f"期次 {d} 无数据，已就近回落到最近可用期次 {p}。"], "OK"

    # 3) 只给年月
    m = re.search(r"(\d{4})[-/年](\d{1,2})", s)
    if m:
        ym = "%04d-%02d" % (int(m.group(1)), int(m.group(2)))
        cands = [p for p in ps if str(p).startswith(ym)]
        if len(cands) == 1:
            return cands[0], [], "OK"
        return None, [f"无法确定「{s}」对应的期次。可用期次：{_join(ps)}"], "NOT_FOUND"

    return None, [f"无法解析期次「{s}」。可用期次：{_join(ps)}"], "NOT_FOUND"


def _last_day_of_prev_month(d):
    y, m, _ = (int(x) for x in str(d).split("-"))
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    nxt = date(y + (m // 12), (m % 12) + 1, 1)
    from datetime import timedelta
    return (nxt - timedelta(days=1)).isoformat()


def _join(xs):
    return "、".join(str(x) for x in xs)


# ==========================================================================
# 核心逻辑二：码值标准化（四级流水线 + 仲裁三出口）
# ==========================================================================
_DIM_CACHE: dict[str, list[str]] = {}

# 归一化时统一剥离的通用后缀
_SUFFIXES = ("业务", "中心", "板块", "类", "的")
_PUNCT = re.compile(r"[\s\-_/\\、,，。.：:；;（）()【】\[\]{}'\"]+")


def _normalize(s):
    """① 归一化：全角转半角 → 去空格标点 → 小写 → 去通用后缀。"""
    if s is None:
        return ""
    s = str(s).strip()
    s = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in s)
    s = s.replace("　", " ")
    s = _PUNCT.sub("", s)
    s = s.lower()
    for suf in _SUFFIXES:
        if len(s) > len(suf) and s.endswith(suf):
            s = s[: -len(suf)]
    return s


def _dim_values(dim):
    """从库里动态取维度的全部可用值（带缓存）。"""
    if dim in _DIM_CACHE:
        return _DIM_CACHE[dim]
    table, col, _ = DIM_SRC[dim]
    _, rows = _query(f"SELECT DISTINCT `{col}` AS v FROM `{table}` "
                     f"WHERE `{col}` IS NOT NULL AND `{col}` <> '' ORDER BY `{col}`")
    vals = [r["v"] for r in rows]
    _DIM_CACHE[dim] = vals
    return vals


# ② 别名映射（键为归一化后的用户说法）
_ALIAS = {
    "CORE": {
        "投资中心": "投资管理中心", "投管": "投资管理中心", "投资管理": "投资管理中心", "投资": "投资管理中心",
        "资金中心": "资金管理中心", "司库中心": "资金管理中心", "资金管理": "资金管理中心", "资金": "资金管理中心",
        "外币资管": "外币资产管理中心", "外币中心": "外币资产管理中心", "外币资产管理": "外币资产管理中心",
        "做市": "做市交易中心", "做市中心": "做市交易中心", "做市交易": "做市交易中心",
        "客户": "客户中心",
    },
    "LIB_CLS": {
        "本币": "本币司库", "人民币": "本币司库", "司库": "本币司库", "本币司库": "本币司库",
        "外币": "外币司库", "外汇": "外币司库", "美元": "外币司库",
        "同业": "同业负债", "同业负债": "同业负债", "同业存款": "同业负债",
        "总额": "合计", "全部": "合计", "总规模": "合计", "整体": "合计", "合计": "合计", "总计": "合计",
    },
    "CUR_RATE": {
        "本币": "01", "人民币": "01", "cny": "01", "rmb": "01", "01": "01",
        "外币": "02", "美元": "02", "usd": "02", "外汇": "02", "02": "02",
        "合计": "合计", "全部": "合计", "总计": "合计",
    },
    "BIZ_LINE_DESC": {
        "外汇交易": "外汇交易业务", "外汇": "外汇交易业务",
        "贵金属": "贵金属及商品交易业务", "黄金": "贵金属及商品交易业务", "商品": "贵金属及商品交易业务",
        "贵金属及商品交易": "贵金属及商品交易业务",
        "债券做市": "债券做市交易业务", "做市": "债券做市交易业务",
        "货币市场": "货币市场业务", "资金业务": "货币市场业务",
        "利率做市": "利率做市交易业务", "利率债": "利率债投资业务",
        "信用债": "信用债投资业务", "债券销售": "债券销售交易业务",
        "跨境做市": "跨境做市交易业务", "跨境服务": "跨境服务业务",
        "外币投资": "外币投资业务", "外币多资产": "外币多资产管理业务",
        "司库投资": "司库投资业务", "结构性衍生品": "结构性衍生品业务",
        "综合资产管理": "综合资产管理业务", "高收益资产管理": "高收益资产管理业务",
        "ficc组合": "FICC组合业务",
    },
    "PROD_NM": {
        "同业存单": "同业存单发行", "拆入": "拆入资金", "正回购": "正回购业务",
        "票据正回购": "票据正回购", "结算性存款": "其中：结算性存款",
        "活期同业存款": "人民币活期同业存款", "约期存款": "人民币约期存款",
        "定期同业存款": "人民币定期同业存款", "信银理财": "信银理财活期存款",
        "外币活期": "外币活期同业存款", "外币定期": "外币定期同业存款",
        "合计": "合计", "全部": "合计",
    },
}

_HIT_THRESHOLD = 62.0        # 模糊命中阈值
_AMBIG_GAP = 12.0            # 前两名分差小于此值 -> 视为歧义，反问


def _score(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    if a in b or b in a:
        return 70.0 + 30.0 * min(len(a), len(b)) / max(len(a), len(b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100


def _resolve(raw, dim):
    """③④ 模糊召回 + 仲裁三出口。

    返回 (规范值, 候选列表, 状态)：
      (值,  [],   "OK")         唯一命中（或 raw 为空 = 不筛选）
      (None, [c1, c2, ...], "AMBIGUOUS")  多候选，须反问
      (None, [可用值...],   "NOT_FOUND")  零命中，给出候选池
    """
    if raw is None or str(raw).strip() == "":
        return None, [], "OK"

    avail = _dim_values(dim)
    n_raw = _normalize(raw)

    # 2) 别名映射（先于模糊，命中即确定）
    alias = _ALIAS.get(dim, {})
    if n_raw in alias:
        return alias[n_raw], [], "OK"

    # 精确（归一化后）
    for v in avail:
        if _normalize(v) == n_raw:
            return v, [], "OK"
    # 原样精确
    if raw in avail:
        return raw, [], "OK"

    # 3) 模糊召回打分
    scored = sorted(((_score(n_raw, _normalize(v)), v) for v in avail),
                    key=lambda x: -x[0])
    if not scored or scored[0][0] < _HIT_THRESHOLD:
        return None, avail[:12], "NOT_FOUND"

    top = scored[0]
    second = scored[1] if len(scored) > 1 else (0.0, None)
    if second[1] and (top[0] - second[0]) < _AMBIG_GAP and second[0] >= _HIT_THRESHOLD:
        # 只把**达到阈值**的项作为候选，避免把无关项（分数极低）也列进候选池
        cands = [v for s, v in scored[:3] if s >= _HIT_THRESHOLD]
        return None, cands, "AMBIGUOUS"
    return top[1], [], "OK"


def _resolve_many(raws, dim):
    """批量标准化（keys 参数用）。"""
    out, cands, state = [], [], "OK"
    for r in raws or []:
        v, c, st = _resolve(r, dim)
        if st == "OK" and v:
            out.append(v)
        elif st != "OK":
            state = st
            cands = c
    return out, cands, state


# ==========================================================================
# 公共小工具
# ==========================================================================
def _empty_state(rows, key=None):
    """判定空值三态：给 SUM 结果为 None / 0 时区分 NULL 与 ZERO。"""
    if not rows:
        return "NOT_FOUND"
    if key is not None:
        v = rows[0].get(key)
        if v is None:
            return "NULL"
        try:
            if float(v) == 0:
                return "ZERO"
        except (TypeError, ValueError):
            pass
    return "OK"


# 2026-10-14 三张源表底层单位统一为「万元」，两条口径说明合并（函数名保留以兼容调用处）
_UNIT_NOTE_WAN = "单位口径：源表为万元，已统一折算为亿元展示（1 亿元 = 10000 万元）。"


def _unit_note_income():
    return _UNIT_NOTE_WAN


def _unit_note_cny():
    return _UNIT_NOTE_WAN


def _period_headline(period):
    return f"数据期次 {period}"


# ==========================================================================
# 工具 1：元数据 / 可用维度（一切边界类问题的唯一出口）
# ==========================================================================
def list_dimensions(dimension="all"):
    dim = (DIM_ALIAS.get(str(dimension or "all").strip().lower()) or
           str(dimension or "all").strip().upper())
    want_all = str(dimension or "all").strip().lower() in ("all", "")
    if want_all:
        dim = "ALL"

    periods = _periods()
    rows = []

    def add(label, name, values):
        rows.append([label, name, "、".join(str(v) for v in values)])

    if dim in ("ALL", "CORE"):
        add("维度", "中心（CORE）", _dim_values("CORE"))
    if dim in ("ALL", "BIZ_LINE_DESC"):
        add("维度", "业务线（BIZ_LINE_DESC）", _dim_values("BIZ_LINE_DESC"))
    if dim in ("ALL", "LIB_CLS"):
        add("维度", "负债大类（LIB_CLS）", _dim_values("LIB_CLS"))
    if dim in ("ALL", "PROD_NM"):
        add("维度", "业务类别（PROD_NM）", _dim_values("PROD_NM"))
    if dim in ("ALL", "CUR_RATE"):
        add("维度", "币种类别（CUR_RATE）",
            [CUR_LABEL.get(v, v) for v in _dim_values("CUR_RATE")])
    if dim in ("ALL", "PERIOD"):
        add("期次", "可用数据期次（DATA_DT）", periods)
    if dim in ("ALL", "METRIC"):
        add("指标", "T3 收入类指标", [f"{k}（{v[1]}）" for k, v in T3_METRIC.items()])
        add("指标", "T2 负债类指标", [f"{k}（{v[1]}）" for k, v in T2_METRIC.items()])
        add("指标", "对比基准 basis（预计算列）", list(T2_BASIS.keys()))
    if dim in ("ALL", "META"):
        rows.append(["元数据", "数据表", f"{T_INCOME} / {T_CAPITAL} / {T_LIABILITY}"])
        rows.append(["元数据", "单位口径", "三表底层已统一为「万元」；对外统一折算为亿元"])
        rows.append(["元数据", "期次性质", "日频快照（每日一期）"])
        rows.append(["元数据", "两套期次", f"损益类 {_latest(T_INCOME)}；"f"资本/负债类 {_latest(T_LIABILITY)}"])

    table = {"columns": ["类别", "名称", "可用值"], "rows": rows}

    summary = (f"{_period_headline(_latest())}（共 {len(periods)} 期）。"
               f"可查询 5 个中心、17 条业务线、4 档负债大类、12 档业务类别、3 档币种。")

    notes = [
        _unit_note_income(),
        "源数据为日频（每日一期）：未指定期次时取最新一期；所问日期无数据时会回落到最近可用期次并明确说明。",
        "零值（0）与「无数据（NULL）」是两种不同状态，不可混用。",
    ]
    return _env("可查询范围与数据字典", summary, UNIT_YI, table=table, notes=notes,
                stat_date=_latest(), shape="table", period_resolved=_latest(),
                resolved={f"dimension={k}": v for k, v in DIM_ALIAS.items()})


# ==========================================================================
# 工具 2：收入总览（T3，按中心）
# ==========================================================================
def get_income_overview(period=None, top_n=0):
    t = T_INCOME
    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("收入总览", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    table=None, result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    _, rows = _query(
        f"SELECT CORE, SUM(NET_OPR_INCOM) AS a, SUM(NOT_INT_INCOM) AS b, SUM(NET_INT) AS c "
        f"FROM `{t}` WHERE DATA_DT=%s GROUP BY CORE ORDER BY a DESC", (per,))
    if not rows:
        return _env("收入总览", f"{_period_headline(per)} 无数据。", UNIT_YI,
                    notes=pnotes, result_state="NOT_FOUND",
                    candidates={"可用期次": _periods(t)})

    data = []
    raw = {"a": 0.0, "b": 0.0, "c": 0.0}
    for r in rows:
        a = _to_yi(r["a"], UNIT_WAN)
        b = _to_yi(r["b"], UNIT_WAN)
        c = _to_yi(r["c"], UNIT_WAN)
        for k, src in (("a", "a"), ("b", "b"), ("c", "c")):
            raw[k] += float(r[src] or 0)
        data.append({"core": r["CORE"], "opr": a, "noti": b, "neti": c,
                     "share": round(b / a * 100, 2) if a else None})

    # 合计按「原始值先汇总、再折算」计算，避免逐行四舍五入后的累积偏差
    total_opr = _to_yi(raw["a"], UNIT_WAN)
    total_noti = _to_yi(raw["b"], UNIT_WAN)
    total_neti = _to_yi(raw["c"], UNIT_WAN)

    show = data[:top_n] if top_n and top_n > 0 else data
    table = {"columns": ["中心", "营业净收入（亿元）", "非息收入（亿元）",
                         "息差（亿元）", "非息占营业比"],
             "rows": [[d["core"], d["opr"], d["noti"], d["neti"],
                       f"{d['share']}%" if d["share"] is not None else "无数据"]
                      for d in show]}
    table["rows"].append(["合计", total_opr, total_noti, total_neti,
                          f"{round(total_noti / total_opr * 100, 2)}%" if total_opr else "无数据"])

    chart = _opt_vbar(f"各中心收入对比（{per}）", [d["core"] for d in show],
                      [{"name": "营业净收入", "data": [d["opr"] for d in show], "color": "#C8102E"},
                       {"name": "非息收入", "data": [d["noti"] for d in show], "color": "#2E5BFF"},
                       {"name": "息差", "data": [d["neti"] for d in show], "color": "#F5A623"}],
                      UNIT_YI)

    top = data[0]
    shown_txt = f"（此处仅展示前 {len(show)} 个中心）" if len(show) < len(data) else ""
    summary = (f"{_period_headline(per)}：全部门营业净收入合计 {total_opr:,.2f} 亿元，"
               f"非息收入 {total_noti:,.2f} 亿元，息差 {total_neti:,.2f} 亿元。"
               f"共 {len(data)} 个中心有数据{shown_txt}。"
               f"营业净收入最高的是「{top['core']}」（{top['opr']:,.2f} 亿元）。")

    notes = [_unit_note_income()] + pnotes
    notes.append("「合计」为各中心同名列直接汇总，非由息差+非息推算。")
    return _env(f"各中心收入总览（{UNIT_YI}）", summary, UNIT_YI, table=table, chart=chart,
                notes=notes, stat_date=per, shape="table+chart", period_resolved=per)


# ==========================================================================
# 工具 3：收入排名（T3，覆盖面最广）
# ==========================================================================
def rank_income(metric="net_opr_incom", by="core", period=None, top_n=10, order="desc"):
    t = T_INCOME
    mk = str(metric or "net_opr_incom").strip().lower()
    if mk not in T3_METRIC:
        return _env("收入排名", f"未知指标「{metric}」。", UNIT_YI,
                    notes=["可用指标：" + "、".join(T3_METRIC.keys())],
                    result_state="NOT_FOUND", candidates={"metric": list(T3_METRIC.keys())})
    col, label, kind = T3_METRIC[mk]

    bk = str(by or "core").strip().lower()
    if bk not in ("core", "biz_line"):
        return _env("收入排名", f"未知分组「{by}」。", UNIT_YI,
                    notes=["by 只接受 core（按中心）或 biz_line（按业务线）。"],
                    result_state="NOT_FOUND", candidates={"by": ["core", "biz_line"]})
    group_col = "CORE" if bk == "core" else "BIZ_LINE_DESC"

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("收入排名", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    dirn = "DESC" if str(order or "desc").lower() != "asc" else "ASC"
    agg = "SUM" if kind == "money" else "AVG"
    _, rows = _query(
        f"SELECT `{group_col}` AS k, {agg}(`{col}`) AS v FROM `{t}` "
        f"WHERE DATA_DT=%s GROUP BY `{group_col}` ORDER BY v {dirn}", (per,))
    if not rows:
        return _env("收入排名", f"{_period_headline(per)} 无数据。", UNIT_YI,
                    notes=pnotes, result_state="NOT_FOUND",
                    candidates={"可用期次": _periods(t)})

    unit = UNIT_YI if kind == "money" else "%"
    items = []
    for r in rows:
        v = _to_yi(r["v"], UNIT_WAN) if kind == "money" else round(float(r["v"] or 0), 2)
        items.append((r["k"], v))

    total_n = len(items)                      # ⚠️ 全量口径，绝不能用 top_n 当统计口径
    show = items[:top_n] if top_n and top_n > 0 else items
    disp = "中心" if bk == "core" else "业务线"

    table = {"columns": [disp, f"{label}（{unit}）"],
             "rows": [[k, v] for k, v in show]}
    chart = _opt_hbar(f"各{disp}{label}排名（{unit}）", [k for k, _ in show],
                      [v for _, v in show], unit, sign_color=(kind == "money"))

    top = items[0]
    shown_txt = f"，此处仅展示前 {len(show)} 个" if len(show) < total_n else ""
    gap_unit = "亿元" if kind == "money" else "个百分点"
    negs = [(k, v) for k, v in items if v is not None and v < 0]

    if dirn == "DESC":
        summary = (f"{_period_headline(per)}：共 {total_n} 个{disp}有数据{shown_txt}。"
                   f"最高为「{top[0]}」（{_fmt_num(top[1], unit)}）。")
        if total_n > 1:
            summary += (f"次高为「{items[1][0]}」（{_fmt_num(items[1][1], unit)}），"
                        f"领先 {_fmt_num(round(top[1] - items[1][1], 2), gap_unit)}。")
        if negs:
            summary += (f"⚠️ 其中 {len(negs)} 个为**负值**（最低「{negs[-1][0]}」"
                        f"（{_fmt_num(negs[-1][1], unit)}））。")
        elif total_n > 1:
            summary += f"末位为「{items[-1][0]}」（{_fmt_num(items[-1][1], unit)}）。"
    else:
        summary = (f"{_period_headline(per)}：共 {total_n} 个{disp}有数据{shown_txt}。"
                   f"最低为「{top[0]}」（{_fmt_num(top[1], unit)}）。")
        if total_n > 1:
            summary += (f"次低为「{items[1][0]}」（{_fmt_num(items[1][1], unit)}），"
                        f"最高为「{items[-1][0]}」（{_fmt_num(items[-1][1], unit)}）。")
        if negs:
            summary += f"⚠️ 其中 {len(negs)} 个为**负值**。"

    notes = ([_unit_note_income()] if kind == "money"
             else ["完成率/序时进度为百分比，非金额，不做单位折算。"]) + pnotes
    notes.append(f"统计口径基于全量 {total_n} 个{disp}，展示条数 {len(show)} 条，两者不同。")
    return _env(f"各{disp}{label}排名（{unit}）", summary, unit, table=table, chart=chart,
                notes=notes, stat_date=per, shape="table+chart", period_resolved=per)


# ==========================================================================
# 工具 4：收入明细（T3，锁定中心或业务线）
# ==========================================================================
def get_income_detail(core=None, biz_line=None, period=None, metric=None):
    t = T_INCOME
    core_v, core_c, core_st = _resolve(core, "CORE")
    biz_v, biz_c, biz_st = _resolve(biz_line, "BIZ_LINE_DESC")

    resolved = {}
    if core_st == "OK" and core_v:
        resolved["CORE"] = core_v
    if biz_st == "OK" and biz_v:
        resolved["BIZ_LINE_DESC"] = biz_v

    # 仲裁三出口：歧义 / 零命中直接返回，不给数据（逼上层反问）
    for st, c, dim, raw in ((core_st, core_c, "CORE", core),
                            (biz_st, biz_c, "BIZ_LINE_DESC", biz_line)):
        if st == "AMBIGUOUS":
            return _env("收入明细", f"「{raw}」在{dim}下匹配到多个候选，请确认具体是哪一个。",
                        UNIT_YI, notes=["未取数，请先澄清维度。"], table=None,
                        result_state="AMBIGUOUS", candidates={dim: c})
        if st == "NOT_FOUND":
            return _env("收入明细", f"库中无「{raw}」这个{dim}。",
                        UNIT_YI, notes=["未取数，请从候选清单中选择。"], table=None,
                        result_state="NOT_FOUND",
                        candidates={dim: _dim_values(dim)[:12]})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("收入明细", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    where, args = ["DATA_DT=%s"], [per]
    if core_v:
        where.append("CORE=%s"); args.append(core_v)
    if biz_v:
        where.append("BIZ_LINE_DESC=%s"); args.append(biz_v)
    w = " AND ".join(where)

    # 都没锁定时，退化为中心级汇总（避免一次吐出 85 行）
    if not core_v and not biz_v:
        _, rows = _query(
            f"SELECT CORE AS k, SUM(NET_OPR_INCOM) AS a, SUM(NOT_INT_INCOM) AS b, "
            f"SUM(NET_INT) AS c, SUM(NET_OPR_INCOM_PLAN) AS p, "
            f"SUM(NET_OPR_INCOM)/NULLIF(SUM(NET_OPR_INCOM_PLAN),0)*100 AS cr "
            f"FROM `{t}` WHERE {w} GROUP BY CORE ORDER BY a DESC", args)
        cols = ["中心/业务线", "营业净收入（亿元）", "非息收入（亿元）", "息差（亿元）",
                "经营计划（亿元）", "完成率"]
        tb = [[r["k"], _to_yi(r["a"], UNIT_WAN), _to_yi(r["b"], UNIT_WAN),
               _to_yi(r["c"], UNIT_WAN), _to_yi(r["p"], UNIT_WAN),
               f"{round(float(r['cr']), 2)}%" if r["cr"] is not None else "无数据"]
              for r in rows]
        headline = "本次未指定中心或业务线，已按中心级汇总返回"
    else:
        _, rows = _query(
            f"SELECT CORE, BIZ_LINE_DESC, NET_OPR_INCOM, NOT_INT_INCOM, NET_INT, "
            f"NET_OPR_INCOM_PLAN, NET_OPR_INCOM_COMP_RATE, NOT_INT_INCOM_COMP_RATE, "
            f"NOT_INT_INCOM_TM_PROG FROM `{t}` WHERE {w} "
            f"ORDER BY NET_OPR_INCOM DESC", args)
        cols = ["中心", "业务线", "营业净收入（亿元）", "非息收入（亿元）", "息差（亿元）",
                "经营计划（亿元）", "营业完成率", "非息完成率", "非息序时进度"]
        tb = [[r["CORE"], r["BIZ_LINE_DESC"], _to_yi(r["NET_OPR_INCOM"], UNIT_WAN),
               _to_yi(r["NOT_INT_INCOM"], UNIT_WAN), _to_yi(r["NET_INT"], UNIT_WAN),
               _to_yi(r["NET_OPR_INCOM_PLAN"], UNIT_WAN),
               f"{round(float(r['NET_OPR_INCOM_COMP_RATE']), 2)}%"
               if r["NET_OPR_INCOM_COMP_RATE"] is not None else "无数据",
               f"{round(float(r['NOT_INT_INCOM_COMP_RATE']), 2)}%"
               if r["NOT_INT_INCOM_COMP_RATE"] is not None else "无数据",
               f"{round(float(r['NOT_INT_INCOM_TM_PROG']), 2)}%"
               if r["NOT_INT_INCOM_TM_PROG"] is not None else "无数据"]
              for r in rows]
        headline = ""

    if not rows:
        return _env("收入明细", f"{_period_headline(per)} 下未匹配到任何记录。", UNIT_YI,
                    notes=pnotes, result_state="NOT_FOUND")

    scope = "、".join(f"{k}={v}" for k, v in resolved.items()) or "全部中心"
    total_opr = round(sum(r[2] for r in tb), 2)
    table = {"columns": cols, "rows": tb}
    summary = (f"{_period_headline(per)}，范围【{scope}】：共 {len(tb)} 条记录，"
               f"营业净收入合计 {total_opr:,.2f} 亿元。"
               + (f"（{headline}）" if headline else ""))

    notes = [_unit_note_income()] + pnotes
    notes.append("非息完成率 = 非息收入 ÷ 非息分解目标；序时进度 = 时间进度基准，两者含义不同。")
    return _env("收入明细", summary, UNIT_YI, table=table, notes=notes, stat_date=per,
                shape="table", period_resolved=per, resolved=resolved)


# ==========================================================================
# 工具 5：资本公积（T1）
# ==========================================================================
def get_capital_reserve(core=None, period=None, basis="none"):
    t = T_CAPITAL
    core_v, core_c, core_st = _resolve(core, "CORE")
    if core_st == "AMBIGUOUS":
        return _env("资本公积", f"「{core}」匹配到多个中心，请确认。", UNIT_YI,
                    table=None, result_state="AMBIGUOUS", candidates={"CORE": core_c})
    if core_st == "NOT_FOUND":
        return _env("资本公积", f"库中无「{core}」这个中心。", UNIT_YI, table=None,
                    result_state="NOT_FOUND", candidates={"CORE": _dim_values("CORE")})

    bk = str(basis or "none").strip().lower()
    if bk not in T1_BASIS:
        return _env("资本公积", f"未知对比基准「{basis}」。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"basis": list(T1_BASIS.keys())})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("资本公积", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    bcol, blabel = T1_BASIS[bk] or (None, None)
    sel = "CORE, BAL_RMB, BAL_FOREGIN, BAL_FOR_SELL" + (f", `{bcol}` AS bv" if bcol else "")
    where, args = ["DATA_DT=%s"], [per]
    if core_v:
        where.append("CORE=%s"); args.append(core_v)
    _, rows = _query(f"SELECT {sel} FROM `{t}` WHERE {' AND '.join(where)} "
                     f"ORDER BY BAL_RMB DESC", args)
    if not rows:
        return _env("资本公积", f"{_period_headline(per)} 下无匹配记录。", UNIT_YI,
                    notes=pnotes, result_state="NOT_FOUND")

    cols = ["中心", "人民币（亿元）", "外币（亿元）", "可供出售账户资产余额（亿元）"]
    if bcol:
        cols.append(f"{blabel}（亿元）")
    tb = []
    for r in rows:
        line = [r["CORE"], _to_yi(r["BAL_RMB"], UNIT_WAN), _to_yi(r["BAL_FOREGIN"], UNIT_WAN),
                _to_yi(r["BAL_FOR_SELL"], UNIT_WAN)]
        if bcol:
            line.append(_to_yi(r.get("bv"), UNIT_WAN))
        tb.append(line)

    chart = _opt_vbar(f"各中心资本公积（{per}）", [r[0] for r in tb],
                      [{"name": "人民币", "data": [r[1] for r in tb], "color": "#C8102E"},
                       {"name": "外币", "data": [r[2] for r in tb], "color": "#2E5BFF"}],
                      UNIT_YI)

    tot = round(sum(r[1] for r in tb), 2)
    top = max(tb, key=lambda x: x[1] or 0)
    summary = (f"{_period_headline(per)}：人民币资本公积合计 {tot:,.2f} 亿元，"
               f"共 {len(tb)} 个中心。最高为「{top[0]}」（{top[1]:,.2f} 亿元）。"
               + (f" 对比口径：{blabel}。" if bcol else ""))

    notes = [_unit_note_cny()] + pnotes
    notes.append("资本公积为可供出售账户口径，可为负值；负值表示浮亏，属正常业务现象。")
    if bcol:
        notes.append(f"「{blabel}」为源表**预计算列**，直接读取，未做两期相减（口径与报表一致）。")
    return _env(f"资本公积（{UNIT_YI}）", summary, UNIT_YI, table={"columns": cols, "rows": tb},
                chart=chart, notes=notes, stat_date=per, shape="table+chart",
                period_resolved=per, resolved=({"CORE": core_v} if core_v else {}))


# ==========================================================================
# 工具 6：负债总览（T2，零值语义主战场）
# ==========================================================================
def get_liability_overview(lib_cls="合计", cur="", period=None, basis="none"):
    t = T_LIABILITY
    lib_v, lib_c, lib_st = _resolve(lib_cls, "LIB_CLS")
    # 「合计/全部/空」= 不筛选币种；否则按 CUR_RATE 筛选
    cur_raw = None if str(cur or "").strip() in ("", "合计", "全部", "总计") else cur
    cur_v, cur_c, cur_st = _resolve(cur_raw, "CUR_RATE")

    for st, c, dim, raw in ((lib_st, lib_c, "LIB_CLS", lib_cls),
                            (cur_st, cur_c, "CUR_RATE", cur_raw)):
        if st == "AMBIGUOUS":
            return _env("负债总览", f"「{raw}」在 {dim} 下匹配到多个候选，请确认。", UNIT_YI,
                        table=None, result_state="AMBIGUOUS", candidates={dim: c})
        if st == "NOT_FOUND":
            return _env("负债总览", f"库中无「{raw}」这个 {dim}。", UNIT_YI, table=None,
                        result_state="NOT_FOUND", candidates={dim: _dim_values(dim)})

    if lib_v is None:
        lib_v = "合计"

    bk = str(basis or "none").strip().lower()
    if bk not in T2_BASIS:
        return _env("负债总览", f"未知对比基准「{basis}」。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"basis": list(T2_BASIS.keys())})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("负债总览", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    bcol, blabel = T2_BASIS[bk] or (None, None)

    if lib_v == "合计":
        # 构成：同业负债 / 本币司库 / 外币司库（读 PROD_NM='合计' 的汇总行）
        sel = ("LIB_CLS, PROD_NM, ACCT_BAL_CNY, ACCT_BAL_CNY_PER, YR_DAY_AVG_BAL_CNY, "
               "AVG_COST_RATE" + (f", `{bcol}` AS bv" if bcol else ""))
        _, rows = _query(
            f"SELECT {sel} FROM `{t}` WHERE DATA_DT=%s AND PROD_NM='合计' AND LIB_CLS<>'合计' "
            f"ORDER BY ACCT_BAL_CNY DESC", (per,))
        total = _scalar(f"SELECT ACCT_BAL_CNY FROM `{t}` WHERE DATA_DT=%s AND LIB_CLS='合计' "
                        f"AND PROD_NM='合计'", (per,))
        is_composition = True
    else:
        sel = ("LIB_CLS, PROD_NM, CUR_RATE, ACCT_BAL_CNY, ACCT_BAL_CNY_PER, "
               "YR_DAY_AVG_BAL_CNY, AVG_COST_RATE" + (f", `{bcol}` AS bv" if bcol else ""))
        where, args = ["DATA_DT=%s", "LIB_CLS=%s"], [per, lib_v]
        if cur_v:
            where.append("CUR_RATE=%s"); args.append(cur_v)
        _, rows = _query(
            f"SELECT {sel} FROM `{t}` WHERE {' AND '.join(where)} "
            f"ORDER BY ACCT_BAL_CNY DESC", args)
        # 有明细行时排除汇总行（本币司库/外币司库只有汇总行，需保留）
        if len(rows) > 1:
            rows = [r for r in rows if r["PROD_NM"] != "合计"]
        # 该类余额取源表汇总行，不从明细加总（明细含「其中」子项，加总会重复计）
        total = _scalar(f"SELECT ACCT_BAL_CNY FROM `{t}` WHERE DATA_DT=%s AND LIB_CLS=%s "
                        f"AND PROD_NM='合计'", (per, lib_v))
        is_composition = False

    if not rows:
        return _env("负债总览", f"{_period_headline(per)}：负债大类「{lib_v}」"
                    + (f"、币种「{cur_v}」" if cur_v else "")
                    + "下无匹配记录。", UNIT_YI,
                    notes=pnotes + ["如果该维度本应有数据，请检查 lib_cls / cur 的取值。"],
                    result_state="NOT_FOUND")

    # 三态判定：真实为零 vs 无数据
    vals = [_to_yi(r["ACCT_BAL_CNY"], UNIT_WAN) for r in rows]
    zero_names = [r["PROD_NM"] for r, v in zip(rows, vals) if v == 0]
    state = "ZERO" if vals and all(v == 0 for v in vals) else "OK"

    cols = ["负债大类", "业务类别"] + ([] if is_composition else ["币种"]) + \
           ["余额（亿元）", "余额占比", "年均余额（亿元）", "成本率"]
    basis_is_rate = bool(bcol) and bcol in RATE_BASIS_COLS
    if bcol:
        cols.append(f"{blabel}（百分点）" if basis_is_rate else f"{blabel}（亿元）")
    tb = []
    for r in rows:
        line = [r["LIB_CLS"], r["PROD_NM"]] + ([] if is_composition else [r["CUR_RATE"]]) + \
               [_to_yi(r["ACCT_BAL_CNY"], UNIT_WAN),
                f"{round(float(r['ACCT_BAL_CNY_PER']), 2)}%"
                if r["ACCT_BAL_CNY_PER"] is not None else "无数据",
                _to_yi(r["YR_DAY_AVG_BAL_CNY"], UNIT_WAN),
                round(float(r["AVG_COST_RATE"]), 4) if r["AVG_COST_RATE"] is not None else "无数据"]
        if bcol:
            bv = r.get("bv")
            if bv is None:
                line.append("无数据")
            elif basis_is_rate:
                line.append(round(float(bv), 2))      # 百分点，原值直出，勿折算
            else:
                line.append(_to_yi(bv, UNIT_WAN))
        tb.append(line)

    items = [(r["PROD_NM"] if not is_composition else r["LIB_CLS"], v)
             for r, v in zip(rows, vals) if v is not None]
    chart = _opt_pie(f"负债构成（{per}）", items, UNIT_YI)

    total_yi = _to_yi(total, UNIT_WAN) if total is not None else round(sum(v or 0 for v in vals), 2)
    label_idx = 0 if is_composition else 1          # 构成口径标签取「负债大类」，明细取「业务类别」
    top_i = max(range(len(vals)), key=lambda i: (vals[i] is not None, vals[i]))
    if is_composition:
        summary = (f"{_period_headline(per)}：负债合计 {total_yi:,.2f} 亿元，"
                   f"构成为 {len(tb)} 项，最大为「{tb[top_i][label_idx]}」"
                   f"（{vals[top_i]:,.2f} 亿元）。")
    else:
        summary = (f"{_period_headline(per)}：「{lib_v}」余额 {total_yi:,.2f} 亿元"
                   + (f"（币种 {cur_v}）" if cur_v else "")
                   + f"，下辖 {len(tb)} 条明细，最大为「{tb[top_i][label_idx]}」"
                   f"（{vals[top_i]:,.2f} 亿元）。")

    notes = [_unit_note_cny()] + pnotes
    if zero_names:
        notes.append(f"⚠️ 三态提示：【{'、'.join(zero_names)}】余额为 0，这是**真实业务事实**"
                     "（负债已全部退出），**不是**「无数据」。请勿表述为「无数据」或「减少 0 亿」。")
    if state == "ZERO":
        notes.append("本次查询结果全部为 0，属 ZERO 态（真实为零），非 NOT_FOUND。")
    notes.append("「其中」类子项（如其中：结算性存款）不计入合计，避免重复计算。")
    if bcol:
        notes.append(f"「{blabel}」为源表**预计算列**，直接读取，未做两期相减。")
        if basis_is_rate:
            notes.append(f"「{blabel}」单位是**百分点**（差值），不是亿元；正值=成本率上升，"
                         "负值=下降。回答时务必带上「个百分点」，不要写「亿元」。")
    return _env(f"负债总览（{UNIT_YI}）", summary, UNIT_YI, table={"columns": cols, "rows": tb},
                chart=chart, notes=notes, stat_date=per, shape="table+chart",
                period_resolved=per, result_state=state)


# ==========================================================================
# 工具 7：负债产品排名（T2）
# ==========================================================================
def rank_liability_products(lib_cls="同业负债", metric="acct_bal", period=None,
                            top_n=10, order="desc"):
    t = T_LIABILITY
    lib_v, lib_c, lib_st = _resolve(lib_cls, "LIB_CLS")
    if lib_st == "AMBIGUOUS":
        return _env("负债产品排名", f"「{lib_cls}」匹配到多个负债大类，请确认。", UNIT_YI,
                    result_state="AMBIGUOUS", candidates={"LIB_CLS": lib_c})
    if lib_st == "NOT_FOUND":
        return _env("负债产品排名", f"库中无「{lib_cls}」这个负债大类。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"LIB_CLS": _dim_values("LIB_CLS")})
    if lib_v is None:
        lib_v = "同业负债"

    mk = str(metric or "acct_bal").strip().lower()
    if mk not in T2_METRIC:
        return _env("负债产品排名", f"未知指标「{metric}」。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"metric": list(T2_METRIC.keys())})
    col, label, kind = T2_METRIC[mk]

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("负债产品排名", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    dirn = "DESC" if str(order or "desc").lower() != "asc" else "ASC"
    _, rows = _query(
        f"SELECT PROD_NM AS k, CUR_RATE AS c, `{col}` AS v FROM `{t}` "
        f"WHERE DATA_DT=%s AND LIB_CLS=%s AND PROD_NM<>'合计' ORDER BY v {dirn}",
        (per, lib_v))
    if not rows:
        return _env("负债产品排名", f"{_period_headline(per)}：负债大类「{lib_v}」下无明细。",
                    UNIT_YI, notes=pnotes, result_state="NOT_FOUND")

    unit = UNIT_YI if kind == "money" else "%"
    items = []
    for r in rows:
        v = _to_yi(r["v"], UNIT_WAN) if kind == "money" else round(float(r["v"] or 0), 2)
        items.append((f"{r['k']}（{r['c']}）", v))

    total_n = len(items)
    show = items[:top_n] if top_n and top_n > 0 else items
    table = {"columns": [f"业务类别（币种）", f"{label}（{unit}）"],
             "rows": [[k, v] for k, v in show]}
    chart = _opt_hbar(f"{lib_v}{label}排名（{unit}）", [k for k, _ in show],
                      [v for _, v in show], unit, sign_color=False)

    shown_txt = f"，此处仅展示前 {len(show)} 个" if len(show) < total_n else ""
    summary = (f"{_period_headline(per)}：负债大类「{lib_v}」共 {total_n} 个业务类别有数据"
               f"{shown_txt}。{label}最高为「{items[0][0]}」（{_fmt_num(items[0][1], unit)}）。")

    notes = ([_unit_note_cny()] if kind == "money"
             else ["占比/成本率为百分比，不做单位折算。"]) + pnotes
    notes.append(f"统计口径基于全量 {total_n} 个业务类别，展示 {len(show)} 条，两者不同。")
    notes.append("「合计」行已排除在明细之外，避免与其他产品重复计算。")
    return _env(f"{lib_v}{label}排名（{unit}）", summary, unit, table=table, chart=chart,
                notes=notes, stat_date=per, shape="table+chart", period_resolved=per,
                resolved={"LIB_CLS": lib_v})


# ==========================================================================
# 工具 8：多期对比（跨表趋势）
# ==========================================================================
_COMPARE = {
    # object -> (表, 分组列, 数值列, 源单位, 中文名, 额外过滤)
    "income": (T_INCOME, "CORE", "NET_OPR_INCOM", UNIT_WAN, "各中心营业净收入", ""),
    "liability": (T_LIABILITY, "LIB_CLS", "ACCT_BAL_CNY", UNIT_WAN, "各负债大类余额",
                  "PROD_NM='合计' AND LIB_CLS<>'合计'"),   # 只用汇总行，避免与明细重复计数
    "capital": (T_CAPITAL, "CORE", "BAL_RMB", UNIT_WAN, "各中心人民币资本公积", ""),
}


def compare_periods(object="income", keys=None, n_periods=3, period_to=None):
    obj = str(object or "income").strip().lower()
    if obj not in _COMPARE:
        return _env("多期对比", f"未知对比对象「{object}」。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"object": list(_COMPARE.keys())})
    table, dim_col, val_col, src_unit, label, extra = _COMPARE[obj]

    per_to, pnotes, pst = _resolve_period(period_to, table)
    if pst != "OK":
        return _env("多期对比", "无法定位结束期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(table)})

    allp = _periods(table)
    idx = allp.index(per_to)
    try:
        n = max(2, min(int(n_periods or 3), 12))
    except (TypeError, ValueError):
        n = 3
    start = max(0, idx - n + 1)
    periods = allp[start: idx + 1]
    degrade = []
    if len(periods) < n:
        degrade.append(f"⚠️ 需 {n} 期，实际可用 {len(periods)} 期（{_join(periods)}），已按实际可得期数返回。")

    # keys 标准化
    key_vals, key_c, key_st = _resolve_many(keys or [], dim_col)
    if key_st == "AMBIGUOUS":
        return _env("多期对比", "对比对象匹配到多个候选，请确认。", UNIT_YI,
                    result_state="AMBIGUOUS", candidates={dim_col: key_c})
    if key_st == "NOT_FOUND":
        return _env("多期对比", "对比对象在库中不存在。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={dim_col: _dim_values(dim_col)[:12]})

    ph = ",".join(["%s"] * len(periods))
    sql = (f"SELECT DATA_DT, `{dim_col}` AS k, SUM(`{val_col}`) AS v FROM `{table}` "
           f"WHERE DATA_DT IN ({ph}) ")
    args = list(periods)
    if extra:
        sql += f"AND {extra} "
    if key_vals:
        kh = ",".join(["%s"] * len(key_vals))
        sql += f"AND `{dim_col}` IN ({kh}) "
        args += key_vals
    sql += f"GROUP BY DATA_DT, `{dim_col}` ORDER BY DATA_DT"
    _, rows = _query(sql, args)
    if not rows:
        return _env("多期对比", "未匹配到数据。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND")

    # 组数据：key -> {period: value}
    keyset, datas = [], {}
    for r in rows:
        k = r["k"]
        if k not in datas:
            datas[k] = {}
            keyset.append(k)
        datas[k][r["DATA_DT"]] = _to_yi(r["v"], src_unit)

    # 展示顺序：按末期值降序
    keyset.sort(key=lambda k: (datas[k].get(periods[-1]) or -1e18), reverse=True)

    table_out = {"columns": ["对象"] + periods,
                 "rows": [[k] + [datas[k].get(p, "无数据") for p in periods] for k in keyset]}
    chart = _opt_line(f"{label}趋势（{UNIT_YI}）", periods,
                      [{"name": k, "data": [datas[k].get(p) for p in periods]} for k in keyset],
                      UNIT_YI)

    first, last = periods[0], periods[-1]
    tot_f = round(sum(datas[k].get(first) or 0 for k in keyset), 2)
    tot_l = round(sum(datas[k].get(last) or 0 for k in keyset), 2)
    delta = round(tot_l - tot_f, 2)
    direction = "上升" if delta > 0 else ("下降" if delta < 0 else "持平")
    summary = (f"{first} → {last}，共 {len(periods)} 期：{label}合计由 {tot_f:,.2f} 亿元"
               f"变为 {tot_l:,.2f} 亿元（{direction} {abs(delta):,.2f} 亿元，"
               f"{round(delta / tot_f * 100, 2) if tot_f else 0}%），共 {len(keyset)} 个对象。")

    notes = ["单位口径：源表为万元，已折算为亿元。"] + pnotes + degrade
    notes.append("趋势为多期实测值连线，期次间隔以源数据为准。")
    return _env(f"{label}多期对比（{UNIT_YI}）", summary, UNIT_YI, table=table_out,
                chart=chart, notes=notes, stat_date=per_to, shape="table+chart",
                period_resolved=per_to)


# ==========================================================================
# 工具 9：只读 SQL 兜底（7 条护栏）
# ==========================================================================
def run_readonly_sql(sql: str, max_rows: int = 200):
    s = (sql or "").strip().rstrip(";").strip()
    notes, problems = [], []

    # 护栏 1：只允许单条 SELECT
    if ";" in s:
        problems.append("检测到分号，只允许执行单条 SELECT（禁止多语句）。")
    if not re.match(r"^(select|with)\b", s, re.I):
        problems.append("只允许以 SELECT / WITH 开头的只读查询。")
    if FORBIDDEN_SQL.search(s):
        problems.append("语句包含被禁止的关键字（写操作或系统库访问）。")

    # 护栏 2：表白名单
    objs = set(re.findall(r"(?:from|join)\s+[`\"]?(\w+)[`\"]?", s, re.I))
    bad = {o for o in objs if o.lower() not in ALLOWED_OBJECTS}
    if bad:
        problems.append(f"访问了未授权的对象：{', '.join(sorted(bad))}。")
    if not objs:
        problems.append("未能识别到任何数据表，请检查语句。")

    if problems:
        return _env("只读查询", "查询被护栏拦截。", "—", table=None,
                    notes=problems + [f"允许访问的对象仅有：{_join(sorted(ALLOWED_OBJECTS))}。"],
                    result_state="NOT_FOUND",
                    candidates={"允许的对象": sorted(ALLOWED_OBJECTS)})

    # 护栏 3：强制 LIMIT
    limit_added = False
    if not re.search(r"\blimit\b", s, re.I):
        s = s + f" LIMIT {max(1, min(int(max_rows or 200), 1000))}"
        limit_added = True

    try:
        cols, rows = _query(s, timeout=5)         # 护栏 4：5 秒超时
    except Exception as e:                        # noqa: BLE001
        return _env("只读查询", f"执行失败：{type(e).__name__}: {e}", "—", table=None,
                    notes=["请检查 SQL 语法、字段名与表名。"], result_state="NOT_FOUND")

    # 护栏 6：结果上限
    truncated = False
    cap = max(1, min(int(max_rows or 200), 1000))
    if len(rows) > cap:
        rows = rows[:cap]
        truncated = True

    if not rows:
        return _env("只读查询", f"查询成功但未返回任何行（扫描到 0 行）。", "未知", table=None,
                    notes=["结果为 NOT_FOUND（条件未命中），**不是** 0，请勿表述为 0。"],
                    result_state="NOT_FOUND")

    table = {"columns": cols, "rows": [[r[c] for c in cols] for r in rows]}
    summary = f"查询返回 {len(rows)} 行 × {len(cols)} 列。" + ("（已截断）" if truncated else "")
    notes = [
        "⚠️ 单位未知：兜底 SQL 结果**不做单位折算**。三张源表底层均为「万元」，"
        "若需亿元口径请自行除以 1e4，**切勿把万元当元**。",
        "本次为框架外的自定义查询，结果正确性取决于 SQL 本身；建议优先使用语义化工具。",
    ]
    if limit_added:
        notes.append(f"原语句无 LIMIT，已自动追加 LIMIT {cap}。")
    if truncated:
        notes.append(f"结果超过 {cap} 行，已截断。")
    return _env("只读查询结果", summary, "未知", table=table, notes=notes,
                shape="table", result_state="OK")


# ==========================================================================
# 序列化 / 装饰器 / 注册
# ==========================================================================
def _json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _safe(fn):
    """统一异常兜底。必须用 functools.wraps 保留签名——MCP 靠 inspect.signature
    生成工具 JSON Schema，签名退化成 (*args, **kwargs) 会被宿主参数校验拒绝。"""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return _json(fn(*a, **kw))
        except Exception as e:  # noqa: BLE001
            return _json({"shape": "text", "title": "查询失败",
                          "summary": f"工具执行异常：{type(e).__name__}: {e}",
                          "unit": "—", "table": None, "chart": None, "notes": [],
                          "result_state": "NOT_FOUND",
                          "period_resolved": None, "resolved": {}, "candidates": {}})
    return wrapper


INSTRUCTIONS = (
    "金融市场部经营分析问答服务（baop 库，只读）。覆盖三块业务："
    "①营业净收入/非息/息差（T3，中心×业务线）；②资本公积变动（T1，按中心）；③负债规模（T2）。"
    "三张源表底层单位均为「万元」，本服务统一折算并输出「亿元」，请勿自行换算。"
    "源数据为日频（每日一期）：未指定期次时自动取最新期次并在 summary 首句回显；"
    "所问日期无数据时会回落到最近可用期次并在 notes 中声明。"
    "维度参数可用口语（如「投资中心」「外币」），服务内部会做码值标准化并在 resolved 中回显映射。"
    "返回值 result_state ∈ OK/ZERO/NULL/NOT_FOUND/AMBIGUOUS："
    "candidates 非空表示需要向用户澄清，此时 table 为空、不要编造数据；"
    "ZERO 表示真实为零（如外币司库负债已退出），NULL 表示无数据，两者不可混为一谈。"
    "\n【问题 → 工具速查】"
    "整体收入/各中心对比/非息占比 → get_income_overview_tool；"
    "排名/最高最低/谁贡献最多/亏损筛查/完成率/序时进度/当周 → rank_income_tool；"
    "单中心或单业务线明细/由哪些业务线构成 → get_income_detail_tool；"
    "资本公积（含浮亏/负数）→ get_capital_reserve_tool；"
    "负债规模与构成/同业·本币·外币司库/较年初·较上月末变化 → get_liability_overview_tool；"
    "负债产品排名/哪类规模最大·成本率最高 → rank_liability_products_tool；"
    "走势·趋势·最近几期·多期对比 → compare_periods_tool；"
    "有哪些中心/业务线/维度、数据截止到什么时候、单位是什么、是否每天更新 → list_dimensions_tool。"
    "\n【必须先取码值】用户用简称/别名/口语（「资金中心」「外汇」「同业」「外币」「贵金属」）时，"
    "先调 list_dimensions_tool 对齐标准码值再传参；拿不准就把候选项列给用户确认，不要猜。"
    "\n【能力边界（超范围一律如实说明，不要编造）】"
    "库中只有上述三张表，**没有**净利润率、不良率/不良贷款率、客户数、人均产能、存款付息率等指标；"
    "不做预测/外推（如「年底能到多少」），不做写操作（改计划、加数据一律拒绝）。"
    "\n【派生指标】占比、增速、计划缺口、跨指标比对等派生值，优先对语义化工具的返回结果自行计算并注明；"
    "仅在取不到原始数据时才使用 run_readonly_sql_tool。"
)


def _new_server(name: str):
    """兼容 mcp 1.x（FastMCP）与 2.x（MCPServer）。"""
    try:
        from mcp.server.mcpserver import MCPServer
        return MCPServer(name, instructions=INSTRUCTIONS), 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP
        return FastMCP(name, instructions=INSTRUCTIONS), 1


def _register_tools(mcp):
    @mcp.tool()
    @_safe
    def list_dimensions_tool(dimension: str = "all") -> str:
        """列出本数据服务可查询的范围与数据字典：中心 / 业务线 / 负债大类 / 业务类别 / 币种 / 可用期次 / 指标 / 元数据。
        当用户问「有哪些中心」「有哪些业务线 / 维度」「数据截止到什么时候」「最新期次是几号」「单位是什么」
        「有哪些数据表」「数据是每天更新吗」时使用。
        也用于**传参前先取标准码值**：用户说的是简称/别名/口语（如「资金中心」「外汇」「同业」「外币」）时，
        先调本工具拿到标准写法，再传给其他工具。
        dimension 可选 all(默认)/center/biz_line/lib_cls/prod/cur/period/metric/meta。"""
        return list_dimensions(dimension)

    @mcp.tool()
    @_safe
    def get_income_overview_tool(period: str = "", top_n: int = 0) -> str:
        """各中心收入总览：营业净收入 / 非息收入 / 息差，并给出「非息占营业比」，返回分组柱状图。
        适合「收入整体怎么样」「全部门收入多少」「各中心收入对比」「谁强谁弱」「收入结构如何」
        「非息占比多少」「息差什么水平」这类总览问题（一次拿全全部中心，含合计行）。
        period 不传即为最新期次；top_n>0 时只展示前 N 个。
        需要严格排序 / 单项排名 / 亏损筛查时请改用 rank_income_tool。"""
        return get_income_overview(period or None, top_n or 0)

    @mcp.tool()
    @_safe
    def rank_income_tool(metric: str = "net_opr_incom", by: str = "core",
                         period: str = "", top_n: int = 10, order: str = "desc") -> str:
        """收入类指标排名，返回横向条形图。摘要给出「最高 / 次高 + 领先差距 / 末位 / 负值个数」。
        适合「谁贡献最多」「哪个中心最高」「哪个业务线收入最高」「排个序看看」「领先第二名多少」
        「有没有中心是亏损的」「各中心收入占比 / 结构贡献」。
        metric 可选（注意口径不同，勿混用）：
          net_opr_incom(营业净收入，默认) / not_int_incom(非息收入) / net_int(息差)
          / plan(营业净收入-经营计划) / comp_rate(营业净收入-计划完成率)
          / tm_prog(息差-序时进度) / curr_week(营业净收入-当周)
        by 可选 core(按中心，默认) / biz_line(按业务线)；order 为 desc(高→低) / asc(低→高)。
        ⚠️ 缺口类问题（「离全年计划还差多少」「完成率是否低于序时进度」）需要两个指标相减/相比，
           本工具一次只返回一个 metric：请分别取 plan 与 net_opr_incom（或 comp_rate 与 tm_prog）后自行计算。
        ⚠️ 「利率相关 / 跨境类 / 外币类」等**同类业务线聚合**：先 by=biz_line、top_n=0 取全量清单，再按名称归并。
        ⚠️ top_n 只影响展示行数，统计口径始终是全量（摘要中同时给出「共 N 个」与展示条数）。"""
        return rank_income(metric, by, period or None, top_n or 0, order)

    @mcp.tool()
    @_safe
    def get_income_detail_tool(core: str = "", biz_line: str = "", period: str = "") -> str:
        """查询某中心或某业务线的收入明细（营业净收入 / 非息 / 息差 / 经营计划 / 营业完成率 / 非息完成率 / 非息序时进度）。
        适合「XX 中心收入怎么样」「XX 中心现在什么情况」「外汇这块收入有多少」「XX 由哪些业务线构成」
        「XX 靠哪些业务线挣钱」「某条业务线做得怎么样」。
        core 与 biz_line 可直接传用户原话（如「投资中心」「外汇」「贵金属」），工具会做码值标准化并在 resolved 中回显；
        两者都不传则按中心级汇总。
        ⚠️ core 与 biz_line 是两个不同维度，不要同时传。
        ⚠️ 要对比两个对象（如「客户中心 vs 投资管理中心谁贡献更大」）请用 rank_income_tool(by='core') 一次拿全再比。"""
        return get_income_detail(core or None, biz_line or None, period or None)

    @mcp.tool()
    @_safe
    def get_capital_reserve_tool(core: str = "", period: str = "", basis: str = "none") -> str:
        """查询资本公积（可供出售账户）情况，返回柱状图；含人民币 / 外币 / 可供出售账户资产余额。
        适合「资本公积多少」「资本公积合计」「各中心资本公积分别是多少」「谁占用最多」
        「有没有中心的资本公积是负数 / 浮亏」「资本公积较年初 / 比上月末变化多少」。
        core 可传口语（如「资金中心」）；basis 为对比基准（本表全部为金额，单位亿元）：
          none(默认) / yr_bgn(较年初) / last_mth(较上月末) / last_fri(较上周五)
          / last_day(较上日) / last_quar(较上季末) / last_year_dt(较上年同期)。
        ⚠️ 本表「较上月末」的键名是 last_mth；last_mth_end 是**负债表**的键名，传到这里会报错。"""
        return get_capital_reserve(core or None, period or None, basis)

    @mcp.tool()
    @_safe
    def get_liability_overview_tool(lib_cls: str = "合计", cur: str = "",
                                    period: str = "", basis: str = "none") -> str:
        """查询负债规模与构成，返回饼图 + 明细表（余额 / 余额占比 / 年均余额 / 成本率）。
        适合「负债规模多大」「负债主要由哪几块构成」「各自占比多少」「同业负债规模多少」
        「本币司库 / 外币司库什么规模」「哪个规模更大」「较年初 / 比上月末 / 较上周五变化多少」
        「成本率较年初是升还是降」「年均余额口径与期末余额差多少」。
        lib_cls 可传「合计 / 同业 / 外币 / 本币 / 司库」等口语（缺省「合计」= 返回三大构成）；
        cur 不传即为全部币种，也可传 01(本币)/02(外币)；
        basis 可选 none(默认)/yr_bgn/last_mth/last_mth_end/last_fri/avg_yr_bgn/avg_last_mth
          /cost_yr_bgn/cost_last_mth/cost_last_fri。
        ⚠️ cost_* 类基准（成本率对比）的单位是**百分点**，其余基准是**亿元**；工具已按此标注列名，引用时勿写成「亿元」。"""
        return get_liability_overview(lib_cls, cur, period or None, basis)

    @mcp.tool()
    @_safe
    def rank_liability_products_tool(lib_cls: str = "同业负债", metric: str = "acct_bal",
                                     period: str = "", top_n: int = 10, order: str = "desc") -> str:
        """负债各业务类别排名，返回横向条形图。
        适合「同业负债里哪类产品规模最大」「哪类负债产品成本率最高」「余额占比最大的产品是什么」。
        lib_cls 指定负债大类（缺省同业负债）；metric 可选 acct_bal(余额，默认)/bal_per(余额占比)
          /avg_bal(年均余额)/avg_bal_per(年均余额占比)/cost_rate(成本率)；order 为 desc/asc。
        ⚠️ top_n 只影响展示行数，统计口径始终是全量。"""
        return rank_liability_products(lib_cls, metric, period or None, top_n or 0, order)

    @mcp.tool()
    @_safe
    def compare_periods_tool(object: str = "income", keys: str = "", n_periods: int = 3,
                             period_to: str = "") -> str:
        """多期对比与趋势，返回折线图。
        适合「最近几期走势如何」「趋势怎么样」「是升还是降」「比上月 / 较年初变化多少」
        「近三期累计变化了多少」「把各对象的多期趋势都列出来」。
        object 可选 income(收入，默认) / liability(负债) / capital(资本公积)；
        keys 为逗号分隔的对象清单（如「资金管理中心,客户中心」），不传则全部对象；
        n_periods 回溯期数(2–12，默认 3；**以源数据的期数为单位，不是月份**)；period_to 为结束期次，不传即最新。
        ⚠️ 摘要只给「合计口径」的变化幅度；单个对象的增速 / 累计变化请按「末期 ÷ 首期 - 1」自行计算后给出。
        ⚠️ 用户说「近半年 / 近一季度」时不要换算成 180 / 90 期，按可得期数返回；
           期数不足时工具会在 notes 里给出「需 N 期，实际可用 M 期」的降级说明，照抄即可。"""
        ks = [x.strip() for x in (keys or "").split(",") if x.strip()]
        return compare_periods(object, ks or None, n_periods or 3, period_to or None)

    @mcp.tool()
    @_safe
    def run_readonly_sql_tool(sql: str, max_rows: int = 200) -> str:
        """兜底工具：语义化工具都无法覆盖时，执行一条只读 SELECT。
        仅允许访问三张事实表白名单，自动追加 LIMIT，5 秒超时，禁止任何写操作。
        可用对象：fpp_fin_mkt_bizline_income_sum_t1、fpp_core_cap_rsv_t1、fpp_whi_lib_biz_situ_t1。
        适用：「中心 × 业务线交叉明细」、自定义分组或条件筛选等语义化工具表达不了的取数需求。
        不适用：新增 / 修改 / 删除 / 建表（一律拒绝）；占比、增速、缺口等派生指标优先由上层
        对语义化工具的结果计算，本工具只用于「取不到」，不是「懒得算」。"""
        return run_readonly_sql(sql, max_rows)

    return mcp


def build_server():
    server, _ = _new_server("baop-analysis")
    return _register_tools(server)


# ==========================================================================
# 自检（不依赖 mcp 包）
# ==========================================================================
def selftest():
    cases = [
        ("list_dimensions(all)", lambda: list_dimensions("all")),
        ("list_dimensions(center)", lambda: list_dimensions("center")),
        ("get_income_overview(latest)", lambda: get_income_overview()),
        ("get_income_overview(上月末降级)", lambda: get_income_overview("近一周")),
        ("rank_income(默认)", lambda: rank_income()),
        ("rank_income(biz_line, 非息)", lambda: rank_income("not_int_incom", "biz_line")),
        ("rank_income(完成率)", lambda: rank_income("comp_rate")),
        ("get_income_detail(投资中心)", lambda: get_income_detail("投资中心")),
        ("get_income_detail(外汇业务)", lambda: get_income_detail(biz_line="外汇")),
        ("get_income_detail(票据中心-零命中)", lambda: get_income_detail("票据中心")),
        ("get_income_detail(利率-歧义)", lambda: get_income_detail(biz_line="利率")),
        ("get_capital_reserve(全部)", lambda: get_capital_reserve()),
        ("get_capital_reserve(资金中心, yr_bgn)", lambda: get_capital_reserve("资金中心", None, "yr_bgn")),
        ("get_liability_overview(合计)", lambda: get_liability_overview()),
        ("get_liability_overview(外币)", lambda: get_liability_overview("外币")),
        ("get_liability_overview(同业负债)", lambda: get_liability_overview("同业负债")),
        ("rank_liability_products(同业负债)", lambda: rank_liability_products()),
        ("compare_periods(income)", lambda: compare_periods()),
        ("compare_periods(liability)", lambda: compare_periods("liability")),
        ("compare_periods(需6期实有3期)", lambda: compare_periods("income", None, 6)),
        ("run_readonly_sql(合法)", lambda: run_readonly_sql(
            "SELECT CORE, SUM(NET_OPR_INCOM) AS v FROM fpp_fin_mkt_bizline_income_sum_t1 GROUP BY CORE")),
        ("run_readonly_sql(写操作)", lambda: run_readonly_sql("DELETE FROM fpp_core_cap_rsv_t1")),
        ("run_readonly_sql(越权表)", lambda: run_readonly_sql("SELECT * FROM mysql.user")),
    ]
    ok = 0
    for name, fn in cases:
        try:
            r = fn()
            shape = r.get("shape")
            rows = len(r["table"]["rows"]) if r.get("table") else 0
            chart = "有图" if r.get("chart") else "无图"
            st = r.get("result_state")
            print(f"[OK]   {name:36s} state={st:10s} shape={shape:12s} 行数={rows:<3d} {chart}")
            print(f"       摘要: {(r.get('summary') or '')[:84]}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name:36s} {type(e).__name__}: {e}")
    print(f"\n自检完成：{ok}/{len(cases)} 通过")


def main():
    ap = argparse.ArgumentParser(description="金融市场部经营分析 MCP Server（baop 库，只读）")
    ap.add_argument("--transport", default="stdio",
                    choices=["stdio", "sse", "streamable-http"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--selftest", action="store_true", help="自检所有查询逻辑")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    server, major = _new_server("baop-analysis")
    server = _register_tools(server)
    if args.transport == "stdio":
        server.run()
    elif major >= 2:
        server.run(args.transport, host=args.host, port=args.port)
    else:
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
