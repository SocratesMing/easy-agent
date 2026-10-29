 # -*- coding: utf-8 -*-
"""
pm_core —— precious_metals MCP 的公共核心层（零依赖，仅标准库）

职责边界（重要）：
  * 本模块只负责「与哪张报表无关」的通用能力：配置读取、HTTP 取数、HTML/XLSX/CSV 解析、
    合并单元格展开、双层表头重建、多行表头拼列名、日期规则、结果信封。
  * 任何「某张报表有哪些维度 / 有哪些指标 / 用什么关键词命中」一律不写在这里，
    而是由每张报表自己的模块（reports/<编号>_<名称>.py）声明。
    这样新增一张报表 = 新增一个文件 + 在 reports/__init__.py 注册，不动本文件。

表头处理是本模块最关键的部分，见 build_header()。
"""
from __future__ import annotations

import calendar
import csv
import datetime
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
SERVER_VERSION = "2.0.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DIM_DICT_PATH = os.path.join(BASE_DIR, "dim_dictionary.json")


# ================================================================ 配置
def read_config() -> dict:
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

# 报表编号的 URL 参数名。内网平台固定用双下划线 __reportId（2026-10-21 用户确认），
# 不再做单/双下划线双写兼容——两套都试会让真正的 404 被另一个 404 掩盖，反而难排查。
REPORT_ID_PARAM = str(CONFIG.get("report_id_param") or "__reportId").strip() or "__reportId"


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
    return "", "未配置（config.json / 环境变量 / --base-url 均未提供地址）"


def pick_timeout() -> int:
    try:
        if CONFIG.get("timeout"):
            return int(CONFIG["timeout"])
        return int(os.environ.get("PRECIOUS_METALS_REPORT_TIMEOUT", "60"))
    except (TypeError, ValueError):
        return 60


def pick_render() -> str:
    v = str(CONFIG.get("render") or os.environ.get("PRECIOUS_METALS_RENDER") or "auto").strip().lower()
    return v if v in ("auto", "off", "force") else "auto"


def pick_export_url() -> str:
    """导出接口地址模板。留空 = 不启用导出，始终解析页面 HTML。"""
    return str(CONFIG.get("export_url") or os.environ.get("PRECIOUS_METALS_EXPORT_URL") or "").strip()


DEFAULT_BASE_URL, BASE_URL_SOURCE = pick_base_url()
HTTP_TIMEOUT = pick_timeout()
RENDER_MODE = pick_render()
EXPORT_URL_TEMPLATE = pick_export_url()

EMPTY_TOKENS = {"", "-", "--", "—", "–", "\xa0", "null", "None", "/", "N/A", "n/a"}


# ================================================================ 维度字典
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


# ================================================================ 通用小工具
def norm_text(s: str) -> str:
    """字段名归一化：去全部空白、全角括号统一，便于与配置字段精确比对。

    报表页面的表头经常带换行（「损益合计<br/>（不含内部账）」），
    不归一化就永远匹配不上配置里的字段名。
    """
    return re.sub(r"\s+", "", str(s or "")).replace("(", "（").replace(")", "）")


def norm_key(s: str) -> str:
    """更激进的归一化：去标点，用于指标名/关键词的模糊命中。"""
    out = str(s or "")
    for ch in " （）()【】[]{}<>《》-_/\\、,，.。:：;；·　%":
        out = out.replace(ch, "")
    return out.strip().lower()


def numeric(text) -> float | None:
    if text is None:
        return None
    s = str(text).replace(",", "").replace("%", "").replace("天", "").replace("笔", "").strip()
    if not s or s in EMPTY_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_ratio(text: str) -> bool:
    return any(k in (text or "") for k in ("率", "占比", "比重", "比例", "百分比"))


EMPTY_CELL = re.compile(r"^(-|--|—|–|/|N/A|n/a)?$")


def is_blank(text) -> bool:
    return (text or "").strip() in EMPTY_TOKENS


def num_like(text: str) -> bool:
    s = (text or "").replace(" ", "")
    return bool(re.fullmatch(r"-?[\d,]+(?:\.\d+)?%?", s))


# ================================================================ 结果信封
REPORT_LABEL_PREFIX = "贵金属业务分析"


def report_label(report_id: str, short_name: str) -> str:
    """报表标识：「业务域-报表名」，如 贵金属业务分析-贵金属业务、贵金属业务分析-整体表格。

    刻意不带报表编号（8670/8666）：编号是技术 ID，业务人员看不懂。编号仍保留在
    report_id 字段里，供排障与追溯，只是不进这个给人看的标识。

    单独抽成模块级函数（而不是 Report 的方法），是因为 envelope() 定义在
    Report 类之前，信封层需要能独立算出这个标识。
    """
    sn = (short_name or "").strip()
    if not sn:
        return REPORT_LABEL_PREFIX
    return f"{REPORT_LABEL_PREFIX}-{sn}"


def envelope(**kw) -> dict:
    env = {
        "shape": "table",
        "title": "",
        "summary": "",
        "report_id": "",
        "report_name": "",
        # 报表标识：「业务域-报表名」，形如 贵金属业务分析-贵金属业务、贵金属业务分析-整体表格。
        # 上层 Agent 引用数据时必须把它带出去，用户才知道这个数字出自哪张报表。
        "report_label": "",
        "report_dt": "",
        "date_source": "",
        "notice": "",
        "url": "",
        "unit": "",
        "source": "",
        "table": {"columns": [], "rows": []},
        "notes": [],
    }
    env.update(kw)
    # 兜底：调用方给了编号和名称却没给 label 时自动补，避免个别分支漏带。
    if not env.get("report_label") and env.get("report_id"):
        env["report_label"] = report_label(env["report_id"], env.get("report_name") or "")
    return env


# ================================================================ HTML 表格解析
class _TableParser(HTMLParser):
    """把页面里每个 <table> 解析成 [[(文本, colspan, rowspan), ...], ...]。

    同时记录每个表的表头区域里出现的 colspan/rowspan，供双层表头重建使用。
    """

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


def cells_of(row) -> list[str]:
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
    carry: dict[int, tuple[str, int]] = {}
    for row in rows:
        queue = list(row)
        cells: list[tuple[str, int]] = []
        col = 0
        limit = len(cells_of(row)) + (max(carry) + 1 if carry else 0) + 8
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


# ---------------------------------------------------------------- 结构降级识别
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
    cells = [c for c in node.children if _is_cell(c)]
    if cells:
        return cells
    kids = [c for c in node.children if c.tag not in ("script", "style")]
    return kids if len(kids) >= 2 else []


def _grid_tables(root: _Node) -> list[list[list[tuple[str, int]]]]:
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
        tokens = [x for x in re.split(r"\s{2,}", line) if x]
        if len(tokens) < 2:
            tokens = line.split()
        if len(tokens) >= 2:
            rows.append([(t, 1) for t in tokens])
    return rows


def _usable(tables: list[list[list[tuple[str, int]]]]) -> bool:
    for t in tables:
        if len(t) < 2:
            continue
        widths = [len(cells_of(r)) for r in t]
        if max(widths, default=0) >= 3 and sum(1 for w in widths if w >= 3) >= 2:
            return True
    return False


def extract_tables(html: str) -> dict:
    """按 表格 -> 栅格 -> 重复块 -> 文本行 逐级降级，返回 {tables, strategy, diag}。

    tables 里每张表有两种形态：
      - 默认是**已展开 rowspan** 的二元组行（[[文本, colspan], ...]），供按行取数用；
      - 同时保留 raw_tables（**原始三元组** [[文本, colspan, rowspan], ...]）。
    表头重建必须用原始三元组——展开后组表头的 colspan 会把第二层叶子表头挤错列，
    且 rowspan 信息丢失后无法判断「同一单元格跨层」，去重逻辑就无从下手。
    """
    parser = _TableParser()
    parser.feed(html)
    raw_tables = [t for t in parser.tables]
    tables = [expand_rowspan(t) for t in raw_tables]
    diag = {"table_count": len(parser.tables),
            "table_rows": sum(len(t) for t in parser.tables)}
    if _usable(tables):
        return {"tables": tables, "raw_tables": raw_tables,
                "strategy": "table", "diag": diag}

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


# ================================================================ 表头重建（核心）
# 内网报表（8670 / 8666）都是双层表头：
#   第一层 = 合并单元格的组表头（如 <th colspan="2">业务损益折人民币</th>），
#            还可能同时有 rowspan 拉两行的单元格（如「投资组合」跨上下两层）；
#   第二层 = 叶子表头（含内部账 / 不含内部账）。
# 直接取「最宽那行的单元格文本」当列名，会得到一串空串和重复的组名，
# 无法与配置字段对上——这正是之前 8670 取不到数的根因。
# 下面 build_header() 按列位置重建：每一列自上而下收集非空标签，
# 重复的相邻标签只保留一次，最后用连接符拼成完整列名。
def _header_score(cells: list[str], fieldset: set[str]) -> int:
    return sum(1 for c in cells if c and norm_text(c) in fieldset)


def _leaf_score(cells: list[str], fieldset: set[str], fields: list[str] | None = None) -> float:
    """叶子表头行的打分：命中率为主，配合「短名奖励」挑出真正的叶子层。

    单纯数命中个数会选错层：第一层组表头「业务损益折人民币」本身就等于某个配置字段的前缀，
    与第二层「含内部账 / 不含内部账」的命中个数往往打平。真正的区别是：
      叶子层的表头名**短**（含内部账、期末余额、非息收入），
      组层的名字**长且自成完整词组**（业务损益折人民币、公允价值变动折人民币）。
    所以 = 命中率 × 0.7 + 短名比例 × 0.3，短名阈值按平均字段长度动态定。
    """
    filled = [c for c in cells if c and not is_blank(c)]
    if not filled:
        return 0.0
    hit_vals = [norm_text(c) for c in filled if norm_text(c) in fieldset]
    if len(hit_vals) < 2:
        return 0.0
    ratio = len(hit_vals) / len(filled)
    lens = [len(norm_text(f)) for f in (fields or [])] or [8]
    avg_len = sum(lens) / len(lens)
    short = sum(1 for v in hit_vals if len(v) <= avg_len * 0.75) / len(hit_vals)
    return ratio * 0.7 + short * 0.3


def raw_grid(html: str) -> list[list[list[tuple[str, int, int]]]]:
    """解析页面里每个 <table> 的**原始行列**（未做 colspan 展开）。

    为什么表头不能复用 expand_rowspan 的产物：展开后组表头 <th colspan="2">
    会变成「业务损益折人民币」+ 一个空串，第二层「含内部账 / 不含内部账」
    就会被挤到错误的列号上，导致列名错位。合并单元格感知的表头重建必须
    同时知道 colspan 与 rowspan，所以这里保留三元组原样。
    """
    parser = _TableParser()
    parser.feed(html)
    return parser.tables


def grid_to_bands(raw_rows: list[list[tuple[str, int, int]]],
                  max_band: int = 4) -> list[list[str]]:
    """把原始行列（含合并信息）铺成等宽网格，只为表头区域服务。

    - colspan 覆盖到的列填同一文本（表示「这一列归属该组」）；
    - rowspan 向下覆盖，保证下层叶子表头能对上正确列号；
    - 被覆盖的格子沿用锚点文本，使「业务损益折人民币」在它覆盖的两列上
      都出现，后续逐列拼接时才能得到「组名+叶子名」。
    """
    grid: list[list[str]] = []
    carry: dict[int, tuple[str, int]] = {}
    for row in raw_rows[:max_band]:
        cells: list[str] = []
        col = 0
        pending = list(row)
        limit = 512
        while (pending or col in carry) and col < limit:
            if col in carry:
                text, remain = carry[col]
                cells.append(text)
                if remain <= 1:
                    del carry[col]
                else:
                    carry[col] = (text, remain - 1)
                col += 1
                continue
            if not pending:
                cells.append("")
                col += 1
                continue
            text, cspan, rspan = pending.pop(0)
            # 组表头（colspan>1 且内容非空）向下填充时只填首列，
            # 后续列留空——否则第二层的「含内部账 / 不含内部账」拼出来会丢掉组名。
            # 跨层的普通单元格（colspan=1）才整列向下填充。
            group = cspan > 1 and bool(text.strip())
            for k in range(cspan):
                cells.append(text)
                if rspan > 1:
                    carry[col + k] = ("" if (group and k > 0) else text, rspan - 1)
            col += cspan
        grid.append(cells)
    return grid


def build_header(rows, fields: list[str] | None = None,
                 max_band: int = 3) -> dict:
    """从表头区域重建列名，自动兼容单层 / 双层 / 三层表头。

    参数：
      rows     已 expand_rowspan 过的行（每行 [[文本, colspan], ...]）
      fields   该报表声明的指标字段名（用于打分，可为空）
      max_band 最多把前几行当作表头区域（默认 3）

    返回 dict：
      leaf_index  叶子表头所在的行下标
      columns     拼好的列名（长度 = 表宽）
      bands       实际用到的表头行数
      width       表宽
      prefix_cols 左侧维度列数（组表头为空的列，通常就是维度列）

    判定逻辑：
      1. 在 fields 命中数（叶子表头命中最多）最高的一行作为叶子行；
         fields 为空时退化为「第一个多列且几乎无数字的行」。
      2. 从叶子行往上，只要上一行是「表头样」（多列、几乎无数字、
         且存在跨列合并或与叶子行有空位互补），就并入表头区，最多 max_band 行。
      3. 逐列自上而下拼标签：同一列上下重复的标签只留一次，
         上级为空的列（如维度列）直接取叶子名。
    """
    fieldset = {norm_text(f) for f in (fields or []) if f}
    # 支持两种入参：原始三元组（含 colspan/rowspan）或已展开的二元组。
    # 表头必须用原始三元组的网格，否则组表头的 colspan 会把叶子表头挤错列。
    is_raw = bool(rows) and all(len(c) >= 3 for c in rows[0])
    raw_rows = rows
    grid = grid_to_bands(raw_rows, max_band + 1) if is_raw else [cells_of(r) for r in rows[:max_band + 1]]
    width = max((len(g) for g in grid), default=0)
    if not grid or width == 0:
        return {"leaf_index": 0, "columns": [], "bands": 0, "width": 0, "prefix_cols": 0,
                "raw_rows": raw_rows if is_raw else []}

    def dense_cells(cs: list[str]) -> int:
        return sum(1 for c in cs if c and not is_blank(c))

    def numeric_ratio(cs: list[str]) -> float:
        vals = [c for c in cs if c and not is_blank(c)]
        if not vals:
            return 1.0
        return sum(1 for v in vals if num_like(v)) / len(vals)

    n_grid = len(grid)

    # ---- 1) 叶子表头行：逐候选行拼一次列名，谁对齐的字段多谁是叶子层。
    # 不能只看单行的命中数：组表头行（业务损益折人民币）和叶子行（含内部账）单独看
    # 都只命中 4 个字段，打平；只有「组名+叶子名」拼起来才能对齐到完整字段名
    # （业务损益折人民币（含内部账）），所以用拼接结果打分才分得开。
    def compose(start_i: int, leaf_i: int) -> list[str]:
        bands_i = grid[start_i: leaf_i + 1]
        out: list[str] = []
        for c in range(width):
            parts: list[str] = []
            prev_bi = -1
            for bi in range(start_i, leaf_i + 1):
                band = grid[bi]
                val = (band[c] if c < len(band) else "").strip()
                if not val or is_blank(val):
                    continue
                # 去重规则与 compose_col() 完全一致：只处理 rowspan 跨层与
                # 「上一层同一列单层单元格同名」两种情形；colspan 横向铺开的组名与
                # 不同组后面的同名叶子表头都必须保留
                if prev_bi >= 0 and parts and parts[-1] == val:
                    cc = 0
                    rs = 1
                    for cell in raw_rows[prev_bi]:
                        cs = cell[1] if len(cell) > 1 else 1
                        rs = cell[2] if len(cell) > 2 else 1
                        if cc <= c < cc + cs:
                            break
                        cc += cs
                    if rs > 1 and bi <= prev_bi + rs - 1:
                        continue                      # 同一单元格 rowspan 跨层
                    if bi == prev_bi + 1 and rs <= 1:
                        continue                      # 上一层单层单元格同名
                parts.append(val)
                prev_bi = bi
            out.append("".join(parts))
        while out and not out[-1]:
            out.pop()
        return out

    def aligned_hits(cols: list[str]) -> int:
        if not fieldset:
            return sum(1 for c in cols if c)
        return sum(1 for i, f in align_to_fields(cols, fields or [])["map"].items())

    leaf = -1
    if fieldset:
        best, best_leaf = -1.0, -1
        for i in range(n_grid):
            for s in range(max(0, i - max_band + 1), i + 1):
                # 数据行不能进表头：只要该行出现明显数值，就只允许它自己当叶子层
                if numeric_ratio(grid[i]) > 0.3:
                    continue
                cols = compose(s, i)
                hit = aligned_hits(cols)
                if hit < 2:
                    continue
                # 同命中数时，表头层数多、叶子行靠后的更可能是真表头（跳过标题行）
                score = hit * 10 + (i - s + 1) * 0.5 + i * 0.01
                if score > best:
                    best, best_leaf = score, i
        leaf = best_leaf
        if leaf < 0:
            for i in range(n_grid):
                if dense_cells(grid[i]) >= 2 and numeric_ratio(grid[i]) <= 0.3:
                    leaf = i
                    break
    if leaf < 0:
        leaf = 0

    # ---- 2) 向上扩展表头区：上一行是表头样、且与叶子行不是同一批重复文本
    def title_row(bi: int) -> bool:
        """是否是「整行一个大合并单元格」的标题行。

        典型形态：<th colspan=全表宽>贵金属业务（分产品明细）</th>，
        作用是显示报表名称，不属于列名体系。若并入表头区，会让每一列都多挂一段
        相同的前缀（「贵金属业务（分产品明细）投资组合」），列名被污染。
        判据用原始三元组：该行只有一个单元格且其 colspan 覆盖整表宽。
        """
        if not (is_raw and 0 <= bi < len(raw_rows)):
            return False
        cells = raw_rows[bi]
        if len(cells) != 1:
            return False
        cs = cells[0][1] if len(cells[0]) > 1 else 1
        return cs >= width

    start = leaf
    while start > 0 and (leaf - start + 1) < max_band:
        up = grid[start - 1]
        same_as_leaf = _row_similar(up, grid[leaf])
        if title_row(start - 1):
            break
        if dense_cells(up) >= 1 and numeric_ratio(up) <= 0.35 and not same_as_leaf:
            start -= 1
        else:
            break

    band_rows = grid[start: leaf + 1]
    bands = len(band_rows)

    # ---- 3) 逐列自上而下拼标签（要拼两层以上时）
    # 什么时候需要"去重"？只有「同一个单元格纵向跨了两层」这一种情况：
    #   <th rowspan=2>投资组合</th> —— 该格同时覆盖两个表头层，
    #   若不去重会拼成"投资组合投资组合"。
    # 而 colspan 横向铺开的组名（业务损益折人民币 占 2 列）在网格里是**两列各自一份**，
    # 不属于"跨层"，绝不能去重；同理不同组后面同名的叶子表头（含内部账/不含内部账）
    # 也各是独立单元格，去了就丢信息。
    # 判据：upper 层在**同一列**的那个单元格，其 rowspan 是否盖到了 cur 层。
    def spanned_from(upper_bi: int, cur_bi: int, col: int) -> bool:
        if not is_raw or upper_bi < 0 or cur_bi >= len(raw_rows):
            return False
        c = 0
        for cell in raw_rows[upper_bi]:
            cs = cell[1] if len(cell) > 1 else 1
            rs = cell[2] if len(cell) > 2 else 1
            if c <= col < c + cs:
                return rs > 1 and cur_bi <= upper_bi + rs - 1
            c += cs
        return False

    def spans_multi(upper_bi: int, col: int) -> bool:
        """upper_bi 层同一列的那个单元格，是否纵向跨了 2 层以上。"""
        if not is_raw or upper_bi < 0 or upper_bi >= len(raw_rows):
            return False
        c = 0
        for cell in raw_rows[upper_bi]:
            cs = cell[1] if len(cell) > 1 else 1
            rs = cell[2] if len(cell) > 2 else 1
            if c <= col < c + cs:
                return rs > 1
            c += cs
        return False

    def compose_col(col: int, lo: int, hi: int) -> str:
        """拼第 col 列在 [lo, hi] 这几层的标签。

        去重只有两种正当情形（其余一律保留，否则会丢组名 / 丢叶子区分）：
          A. 上一个非空层是**同一个单元格**（rowspan 纵向跨层）→ spanned_from()；
          B. 上一个非空层是**上一层同一列的单层单元格**且文本相同
             （如「SELL交易本金折人民币」单层表头，页面又在第二层补了个同名单元）。
        注意 B 必须限定在相邻的上一层，否则会把不同组后面同名的
        「含内部账 / 不含内部账」误去重。
        """
        parts: list[str] = []
        prev_bi = -1
        for bi in range(lo, hi + 1):
            band = grid[bi]
            val = (band[col] if col < len(band) else "").strip()
            if not val or is_blank(val):
                continue
            if prev_bi >= 0 and parts and parts[-1] == val:
                if spanned_from(prev_bi, bi, col):
                    continue                     # 情形 A
                if bi == prev_bi + 1 and not spans_multi(prev_bi, col):
                    continue                     # 情形 B
            parts.append(val)
            prev_bi = bi
        return "".join(parts)

    columns = [compose_col(col, start, leaf) for col in range(width)]

    while columns and not columns[-1]:
        columns.pop()

    # ---- 3b) 每列的 (组名, 叶子名)：对齐时优先用结构信息，而不是对拼好的
    # 整串列名做「包含」猜测。拼出来的 '不含内部账' 这种短叶子没有上下文，
    # 单看字符串无法判断归属哪个组，只能靠这里带出组名。
    def split_col(col: int) -> tuple[str, str]:
        vals: list[tuple[int, str]] = []
        prev_bi = -1
        for bi in range(start, leaf + 1):
            band = grid[bi]
            val = (band[col] if col < len(band) else "").strip()
            if not val or is_blank(val):
                continue
            if prev_bi >= 0 and vals and vals[-1][1] == val:
                if spanned_from(prev_bi, bi, col):
                    continue
                if bi == prev_bi + 1 and not spans_multi(prev_bi, col):
                    continue
            vals.append((bi, val))
            prev_bi = bi
        if not vals:
            return "", ""
        if len(vals) == 1:
            return "", vals[0][1]
        return vals[0][1], vals[-1][1]

    col_pairs = [split_col(col) for col in range(len(columns))]

    # ---- 4) 校正：若只有一层但列名命中明显不足，把上一行并入再拼一次
    if fieldset and len(columns) > 0 and start + leaf > 0:
        hit1 = sum(1 for c in columns if norm_text(c) in fieldset)
        if hit1 < min(3, len(fieldset) // 4):
            alt_start = max(0, start - 1)
            alt_bands = grid[alt_start: leaf + 1]
            alt_cols = [compose_col(col, alt_start, leaf) for col in range(width)]
            hit2 = sum(1 for c in alt_cols if norm_text(c) in fieldset)
            if hit2 > hit1:
                columns, bands, start = alt_cols, len(alt_bands), alt_start
                while columns and not columns[-1]:
                    columns.pop()

    prefix_cols = 0
    if bands > 1:
        band_rows_final = grid[start: leaf + 1]
        for col in range(len(columns)):
            leaf_val = (band_rows_final[-1][col]
                        if col < len(band_rows_final[-1]) else "").strip()
            if not leaf_val:
                break
            up_empty = all(not (b[col] if col < len(b) else "").strip()
                           for b in band_rows_final[:-1])
            if up_empty:
                prefix_cols += 1
            else:
                break

    # columns 可能被裁剪过，pairs 一律按最终 start/leaf 重算，保证一一对应
    col_pairs = [split_col(col) for col in range(len(columns))]

    return {"leaf_index": leaf, "columns": columns, "bands": bands,
            "width": len(columns), "prefix_cols": prefix_cols,
            "pairs": col_pairs,
            "raw_rows": raw_rows if is_raw else []}


def _row_similar(a: list[str], b: list[str]) -> bool:
    """两行文本是否几乎相同（防止把重复的表头行算成两层）。"""
    fa = [c for c in a if c and not is_blank(c)]
    fb = [c for c in b if c and not is_blank(c)]
    if not fa or not fb:
        return False
    common = sum(1 for c in fa if c in fb)
    return common >= max(len(fa), len(fb)) * 0.8


def _norm_for_align(s: str) -> str:
    """对齐专用归一化：去掉全部括号、空白与层间连接符。

    双层表头拼出来的是「业务损益折人民币含内部账」（页面里括号在合并单元格内，
    拼接时已被吃掉），而配置字段名是「业务损益折人民币（含内部账）」。
    去括号后两者才等价。

    连字符同理：配置里用「新发生-名义本金折人民币」表达「新发生」组下的叶子，
    页面拼出来是「新发生名义本金折人民币」，去掉 '-' 两者才等价。
    """
    out = norm_text(s)
    for ch in "（）()[]【】「」『』":
        out = out.replace(ch, "")
    for ch in "-—_·":
        out = out.replace(ch, "")
    return out


def align_to_fields(columns: list[str], fields: list[str],
                    pairs: list[tuple[str, str]] | None = None) -> dict:
    """把重建出的列名对齐到报表声明的字段名，返回 {index: field} 与命中统计。

    报表导出文件（XLSX/CSV）的列名可能带单位后缀或换行，页面 DOM 也可能多出
    「序号」「操作」之类的列。这里按归一化后的名字做精确匹配，
    再对没对上的列做一次「包含」匹配，避免因为一个多余空格整表错位。

    传了 pairs（每列的 (组名, 叶子名)）时，先用结构信息对齐：
    组名+叶子名归一化后与字段名相等的直接拿下。这一步很重要——
    「不含内部账」这种叶子名脱离组名毫无区分度，靠字符串包含只能瞎猜，
    实测会把 5 列的数值挂到别的组上去。
    """
    fmap: dict[str, str] = {_norm_for_align(f): f for f in fields}
    out: dict[int, str] = {}
    dup: list[str] = []
    # ---- 第一轮：结构匹配（组名 + 叶子名 组合后与字段名相等）
    # 必须先收集再裁决：同一个字段可能被多列同时命中（例如「新发生」+「SELL交易本金」
    # 与单层的「SELL交易本金」都会给出同一个候选），按列号先后决定归属会随表头
    # 顺序漂移。裁决依据是「候选 key 越长越具体」——带了组名的那个才是真归属。
    if pairs:
        claims: list[tuple[int, int, str]] = []   # (key 长度, 列号, 字段名)
        for i, pr in enumerate(pairs):
            if i >= len(columns):
                break
            g, lf = pr if pr else ("", "")
            if not lf:
                continue
            gl, lln = _norm_for_align(g), _norm_for_align(lf)
            cands: list[str] = []
            if gl:
                cands += [gl + lln,                      # 组+叶子 直接相连
                          gl + "-" + lln,                # 组-叶子
                          _norm_for_align(g + "（" + lf + "）")]  # 组（叶子）
                # 组名与叶子名口径不一致的情况不在这里猜（例如组「新发生笔数」
                # + 叶子「交易笔数」而字段写作「新发生-交易笔数」），由报表的
                # column_overrides 显式声明——启发式切分实测会把
                # 「新发生-SELL/BUY 交易本金」「新发生-平均持有期限」全部抢走。
            else:
                # 只有叶子名（单层表头）：直接拿叶子名匹配。
                # 有组名时绝不能只拿叶子名去撞，'不含内部账' 脱离组名毫无区分度。
                cands.append(lln)
            for key in cands:
                if key in fmap:
                    claims.append((len(key), i, fmap[key]))
                    break
        for _, i, fn in sorted(claims, reverse=True):
            if i in out or fn in out.values():
                continue
            out[i] = fn
    # ---- 第二轮：整串列名精确匹配
    for i, c in enumerate(columns):
        if i in out:
            continue
        key = _norm_for_align(c)
        if key in fmap:
            if fmap[key] in out.values():
                dup.append(c)      # 字段已被更具体的列占用，此列只记重复，不抢占
                continue
            out[i] = fmap[key]
    matched = set(out.values())
    # 包含匹配兜底（只看还没对上的字段与列）。
    # 注意：这里必须保持「字段声明顺序」，且命中后立即占用该字段。
    # 曾试过"按字段名长度降序挑"，结果 '不含内部账' 这种短叶子会被
    # 随机分配给第一个还没被占用的长字段，反而把 5 列的数值挂错组——
    # 兜底只能是最后手段，不能改变归属关系。
    for i, c in enumerate(columns):
        if i in out:
            continue
        key = _norm_for_align(c)
        if len(key) < 2:
            continue
        for fk, f in fmap.items():
            if f in matched:
                continue
            if key == fk or (len(fk) >= 4 and (fk in key or key in fk)):
                out[i] = f
                matched.add(f)
                break
    return {"map": out, "hit": len(out), "dup": dup}


def split_header_pairs(columns: list[str]) -> list[tuple[str, str]]:
    """把拼好的列名拆回 (上级, 叶子)，供前端/模型展示双层结构。"""
    out = []
    for c in columns:
        out.append(("", c))
    return out


# ================================================================ HTTP 取数
def build_url(report_id: str, report_dt: str, base_url: str | None = None) -> str:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/ereport/output?{REPORT_ID_PARAM}={report_id}&report_dt={report_dt}"


def resolve_date(report_dt: str | None, today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    raw = (report_dt or "").strip()
    if not raw:
        d = today - datetime.timedelta(days=1)
        return {
            "report_dt": d.isoformat(),
            "date_source": "default_yesterday",
            "notice": (f"用户未指定查询日期，已按规则默认使用前一日（{d.isoformat()}）。"
                       f"请在回复中明确告知用户本次数据日期为 {d.isoformat()}（前一日），避免误读。"),
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
            "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) precious-metals-mcp/{SERVER_VERSION}",
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


# ---------------------------------------------------------------- 导出接口
XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_PERCENT_IDS = {9, 10}


def build_export_url(report_id: str, report_dt: str, base_url: str | None = None,
                     template: str | None = None) -> str:
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
        url = f"{url}{sep}{REPORT_ID_PARAM}={report_id}&report_dt={report_dt}"
    return url


def fetch_binary(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) precious-metals-mcp/{SERVER_VERSION}",
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


def guess_export_kind(raw: bytes, ctype: str, cdisp: str) -> str:
    head = raw[:2048].lstrip().lower()
    if raw[:2] == b"PK" or "spreadsheetml" in ctype or ".xlsx" in cdisp.lower() or "officedocument" in ctype:
        return "xlsx"
    if head.startswith(b"<") or b"<html" in head or b"<!doctype" in head:
        return "html"
    if "csv" in ctype or "text/plain" in ctype or ".csv" in cdisp.lower():
        return "csv"
    try:
        probe = raw[:4096].decode("utf-8")
    except UnicodeDecodeError:
        probe = raw[:4096].decode("gb18030", errors="replace")
    return "html" if "<table" in probe.lower() else "csv"


def decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def norm_cell(text: str) -> str:
    return " ".join(html_unescape(text or "").replace("\xa0", " ").split())


def split_csv(text: str) -> list[list[str]]:
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


def join_thousands(cells: list[str]) -> list[str]:
    """把被分隔符拆开的千分位数字拼回去：['226','518.61'] -> ['226,518.61']。"""
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


def has_split_number(row: list[str]) -> bool:
    for a, b in zip(row, row[1:]):
        if THOUSAND_LEFT.fullmatch(a.strip() or "x") and THOUSAND_RIGHT.fullmatch(b.strip() or "x"):
            return True
    return False


def parse_csv_table(raw: bytes) -> list[list[tuple[str, int]]]:
    raw_rows = [[norm_cell(c) for c in r] for r in split_csv(decode_text(raw))]
    raw_rows = [r for r in raw_rows if any(x for x in r)]
    if not raw_rows:
        return []

    expected = 0
    for r in raw_rows:
        if sum(1 for c in r if c) >= 3 and not has_split_number(r):
            expected = len(r)
            break
    if not expected:
        widths = [len(r) for r in raw_rows]
        expected = max(set(widths), key=widths.count)

    for i, r in enumerate(raw_rows):
        guard = 0
        while len(r) > expected and guard < 6:
            joined = join_thousands(r)
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
    m = re.match(r"([A-Za-z]+)(\d+)$", (ref or "").strip())
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


def _fmt_number(raw_text: str, decimals: int | None = None) -> str:
    try:
        v = float(raw_text)
    except (TypeError, ValueError):
        return raw_text
    if decimals is None:
        decimals = len(raw_text.split(".")[1]) if "." in raw_text else 0
    return f"{v:,.{decimals}f}" if abs(v) >= 1000 else f"{v:.{decimals}f}"


def _xlsx_cell_styles(zf: zipfile.ZipFile) -> list[tuple[bool, int | None]]:
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
            grid[(r, col)] = norm_cell(text)
            max_r, max_c = max(max_r, r), max(max_c, col)

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


# ================================================================ 报表实例
class Report:
    """一张报表的全部元数据与解析方式。

    每张报表在自己的模块里实例化一次并导出 REPORT（或 REPORTS 列表），
    路由层只认这些声明，不再需要「先读一张再读另一张」地试探。
    """

    def __init__(self, report_id: str, name: str, title: str, layout: str, unit: str,
                 desc: str, dims: list[dict], fields: list[str],
                 field_docs: dict | None = None, aliases: dict | None = None,
                 keywords: dict | None = None, scope_words: list[str] | None = None,
                 row_filters: list[str] | None = None, notes: list[str] | None = None,
                 column_overrides: dict | None = None, short_name: str = ""):
        self.report_id = report_id
        self.name = name
        self.title = title
        # 简短中文名，用于拼「业务域-报表名」的报表标识。缺省时退回 name。
        self.short_name = short_name or name
        self.layout = layout          # "matrix"（明细平铺）/ "sections"（分区堆叠）
        self.unit = unit
        self.desc = desc
        self.dims = dims              # [{"name":..,"desc":..,"values":[...]}, ...]
        self.fields = fields
        self.field_docs = field_docs or {}
        self.aliases = aliases or {}   # 口语 -> [标准指标名]
        self.keywords = keywords or {}  # 指标/概念 -> 命中关键词（给路由打分用）
        self.scope_words = scope_words or []
        self.row_filters = row_filters or []
        self.notes = notes or []
        # {(组名, 叶子名): 标准字段名} —— 页面表头文字与字段名口径不一致时用它兜底。
        # 只在这两者的组合无法按常规规则对齐时才需要写，属于显式声明，不做猜测。
        self.column_overrides = column_overrides or {}

    @property
    def label(self) -> str:
        """报表标识：「业务域-报表名」，如 贵金属业务分析-贵金属业务、贵金属业务分析-整体表格。

        所有返回给上层的信封都会带这个值，让 Agent 在回答里能明确说清
        「这个数字出自哪张报表」，而不是只报一个裸指标名。
        """
        return report_label(self.report_id, self.short_name)

    # -------------------------------------------------- 维度
    def dim(self, name: str) -> dict | None:
        for d in self.dims:
            if d["name"] == name:
                return d
        return None

    def dim_values(self, name: str) -> list[str]:
        d = self.dim(name)
        return list((d or {}).get("values") or [])

    def dim_key(self, d: dict) -> str:
        """该维度在工具入参里的参数名。

        维度名可能在不同报表间重名（如都叫「统计维度」），直接用会撞参数名，
        因此统一加报表前缀：8670 的 portfolio -> `p8670_portfolio`。
        上层也接受不带前缀的写法（由入口层做后缀匹配兜底）。
        """
        base = d.get("key") or d["name"]
        return f"p{self.report_id}_{base}"

    def dim_by_arg(self, arg: str) -> dict | None:
        """按入参名反查维度，支持带前缀与不带前缀两种写法。"""
        a = str(arg or "").strip()
        for d in self.dims:
            if a == self.dim_key(d) or a == (d.get("key") or d["name"]) or a == d["name"]:
                return d
        return None

    # -------------------------------------------------- 取数
    def fetch(self, report_dt: str = "", base_url: str | None = None) -> dict:
        return fetch_report(self, report_dt, base_url)

    def __repr__(self) -> str:
        return f"<Report {self.report_id} {self.name}>"


# ================================================================ 取报表（导出优先，回退页面）
def _drop_leading_titles(rows: list[list[tuple[str, int]]]) -> list[list[tuple[str, int]]]:
    """丢掉表头之前的标题行（标题行通常只有 1~2 个非空格）。"""
    for i, r in enumerate(rows):
        if sum(1 for c in cells_of(r) if c and not is_blank(c)) >= 3:
            return rows[i:]
    return rows


def fetch_export(report: Report, report_dt: str, base_url: str | None = None) -> dict:
    """走导出接口取数。返回 {ok, tables, kind, detail, url, error}。"""
    url = build_export_url(report.report_id, report_dt, base_url)
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

    kind = guess_export_kind(raw, ctype, cdisp)
    if kind == "html":
        return {"ok": False, "url": url,
                "error": "导出接口返回的是 HTML 页面（常见原因：未登录/无权限、报表编号不对、接口已改）"}
    try:
        if kind == "xlsx":
            tables, label = parse_xlsx_tables(raw), "XLSX"
        else:
            tables, label = [parse_csv_table(raw)], "CSV"
    except Exception as e:
        return {"ok": False, "url": url, "error": f"{kind.upper()} 解析失败：{e}"}

    tables = [t for t in tables if t and (len(t) >= 2 or sum(len(cells_of(r)) for r in t) >= 3)]
    if not tables or not _usable(tables):
        return {"ok": False, "url": url, "error": f"{label} 里没有识别出可用的表格结构"}
    if report.layout == "matrix":
        tables = [_drop_leading_titles(t) for t in tables]
    return {"ok": True, "url": url, "kind": label, "tables": tables,
            "detail": f"{label}，{len(raw)} 字节，{len(tables)} 个表，"
                      f"共 {sum(len(t) for t in tables)} 行"}


def fetch_report(report: Report, report_dt: str = "", base_url: str | None = None) -> dict:
    """取一张报表并解析，返回统一信封。

    顺序：导出接口 -> 页面 HTML -> （可选）无头浏览器渲染。
    所有分支都明确区分「无数据」与「解析失败」，不把结构问题说成没数据。
    """
    dinfo = resolve_date(report_dt)
    if "error" in dinfo:
        return envelope(shape="text", title="日期参数错误", summary=dinfo["error"],
                        report_id=report.report_id, report_name=report.name,
                        report_label=report.label)
    dt = dinfo["report_dt"]
    url = build_url(report.report_id, dt, base_url)

    # 首条 note 固定放报表标识，让上层不必翻 report_id 就能直接引用
    notes = [f"报表：{report.label}（报表编号 {report.report_id} 仅供排障追溯，对外引用用左侧报表标识）",
             f"金额单位：{report.unit}"]
    tables: list = []
    raw_tables: list = []
    strategy, source = "", "page"

    exp = fetch_export(report, dt, base_url)
    if exp.get("ok"):
        tables, source = exp["tables"], "export"
        raw_tables = exp.get("raw_tables") or []
        notes.append(f"数据来源：报表导出文件（{exp['detail']}），本次未解析页面 HTML。")
    elif not exp.get("skipped"):
        notes.append(f"⚠ 已配置导出接口但本次未成功：{exp.get('error')}；已回退到解析页面 HTML。")

    render_note = ""
    if not tables:
        try:
            html = fetch_html(url)
        except urllib.error.HTTPError as e:
            return envelope(shape="text", title=report.title, report_id=report.report_id, report_name=report.name,
                            report_label=report.label, report_dt=dt,
                            date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                            summary=f"报表页面返回 HTTP {e.code}（{url}）。"
                                    f"请确认报表编号与日期，或报表服务是否正常。",
                            notes=notes)
        except Exception as e:
            return envelope(shape="text", title=report.title, report_id=report.report_id, report_name=report.name,
                            report_label=report.label, report_dt=dt,
                            date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                            summary=f"无法打开报表页面：{e}。请确认报表服务地址（当前 {DEFAULT_BASE_URL}）"
                                    f"可访问、服务已启动；若页面由前端 JS 渲染，纯 HTTP 抓取拿不到数据。",
                            notes=notes)

        ext = extract_tables(html)
        strategy = ext["strategy"]
        if strategy == "none" and RENDER_MODE != "off":
            rendered, msg = render_with_browser(url)
            if rendered:
                ext2 = extract_tables(rendered)
                if ext2["strategy"] != "none":
                    ext, strategy = ext2, ext2["strategy"]
                    render_note = f"{msg}后解析成功（策略：{STRATEGY_NAMES[strategy]}）。"
        tables = ext["tables"]
        raw_tables = ext.get("raw_tables") or []
        if not tables:
            return envelope(shape="text", title=report.title, report_id=report.report_id, report_name=report.name,
                            report_label=report.label, report_dt=dt,
                            date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                            summary="页面已打开，但未从页面里识别出任何行列结构。"
                                    "常见原因：页面由前端 JS 异步加载（可把 config.json 的 render 设为 force）、"
                                    "需要登录态、或报表改版换了结构。",
                            notes=notes + ([render_note] if render_note else []))

    if source == "page" and strategy and strategy != "table":
        notes.append(f"⚠ 页面不是标准 <table> 结构，已按「{STRATEGY_NAMES.get(strategy, strategy)}」抽取，"
                     f"列对齐可能不如标准表格稳定，引用前请抽查 1~2 个数值。")
    if render_note:
        notes.append(render_note)
    if dinfo["notice"]:
        notes.append(dinfo["notice"])

    if report.layout == "matrix":
        parsed, raw_rows = parse_matrix_report(report, tables, raw_tables)
    else:
        parsed, raw_rows = parse_sections_report(report, tables)

    if not parsed["rows"]:
        if raw_rows > 3:
            return envelope(shape="text", title=report.title, report_id=report.report_id, report_name=report.name,
                            report_label=report.label, report_dt=dt,
                            date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                            unit=report.unit,
                            summary=(f"「{report.label}」页面可打开且有 {raw_rows} 行内容，"
                                     f"但未能按已知字段识别出表头，本次取数失败（不是无数据）。"
                                     f"请确认报表版式是否改版。"),
                            notes=notes)
        return envelope(shape="empty", title=report.title, report_id=report.report_id, report_name=report.name,
                            report_label=report.label, report_dt=dt,
                        date_source=dinfo["date_source"], notice=dinfo["notice"], url=url,
                        unit=report.unit,
                        summary=f"「{report.label}」在 {dt} 无数据：页面可正常打开且表头完整，"
                                f"但没有任何数据行。这不是报错，请告知用户该日期暂无数据，不要推测数值。",
                        notes=notes)

    src_tag = "（数据来自导出文件）" if source == "export" else ""
    summary = (f"数据来自报表「{report.label}」：共 {len(parsed['rows'])} 行 × "
               f"{len(parsed['columns'])} 列，报表日期 {dt}{src_tag}。")
    return envelope(shape="table", title=report.title, summary=summary,
                    report_id=report.report_id, report_name=report.name,
                    report_label=report.label,
                    report_dt=dt, date_source=dinfo["date_source"], notice=dinfo["notice"],
                    url=url, unit=report.unit, table=parsed, notes=notes, source=source)


# ---------------------------------------------------------------- 明细平铺版式
def parse_matrix_report(report: Report, tables: list,
                        raw_tables: list | None = None) -> tuple[dict, int]:
    """解析「每行一个组合、每列一个指标」的明细表。

    表头形态两种都支持，由 build_header() 自动识别：
      ① 两行：第一行是整表合并的空白占位（无文字），第二行一列一个完整列名
         —— 这是 8670 贵金属业务的真实结构，占位行会被判为无内容而不计入表头层；
      ② 多层：第一层组表头（合并单元格）+ 第二层叶子表头，逐列拼成完整列名。
    拼好的列名再按报表声明的 fields 对齐。

    tables / raw_tables 是同一批表的两种形态（已展开 / 原始三元组）。
    表头重建**必须**用 raw_tables：展开后的行丢掉了 rowspan，
    既拼不出「组名+叶子名」，也无法判断单元格是否跨层去重。
    取数据行则用已展开的 tables（行内单元格已对齐到列）。
    返回 (parsed, 页面原始总行数)。
    """
    raw_rows = sum(len(t) for t in tables)
    src = raw_tables if raw_tables and len(raw_tables) == len(tables) else tables
    best = None          # (hit, n_data, parsed, header_info)
    for t, tr in zip(tables, src):
        t2 = _drop_leading_titles(t)
        tr2 = _drop_leading_titles(tr) if src is raw_tables else t2
        if len(t2) < 2:
            continue
        hi = build_header(tr2, report.fields)
        if hi["width"] < 3:
            continue
        cols = hi["columns"]
        aligned = align_to_fields(cols, report.fields, hi.get("pairs"))
        # 显式声明的列覆盖：页面表头口径与字段名不一致时，由报表自己兜底，
        # 不靠对字符串做启发式猜测（猜错会把数值挂到别的字段上）。
        for (g, lf), fn in (report.column_overrides or {}).items():
            if fn not in report.fields:
                continue
            for i, pr in enumerate(hi.get("pairs") or []):
                if pr == (g, lf):
                    for j, v in list(aligned["map"].items()):
                        if v == fn and j != i:
                            aligned["map"].pop(j)
                    aligned["map"][i] = fn
                    aligned["hit"] = len(aligned["map"])
                    break
        if aligned["hit"] < 2:
            continue
        data = []
        for r in t2[hi["leaf_index"] + 1:]:
            cells = cells_of(r)[: hi["width"]]
            if not any(c and not is_blank(c) for c in cells):
                continue
            cells += [""] * (hi["width"] - len(cells))
            data.append(cells)
        parsed = {"columns": cols, "rows": data,
                  "fieldMap": {str(k): v for k, v in aligned["map"].items()},
                  "headerBands": hi["bands"]}
        if best is None or (aligned["hit"], len(data)) > (best[0], best[1]):
            best = (aligned["hit"], len(data), parsed, hi)
    if best is None:
        return {"columns": [], "rows": []}, raw_rows
    return best[2], raw_rows


# ---------------------------------------------------------------- 分区堆叠版式
NUM_LIKE_FULL = re.compile(r"^-?[\d,]+(?:\.\d+)?%?$|^-?[\d,]+天$")


def looks_like_header(cells: list[str]) -> bool:
    filled = [c for c in cells if c and not is_blank(c)]
    if len(filled) < 3:
        return False
    numeric = sum(1 for c in filled if NUM_LIKE_FULL.match(c.replace(" ", "")))
    return numeric <= len(filled) * 0.25


def parse_sections_report(report: Report, tables: list) -> tuple[dict, int]:
    """解析「板块标题 + 表头 + 多行」的堆叠版式（如整体情况表）。

    输出仍是 columns/rows 两维结构，列固定为
    [板块, 维度类型, 维度值, 指标, 数值]，便于上层统一处理。
    """
    rows: list[list[tuple[str, int]]] = []
    for t in tables:
        if max((len(cells_of(r)) for r in t), default=0) >= 3:
            rows.extend(t)
    raw_rows = len(rows)
    if not rows:
        return {"columns": [], "rows": []}, 0

    dim_names = [d["name"] for d in report.dims] or ["板块", "统计维度"]
    # ⚠️ dim_cols 是**数据区的维度列数**（该报表实际有几列标签/取值），
    # 不等于 report.dims 的声明个数——dims 里可以有多个逻辑维度映射到同一列
    # （如「分组」和「统计维度」都来自第 1、2 列）。用 2 兜底，再按表头实测修正。
    dim_cols = 2
    dim_heads = [norm_text(n) for n in dim_names]

    records: list[list[str]] = []
    section, header = "", []
    for r in rows:
        cells = cells_of(r)
        spans = [s for _, s in r] or [1]
        filled = [c for c in cells if c and not is_blank(c)]
        if not filled:
            continue
        # 板块标题：整行只有一个非空格（通常 colspan 铺满整行）
        if len(filled) == 1 and (len(cells) == 1 or spans[0] >= 2):
            if not _is_header_word(filled[0]):
                section = filled[0]
                header = []
                continue
        # 表头行：首格是跨列合并（覆盖前两列），或首格为空，或首两格就是维度名
        if len(filled) >= 2 and (spans[0] >= dim_cols or not cells[0] or
                                 _is_dim_header(cells, dim_heads)):
            # 表头行首格合并了几列，决定数据区的维度列数
            if spans and spans[0] >= 2:
                dim_cols = spans[0]
            header = [c for c in cells[dim_cols:] if c]
            continue
        if not header and looks_like_header(cells):
            header = cells[dim_cols:]
            continue
        if not header:
            continue
        label = (cells[0] or "").strip()
        value = (cells[1] if len(cells) > 1 else "").strip()
        kind, dim = classify_dim(label, value, report)
        vals = cells[dim_cols:]
        for i, name in enumerate(header):
            if not name:
                continue
            v = vals[i] if i < len(vals) else ""
            if v in EMPTY_TOKENS:
                continue
            records.append([section, kind or dim_names[-1], dim, name, v])
    return {"columns": ["板块", "维度类型", "维度值", "指标", "数值"], "rows": records}, raw_rows


def _is_header_word(text: str) -> bool:
    return bool(re.search(r"(指标|项目|科目|名称|本期|金额|余额|单位)", text or ""))


def _is_dim_header(cells: list[str], dim_heads: list[str]) -> bool:
    hits = sum(1 for c in cells[: len(dim_heads)] if norm_text(c) in dim_heads)
    return hits >= 1 and any(norm_text(c) in dim_heads for c in cells)


def classify_dim(label: str, value: str, report: Report) -> tuple[str, str]:
    """由「维度列标签 + 维度取值」判定 (维度类型, 维度值)。

    🚨 **标签优先**，这是本函数的关键（2026-10-22 修）：
    8666 报表第一列是「按投资组合 / 按产品分类 / 合计」的分组标签，第二列才是取值。
    原实现先按「取值在不在某维度池里」判定，而 `TGLD` 这类值**同时**出现在
    「统计维度」池里（因为统计维度池 = 合计 + 全部组合码 + 全部产品分类），
    于是永远先命中「统计维度」，真实的「按投资组合 / 按产品分类」标签被丢掉，
    导致上层看到一列里混着码值和中文名，误以为"两套互斥分类混装"——
    实际是**分组维度整列丢失**。

    正确顺序：
      1) 标签能对上某维度名（「按投资组合」→ 投资组合）→ 就是它，不看取值；
      2) 标签是「合计/总计」→ 合计；
      3) 标签对不上时，才退回按取值判定。
    """
    lab, val = (label or "").strip(), (value or "").strip()
    if not val:
        val = lab
    if not val:
        return "未知", val

    # 1) 标签优先：dim 里声明的 labels 映射（「按投资组合」-> 分组·投资组合）
    if lab:
        for d in report.dims:
            labels = d.get("labels") or {}
            if lab in labels:
                # 维度类型返「分组」，但值要带上细分类别，否则上层分不清
                # 这一行是「按投资组合」还是「按产品分类」（两者不可相加）。
                return d["name"], f"{labels[lab]}:{val}" if labels[lab] != "合计" else "合计"
        bare = re.sub(r"^[按分依|]|[:：]", "", lab).strip()
        for d in report.dims:
            name = d["name"]
            if bare and (bare == name or bare in name or name in bare):
                return name, val
        if lab in ("合计", "小计", "总计", "全行合计") or bare in ("合计", "小计", "总计"):
            return "合计", val

    # 2) 标签对不上，才按取值落到哪个维度池
    for d in report.dims:
        pool = {str(v).upper() for v in d.get("values") or []}
        if pool and val.upper() in pool:
            return d["name"], val
    if val in ("合计", "小计", "总计", "全行合计"):
        return "合计", val
    if re.fullmatch(r"[A-Z]{4}", val.upper()) and PORTFOLIO_CODES:
        return ("投资组合" if val.upper() in {c.upper() for c in PORTFOLIO_CODES} else "未知"), val
    return "未知", val
