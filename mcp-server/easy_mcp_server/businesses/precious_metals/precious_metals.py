# -*- coding: utf-8 -*-
"""
precious_metals MCP Server —— 贵金属业务数据查询（零依赖实现）v1.5

协议：MCP over stdio（换行分隔的 JSON-RPC 2.0），仅使用 Python 标准库。
能力：优先读报表「导出」文件（CSV/XLSX），失败再解析 ereport 页面表格，结构化返回数值。

v1.5 相比 v1.4（导出接口优先）：
  1) config.json 新增 export_url：配了就先走报表导出接口，取服务端生成的 CSV/XLSX；
     失败自动回退解析页面，并在 notes 里写明回退原因——不会静默降级；
  2) 自带 CSV/XLSX 解析（标准库 zipfile + csv）：展开合并单元格、还原百分比（0.18 样式 -> 18%）、
     按数字格式补回被 Excel 丢掉的末尾 0（#,##0.00 -> 713,307.20）；
  3) CSV 兜底修复：未做引号转义的千分位（1,258,436.72 被逗号切开）会按表头列数拼回去，
     否则整行会列错位且不报错；
  4) 返回信封新增 source=export/page，probe 增加「导出接口」体检行；
  5) 命令行新增 --set-export-url。

v1.4 相比 v1.3（页面结构自适应）：
  1) 不再假设页面一定是 <table>：按「标准表格 -> div 栅格(role/class) -> 重复块 -> 文本行」
     四级降级抽取，每级产出与表格一致的行结构，下游解析不变；
  2) HTML 里没有数据（JS 渲染）时，可用本机 Edge/Chrome 无头渲染后重试，
     config.json 的 render 控制：auto（默认）/ off / force；
  3) 补齐 rowspan 展开：纵向合并的品类名会向下填充，避免「只有首行有维度」；
  4) 新增 precious_metals_probe：页面结构体检（体积/文字量/表格数/role 数/判定方式/导出链接），
     换内网或取不到数时先跑它，能直接区分「结构变了 / 需要渲染 / 需要登录」；
  5) 非标准结构取数时在 notes 里显式告警「列对齐可能不稳，请抽查」。

v1.2 相比 v1.1：
  1) 接入维度码值字典 dim_dictionary.json：投资组合是 4 位英文码值（无中文名），
     产品分类固定 5 项枚举，五级产品为全行统一字典（159 项）；
  2) portfolio 参数只认码值：传中文业务名时明确返回"只能按码值筛"并给出 35 个码值，
     不静默返回空；product 传非法值同样返回 5 项合法取值；
  3) metric 整句里能识别码值（"ZCCS 的损益合计"）；
  4) 新增一类判定：指标存在但维度组合当期无数据 -> shape=empty 并列出当期实有组合，
     与"指标本身无明细口径"（shape=text）区分开；
  5) describe 支持查维度取值清单（field=投资组合 / 产品分类 / 五级产品）。

v1.1 相比 v1.0：
  1) query_metric 支持行维度过滤（投资组合/产品分类/五级产品/板块），
     并能从整句里自动识别维度词；
  2) 指标在某层级不存在时明确说明，不再用整体表合计值冒充明细值（防静默错答）；
  3) 数值附带 数值_num 字段，避免模型解析千分位字符串出错；
  4) 新增 shape=empty：查询成功但该日期无数据，与解析失败区分；
  5) 新增 rank（排名）、compare（环比/两日期对比）、describe（口径说明）工具；
  6) 口语问法通过别名表映射到标准指标名。

启动：
    python precious_metals_query_mcp.py                 # stdio
    python precious_metals_query_mcp.py --base-url http://29.241.91.84:9080

日期规则：用户未指定日期时默认取「前一日」，返回 date_source=default_yesterday + notice，
回复用户时必须转告该日期。
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime
import difflib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from html import unescape as html_unescape
from html.parser import HTMLParser

SERVER_NAME = "precious_metals"
SERVER_VERSION = "1.5.0"

# ---------------------------------------------------------------- 服务地址配置
# 换环境（例如搬到内网）只改同目录下的 config.json 里的 report_base_url，
# 不用动本脚本，也不用改 mcp.json。优先级从高到低：
#   1) 命令行参数 --base-url
#   2) config.json 的 report_base_url      <-- 换环境请改这里
#   3) 环境变量 PRECIOUS_METALS_REPORT_BASE_URL（兼容旧部署，优先级已下调）
#   4) 内置默认值 BUILTIN_BASE_URL（留空 = 未配置，启动时直接报错提示，不会误连任何地址）
BUILTIN_BASE_URL = ""
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def read_config() -> dict:
    """读取同目录 config.json；缺失或格式错误时返回空字典，不影响取数。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[{SERVER_NAME}] config.json 读取失败（{e}），改用环境变量/内置默认值",
              file=sys.stderr, flush=True)
        return {}


CONFIG = read_config()


def pick_base_url(cli_value: str | None = None) -> tuple[str, str]:
    """按优先级确定报表服务地址，返回 (地址, 来源说明)。"""
    if cli_value:
        return cli_value.strip().rstrip("/"), "命令行 --base-url"
    cfg = str(CONFIG.get("report_base_url") or "").strip()
    if cfg:
        env = (os.environ.get("PRECIOUS_METALS_REPORT_BASE_URL") or "").strip()
        if env and env.rstrip("/") != cfg.rstrip("/"):
            print(f"[{SERVER_NAME}] 提示：环境变量里是 {env}，config.json 里是 {cfg}，"
                  f"当前按 config.json 生效。建议把环境变量清掉，只留一处配置。",
                  file=sys.stderr, flush=True)
        return cfg.rstrip("/"), f"配置文件 {CONFIG_PATH}"
    env = (os.environ.get("PRECIOUS_METALS_REPORT_BASE_URL") or "").strip()
    if env:
        return env.rstrip("/"), "环境变量 PRECIOUS_METALS_REPORT_BASE_URL"
    if BUILTIN_BASE_URL:
        return BUILTIN_BASE_URL, "内置默认值 BUILTIN_BASE_URL"
    return "", "未配置（config.json / 环境变量 / --base-url 均未提供地址）"


def pick_timeout() -> int:
    try:
        if CONFIG.get("timeout"):
            return int(CONFIG["timeout"])
        return int(os.environ.get("PRECIOUS_METALS_REPORT_TIMEOUT", "20"))
    except (TypeError, ValueError):
        return 20


def pick_render() -> str:
    """页面获取方式：auto（默认，直接抓不到再尝试无头浏览器）/ off（只用 HTTP）/ force（总是渲染）。"""
    v = str(CONFIG.get("render") or os.environ.get("PRECIOUS_METALS_RENDER") or "auto").strip().lower()
    return v if v in ("auto", "off", "force") else "auto"


def pick_export_url() -> str:
    """导出接口地址模板。留空 = 不启用导出，始终解析页面 HTML。

    模板里可用 {base} / {report_id} / {report_dt} 三个占位符；
    不含占位符时自动追加 &_reportId=…&report_dt=…。
    例：http://10.1.2.3:9080/ereport/export?_reportId={report_id}&report_dt={report_dt}&format=xlsx
    """
    return str(CONFIG.get("export_url") or os.environ.get("PRECIOUS_METALS_EXPORT_URL") or "").strip()


DEFAULT_BASE_URL, BASE_URL_SOURCE = pick_base_url()
HTTP_TIMEOUT = pick_timeout()
RENDER_MODE = pick_render()
EXPORT_URL_TEMPLATE = pick_export_url()

SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL = "2024-11-05"

# ---------------------------------------------------------------- 报表定义
REPORTS = {
    "8670": {
        "name": "贵金属业务",
        "short": "贵金属业务（分产品明细）",
        "title": "贵金属业务（分产品明细）",
        "layout": "matrix",
        "unit": "万元人民币",
        "desc": "明细表：按投资组合 / 产品分类 / 五级产品列示名义本金、SELL/BUY 交易本金、"
                "新发生系列、存量本金、当期公允价值、业务损益、公允价值变动、交易笔数、"
                "平均持有期限、其他损益、评价损益、损益合计（均区分含/不含内部账）。",
        "dims": ["投资组合", "产品分类", "五级产品"],
        "fields": [
            "投资组合", "产品分类", "五级产品", "名义本金折人民币", "SELL交易本金折人民币",
            "BUY交易本金折人民币", "新发生-名义本金折人民币", "新发生-SELL交易本金折人民币",
            "新发生-BUY交易本金折人民币", "存量本金折人民币", "当期公允价值折人民币",
            "业务损益折人民币（含内部账）", "业务损益折人民币（不含内部账）",
            "公允价值变动折人民币（含内部账）", "公允价值变动折人民币（不含内部账）",
            "交易笔数", "平均持有期限", "新发生-交易笔数", "新发生-平均持有期限",
            "其他损益（含内部账）", "其他损益（不含内部账）",
            "评价损益折人民币（含内部账）", "评价损益折人民币（不含内部账）",
            "损益合计（含内部账）", "损益合计（不含内部账）",
        ],
    },
    "8666": {
        "name": "贵金属业务-整体情况表",
        "short": "贵金属业务-整体情况（全业务合计）",
        "title": "贵金属业务-整体情况表",
        "layout": "sections",
        "unit": "万元人民币（占比为百分数）",
        "desc": "整体汇总表：分「贵金属业务规模情况」（期末余额、期末余额占比、当期发生额、"
                "当期发生额占比、当期交易笔数、当期交易笔数占比）与「贵金属业务损益情况」"
                "（营业净收入、非息收入、营业净收入占比、业务损益、公允价值变动、评价损益、"
                "其他损益及折人民币不含内部账口径系列、损益合计折人民币不含内部账）两大板块。"
                "第一列为统计维度，取值可为「合计」、4 位投资组合码值（如 TGLD）或产品分类"
                "（如 贵金属即远期），后两者取值集合见 dim_dictionary.json。",
        "dims": ["板块", "统计维度"],
        "fields": [
            "期末余额", "期末余额占比", "当期发生额", "当期发生额占比", "当期交易笔数", "当期交易笔数占比",
            "营业净收入", "非息收入", "营业净收入占比", "业务损益", "公允价值变动", "评价损益", "其他损益",
            "业务损益折人民币（不含内部账）", "公允价值变动折人民币（不含内部账）",
            "评价损益折人民币（不含内部账）", "其他损益（不含内部账）", "损益合计折人民币（不含内部账）",
        ],
    },
}

EMPTY_TOKENS = {"", "-", "--", "—", "\xa0", "null", "None", "/"}

# ---------------------------------------------------------------- 维度码值字典
# 报表页面中的维度值：投资组合为 4 位英文码值（无中文名），
# 产品分类为 5 项中文枚举，五级产品为全行统一字典（159 项）。
# 字典文件与主程序同目录；缺失时自动降级为不做码值校验，不影响取数。
DIM_DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dim_dictionary.json")


def _load_dim_dict() -> dict:
    try:
        with open(DIM_DICT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


DIM_DICT = _load_dim_dict()
PORTFOLIO_CODES = list((DIM_DICT.get("投资组合") or {}).get("values") or [])
PRODUCT_CATEGORIES = list((DIM_DICT.get("产品分类") or {}).get("values") or [])
LEVEL5_VALUES = list((DIM_DICT.get("五级产品") or {}).get("values") or [])


# 口语问法 -> 标准指标名
ALIASES = {
    "赚了多少钱": ["损益合计"],
    "赚了多少": ["损益合计"],
    "赚多少": ["损益合计"],
    "赚了": ["损益合计"],
    "利润": ["损益合计"],
    "盈利": ["损益合计"],
    "效益": ["损益合计"],
    "规模": ["期末余额", "名义本金折人民币"],
    "余额": ["期末余额", "存量本金折人民币"],
    "存量": ["存量本金折人民币"],
    "收入": ["营业净收入"],
    "非息": ["非息收入"],
    "交易量": ["当期发生额", "名义本金折人民币"],
    "成交多少": ["当期发生额"],
    "笔数": ["交易笔数"],
    "多少笔": ["交易笔数"],
    "公允价值": ["公允价值变动", "当期公允价值折人民币"],
    "估值": ["公允价值变动"],
    "持有期": ["平均持有期限"],
    "本金": ["名义本金折人民币"],
    "占比": ["占比"],
    "比重": ["占比"],
}

# 字段口径字典
FIELD_DOCS = {
    "含内部账": "统计范围包含行内分支机构之间（内部账）的贵金属交易。适用于内部经营口径、条线全额考核。",
    "不含内部账": "剔除行内内部交易，只统计对银行外部客户/交易对手的部分，反映对外真实损益。"
                  "对外报送、监管口径与分中心考核通常采用此口径；两者差异即内部交易贡献。",
    "名义本金": "合约标的本金金额，不区分买卖方向；SELL/BUY 交易本金分别列示卖出、买入方向本金。",
    "新发生": "当期新叙做（新开仓）业务口径；不带「新发生-」前缀的为当期全部发生额口径。",
    "存量本金": "期末仍存续的合约本金余额（区别于当期发生额）。",
    "当期公允价值": "期末对持仓按市价重估后的公允价值。",
    "业务损益": "交易类业务形成的已实现损益与估值损益合计。",
    "公允价值变动": "持仓估值变动形成的未实现损益。",
    "评价损益": "非交易性资产（如持有至到期、应收款项类）按评价/减值规则确认的损益影响。",
    "其他损益": "上述之外的手续费、汇兑、税费等损益。",
    "损益合计": "业务损益 + 公允价值变动 + 评价损益 + 其他损益。",
    "营业净收入": "利息净收入 + 非息收入，为条线营业收入口径。",
    "非息收入": "手续费及佣金、价差、挂钩衍生等非利息收入。",
    "占比": "分子为本行贵金属业务对应指标，分母为全行同类指标总量（具体分母以报表配置为准，"
            "如期末余额占比的分母为全行资产/负债期末余额）。",
    "平均持有期限": "按本金加权的平均持有天数（天）。",
    "交易笔数": "当期成交笔数（笔）；新发生-交易笔数仅统计当期新叙做部分。",
    "期末余额": "期末时点余额；贵金属业务中通常对应存量本金/持仓规模口径。",
    "当期发生额": "统计期内累计发生金额（流量口径）。",
}


# ---------------------------------------------------------------- HTML 解析
class _TableParser(HTMLParser):
    """把页面里的所有 <table> 解析成 [[(文本, colspan, rowspan), ...], ...]。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, int, int]]]] = []
        self._stack: list[list[list[tuple[str, int, int]]]] = []
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: list[str] | None = None
        self._colspan = 1
        self._rowspan = 1

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr":
            if self._stack:
                self._row = []
        elif tag in ("td", "th"):
            if self._row is not None:
                d = dict(attrs)
                self._colspan = _span_attr(d.get("colspan"))
                self._rowspan = _span_attr(d.get("rowspan"))
                self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = " ".join("".join(self._cell).replace("\xa0", " ").split())
            self._row.append((text, self._colspan, self._rowspan))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            rows = self._stack.pop()
            if rows:
                self.tables.append(rows)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _span_attr(raw) -> int:
    try:
        return max(1, int(str(raw or "1").strip()))
    except (TypeError, ValueError):
        return 1


def _cells(row) -> list[str]:
    """把一行按 colspan 展开成一维文本数组。

    兼容两种行格式：老格式 (文本, colspan)，新格式 (文本, colspan, rowspan)。
    """
    out: list[str] = []
    for cell in row:
        text = cell[0]
        span = cell[1] if len(cell) > 1 else 1
        out.append(text)
        out.extend([""] * (span - 1))
    return out


def expand_rowspan(rows: list[list[tuple[str, int, int]]]) -> list[list[tuple[str, int]]]:
    """把 rowspan 展开成占位空格，保证每行等宽、按列下标取值不错位。

    合并单元格（纵向）在报表里很常见：品类名跨若干行只写一次，下面几行留空。
    不展开的话只有首行能取到品类，其余行会变成「空标签」而丢维度。
    """
    out: list[list[tuple[str, int]]] = []
    carry: dict[int, tuple[str, int]] = {}          # 列号 -> (文本, 还需占用的行数)
    for row in rows:
        queue = list(row)
        cells: list[tuple[str, int]] = []
        col = 0
        limit = len(_cells(row)) + (max(carry) + 1 if carry else 0) + 8
        while (queue or col in carry) and col < limit:
            if col in carry:
                text, remain = carry[col]
                cells.append((text, 1))
                if remain <= 1:
                    del carry[col]
                else:
                    carry[col] = (text, remain - 1)
                col += 1
                continue
            raw = queue.pop(0)
            text = raw[0]
            cspan = raw[1] if len(raw) > 1 else 1
            rspan = raw[2] if len(raw) > 2 else 1
            cells.append((text, cspan))
            if rspan > 1:
                for k in range(cspan):
                    carry[col + k] = (text if k == 0 else "", rspan - 1)
            col += cspan
        out.append(cells)
    return out


# ---------------------------------------------------------------- 页面结构识别
# 报表页不一定是 <table>：可能是 div 栅格、role=row 的 ARIA 表格、重复块列表，
# 也可能是纯 JS 渲染。这里按「表格 -> 栅格 -> 重复块 -> 文本行」逐级降级，
# 每一级都产出与表格完全一致的行结构，下游解析逻辑不用改。
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col",
             "embed", "source", "track", "wbr", "path", "circle", "rect", "line",
             "polygon", "polyline", "stop", "use"}

ROW_ROLES = {"row"}
CELL_ROLES = {"cell", "gridcell", "columnheader", "rowheader"}
GRID_ROLES = {"table", "grid", "treegrid"}
ROW_TOKENS = {"row", "tr", "datarow", "tablerow"}
CELL_TOKENS = {"cell", "td", "th", "col", "column", "gridcell"}
GRID_TOKENS = {"table", "grid", "datagrid", "tablebody", "tbody"}

STRATEGY_NAMES = {
    "table": "标准表格 <table>",
    "grid": "div 栅格（role/class 行单元格）",
    "blocks": "重复块列表",
    "text": "文本行重建",
    "none": "未识别出结构",
}


class _Node:
    __slots__ = ("tag", "attrs", "children", "texts")

    def __init__(self, tag: str, attrs: dict | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[_Node] = []
        self.texts: list[str] = []


class _DomParser(HTMLParser):
    """建一棵极简 DOM 树，供结构识别使用（不依赖第三方库）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].texts.append(data)


def _walk(node: _Node):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(cur.children))


def _text_of(node: _Node) -> str:
    if node.tag in ("script", "style"):
        return ""
    parts = list(node.texts)
    for c in node.children:
        parts.append(_text_of(c))
    return " ".join("".join(parts).replace("\xa0", " ").split())


def _cls_tokens(node: _Node) -> set[str]:
    raw = (node.attrs.get("class") or "").lower()
    return {t for t in re.split(r"[^0-9a-z]+", raw) if t}


def _is_row(node: _Node) -> bool:
    return (node.attrs.get("role") or "").lower() in ROW_ROLES or bool(_cls_tokens(node) & ROW_TOKENS)


def _is_cell(node: _Node) -> bool:
    role = (node.attrs.get("role") or "").lower()
    return role in CELL_ROLES or bool(_cls_tokens(node) & CELL_TOKENS)


def _direct_cells(node: _Node) -> list[_Node]:
    """取一行里的单元格：优先 role/class 命中的直接子节点，否则退化为直接子块。"""
    cells = [c for c in node.children if _is_cell(c)]
    if cells:
        return cells
    kids = [c for c in node.children if c.tag not in ("script", "style")]
    return kids if len(kids) >= 2 else []


def _grid_tables(root: _Node) -> list[list[list[tuple[str, int]]]]:
    """识别 div/ARIA 栅格表：容器 role=table|grid 或 class 含 table/grid。"""
    found: list[list[list[tuple[str, int]]]] = []
    for node in _walk(root):
        if node.tag == "table":
            continue
        role = (node.attrs.get("role") or "").lower()
        if role not in GRID_ROLES and not (_cls_tokens(node) & GRID_TOKENS):
            continue
        rows = [n for n in _walk(node) if n is not node and _is_row(n)]
        if not rows:
            rows = [c for c in node.children if len(_direct_cells(c)) >= 2]
        table = []
        for r in rows:
            cells = _direct_cells(r)
            if len(cells) < 2:
                continue
            table.append([(_text_of(c), 1) for c in cells])
        if len(table) >= 2:
            found.append(table)
    if not found:
        return []
    return [max(found, key=len)]


def _block_tables(root: _Node) -> list[list[list[tuple[str, int]]]]:
    """识别重复块列表：同父下 ≥3 个同 tag+class 的兄弟，每个含 ≥2 个文本块。"""
    best: list[list[tuple[str, int]]] = []
    for node in _walk(root):
        kids = [c for c in node.children if c.tag not in ("script", "style")]
        if len(kids) < 3:
            continue
        groups: dict[tuple[str, str], list[_Node]] = {}
        for c in kids:
            groups.setdefault((c.tag, c.attrs.get("class") or ""), []).append(c)
        for members in groups.values():
            if len(members) < 3:
                continue
            table = []
            for m in members:
                cells = _direct_cells(m)
                if len(cells) >= 2:
                    table.append([(_text_of(x), 1) for x in cells])
            if len(table) >= 3 and len(table) > len(best):
                best = table
    return [best] if best else []


def _text_table(html: str) -> list[list[tuple[str, int]]]:
    """最后的兜底：剥标签后按行重建，靠制表符/换行还原列。"""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</?(td|th)\b[^>]*>", "\t", s)
    s = re.sub(r"(?i)</?(tr|div|p|li|h[1-6]|dt|dd|section|article|header|footer|"
               r"ul|ol|dl|table|thead|tbody|tfoot)\b[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html_unescape(s)
    rows: list[list[tuple[str, int]]] = []
    for raw in s.splitlines():
        parts = [x.strip() for x in raw.split("\t")]
        parts = [x for x in parts if x and x not in EMPTY_TOKENS]
        if len(parts) >= 2:
            rows.append([(p, 1) for p in parts])
            continue
        line = raw.strip()
        if not line:
            continue
        # 没有制表符时：先按 2 个以上空格切列，再退回按空白切
        tokens = [x for x in re.split(r"\s{2,}", line) if x]
        if len(tokens) < 2:
            tokens = line.split()
        if len(tokens) >= 2:
            rows.append([(t, 1) for t in tokens])
    return rows


def _usable(tables: list[list[list[tuple[str, int]]]]) -> bool:
    """结构是否可用：至少 2 行，且最宽 ≥3 列、至少 2 行达到 3 列。"""
    for t in tables:
        if len(t) < 2:
            continue
        widths = [len(_cells(r)) for r in t]
        if max(widths, default=0) >= 3 and sum(1 for w in widths if w >= 3) >= 2:
            return True
    return False


def extract_tables(html: str) -> dict:
    """按 表格 -> 栅格 -> 重复块 -> 文本行 逐级降级，返回 {tables, strategy, diag}。"""
    parser = _TableParser()
    parser.feed(html)
    tables = [expand_rowspan(t) for t in parser.tables]
    diag = {"table_count": len(parser.tables),
            "table_rows": sum(len(t) for t in parser.tables)}
    if _usable(tables):
        return {"tables": tables, "strategy": "table", "diag": diag}

    dom = _DomParser()
    dom.feed(html)
    diag["dom_nodes"] = sum(1 for _ in _walk(dom.root))
    diag["role_rows"] = sum(1 for n in _walk(dom.root) if _is_row(n))
    diag["role_cells"] = sum(1 for n in _walk(dom.root) if _is_cell(n))

    for strategy, getter in (("grid", _grid_tables), ("blocks", _block_tables)):
        found = getter(dom.root)
        if _usable(found):
            return {"tables": found, "strategy": strategy, "diag": diag}

    text_rows = _text_table(html)
    if _usable([text_rows]):
        return {"tables": [text_rows], "strategy": "text", "diag": diag}
    return {"tables": tables, "strategy": "none", "diag": diag}


BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    for p in BROWSER_CANDIDATES:
        if os.path.isfile(p):
            return p
    for name in ("msedge", "chrome", "chromium", "chromium-browser", "google-chrome"):
        w = shutil.which(name)
        if w:
            return w
    return None


def render_with_browser(url: str, timeout: int | None = None) -> tuple[str | None, str]:
    """用本机 Edge/Chrome 无头渲染后取 DOM，覆盖 JS 渲染的报表页。"""
    exe = find_browser()
    if not exe:
        return None, "未找到 Edge/Chrome，无法走浏览器渲染"
    wait = timeout or max(10, min(HTTP_TIMEOUT, 60))
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
           "--disable-background-networking", f"--virtual-time-budget={wait * 1000}",
           "--dump-dom", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=wait + 15)
    except subprocess.TimeoutExpired:
        return None, f"浏览器渲染超时（{wait + 15} 秒）"
    except OSError as e:
        return None, f"浏览器启动失败：{e}"
    out = proc.stdout.decode("utf-8", errors="replace")
    if len(out.strip()) < 200:
        return None, "浏览器没有取到 DOM（可能被安全策略或登录页拦截）"
    return out, f"已用 {os.path.basename(exe)} 无头渲染"


def _table_width(rows: list[list[tuple[str, int]]]) -> int:
    """该表格最宽一行的列数（按 colspan 展开后）。"""
    return max((len(_cells(r)) for r in rows), default=0)


def _merge_tables(tables: list[list[list[tuple[str, int]]]]) -> list[list[tuple[str, int]]]:
    """把页面里多张表按文档顺序拼成一段行，供分区解析使用。

    报表页常把多个板块渲染成多个上下排列的 <table>，只读第一张会静默丢掉后面的
    板块（不报错、数值偏少）。这里过滤掉筛选区、页脚之类的装饰表格：
    只保留列数 >= 3 的表；若全都不满足则退回用全部表。
    """
    useful = [t for t in tables if _table_width(t) >= 3] or tables
    return [row for t in useful for row in t]


def _pick_matrix_table(tables: list[list[list[tuple[str, int]]]]) -> list[list[tuple[str, int]]]:
    """选出最像主报表的一张：表头最宽优先，其次行数最多。"""
    return max(tables, key=lambda t: (_table_width(t), len(t)))


# ---------------------------------------------------------------- 取数与日期
def build_url(report_id: str, report_dt: str, base_url: str | None = None) -> str:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/ereport/output?_reportId={report_id}&report_dt={report_dt}"


def resolve_date(report_dt: str | None, today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    raw = (report_dt or "").strip()
    if not raw:
        d = today - datetime.timedelta(days=1)
        return {
            "report_dt": d.isoformat(),
            "date_source": "default_yesterday",
            "notice": (
                f"用户未指定查询日期，已按规则默认使用前一日（{d.isoformat()}）。"
                f"请在回复中明确告知用户本次数据日期为 {d.isoformat()}（前一日），避免误读。"
            ),
        }
    try:
        d = datetime.date.fromisoformat(raw)
    except ValueError:
        return {"error": f"日期格式不合法：{raw}。请使用 YYYY-MM-DD，例如 2026-06-30。"}
    info = {"report_dt": d.isoformat(), "date_source": "user_specified", "notice": ""}
    if d > today:
        info["notice"] = f"查询日期 {d.isoformat()} 晚于今天（{today.isoformat()}），报表可能无数据。"
    return info


def prev_month_same_day(d: datetime.date) -> datetime.date:
    year, month = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) precious-metals-mcp/1.1",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 导出接口（优先路径）
# 报表页一般都有「导出」按钮，它返回服务端生成的文件（CSV / XLSX）。
# 比起解析 HTML，这条路更稳：格式由服务端保证，不受前端改版、JS 渲染、
# 合并单元格写法的影响。config.json 里配了 export_url 就优先走它，失败再回退页面。
XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# 内置的百分比数字格式 id（Excel 规范固定值）
XLSX_PERCENT_IDS = {9, 10}


def build_export_url(report_id: str, report_dt: str, base_url: str | None = None,
                     template: str | None = None) -> str:
    """按模板拼导出地址；未配置模板时返回空串（表示不启用导出路径）。"""
    tpl = (EXPORT_URL_TEMPLATE if template is None else (template or "")).strip()
    if not tpl:
        return ""
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    url = (tpl.replace("{base}", base)
              .replace("{report_id}", report_id)
              .replace("{report_dt}", report_dt))
    if url.startswith("/"):
        url = base + url
    if "{report_id}" not in tpl:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}_reportId={report_id}&report_dt={report_dt}"
    return url


def fetch_binary(url: str) -> tuple[bytes, str, str]:
    """下载导出文件，返回 (字节, Content-Type, Content-Disposition)。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) precious-metals-mcp/1.5",
            "Accept": "text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
                      "application/vnd.ms-excel,application/octet-stream,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        cdisp = resp.headers.get("Content-Disposition") or ""
        final = resp.geturl()
    if final and final != url:
        print(f"[{SERVER_NAME}] 导出接口发生跳转：{url} -> {final}", file=sys.stderr, flush=True)
        url = final
    return raw, ctype, cdisp


def _guess_export_kind(raw: bytes, ctype: str, cdisp: str) -> str:
    """判定导出文件类型：xlsx / csv / html（html 说明接口返回了页面而不是文件）。"""
    head = raw[:2048].lstrip().lower()
    if raw[:2] == b"PK" or "spreadsheetml" in ctype or ".xlsx" in cdisp.lower() or "officedocument" in ctype:
        return "xlsx"
    if head.startswith(b"<") or b"<html" in head or b"<!doctype" in head:
        return "html"
    if "csv" in ctype or "text/plain" in ctype or ".csv" in cdisp.lower():
        return "csv"
    # 扩展名与类型都没给：靠内容猜——有 HTML 标签就是页面，否则按文本表处理
    try:
        probe = raw[:4096].decode("utf-8")
    except UnicodeDecodeError:
        probe = raw[:4096].decode("gb18030", errors="replace")
    return "html" if "<table" in probe.lower() else "csv"


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _norm_cell(text: str) -> str:
    return " ".join(html_unescape(text or "").replace("\xa0", " ").split())


def _split_csv(text: str) -> list[list[str]]:
    """切 CSV，分隔符按「每行字段数最稳定」自动选择（逗号/制表/分号/竖线）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sample = [ln for ln in text.split("\n")[:200] if ln.strip()]
    best, best_score = ",", -1.0
    for d in (",", "\t", ";", "|"):
        counts = sorted(ln.count(d) for ln in sample)
        if not counts:
            continue
        med = counts[len(counts) // 2]
        if med < 1:
            continue
        stability = sum(1 for c in counts if c == med) / len(counts)
        if med * stability > best_score:
            best, best_score = d, med * stability
    try:
        return [r for r in csv.reader(io.StringIO(text), delimiter=best)]
    except csv.Error:
        return [ln.split(best) for ln in text.split("\n")]


THOUSAND_LEFT = re.compile(r"-?\d{1,3}(?:,\d{3})*")
THOUSAND_RIGHT = re.compile(r"\d{3}(?:\.\d+)?%?")


def _join_thousands(cells: list[str]) -> list[str]:
    """把被分隔符拆开的千分位数字拼回去：['226','518.61'] -> ['226,518.61']。

    有些导出接口不做引号转义，1,258,436.72 会被切成多个字段，
    进而让整行列错位——这类错误不会报错，只会悄悄把指标对到别的列上。
    逐步合并，支持「1」「258」「436.72」这种被切成三段的情况。
    """
    out: list[str] = []
    i = 0
    while i < len(cells):
        cur = cells[i].strip()
        while (i + 1 < len(cells)
               and THOUSAND_LEFT.fullmatch(cur or "x")
               and THOUSAND_RIGHT.fullmatch(cells[i + 1].strip() or "x")):
            cur = f"{cur},{cells[i + 1].strip()}"
            i += 1
        out.append(cur)
        i += 1
    return out


def _has_split_number(row: list[str]) -> bool:
    """这一行里是否存在被分隔符切开的千分位数字（'226' 紧跟 '518.61'）。"""
    for a, b in zip(row, row[1:]):
        if THOUSAND_LEFT.fullmatch(a.strip() or "x") and THOUSAND_RIGHT.fullmatch(b.strip() or "x"):
            return True
    return False


def parse_csv_table(raw: bytes) -> list[list[tuple[str, int]]]:
    """CSV -> 与 HTML 表格一致的行结构：每行 [(文本, colspan), ...]。"""
    raw_rows = [[_norm_cell(c) for c in r] for r in _split_csv(_decode_text(raw))]
    raw_rows = [r for r in raw_rows if any(x for x in r)]
    if not raw_rows:
        return []

    # 列数基准优先取表头行（表头是文字、不会被千分位拆开，字段数就是真实列数）
    expected = 0
    for r in raw_rows:
        if sum(1 for c in r if c) >= 3 and not _has_split_number(r):
            expected = len(r)
            break
    if not expected:
        widths = [len(r) for r in raw_rows]
        expected = max(set(widths), key=widths.count)

    for i, r in enumerate(raw_rows):
        guard = 0
        while len(r) > expected and guard < 6:
            joined = _join_thousands(r)
            if joined == r:
                break
            r = joined
            guard += 1
        raw_rows[i] = r

    rows: list[list[tuple[str, int]]] = []
    for r in raw_rows:
        cells = list(r)
        while cells and cells[-1] == "":
            cells.pop()
        rows.append([(c, 1) for c in cells])
    return rows


def _xlsx_ref(ref: str) -> tuple[int, int]:
    """'B7' -> (行号, 列号)，均为 1 起。"""
    m = re.match(r"([A-Za-z]+)(\d+)$", (ref or "").strip())
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


def _fmt_number(raw_text: str, decimals: int | None = None) -> str:
    """数字单元格还原成报表写法：≥1000 加千分位。

    Excel 把 713307.20 存成 713307.2（数值不保留末尾 0），
    所以小数位数优先取该单元格的数字格式（如 #,##0.00 → 2 位），
    取不到时退回原始文本里的小数位。
    """
    try:
        v = float(raw_text)
    except (TypeError, ValueError):
        return raw_text
    if decimals is None:
        decimals = len(raw_text.split(".")[1]) if "." in raw_text else 0
    return f"{v:,.{decimals}f}" if abs(v) >= 1000 else f"{v:.{decimals}f}"


def _xlsx_cell_styles(zf: zipfile.ZipFile) -> list[tuple[bool, int | None]]:
    """返回 cellXfs 每个样式的 (是否百分比, 小数位数)。

    Excel 把 18% 存成 0.18 + 百分比样式，不还原就会少两个数量级，而且不会报错。
    小数位数取数字格式里的 0 的个数（#,##0.00 → 2），取不到则返回 None。
    """
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return []
    custom: dict[int, str] = {}
    for nf in root.iter(XLSX_NS + "numFmt"):
        try:
            custom[int(nf.get("numFmtId") or -1)] = nf.get("formatCode") or ""
        except (TypeError, ValueError):
            continue
    cellxfs = root.find(XLSX_NS + "cellXfs")
    out: list[tuple[bool, int | None]] = []
    for xf in (list(cellxfs) if cellxfs is not None else []):
        try:
            fid = int(xf.get("numFmtId") or 0)
        except (TypeError, ValueError):
            fid = 0
        code = custom.get(fid, "")
        is_pct = fid in XLSX_PERCENT_IDS or "%" in code
        m = re.search(r"\.(0+)", code)
        out.append((is_pct, len(m.group(1)) if m else None))
    return out


def parse_xlsx_tables(raw: bytes) -> list[list[list[tuple[str, int]]]]:
    """XLSX -> 每个 sheet 一张表（已展开合并单元格、还原百分比）。"""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in sroot.iter(XLSX_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(XLSX_NS + "t")))
    styles = _xlsx_cell_styles(zf)

    sheets = sorted((n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    tables: list[list[list[tuple[str, int]]]] = []
    for sheet in sheets:
        ws = ET.fromstring(zf.read(sheet))
        grid: dict[tuple[int, int], str] = {}
        max_r = max_c = 0
        for c in ws.iter(XLSX_NS + "c"):
            r, col = _xlsx_ref(c.get("r") or "")
            if not r or not col:
                continue
            kind = (c.get("t") or "n").lower()
            if kind == "s":
                v = c.find(XLSX_NS + "v")
                try:
                    text = shared[int(v.text)]  # type: ignore[arg-type,union-attr]
                except (TypeError, ValueError, AttributeError, IndexError):
                    text = ""
            elif kind == "inlinestr":
                text = "".join(t.text or "" for t in c.iter(XLSX_NS + "t"))
            else:
                v = c.find(XLSX_NS + "v")
                text = (v.text or "") if v is not None else ""
                if text.strip():
                    try:
                        sidx = int(c.get("s") or 0)
                        is_pct, dec = styles[sidx] if sidx < len(styles) else (False, None)
                    except (TypeError, ValueError):
                        is_pct, dec = False, None
                    try:
                        if is_pct:
                            text = f"{float(text) * 100:.{dec if dec is not None else 2}f}%"
                        else:
                            text = _fmt_number(text, dec)
                    except (TypeError, ValueError):
                        pass
            grid[(r, col)] = _norm_cell(text)
            max_r, max_c = max(max_r, r), max(max_c, col)

        # 合并单元格：锚点带上 colspan/rowspan，被覆盖的格子直接不出现（与 HTML 一致）
        anchors: dict[tuple[int, int], tuple[int, int]] = {}
        covered: set[tuple[int, int]] = set()
        for mc in ws.iter(XLSX_NS + "mergeCell"):
            ref = mc.get("ref") or ""
            if ":" not in ref:
                continue
            a, b = ref.split(":", 1)
            r1, c1 = _xlsx_ref(a)
            r2, c2 = _xlsx_ref(b)
            if not r1 or not r2 or not c1 or not c2:
                continue
            anchors[(r1, c1)] = (r2 - r1 + 1, c2 - c1 + 1)
            for rr in range(r1, r2 + 1):
                for cc in range(c1, c2 + 1):
                    if (rr, cc) != (r1, c1):
                        covered.add((rr, cc))

        rows3: list[list[tuple[str, int, int]]] = []
        for r in range(1, max_r + 1):
            row: list[tuple[str, int, int]] = []
            for col in range(1, max_c + 1):
                if (r, col) in covered:
                    continue
                rspan, cspan = anchors.get((r, col), (1, 1))
                row.append((grid.get((r, col), ""), cspan, rspan))
            while row and row[-1][0] == "" and row[-1][1] == 1 and row[-1][2] == 1:
                row.pop()
            rows3.append(row)
        tables.append(expand_rowspan(rows3))
    return tables


def _drop_leading_titles(rows: list[list[tuple[str, int]]]) -> list[list[tuple[str, int]]]:
    """matrix 版式下丢掉表头之前的标题行（标题行通常只有 1~2 个非空格）。"""
    for i, r in enumerate(rows):
        if sum(1 for c in _cells(r) if c and c not in EMPTY_TOKENS) >= 3:
            return rows[i:]
    return rows


def fetch_export(report_id: str, report_dt: str, layout: str = "sections",
                 base_url: str | None = None) -> dict:
    """走导出接口取数。返回 {ok, tables, kind, detail, url, error}。"""
    url = build_export_url(report_id, report_dt, base_url)
    if not url:
        return {"ok": False, "skipped": True, "url": "", "error": "未配置 export_url"}
    try:
        raw, ctype, cdisp = fetch_binary(url)
    except urllib.error.HTTPError as e:
        return {"ok": False, "url": url, "error": f"导出接口返回 HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "url": url, "error": f"导出接口访问失败：{e}"}
    if not raw:
        return {"ok": False, "url": url, "error": "导出接口返回空内容"}

    kind = _guess_export_kind(raw, ctype, cdisp)
    if kind == "html":
        return {"ok": False, "url": url,
                "error": "导出接口返回的是 HTML 页面（常见原因：未登录/无权限、参数名不对、接口已改）"}
    try:
        if kind == "xlsx":
            tables = parse_xlsx_tables(raw)
            label = "XLSX"
        else:
            tables = [parse_csv_table(raw)]
            label = "CSV"
    except Exception as e:
        return {"ok": False, "url": url, "error": f"{kind.upper()} 解析失败：{e}"}

    tables = [t for t in tables if t and (len(t) >= 2 or sum(len(_cells(r)) for r in t) >= 3)]
    if not tables or not _usable(tables):
        return {"ok": False, "url": url, "error": f"{label} 里没有识别出可用的表格结构"}

    if layout == "matrix":
        tables = [_drop_leading_titles(t) for t in tables]

    return {"ok": True, "url": url, "kind": label, "tables": tables,
            "detail": f"{label}，{len(raw)} 字节，{len(tables)} 个表，"
                      f"共 {sum(len(t) for t in tables)} 行"}


# ---------------------------------------------------------------- 解析
def parse_matrix(rows: list[list[tuple[str, int]]]) -> dict:
    header = _cells(rows[0])
    while header and header[-1] in EMPTY_TOKENS:
        header.pop()
    data = []
    for r in rows[1:]:
        cells = _cells(r)[: len(header)]
        if not any(c and c not in EMPTY_TOKENS for c in cells):
            continue
        cells += [""] * (len(header) - len(cells))
        data.append(cells)
    return {"columns": header, "rows": data}


def _classify_dim(value: str) -> str:
    """判定整体表第一列的语义：合计 / 投资组合 / 产品分类 / 未知。

    整体表第一列按口径可以是「合计」，也可以是某个投资组合码值或产品分类，
    取值的合法集合来自 dim_dictionary.json（码值表）。这里只做归类，不做翻译：
    投资组合在报表里只有 4 位英文码值、没有中文名。
    """
    v = (value or "").strip()
    if not v:
        return ""
    if v in ("合计", "小计", "总计", "全行合计"):
        return "合计"
    if v in PRODUCT_CATEGORIES:
        return "产品分类"
    codes = {c.upper() for c in PORTFOLIO_CODES}
    if codes:
        # 码值表存在时以它为准：不在表内的 4 位码值判为「未知」并告警，
        # 避免新增/拼错的组合码被静默当成合法组合
        return "投资组合" if v.upper() in codes else "未知"
    return "投资组合" if re.fullmatch(r"[A-Z]{4}", v) else "未知"


DIM_LABELS = {
    "按投资组合": "投资组合", "投资组合": "投资组合", "投资组合码": "投资组合", "组合": "投资组合",
    "按产品分类": "产品分类", "产品分类": "产品分类", "产品类别": "产品分类",
    "合计": "合计", "小计": "合计", "总计": "合计", "全行合计": "合计",
}


def _dim_of(label: str, value: str) -> tuple[str, str]:
    """由「维度列标签 + 维度取值」判定 (维度类型, 维度值)。

    整体表前端是两列维度：第一列写维度类型（按投资组合 / 按产品分类 / 合计），
    第二列才是该类型下的具体码值或分类。老版式只有一列时，标签本身就是码值。
    """
    lab = (label or "").strip()
    val = (value or "").strip()
    if not val:
        val = lab
    kind = DIM_LABELS.get(lab) or DIM_LABELS.get(val)
    if kind:
        return kind, val
    return (_classify_dim(val) or "未知"), val


NUM_LIKE = re.compile(r"^-?[\d,]+(?:\.\d+)?%?$|^-?[\d,]+天$")


def _looks_like_header(cells: list[str]) -> bool:
    """多列且几乎不含数值的行，按表头处理（div 栅格页常没有合并表头）。"""
    filled = [c for c in cells if c and c not in EMPTY_TOKENS]
    if len(filled) < 3:
        return False
    numeric = sum(1 for c in filled if NUM_LIKE.match(c.replace(" ", "")))
    return numeric <= len(filled) * 0.25


def parse_sections(rows: list[list[tuple[str, int]]], dim_cols: int = 2) -> list[dict]:
    """解析「分区 + 表头 + 多行」版式。

    版式约定：前 dim_cols 列是维度（类型 + 取值），其表头为跨列合并的单格
    （如 <th colspan="2">贵金属业务</th>，展开后首格有值、次格为空）；
    指标名从第 dim_cols 列开始，与数据行按列下标一一对应。
    """
    records: list[dict] = []
    section = ""
    header: list[str] = []
    for r in rows:
        cells = _cells(r)
        spans = [s for _, s in r] or [1]
        filled = [c for c in cells if c and c not in EMPTY_TOKENS]
        if not filled:
            continue
        # 分区标题：整行只有一个非空格（通常 colspan 铺满整行）
        if len(filled) == 1 and (len(cells) == 1 or spans[0] >= 2):
            section = filled[0]
            header = []
            continue
        # 表头行：维度列表头是跨列合并的单格，或首格留空
        if len(filled) >= 2 and (spans[0] >= dim_cols or not cells[0]):
            header = cells[dim_cols:]
            continue
        if not header and _looks_like_header(cells):
            # div 栅格页常没有合并表头：把首个「多列且几乎全非数值」的行当表头
            header = cells[dim_cols:]
            continue
        if not header:
            continue
        label = (cells[0] or "").strip()
        value = (cells[1] if len(cells) > 1 else "").strip()
        kind, dim = _dim_of(label, value)
        values = cells[dim_cols:]
        for i, name in enumerate(header):
            if not name:
                continue
            val = values[i] if i < len(values) else ""
            if val in EMPTY_TOKENS:
                continue
            records.append({"section": section, "dim_label": label, "dim": dim,
                            "dim_kind": kind, "metric": name, "value": val})
    return records


# ---------------------------------------------------------------- 通用小工具
def _numeric(text: str) -> float | None:
    if text is None:
        return None
    s = str(text).replace(",", "").replace("%", "").replace("天", "").strip()
    if not s or s in EMPTY_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm(s: str) -> str:
    for ch in " （）()【】[]-_/、,，:：.　":
        s = s.replace(ch, "")
    return s.strip().lower()


def _envelope(**kw) -> dict:
    env = {
        "shape": "table",
        "title": "",
        "summary": "",
        "report_id": "",
        "report_dt": "",
        "date_source": "",
        "notice": "",
        "url": "",
        "unit": "",
        "table": {"columns": [], "rows": []},
        "notes": [],
    }
    env.update(kw)
    return env


def _terms(raw: str) -> list[str]:
    """把用户问法展开成候选指标关键词（含别名）。"""
    key = _norm(raw)
    terms = [key] if key else []
    for alias, targets in ALIASES.items():
        na = _norm(alias)
        if not na:
            continue
        if na in key or (len(key) >= 2 and key in na):
            terms.extend(_norm(t) for t in targets)
    return list(dict.fromkeys(t for t in terms if t))


def _name_hit(name: str, terms: list[str]) -> bool:
    n = _norm(name)
    if not n:
        return False
    # 整体情况表的指标名带「折人民币」前缀（如「损益合计折人民币（不含内部账）」），
    # 与分产品明细表的同名口径对不上，比较前统一剥掉，否则跨表勾稽/占比永远匹配不到。
    n2 = n.replace("折人民币", "")
    for t in terms:
        if not t:
            continue
        t2 = t.replace("折人民币", "")
        if t in n or (t2 and t2 in n2):
            return True
        if len(t) >= 3 and (n in t or (n2 and n2 in t2)):
            return True
    return False


def _dim_match(value: str, flt: str) -> bool:
    if not flt:
        return True
    v, f = _norm(value), _norm(flt)
    return bool(v) and (f in v or v in f)


def _suggest(fields: list[str], raw: str, n: int = 5) -> list[str]:
    pool = {f: _norm(f) for f in fields}
    key = _norm(raw)
    if not key:
        return []
    matched = difflib.get_close_matches(key, list(pool.values()), n=n, cutoff=0.2)
    out = []
    for norm_name in matched:
        for orig, nm in pool.items():
            if nm == norm_name and orig not in out:
                out.append(orig)
                break
    return out


RATIO_KEYS = ("率", "占比", "比重", "比例")


def _is_ratio(text: str) -> bool:
    return any(k in (text or "") for k in RATIO_KEYS)


def _pick_col(cols: list[str], core: str, terms: list[str], min_idx: int = 3) -> int | None:
    """优先取名称直接包含 core 的列；比率类指标不允许退化为金额类别名。"""
    if core:
        nc = _norm(core)
        for i, c in enumerate(cols):
            if i >= min_idx and nc and nc in _norm(c):
                return i
        if _is_ratio(core):
            return None
    return next((i for i, c in enumerate(cols) if i >= min_idx and _name_hit(c, terms)), None)


# ---------------------------------------------------------------- 维度校验
def _dim_notes(records: list[dict]) -> list[str]:
    """整体表的维度自检：分组、码值是否在表内、标签是否合法。"""
    out: list[str] = []
    groups: dict[str, list[str]] = {}
    for r in records:
        bucket = groups.setdefault(r["dim_kind"], [])
        if r["dim"] not in bucket:
            bucket.append(r["dim"])
    if not groups:
        return out

    if len(groups) > 1 or "合计" not in groups:
        order = [k for k in ("投资组合", "产品分类", "合计") if k in groups]
        order += [k for k in groups if k not in order]
        out.append("维度口径：" + "；".join(f"{k}（{'、'.join(groups[k])}）" for k in order)
                   + "。不同维度之间不可相加，请按维度分别解读。")

    codes = {c.upper() for c in PORTFOLIO_CODES}
    bad_pf = sorted({r["dim"] for r in records
                     if r["dim_kind"] == "投资组合" and codes and r["dim"].upper() not in codes})
    if bad_pf:
        out.append(f"⚠「按投资组合」下出现码值表之外的取值：{'、'.join(bad_pf)}。"
                   f"可能是新增组合或拼写错误，请与报表方确认后再引用。")

    cats = set(PRODUCT_CATEGORIES)
    bad_cat = sorted({r["dim"] for r in records
                      if r["dim_kind"] == "产品分类" and cats and r["dim"] not in cats})
    if bad_cat:
        out.append(f"⚠「按产品分类」下出现码值表之外的取值：{'、'.join(bad_cat)}。请与报表方确认。")

    bad_label = sorted({r["dim_label"] for r in records
                        if r["dim_label"] and r["dim_label"] not in DIM_LABELS})
    if bad_label:
        out.append(f"⚠ 维度列出现预期之外的类型标签：{'、'.join(bad_label)}；"
                   f"预期为「按投资组合 / 按产品分类 / 合计」，已按取值反推归类。")

    unknown = sorted({r["dim"] for r in records if r["dim_kind"] == "未知"})
    if unknown:
        out.append(f"⚠ 以下取值既不在投资组合码表、也不在产品分类表中：{'、'.join(unknown)}。")
    return out


# ---------------------------------------------------------------- 取报表
def query_report(report_id: str, report_dt: str = "", base_url: str | None = None) -> dict:
    cfg = REPORTS.get(report_id)
    if cfg is None:
        return _envelope(
            shape="text", title="报表编号无效",
            summary=f"不支持的报表标识 {report_id}，可选：贵金属业务（分产品明细）、贵金属业务-整体情况表。",
        )

    dinfo = resolve_date(report_dt)
    if "error" in dinfo:
        return _envelope(shape="text", title="日期参数错误", summary=dinfo["error"], report_id=cfg["short"])

    dt = dinfo["report_dt"]
    url = build_url(report_id, dt, base_url)

    notes = [f"报表：{cfg['short']}", f"金额单位：{cfg['unit']}"]
    tables: list = []
    strategy = ""
    source = "page"

    # 第一优先：报表导出接口（服务端生成 CSV/XLSX，比解析 HTML 稳）
    exp = fetch_export(report_id, dt, cfg.get("layout", "sections"), base_url)
    if exp.get("ok"):
        tables, strategy, source = exp["tables"], "export", "export"
        notes.append(f"数据来源：报表导出文件（{exp['detail']}），本次未解析页面 HTML。")
        notes.append(f"导出地址：{exp['url']}")
    elif not exp.get("skipped"):
        notes.append(f"⚠ 已配置导出接口但本次未成功：{exp.get('error')}；已回退到解析页面 HTML。")

    render_note = ""
    if not tables:
        try:
            html = fetch_html(url)
        except urllib.error.HTTPError as e:
            return _envelope(shape="text", title=cfg["title"], report_id=cfg["short"], report_dt=dt,
                             date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                             summary=f"报表页面返回 HTTP {e.code}（{url}）。请确认报表编号与日期，或报表服务是否正常。",
                             notes=notes)
        except Exception as e:
            return _envelope(shape="text", title=cfg["title"], report_id=cfg["short"], report_dt=dt,
                             date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                             summary=f"无法打开报表页面：{e}。请确认报表服务地址（当前 {DEFAULT_BASE_URL}）可访问、"
                                     f"服务已启动；若页面由前端 JS 渲染，纯 HTTP 抓取拿不到数据。",
                             notes=notes)

        ext = extract_tables(html)
        strategy = ext["strategy"]
        if strategy == "none" and RENDER_MODE != "off":
            # 纯 HTTP 拿不到结构：多半是 JS 渲染，交给无头浏览器再试一次
            rendered, msg = render_with_browser(url)
            if rendered:
                ext2 = extract_tables(rendered)
                if ext2["strategy"] != "none":
                    ext, strategy = ext2, ext2["strategy"]
                    render_note = f"{msg}后解析成功（策略：{STRATEGY_NAMES[strategy]}）。"
        tables = ext["tables"]
        if not tables:
            return _envelope(shape="text", title=cfg["title"], report_id=cfg["short"], report_dt=dt,
                             date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                             summary="页面已打开，但未从页面里识别出任何行列结构。"
                                     "常见原因：页面由前端 JS 异步加载（可用 render 配置开无头浏览器）、"
                                     "需要登录态、或报表改版换了结构。"
                                     "建议先调 precious_metals_probe 做一次页面结构体检。",
                             notes=notes + ([render_note] if render_note else []))

    if len(tables) > 1:
        notes.append(f"页面含 {len(tables)} 张表格，已按文档顺序合并解析，未丢弃任何板块。")
    if source == "page" and strategy != "table":
        notes.append(f"⚠ 页面不是标准 <table> 结构，已按「{STRATEGY_NAMES.get(strategy, strategy)}」抽取，"
                     f"列对齐可能不如标准表格稳定，引用前请抽查 1~2 个数值。")
    if render_note:
        notes.append(render_note)
    if dinfo["notice"]:
        notes.append(dinfo["notice"])

    src_tag = "（数据来自导出文件）" if source == "export" else ""

    if cfg["layout"] == "matrix":
        parsed = parse_matrix(_pick_matrix_table(tables))
        if not parsed["rows"]:
            return _envelope(shape="empty", title=cfg["title"], report_id=cfg["short"], report_dt=dt,
                             date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                             unit=cfg["unit"],
                             summary=f"「{cfg['short']}」在 {dt} 无数据：页面可正常打开且表头完整，但没有任何数据行。"
                                     f"这不是报错，请告知用户该日期暂无数据，不要推测数值。",
                             notes=notes)
        return _envelope(
            shape="table", title=cfg["title"],
            summary=f"「{cfg['short']}」共 {len(parsed['rows'])} 行 × {len(parsed['columns'])} 列，"
                    f"报表日期 {dt}{src_tag}。",
            report_id=cfg["short"], report_dt=dt, date_source=dinfo["date_source"],
            notice=dinfo["notice"], url=url, unit=cfg["unit"], table=parsed, notes=notes,
            source=source)

    records = parse_sections(_merge_tables(tables))
    if not records:
        return _envelope(shape="empty", title=cfg["title"], report_id=cfg["short"], report_dt=dt,
                         date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                         unit=cfg["unit"],
                         summary=f"「{cfg['short']}」在 {dt} 无数据（页面结构正常但无指标行），请告知用户该日期暂无数据。",
                         notes=notes)

    # 第一列维度类型（按投资组合/按产品分类/合计）+ 第二列码值，逐条带上，避免混排串口径
    rows = [[r["section"], r["dim_kind"], r["dim"], r["metric"], r["value"]] for r in records]
    kinds = sorted({r["dim_kind"] for r in records if r["dim_kind"]})
    notes.extend(_dim_notes(records))
    summary = (f"「{cfg['short']}」共 {len(rows)} 项指标，报表日期 {dt}"
               f"（维度：{'/'.join(kinds) if kinds else '合计'}，已按板块归一化）{src_tag}。")
    return _envelope(
        shape="table", title=cfg["title"], summary=summary,
        report_id=cfg["short"], report_dt=dt, date_source=dinfo["date_source"],
        notice=dinfo["notice"], url=url, unit=cfg["unit"],
        table={"columns": ["板块", "维度类型", "维度值", "指标", "数值"], "rows": rows}, notes=notes,
        source=source)


def probe(report_id: str = "8666", report_dt: str = "", base_url: str | None = None) -> dict:
    """页面结构体检：不做业务解析，只回答「这个页面长什么样、能不能解析、该走哪条路」。

    换到内网、或报表改版后被问「为什么取不到数」时，先跑这个工具，
    它能区分：标准表格 / div 栅格 / 重复块 / 纯文本 / JS 空壳 / 需要登录。
    """
    rid = (report_id or "8666").strip()
    if rid not in REPORTS:
        rid = "8666"
    cfg = REPORTS[rid]
    dinfo = resolve_date(report_dt)
    if "error" in dinfo:
        return _envelope(shape="text", title="日期参数错误", summary=dinfo["error"])
    dt = dinfo["report_dt"]
    url = build_url(rid, dt, base_url)

    try:
        html = fetch_html(url)
    except urllib.error.HTTPError as e:
        return _envelope(shape="text", title="页面打不开", report_id=cfg["short"], report_dt=dt, url=url,
                         summary=f"报表页面返回 HTTP {e.code}，这不是解析问题，是地址/权限/报表编号问题。")
    except Exception as e:
        return _envelope(shape="text", title="页面打不开", report_id=cfg["short"], report_dt=dt, url=url,
                         summary=f"无法连接报表服务（当前 {DEFAULT_BASE_URL}）：{e}")

    ext = extract_tables(html)
    dom = _DomParser()
    dom.feed(html)
    dom_nodes = list(_walk(dom.root))
    text_only = re.sub(r"(?s)<[^>]+>", " ", html)
    text_only = " ".join(html_unescape(text_only).split())
    numbers = re.findall(r"-?\d[\d,]*(?:\.\d+)?", text_only)
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    export_links = re.findall(r'(?i)href="([^"]*(?:export|excel|csv|download|xls)[^"]*)"', html)
    script_n = len(re.findall(r"(?is)<script\b", html))
    iframe_n = len(re.findall(r"(?is)<iframe\b", html))

    rows: list[list[str]] = [
        ["报表地址", url, "实际请求的地址"],
        ["页面体积", f"{len(html.encode('utf-8')):,} 字节", "JS 空壳通常 2KB 以内"],
        ["页面标题", (title_m.group(1).strip()[:60] if title_m else "（无 <title>）"), "登录页会显示登录/统一认证字样"],
        ["可见文字量", f"{len(text_only)} 字符 / 约 {len(numbers)} 个数字片段", "有数字说明数据在 HTML 里"],
        ["<table> 数量", str(ext["diag"].get("table_count", 0)), "0 表示不是标准表格页"],
        ["表格总行数", str(ext["diag"].get("table_rows", 0)), "行数为 0 但 table 数 >0 多为空壳模板"],
        ["role=row 行数", str(ext["diag"].get("role_rows", 0)), "ARIA 表格/div 栅格的典型标记"],
        ["role=cell 单元格数", str(ext["diag"].get("role_cells", 0)), "同上"],
        ["DOM 节点数", str(len(dom_nodes)), "<20 基本是未渲染的空壳"],
        ["<script> / <iframe>", f"{script_n} / {iframe_n}", "脚本多且文字少 = 前端渲染"],
        ["判定抽取方式", STRATEGY_NAMES.get(ext["strategy"], ext["strategy"]), "本次实际走的那一级降级策略"],
    ]

    ok_tables = [t for t in ext["tables"] if len(t) >= 2]
    if ok_tables:
        preview = _cells(ok_tables[0][0])[:6]
        rows.append(["表头预览（首行）", " | ".join(c for c in preview if c), "对照页面第一行表头"])

    render_note = ""
    final_strategy = ext["strategy"]
    if ext["strategy"] == "none" and RENDER_MODE != "off":
        rendered, msg = render_with_browser(url)
        if rendered:
            ext2 = extract_tables(rendered)
            if ext2["strategy"] != "none":
                final_strategy = ext2["strategy"]
                render_note = f"{msg}后识别为「{STRATEGY_NAMES[final_strategy]}」，说明页面需要 JS 渲染才能取到数据。"
                rows.append(["无头浏览器渲染", f"成功（{STRATEGY_NAMES[final_strategy]}）", render_note])
            else:
                render_note = f"{msg}后仍无结构，页面可能需要登录态或数据不在 DOM 里。"
                rows.append(["无头浏览器渲染", "无结构", render_note])
        else:
            render_note = msg
            rows.append(["无头浏览器渲染", "未成功", msg])

    if export_links:
        uniq = list(dict.fromkeys(export_links))[:5]
        rows.append(["页面上的导出/下载链接", str(len(uniq)) + " 条", "；".join(uniq)])
    else:
        rows.append(["页面上的导出/下载链接", "无", "若页面是 JS 渲染，优先找平台的导出接口，比解析 HTML 稳"])

    # 导出接口体检：配置了就直接试一次，这是最该优先走的路
    exp = fetch_export(rid, dt, cfg.get("layout", "sections"), base_url)
    if exp.get("skipped"):
        rows.append(["导出接口（export_url）", "未配置",
                     "平台一般有导出按钮：把它的地址填进 config.json 的 export_url，"
                     "取数会优先解析导出文件，页面改版/JS 渲染都不影响"])
    elif exp.get("ok"):
        rows.append(["导出接口（export_url）", f"可用（{exp['kind']}）", exp["detail"]])
        rows.append(["导出地址", exp["url"], "取数时优先走这条路，失败才回退页面"])
    else:
        rows.append(["导出接口（export_url）", "失败", f"{exp.get('error')}；地址：{exp['url']}"])

    strategy = ext["strategy"]
    if final_strategy != strategy:
        advice = ("页面是 JS 渲染的空壳：直接抓 HTML 没有数据，无头浏览器渲染后可以取到。"
                  "config.json 的 render=auto 已能自动处理；若每次都慢，可把 render 设为 force 省掉一次失败尝试。")
    elif strategy == "table":
        advice = "标准表格，解析可靠，无需额外处理。"
    elif strategy == "grid":
        advice = "div 栅格可解析，但列对齐依赖 role/class 命名；报表改版后需复核首行表头。"
    elif strategy == "blocks":
        advice = "重复块可解析，靠兄弟节点结构推断行列，建议抽查首尾两行。"
    elif strategy == "text":
        advice = "只能按文本行重建，列对齐最脆弱，仅作应急；优先考虑导出接口或渲染模式。"
    else:
        advice = ("没有识别出任何行列结构。按此顺序排查："
                  "① config.json 设 render=force 走无头浏览器；"
                  "② 确认是否需要登录态（请求未带 cookie）；"
                  "③ 找平台的导出/接口地址替代抓页面。")

    if exp.get("ok"):
        advice = (f"导出接口可用（{exp['kind']}）：取数会优先解析导出文件，"
                  f"页面改版、JS 渲染、合并单元格写法变化都不影响结果。{advice}")
    elif exp.get("skipped"):
        advice = ("未配置导出接口，取数只能解析页面 HTML。" + advice)

    notes = [f"报表：{cfg['short']}", f"当前地址来源：{BASE_URL_SOURCE}",
             f"渲染模式：{RENDER_MODE}（config.json 的 render，可选 auto/off/force）"]
    verdict = (f"直接抓 HTML 判定为「{STRATEGY_NAMES.get(strategy, strategy)}」，"
               f"无头浏览器渲染后判定为「{STRATEGY_NAMES.get(final_strategy, final_strategy)}」"
               if final_strategy != strategy
               else f"判定为「{STRATEGY_NAMES.get(final_strategy, final_strategy)}」")
    return _envelope(
        shape="table", title=f"页面结构体检：{cfg['short']}",
        summary=f"{dt} 的页面{verdict}。{advice}",
        report_id=cfg["short"], report_dt=dt, date_source=dinfo["date_source"],
        notice=dinfo["notice"], url=url, unit="",
        table={"columns": ["检查项", "结果", "说明"], "rows": rows}, notes=notes)


def _detail_records(env: dict) -> list[dict]:
    """8670 明细：拍平成 {row_key, dims, metric, value} 记录。"""
    if env.get("shape") != "table":
        return []
    cols = env["table"]["columns"]
    out = []
    for row in env["table"]["rows"]:
        dims = {"投资组合": row[0], "产品分类": row[1], "五级产品": row[2]}
        for i in range(3, len(cols)):
            out.append({"row_key": f"{row[0]}/{row[1]}/{row[2]}", "dims": dims,
                        "metric": cols[i], "value": row[i] if i < len(row) else ""})
    return out


def _overview_records(env: dict) -> list[dict]:
    """8666 整体表：拍平成 {row_key, section, dim, dim_kind, dims, metric, value}。

    第一列可能是合计、投资组合码值或产品分类，row_key 用「板块/维度」表达，
    避免不同维度下的同名指标被当成同一条记录。
    """
    if env.get("shape") != "table":
        return []
    cols = env["table"]["columns"]
    idx = {c: i for i, c in enumerate(cols)}

    def cell(row: list[str], *names: str) -> str:
        for n in names:
            i = idx.get(n)
            if i is not None and i < len(row):
                return row[i]
        return ""

    out = []
    for row in env["table"]["rows"]:
        section = cell(row, "板块")
        dim = cell(row, "维度值", "统计维度") or "合计"
        kind = cell(row, "维度类型") or _classify_dim(dim)
        out.append({
            # 行标签带上维度类型，避免「TGLD」与「贵金属即远期」在同列混排时被误读为同一维度
            "row_key": (section if dim in ("", "合计")
                        else f"{section}/{kind} {dim}") if section else dim,
            "section": section, "dim": dim, "dim_kind": kind,
            "dims": {"板块": section, "维度": dim, "维度类型": kind},
            "metric": cell(row, "指标"), "value": cell(row, "数值"),
        })
    return out


def _auto_dims(text: str, detail: list[dict]) -> dict:
    """从整句里自动识别投资组合 / 产品分类 / 五级产品。"""
    found: dict[str, str] = {}
    low = _norm(text)
    for field in ("五级产品", "产品分类", "投资组合"):   # 细粒度优先
        for rec in detail:
            val = rec["dims"].get(field, "")
            if val and val != "合计" and _norm(val) in low and field not in found:
                found[field] = val
    # 投资组合是 4 位码值：即使当期报表没有该行，只要问题里出现合法码值就识别出来，
    # 以便后续走「该码值当期无数据」的明确提示，而不是误判为指标名的一部分。
    if "投资组合" not in found and PORTFOLIO_CODES:
        up = (text or "").upper()
        for code in PORTFOLIO_CODES:
            if re.search(r"(?<![A-Z0-9])" + code + r"(?![A-Z0-9])", up):
                found["投资组合"] = code
                break
    return found


DATE_PATTERNS = (
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日",
    r"\d{4}\s*年\s*\d{1,2}\s*月",
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
)
TIME_WORDS = ("昨天", "昨日", "今天", "今日", "当天", "当日", "上个月", "上月", "本月",
              "这个月", "上个季度", "上季度", "本季度", "去年", "今年", "上一年", "上半年", "下半年")
# 用户口语里会顺带说出维度字段名，这些词不是维度值，必须从指标名里剥掉
DIM_MENTION = ("这个投资组合", "该投资组合", "上述投资组合", "这个组合", "该组合", "投资组合",
               "这个产品分类", "该产品分类", "产品分类",
               "这个五级产品", "该五级产品", "五级产品",
               "这个产品", "该产品", "这个品种", "该品种", "这个币种", "该币种")
FILLER = ("请问", "帮我", "帮忙", "查一下", "看一下", "看一下呢", "麻烦", "请教",
          "告诉我", "我想知道", "看一下", "调出来", "取一下",
          "整个", "全行", "全公司", "本公司", "咱们",
          "一共做了", "总共做了", "一共", "总共", "做了", "累计", "大约", "大概")
# 用户会直接说报表名，报表名不是维度，剥掉后按全口径查询
REPORT_SCOPE_WORDS = ("贵金属业务-整体情况表", "贵金属业务整体情况表", "整体情况表",
                      "贵金属业务（分产品明细）", "贵金属业务分产品明细", "分产品明细表", "分产品明细")
DUAL_SCOPE = ("含内部账和不含内部账", "含内部账与不含内部账", "含内部账及不含内部账",
              "含和不含内部账", "含内部账、不含内部账")
SHARE_RE = re.compile(r"占\s*(.{1,20}?)\s*(?:的)?\s*(?:比重|占比|比例|百分比)")


def _strip_time(s: str) -> str:
    for pat in DATE_PATTERNS:
        s = re.sub(pat, "", s)
    for w in TIME_WORDS:
        s = s.replace(w, "")
    return s


def _metric_core(raw: str, dims: dict) -> str:
    """从整句里剥掉维度词、时间、口语修饰与助词，得到纯指标名。"""
    s = raw or ""
    for v in dims.values():
        if v:
            s = s.replace(v, "")
    s = _strip_time(s)
    for w in DIM_MENTION:
        s = s.replace(w, "")
    for w in REPORT_SCOPE_WORDS:
        s = s.replace(w, "")
    for w in FILLER:
        s = s.replace(w, "")
    for w in DUAL_SCOPE:
        s = s.replace(w, "")
    # 占比意图：「占 X 的比重」问的是 X 占整体的比例，指标主体是 X
    mm = SHARE_RE.search(s)
    if mm:
        s = mm.group(1)
        for w in ("整体", "全行", "全业务", "全部", "整个"):
            s = s.replace(w, "")
    # 泛称（「贵金属的损益合计」里的"贵金属"）不属于维度，剥掉以免污染指标名匹配
    for g in sorted(GENERIC_SCOPES, key=len, reverse=True):
        if g in s and len(s) > len(g):
            s = s.replace(g, "")
    for ch in ("的", "是多少", "多少钱", "怎么样", "情况", "多少", "呢", "吗", "占",
               "里面", "里边", "当中", "里", "中"):
        s = s.replace(ch, "")
    return s.strip()


# 「贵金属的损益合计」这类泛称不算未匹配维度，避免把全业务口径的问法误拦
GENERIC_SCOPES = {"贵金属", "黄金", "白银", "全行", "全业务", "全部", "整体", "总体",
                  "这些", "报表", "两张报表", "整体情况", "贵金属业务"}


def _unmatched_dim_hint(raw: str, auto: dict) -> str:
    """问句形如「XX的YY」，XX 对不上任何维度值、也不是泛称 → 返回 XX。

    存在这种情况时不能静默按全业务口径作答，否则用户会把合计值当成 XX 的数值。
    """
    if auto or not raw:
        return ""
    # 「昨天/6月30日 贵金属业务的…」里的时间与泛称都不是维度，先剥干净再判断
    probe = _strip_time(raw)
    for w in FILLER:
        probe = probe.replace(w, "")
    if "的" not in probe:
        return ""
    prefix = probe.split("的", 1)[0].strip()
    for w in DIM_MENTION:
        prefix = prefix.replace(w, "").strip()
    for w in ("整个", "全行", "全公司", "本公司", "咱们"):
        prefix = prefix.replace(w, "").strip()
    if len(prefix) < 2:
        return ""
    # 说报表名不等于指定维度
    if any(w in prefix for w in REPORT_SCOPE_WORDS):
        return ""
    # 只有「整个就是泛称」才放行；「黄金租赁」含"黄金"但它是具体品种，必须拦
    if prefix in GENERIC_SCOPES:
        return ""
    # 前缀本身就是指标名（如「损益合计的占比」）时，不算维度意图
    pool = REPORTS["8670"]["fields"] + REPORTS["8666"]["fields"]
    pterms = _terms(prefix)
    if pterms and any(_name_hit(f, pterms) for f in pool):
        return ""
    return prefix


def _similar_dim_values(hint: str, detail: list[dict], n: int = 4) -> list[str]:
    """在当期实际出现的五级产品里，按字面相近度给出候选。"""
    vals = sorted({r["dims"].get("五级产品", "") for r in detail} - {"", "合计"})
    key = _norm(hint)
    scored = []
    for v in vals:
        nv = _norm(v)
        overlap = len(set(key) & set(nv))
        ratio = difflib.SequenceMatcher(None, key, nv).ratio()
        scored.append((overlap * 0.6 + ratio, v))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [v for sc, v in scored[:n] if sc > 0.6]


# ---------------------------------------------------------------- 指标查询
def query_metric(metric: str, report_dt: str = "", portfolio: str = "", product: str = "",
                 level5: str = "", section: str = "", base_url: str | None = None) -> dict:
    raw = (metric or "").strip()
    if not raw and not (portfolio or product or level5):
        return _envelope(shape="text", title="指标名为空",
                         summary="请提供指标名（如「损益合计」「期末余额」），或至少一个维度过滤条件。")

    detail_env = query_report("8670", report_dt, base_url)
    overview_env = query_report("8666", report_dt, base_url)

    dt = detail_env.get("report_dt") or overview_env.get("report_dt") or ""
    date_source = detail_env.get("date_source") or overview_env.get("date_source") or ""
    notices = [n for n in (detail_env.get("notice"), overview_env.get("notice")) if n]
    notice = "；".join(dict.fromkeys(notices))
    if detail_env.get("shape") != "table" and overview_env.get("shape") != "table":
        return _envelope(shape="text", title="取数失败", report_dt=dt, date_source=date_source,
                         notice=notice,
                         summary="；".join(e.get("summary", "") for e in (detail_env, overview_env)))

    detail = _detail_records(detail_env)
    overview = _overview_records(overview_env)

    auto = _auto_dims(raw, detail) if raw else {}
    core = _metric_core(raw, auto) if raw else ""
    terms = _terms(core) if core else []
    if not terms and raw:
        terms = _terms(raw)
    use_portfolio = portfolio or auto.get("投资组合", "")
    use_product = product or auto.get("产品分类", "")
    use_level5 = level5 or auto.get("五级产品", "")
    row_filtered = bool(use_portfolio or use_product or use_level5)

    # ---- 说了维度但没对上：不能静默按全业务口径作答（否则合计会被当成该维度的值）
    hint = _unmatched_dim_hint(raw, auto) if not row_filtered else ""
    if hint:
        near = _similar_dim_values(hint, detail)
        return _envelope(
            shape="text", title=f"未能匹配该维度：{hint}",
            report_dt=dt, date_source=date_source, notice=notice,
            summary=(f"「{hint}」不在 {dt} 报表的维度取值中（既不是五级产品，也不是产品分类）。"
                     f"当前 MCP 不支持按「{hint}」这个范围查询，因此本次不返回结果——"
                     f"若直接给出全业务合计，会把它误当成「{hint}」的数值。"),
            notes=([f"当期五级产品中字面相近的有：{'、'.join(near)}"] if near else [])
                  + ["如需全业务合计口径，请明确说明「全业务合计」；如需确认维度取值，"
                     "可调 precious_metals_describe(field=五级产品)。"],
        )

    # ---- 维度值合法性校验（防止拿中文业务名去匹配码值，静默返回空）
    if use_portfolio:
        codes = [c for c in PORTFOLIO_CODES if c.upper() == use_portfolio.strip().upper()]
        if codes:
            use_portfolio = codes[0]
        elif PORTFOLIO_CODES and not any(
                _dim_match(r["dims"].get("投资组合", ""), use_portfolio) for r in detail):
            return _envelope(
                shape="text", title=f"投资组合只能按码值筛选：{use_portfolio}",
                report_dt=dt, date_source=date_source, notice=notice,
                summary=(f"「{REPORTS['8670']['short']}」的「投资组合」列直接显示 4 位英文码值，没有中文名称，"
                         f"因此无法用「{use_portfolio}」这类中文描述匹配。"),
                notes=[f"可选码值（共 {len(PORTFOLIO_CODES)} 个）：" + "、".join(PORTFOLIO_CODES),
                       "如需按业务含义筛选，请改用 level5（五级产品，中文，例：贵金属租出-企业 / Au99.99现货实盘）"
                       "或 product（产品分类，五选一：贵金属即远期 / 贵金属掉期 / 贵金属期货延期 / 贵金属期权 / 其他）。"])

    if use_product and PRODUCT_CATEGORIES:
        exact = [c for c in PRODUCT_CATEGORIES if c == use_product.strip()]
        if exact:
            use_product = exact[0]
        elif not any(_dim_match(r["dims"].get("产品分类", ""), use_product) for r in detail):
            return _envelope(
                shape="text", title=f"产品分类取值不合法：{use_product}",
                report_dt=dt, date_source=date_source, notice=notice,
                summary=f"产品分类只有 {len(PRODUCT_CATEGORIES)} 个固定取值，没有「{use_product}」。",
                notes=[f"合法取值：{'、'.join(PRODUCT_CATEGORIES)}"])

    hits: list[list[str]] = []
    for rec in detail:
        d = rec["dims"]
        if not (_dim_match(d["投资组合"], use_portfolio) and _dim_match(d["产品分类"], use_product)
                and _dim_match(d["五级产品"], use_level5)):
            continue
        # 问「交易笔数」时不要带出「新发生-交易笔数」，除非用户明确提到新发生
        if core and "新发生" not in core and rec["metric"].startswith("新发生"):
            continue
        if not terms or _name_hit(rec["metric"], terms):
            num = _numeric(rec["value"])
            hits.append([REPORTS["8670"]["short"], rec["row_key"], rec["metric"], rec["value"],
                         "" if num is None else f"{num:.2f}", "万元人民币"])

    # 整体表始终参与查询：带维度过滤时不并入主结果（它是全业务合计，混排会串口径），
    # 但作为参考值放进 notes，保证「表里有的数」不会取不到。
    ov_hits: list[list[str]] = []
    for rec in overview:
        if section and not _dim_match(rec.get("section", ""), section):
            continue
        if not terms or _name_hit(rec["metric"], terms):
            num = _numeric(rec["value"])
            unit = "%" if rec["value"].endswith("%") else "万元人民币"
            ov_hits.append([REPORTS["8666"]["short"], rec["row_key"], rec["metric"], rec["value"],
                            "" if num is None else f"{num:.2f}", unit])

    if not row_filtered:
        hits.extend(ov_hits)

    # 精确优先：命中里存在名称直接含 core 的项时，丢弃仅靠别名带出的近似项
    ratio_missing = False
    if core:
        nc = _norm(core)
        core_hits = [h for h in hits if nc in _norm(h[2])]
        if core_hits:
            hits = core_hits
        elif _is_ratio(core):
            # 比率类指标不能拿金额类指标顶替（量纲不同）
            hits = []
            ratio_missing = True

    # 占比问法（「X 占整体的比重」）：主语是 X，用户要的是合计口径，优先保留「合计」类指标
    share_intent = bool(SHARE_RE.search(raw))
    if share_intent and hits:
        pref = [h for h in hits if "合计" in h[2]]
        if pref:
            hits = pref

    # 整体口径问法（未指定维度、也没说"各/分别"）把合计行置顶，避免模型在明细里挑错行
    if not row_filtered and hits and not re.search(r"各|每个|分别|逐个|每一|明细", raw):
        totals = [h for h in hits if str(h[1]).startswith("合计")]
        if totals:
            hits = totals + [h for h in hits if not str(h[1]).startswith("合计")]

    # ---- 维度过滤后无行：分清「该组合当期无数据」与「该指标没有分项口径」
    ref_note = ([f"参考（全业务合计，不等于所选维度的值，不可替代）："
                 + "；".join(f"{h[2]}={h[3]}（数值_num={h[4]}）" for h in ov_hits[:6])] if ov_hits else [])

    if not hits and row_filtered:
        dim_desc = " / ".join(x for x in (use_portfolio, use_product, use_level5) if x)
        metric_has_detail = bool(terms) and any(_name_hit(f, terms) for f in REPORTS["8670"]["fields"])
        if metric_has_detail:
            combos = sorted({r["row_key"] for r in detail if r["row_key"].split("/")[0] != "合计"})
            return _envelope(
                shape="empty", title=f"报表中没有该组合的数据：{dim_desc}",
                report_dt=dt, date_source=date_source, notice=notice,
                summary=(f"「{core or raw}」这项指标在「{REPORTS['8670']['short']}」中是有明细口径的，"
                         f"但按「{dim_desc}」筛选后没有任何匹配行——说明该组合在 {dt} 的报表里没有数据。"
                         f"当前 MCP 无法取到该组合的数值，请先确认该码值/品种名是否属于当期报表。"),
                notes=ref_note + [f"当期报表实际有 {len(combos)} 个组合：" + "；".join(combos),
                                  "确认维度取值：precious_metals_describe(field=投资组合 / 产品分类 / 五级产品)。"],
            )
        near = _suggest(REPORTS["8670"]["fields"], core or raw, 4)
        if ov_hits:
            return _envelope(
                shape="text", title=f"该指标只有全业务合计口径：{core or raw}",
                report_dt=dt, date_source=date_source, notice=notice,
                summary=(f"「{ov_hits[0][2]}」只在「{REPORTS['8666']['short']}」里提供全业务合计口径，"
                         f"「{REPORTS['8670']['short']}」中没有这项指标，因此取不到「{dim_desc}」的分项数值。"
                         f"当前 MCP 不支持按维度查询该指标，请勿用全业务合计代替该分项值。"),
                notes=ref_note + ([f"口径相近的明细指标：" + "、".join(near)] if near else []),
            )

    if not hits:
        pool = REPORTS["8670"]["fields"] + REPORTS["8666"]["fields"]
        if ratio_missing:
            ratio_fields = [f for f in pool if _is_ratio(f)]
            return _envelope(
                shape="text", title=f"报表无比率类指标：{core}", report_dt=dt,
                date_source=date_source, notice=notice,
                summary=(f"两张报表中没有「{core}」这类比率指标，当前 MCP 不支持查询。"
                         f"比率类指标只有：{'、'.join(ratio_fields[:8])}。"),
            )
        return _envelope(
            shape="text", title=f"当前 MCP 不支持查询该指标：{core or raw}",
            report_dt=dt, date_source=date_source, notice=notice,
            summary=f"两张报表中都没有包含「{core or raw}」的指标，当前 MCP 不支持该查询。"
                    f"请改用标准指标名，或先用 precious_metals_list_reports 查看可用字段。",
            notes=[f"可能是你想找的：{'、'.join(_suggest(pool, core or raw, 5))}"] if raw else [],
        )

    cols = ["报表", "板块/行", "指标", "数值", "数值_num", "单位"]
    if not core and row_filtered:
        summary = f"未指定指标，返回该维度下全部 {len(hits)} 项指标，报表日期 {dt}。"
        title = f"维度明细：{use_portfolio}{use_product}{use_level5}"
    else:
        summary = f"命中 {len(hits)} 处，报表日期 {dt}。"
        title = f"指标查询：{core or raw}" + (f"（{use_portfolio}{use_level5}{use_product}）" if row_filtered else "")

    notes = ["数值_num 为去掉千分位的浮点数，用于排序/加减/占比计算。"]
    if auto:
        notes.append(f"已从问题中自动识别维度：{auto}")
    if core and not any(_norm(core) in _norm(h[2]) for h in hits):
        notes.append(f"报表中没有名称含「{core}」的指标，以上为口径最接近的替代指标，"
                     f"回复时请向用户说明这一替换关系，不要直接当成原指标。")
    if ref_note and hits and row_filtered:
        # 只有按维度过滤时整体表才被排除，这时需要把全业务合计带回来供算占比/勾稽；
        # 未加维度过滤时 hits 里本来就含整体表行，再重复一遍是噪音。
        notes.extend(ref_note)
        notes.append("如需计算占比：用所选维度的 数值_num ÷ 上面的全业务合计 数值_num。")
    if notice:
        notes.append(notice)

    return _envelope(
        shape="table", title=title, summary=summary, report_id="两张报表",
        report_dt=dt, date_source=date_source, notice=notice, unit="见各行单位",
        table={"columns": cols, "rows": hits}, notes=notes)


# ---------------------------------------------------------------- 排名
def rank(metric: str, top_n: int = 0, order: str = "desc", report_dt: str = "",
         portfolio: str = "", product: str = "", base_url: str | None = None) -> dict:
    env = query_report("8670", report_dt, base_url)
    dt = env.get("report_dt", "")
    if env.get("shape") != "table":
        return _envelope(shape="text", title="排名失败", report_dt=dt,
                         date_source=env.get("date_source", ""), notice=env.get("notice", ""),
                         summary=env.get("summary", ""))

    cols = env["table"]["columns"]
    core = _metric_core(metric, {})
    idx = _pick_col(cols, core, _terms(core or metric))
    if idx is None or idx < 3:
        return _envelope(shape="text", title=f"无法排名：{metric}", report_dt=dt,
                         date_source=env.get("date_source", ""), notice=env.get("notice", ""),
                         summary=f"「贵金属业务（分产品明细）」中没有可用于排名的数值指标「{metric}」。排名仅支持明细表指标"
                                 f"（如「损益合计（不含内部账）」「交易笔数」「名义本金折人民币」）。",
                         notes=[f"可能是你想找的："
                                + "、".join(_suggest(REPORTS["8670"]["fields"], metric, 5))])

    items = []
    for row in env["table"]["rows"]:
        if row[0] in ("合计", "小计", "总计"):
            continue
        if not (_dim_match(row[0], portfolio) and _dim_match(row[1], product)):
            continue
        num = _numeric(row[idx])
        if num is None:
            continue
        items.append((row[0], row[1], row[2], row[idx], num))

    if not items:
        return _envelope(shape="text", title=f"无法排名：{metric}", report_dt=dt,
                         date_source=env.get("date_source", ""), notice=env.get("notice", ""),
                         summary="过滤后没有可比较的数据行。")

    reverse = order != "asc"
    items.sort(key=lambda x: x[4], reverse=reverse)
    if top_n and top_n > 0:
        items = items[:top_n]

    rows = [[str(i + 1), it[0], it[1], it[2], cols[idx], it[3], f"{it[4]:.2f}"]
            for i, it in enumerate(items)]
    return _envelope(
        shape="table", title=f"排名：{cols[idx]}（{'降序' if reverse else '升序'}）",
        summary=f"「贵金属业务（分产品明细）」按「{cols[idx]}」{'降序' if reverse else '升序'}排列，共 {len(rows)} 行，报表日期 {dt}。",
        report_id="贵金属业务（分产品明细）", report_dt=dt, date_source=env.get("date_source", ""),
        notice=env.get("notice", ""), unit="万元人民币",
        table={"columns": ["排名", "投资组合", "产品分类", "五级产品", "指标", "数值", "数值_num"],
               "rows": rows},
        notes=["已排除「合计」行；数值_num 可直接用于比较。"])


# ---------------------------------------------------------------- 两期对比
def compare(metric: str, report_dt: str = "", report_dt_prev: str = "", portfolio: str = "",
            product: str = "", level5: str = "", base_url: str | None = None) -> dict:
    a = query_report("8670", report_dt, base_url)
    dt_a = a.get("report_dt", "")
    if not report_dt_prev:
        try:
            dt_b = prev_month_same_day(datetime.date.fromisoformat(dt_a)).isoformat()
            prev_rule = f"用户未指定对比日期，默认取上一月同日（{dt_b}）"
        except ValueError:
            return _envelope(shape="text", title="对比失败", summary=f"日期不合法：{dt_a}")
    else:
        dinfo = resolve_date(report_dt_prev)
        if "error" in dinfo:
            return _envelope(shape="text", title="对比失败", summary=dinfo["error"])
        dt_b = dinfo["report_dt"]
        prev_rule = "对比日期由用户指定"

    b = query_report("8670", dt_b, base_url)
    for env in (a, b):
        if env.get("shape") != "table":
            return _envelope(shape="text", title="对比失败", report_dt=env.get("report_dt", ""),
                             date_source=env.get("date_source", ""), notice=env.get("notice", ""),
                             summary=f"{env.get('report_dt')}：{env.get('summary')}")

    terms = _terms(metric)
    core = _metric_core(metric, {})
    cols_a, cols_b = a["table"]["columns"], b["table"]["columns"]
    ia = _pick_col(cols_a, core, terms)
    ib = _pick_col(cols_b, core, terms)
    if ia is None or ib is None or ia < 3 or ib < 3:
        return _envelope(shape="text", title=f"无法对比：{metric}", report_dt=dt_a,
                         summary=f"「贵金属业务（分产品明细）」中没有指标「{metric}」，无法做两期对比。",
                         notes=[f"可能是你想找的："
                                + "、".join(_suggest(REPORTS["8670"]["fields"], metric, 5))])

    def index(env, idx):
        out = {}
        for row in env["table"]["rows"]:
            if row[0] in ("合计", "小计", "总计"):
                continue
            if not (_dim_match(row[0], portfolio) and _dim_match(row[1], product)
                    and _dim_match(row[2], level5)):
                continue
            num = _numeric(row[idx])
            if num is None:
                continue
            out[f"{row[0]}/{row[1]}/{row[2]}"] = (row[idx], num)
        return out

    ma, mb = index(a, ia), index(b, ib)
    keys = [k for k in ma if k in mb]
    if not keys:
        tot_a = next((r[ia] for r in a["table"]["rows"] if r[0] == "合计"), "")
        tot_b = next((r[ib] for r in b["table"]["rows"] if r[0] == "合计"), "")
        na, nb = _numeric(tot_a), _numeric(tot_b)
        delta = "" if (na is None or nb is None) else f"{na - nb:,.2f}"
        pct = "" if (na is None or nb in (None, 0)) else f"{(na - nb) / abs(nb) * 100:.2f}%"
        return _envelope(
            shape="table", title=f"对比：{cols_a[ia]}（{dt_a} vs {dt_b}）",
            summary=f"明细行无法一一对应，仅给出合计对比。{prev_rule}。",
            report_id="贵金属业务（分产品明细）", report_dt=dt_a, date_source=a.get("date_source", ""),
            notice=a.get("notice", ""), unit="万元人民币",
            table={"columns": ["口径", dt_a, dt_b, "差额", "变动率"],
                   "rows": [["合计", tot_a, tot_b, delta, pct]]},
            notes=[prev_rule])

    rows = []
    for k in keys:
        va, na = ma[k]
        vb, nb = mb[k]
        delta = na - nb
        pct = "" if nb == 0 else f"{delta / abs(nb) * 100:.2f}%"
        rows.append([k, va, vb, f"{delta:,.2f}", pct])
    rows.sort(key=lambda r: -abs(float(r[3].replace(",", ""))))

    return _envelope(
        shape="table", title=f"对比：{cols_a[ia]}（{dt_a} vs {dt_b}）",
        summary=f"按「{cols_a[ia]}」对比 {dt_a} 与 {dt_b}，共 {len(rows)} 行（按绝对差额降序）。{prev_rule}。",
        report_id="贵金属业务（分产品明细）", report_dt=dt_a, date_source=a.get("date_source", ""),
        notice=a.get("notice", ""), unit="万元人民币",
        table={"columns": ["行（投资组合/产品分类/五级产品）", dt_a, dt_b, "差额", "变动率"],
               "rows": rows},
        notes=[prev_rule, "差额 = 当期 - 对比期；变动率 = 差额 / |对比期|。"])


# ---------------------------------------------------------------- 口径说明
# 维度名 -> 字典键（describe 支持直接查维度取值清单）
DIM_ALIAS = {
    "投资组合": "投资组合", "组合": "投资组合",
    "产品分类": "产品分类", "分类": "产品分类",
    "五级产品": "五级产品", "五级": "五级产品",
}


def describe(field: str = "") -> dict:
    key = (field or "").strip()

    # ---- 维度取值清单优先（投资组合为码值、产品分类为 5 项枚举、五级产品为中文）
    if key:
        dk = next((v for k, v in DIM_ALIAS.items() if k in key or key in k), None)
        cfg = DIM_DICT.get(dk) if dk else None
        if cfg and cfg.get("values"):
            vals = cfg["values"]
            return _envelope(
                shape="table", title=f"维度取值：{key}",
                summary=f"{cfg.get('desc', '')} 共 {len(vals)} 个取值。",
                table={"columns": ["序号", "取值"], "rows": [[str(i + 1), v] for i, v in enumerate(vals)]},
                notes=[(f"全部取值（{len(vals)} 项）：" + "、".join(vals)) if len(vals) <= 40
                       else f"取值较多（共 {len(vals)} 项），前 40 项：" + "、".join(vals[:40]) + " …（其余见 table 列）"])

    if not key:
        rows = [[k, v] for k, v in FIELD_DOCS.items()]
        dims = [n for n in ("产品分类", "投资组合", "五级产品")
                if (DIM_DICT.get(DIM_ALIAS[n]) or {}).get("values")]
        return _envelope(shape="table", title="字段口径说明（全部）",
                         summary=f"共 {len(rows)} 条口径说明；另可用 field={'/'.join(dims)} 查询维度取值清单。",
                         table={"columns": ["字段/口径", "说明"], "rows": rows})
    nk = _norm(key)
    hit = [(k, v) for k, v in FIELD_DOCS.items() if nk and (_norm(k) in nk or nk in _norm(k))]
    if not hit:
        close = difflib.get_close_matches(nk, [_norm(k) for k in FIELD_DOCS], n=4, cutoff=0.2)
        return _envelope(shape="text", title=f"未收录该字段：{key}",
                         summary=f"口径字典中没有「{key}」。不带 field 参数调用可查看全部条目。",
                         notes=[f"相近条目：{'、'.join(close)}"] if close else [])
    rows = [[k, v] for k, v in hit]
    return _envelope(shape="table", title=f"口径说明：{key}", summary=f"命中 {len(rows)} 条。",
                     table={"columns": ["字段/口径", "说明"], "rows": rows})


# ---------------------------------------------------------------- 报表清单
def list_reports() -> dict:
    dinfo = resolve_date("")
    items = [{"report_id": rid, "name": cfg["short"], "desc": cfg["desc"],
              "fields": cfg["fields"], "example_url": build_url(rid, dinfo["report_dt"])}
             for rid, cfg in REPORTS.items()]
    dim_info = {}
    for label, dkey in (("产品分类", "产品分类"), ("投资组合", "投资组合"), ("五级产品", "五级产品")):
        cfg = DIM_DICT.get(dkey) or {}
        if cfg.get("values"):
            vals = cfg["values"]
            dim_info[label] = {"desc": cfg.get("desc", ""), "count": len(vals),
                               "values": vals if len(vals) <= 40 else vals[:40]}
    dim_note = ("维度取值范围：" + json.dumps(dim_info, ensure_ascii=False)
                if dim_info else "未加载维度字典（dim_dictionary.json 缺失），维度参数不做校验。")
    return _envelope(
        shape="table", title="可用报表清单",
        summary=(f"共 {len(items)} 张报表；默认查询日期（前一日）为 {dinfo['report_dt']}。"
                 + ("注意：投资组合维度是 4 位英文码值（如 FBHF），没有中文名。"
                    if PORTFOLIO_CODES else "")),
        report_dt=dinfo["report_dt"], date_source="default_yesterday", notice=dinfo["notice"],
        table={"columns": ["报表名称", "说明"],
               "rows": [[i["name"], i["desc"]] for i in items]},
        notes=[json.dumps(items, ensure_ascii=False, indent=2), dim_note])


# ---------------------------------------------------------------- 工具注册
TOOLS = [
    {
        "name": "precious_metals_list_reports",
        "description": "列出贵金属业务两张报表（分产品明细表、整体情况表）及全部指标字段、维度取值与默认查询日期。不确定指标名时先调它。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "handler": lambda a: list_reports(),
    },
    {
        "name": "precious_metals_query_detail",
        "description": ("【贵金属业务（分产品明细）】按投资组合/产品分类/五级产品列示本金、损益、公允价值、交易笔数、"
                        "持有期限等 22 项指标（含/不含内部账双口径）。用于分产品、分组合的明细问题。"
                        "report_dt 不填则默认前一日，返回会给出 notice，必须转告用户实际查询日期。"),
        "inputSchema": {"type": "object", "properties": {
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；用户未明确指定时不要填。"}},
            "required": []},
        "handler": lambda a: query_report("8670", a.get("report_dt", "")),
    },
    {
        "name": "precious_metals_query_overview",
        "description": ("【贵金属业务-整体情况（全业务合计）】规模板块（期末余额、占比、当期发生额、交易笔数）与损益板块"
                        "（营业净收入、非息收入、业务损益、公允价值变动、评价损益、其他损益、损益合计）。"
                        "用于总量、占比、整体损益问题。report_dt 不填则默认前一日。"),
        "inputSchema": {"type": "object", "properties": {
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；用户未明确指定时不要填。"}},
            "required": []},
        "handler": lambda a: query_report("8666", a.get("report_dt", "")),
    },
    {
        "name": "precious_metals_query_metric",
        "description": ("按指标名查具体数值，返回「报表/板块或行/指标/数值/数值_num/单位」。支持维度过滤与口语问法："
                        "问「贵金属租出-企业的损益合计」可传 level5，也可整句传给 metric 由工具自动识别维度。"
                        "注意：投资组合是 4 位英文码值（如 FBHF），只能按码值筛；按业务含义筛请用 level5/product。"
                        "某指标在明细层不存在时会明确说明，不会用整体表合计值冒充分项值。report_dt 不填默认前一日。"),
        "inputSchema": {"type": "object", "properties": {
            "metric": {"type": "string", "description": "指标名或含维度词的整句，如「损益合计」「TGLD 的损益合计」。"},
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；用户未明确指定时不要填。"},
            "portfolio": {"type": "string", "description": "投资组合过滤，必须传 4 位码值（如 FBHF / TGLD），不能传中文。"},
            "product": {"type": "string", "description": "产品分类过滤，五选一：贵金属即远期 / 贵金属掉期 / 贵金属期货延期 / 贵金属期权 / 其他。"},
            "level5": {"type": "string", "description": "五级产品过滤（中文），如「贵金属租出-企业」「Au99.99现货实盘」「黄金期货」。"},
            "section": {"type": "string", "description": "整体情况表的板块过滤，如「贵金属业务规模情况」。"}},
            "required": []},
        "handler": lambda a: query_metric(a.get("metric", ""), a.get("report_dt", ""),
                                          a.get("portfolio", ""), a.get("product", ""),
                                          a.get("level5", ""), a.get("section", "")),
    },
    {
        "name": "precious_metals_rank",
        "description": ("按明细指标排序/排名（如「哪个产品损益最高」）。返回排名/投资组合/产品分类/五级产品/数值，"
                        "已排除合计行。top_n=0 表示全部。仅支持分产品明细表的指标。"),
        "inputSchema": {"type": "object", "properties": {
            "metric": {"type": "string", "description": "排名依据指标，如「损益合计（不含内部账）」「交易笔数」。"},
            "top_n": {"type": "integer", "description": "取前 N 名，0 表示全部。"},
            "order": {"type": "string", "description": "desc（默认，高到低）或 asc。"},
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；不填默认前一日。"},
            "portfolio": {"type": "string", "description": "限定投资组合，必须传 4 位码值（如 FBHF）。"},
            "product": {"type": "string", "description": "限定产品分类，五选一：贵金属即远期 / 贵金属掉期 / 贵金属期货延期 / 贵金属期权 / 其他。"}},
            "required": ["metric"]},
        "handler": lambda a: rank(a.get("metric", ""), int(a.get("top_n") or 0), a.get("order", "desc"),
                                  a.get("report_dt", ""), a.get("portfolio", ""), a.get("product", "")),
    },
    {
        "name": "precious_metals_compare",
        "description": ("对比两个日期的同一指标（环比/同比），返回每行两期数值、差额、变动率。"
                        "report_dt_prev 不填时默认取上一月同日。用于「和上个月比怎么样」「环比变化」。"),
        "inputSchema": {"type": "object", "properties": {
            "metric": {"type": "string", "description": "对比指标，如「损益合计（不含内部账）」。"},
            "report_dt": {"type": "string", "description": "当期日期 YYYY-MM-DD；不填默认前一日。"},
            "report_dt_prev": {"type": "string", "description": "对比期日期 YYYY-MM-DD；不填默认上一月同日。"},
            "portfolio": {"type": "string", "description": "限定投资组合，必须传 4 位码值（如 FBHF）。"},
            "product": {"type": "string", "description": "限定产品分类（中文五选一）。"},
            "level5": {"type": "string", "description": "限定五级产品（中文，如「贵金属租出-企业」）。"}},
            "required": ["metric"]},
        "handler": lambda a: compare(a.get("metric", ""), a.get("report_dt", ""), a.get("report_dt_prev", ""),
                                     a.get("portfolio", ""), a.get("product", ""), a.get("level5", "")),
    },
    {
        "name": "precious_metals_describe",
        "description": ("返回字段/口径的业务含义，如「含内部账和不含内部账有什么区别」「占比的分母是什么」。"
                        "也用于查维度取值清单：field 传「投资组合」返回 35 个码值、传「产品分类」返回 5 项枚举、"
                        "传「五级产品」返回 159 项完整品种字典。不传 field 则返回全部口径说明。"),
        "inputSchema": {"type": "object", "properties": {
            "field": {"type": "string", "description": "字段名/口径关键词/维度名，如「含内部账」「损益合计」「投资组合」「产品分类」。"}},
            "required": []},
        "handler": lambda a: describe(a.get("field", "")),
    },
    {
        "name": "precious_metals_probe",
        "description": ("页面结构体检：取不到数、或换到内网/报表改版后先调它。返回页面体积、可见文字量、"
                        "<table> 与 role=row 数量、判定的抽取方式（标准表格 / div 栅格 / 重复块 / 文本行 / 未识别）、"
                        "是否需要无头浏览器渲染、页面上发现的导出链接，以及导出接口（export_url）是否可用。"
                        "只做诊断，不返回业务数值。"),
        "inputSchema": {"type": "object", "properties": {
            "report_id": {"type": "string", "description": "报表编号：8666（整体情况表，默认）或 8670（分产品明细）。"},
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；不填默认前一日。"}},
            "required": []},
        "handler": lambda a: probe(a.get("report_id", "8666"), a.get("report_dt", "")),
    },
]

INSTRUCTIONS = (
    "precious_metals 用于查询贵金属业务的两张报表：分产品明细表与整体情况表（全业务合计）。\n"
    "工具选择：\n"
    "- 分产品/分组合明细 → precious_metals_query_detail\n"
    "- 总量/占比/整体损益 → precious_metals_query_overview\n"
    "- 某指标数值（可带维度过滤）→ precious_metals_query_metric\n"
    "- 谁最高/谁最低/排序 → precious_metals_rank\n"
    "- 与上月或另一日期比较 → precious_metals_compare\n"
    "- 字段含义、含不含内部账、占比分母 → precious_metals_describe\n"
    "- 某个维度有哪些取值（码值/枚举）→ precious_metals_describe，field 传维度名\n"
    "维度规则：\n"
    "- 投资组合：报表里是 4 位英文码值（如 FBHF / TGLD），没有中文名。只能按码值精确筛，"
    "不能按「黄金租赁」这类中文描述筛；按业务含义筛请改用五级产品（level5）或产品分类（product）。\n"
    "- 产品分类：固定 5 项 —— 贵金属即远期 / 贵金属掉期 / 贵金属期货延期 / 贵金属期权 / 其他。\n"
    "- 五级产品：中文品种名（如 Au99.99现货实盘 / 黄金期货 / 贵金属租出-企业），可按中文筛。\n"
    "硬性规则：\n"
    "1) 日期：用户未明确给出日期时不要自己编，交给工具默认取前一日；返回中的 notice 与 report_dt "
    "必须在回复里明确告知用户（例如：本次数据日期为前一日 2026-10-19）。\n"
    "2) 数值一律以工具返回为准，计算优先用「数值_num」列；单位见 unit（万元人民币）。\n"
    "3) 若工具说明某指标无明细口径，禁止用整体表合计值替代分项值，应如实告知用户。\n"
    "4) shape=empty 表示该日期确实无数据，不要推测数值；shape=text 表示参数或取数异常。\n"
    "5) 对用户只说报表名称（「贵金属业务（分产品明细）」「贵金属业务-整体情况」），"
    "不要提报表编号 8670 / 8666 这类内部标识。\n"
    "6) 取不到数据时如实说「当前 MCP 不支持该查询」或「报表中没有该组合的数据」，"
    "不要用别的口径、别的日期的数值替代，也不要自行推算。\n"
    "7) 返回里的 source=export 表示本次数据来自报表导出文件（服务端生成，比解析页面更可靠）；"
    "source=page 表示解析页面得到。若 notes 出现「⚠ 已配置导出接口但本次未成功」，说明本次回退到了页面解析，"
    "可提醒用户导出接口可能失效，或先调 precious_metals_probe 复查。"
)


# ---------------------------------------------------------------- 协议层
def _write(obj: dict) -> None:
    sys.stdout.buffer.write(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _result(msg_id, result: dict) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _tool_by_name(name: str):
    return next((t for t in TOOLS if t["name"] == name), None)


def handle_message(msg: dict) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if msg_id is None:
        return

    if method == "initialize":
        requested = (params.get("protocolVersion") or "").strip()
        version = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        })
        return
    if method == "ping":
        _result(msg_id, {})
        return
    if method == "tools/list":
        _result(msg_id, {"tools": [{"name": t["name"], "description": t["description"],
                                    "inputSchema": t["inputSchema"]} for t in TOOLS]})
        return
    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        tool = _tool_by_name(name)
        if tool is None:
            _error(msg_id, -32602, f"未知工具：{name}")
            return
        try:
            payload = tool["handler"](args)
            _result(msg_id, {"content": [{"type": "text",
                                          "text": json.dumps(payload, ensure_ascii=False)}],
                             "isError": False})
        except Exception as e:
            payload = _envelope(shape="text", title="工具执行失败", summary=f"{type(e).__name__}: {e}")
            _result(msg_id, {"content": [{"type": "text",
                                          "text": json.dumps(payload, ensure_ascii=False)}],
                             "isError": True})
        return
    if method in ("resources/list", "prompts/list"):
        _result(msg_id, {method.split("/")[0]: []})
        return
    _error(msg_id, -32601, f"不支持的方法：{method}")


def serve_stdio() -> None:
    print(f"[{SERVER_NAME}] stdio server ready, base_url={DEFAULT_BASE_URL} (来源：{BASE_URL_SOURCE})",
          file=sys.stderr, flush=True)
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(f"[{SERVER_NAME}] 非 JSON 行，已忽略：{line[:120]}", file=sys.stderr, flush=True)
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            if isinstance(m, dict):
                handle_message(m)


def save_config_value(key: str, value: str) -> int:
    """把某一项写进 config.json（换内网/换导出接口时一条命令搞定）。"""
    value = (value or "").strip()
    if value and not value.lower().startswith(("http://", "https://", "/")):
        print(f"地址必须以 http:// 或 https:// 开头，或以 / 开头的相对路径：{value}", file=sys.stderr)
        return 2
    cfg = dict(CONFIG)
    cfg[key] = value.rstrip("/") if key == "report_base_url" else value
    cfg.setdefault("timeout", HTTP_TIMEOUT)
    cfg.setdefault("_说明", "换环境只改 report_base_url 这一项；改完重启 MCP 服务生效。本文件优先级高于 mcp.json 里的 env。")
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        print(f"写入 config.json 失败：{e}", file=sys.stderr)
        return 1
    print(f"已写入 {CONFIG_PATH}\n  {key} = {cfg[key] or '(空，表示关闭)'}\n重启 MCP 服务后生效。")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="precious_metals MCP Server（贵金属业务数据查询）")
    ap.add_argument("--transport", default="stdio", choices=["stdio"])
    ap.add_argument("--base-url", default=None, help="临时覆盖报表服务地址（优先级最高，不落盘）")
    ap.add_argument("--set-base-url", default=None, metavar="URL",
                    help="把报表服务地址写入 config.json 后退出，例：--set-base-url http://10.1.2.3:9080")
    ap.add_argument("--set-export-url", default=None, metavar="URL",
                    help="把导出接口地址模板写入 config.json；传空串表示关闭导出路径")
    ap.add_argument("--show-config", action="store_true", help="打印当前生效的地址、来源与配置文件路径")
    ap.add_argument("--tools-json", action="store_true")
    args = ap.parse_args()

    global DEFAULT_BASE_URL, BASE_URL_SOURCE
    if args.set_base_url:
        sys.exit(save_config_value("report_base_url", args.set_base_url))
    if args.set_export_url is not None:
        sys.exit(save_config_value("export_url", args.set_export_url))
    if args.base_url:
        DEFAULT_BASE_URL, BASE_URL_SOURCE = pick_base_url(args.base_url)
    if args.show_config:
        print(f"生效地址：{DEFAULT_BASE_URL}")
        print(f"来源　　：{BASE_URL_SOURCE}")
        print(f"配置文件：{CONFIG_PATH}（{'存在' if os.path.exists(CONFIG_PATH) else '不存在'}）")
        print(f"渲染模式：{RENDER_MODE}（auto/off/force）　可用浏览器：{find_browser() or '未找到'}")
        print(f"导出接口：{EXPORT_URL_TEMPLATE or '未配置（只解析页面 HTML）'}")
        if EXPORT_URL_TEMPLATE:
            print(f"　　　　　实际示例：{build_export_url('8666', '2026-06-30')}")
        print(f"超时　　：{HTTP_TIMEOUT} 秒")
        return

    if args.tools_json:
        print(json.dumps({"tools": [{"name": t["name"], "description": t["description"],
                                     "inputSchema": t["inputSchema"]} for t in TOOLS]},
                         ensure_ascii=False, indent=2))
        return
    if not DEFAULT_BASE_URL:
        print(f"[{SERVER_NAME}] 未配置报表服务地址。请任选一种方式配置后重启：",
              file=sys.stderr, flush=True)
        print(f"  1) 编辑 {CONFIG_PATH} 里的 report_base_url",
              file=sys.stderr, flush=True)
        print(f"  2) 运行 python {os.path.basename(__file__)} --set-base-url http://<内网IP>:<端口>",
              file=sys.stderr, flush=True)
        print(f"  3) 启动参数追加 --base-url http://<内网IP>:<端口>",
              file=sys.stderr, flush=True)
        sys.exit(2)
    serve_stdio()


if __name__ == "__main__":
    main()
