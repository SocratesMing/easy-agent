# -*- coding: utf-8 -*-
"""
金融市场部经营分析（baop 库）—— MCP Server（只读）

用途：把 MySQL `baop` 库中三张 FPP 业务表的数据，以「业务语义化工具」的形式暴露给
     支持 MCP 的宿主（WorkBuddy / easyagent），供自然语言问答调用。

数据底座
  T3  fpp_fin_mkt_bizline_income_sum_t1  营业净收入/非息/息差                      单位：万元
  T1  fpp_core_cap_rsrv_t1                资本公积变动                             单位：万元
  T2  fpp_whl_liab_biz_situ_t1            负债规模                                 单位：万元

单位口径（**已锁定，勿再变更**，2026-10-14 业务方最终确认）
  三张源表底层**统一为「万元」**。工具层一律折算为**亿元**（万元 ÷ 1e4）对外呈现。
  百分比列（占比 / 成本率 / 完成率 / 序时进度 / 增速）不参与折算。

三条核心逻辑（本文件是它们的唯一实现处）
  1. 日期/期次缺省：**以库为准、不以时钟为准**。
     - T3（BIZ_DT）/ T2（DATA_PRDC_DT）为双日期表：`DATA_DT` 恒取 `MAX(DATA_DT)`（最新快照），
       业务日期取用户所查日期；用户未给日期时取该快照下的 `MAX(业务日期)`。
       两者缺一不可，否则同一快照多业务日期、或同一业务日期跨多快照，都会重复统计。
     - T1：只有 DATA_DT，无日期 → MAX(DATA_DT)。
     对比类问题优先读「预计算列」（basis 参数是列选择器，不是时间算术）。
  2. 码值标准化：`_resolve()` 四级流水线（归一化→别名→模糊→仲裁三出口）。
     低置信度**绝不静默取第一个**，返回 candidates 由上层反问。
  3. 覆盖策略（2026-10-19 起）：**不设自由 SQL 入口**，改为「指标定义下沉到工具」。
     完成率 / 序时进度 / 缺口 / 发生额 / 增速等派生指标全部在工具内按加权口径计算，
     交叉取数由 get_income_matrix / get_income_flow / get_liability_detail 覆盖。
     原 SQL 兜底工具（含 7 条护栏、表白名单与自检项）已彻底移除，工具面固定为 12 个语义化工具。

统一返回信封
  shape | title | summary | unit | stat_date | table | chart | notes
  + period_resolved | resolved | candidates | result_state
  result_state ∈ OK / ZERO / NULL / NOT_FOUND / AMBIGUOUS

运行
  python baop_mcp_server.py                                  # stdio（默认）
  python baop_mcp_server.py --transport sse --port 8766
  python baop_mcp_server.py --selftest                       # 不依赖 mcp 包自检

连接配置见 :func:`cfg`，读 ``BAOP_HOST`` / ``BAOP_PORT`` / ``BAOP_USER`` /
``BAOP_PASSWORD`` / ``BAOP_DB``（来自 mcp-server/.env）。
"""

from __future__ import annotations

import argparse
import difflib
import functools
import json
import os
import re
import sys
import time
from datetime import date, datetime
from decimal import Decimal

from ...env import load_env

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def cfg() -> dict:
    """连接配置。**函数式读取**，改环境变量后下次查询即生效（无需重启）。"""
    load_env()
    port_raw = os.environ.get("BAOP_PORT", "3306").strip() or "3306"
    try:
        port = int(port_raw)
    except ValueError:
        port = 3306
    return {
        "host": os.environ.get("BAOP_HOST", "127.0.0.1").strip() or "127.0.0.1",
        "port": port,
        "user": os.environ.get("BAOP_USER", "baop_ro").strip() or "baop_ro",
        "password": os.environ.get("BAOP_PASSWORD", ""),
        "db": os.environ.get("BAOP_DB", "baop").strip() or "baop",
    }

T_INCOME = "fpp_fin_mkt_bizline_income_sum_t1"   # T3，单位万元
T_CAPITAL = "fpp_core_cap_rsrv_t1"                # T1，单位万元
T_LIABILITY = "fpp_whl_liab_biz_situ_t1"          # T2，单位万元

# 源表里表示「合计/小计」的维度码值。这些行本身是已汇总好的结果，
# 一旦与明细行一起参与 SUM 就会翻倍（T1 的 CORE 实测存在「合计」码值）。
_TOTAL_LABELS = ("合计", "总计", "小计", "汇总")


def _exclude_total(col, exclude=True):
    """返回 (SQL 片段, 参数)。exclude=False 时返回空片段，用于用户显式点名「合计」的场景。"""
    if not exclude:
        return "", []
    ph = ",".join(["%s"] * len(_TOTAL_LABELS))
    return f"`{col}` NOT IN ({ph})", list(_TOTAL_LABELS)


def _is_total(v):
    """该维度值是否表示「合计」类汇总行。"""
    return v is not None and str(v).strip() in _TOTAL_LABELS


# --------------------------------------------------------------------------
# 组织映射：库里没有「部门」维度，部门口径 = 中心集合（2026-10-19 业务确认：
# T3 源表全部数据即为金融市场部口径，覆盖全部中心；合计行不参与，由 _TOTAL_LABELS 防护）。
#   值为 "ALL"  -> 全部中心
#   值为 (中心1, 中心2, ...) -> 指定中心集合（SQL 走 CORE IN）
# ---------------------------------------------------------------------------
DEPARTMENT_MAP = {
    "金融市场部": "ALL",
}


def _match_department(raw):
    """按部门名匹配组织映射。返回 (部门名, 中心集合) 或 (None, None)。

    匹配规则：全等 / 输入包含部门名 / 部门名包含输入，
    容忍「金融市场部」「金融市场部门」「金融市场」等写法；
    中心名（如「资金管理中心」）不会被误匹配。
    """
    if not raw:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    for name, centers in DEPARTMENT_MAP.items():
        if s == name or name in s or s in name:
            return name, centers
    return None, None


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
    "CUR_CATE": (T_LIABILITY, "CUR_CATE", "币种类别"),
    "CORE_CAP": (T_CAPITAL, "CORE", "中心（资本公积）"),
}
DIM_ALIAS = {           # list_dimensions 的对外名字 -> 内部维度
    "center": "CORE",
    "biz_line": "BIZ_LINE_DESC",
    "lib_cls": "LIB_CLS",
    "prod": "PROD_NM",
    "cur": "CUR_CATE",
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

# --------------------------------------------------------------------------
# 计划 / 分解目标口径（2026-10-19 新增）
#   metric -> (实际列, 计划或分解目标列, 序时进度列, 中文名)
#   ⚠️ 完成率一律按 Σ实际 ÷ Σ计划 **加权**计算，禁止对各行完成率求平均：
#      源列 NET_OPR_INCOM_COMP_RATE 是单行口径，聚合到中心/部门后必须重算。
# --------------------------------------------------------------------------
T3_PLAN = {
    "net_opr_incom": ("NET_OPR_INCOM", "NET_OPR_INCOM_PLAN", None, "营业净收入"),
    "not_int_incom": ("NOT_INT_INCOM", "NOT_INT_INCOM_UNPKG_TGT", "NOT_INT_INCOM_TM_PROG", "非息收入"),
    "net_int": ("NET_INT", "NET_INT_UNPKG_TGT", "NET_INT_TM_PROG", "息差"),
}

# 比率类指标的加权表达式：(分子 SQL, 分母 SQL)。rank_income 按维度聚合时用它重算，
# 取代此前的 AVG(比率列)——简单平均会让大计划体量的小中心虚增权重。
RATE_WEIGHT_SQL = {
    "comp_rate": ("SUM(NET_OPR_INCOM)", "SUM(NET_OPR_INCOM_PLAN)"),
    "tm_prog": ("SUM(NET_INT_TM_PROG*NET_INT_UNPKG_TGT)", "SUM(NET_INT_UNPKG_TGT)"),
}

# 当周 / 发生额口径：metric -> (累计列, 当周列, 中文名)
# 期间发生额 = 本期累计 − 上一可用期次累计（源表只有 YTD，没有当期发生额字段）
T3_FLOW = {
    "net_opr_incom": ("NET_OPR_INCOM", "NET_OPR_INCOM_CURR_WEEK", "营业净收入"),
    "not_int_incom": ("NOT_INT_INCOM", "NOT_INT_INCOM_CURR_WEEK", "非息收入"),
    "net_int": ("NET_INT", "NET_INT_CURR_WEEK", "息差"),
}

# 中心 × 业务线交叉矩阵可用的口径：metric -> (中文名, 量纲)
MATRIX_METRIC = {
    "net_opr_incom": ("营业净收入", "money"),
    "not_int_incom": ("非息收入", "money"),
    "net_int": ("息差", "money"),
    "plan": ("营业净收入-经营计划", "money"),
    "curr_week": ("营业净收入-当周", "money"),
    "comp_rate": ("营业净收入-计划完成率", "rate"),
}

# 增速：源表预计算百分比列，直接读取，**不做两期相减、不做单位折算**
T1_GROWTH = {
    "rmb_mth": ("RATE_BAL_RMB_VS_LAST_MTH", "人民币环比增速"),
    "rmb_ytd": ("RATE_BAL_RMB_VS_LAST_YEAR", "人民币较年初增速"),
    "rmb_yoy": ("RATE_BAL_RMB_VS_LAST_YEAR_DT", "人民币同比增速"),
    "fx_mth": ("RATE_BAL_FOREGIN_VS_LAST_MTH", "外币环比增速"),
    "fx_ytd": ("RATE_BAL_FOREGIN_VS_LAST_YEAR", "外币较年初增速"),
    "fx_yoy": ("RATE_BAL_FOREGIN_VS_LAST_YEAR_DT", "外币同比增速"),
}
T2_GROWTH = {
    "mth": ("DUBIL_BAL_VS_LAST_TERM_RATE", "余额环比增速"),
    "yr": ("DUBIL_BAL_VS_LAST_YEAR_RATE", "余额同比增速"),
    "avg_mth": ("YRD_AVG_BAL_VS_LAST_TERM_RATE", "年均余额环比增速"),
    "avg_yr": ("YRD_AVG_BAL_VS_LAST_YEAR_RATE", "年均余额同比增速"),
}
# 明细页可选的「对比变动」三件套：余额 / 年均余额 / 成本率（百分点）
T2_DELTA = {
    "last_mth_end": ("ACCT_BAL_CNY_VS_LAST_MTH_END", "YR_DAY_AVG_BAL_CNY_VS_LAST_MTH_END",
                     "AVG_COST_RATE_VS_LAST_MTH_END", "较上月末"),
    "last_fri": ("ACCT_BAL_CNY_VS_LAST_FRI", "YR_DAY_AVG_BAL_CNY_VS_LAST_FRI",
                 "AVG_COST_RATE_VS_LAST_FRI", "较上周五"),
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
# 外币口径的同类对比列（2026-10-19 补：此前 basis 只能取人民币列，
# 「外币资本公积比上月末多了多少」答不了）。键与 T1_BASIS 一一对应。
T1_BASIS_FX = {
    "none": None,
    "last_day": ("BAL_FOREGIN_VS_LAST_DAY", "较上日外币变动"),
    "last_fri": ("COMPARE_LAST_FRI_BAL_FOREGIN", "较上周外币变动"),
    "last_mth": ("BAL_FOREGIN_VS_LAST_MTH", "较上月末外币变动"),
    "last_quar": ("BAL_FOREGIN_VS_LAST_QUAR", "较上季末外币变动"),
    "last_year_dt": ("BAL_FOREGIN_VS_LAST_YEAR_DT", "较上年同期外币变动"),
    "yr_bgn": ("COMPARE_BAL_FOREGIN", "较年初外币变动"),
}
T2_BASIS = {
    "none": None,
    "yr_bgn": ("ACCT_BAL_CNY_VS_YR_BGN", "余额较年初增量"),
    "last_mth": ("ACCT_BAL_CNY_VS_LAST_MTH", "余额较上月增量"),
    "last_mth_end": ("ACCT_BAL_CNY_VS_LAST_MTH_END", "余额较上月末增量"),
    "last_fri": ("ACCT_BAL_CNY_VS_LAST_FRI", "余额较上周五增量"),
    "avg_yr_bgn": ("YR_DAY_AVG_BAL_CNY_VS_YR_BGN", "年均余额较年初增量"),
    "avg_last_mth": ("YR_DAY_AVG_BAL_CNY_VS_LAST_MTH", "年均余额较上月增量"),
    "avg_last_mth_end": ("YR_DAY_AVG_BAL_CNY_VS_LAST_MTH_END", "年均余额较上月末增量"),
    "avg_last_fri": ("YR_DAY_AVG_BAL_CNY_VS_LAST_FRI", "年均余额较上周五增量"),
    "cost_yr_bgn": ("AVG_COST_RATE_VS_YR_BGN", "成本率较年初"),
    "cost_last_mth": ("AVG_COST_RATE_VS_LAST_MTH", "成本率较上月"),
    "cost_last_mth_end": ("AVG_COST_RATE_VS_LAST_MTH_END", "成本率较上月末"),
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
    c = cfg()
    return pymysql.connect(
        host=c["host"], port=c["port"], user=c["user"],
        password=c["password"], database=c["db"],
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
    (("上一期", "上期", "前一期", "前期"), "prev"),
    (("本月", "当期", "现在", "目前", "最新", "当前"), "latest"),
]


# 双日期表：DATA_DT = 数据日期（跑批快照日），业务日期列 = 该快照对应的业务期。
# 只按单一字段过滤都会重复统计：
#   - 只按 DATA_DT：同一快照下的多个业务日期会叠加
#   - 只按业务日期：同一业务日期会被多份快照重复统计
# 规则统一为：DATA_DT 恒取 MAX(DATA_DT)，业务日期取用户所查（未给则取该快照下的 MAX）。
_BIZ_DATE_COL = {
    T_INCOME: "BIZ_DT",           # NOT NULL
    T_LIABILITY: "DATA_PRDC_DT",  # 可为 NULL，取数时用 COALESCE 回落到 DATA_DT
}
_NULLABLE_BIZ_COL = {T_LIABILITY}
_DUAL_DATE_TABLES = set(_BIZ_DATE_COL)


def _biz_expr(table):
    """业务日期列的 SQL 表达式。NULL 值回落到 DATA_DT，避免整行因空业务日期而查不到。"""
    col = _BIZ_DATE_COL[table]
    return f"`{col}`" if table not in _NULLABLE_BIZ_COL else f"COALESCE(`{col}`, DATA_DT)"


_ANCHOR_CACHE: dict[str, tuple[float, str | None]] = {}
_ANCHOR_TTL = 3.0          # 秒：保证「同一次回答」内的数据日期锚点一致，同时容忍跑批刷新


def _anchor_dt(table):
    """数据日期锚点：总是取 MAX(DATA_DT)。双日期表用它固定快照，杜绝跨期叠加。

    同一进程内 3 秒缓存，避免一次调用里重复 MAX 查询、也避免各子句取到不同锚点。
    """
    hit = _ANCHOR_CACHE.get(table)
    now = time.monotonic()
    if hit and now - hit[0] < _ANCHOR_TTL:
        return hit[1]
    val = _scalar(f"SELECT MAX(DATA_DT) AS d FROM `{table}`")
    _ANCHOR_CACHE[table] = (now, val)
    return val


def _periods(table=None):
    """返回可查询期次（升序）。table 为 None 时取三表交集。

    注意：无参版本里 T3/T2 贡献的是业务日期、T1 贡献的是数据日期，语义不同、交集可能为空，
    仅用于粗粒度概览；按表取期次时请显式传 table。

    - 双日期表（T3 `BIZ_DT` / T2 `DATA_PRDC_DT`）：返回**最新 DATA_DT 下的**业务日期清单，
      即用户实际可按业务日期查询的范围
    - 其他表（T1）：返回 DATA_DT 清单
    """
    if table:
        if table in _DUAL_DATE_TABLES:
            anchor = _anchor_dt(table)
            if not anchor:
                return []
            _, rows = _query(f"SELECT DISTINCT {_biz_expr(table)} AS d FROM `{table}` "
                             "WHERE DATA_DT=%s ORDER BY d", (anchor,))
            return [r["d"] for r in rows if r["d"] is not None]
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


def _dual_note(table, period):
    """双日期表的口径说明：明确「数据日期取最新、业务日期为所查日期」。"""
    if table not in _DUAL_DATE_TABLES or not period:
        return []
    anchor = _anchor_dt(table)
    if not anchor:
        return []
    if str(anchor) == str(period):
        return [f"口径：数据日期与业务日期均为 {anchor}（最新一期快照）。"]
    return [f"口径：数据日期取最新 {anchor}，业务日期为所查 {period}。"]


def _resolve_period(raw, table):
    """期次锚点解析。返回 (period, notes, state)。

    period 的语义：
      - 双日期表（T3）：**业务日期 BIZ_DT**；数据日期 DATA_DT 由 _date_where 固定取 MAX
      - 其他表：DATA_DT

    - 无日期       -> 该表可查期次的最大值                       OK
    - 命中期次     -> 原值                                       OK
    - 落在区间内   -> 就近回落到 ≤ 该日的最近期次 + 说明          OK
    - 超范围       -> 报边界 + 给可用期次清单                    NOT_FOUND
    - 无该日数据   -> 回落最近一期 + 明确声明回落原因              OK
    """
    ps = _periods(table)
    if not ps:
        return None, ["该表无任何可用期次。"], "NOT_FOUND"
    latest, notes = ps[-1], []

    def fin(per, ns, state):
        return per, ns + _dual_note(table, per), state

    if raw is None or str(raw).strip() == "":
        return fin(latest, [f"未指定日期，已按最新可用期次 {latest} 回答。"], "OK")

    s = str(raw).strip()

    # 1) 相对关键词
    for kws, mode in _REL_KEYWORDS:
        if any(k in s for k in kws):
            if mode == "latest":
                return fin(latest, [f"已识别为「当期」，按最新可用期次 {latest} 回答。"], "OK")
            if mode == "prev":
                p = _prev(table, latest)
                return fin(p, [f"已按上一期次 {p} 回答。"], "OK") if p else \
                       (None, ["库中无上一期次。"], "NOT_FOUND")
            if mode == "prev_mth_end":
                # 上一自然月末：取 ≤ (最新期的上一个月最后一天) 的最近期次
                cands = [p for p in ps if p <= _last_day_of_prev_month(latest)]
                if cands:
                    p = cands[-1]
                    return fin(p, [f"已按「上月末」口径取最近可用期次 {p}。"], "OK")
                return None, [f"库中无「上月末」口径的数据，可用期次：{_join(ps)}"], "NOT_FOUND"
            if mode == "degrade":
                return fin(latest, [
                    f"⚠️ 你问的「{s}」在库中暂无数据，已降级为最近可用期次 {latest} 回答。"
                ], "OK")
            if mode == "boundary":
                return None, [
                    f"库中无「{s}」对应的期次。当前可用期次：{_join(ps)}。"
                ], "NOT_FOUND"

    # 2) 显式日期
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        d = "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d in ps:
            return fin(d, [], "OK")
        if d < ps[0] or d > ps[-1]:
            return None, [f"期次 {d} 超出数据范围。可用期次：{_join(ps)}"], "NOT_FOUND"
        p = max(x for x in ps if x <= d)
        return fin(p, [f"期次 {d} 无数据，已就近回落到最近可用期次 {p}。"], "OK")

    # 3) 只给年月
    m = re.search(r"(\d{4})[-/年](\d{1,2})", s)
    if m:
        ym = "%04d-%02d" % (int(m.group(1)), int(m.group(2)))
        cands = [p for p in ps if str(p).startswith(ym)]
        if len(cands) == 1:
            return fin(cands[0], [], "OK")
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


# ---- 期次过滤：T3 收入表 = 最新数据日期 + 指定业务日期 ----
# DATA_DT = 数据日期（快照/跑批日），BIZ_DT = 业务日期。
# 规则：DATA_DT 恒取 MAX(DATA_DT)（最新快照），BIZ_DT 取用户所查日期（未给则取该快照下的 MAX）。
# 这样既不会把历史快照叠加进来，又能查到最新快照里携带的任意业务日期数据。
def _date_where(table, period):
    """返回 (SQL 条件, 参数列表)。双日期表 = 最新 DATA_DT + 指定业务日期，其余表只锁 DATA_DT。"""
    if table in _DUAL_DATE_TABLES:
        return f"DATA_DT=%s AND {_biz_expr(table)}=%s", [_anchor_dt(table), period]
    return "DATA_DT=%s", [period]


def _date_where_in(table, periods):
    """多期次的 IN 版本，返回 (SQL 条件片段, 参数列表)。"""
    ph = ",".join(["%s"] * len(periods))
    if table in _DUAL_DATE_TABLES:
        return (f"DATA_DT=%s AND {_biz_expr(table)} IN ({ph})",
                [_anchor_dt(table)] + list(periods))
    return f"DATA_DT IN ({ph})", list(periods)


def _dual_date_note(table, period):
    """取不到数时的双日期诊断（双日期表专用，且只在空结果分支调用）。"""
    if table not in _DUAL_DATE_TABLES:
        return []
    anchor, bexpr = _anchor_dt(table), _biz_expr(table)
    n_dual = _scalar(f"SELECT COUNT(*) AS n FROM `{table}` "
                     f"WHERE DATA_DT=%s AND {bexpr}=%s", (anchor, period))
    if n_dual:
        return []
    n_hist = _scalar(f"SELECT COUNT(*) AS n FROM `{table}` "
                     f"WHERE DATA_DT<>%s AND {bexpr}=%s", (anchor, period))
    if n_hist:
        return [f"⚠️ 业务日期 {period} 只存在于历史数据日期（早于 {anchor}）下，"
                f"最新快照 {anchor} 中不含该业务日期。请核对上游跑批是否漏跑该业务日期的数据。"]
    return [f"⚠️ 最新数据日期 {anchor} 下没有业务日期 {period} 的数据。"
            f"可用业务日期：{_join(_periods(table))}。"]


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
    """从库里动态取维度的全部可用值（带缓存）。

    中心维度（CORE / CORE_CAP）会剔除「合计」类码值：它是源表自带的小计行，
    既不是可选的中心，也不该出现在候选清单里。
    """
    if dim in _DIM_CACHE:
        return _DIM_CACHE[dim]
    table, col, _ = DIM_SRC[dim]
    tcond, targs = _exclude_total(col) if col == "CORE" else ("", [])
    where = f"`{col}` IS NOT NULL AND `{col}` <> ''" + (f" AND {tcond}" if tcond else "")
    _, rows = _query(f"SELECT DISTINCT `{col}` AS v FROM `{table}` "
                     f"WHERE {where} ORDER BY `{col}`", targs)
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
    "CUR_CATE": {
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

# 资本表的中心维度键是 CORE_CAP（独立表、独立候选池），别名与收入表共用一套
_ALIAS["CORE_CAP"] = _ALIAS["CORE"]

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


def _period_headline(period, table=None):
    """期次描述。双日期表同时点明「数据日期」与「业务日期」，避免口径歧义。"""
    if table in _DUAL_DATE_TABLES:
        anchor = _anchor_dt(table)
        if anchor and str(anchor) != str(period):
            return f"业务日期 {period}（数据日期 {anchor}）"
        return f"数据日期与业务日期同为 {anchor}"
    return f"数据期次 {period}"


def _calendar_progress(period):
    """日历进度（%）：业务日期已过天数 ÷ 全年天数。

    营业净收入没有源表序时进度列，用它作为「时间已过去多少」的统一基准，
    回答「完成率是否落后进度」时不必再让上层自己算。
    """
    try:
        d = datetime.strptime(str(period)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    days = 366 if (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else 365
    return round(d.timetuple().tm_yday / days * 100, 2)


def _pct(v, nd=2):
    """百分比格式化：None -> 无数据，避免把 NULL 说成 0%。"""
    if v is None:
        return "无数据"
    try:
        return f"{round(float(v), nd)}%"
    except (TypeError, ValueError):
        return "无数据"


# ==========================================================================
# 工具 1：元数据 / 可用维度（一切边界类问题的唯一出口）
# ==========================================================================
def list_dimensions(dimension="all"):
    dim = (DIM_ALIAS.get(str(dimension or "all").strip().lower()) or
           str(dimension or "all").strip().upper())
    want_all = str(dimension or "all").strip().lower() in ("all", "")
    if want_all:
        dim = "ALL"

    periods = _periods(T_INCOME)
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
    if dim in ("ALL", "CUR_CATE"):
        add("维度", "币种类别（CUR_CATE）",
            [CUR_LABEL.get(v, v) for v in _dim_values("CUR_CATE")])
    if dim in ("ALL", "PERIOD"):
        add("期次", "最新数据日期（DATA_DT）", [_anchor_dt(T_INCOME)])
        add("期次", "T3 收入-可查业务日期（BIZ_DT）", _periods(T_INCOME))
        add("期次", "T2 负债-可查业务日期（DATA_PRDC_DT）", _periods(T_LIABILITY))
        add("期次", "T1 资本-可查数据日期（DATA_DT）", _periods(T_CAPITAL))
    if dim in ("ALL", "METRIC"):
        add("指标", "T3 收入类指标", [f"{k}（{v[1]}）" for k, v in T3_METRIC.items()])
        add("指标", "T3 计划/目标口径（get_plan_completion_tool）",
            [f"{k}（{v[3]}）" for k, v in T3_PLAN.items()])
        add("指标", "T3 交叉矩阵口径（get_income_matrix_tool）",
            [f"{k}（{v[0]}）" for k, v in MATRIX_METRIC.items()])
        add("指标", "T3 发生额口径（get_income_flow_tool）",
            [f"{k}（{v[2]}）" for k, v in T3_FLOW.items()])
        add("指标", "T2 负债类指标", [f"{k}（{v[1]}）" for k, v in T2_METRIC.items()])
        add("指标", "对比基准 basis（预计算列）", list(T2_BASIS.keys()))
        add("指标", "增速列（预计算百分比）",
            [f"{lab}" for _, lab in T1_GROWTH.values()] +
            [f"负债{lab}" for _, lab in T2_GROWTH.values()])
    if dim in ("ALL", "METRIC", "META"):
        rows.append(["指标定义", "计划完成率",
                     "Σ实际累计 ÷ Σ计划（或分解目标），**加权**；任何聚合层级都不得对行内比率求平均"])
        rows.append(["指标定义", "缺口",
                     "计划/分解目标 − 实际累计；正数=尚未完成（非息口径的分母是分解目标，不是经营计划）"])
        rows.append(["指标定义", "日历进度",
                     "业务日期已过天数 ÷ 全年天数；营业净收入无源表序时进度列，用它作时间基准"])
        rows.append(["指标定义", "序时进度",
                     "源表预计算列（非息/息差各有），按目标加权到上层；与「完成率」含义不同"])
        rows.append(["指标定义", "当周 / 期间发生额",
                     "当周=源表 CURR_WEEK 列；期间发生额=本期累计−上一可用期次累计（YTD 相减）"])
        rows.append(["指标定义", "增速",
                     "源表预计算百分比列（环比/较年初/同比），直接读取，不做两期相减"])
        rows.append(["指标定义", "合计成本率",
                     "优先取源表 LIB_CLS='合计' 行的 AVG_COST_RATE；无对应合计行时按日均余额加权估算，"
                     "不是各行成本率的简单平均"])
    if dim in ("ALL", "META"):
        rows.append(["元数据", "数据表", f"{T_INCOME} / {T_CAPITAL} / {T_LIABILITY}"])
        rows.append(["元数据", "单位口径", "三表底层已统一为「万元」；对外统一折算为亿元"])
        rows.append(["元数据", "期次性质", "日频快照（每日一期）"])
        rows.append(["元数据", "双日期口径",
                     "T3（BIZ_DT）/ T2（DATA_PRDC_DT）：DATA_DT 恒取 MAX（最新快照），"
                     "业务日期取所查日期，未指定则取该快照下的最大值"])
        rows.append(["元数据", "两套期次", f"损益类 {_latest(T_INCOME)}；"f"资本/负债类 {_latest(T_LIABILITY)}"])
        rows.append(["元数据", "部门口径",
                     "金融市场部 = 全部中心（源数据即金融市场部口径，不含合计行）；"
                     "传「金融市场部」等字样会自动按全部门汇总并给出加权完成率"])

    if not rows:
        # 未知维度不能返回「OK + 空表」：调用方会以为查询成功但没有数据。
        valid = ["all"] + list(DIM_ALIAS)
        return _env("可查询范围与数据字典",
                    f"未知维度「{dimension}」。可用取值：{'、'.join(valid)}。",
                    UNIT_YI, table=None,
                    notes=["dimension 只接受上述取值；传 all 可一次看全。"],
                    result_state="NOT_FOUND", candidates={"dimension": valid})

    table = {"columns": ["类别", "名称", "可用值"], "rows": rows}

    summary = (f"{_period_headline(_latest(T_INCOME), T_INCOME)}（共 {len(periods)} 期）。"
               f"可查询 5 个中心、17 条业务线、4 档负债大类、12 档业务类别、3 档币种。")

    notes = [
        _unit_note_income(),
        "源数据为日频（每日一期）：T3 / T2 的数据日期恒取最新，业务日期未指定时取该快照下的最大值；"
        "所问日期无数据时会回落到最近可用期次并明确说明。",
        "零值（0）与「无数据（NULL）」是两种不同状态，不可混用。",
    ]
    return _env("可查询范围与数据字典", summary, UNIT_YI, table=table, notes=notes,
                stat_date=_latest(T_INCOME), shape="table",
                period_resolved=_latest(T_INCOME),
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

    dcond, dargs = _date_where(t, per)
    tcond, targs = _exclude_total("CORE")
    _, rows = _query(
        f"SELECT CORE, SUM(NET_OPR_INCOM) AS a, SUM(NOT_INT_INCOM) AS b, SUM(NET_INT) AS c "
        f"FROM `{t}` WHERE {dcond} AND {tcond} GROUP BY CORE ORDER BY a DESC",
        list(dargs) + targs)
    if not rows:
        return _env("收入总览", f"{_period_headline(per, t)} 无数据。", UNIT_YI,
                    notes=pnotes + _dual_date_note(t, per), result_state="NOT_FOUND",
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
    table = {"columns": ["中心", "营业净收入累计（亿元）", "非息收入累计（亿元）",
                         "息差累计（亿元）", "非息占营业比"],
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
    summary = (f"{_period_headline(per, t)}：全部门营业净收入累计（截止期末）{total_opr:,.2f} 亿元，"
               f"非息收入累计 {total_noti:,.2f} 亿元，息差累计 {total_neti:,.2f} 亿元。"
               f"共 {len(data)} 个中心有数据{shown_txt}。"
               f"营业净收入最高的是「{top['core']}」（{top['opr']:,.2f} 亿元）。")

    notes = [_unit_note_income()] + pnotes
    notes.append("三个收入指标均为「截止期末」年初至今累计值，不是当期发生额。")
    notes.append("「合计」为各中心同名列直接汇总，非由息差+非息推算。")
    return _env(f"各中心收入总览（{UNIT_YI}）", summary, UNIT_YI, table=table, chart=chart,
                notes=notes, stat_date=per, shape="table+chart", period_resolved=per)


# ==========================================================================
# 工具 3：收入排名（T3，覆盖面最广）
# ==========================================================================
def rank_income(metric="net_opr_incom", by="core", period=None, top_n=10, order="desc",
                core=None):
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

    # 中心过滤：用于「某个中心内部按业务线排名」，此前只能全局排名
    # 部门名（如「金融市场部」）命中组织映射时按中心集合过滤，不报 NOT_FOUND
    dept_name, dept_centers = _match_department(core)
    if dept_name:
        core = None
    core_v, core_c, core_st = _resolve(core, "CORE")
    if core_st == "AMBIGUOUS":
        return _env("收入排名", f"「{core}」匹配到多个中心，请确认。", UNIT_YI,
                    table=None, result_state="AMBIGUOUS", candidates={"CORE": core_c})
    if core_st == "NOT_FOUND":
        return _env("收入排名", f"库中无「{core}」这个中心。", UNIT_YI, table=None,
                    result_state="NOT_FOUND", candidates={"CORE": _dim_values("CORE")})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("收入排名", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    dirn = "DESC" if str(order or "desc").lower() != "asc" else "ASC"
    # 比率类指标按加权重算（2026-10-19 修口径）：此前 AVG(比率列) 会让计划体量小的
    # 业务线获得与大体量业务线相同的权重，中心级完成率因此偏离真值（79.97% vs 81.23%）。
    if kind == "money":
        vexpr = f"SUM(`{col}`)"
    else:
        num, den = RATE_WEIGHT_SQL.get(mk, (f"SUM(`{col}`)", "1"))
        vexpr = f"{num}/NULLIF({den},0)*100"
    dcond, dargs = _date_where(t, per)
    where, args = [dcond], list(dargs)
    dept_scope = ""
    if dept_name:
        if dept_centers == "ALL":
            dept_scope = f"范围【{dept_name}=全部中心】："
        else:
            ph = ",".join(["%s"] * len(dept_centers))
            where.append(f"CORE IN ({ph})"); args += list(dept_centers)
            dept_scope = f"范围【{dept_name}（{len(dept_centers)}个中心）】："
    elif core_v:
        where.append("CORE=%s"); args.append(core_v)
    tcond, targs = _exclude_total("CORE")
    where.append(tcond); args += targs
    _, rows = _query(
        f"SELECT `{group_col}` AS k, {vexpr} AS v FROM `{t}` "
        f"WHERE {' AND '.join(where)} GROUP BY `{group_col}` ORDER BY v {dirn}", args)
    if not rows:
        return _env("收入排名", f"{_period_headline(per, t)} 无数据。", UNIT_YI,
                    notes=pnotes + _dual_date_note(t, per),
                    result_state="NOT_FOUND",
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
    scope = dept_scope or (f"范围【CORE={core_v}】：" if core_v else "")

    if dirn == "DESC":
        summary = (f"{_period_headline(per, t)}，{scope}共 {total_n} 个{disp}有数据{shown_txt}。"
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
        summary = (f"{_period_headline(per, t)}，{scope}共 {total_n} 个{disp}有数据{shown_txt}。"
                   f"最低为「{top[0]}」（{_fmt_num(top[1], unit)}）。")
        if total_n > 1:
            summary += (f"次低为「{items[1][0]}」（{_fmt_num(items[1][1], unit)}），"
                        f"最高为「{items[-1][0]}」（{_fmt_num(items[-1][1], unit)}）。")
        if negs:
            summary += f"⚠️ 其中 {len(negs)} 个为**负值**。"

    notes = ([_unit_note_income()] if kind == "money"
             else ["完成率/序时进度为百分比，非金额，不做单位折算。",
                   "比率按加权口径重算（Σ实际÷Σ计划 / 目标加权），不是各行比率的简单平均。"]) + pnotes
    if core_v:
        notes.append(f"已限定中心「{core_v}」，排名只在该中心内的 {total_n} 个{disp}之间比较。")
    notes.append(f"统计口径基于全量 {total_n} 个{disp}，展示条数 {len(show)} 条，两者不同。")
    return _env(f"各{disp}{label}排名（{unit}）", summary, unit, table=table, chart=chart,
                notes=notes, stat_date=per, shape="table+chart", period_resolved=per,
                resolved=({"CORE": core_v} if core_v else {}))


# ==========================================================================
# 工具 4：收入明细（T3，锁定中心或业务线）
# ==========================================================================
def get_income_detail(core=None, biz_line=None, period=None):
    t = T_INCOME
    dept_name, dept_centers = _match_department(core)
    if dept_name:
        core = None                       # 部门命中：按部门口径（中心集合）取数，不再按单中心解析
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

    dcond, dargs = _date_where(t, per)
    where, args = [dcond], list(dargs)
    dept_desc = ""
    if dept_name:
        if dept_centers == "ALL":
            dept_desc = f"{dept_name}（全部中心）"
        else:
            ph = ",".join(["%s"] * len(dept_centers))
            where.append(f"CORE IN ({ph})"); args += list(dept_centers)
            dept_desc = f"{dept_name}（{len(dept_centers)}个中心）"
    elif core_v:
        where.append("CORE=%s"); args.append(core_v)
    if biz_v:
        where.append("BIZ_LINE_DESC=%s"); args.append(biz_v)
    tcond, targs = _exclude_total("CORE")
    where.append(tcond); args += targs
    w = " AND ".join(where)

    # 都没锁定时，退化为中心级汇总（避免一次吐出 85 行）
    if not core_v and not biz_v:
        _, rows = _query(
            f"SELECT CORE AS k, SUM(NET_OPR_INCOM) AS a, SUM(NOT_INT_INCOM) AS b, "
            f"SUM(NET_INT) AS c, SUM(NET_OPR_INCOM_PLAN) AS p, "
            f"SUM(NET_OPR_INCOM_PLAN)-SUM(NET_OPR_INCOM) AS gp, "
            f"SUM(NET_OPR_INCOM)/NULLIF(SUM(NET_OPR_INCOM_PLAN),0)*100 AS cr "
            f"FROM `{t}` WHERE {w} GROUP BY CORE ORDER BY a DESC", args)
        cols = ["中心/业务线", "营业净收入累计（亿元）", "非息收入累计（亿元）", "息差累计（亿元）",
                "经营计划（亿元）", "营业缺口（亿元）", "完成率"]
        tb = [[r["k"], _to_yi(r["a"], UNIT_WAN), _to_yi(r["b"], UNIT_WAN),
               _to_yi(r["c"], UNIT_WAN), _to_yi(r["p"], UNIT_WAN),
               _to_yi(r["gp"], UNIT_WAN),
               f"{round(float(r['cr']), 2)}%" if r["cr"] is not None else "无数据"]
              for r in rows]
        headline = ("「金融市场部」源数据即覆盖全部中心，已按全部门汇总，不含合计行。"
                    if dept_name else "本次未指定中心或业务线，已按中心级汇总返回")
    else:
        _, rows = _query(
            f"SELECT CORE, BIZ_LINE_DESC, NET_OPR_INCOM, NOT_INT_INCOM, NET_INT, "
            f"NET_OPR_INCOM_PLAN, NET_OPR_INCOM_PLAN-NET_OPR_INCOM AS GP, "
            f"NET_OPR_INCOM_COMP_RATE, NOT_INT_INCOM_UNPKG_TGT, "
            f"NOT_INT_INCOM_UNPKG_TGT-NOT_INT_INCOM AS NGP, NOT_INT_INCOM_COMP_RATE, "
            f"NOT_INT_INCOM_TM_PROG FROM `{t}` WHERE {w} "
            f"ORDER BY NET_OPR_INCOM DESC", args)
        cols = ["中心", "业务线", "营业净收入累计（亿元）", "非息收入累计（亿元）", "息差累计（亿元）",
                "经营计划（亿元）", "营业缺口（亿元）", "营业完成率",
                "非息分解目标（亿元）", "非息缺口（亿元）", "非息完成率", "非息序时进度"]
        tb = [[r["CORE"], r["BIZ_LINE_DESC"], _to_yi(r["NET_OPR_INCOM"], UNIT_WAN),
               _to_yi(r["NOT_INT_INCOM"], UNIT_WAN), _to_yi(r["NET_INT"], UNIT_WAN),
               _to_yi(r["NET_OPR_INCOM_PLAN"], UNIT_WAN), _to_yi(r["GP"], UNIT_WAN),
               f"{round(float(r['NET_OPR_INCOM_COMP_RATE']), 2)}%"
               if r["NET_OPR_INCOM_COMP_RATE"] is not None else "无数据",
               _to_yi(r["NOT_INT_INCOM_UNPKG_TGT"], UNIT_WAN), _to_yi(r["NGP"], UNIT_WAN),
               f"{round(float(r['NOT_INT_INCOM_COMP_RATE']), 2)}%"
               if r["NOT_INT_INCOM_COMP_RATE"] is not None else "无数据",
               f"{round(float(r['NOT_INT_INCOM_TM_PROG']), 2)}%"
               if r["NOT_INT_INCOM_TM_PROG"] is not None else "无数据"]
              for r in rows]
        headline = ""

    if not rows:
        return _env("收入明细", f"{_period_headline(per, t)} 下未匹配到任何记录。", UNIT_YI,
                    notes=pnotes + _dual_date_note(t, per), result_state="NOT_FOUND")

    scope = "、".join(f"{k}={v}" for k, v in resolved.items()) or "全部中心"
    if dept_name:
        scope = dept_desc
    total_opr = round(sum(r[2] for r in tb), 2)
    table = {"columns": cols, "rows": tb}

    # 全口径加权完成率：Σ实际 ÷ Σ计划（各中心计划规模不同，绝不能对各行完成率求平均）
    # 同时用 SQL 原始值修正 total_opr —— 中心级分支的 tb[2] 是非息收入列，
    # 旧代码直接 sum(tb[*][2]) 会把非息合计误标成「营业净收入累计」。
    if not core_v and not biz_v:
        tot_a = sum(r["a"] or 0 for r in rows)
        tot_p = sum(r["p"] or 0 for r in rows)
    else:
        tot_a = sum(r["NET_OPR_INCOM"] or 0 for r in rows)
        tot_p = sum(r["NET_OPR_INCOM_PLAN"] or 0 for r in rows)
    overall_cr = round(tot_a / tot_p * 100, 2) if tot_p else None
    total_opr = _to_yi(tot_a, UNIT_WAN)
    cr_txt = (f"，全口径计划完成率 {overall_cr:.2f}%（Σ实际÷Σ计划加权，"
              f"非各行完成率平均）" if overall_cr is not None else "")

    summary = (f"{_period_headline(per, t)}，范围【{scope}】：共 {len(tb)} 条记录，"
               f"营业净收入累计 {total_opr:,.2f} 亿元（截止期末）{cr_txt}。"
               + (f"（{headline}）" if headline else ""))

    notes = [_unit_note_income()] + pnotes
    notes.append("营业净收入/非息收入/息差均为「截止期末」年初至今累计值，不是当期发生额。")
    notes.append("营业缺口 = 经营计划 − 营业净收入累计；非息缺口 = 非息分解目标 − 非息收入累计，正数表示尚未完成。")
    notes.append("非息完成率 = 非息收入 ÷ 非息分解目标；序时进度 = 时间进度基准，两者含义不同。")
    return _env("收入明细", summary, UNIT_YI, table=table, notes=notes, stat_date=per,
                shape="table", period_resolved=per, resolved=resolved)


# ==========================================================================
# 工具 4.1：计划完成率 / 缺口（T3，三口径统一加权，2026-10-19 新增）
# ==========================================================================
def get_plan_completion(metric="net_opr_incom", by="core", core=None, period=None,
                        top_n=0, order="desc"):
    t = T_INCOME
    mk = str(metric or "net_opr_incom").strip().lower()
    if mk not in T3_PLAN:
        return _env("计划完成率", f"未知口径「{metric}」。", "%",
                    notes=["可用口径：" + "、".join(f"{k}（{v[3]}）" for k, v in T3_PLAN.items())],
                    result_state="NOT_FOUND", candidates={"metric": list(T3_PLAN.keys())})
    a_col, p_col, tm_col, label = T3_PLAN[mk]

    bk = str(by or "core").strip().lower()
    if bk not in ("core", "biz_line"):
        return _env("计划完成率", f"未知分组「{by}」。", "%",
                    notes=["by 只接受 core（按中心）或 biz_line（按业务线）。"],
                    result_state="NOT_FOUND", candidates={"by": ["core", "biz_line"]})
    group_col = "CORE" if bk == "core" else "BIZ_LINE_DESC"

    dept_name, dept_centers = _match_department(core)
    if dept_name:
        core = None
    core_v, core_c, core_st = _resolve(core, "CORE")
    if core_st == "AMBIGUOUS":
        return _env("计划完成率", f"「{core}」匹配到多个中心，请确认。", "%",
                    table=None, result_state="AMBIGUOUS", candidates={"CORE": core_c})
    if core_st == "NOT_FOUND":
        return _env("计划完成率", f"库中无「{core}」这个中心。", "%", table=None,
                    result_state="NOT_FOUND", candidates={"CORE": _dim_values("CORE")})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("计划完成率", "无法定位数据期次。", "%", notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    dcond, dargs = _date_where(t, per)
    where, args = [dcond], list(dargs)
    scope = "全部门"
    if dept_name:
        if dept_centers == "ALL":
            scope = f"{dept_name}（全部中心）"
        else:
            ph = ",".join(["%s"] * len(dept_centers))
            where.append(f"CORE IN ({ph})"); args += list(dept_centers)
            scope = f"{dept_name}（{len(dept_centers)}个中心）"
    elif core_v:
        where.append("CORE=%s"); args.append(core_v)
        scope = core_v
    tcond, targs = _exclude_total("CORE")
    where.append(tcond); args += targs

    tmw = f"SUM(`{tm_col}`*`{p_col}`)" if tm_col else "NULL"
    _, rows = _query(
        f"SELECT `{group_col}` AS k, SUM(`{a_col}`) AS a, SUM(`{p_col}`) AS p, "
        f"SUM(`{p_col}`)-SUM(`{a_col}`) AS gap, "
        f"SUM(`{a_col}`)/NULLIF(SUM(`{p_col}`),0)*100 AS cr, {tmw} AS tmw "
        f"FROM `{t}` WHERE {' AND '.join(where)} GROUP BY `{group_col}`", args)
    if not rows:
        return _env("计划完成率", f"{_period_headline(per, t)} 下未匹配到记录。", "%",
                    notes=pnotes + _dual_date_note(t, per), result_state="NOT_FOUND")

    cal = _calendar_progress(per)
    items = []
    for r in rows:
        a, p = float(r["a"] or 0), float(r["p"] or 0)
        tm = (round(float(r["tmw"]) / p, 2)
              if (tm_col and p and r["tmw"] is not None) else None)
        items.append({"k": r["k"], "a": a, "p": p, "gap": p - a,
                      "cr": round(a / p * 100, 2) if p else None, "tm": tm})
    sign = -1 if str(order or "desc").lower() != "asc" else 1
    items.sort(key=lambda x: (x["cr"] is None, sign * (x["cr"] or 0)))

    ta = sum(i["a"] for i in items)
    tp = sum(i["p"] for i in items)
    tcr = round(ta / tp * 100, 2) if tp else None
    ttm = None
    if tm_col and tp:
        num = sum((i["tm"] or 0) * i["p"] for i in items if i["tm"] is not None)
        den = sum(i["p"] for i in items if i["tm"] is not None)
        ttm = round(num / den, 2) if den else None

    disp = "中心" if bk == "core" else "业务线"
    cols = [disp, f"{label}累计（亿元）", "计划/目标（亿元）", "缺口（亿元）",
            "完成率（加权）", "日历进度", "完成率−日历进度"]
    if tm_col:
        cols.append("序时进度（源表加权）")

    def _line(name, a, p, cr, tm):
        row = [name, _to_yi(a, UNIT_WAN), _to_yi(p, UNIT_WAN), _to_yi(p - a, UNIT_WAN),
               _pct(cr), _pct(cal),
               (f"{round(cr - cal, 2)}pp" if (cr is not None and cal is not None) else "无数据")]
        if tm_col:
            row.append(_pct(tm))
        return row

    show = items[:top_n] if top_n and top_n > 0 else items
    tb = [_line(i["k"], i["a"], i["p"], i["cr"], i["tm"]) for i in show]
    if len(show) < len(items):
        tb.append(_line("合计（全量口径）", ta, tp, tcr, ttm))
    else:
        tb.append(_line("合计", ta, tp, tcr, ttm))

    diff = round(tcr - cal, 2) if (tcr is not None and cal is not None) else None
    if tcr is None:
        summary = (f"{_period_headline(per, t)}，范围【{scope}】：{label}的计划/分解目标缺失，"
                   f"无法计算完成率（实际累计 {_to_yi(ta, UNIT_WAN):,.2f} 亿元）。")
    else:
        if diff is None:
            gap_txt = ""
        elif diff < 0:
            gap_txt = f"，较日历进度 {cal}% **低 {abs(diff)} 个百分点**"
        elif diff > 0:
            gap_txt = f"，较日历进度 {cal}% 高 {diff} 个百分点"
        else:
            gap_txt = f"，与日历进度 {cal}% 持平"
        summary = (f"{_period_headline(per, t)}，范围【{scope}】：{label}累计 "
                   f"{_to_yi(ta, UNIT_WAN):,.2f} 亿元，计划/目标 {_to_yi(tp, UNIT_WAN):,.2f} 亿元，"
                   f"计划完成率 **{tcr:.2f}%**（Σ实际÷Σ计划加权），"
                   f"缺口 {_to_yi(tp - ta, UNIT_WAN):,.2f} 亿元{gap_txt}。"
                   f"共 {len(items)} 个{disp}。")
        rated = [i for i in items if i["cr"] is not None]
        if len(rated) > 1:
            best = max(rated, key=lambda x: x["cr"])
            worst = min(rated, key=lambda x: x["cr"])
            summary += f"完成率最高「{best['k']}」（{best['cr']:.2f}%），最低「{worst['k']}」（{worst['cr']:.2f}%）。"

    chart = _opt_hbar(f"{label}计划完成率（{per}）", [i["k"] for i in show],
                      [i["cr"] for i in show], "%", sign_color=False)

    notes = ["完成率为百分比，非金额，不做单位折算。",
             "完成率统一按加权口径计算：Σ实际累计 ÷ Σ计划（或分解目标），"
             "**不是**各行完成率的简单平均。",
             "缺口 = 计划/分解目标 − 实际累计，正数表示尚未完成。"]
    if tm_col:
        notes.append("「序时进度（源表加权）」为源表预计算列 NET_INT_TM_PROG / "
                     "NOT_INT_INCOM_TM_PROG 按目标加权的值，代表该口径到当前时点应完成的进度。")
    else:
        notes.append("营业净收入源表无「序时进度」列，此处以日历进度（已过天数÷全年天数）作为时间基准。")
    notes += pnotes
    return _env(f"{label}计划完成率与缺口", summary, "%", table={"columns": cols, "rows": tb},
                chart=chart, notes=notes, stat_date=per, shape="table+chart",
                period_resolved=per, resolved=({"CORE": core_v} if core_v else {}))


# ==========================================================================
# 工具 4.2：中心 × 业务线交叉矩阵（T3，2026-10-19 新增，替代原 SQL 兜底主场景）
# ==========================================================================
def get_income_matrix(metric="net_opr_incom", core=None, biz_line=None, period=None):
    t = T_INCOME
    mk = str(metric or "net_opr_incom").strip().lower()
    if mk not in MATRIX_METRIC:
        return _env("交叉矩阵", f"未知口径「{metric}」。", UNIT_YI,
                    notes=["可用口径：" + "、".join(f"{k}（{v[0]}）" for k, v in MATRIX_METRIC.items())],
                    result_state="NOT_FOUND", candidates={"metric": list(MATRIX_METRIC.keys())})
    label, kind = MATRIX_METRIC[mk]
    unit = UNIT_YI if kind == "money" else "%"

    dept_name, dept_centers = _match_department(core)
    if dept_name:
        core = None
    core_v, core_c, core_st = _resolve(core, "CORE")
    biz_v, biz_c, biz_st = _resolve(biz_line, "BIZ_LINE_DESC")
    for st, cands, dim, raw in ((core_st, core_c, "CORE", core),
                                (biz_st, biz_c, "BIZ_LINE_DESC", biz_line)):
        if st == "AMBIGUOUS":
            return _env("交叉矩阵", f"「{raw}」在 {dim} 下匹配到多个候选，请确认。", unit,
                        table=None, result_state="AMBIGUOUS", candidates={dim: cands})
        if st == "NOT_FOUND":
            return _env("交叉矩阵", f"库中无「{raw}」这个 {dim}。", unit, table=None,
                        result_state="NOT_FOUND", candidates={dim: _dim_values(dim)[:12]})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("交叉矩阵", "无法定位数据期次。", unit, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    dcond, dargs = _date_where(t, per)
    where, args = [dcond], list(dargs)
    if dept_name and dept_centers != "ALL":
        ph = ",".join(["%s"] * len(dept_centers))
        where.append(f"CORE IN ({ph})"); args += list(dept_centers)
    elif core_v:
        where.append("CORE=%s"); args.append(core_v)
    if biz_v:
        where.append("BIZ_LINE_DESC=%s"); args.append(biz_v)
    tcond, targs = _exclude_total("CORE")
    where.append(tcond); args += targs

    _, rows = _query(
        f"SELECT CORE, BIZ_LINE_DESC, NET_OPR_INCOM, NET_OPR_INCOM_PLAN, "
        f"NOT_INT_INCOM, NET_INT, NET_OPR_INCOM_CURR_WEEK "
        f"FROM `{t}` WHERE {' AND '.join(where)}", args)
    if not rows:
        return _env("交叉矩阵", f"{_period_headline(per, t)} 下未匹配到记录。", unit,
                    notes=pnotes + _dual_date_note(t, per), result_state="NOT_FOUND")

    col_map = {"net_opr_incom": "NET_OPR_INCOM", "not_int_incom": "NOT_INT_INCOM",
               "net_int": "NET_INT", "plan": "NET_OPR_INCOM_PLAN",
               "curr_week": "NET_OPR_INCOM_CURR_WEEK"}
    cells = {}          # (core, biz) -> [分子, 分母]
    for r in rows:
        k = (r["CORE"], r["BIZ_LINE_DESC"])
        num = float(r[col_map.get(mk, "NET_OPR_INCOM")] or 0)
        den = float(r["NET_OPR_INCOM_PLAN"] or 0)
        acc = cells.setdefault(k, [0.0, 0.0])
        acc[0] += num
        acc[1] += den

    cores = sorted({c for c, _ in cells})
    bizs = sorted({b for _, b in cells})

    def _v(num, den):
        if kind == "rate":
            return round(num / den * 100, 2) if den else None
        return _to_yi(num, UNIT_WAN)

    row_tot = {c: [sum(cells[(c, b)][0] for b in bizs if (c, b) in cells),
                   sum(cells[(c, b)][1] for b in bizs if (c, b) in cells)] for c in cores}
    col_tot = {b: [sum(cells[(c, b)][0] for c in cores if (c, b) in cells),
                   sum(cells[(c, b)][1] for c in cores if (c, b) in cells)] for b in bizs}
    grand = [sum(v[0] for v in row_tot.values()), sum(v[1] for v in row_tot.values())]

    cores.sort(key=lambda c: (row_tot[c][0] if kind == "money" else (_v(*row_tot[c]) or 0)),
               reverse=True)
    bizs.sort(key=lambda b: (col_tot[b][0] if kind == "money" else (_v(*col_tot[b]) or 0)),
              reverse=True)

    def _disp(x):
        return "无数据" if x is None else (_pct(x) if kind == "rate" else x)

    cols = ["中心"] + bizs + ["合计"]
    tb = []
    for c in cores:
        line = [c] + [_disp(_v(*cells[(c, b)]) if (c, b) in cells else None) for b in bizs]
        line.append(_disp(_v(*row_tot[c])))
        tb.append(line)
    tb.append(["合计"] + [_disp(_v(*col_tot[b])) for b in bizs] + [_disp(_v(*grand))])

    best = None
    for (c, b), (num, den) in cells.items():
        v = _v(num, den)
        if v is not None and (best is None or v > best[0]):
            best = (v, c, b)

    scope = []
    if dept_name:
        scope.append(dept_name)
    if core_v:
        scope.append(core_v)
    if biz_v:
        scope.append(biz_v)
    scope_txt = "、".join(scope) if scope else "全部门（5 个中心 × 17 条业务线）"
    summary = (f"{_period_headline(per, t)}，范围【{scope_txt}】：{label}交叉矩阵，"
               f"{len(cores)} 个中心 × {len(bizs)} 条业务线，合计 {_disp(_v(*grand))}"
               f"{unit if kind == 'money' else ''}。")
    if best:
        summary += (f"最高单元格为「{best[1]} × {best[2]}」"
                    f"（{_disp(best[0])}{unit if kind == 'money' else ''}）。")

    notes = ([_unit_note_income()] if kind == "money"
             else ["完成率按加权口径重算（Σ实际÷Σ计划），非各行比率平均。"]) + pnotes
    notes.append("单元格为「中心 × 业务线」的累计值（年初至今），不是当期发生额。")
    notes.append("「合计」行/列为加权汇总（比率口径为 Σ实际÷Σ计划），不是可见单元格之和。")
    return _env(f"{label}：中心 × 业务线矩阵", summary, unit,
                table={"columns": cols, "rows": tb}, notes=notes, stat_date=per,
                shape="table", period_resolved=per)


# ==========================================================================
# 工具 4.3：当周与期间发生额（T3，2026-10-19 新增）
# ==========================================================================
def get_income_flow(metric="net_opr_incom", by="core", core=None, period=None):
    t = T_INCOME
    mk = str(metric or "net_opr_incom").strip().lower()
    if mk not in T3_FLOW:
        return _env("当周与发生额", f"未知口径「{metric}」。", UNIT_YI,
                    notes=["可用口径：" + "、".join(f"{k}（{v[2]}）" for k, v in T3_FLOW.items())],
                    result_state="NOT_FOUND", candidates={"metric": list(T3_FLOW.keys())})
    a_col, w_col, label = T3_FLOW[mk]

    bk = str(by or "core").strip().lower()
    if bk not in ("core", "biz_line"):
        return _env("当周与发生额", f"未知分组「{by}」。", UNIT_YI,
                    notes=["by 只接受 core 或 biz_line。"],
                    result_state="NOT_FOUND", candidates={"by": ["core", "biz_line"]})
    group_col = "CORE" if bk == "core" else "BIZ_LINE_DESC"

    dept_name, dept_centers = _match_department(core)
    if dept_name:
        core = None
    core_v, core_c, core_st = _resolve(core, "CORE")
    if core_st == "AMBIGUOUS":
        return _env("当周与发生额", f"「{core}」匹配到多个中心，请确认。", UNIT_YI,
                    table=None, result_state="AMBIGUOUS", candidates={"CORE": core_c})
    if core_st == "NOT_FOUND":
        return _env("当周与发生额", f"库中无「{core}」这个中心。", UNIT_YI, table=None,
                    result_state="NOT_FOUND", candidates={"CORE": _dim_values("CORE")})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("当周与发生额", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})
    prev = _prev(t, per)

    dcond, dargs = _date_where(t, per)
    # 维度过滤单独存一份：上一期次要用同一组过滤条件，只是换日期，
    # 若从拼好的 where/args 里切片，双日期表的两个占位符会被错位（2026-10-19 修复）。
    filters, fargs = [], []
    if dept_name and dept_centers != "ALL":
        ph = ",".join(["%s"] * len(dept_centers))
        filters.append(f"CORE IN ({ph})"); fargs += list(dept_centers)
    elif core_v:
        filters.append("CORE=%s"); fargs.append(core_v)
    tcond, targs = _exclude_total("CORE")
    filters.append(tcond); fargs += targs
    w = " AND ".join([dcond] + filters)
    args = list(dargs) + fargs

    _, rows = _query(
        f"SELECT `{group_col}` AS k, SUM(`{a_col}`) AS a, SUM(`{w_col}`) AS w "
        f"FROM `{t}` WHERE {w} GROUP BY `{group_col}` ORDER BY a DESC", args)
    if not rows:
        return _env("当周与发生额", f"{_period_headline(per, t)} 下未匹配到记录。", UNIT_YI,
                    notes=pnotes + _dual_date_note(t, per), result_state="NOT_FOUND")

    prev_map = {}
    if prev:
        pcond, pargs = _date_where(t, prev)
        pw = " AND ".join([pcond] + filters)
        _, prows = _query(
            f"SELECT `{group_col}` AS k, SUM(`{a_col}`) AS a FROM `{t}` "
            f"WHERE {pw} GROUP BY `{group_col}`", list(pargs) + fargs)
        prev_map = {r["k"]: float(r["a"] or 0) for r in prows}

    disp = "中心" if bk == "core" else "业务线"
    cols = [disp, f"本期累计（{per}）", f"上期累计（{prev or '无'}）",
            "期间发生额（亿元）", "当周（亿元）"]
    tb, tot_a, tot_prev, tot_w = [], 0.0, 0.0, 0.0
    for r in rows:
        k = r["k"]
        a = float(r["a"] or 0)
        wk = float(r["w"] or 0)
        pv = prev_map.get(k)
        tot_a += a
        tot_w += wk
        if pv is not None:
            tot_prev += pv
        tb.append([k, _to_yi(a, UNIT_WAN),
                   _to_yi(pv, UNIT_WAN) if pv is not None else "无数据",
                   _to_yi(a - pv, UNIT_WAN) if pv is not None else "无数据",
                   _to_yi(wk, UNIT_WAN)])
    tb.append(["合计", _to_yi(tot_a, UNIT_WAN),
               _to_yi(tot_prev, UNIT_WAN) if prev_map else "无数据",
               _to_yi(tot_a - tot_prev, UNIT_WAN) if prev_map else "无数据",
               _to_yi(tot_w, UNIT_WAN)])

    # 期间发生额的时间跨度：业务日期不连续时（如 9/30 之后跳两周），必须点明天数，
    # 否则 17.24 亿元容易被读成「一个月挣的」（2026-10-19 补）。
    span = None
    if prev:
        try:
            span = (date.fromisoformat(str(per)) - date.fromisoformat(str(prev))).days
        except (TypeError, ValueError):
            span = None
    span_txt = f"，跨度 {span} 天" if span else ""

    flow_total = tot_a - tot_prev if prev_map else None
    summary = (f"{_period_headline(per, t)}，范围【{'全部门' if not core_v else core_v}】："
               f"{label}本期累计 {_to_yi(tot_a, UNIT_WAN):,.2f} 亿元，当周 {_to_yi(tot_w, UNIT_WAN):,.2f} 亿元"
               + (f"；较上期（{prev}）累计 {_to_yi(tot_prev, UNIT_WAN):,.2f} 亿元，"
                  f"期间发生额 {_to_yi(flow_total, UNIT_WAN):,.2f} 亿元{span_txt}"
                  if flow_total is not None else "；库中无上一期次，无法计算期间发生额")
               + f"。共 {len(rows)} 个{disp}。")

    chart = _opt_hbar(f"{label}期间发生额（{per}）",
                      [r[0] for r in tb[:-1]],
                      [r[3] if r[3] != "无数据" else 0 for r in tb[:-1]],
                      UNIT_YI)

    notes = [_unit_note_income()] + pnotes
    notes.append("「期间发生额」= 本期累计 − 上一可用期次累计（YTD 相减），"
                 + (f"本次覆盖 {prev} → {per} 共 {span} 天，" if span else "")
                 + "回答「这段时间挣了多少」用它，不要用累计值。")
    notes.append("「当周」为源表预计算列（NET_*_CURR_WEEK），是最新一周的发生额，"
                 "与期间发生额口径不同，两者不可直接相减或相加。")
    return _env(f"{label}当周与期间发生额", summary, UNIT_YI,
                table={"columns": cols, "rows": tb}, chart=chart, notes=notes,
                stat_date=per, shape="table+chart", period_resolved=per)


# ==========================================================================
# 工具 5：资本公积（T1）
# ==========================================================================
def get_capital_reserve(core=None, period=None, basis="none", growth=False, cur="rmb"):
    t = T_CAPITAL
    # 源表 CORE 可能混有「合计」码值：无论用户是否点名，聚合时都必须先排除，
    # 否则它会与 5 个中心一起被加总（翻倍）。
    # ⚠️ T1 当前实测**没有** CORE='合计' 行，因此「资本公积合计」需按各中心加总。
    only_total = _is_total(core)
    tcond, targs = _exclude_total("CORE", exclude=True)
    if only_total:
        core_v, core_c, core_st = None, [], "OK"     # 先取全部中心，稍后合成合计行
    else:
        core_v, core_c, core_st = _resolve(core, "CORE_CAP")
    if core_st == "AMBIGUOUS":
        return _env("资本公积", f"「{core}」匹配到多个中心，请确认。", UNIT_YI,
                    table=None, result_state="AMBIGUOUS", candidates={"CORE": core_c})
    if core_st == "NOT_FOUND":
        return _env("资本公积", f"库中无「{core}」这个中心。", UNIT_YI, table=None,
                    result_state="NOT_FOUND", candidates={"CORE": _dim_values("CORE_CAP")})

    # 对比基准的币种口径：rmb=人民币（默认）/ fx=外币（2026-10-19 新增）
    ck = str(cur or "rmb").strip().lower()
    if ck not in ("rmb", "fx"):
        return _env("资本公积", f"未知币种口径「{cur}」。", UNIT_YI,
                    notes=["cur 只接受 rmb（人民币，默认）或 fx（外币）。"],
                    result_state="NOT_FOUND", candidates={"cur": ["rmb", "fx"]})
    bmap = T1_BASIS_FX if ck == "fx" else T1_BASIS

    bk = str(basis or "none").strip().lower()
    if bk not in bmap:
        return _env("资本公积", f"未知对比基准「{basis}」。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"basis": list(bmap.keys())})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("资本公积", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    bcol, blabel = bmap[bk] or (None, None)
    # growth=True 时追加源表预计算的增速列（百分比，直接读取，不做两期相减）
    gitems = list(T1_GROWTH.items()) if growth else []
    sel = "CORE, BAL_RMB, BAL_FOREGIN, BAL_FOR_SELL" + (f", `{bcol}` AS bv" if bcol else "")
    sel += "".join(f", `{gcol}` AS `{gk}`" for gk, (gcol, _) in gitems)
    where, args = ["DATA_DT=%s"], [per]
    if tcond:
        where.append(tcond); args += targs
    if core_v:
        where.append("CORE=%s"); args.append(core_v)
    _, rows = _query(f"SELECT {sel} FROM `{t}` WHERE {' AND '.join(where)} "
                     f"ORDER BY BAL_RMB DESC", args)
    # 显式要「合计」：优先用源表合计行；源表没有该行（T1 现状）则按全部中心加总。
    total_source = None
    if only_total:
        _, tr = _query(
            f"SELECT CORE, BAL_RMB, BAL_FOREGIN, BAL_FOR_SELL"
            + (f", `{bcol}` AS bv" if bcol else "")
            + "".join(f", `{gcol}` AS `{gk}`" for gk, (gcol, _) in gitems)
            + f" FROM `{t}` WHERE DATA_DT=%s AND CORE=%s ORDER BY BAL_RMB DESC",
            [per, str(core).strip()])
        if tr:
            rows, total_source = tr, "源表合计行"
        elif rows:
            agg = {"CORE": "合计"}
            for k in ("BAL_RMB", "BAL_FOREGIN", "BAL_FOR_SELL"):
                agg[k] = sum(float(r[k] or 0) for r in rows)
            if bcol:
                agg["bv"] = sum(float(r["bv"] or 0) for r in rows)
            for gk, _ in gitems:
                agg[gk] = None                # 增速不做加总，合计行留空
            total_source = f"按 {len(rows)} 个中心加总，源表无合计行"
            rows = [agg]
    if not rows:
        return _env("资本公积", f"{_period_headline(per)} 下无匹配记录。", UNIT_YI,
                    notes=pnotes, result_state="NOT_FOUND")

    cols = ["中心", "人民币（亿元）", "外币（亿元）", "可供出售账户资产余额（亿元）"]
    if bcol:
        cols.append(f"{blabel}（亿元）")
    cols += [glab for _, (_, glab) in gitems]
    tb = []
    for r in rows:
        line = [r["CORE"], _to_yi(r["BAL_RMB"], UNIT_WAN), _to_yi(r["BAL_FOREGIN"], UNIT_WAN),
                _to_yi(r["BAL_FOR_SELL"], UNIT_WAN)]
        if bcol:
            line.append(_to_yi(r.get("bv"), UNIT_WAN))
        for gk, _ in gitems:
            gv = r.get(gk)
            line.append(round(float(gv), 2) if gv is not None else "无数据")
        tb.append(line)

    chart = _opt_vbar(f"各中心资本公积（{per}）", [r[0] for r in tb],
                      [{"name": "人民币", "data": [r[1] for r in tb], "color": "#C8102E"},
                       {"name": "外币", "data": [r[2] for r in tb], "color": "#2E5BFF"}],
                      UNIT_YI)

    tot = round(sum(r[1] for r in tb), 2)
    tot_fx = round(sum(float(r[2]) for r in tb if isinstance(r[2], (int, float))), 2)
    n_centers = len({r[0] for r in tb})
    dup_rows = len(tb) - n_centers
    # 摘要主口径跟随 cur：外币口径下先报外币合计，避免「人民币合计 948.66 亿」与
    # 「较上月末外币变动」两个口径并列造成误读（2026-10-19 修）。
    if only_total:
        summary = (f"{_period_headline(per)}：资本公积合计 {tot:,.2f} 亿元人民币"
                   + (f"、外币 {tot_fx:,.2f} 亿元" if ck == "fx" else "")
                   + (f"（{total_source}）" if total_source else "")
                   + (f"，对比口径：{blabel}。" if bcol else "。"))
    else:
        top = max(tb, key=lambda x: x[1] or 0)
        main = (f"外币资本公积合计 {tot_fx:,.2f} 亿元（各中心人民币合计 {tot:,.2f} 亿元）"
                if ck == "fx" else f"人民币资本公积合计 {tot:,.2f} 亿元")
        summary = (f"{_period_headline(per)}：{main}，"
                   f"共 {n_centers} 个中心。最高为「{top[0]}」（{top[1]:,.2f} 亿元人民币）。"
                   + (f" 对比口径：{blabel}。" if bcol else ""))

    notes = [_unit_note_cny()] + pnotes
    if not only_total:
        notes.append("源表中 CORE=「合计」的行是自带小计，已排除在外，不参与任何汇总，避免翻倍。")
    elif total_source and total_source.startswith("按"):
        notes.append(f"源表 CORE 中没有「合计」行，本次合计由各中心逐行加总得到"
                     f"（{total_source}），与「各中心相加」口径一致。")
    if dup_rows > 0:
        notes.append(f"⚠️ 同一中心出现多行（{len(tb)} 行 / {n_centers} 个中心，多出 {dup_rows} 行），"
                     f"合计值可能被重复计入。请核对源表该期次是否存在重复行。")
    notes.append("资本公积为可供出售账户口径，可为负值；负值表示浮亏，属正常业务现象。")
    if bcol:
        notes.append(f"「{blabel}」为源表**预计算列**，直接读取，未做两期相减（口径与报表一致）。")
    if gitems:
        notes.append("增速列为源表预计算百分比（环比 / 较年初 / 同比），直接读取，"
                     "**不是**由本期数据推算；单位是 %，不要写成亿元。")
    if ck == "fx":
        notes.append("本次对比基准取**外币**口径列（BAL_FOREGIN_VS_* / COMPARE_*_FOREGIN）；"
                     "「人民币（亿元）」「外币（亿元）」两列仍为存量余额，不受口径影响。")
    return _env(f"资本公积（{UNIT_YI}）", summary, UNIT_YI, table={"columns": cols, "rows": tb},
                chart=chart, notes=notes, stat_date=per, shape="table+chart",
                period_resolved=per,
                resolved=({"CORE": str(core).strip()} if only_total else
                          ({"CORE": core_v} if core_v else {})))


# ==========================================================================
# 工具 6：负债总览（T2，零值语义主战场）
# ==========================================================================
def get_liability_overview(lib_cls="合计", cur="", period=None, basis="none",
                           group_by="detail"):
    t = T_LIABILITY
    lib_v, lib_c, lib_st = _resolve(lib_cls, "LIB_CLS")
    # 「合计/全部/空」= 不筛选币种；否则按 CUR_CATE 筛选
    cur_raw = None if str(cur or "").strip() in ("", "合计", "全部", "总计") else cur
    cur_v, cur_c, cur_st = _resolve(cur_raw, "CUR_CATE")

    for st, c, dim, raw in ((lib_st, lib_c, "LIB_CLS", lib_cls),
                            (cur_st, cur_c, "CUR_CATE", cur_raw)):
        if st == "AMBIGUOUS":
            return _env("负债总览", f"「{raw}」在 {dim} 下匹配到多个候选，请确认。", UNIT_YI,
                        table=None, result_state="AMBIGUOUS", candidates={dim: c})
        if st == "NOT_FOUND":
            return _env("负债总览", f"库中无「{raw}」这个 {dim}。", UNIT_YI, table=None,
                        result_state="NOT_FOUND", candidates={dim: _dim_values(dim)})

    if lib_v is None:
        lib_v = "合计"

    # group_by：detail=逐条明细（默认）/ cur=按币种汇总（2026-10-19 新增，
    # 回答「本币和外币各占多少」不必再自己把明细按币种加总）
    gk = str(group_by or "detail").strip().lower()
    if gk not in ("detail", "cur"):
        return _env("负债总览", f"未知分组方式「{group_by}」。", UNIT_YI,
                    notes=["group_by 只接受 detail（明细，默认）或 cur（按币种汇总）。"],
                    result_state="NOT_FOUND", candidates={"group_by": ["detail", "cur"]})

    bk = str(basis or "none").strip().lower()
    if bk not in T2_BASIS:
        return _env("负债总览", f"未知对比基准「{basis}」。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"basis": list(T2_BASIS.keys())})

    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("负债总览", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    bcol, blabel = T2_BASIS[bk] or (None, None)
    basis_is_rate = bool(bcol) and bcol in RATE_BASIS_COLS
    dcond, dargs = _date_where(t, per)
    bsel = f", `{bcol}` AS bv" if bcol else ""
    tsel = f"ACCT_BAL_CNY, ACCT_BAL_CNY_PER, YR_DAY_AVG_BAL_CNY, AVG_COST_RATE{bsel}"

    # 源表「合计」行 = 全表权威口径（成本率直接取源值，不做加权估算）
    _, trs = _query(f"SELECT {tsel} FROM `{t}` WHERE {dcond} AND LIB_CLS='合计' "
                    f"AND PROD_NM='合计'", dargs)
    src_total = trs[0] if trs else None

    # 单个大类的汇总行（如「同业负债」整体），用于该类别的合计行
    src_lib = None
    if lib_v != "合计":
        _, lrs = _query(f"SELECT {tsel} FROM `{t}` WHERE {dcond} AND LIB_CLS=%s "
                        f"AND PROD_NM='合计'", list(dargs) + [lib_v])
        src_lib = lrs[0] if lrs else None

    if gk == "cur":
        # 按币种汇总：取原始行在 Python 里聚合（表很小），原因有二——
        #   ① 本币/外币司库只有「合计」行，SQL 直接排除 PROD_NM='合计' 会漏掉它们；
        #   ② 需按「有明细才排除汇总行」的规则去重，SQL 里不好表达。
        where, args = [dcond, "PROD_NM NOT LIKE %s"], list(dargs) + ["其中%"]
        if lib_v == "合计":
            where.append("LIB_CLS<>'合计'")
        else:
            where.append("LIB_CLS=%s"); args.append(lib_v)
        if cur_v:
            where.append("CUR_CATE=%s"); args.append(cur_v)
        sel = (f"LIB_CLS, PROD_NM, CUR_CATE, ACCT_BAL_CNY, ACCT_BAL_CNY_PER, "
               f"YR_DAY_AVG_BAL_CNY, AVG_COST_RATE{bsel}")
        _, raw_rows = _query(f"SELECT {sel} FROM `{t}` WHERE {' AND '.join(where)}", args)
        has_detail = {r["LIB_CLS"] for r in raw_rows if r["PROD_NM"] != "合计"}
        agg = {}
        for r in raw_rows:
            if r["PROD_NM"] == "合计" and r["LIB_CLS"] in has_detail:
                continue                      # 已有明细行，汇总行不参与，避免翻倍
            a = agg.setdefault(r["CUR_CATE"], {"bal": 0.0, "per": 0.0, "avg": 0.0,
                                               "cw": 0.0, "aw": 0.0, "bv": 0.0, "bvw": 0.0})
            avg = float(r["YR_DAY_AVG_BAL_CNY"] or 0)
            a["bal"] += float(r["ACCT_BAL_CNY"] or 0)
            a["per"] += float(r["ACCT_BAL_CNY_PER"] or 0)
            a["avg"] += avg
            if r["AVG_COST_RATE"] is not None:
                a["cw"] += float(r["AVG_COST_RATE"]) * avg
                a["aw"] += avg
            if bcol and r.get("bv") is not None:
                a["bv"] += float(r["bv"])
                a["bvw"] += float(r["bv"]) * avg
        rows = []
        for c, a in agg.items():
            rows.append({
                "g": c, "bal": a["bal"], "per": a["per"], "avg_bal": a["avg"],
                "cost": (round(a["cw"] / a["aw"], 4) if a["aw"] else None),
                "bv": ((round(a["bvw"] / a["aw"], 4) if a["aw"] else None)
                       if basis_is_rate else (a["bv"] if bcol else None))})
        rows.sort(key=lambda x: x["bal"], reverse=True)
    elif lib_v == "合计":
        # 构成：同业负债 / 本币司库 / 外币司库（读 PROD_NM='合计' 的汇总行）
        sel = f"LIB_CLS, PROD_NM, {tsel}"
        _, rows = _query(
            f"SELECT {sel} FROM `{t}` WHERE {dcond} AND PROD_NM='合计' AND LIB_CLS<>'合计' "
            f"ORDER BY ACCT_BAL_CNY DESC", dargs)
    else:
        sel = f"LIB_CLS, PROD_NM, CUR_CATE, {tsel}"
        where, args = [dcond, "LIB_CLS=%s"], list(dargs) + [lib_v]
        if cur_v:
            where.append("CUR_CATE=%s"); args.append(cur_v)
        _, rows = _query(f"SELECT {sel} FROM `{t}` WHERE {' AND '.join(where)} "
                         f"ORDER BY ACCT_BAL_CNY DESC", args)
        # 有明细行时排除汇总行（本币司库/外币司库只有汇总行，需保留）
        if len(rows) > 1:
            rows = [r for r in rows if r["PROD_NM"] != "合计"]

    if not rows:
        return _env("负债总览", f"{_period_headline(per, t)}：负债大类「{lib_v}」"
                    + (f"、币种「{cur_v}」" if cur_v else "")
                    + "下无匹配记录。", UNIT_YI,
                    notes=pnotes + _dual_date_note(t, per)
                    + ["如果该维度本应有数据，请检查 lib_cls / cur 的取值。"],
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    # 统一成内部结构，供三种呈现方式共用
    items = []
    if gk == "cur":
        for r in rows:
            items.append({
                "k": CUR_LABEL.get(r["g"], r["g"]), "c2": None, "cur": None,
                "bal": r["bal"], "per": r["per"], "avg": r["avg_bal"],
                "cost": r["cost"], "bv": r.get("bv")})
    else:
        for r in rows:
            items.append({
                "k": r["LIB_CLS"], "c2": r["PROD_NM"], "cur": r.get("CUR_CATE"),
                "bal": r["ACCT_BAL_CNY"], "per": r["ACCT_BAL_CNY_PER"],
                "avg": r["YR_DAY_AVG_BAL_CNY"], "cost": r["AVG_COST_RATE"],
                "bv": r.get("bv")})

    # 合计行：优先取源表汇总行（权威口径），没有对应汇总行时才自行聚合
    if not cur_v:
        tot_src = src_total if lib_v == "合计" else src_lib
    else:
        tot_src = None

    # 合计聚合时排除「其中：」子项（它是所属产品的子集，计入会翻倍）
    real_items = [i for i in items if not str(i["c2"] or "").startswith("其中")]
    sum_bal = sum(float(i["bal"] or 0) for i in real_items)
    sum_per = sum(float(i["per"]) for i in real_items if i["per"] is not None)
    sum_avg = sum(float(i["avg"] or 0) for i in real_items)
    w_den = sum(float(i["avg"] or 0) for i in real_items if i["cost"] is not None)
    w_num = sum(float(i["cost"] or 0) * float(i["avg"] or 0)
                for i in real_items if i["cost"] is not None)
    calc_cost = round(w_num / w_den, 4) if w_den else None
    if bcol and basis_is_rate:
        bv_den = sum(float(i["avg"] or 0) for i in real_items if i["bv"] is not None)
        bv_num = sum(float(i["bv"] or 0) * float(i["avg"] or 0)
                     for i in real_items if i["bv"] is not None)
        sum_bv = round(bv_num / bv_den, 4) if bv_den else None
    else:
        sum_bv = (sum(float(i["bv"] or 0) for i in real_items if i["bv"] is not None)
                  if bcol else None)

    if tot_src:
        t_bal, t_per = tot_src["ACCT_BAL_CNY"], tot_src["ACCT_BAL_CNY_PER"]
        t_avg, t_cost = tot_src["YR_DAY_AVG_BAL_CNY"], tot_src["AVG_COST_RATE"]
        t_bv = tot_src.get("bv") if bcol else None
        cost_from_src = True
    else:
        t_bal, t_per, t_avg, t_cost = sum_bal, sum_per, sum_avg, calc_cost
        t_bv = sum_bv
        cost_from_src = False

    cols = (["币种"] if gk == "cur" else ["负债大类", "业务类别"]
            + ([] if lib_v == "合计" else ["币种"])) + \
           ["余额（亿元）", "余额占比", "年均余额（亿元）", "成本率"]
    if bcol:
        cols.append(f"{blabel}（百分点）" if basis_is_rate else f"{blabel}（亿元）")

    def _vals(bal, per_, avg, cost, bv):
        line = [_to_yi(bal, UNIT_WAN),
                f"{round(float(per_), 2)}%" if per_ is not None else "无数据",
                _to_yi(avg, UNIT_WAN),
                round(float(cost), 2) if cost is not None else "无数据"]
        if bcol:
            if bv is None:
                line.append("无数据")
            elif basis_is_rate:
                line.append(round(float(bv), 2))      # 百分点，原值直出，勿折算
            else:
                line.append(_to_yi(bv, UNIT_WAN))
        return line

    tb = []
    for i in items:
        head = ([i["k"]] if gk == "cur"
                else [i["k"], i["c2"]] + ([] if lib_v == "合计" else [i["cur"]]))
        tb.append(head + _vals(i["bal"], i["per"], i["avg"], i["cost"], i["bv"]))
    # 只有一行（如本币/外币司库本身就是一个汇总行）时不再追加合计，避免重复
    if len(items) > 1:
        tb.append((["合计"] if gk == "cur" else ["合计", "—"] +
                   ([] if lib_v == "合计" else ["合计"]))
                  + _vals(t_bal, t_per, t_avg, t_cost, t_bv))

    # 三态判定：真实为零 vs 无数据
    vals = [_to_yi(i["bal"], UNIT_WAN) for i in items]
    zero_names = [i["k"] if gk == "cur" else i["c2"] for i, v in zip(items, vals) if v == 0]
    state = "ZERO" if vals and all(v == 0 for v in vals) else "OK"

    def _label(i):
        if gk == "cur" or lib_v == "合计":
            return i["k"]
        return i["k"] if i["c2"] in (None, "合计") else i["c2"]

    items_chart = [(_label(i), v) for i, v in zip(items, vals) if v is not None]
    chart = _opt_pie(f"负债构成（{per}）", items_chart, UNIT_YI)

    total_yi = _to_yi(t_bal, UNIT_WAN) if t_bal is not None else round(sum(v or 0 for v in vals), 2)
    top_i = max(range(len(vals)), key=lambda x: (vals[x] is not None, vals[x]))
    top_name = _label(items[top_i])
    if lib_v == "合计" and gk != "cur":
        summary = (f"{_period_headline(per, t)}：负债合计 {total_yi:,.2f} 亿元，"
                   f"构成为 {len(items)} 项，最大为「{top_name}」"
                   f"（{vals[top_i]:,.2f} 亿元）。")
    elif gk == "cur":
        summary = (f"{_period_headline(per, t)}："
                   + (f"「{lib_v}」" if lib_v != "合计" else "全部负债")
                   + f"按币种汇总，共 {len(items)} 个币种，合计 {total_yi:,.2f} 亿元，"
                   f"最大为「{top_name}」（{vals[top_i]:,.2f} 亿元）。")
    else:
        summary = (f"{_period_headline(per, t)}：「{lib_v}」余额 {total_yi:,.2f} 亿元"
                   + (f"（币种 {cur_v}）" if cur_v else "")
                   + f"，下辖 {len(items)} 条明细，最大为「{top_name}」"
                   f"（{vals[top_i]:,.2f} 亿元）。")
    if t_cost is not None:
        summary += (f"整体成本率 {round(float(t_cost), 2)}%"
                    + ("（源表合计行）。" if cost_from_src else "（按日均余额加权估算）。"))

    notes = [_unit_note_cny()] + pnotes
    if zero_names:
        notes.append(f"⚠️ 三态提示：【{'、'.join(zero_names)}】余额为 0，这是**真实业务事实**"
                     "（负债已全部退出），**不是**「无数据」。请勿表述为「无数据」或「减少 0 亿」。")
    if state == "ZERO":
        notes.append("本次查询结果全部为 0，属 ZERO 态（真实为零），非 NOT_FOUND。")
    notes.append("「其中」类子项（如其中：结算性存款）不计入合计，避免重复计算。")
    notes.append("末行「合计」：有源表汇总行时直接读取（含成本率），"
                 "否则按当前可见行聚合，成本率按**日均余额**加权估算。")
    if gk == "cur":
        notes.append("「按币种汇总」已排除各大类的合计行与「其中：」子项，"
                     "口径为逐条产品相加；余额占比是相对全部负债。")
    if bcol:
        notes.append(f"「{blabel}」为源表**预计算列**，直接读取，未做两期相减。")
        if basis_is_rate:
            notes.append(f"「{blabel}」单位是**百分点**（差值），不是亿元；正值=成本率上升，"
                         "负值=下降。回答时务必带上「个百分点」，不要写「亿元」。")
        if gk == "cur" and basis_is_rate:
            notes.append("分币种的成本率变动按**日均余额加权**估算；末行「合计」取源表合计行原值，"
                         "两者可能略有出入，对外以合计行为准。")
    return _env(f"负债总览（{UNIT_YI}）", summary, UNIT_YI, table={"columns": cols, "rows": tb},
                chart=chart, notes=notes, stat_date=per, shape="table+chart",
                period_resolved=per, result_state=state)


# ==========================================================================
# 工具 6.1：负债交叉明细（T2，大类 × 币种 × 产品 + 增速，2026-10-19 新增）
# ==========================================================================
def get_liability_detail(lib_cls="合计", cur="", period=None, top_n=0, growth=True,
                         vs="none"):
    t = T_LIABILITY
    lib_v, lib_c, lib_st = _resolve(lib_cls, "LIB_CLS")
    cur_raw = None if str(cur or "").strip() in ("", "合计", "全部", "总计") else cur
    cur_v, cur_c, cur_st = _resolve(cur_raw, "CUR_CATE")

    # vs：可选追加「较上月末 / 较上周五」三件套（余额、年均余额、成本率）
    vk = str(vs or "none").strip().lower()
    if vk not in ("none", "last_mth_end", "last_fri"):
        return _env("负债明细", f"未知对比基准「{vs}」。", UNIT_YI,
                    notes=["vs 只接受 none（默认）/ last_mth_end（较上月末）/ last_fri（较上周五）。"],
                    result_state="NOT_FOUND",
                    candidates={"vs": ["none", "last_mth_end", "last_fri"]})
    dcols = T2_DELTA.get(vk) if vk != "none" else None

    for st, cands, dim, raw in ((lib_st, lib_c, "LIB_CLS", lib_cls),
                                (cur_st, cur_c, "CUR_CATE", cur_raw)):
        if st == "AMBIGUOUS":
            return _env("负债明细", f"「{raw}」在 {dim} 下匹配到多个候选，请确认。", UNIT_YI,
                        table=None, result_state="AMBIGUOUS", candidates={dim: cands})
        if st == "NOT_FOUND":
            return _env("负债明细", f"库中无「{raw}」这个 {dim}。", UNIT_YI, table=None,
                        result_state="NOT_FOUND", candidates={dim: _dim_values(dim)})

    # 「合计/全部」= 不限定大类，返回全量产品明细
    only_all = lib_v in (None, "", "合计")
    per, pnotes, pst = _resolve_period(period, t)
    if pst != "OK":
        return _env("负债明细", "无法定位数据期次。", UNIT_YI, notes=pnotes,
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

    dcond, dargs = _date_where(t, per)
    where, args = [dcond], list(dargs)
    if only_all:
        where.append("LIB_CLS<>'合计'")
    else:
        where.append("LIB_CLS=%s"); args.append(lib_v)
    if cur_v:
        where.append("CUR_CATE=%s"); args.append(cur_v)

    gsel = ""
    if growth:
        gsel = (f", `{T2_GROWTH['mth'][0]}` AS g_mth, `{T2_GROWTH['yr'][0]}` AS g_yr, "
                f"`{T2_GROWTH['avg_mth'][0]}` AS g_amth, "
                f"`{T2_GROWTH['avg_yr'][0]}` AS g_ayr")
    vsel = ""
    if dcols:
        vsel = f", `{dcols[0]}` AS d_bal, `{dcols[1]}` AS d_avg, `{dcols[2]}` AS d_cost"
    _, rows = _query(
        f"SELECT LIB_CLS, THD_CTLG_CD, PROD_NM, CUR_CATE, ACCT_BAL_CNY, ACCT_BAL_CNY_PER, "
        f"YR_DAY_AVG_BAL_CNY, AVG_COST_RATE{gsel}{vsel} FROM `{t}` "
        f"WHERE {' AND '.join(where)} ORDER BY ACCT_BAL_CNY DESC", args)
    if not rows:
        return _env("负债明细", f"{_period_headline(per, t)} 下未匹配到记录。", UNIT_YI,
                    notes=pnotes + _dual_date_note(t, per), result_state="NOT_FOUND")

    # 有大类汇总行时排除之（PROD_NM='合计' 是自带小计，与明细一起加总会翻倍）；
    # 本币司库 / 外币司库只有汇总行，必须保留。
    # ⚠️ 判定要**按大类**逐组做：全局一刀切会把只有汇总行的司库整块丢掉
    #    （2026-10-19 实测：合计因此少了 184.47 亿元）。
    _order = {id(r): i for i, r in enumerate(rows)}
    _by_cls = {}
    for r in rows:
        _by_cls.setdefault(r["LIB_CLS"], []).append(r)
    keep = []
    for _rs in _by_cls.values():
        _detail = [r for r in _rs if r["PROD_NM"] != "合计"]
        keep.extend(_detail if _detail else _rs)
    keep.sort(key=lambda r: _order[id(r)])

    cols = ["负债大类", "产品编码", "业务类别", "币种", "余额（亿元）", "余额占比",
            "年均余额（亿元）", "成本率"]
    if growth:
        cols += [T2_GROWTH["mth"][1], T2_GROWTH["yr"][1],
                 T2_GROWTH["avg_mth"][1], T2_GROWTH["avg_yr"][1]]
    if dcols:
        cols += [f"余额{dcols[3]}（亿元）", f"年均余额{dcols[3]}（亿元）",
                 f"成本率{dcols[3]}（百分点）"]
    show = keep[:top_n] if top_n and top_n > 0 else keep

    def _cur(c):
        return CUR_LABEL.get(c, c)

    def _n(v, nd=2):
        return round(float(v), nd) if v is not None else "无数据"

    def _m(v):
        return _to_yi(v, UNIT_WAN) if v is not None else "无数据"

    tb = []
    for r in show:
        line = [r["LIB_CLS"], r["THD_CTLG_CD"], r["PROD_NM"], _cur(r["CUR_CATE"]),
                _to_yi(r["ACCT_BAL_CNY"], UNIT_WAN),
                _pct(r["ACCT_BAL_CNY_PER"]),
                _to_yi(r["YR_DAY_AVG_BAL_CNY"], UNIT_WAN),
                round(float(r["AVG_COST_RATE"]), 4) if r["AVG_COST_RATE"] is not None else "无数据"]
        if growth:
            line += [_n(r["g_mth"]), _n(r["g_yr"]), _n(r["g_amth"]), _n(r["g_ayr"])]
        if dcols:
            line += [_m(r["d_bal"]), _m(r["d_avg"]), _n(r["d_cost"])]
        tb.append(line)

    # 合计：只累加真实产品行，跳过「其中：」类子项，避免重复计入
    real = [r for r in keep if not str(r["PROD_NM"]).startswith("其中")]
    tot = sum(float(r["ACCT_BAL_CNY"] or 0) for r in real)
    up = sum(1 for r in real if growth and r["g_mth"] is not None and float(r["g_mth"]) > 0)
    down = sum(1 for r in real if growth and r["g_mth"] is not None and float(r["g_mth"]) < 0)
    top = real[0] if real else None

    scope = "全部负债" if only_all else lib_v
    if cur_v:
        scope += f" / 币种 {_cur(cur_v)}"
    summary = (f"{_period_headline(per, t)}，范围【{scope}】：共 {len(keep)} 条产品明细"
               f"（展示 {len(show)} 条），余额合计 {_to_yi(tot, UNIT_WAN):,.2f} 亿元。")
    if top:
        summary += f"最大为「{top['PROD_NM']}（{_cur(top['CUR_CATE'])}）」（{_to_yi(top['ACCT_BAL_CNY'], UNIT_WAN):,.2f} 亿元）。"
    if growth and (up or down):
        summary += f"环比上升 {up} 条、下降 {down} 条。"

    chart = _opt_hbar(f"负债产品余额（{per}）", [f"{r['PROD_NM']}（{_cur(r['CUR_CATE'])}）" for r in show],
                      [_to_yi(r["ACCT_BAL_CNY"], UNIT_WAN) for r in show], UNIT_YI,
                      sign_color=False)

    notes = [_unit_note_cny()] + pnotes
    notes.append("「产品编码」为源表三级目录码（THD_CTLG_CD），与业务类别一一对应。")
    notes.append("「其中：」类子项（如其中：结算性存款）不计入合计，避免重复计算。")
    if len(keep) < len(rows):
        notes.append(f"已排除各大类的「合计」汇总行（PROD_NM=合计）{len(rows) - len(keep)} 行，"
                     "避免与产品明细重复加总；只提供汇总行的大类（如本币/外币司库）予以保留。")
    if growth:
        notes.append("增速列为源表预计算百分比（余额环比/同比、年均余额环比/同比），"
                     "直接读取，不做两期相减。")
    if dcols:
        notes.append(f"「{dcols[3]}」三列为源表预计算差值：余额与年均余额是**亿元**，"
                     "成本率是**百分点**（不是亿元，勿折算）。")
    return _env("负债交叉明细", summary, UNIT_YI, table={"columns": cols, "rows": tb},
                chart=chart, notes=notes, stat_date=per, shape="table+chart",
                period_resolved=per)


# ==========================================================================
# 工具 7：负债产品排名（T2）
# ==========================================================================
def rank_liability_products(lib_cls="同业负债", metric="acct_bal", period=None,
                            top_n=10, order="desc", cur=None):
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
    # 2026-10-19 新增币种筛选：此前「外币口径下哪类产品成本率最高」只能靠 SQL 兜底
    cur_raw = None if str(cur or "").strip() in ("", "合计", "全部", "总计") else cur
    cur_v, cur_c, cur_st = _resolve(cur_raw, "CUR_CATE")
    if cur_st == "AMBIGUOUS":
        return _env("负债产品排名", f"「{cur_raw}」匹配到多个币种，请确认。", UNIT_YI,
                    result_state="AMBIGUOUS", candidates={"CUR_CATE": cur_c})
    if cur_st == "NOT_FOUND":
        return _env("负债产品排名", f"库中无「{cur_raw}」这个币种。", UNIT_YI,
                    result_state="NOT_FOUND", candidates={"CUR_CATE": _dim_values("CUR_CATE")})
    dcond, dargs = _date_where(t, per)
    wsql, wargs = [dcond, "LIB_CLS=%s", "PROD_NM<>'合计'"], list(dargs) + [lib_v]
    if cur_v:
        wsql.append("CUR_CATE=%s"); wargs.append(cur_v)
    _, rows = _query(
        f"SELECT PROD_NM AS k, CUR_CATE AS c, `{col}` AS v FROM `{t}` "
        f"WHERE {' AND '.join(wsql)} ORDER BY v {dirn}", wargs)
    if not rows:
        # 司库类在源表只有一行汇总（PROD_NM='合计'），没有产品级明细。
        # 直接说「无明细」会被读成「没有数据」，必须点明原因并给出替代工具。
        n_sum = _scalar(f"SELECT COUNT(*) AS n FROM `{t}` WHERE {dcond} "
                        f"AND LIB_CLS=%s AND PROD_NM='合计'", list(dargs) + [lib_v]) or 0
        if n_sum:
            bal = _scalar(f"SELECT ACCT_BAL_CNY FROM `{t}` WHERE {dcond} "
                          f"AND LIB_CLS=%s AND PROD_NM='合计'", list(dargs) + [lib_v])
            return _env(
                "负债产品排名",
                f"{_period_headline(per, t)}：「{lib_v}」在源表中只有一行汇总"
                f"（余额 {_to_yi(bal, UNIT_WAN):,.2f} 亿元），**没有产品级明细**，无法排名。"
                f"看规模与成本率请用 get_liability_overview_tool(lib_cls='{lib_v}')。",
                UNIT_YI, notes=pnotes + _dual_date_note(t, per) + [
                    "该大类不是「无数据」，而是源表只提供汇总口径；"
                    "产品级排名仅适用于「同业负债」。"],
                result_state="NOT_FOUND", candidates={"lib_cls": ["同业负债"]})
        return _env("负债产品排名", f"{_period_headline(per, t)}：负债大类「{lib_v}」下无明细。",
                    UNIT_YI, notes=pnotes + _dual_date_note(t, per),
                    result_state="NOT_FOUND", candidates={"可用期次": _periods(t)})

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
    summary = (f"{_period_headline(per, t)}：负债大类「{lib_v}」共 {total_n} 个业务类别有数据"
               f"{shown_txt}。{label}最高为「{items[0][0]}」（{_fmt_num(items[0][1], unit)}）。")

    notes = ([_unit_note_cny()] if kind == "money"
             else ["占比/成本率为百分比，不做单位折算。"]) + pnotes
    notes.append(f"统计口径基于全量 {total_n} 个业务类别，展示 {len(show)} 条，两者不同。")
    if cur_v:
        notes.append(f"已限定币种 {CUR_LABEL.get(cur_v, cur_v)}，排名只在该币种内比较。")
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
    # 只有 1 期时不能出「持平 0.00 亿元」这种假结论（2026-10-19 修）：
    # 源数据 T1/T2 各只有 1 个业务日期，旧实现会返回一条水平直线并宣称持平。
    if len(periods) < 2:
        return _env("多期对比",
                    f"库内「{label}」只有 {len(periods)} 个可用期次"
                    f"（{_join(periods) if periods else '无'}），不足以做趋势对比。"
                    "单期数值请改用单期工具：收入→get_income_overview_tool，"
                    "负债→get_liability_overview_tool，资本→get_capital_reserve_tool。",
                    UNIT_YI,
                    notes=pnotes + ["趋势对比至少需要 2 个期次；当前快照尚未累积历史，"
                                    "不是「持平」，而是「无法比较」。"],
                    result_state="NOT_FOUND", candidates={"可用期次": allp})
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

    per_col = _biz_expr(table) if table in _DUAL_DATE_TABLES else "DATA_DT"
    dcond, dargs = _date_where_in(table, periods)
    sql = (f"SELECT {per_col} AS p, `{dim_col}` AS k, SUM(`{val_col}`) AS v FROM `{table}` "
           f"WHERE {dcond} ")
    args = list(dargs)
    if extra:
        sql += f"AND {extra} "
    # 中心维度的「合计」码值不参与聚合：它在每个期次都是自带小计，
    # 与各中心一起 GROUP BY 后会多出一条对象，汇总时再翻一倍。
    tcond, targs = (_exclude_total(dim_col)
                    if dim_col == "CORE" and not any(_is_total(k) for k in (keys or []))
                    else ("", []))
    if tcond:
        sql += f"AND {tcond} "
        args += targs
    if key_vals:
        kh = ",".join(["%s"] * len(key_vals))
        sql += f"AND `{dim_col}` IN ({kh}) "
        args += key_vals
    sql += f"GROUP BY {per_col}, `{dim_col}` ORDER BY p"
    _, rows = _query(sql, args)
    if not rows:
        return _env("多期对比", "未匹配到数据。", UNIT_YI,
                    notes=pnotes + _dual_date_note(table, per_to),
                    result_state="NOT_FOUND")

    # 组数据：key -> {period: value}
    keyset, datas = [], {}
    for r in rows:
        k = r["k"]
        if k not in datas:
            datas[k] = {}
            keyset.append(k)
        datas[k][r["p"]] = _to_yi(r["v"], src_unit)

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
# 序列化 / 装饰器 / 注册
# ==========================================================================
def _json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)

# 别名：与仓库其它业务包一致，工具层异常兜底用 baop.env 调用
env = _env
