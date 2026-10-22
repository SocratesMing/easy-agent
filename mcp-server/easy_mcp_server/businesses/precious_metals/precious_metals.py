# -*- coding: utf-8 -*-
"""
precious_metals MCP Server v2.0 —— 贵金属业务数据查询（零依赖，仅标准库）

架构（v2.0 重构，2026-10-21）：
  一张报表 = 一个模块（reports/r<编号>_<名称>.py），各自声明：
    支持的维度、维度取值、指标字段、口语别名、路由关键词、板块/行过滤项。
  取数时按报表编号直达对应模块，不再「读了一张再读另一张」地试探；
  跨表问题（如「这个指标在哪张表里」）由 route() 按**各表自声明的关键词**打分选表，
  命中不明确时返回候选清单让上层决定，不擅自猜。

  pm_core.py       公共核心：配置、HTTP、HTML/XLSX/CSV 解析、合并单元格、双层表头重建
  reports/*.py     报表声明（维度/指标/关键词）
  precious_metals_query_mcp.py  本文件：工具实现 + JSON-RPC 协议层

关键修正（对比 v1.5）：
  1) 报表编号参数名统一为双下划线 __reportId（内网实测），删除单/双下划线双写兼容；
  2) 双层表头：第一层是合并单元格的组表头，第二层才是叶子字段名。
     build_header() 按列位置自上而下拼列名，解决「组表头展开后一堆空串」导致取不到数的问题；
  3) 工具精简为 5 个：list / query / breakdown / describe / probe。

启动： python precious_metals_query_mcp.py [--base-url http://ip:port]
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import re
import sys
import urllib.error

import pm_core as C
from pm_core import envelope, norm_text, norm_key, numeric, is_ratio, is_blank, cells_of
import reports as R

SERVER_NAME = C.SERVER_NAME
SERVER_VERSION = C.SERVER_VERSION
DEFAULT_BASE_URL = C.DEFAULT_BASE_URL
BASE_URL_SOURCE = C.BASE_URL_SOURCE

SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL = "2024-11-05"


# ================================================================ 报表取数缓存
_CACHE: dict[tuple[str, str], dict] = {}


def _load(report: R.Report, dt: str = "", base_url: str | None = None) -> dict:
    """取一张报表（同一次进程内按 报表+日期 缓存，避免同一问题重复拉页面）。"""
    dinfo = C.resolve_date(dt)
    key = (report.report_id, dinfo.get("report_dt", ""))
    if key not in _CACHE:
        _CACHE[key] = C.fetch_report(report, dt, base_url)
    return _CACHE[key]


# ================================================================ 记录拍平
def records_of(report: R.Report, env: dict) -> list[dict]:
    """把一张报表的解析结果拍平成统一记录：

    {"row_key", "dims": {维度名: 值}, "metric", "value", "section"}
    明细表按 fieldMap 把列下标映射回标准字段名；分区表直接用 板块/维度 列。
    """
    if env.get("shape") != "table":
        return []
    cols = env["table"]["columns"]
    fmap = {int(k): v for k, v in (env["table"].get("fieldMap") or {}).items()}
    rows = env["table"]["rows"]
    out: list[dict] = []

    if report.layout == "matrix":
        dim_names = [d["name"] for d in report.dims]
        for row in rows:
            dims = {}
            for i in range(min(len(dim_names), len(row))):
                dims[dim_names[i]] = row[i]
            key = "/".join(dims.get(n, "") for n in dim_names)
            for i in range(len(dim_names), len(cols)):
                name = fmap.get(i) or cols[i]
                if not name:
                    continue
                out.append({"row_key": key, "section": "", "dims": dims,
                            "metric": name, "value": row[i] if i < len(row) else ""})
        return out

    idx = {c: i for i, c in enumerate(cols)}

    def cell(row, *names):
        for n in names:
            i = idx.get(n)
            if i is not None and i < len(row):
                return row[i]
        return ""

    for row in rows:
        section = cell(row, "板块")
        dim = cell(row, "维度值", "统计维度") or "合计"
        kind = cell(row, "维度类型") or "合计"
        # 行键必须保留「合计」身份：dim 为空或「合计」时若只写 section，
        # 「贵金属业务损益情况/合计」会退化成「贵金属业务损益情况」，
        # 后续按 row_key 判断合计行的逻辑（合计置顶、breakdown 排除合计）会全部失效。
        if section:
            key = f"{section}/合计" if dim in ("", "合计") else f"{section}/{kind} {dim}"
        else:
            key = dim
        out.append({
            "row_key": key,
            "section": section, "dims": {"板块": section, "统计维度": dim, "维度类型": kind},
            "metric": cell(row, "指标"), "value": cell(row, "数值"),
        })
    return out


# ================================================================ 指标命中
def _terms(raw: str, report: R.Report) -> list[str]:
    """把用户问法展开成候选指标关键词（含本报表声明的别名）。"""
    key = norm_key(raw)
    terms = [key] if key else []
    for alias, targets in report.aliases.items():
        na = norm_key(alias)
        if not na:
            continue
        if na in key or (len(key) >= 2 and key in na):
            terms.extend(norm_key(t) for t in targets)
    return list(dict.fromkeys(t for t in terms if t))


def _name_hit(name: str, terms: list[str]) -> bool:
    n = norm_key(name)
    if not n:
        return False
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


def _pick_metric(records: list[dict], raw: str, report: R.Report) -> list[dict]:
    """按指标名筛记录：先精确，再别名，最后模糊建议。

    返回命中的记录列表（可能为空）；不做跨报表的兜底——兜底由 route 决定。
    """
    core = (raw or "").strip()
    if not core:
        return records
    if core in report.fields:
        return [r for r in records if r["metric"] == core]
    terms = _terms(core, report)
    hits = [r for r in records if _name_hit(r["metric"], terms)]
    # 「问交易笔数不要带出新发生-交易笔数」
    if core and "新发生" not in core:
        hits = [r for r in hits if not r["metric"].startswith("新发生")]
    # 精确优先：命中里存在名称直接含 core 的项时，丢弃仅靠别名带出的近似项
    nc = norm_key(core)
    core_hits = [h for h in hits if nc and nc in norm_key(h["metric"])]
    if core_hits:
        return core_hits
    # 比率类指标不能拿金额类指标顶替（量纲不同）
    if is_ratio(core):
        return [h for h in hits if is_ratio(h["metric"])]
    return hits


def _metric_names(report: R.Report) -> list[str]:
    return list(report.fields)


# ================================================================ 维度过滤

# 「整个部门」口径的同义词（2026-10-22 补）。
# 贵金属两张报表**没有中心/部门维度**，用户问「金融市场部」时不能去池子里找这个值
# （找到才会有鬼），正确语义是「不要筛选 = 全量」，再由上层按投资组合/产品分类展开。
# 判据必须**严格**：只认「明确指整个部门」的说法，绝不能把「资金」「投资」这类中心词吞进来。
_DEPT_WORDS = (
    "金融市场部", "金融市场部门", "金融市场", "分行金融市场部",
    "中信银行金融市场", "中信银行金融市场部", "中信金融市场",
    "资金业务部", "资金业务中心",
    "全部门", "整个部门", "全部部门", "各部门", "全部组合", "全行贵金属", "全行口径",
    "部门口径", "部门整体", "部门合计", "部门汇总", "咱们部门", "我们部门", "本部门",
    "整体情况", "全部数据", "全量",
)


def _is_dept_word(val: str) -> bool:
    """该取值是否表示「整个部门」（= 全量，不做维度筛选）。"""
    s = norm_key(val)
    if not s:
        return False
    return any(norm_key(w) == s or norm_key(w) in s for w in _DEPT_WORDS)


def _dim_match(value: str, flt: str) -> bool:
    if not flt:
        return True
    v, f = norm_key(value), norm_key(flt)
    return bool(v) and (f in v or v in f)


def _resolve_dim_filters(report: R.Report, args: dict, question: str) -> dict:
    """把 {维度名: 值} 校验成合法取值，并给出需要反问的提示。

    「整个部门」类说法（金融市场部 / 全部门 / 整个部门 …）不是维度取值，
    而是**「不筛选 = 全量」**的意思：直接忽略该参数并记一条 dept_scope 提示，
    交给上层按分类维度展开。绝不能报「取值不合法」——那会让用户以为数据不存在。
    """
    out: dict[str, str] = {}
    problems: list[str] = []
    dept_hit: str | None = None
    for d in report.dims:
        name = d["name"]
        val = (args.get(name) or "").strip()
        if not val:
            continue
        # 部门口径：不当取值校验，直接视为「全量」
        if _is_dept_word(val):
            dept_hit = dept_hit or val
            continue
        pool = [str(v) for v in d.get("values") or []]
        if not pool:
            out[name] = val
            continue
        if d.get("match") == "code":
            hit = next((c for c in pool if c.upper() == val.upper()), None)
            if hit:
                out[name] = hit
            else:
                problems.append(
                    f"「{name}」在报表里是 4 位英文码值，没有中文名，"
                    f"无法用「{val}」这类中文描述筛选。可选码值（共 {len(pool)} 个）："
                    + "、".join(pool))
        else:
            hit = next((c for c in pool if c == val), None) or \
                  next((c for c in pool if _dim_match(c, val)), None)
            if hit:
                out[name] = hit
            else:
                problems.append(f"「{name}」没有「{val}」这个取值。合法取值："
                                + "、".join(pool[:60]) + ("…" if len(pool) > 60 else ""))
    return {"dims": out, "problems": problems, "dept_scope": dept_hit}


def _auto_dims(question: str, records: list[dict], report: R.Report) -> dict:
    """从整句里自动识别维度值（细粒度优先：五级产品 > 产品分类 > 投资组合）。"""
    found: dict[str, str] = {}
    low = norm_key(question)
    if not low:
        return found
    ordered = sorted(report.dims, key=lambda d: len(d.get("values") or []))
    for d in ordered:
        name = d["name"]
        if d.get("match") == "code":
            up = (question or "").upper()
            for code in d.get("values") or []:
                if re.search(r"(?<![A-Z0-9])" + re.escape(code.upper()) + r"(?![A-Z0-9])", up):
                    found[name] = code
                    break
            continue
        for rec in records:
            val = rec["dims"].get(name, "")
            if val and val not in ("合计", "小计", "总计") and norm_key(val) in low:
                found[name] = val
                break
    return found


def _apply_dims(records: list[dict], dims: dict) -> list[dict]:
    if not dims:
        return records
    out = []
    for r in records:
        ok = True
        for name, want in dims.items():
            have = r["dims"].get(name, "")
            if not _dim_match(have, want):
                ok = False
                break
        if ok:
            out.append(r)
    return out


# ================================================================ 路由
def _metric_owner(question: str) -> str | None:
    """判断问题里的**指标**属于哪张报表。返回 report_id 或 None。

    为什么要单独判「指标归属」：
    「各投资组合的营业净收入」这类问法里，**指标词和维度词分属两张表**——
    「投资组合」是 8670 的维度，「营业净收入」是 8666 的独占指标（8670 根本没有）。
    纯靠词频打分会把这类问题判给 8670，然后报「未命中」。
    而实际上 8666 的「统计维度」里**同样有那 35 个投资组合码值**，
    所以「按组合拆收入」在 8666 完全查得到。

    规则：**指标比维度更具体**，指标命中就按指标走。
    只在「某张表命中了指标词」且「另一张表只命中维度词」时生效，
    两边都命中指标时不做裁决（交回原打分逻辑）。
    """
    q = norm_key(question)
    if not q:
        return None

    def _hit_fields(rep):
        """命中的**指标**字段名，返回实际字段名集合。

        ⚠️ 必须排除维度列（「投资组合」「产品分类」「五级产品」「板块」「统计维度」）：
        它们虽然也在 fields 里，但语义是「怎么分组」而不是「量什么」。
        不排除的话，「各投资组合的营业净收入」会同时命中
        8670 的「投资组合」和 8666 的「营业净收入」，两边都有"指标"命中，
        裁决就直接放弃了——正是要修的那个 case。
        """
        dim_names = set()
        for d in rep.dims:
            dim_names.add(d.get("name", ""))
        got = set()
        for f in rep.fields:
            if f in dim_names:
                continue
            nf = norm_key(f)
            # 字段名太长（如「业务损益折人民币（不含内部账）」）不适合直接子串匹配整句，
            # 只认长度 >= 3 且完整出现的；短字段名（如「营业净收入」）才是强信号。
            if len(nf) >= 3 and nf in q:
                got.add(f)
        for spoken, targets in rep.aliases.items():
            if norm_key(spoken) and norm_key(spoken) in q:
                got.update(t for t in targets if t in rep.fields and t not in dim_names)
        return got

    def _hit_dims(rep):
        """命中的维度**取值**（如「投资组合」下的 4 位码值）。"""
        q_ = q
        got = set()
        for d in rep.dims:
            for v in (d.get("values") or []):
                nv = norm_key(v)
                if len(nv) >= 3 and nv in q_:
                    got.add(v)
                    break
            # 维度名本身（「投资组合」「产品分类」）也算维度信号
            nd = norm_key(d.get("name", ""))
            if len(nd) >= 3 and nd in q_:
                got.add(d.get("name", ""))
        return got

    owners: dict[str, set] = {}
    for rep in R.ALL:
        f = _hit_fields(rep)
        if f:
            owners[rep.report_id] = f
    if len(owners) != 1:
        # 0 张或 ≥2 张表都有指标命中 -> 不裁决，交回打分
        return None
    rid, fields = next(iter(owners.items()))
    # 维度词只在**另一张**表命中时，指标优先
    for o in R.ALL:
        if o.report_id == rid:
            continue
        if _hit_dims(o) and not _hit_fields(o):
            return rid
    return None


def route(question: str, report_id: str = "") -> dict:
    """决定这个问题该查哪张报表。

    打分依据各报表自己声明的 keywords / scope_words / fields / dims / 别名，
    不依赖固定顺序，也不靠「读一张试一张」。
    返回 {report, score, ranked, ambiguous, candidates}
    """
    if report_id:
        r = R.get(report_id)
        if r:
            return {"report": r, "score": 999, "ranked": [(r, 999)], "ambiguous": False,
                    "candidates": [r]}
        return {"report": None, "score": 0, "ranked": [], "ambiguous": False, "candidates": [],
                "error": f"不支持的报表标识 {report_id}，可选：{'、'.join(R.labels())}"}

    # 指标归属优先：解决「投资组合的营业净收入」这类指标准、维度跨表的问法
    owner = _metric_owner(question)
    if owner:
        rep = R.get(owner)
        others = [(r, 0) for r in R.ALL if r.report_id != owner]
        return {"report": rep, "score": 999, "ranked": [(rep, 999)] + others,
                "ambiguous": False, "candidates": [rep],
                "reason": f"问题里的指标只在「{rep.label}」中提供，已按指标归属定位报表。"}

    q = norm_key(question)
    ranked: list[tuple[R.Report, int]] = []
    for rep in R.ALL:
        score = 0
        detail: dict[str, int] = {}
        for concept, words in rep.keywords.items():
            hit = sum(1 for w in words if norm_key(w) and norm_key(w) in q)
            if hit:
                detail[concept] = hit
                score += hit * 3
        for w in rep.scope_words:
            if norm_key(w) and norm_key(w) in q:
                score += 5
        for f in rep.fields:
            if norm_key(f) and norm_key(f) in q:
                score += 4
        for a in rep.aliases:
            if norm_key(a) and norm_key(a) in q:
                score += 2
        for d in rep.dims:
            for v in d.get("values") or []:
                nv = norm_key(v)
                if len(nv) >= 3 and nv in q and not d.get("values") or len(nv) >= 3 and nv in q:
                    score += 2
                    break
        # 明确说了「分产品/明细/组合」是本表专属信号，说了「整体/占比/营收」是另一张
        ranked.append((rep, score))
    ranked.sort(key=lambda x: -x[1])

    if not ranked:
        return {"report": None, "score": 0, "ranked": [], "ambiguous": False, "candidates": []}

    top_rep, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if top <= 0:
        # 完全没命中：不猜，把所有报表作为候选交回上层
        return {"report": None, "score": 0, "ranked": ranked, "ambiguous": True,
                "candidates": [r for r, _ in ranked],
                "reason": "问题里没有出现任何可识别的报表特征词，无法判断该查哪张表。"}
    if top == second and len(ranked) > 1:
        return {"report": None, "score": top,
                "ranked": [x for x in ranked if x[1] == top], "ambiguous": True,
                "candidates": [r for r, s in ranked if s == top],
                "reason": "两张报表对该问题的特征命中相同，需要明确是指明细还是整体。"}
    if top - second < 3 and second > 0 and _has_close_metric(question, top_rep, ranked[1][0]):
        return {"report": None, "score": top, "ranked": ranked[:2], "ambiguous": True,
                "candidates": [top_rep, ranked[1][0]],
                "reason": "指标名与两张报表都沾边，需要明确口径。"}
    return {"report": top_rep, "score": top, "ranked": ranked, "ambiguous": False,
            "candidates": [top_rep, ranked[1][0]] if len(ranked) > 1 else [top_rep]}


def _has_close_metric(question: str, a: R.Report, b: R.Report) -> bool:
    q = norm_key(question)
    if not q:
        return False
    ha = any(norm_key(f) in q for f in a.fields)
    hb = any(norm_key(f) in q for f in b.fields)
    return ha and hb


# ================================================================ 指标单位
# 报表声明的 unit 是「金额单位」，只适用于金额类指标。
# 笔数 / 期限 / 占比这类指标若沿用金额单位，会被误标成「元」，
# 并让 965 这种整数变成 965.00，上游模型极易误读成金额。
# 键为指标名（精确匹配），值为 (展示单位, 小数位)。
_METRIC_UNITS: dict[str, tuple[str, int]] = {
    "交易笔数": ("笔", 0),
    "新发生-交易笔数": ("笔", 0),
    "平均持有期限": ("天", 2),
    "新发生-平均持有期限": ("天", 2),
}


def _metric_unit(metric: str, value: str, report_unit: str) -> tuple[str, int | None]:
    """返回 (单位文案, 小数位)。小数位为 None 表示按原值原样输出。

    优先级：百分比（看值本身）> 指标专有单位 > 报表金额单位。
    """
    if str(value).strip().endswith("%"):
        return "%", 2
    key = str(metric).strip()
    if key in _METRIC_UNITS:
        return _METRIC_UNITS[key]
    return report_unit, 2


# ================================================================ 工具实现
def list_reports() -> dict:
    """列出所有报表及各自的维度、指标、单位。"""
    dinfo = C.resolve_date("")
    rows = []
    for rep in R.ALL:
        # 首列固定为报表标识（业务域-报表名），Agent 引用数据时直接用这个标识标注来源
        rows.append([rep.label, rep.report_id, rep.name, rep.unit,
                     "/".join(d["name"] for d in rep.dims),
                     str(len(rep.fields)),
                     rep.desc])
    return envelope(
        shape="table", title="可用报表清单",
        summary=f"共 {len(R.ALL)} 张报表：" + "、".join(r.label for r in R.ALL) +
                f"；默认查询日期（前一日）为 {dinfo['report_dt']}。"
                f"当前报表服务地址：{DEFAULT_BASE_URL or '未配置'}。",
        report_dt=dinfo["report_dt"], date_source="default_yesterday", notice=dinfo["notice"],
        table={"columns": ["报表标识", "报表编号", "报表名称", "金额单位",
                           "维度", "指标数", "说明"], "rows": rows},
        notes=["回答里引用数值时，请带上「报表标识」注明数据出自哪张报表。",
               "维度取值范围：" + "；".join(
                   f"{rep.label} → " + "、".join(f"{d['name']}({len(d.get('values') or [])}个)"
                                                for d in rep.dims)
                   for rep in R.ALL)] + [dinfo["notice"]],
    )


def query(report: str = "", metric: str = "", report_dt: str = "", top_n: int = 0,
          order: str = "desc", base_url: str | None = None, **dim_filters) -> dict:
    """主查询工具：不指定报表时自动路由；指定场景条件时直接走取数，不闲聊。

    - 只有题面、没给 metric：按题面路由后，返回该报表里命中的指标（或整表）
    - 给了 metric：路由 + 指标筛选 + 维度过滤
    - 给了 top_n > 0：按指标排序取前 N
    """
    question = (metric or "").strip()
    rt = route(question, report)
    if rt.get("error"):
        return envelope(shape="text", title="报表标识无效", summary=rt["error"])
    if rt["ambiguous"]:
        cands = rt["candidates"]
        return envelope(
            shape="text", title="需要确认是哪张报表",
            summary=(f"{rt.get('reason', '')} 可选报表："
                     + "；".join(f"「{r.name}」— {r.desc[:60]}" for r in cands)),
            table={"columns": ["报表", "适用场景", "维度"],
                   "rows": [[r.name, r.desc, "/".join(d["name"] for d in r.dims)] for r in cands]},
            notes=["请让用户明确「分产品明细」还是「整体情况」，或用 report 参数指定报表名。"],
        )

    rep = rt["report"]
    env = _load(rep, report_dt, base_url)
    dt = env.get("report_dt", "")
    if env.get("shape") != "table":
        return env

    dim_args, unknown = _split_dims(rep, dim_filters)
    if unknown:
        return envelope(shape="text", title="不支持的筛选参数", report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt,
                        summary=f"「{rep.label}」没有名为 {('、'.join(unknown))} 的维度。",
                        notes=[f"可用维度参数：{rep.dim_key(d)}（{d['name']}）" for d in rep.dims])

    recs = records_of(rep, env)
    filters = _resolve_dim_filters(rep, dim_args, question)
    if filters["problems"]:
        return envelope(shape="text", title="维度取值不合法", report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt,
                        summary="；".join(filters["problems"]),
                        notes=[f"报表：{rep.label}（报表编号 {rep.report_id} 仅供排障追溯）",
                               "可用维度：" + "、".join(f"{d['name']}({d.get('key')})"
                                                       for d in rep.dims)])

    auto = _auto_dims(question, recs, rep)
    use = dict(auto)
    use.update(filters["dims"])
    row_filtered = bool(use)

    # 「金融市场部 / 全部门」= 全量口径：不加任何维度筛选，并在结果里显式声明
    dept_note = ""
    if filters.get("dept_scope"):
        dept_note = (f"「{filters['dept_scope']}」按**全部门口径**处理"
                     f"（本报表无部门/中心维度，已取全部数据，未做维度筛选）。")

    hits = _pick_metric(recs, question, rep) if question else recs
    hits = _apply_dims(hits, use)

    if not hits:
        pool = _metric_names(rep)
        near = difflib.get_close_matches(norm_key(question), [norm_key(f) for f in pool],
                                         n=5, cutoff=0.2) if question else []
        near_names = []
        for n in near:
            for f in pool:
                if norm_key(f) == n and f not in near_names:
                    near_names.append(f)
        dim_desc = " / ".join(f"{k}={v}" for k, v in use.items())

        # 区分两种「查不到」，否则会误导用户以为报表里没这个概念：
        #   ① 指标名本身不存在  -> 报「没有这个指标」
        #   ② 指标存在、维度值也合法，但**当期无数据** -> 报「该档位本期无数据」
        # 典型场景：「贵金属即期」是合法五级产品档位，但当期没有发生额，
        # 若笼统说「没有匹配的数据」，用户会以为报表不支持即期。
        _metric_hit = bool(question) and bool(_pick_metric(recs, question, rep))
        # 维度值是否**合法**（在报表声明的取值池里），而不是"当期是否有数据"。
        # 「贵金属即期」是合法档位但当期无发生额，必须按「本期无数据」报，
        # 不能按「报表没这个概念」报——否则用户会误以为报表不支持即期。
        _dim_declared = all(
            any(str(v).upper() == str(vv).upper() for vv in (d.get("values") or []))
            for k, v in use.items()
            for d in rep.dims if d.get("name") == k
        ) if use else True
        if _metric_hit and _dim_declared and use:
            miss_title = f"该档位本期无数据：{dim_desc}"
            miss_summary = (f"「{rep.label}」中「{question}」这个指标存在，"
                            f"且「{dim_desc}」是报表登记的合法档位，"
                            f"但**本期没有数据**。这不代表报表不支持该档位，"
                            f"换期次或去掉维度筛选再试。")
            miss_notes = ["可先不带维度取全量，确认该指标本期总共有哪些档位有数。"]
        else:
            miss_title = f"未命中：{question or '该维度'}"
            miss_summary = (f"「{rep.label}」里没有匹配「{question}」的数据"
                            + (f"（筛选条件：{dim_desc}）" if dim_desc else "") +
                            f"。请核对指标名或维度取值，或调 precious_metals_describe 查看本表可用指标。")
            miss_notes = [f"可能是你想找的：{'、'.join(near_names)}"] if near_names else []
        return envelope(
            shape="text", title=miss_title,
            report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt, date_source=env.get("date_source", ""),
            notice=env.get("notice", ""),
            summary=miss_summary,
            notes=miss_notes,
        )

    # 排序 / 取前 N
    if top_n and top_n > 0:
        items = [h for h in hits if numeric(h["value"]) is not None]
        items.sort(key=lambda h: numeric(h["value"]), reverse=(order != "asc"))
        hits = items[:top_n]

    # 整体口径问法（未指定维度、也没说"各/分别"）把合计行置顶
    if not row_filtered and not top_n and not re.search(r"各|每个|分别|逐个|每一|明细", question):
        totals = [h for h in hits if "合计" in str(h["row_key"])]
        if totals:
            hits = totals + [h for h in hits if "合计" not in str(h["row_key"])]

    cols = ["报表标识", "板块/行", "指标", "数值", "数值_num", "单位"]
    out_rows = []
    for h in hits:
        n = numeric(h["value"])
        u, nd = _metric_unit(h["metric"], h["value"], rep.unit)
        num_txt = "" if n is None else (f"{n:.0f}" if nd == 0 else f"{n:.2f}")
        out_rows.append([rep.label, h["row_key"], h["metric"], h["value"],
                         num_txt, u])

    notes = [f"报表：{rep.label}（报表编号 {rep.report_id} 仅供排障追溯）", f"金额单位：{rep.unit}",
             "数值_num 为去掉千分位的浮点数，用于排序/加减/占比计算。"]
    # 🚨 8666「统计维度」混装两套互斥分类（4 位码值类 9 行 + 中文产品类 4 行），
    # 实测 13 行全加 = 598,668,964，比合计行 265,711,652 多 125%。
    # 维度描述里写了「不可相加」，但模型看不见就不会守，必须把警告顶到 notes 前面。
    if rep.report_id == "8666" and len(out_rows) > 2 and \
            any("统计维度" in str(r[1]) for r in out_rows):
        notes.insert(0, "⚠️ 本表「统计维度」把**两套互斥分类**混在一列："
                        "9 个 4 位码值类 + 4 个中文产品类，还有 1 个合计行。"
                        "**各行严禁相加**（相加会比合计行多出 100% 以上）。"
                        "总量一律取「合计」行；要比大小只比同一套分类内部。")
    if dept_note:
        notes.insert(0, dept_note)
    if auto:
        notes.append(f"已从问题中自动识别维度：{auto}")
    if filters["dims"]:
        notes.append(f"按入参指定的维度过滤：{filters['dims']}")
    if env.get("notice"):
        notes.append(env["notice"])
    notes.extend(rep.notes)

    title = f"{rep.name}：{question}" if question else f"{rep.name} 全量"
    if top_n:
        title = f"{rep.name}：{question} Top{top_n}（{'降序' if order != 'asc' else '升序'}）"
    dept_prefix = f"（全部门口径）" if dept_note else ""
    return envelope(
        shape="table", title=title,
        summary=f"数据来自报表「{rep.label}」{dept_prefix}：命中 {len(out_rows)} 条，报表日期 {dt}。",
        report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt,
        date_source=env.get("date_source", ""), notice=env.get("notice", ""),
        url=env.get("url", ""), unit=rep.unit, source=env.get("source", ""),
        table={"columns": cols, "rows": out_rows}, notes=notes,
    )


def breakdown(report: str = "", metric: str = "", report_dt: str = "", top_n: int = 0,
              order: str = "desc", base_url: str | None = None, **dim_filters) -> dict:
    """按本表的行维度拆解某个指标（分组合 / 分产品 / 分板块），支持排名取前 N。"""
    rt = route(metric or "", report)
    if rt["ambiguous"] or not rt["report"]:
        return query(report=report, metric=metric, report_dt=report_dt, top_n=top_n,
                     order=order, base_url=base_url, **dim_filters)
    rep = rt["report"]
    env = _load(rep, report_dt, base_url)
    dt = env.get("report_dt", "")
    if env.get("shape") != "table":
        return env
    recs = records_of(rep, env)
    dim_args, unknown = _split_dims(rep, dim_filters)
    if unknown:
        return envelope(shape="text", title="不支持的筛选参数", report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt,
                        summary=f"「{rep.label}」没有名为 {('、'.join(unknown))} 的维度。",
                        notes=[f"可用维度参数：{rep.dim_key(d)}（{d['name']}）" for d in rep.dims])
    filters = _resolve_dim_filters(rep, dim_args, metric)
    if filters["problems"]:
        return envelope(shape="text", title="维度取值不合法", report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt,
                        summary="；".join(filters["problems"]))

    hits = _pick_metric(recs, metric, rep)
    hits = _apply_dims(hits, filters["dims"])
    def _is_rollup(key: str) -> bool:
        """是否为小计/总计/合计这类汇总行。

        行键形如「贵金属业务损益情况/合计」「TGLD 小计//」「合计//」，
        合计可能在**末尾段**而不是开头，所以按「/」切段后逐段判断，
        不能只看 startswith。
        """
        segs = [s.strip() for s in str(key).split("/") if s.strip()]
        return any(any(k in s for k in ("小计", "总计", "合计")) for s in segs)

    hits = [h for h in hits if not _is_rollup(h["row_key"])]
    items = [h for h in hits if numeric(h["value"]) is not None]
    if not items:
        return envelope(shape="text", title=f"无法拆解：{metric}", report_id=rep.report_id, report_name=rep.name, report_label=rep.label,
                        report_dt=dt, summary=f"「{rep.label}」中没有可用于拆解的指标「{metric}」。",
                        notes=[f"可用指标：{'、'.join(rep.fields[:12])}…"])

    items.sort(key=lambda h: numeric(h["value"]), reverse=(order != "asc"))
    if top_n and top_n > 0:
        items = items[:top_n]
    rows = [[str(i + 1), h["row_key"], h["metric"], h["value"], f"{numeric(h['value']):.2f}"]
            for i, h in enumerate(items)]
    cols = ["排名", "维度组合", "指标", "数值", "数值_num", "单位"]
    dept_prefix = f"（全部门口径：{filters['dept_scope']}，未做维度筛选）" if filters.get("dept_scope") else ""
    return envelope(
        shape="table", title=f"{rep.name}：{metric} 拆解（{'降序' if order != 'asc' else '升序'}）",
        summary=f"数据来自报表「{rep.label}」{dept_prefix}：按「{'/'.join(d['name'] for d in rep.dims)}」拆解，"
                f"共 {len(rows)} 行，报表日期 {dt}。已排除合计/小计行。",
        report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt,
        date_source=env.get("date_source", ""), notice=env.get("notice", ""),
        unit=rep.unit, source=env.get("source", ""),
        table={"columns": cols, "rows": rows},
        notes=([dept_prefix] if dept_prefix else []) +
              [f"报表：{rep.label}（报表编号 {rep.report_id} 仅供排障追溯）",
               "数值_num 可直接用于比较。"] + rep.notes,
    )


def describe(report: str = "", field: str = "", report_dt: str = "") -> dict:
    """说明某张报表的维度取值与字段口径；不传 report 时说明全部报表。"""
    if not report and not field:
        rows = [[rep.label, rep.unit, "/".join(d["name"] for d in rep.dims),
                 f"{len(rep.fields)} 项", rep.desc] for rep in R.ALL]
        return envelope(shape="table", title="报表与口径总览",
                        summary=f"共 {len(R.ALL)} 张报表：" + "、".join(r.label for r in R.ALL) +
                                "。查具体维度取值请传 report + field。",
                        table={"columns": ["报表标识", "单位", "维度", "指标数", "说明"],
                               "rows": rows})

    rep = R.get(report) if report else None
    if rep is None and report:
        matched = [r for r in R.ALL if norm_key(report) in norm_key(r.name) or
                   norm_key(r.name) in norm_key(report)]
        rep = matched[0] if len(matched) == 1 else None
    if rep is None:
        # 只给了 field：在全部报表里找维度名
        for cand in R.ALL:
            for d in cand.dims:
                if norm_key(field) and (norm_key(field) in norm_key(d["name"]) or
                                        norm_key(d["name"]) in norm_key(field)):
                    rep = cand
                    break
            if rep:
                break
    if rep is None:
        return envelope(shape="text", title="未找到对应报表",
                        summary=f"没有与「{report or field}」对应的报表。可选：{'、'.join(R.labels())}")

    key = (field or "").strip()
    if key:
        for d in rep.dims:
            if norm_key(key) and (norm_key(key) in norm_key(d["name"]) or
                                  norm_key(d["name"]) in norm_key(key)):
                vals = [str(v) for v in d.get("values") or []]
                if vals:
                    return envelope(
                        shape="table", title=f"{rep.name} · 维度取值：{d['name']}",
                        summary=f"{d.get('desc', '')} 共 {len(vals)} 个取值。",
                        report_id=rep.report_id, report_name=rep.name, report_label=rep.label,
                        table={"columns": ["序号", "取值"], "rows": [[str(i + 1), v] for i, v in enumerate(vals)]},
                        notes=[f"传给工具时用参数名 {d.get('key') or d['name']}。"])
        docs = dict(rep.field_docs)
        nk = norm_key(key)
        hit = [(k, v) for k, v in docs.items() if nk and (norm_key(k) in nk or nk in norm_key(k))]
        if hit:
            return envelope(shape="table", title=f"{rep.name} · 口径说明：{key}",
                            summary=f"命中 {len(hit)} 条。", report_id=rep.report_id, report_name=rep.name, report_label=rep.label,
                            table={"columns": ["字段/口径", "说明"], "rows": [[k, v] for k, v in hit]})
        near = difflib.get_close_matches(nk, [norm_key(k) for k in docs], n=4, cutoff=0.2)
        return envelope(shape="text", title=f"未收录：{key}", report_id=rep.report_id, report_name=rep.name, report_label=rep.label,
                        summary=f"「{rep.label}」的口径字典中没有「{key}」。",
                        notes=[f"相近条目：{'、'.join(near)}"] if near else [])

    return envelope(
        shape="table", title=f"{rep.name} · 指标与维度",
        summary=f"{rep.desc}（金额单位：{rep.unit}）",
        report_id=rep.report_id, report_name=rep.name, report_label=rep.label,
        table={"columns": ["类别", "名称", "说明"],
               "rows": ([["维度", d["name"], d.get("desc", "")] for d in rep.dims]
                        + [["指标", f, ""] for f in rep.fields])},
        notes=rep.notes,
    )


def probe(report: str = "", report_dt: str = "", base_url: str | None = None) -> dict:
    """页面结构体检：取不到数、或换到内网/报表改版后先调它。只做诊断，不返回业务数值。"""
    rep = R.get(report) if report else R.ALL[0]
    if rep is None:
        rep = R.ALL[0]
    dinfo = C.resolve_date(report_dt)
    if "error" in dinfo:
        return envelope(shape="text", title="日期参数错误", summary=dinfo["error"])
    dt = dinfo["report_dt"]
    url = C.build_url(rep.report_id, dt, base_url)

    try:
        html = C.fetch_html(url)
    except urllib.error.HTTPError as e:
        return envelope(shape="text", title="页面打不开", report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt, url=url,
                        summary=f"报表页面返回 HTTP {e.code}，这不是解析问题，是地址/权限/报表编号问题。")
    except Exception as e:
        return envelope(shape="text", title="页面打不开", report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt, url=url,
                        summary=f"无法连接报表服务（当前 {DEFAULT_BASE_URL}）：{e}")

    ext = C.extract_tables(html)
    dom = C._DomParser()
    dom.feed(html)
    text_only = re.sub(r"(?s)<[^>]+>", " ", html)
    text_only = " ".join(C.html_unescape(text_only).split())
    numbers = re.findall(r"-?[\d][\d,]*(?:\.\d+)?", text_only)
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    script_n = len(re.findall(r"(?is)<script\b", html))
    iframe_n = len(re.findall(r"(?is)<iframe\b", html))

    rows: list[list[str]] = [
        ["报表", rep.name, f"编号 {rep.report_id}"],
        ["报表地址", url, f"参数名 {C.REPORT_ID_PARAM}"],
        ["页面体积", f"{len(html.encode('utf-8')):,} 字节", "JS 空壳通常 2KB 以内"],
        ["页面标题", (title_m.group(1).strip()[:60] if title_m else "（无 <title>）"),
         "登录页会显示登录/统一认证字样"],
        ["可见文字量", f"{len(text_only)} 字符 / 约 {len(numbers)} 个数字片段", "有数字说明数据在 HTML 里"],
        ["<table> 数量", str(ext["diag"].get("table_count", 0)), "0 表示不是标准表格页"],
        ["表格总行数", str(ext["diag"].get("table_rows", 0)), "行数为 0 但 table 数 >0 多为空壳模板"],
        ["判定抽取方式", C.STRATEGY_NAMES.get(ext["strategy"], ext["strategy"]), "本次实际走的降级策略"],
        ["<script> / <iframe>", f"{script_n} / {iframe_n}", "脚本多且文字少 = 前端渲染"],
    ]

    # 表头诊断：这是双层表头最需要看的一项
    ok_tables = [t for t in ext["tables"] if len(t) >= 2]
    header_note = ""
    if ok_tables:
        t0 = C._drop_leading_titles(max(ok_tables, key=lambda t: max((len(cells_of(r)) for r in t), default=0)))
        hi = C.build_header(t0, rep.fields)
        al = C.align_to_fields(hi["columns"], rep.fields)
        rows.append(["表头层数", str(hi["bands"]),
                     "1=单层；2=双层（第一层合并单元格组表头 + 第二层叶子表头）"])
        rows.append(["表头区域行", " / ".join(
            " | ".join(c for c in cells_of(t0[i]) if c)[:90]
            for i in range(max(0, hi["leaf_index"] - hi["bands"] + 1), hi["leaf_index"] + 1)),
            "自上而下拼列名所用到的行"])
        rows.append(["识别出的列（前 8）", " | ".join(hi["columns"][:8]), "拼好的完整列名"])
        rows.append(["字段命中", f"{al['hit']} / {len(rep.fields)}",
                     "命中的列能与报表声明的指标名对上；命中过少说明表头结构变了"])
        if al["dup"]:
            rows.append(["重复列名", "、".join(al["dup"][:6]), "同名列可能是合并残留，需要核对"])
        header_note = f"表头 {hi['bands']} 层，字段命中 {al['hit']}/{len(rep.fields)}。"
    else:
        rows.append(["表头层数", "未取到表格", "页面无可用表格结构"])

    exp = C.fetch_export(rep, dt, base_url)
    if exp.get("skipped"):
        rows.append(["导出接口（export_url）", "未配置",
                     "把平台导出按钮的地址填进 config.json 的 export_url，取数会优先解析导出文件"])
    elif exp.get("ok"):
        rows.append(["导出接口（export_url）", f"可用（{exp['kind']}）", exp["detail"]])
    else:
        rows.append(["导出接口（export_url）", "失败", f"{exp.get('error')}；地址：{exp.get('url')}"])

    strategy = ext["strategy"]
    if strategy == "table":
        advice = "标准表格，解析可靠。"
    elif strategy in ("grid", "blocks"):
        advice = f"页面按「{C.STRATEGY_NAMES[strategy]}」抽取，列对齐依赖节点结构，改版后需复核表头。"
    elif strategy == "text":
        advice = "只能按文本行重建，列对齐最脆弱，仅作应急；优先考虑导出接口或 render=force。"
    else:
        advice = ("没有识别出任何行列结构。排查顺序：① config.json 设 render=force 走无头浏览器；"
                  "② 确认是否需要登录态；③ 找平台的导出接口替代抓页面。")
    if exp.get("ok"):
        advice = f"导出接口可用（{exp['kind']}），取数优先走导出文件。{advice}"

    return envelope(
        shape="table", title=f"页面结构体检：{rep.name}",
        summary=f"{dt} 的页面判定为「{C.STRATEGY_NAMES.get(strategy, strategy)}」，{header_note}{advice}",
        report_id=rep.report_id, report_name=rep.name, report_label=rep.label, report_dt=dt,
        date_source=dinfo["date_source"], notice=dinfo["notice"], url=url, unit="",
        table={"columns": ["检查项", "结果", "说明"], "rows": rows},
        notes=[f"当前地址来源：{BASE_URL_SOURCE}", f"渲染模式：{C.RENDER_MODE}（auto/off/force）"],
    )


# ================================================================ 工具注册（v2.0 精简为 5 个）
def _dim_props() -> dict:
    """按报表声明生成维度参数 schema，参数名带报表前缀避免撞名。

    8670 与 8666 都声明了名为「板块/统计维度」的维度，直接叫 section/dim 会互相覆盖，
    因此统一为 p<报表编号>_<key>，如 p8670_portfolio / p8666_dim。
    """
    props: dict = {}
    for rep in R.ALL:
        for d in rep.dims:
            key = rep.dim_key(d)
            if key in props:
                continue
            vals = [str(v) for v in (d.get("values") or [])]
            desc = f"【{rep.name}】{d.get('desc', '')}"
            if vals:
                desc += f" 取值（{len(vals)} 个）：" + "、".join(vals[:12]) + ("…" if len(vals) > 12 else "")
            props[key] = {"type": "string", "description": desc}
    return props


_DIM_PROPS = _dim_props()


def _split_dims(report: R.Report | None, args: dict) -> tuple[dict, list[str]]:
    """把入参里的维度项按 report 的声明拆出来，返回 ({维度名: 值}, 无法识别的参数名)。"""
    out: dict[str, str] = {}
    unknown: list[str] = []
    reps = [report] if report else R.ALL
    for k, v in list(args.items()):
        if v in (None, ""):
            continue
        hit = None
        for rep in reps:
            hit = rep.dim_by_arg(k)
            if hit:
                break
        if hit:
            out[hit["name"]] = str(v).strip()
        else:
            unknown.append(k)
    return out, unknown

TOOLS = [
    {
        "name": "precious_metals_list_reports",
        "description": ("列出可查的贵金属报表，以及每张报表支持的维度、指标数量与口径简述。"
                        "不确定该查哪张表、或想知道有哪些维度时先调它。"),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "handler": lambda a: list_reports(),
    },
    {
        "name": "precious_metals_query",
        "description": ("【主查询】按指标名（或整句问题）取数，返回「报表/板块或行/指标/数值/数值_num/单位」。"
                        "不传 report 时按问题内容自动路由到最匹配的报表；命中不明确会返回候选清单要求确认，不会乱猜。"
                        "支持维度过滤参数（见各参数说明）与自动识别：问「贵金属租出-企业的损益合计」可直接传 level5，"
                        "也可整句传给 metric 由工具识别。top_n>0 时按指标排序取前 N（排名场景）。"
                        "report_dt 不填默认前一日，返回里的 notice 必须转告用户。"),
        "inputSchema": {"type": "object", "properties": {
            "metric": {"type": "string",
                       "description": "指标名或含维度词的整句，如「损益合计（不含内部账）」「期末余额」「TGLD 的损益合计」。留空则返回该报表全量。"},
            "report": {"type": "string",
                       "description": "报表名或编号，可留空由工具自动路由。可选：" + "、".join(R.labels())},
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；用户未明确指定时不要填。"},
            "top_n": {"type": "integer", "description": "取前 N 名，0 或不填表示全部。"},
            "order": {"type": "string", "description": "desc（默认，高到低）或 asc。"},
            **_DIM_PROPS,
        }, "required": []},
        "handler": lambda a: query(a.pop("report", ""), a.pop("metric", ""), a.pop("report_dt", ""),
                                   int(a.pop("top_n", 0) or 0), a.pop("order", "desc"),
                                   base_url=None, **a),
    },
    {
        "name": "precious_metals_breakdown",
        "description": ("【拆解/排名】把某个指标按本表的行维度拆开（分组合 / 分产品 / 分板块），"
                        "并按数值排序，用于「哪个产品损益最高」「各投资组合分别多少」。已排除合计/小计行。"),
        "inputSchema": {"type": "object", "properties": {
            "metric": {"type": "string", "description": "用于拆解的指标，如「损益合计（不含内部账）」「期末余额」。"},
            "report": {"type": "string", "description": "报表名或编号，可留空自动路由。"},
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；不填默认前一日。"},
            "top_n": {"type": "integer", "description": "取前 N 名，0 表示全部。"},
            "order": {"type": "string", "description": "desc（默认）或 asc。"},
            **_DIM_PROPS,
        }, "required": ["metric"]},
        "handler": lambda a: breakdown(a.pop("report", ""), a.pop("metric", ""), a.pop("report_dt", ""),
                                       int(a.pop("top_n", 0) or 0), a.pop("order", "desc"),
                                       base_url=None, **a),
    },
    {
        "name": "precious_metals_describe",
        "description": ("查口径与取值：某张报表支持哪些维度、每个维度有哪些取值（码值/枚举/品种名）、"
                        "某字段的业务含义（如「含内部账和不含内部账有什么区别」）。"
                        "不传参数返回全部报表总览。"),
        "inputSchema": {"type": "object", "properties": {
            "report": {"type": "string", "description": "报表名或编号；可留空，只传 field 时自动在该维度所属报表里查。"},
            "field": {"type": "string", "description": "维度名或字段/口径关键词，如「投资组合」「产品分类」「含内部账」「损益合计」。"},
        }, "required": []},
        "handler": lambda a: describe(a.get("report", ""), a.get("field", "")),
    },
    {
        "name": "precious_metals_probe",
        "description": ("页面结构体检：取不到数、或换到内网/报表改版后先调它。返回页面是否可抓、"
                        "判定抽取方式、**表头层数与字段命中数**、识别出的列名、导出接口是否可用。"
                        "只做诊断，不返回业务数值。"),
        "inputSchema": {"type": "object", "properties": {
            "report": {"type": "string", "description": "报表名或编号；不填则体检第一张报表。"},
            "report_dt": {"type": "string", "description": "报表日期 YYYY-MM-DD；不填默认前一日。"},
        }, "required": []},
        "handler": lambda a: probe(a.get("report", ""), a.get("report_dt", "")),
    },
]

INSTRUCTIONS = (
    "precious_metals 用于查询贵金属业务报表。共 " + str(len(R.ALL)) + " 张报表："
    + "；".join(f"「{r.label}」（{r.desc[:40]}…）" for r in R.ALL) + "。\n"
    "工具选择：\n"
    "- 不知道该查哪张表 / 有哪些维度 → precious_metals_list_reports\n"
    "- 查某指标数值（可带维度过滤）→ precious_metals_query，report 可留空由工具路由\n"
    "- 谁最高/谁最低/各组合分别多少 → precious_metals_breakdown\n"
    "- 某维度有哪些取值、某口径什么意思 → precious_metals_describe\n"
    "- 取不到数 / 报表改版 / 换内网 → precious_metals_probe\n"
    "硬性规则：\n"
    "1) 日期：用户未明确给出日期时不要自己编，交给工具默认取前一日；返回里的 notice 与 report_dt "
    "必须在回复里明确告知用户（例如：本次数据日期为前一日 2026-10-19）。\n"
    "2) 数值一律以工具返回为准，计算优先用「数值_num」列；单位见 unit。\n"
    "3) **数据来源必须交代清楚**：每次给出数值都要说明出自哪张报表，"
    "用返回里的 report_label（形如「贵金属业务分析-贵金属业务」「贵金属业务分析-整体表格」）。"
    "列表类回答可放在表格首列或小标题，单值回答用「据 贵金属业务分析-贵金属业务」这类前缀。"
    "不要只写一个指标名就报数。\n"
    "4) 工具返回 shape=text 且 title 为「需要确认是哪张报表」时，必须向用户确认是明细还是整体口径，"
    "不得自行选一张作答。\n"
    "5) shape=empty 表示该日期确实无数据，不要推测数值；shape=text 表示参数或取数异常。\n"
    "6) 取不到数据时如实说「报表中没有该数据」或「维度取值不合法」，"
    "不要用别的口径、别的日期的数值替代，也不要自行推算。\n"
    "7) 维度取值必须用 describe 查到的原始写法：投资组合只有 4 位英文码值、没有中文名。\n"
    "8) report_label 是给用户看的标识，要原样输出，不要自行改写成别的名字；"
    "回答里**不要**出现 8670/8666 这类报表编号，业务人员看不懂技术 ID。"
)


# ================================================================ 协议层
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
        args = dict(params.get("arguments") or {})
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
            payload = envelope(shape="text", title="工具执行失败",
                               summary=f"{type(e).__name__}: {e}")
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
    value = (value or "").strip()
    if value and not value.lower().startswith(("http://", "https://", "/")):
        print(f"地址必须以 http:// 或 https:// 开头，或以 / 开头的相对路径：{value}", file=sys.stderr)
        return 2
    cfg = dict(C.CONFIG)
    cfg[key] = value.rstrip("/") if key == "report_base_url" else value
    cfg.setdefault("timeout", C.HTTP_TIMEOUT)
    try:
        with open(C.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        print(f"写入 config.json 失败：{e}", file=sys.stderr)
        return 1
    print(f"已写入 {C.CONFIG_PATH}\n  {key} = {cfg[key] or '(空，表示关闭)'}\n重启 MCP 服务后生效。")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="precious_metals MCP Server（贵金属业务数据查询）")
    ap.add_argument("--transport", default="stdio", choices=["stdio"])
    ap.add_argument("--base-url", default=None, help="临时覆盖报表服务地址（优先级最高，不落盘）")
    ap.add_argument("--set-base-url", default=None, metavar="URL",
                    help="把报表服务地址写入 config.json 后退出")
    ap.add_argument("--set-export-url", default=None, metavar="URL",
                    help="把导出接口地址模板写入 config.json；传空串表示关闭导出路径")
    ap.add_argument("--show-config", action="store_true", help="打印当前生效的地址、来源与配置文件路径")
    ap.add_argument("--tools-json", action="store_true", help="打印工具清单 JSON 后退出")
    ap.add_argument("--selftest", action="store_true", help="离线自检：报表注册、双层表头、路由")
    args = ap.parse_args()

    global DEFAULT_BASE_URL, BASE_URL_SOURCE
    if args.set_base_url:
        sys.exit(save_config_value("report_base_url", args.set_base_url))
    if args.set_export_url is not None:
        sys.exit(save_config_value("export_url", args.set_export_url))
    if args.base_url:
        DEFAULT_BASE_URL, BASE_URL_SOURCE = C.pick_base_url(args.base_url)
        C.DEFAULT_BASE_URL = DEFAULT_BASE_URL
    if args.show_config:
        print(f"生效地址：{DEFAULT_BASE_URL}")
        print(f"来源　　：{BASE_URL_SOURCE}")
        print(f"配置文件：{C.CONFIG_PATH}（{'存在' if os.path.exists(C.CONFIG_PATH) else '不存在'}）")
        print(f"报表编号参数名：{C.REPORT_ID_PARAM}")
        print(f"渲染模式：{C.RENDER_MODE}（auto/off/force）　可用浏览器：{C.find_browser() or '未找到'}")
        print(f"导出接口：{C.EXPORT_URL_TEMPLATE or '未配置（只解析页面 HTML）'}")
        if C.EXPORT_URL_TEMPLATE:
            print(f"　　　　　实际示例：{C.build_export_url(R.ALL[0].report_id, '2026-06-30')}")
        print(f"超时　　：{C.HTTP_TIMEOUT} 秒")
        print(f"已注册报表：{len(R.ALL)} 张")
        for rep in R.ALL:
            print(f"  - {rep.report_id} {rep.name}：{len(rep.fields)} 指标 / "
                  f"{len(rep.dims)} 维度（{'、'.join(d['name'] for d in rep.dims)}）")
        return

    if args.tools_json:
        print(json.dumps({"tools": [{"name": t["name"], "description": t["description"],
                                     "inputSchema": t["inputSchema"]} for t in TOOLS]},
                         ensure_ascii=False, indent=2))
        return
    if args.selftest:
        sys.exit(selftest())
    if not DEFAULT_BASE_URL:
        print(f"[{SERVER_NAME}] 未配置报表服务地址。请任选一种方式配置后重启：",
              file=sys.stderr, flush=True)
        print(f"  1) 编辑 {C.CONFIG_PATH} 里的 report_base_url", file=sys.stderr, flush=True)
        print(f"  2) 运行 python {os.path.basename(__file__)} --set-base-url http://<内网IP>:<端口>",
              file=sys.stderr, flush=True)
        sys.exit(2)
    serve_stdio()


def selftest() -> int:
    """离线自检，不联网。校验：报表注册、URL 参数名、双层表头重建、字段对齐、路由。"""
    ok = fail = 0

    # 本文件源码，供「不得出现单下划线兼容」这类静态断言检查
    with open(os.path.abspath(__file__), encoding="utf-8") as _f:
        _SELF_SRC = _f.read()

    def check(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  ✓ {name}")
        else:
            fail += 1
            print(f"  ✗ {name} {extra}")

    print("[1] 报表注册")
    check("至少注册 2 张报表", len(R.ALL) >= 2, f"实际 {len(R.ALL)}")
    check("每张报表都有 report_id/name/fields", all(
        r.report_id and r.name and r.fields for r in R.ALL))
    check("报表编号不重复", len({r.report_id for r in R.ALL}) == len(R.ALL))
    check("报表模块名与注册表一致", all(
        r.filename if hasattr(r, "filename") else True for r in R.ALL))

    print("[2] URL 参数名")
    u = C.build_url("8670", "2026-10-21")
    check("使用双下划线 __reportId", "__reportId=8670" in u, u)
    check("不含单下划线写法", "_reportId=" not in u.replace("__reportId=", ""), u)
    check("参数名可配置且默认双下划线", C.REPORT_ID_PARAM == "__reportId", C.REPORT_ID_PARAM)
    # 静态检查：源码里不得出现「把单下划线当查询参数读取」的兼容分支。
    # (?<!_) 排除 __reportId 自身的末两位；本条找的是孤立的 "_reportId"。
    check("源码无单下划线兼容分支",
          not re.search(r"""[\.\[]\s*["'](?<!_)_reportId["']""", _SELF_SRC))

    print("[2b] 报表标识 report_label")
    check("每张报表都有 label", all(r.label for r in R.ALL))
    check("label 形如「业务域-报表名」", all(
        r.label.startswith(C.REPORT_LABEL_PREFIX + "-")
        and len(r.label) > len(C.REPORT_LABEL_PREFIX) + 1
        for r in R.ALL), [r.label for r in R.ALL])
    check("label 不含报表编号（业务人员不看技术 ID）",
          all(r.report_id not in r.label for r in R.ALL), [r.label for r in R.ALL])
    check("8670 标识为 贵金属业务分析-贵金属业务",
          R.get("8670") and R.get("8670").label == "贵金属业务分析-贵金属业务",
          R.get("8670").label if R.get("8670") else None)
    check("8666 标识为 贵金属业务分析-整体表格",
          R.get("8666") and R.get("8666").label == "贵金属业务分析-整体表格",
          R.get("8666").label if R.get("8666") else None)
    lr = list_reports()
    check("list_reports 首列为报表标识", lr["table"]["columns"][0].startswith("报表标识"),
          lr["table"]["columns"])
    check("list_reports 每行首格都是标识",
          all(row[0] == rep.label for row, rep in zip(lr["table"]["rows"], R.ALL)))
    check("list_reports 第二列仍保留报表编号，便于排障",
          all(row[1] == rep.report_id for row, rep in zip(lr["table"]["rows"], R.ALL)))
    _e = C.envelope(report_id="8670", report_name="贵金属业务")
    check("envelope 缺省自动补 report_label",
          _e["report_label"] == "贵金属业务分析-贵金属业务", _e["report_label"])

    print("[3] 双层表头重建")
    # 模拟内网 8670：第一层合并单元格组表头 + rowspan 维度列，第二层叶子表头
    raw_hdr = [
        [("投资组合", 1, 2), ("产品分类", 1, 2), ("名义本金折人民币", 1, 2),
         ("业务损益折人民币", 2, 1), ("公允价值变动折人民币", 2, 1),
         ("交易笔数", 1, 2)],
        [("含内部账", 1, 1), ("不含内部账", 1, 1), ("含内部账", 1, 1), ("不含内部账", 1, 1)],
    ]
    fields_full = ["投资组合", "产品分类", "名义本金折人民币",
                   "业务损益折人民币（含内部账）", "业务损益折人民币（不含内部账）",
                   "公允价值变动折人民币（含内部账）", "公允价值变动折人民币（不含内部账）",
                   "交易笔数"]
    hi = C.build_header(raw_hdr, fields_full)
    check("识别为双层表头", hi["bands"] == 2, hi["bands"])
    # colspan=2 的组表头覆盖 2 列，展开后总宽 = 3 维度列 + 2 + 2 + 1 = 8 列
    check("列数正确（8 列，合并单元格按列展开）", hi["width"] == 8, hi["columns"])
    check("维度列原样保留", hi["columns"][:3] == ["投资组合", "产品分类", "名义本金折人民币"],
          hi["columns"][:3])
    check("组表头与叶子表头拼接", hi["columns"][3] == "业务损益折人民币含内部账", hi["columns"][3])
    check("第二层叶子列正确", hi["columns"][4] == "业务损益折人民币不含内部账", hi["columns"][4])
    check("跨两层的单元格不重复拼接", hi["columns"][7] == "交易笔数", hi["columns"][7])
    al = C.align_to_fields(hi["columns"], fields_full)
    check("字段对齐全部命中（8/8）", al["hit"] == 8, al["hit"])

    print("[4] 单层表头不误判")
    single = [[("投资组合", 1, 1), ("产品分类", 1, 1), ("期末余额", 1, 1), ("营业净收入", 1, 1)]]
    hi2 = C.build_header(single, ["投资组合", "产品分类", "期末余额", "营业净收入"])
    check("单层表头 bands=1", hi2["bands"] == 1, hi2["bands"])
    check("单层列名正确", hi2["columns"] == ["投资组合", "产品分类", "期末余额", "营业净收入"],
          hi2["columns"])

    print("[4b] 完整 HTML 端到端（含数据行、rowspan 维度列）")
    html = (
        "<table>"
        "<tr><th rowspan=2>投资组合</th><th rowspan=2>产品分类</th><th rowspan=2>五级产品</th>"
        "<th rowspan=2>名义本金折人民币</th>"
        "<th colspan=2>业务损益折人民币</th><th colspan=2>公允价值变动折人民币</th>"
        "<th rowspan=2>交易笔数</th><th rowspan=2>平均持有期限</th></tr>"
        "<tr><th>含内部账</th><th>不含内部账</th><th>含内部账</th><th>不含内部账</th></tr>"
        "<tr><td>TGLD</td><td>贵金属即远期</td><td>Au99.99现货实盘</td><td>1,000.00</td>"
        "<td>12.50</td><td>10.00</td><td>3.20</td><td>2.80</td><td>15</td><td>7.5</td></tr>"
        "</table>")
    rep = R.get("8670")
    parsed, _ = C.parse_matrix_report(rep, [C.raw_grid(html)[0]])
    check("数据行被排除在表头之外", parsed["columns"][0] == "投资组合", parsed["columns"][0])
    check("列数 = 10", len(parsed["columns"]) == 10, parsed["columns"])
    check("4 个含/不含内部账列全部带组名",
          parsed["columns"][4:8] == ["业务损益折人民币含内部账", "业务损益折人民币不含内部账",
                                     "公允价值变动折人民币含内部账", "公允价值变动折人民币不含内部账"],
          parsed["columns"][4:8])
    check("全部 10 列对齐到标准字段名", len(parsed["fieldMap"]) == 10, parsed["fieldMap"])
    check("数据行解析正确（1 行 10 列）",
          len(parsed["rows"]) == 1 and len(parsed["rows"][0]) == 10, parsed["rows"])

    print("[4c] 空白占位行 + 单层完整列名（8670 真实结构）")
    # 第一行是整表合并的空白单元格（无文字），第二行一列一个完整列名。
    # 期望：占位行不计入表头层，bands=1，列名原样保留，不被拼成空串。
    _PH_HTML = ("<table>"
                '<tr><th colspan="4" class="blank"></th></tr>'
                "<tr><th>投资组合</th><th>名义本金折人民币</th>"
                "<th>新发生-SELL交易本金折人民币</th><th>交易笔数</th></tr>"
                "<tr><td>A</td><td>1</td><td>2</td><td>3</td></tr>"
                "</table>")
    _ph_flds = ["投资组合", "名义本金折人民币", "新发生-SELL交易本金折人民币", "交易笔数"]
    _ph = C.raw_grid(_PH_HTML)[0]
    _ph_hi = C.build_header(_ph, _ph_flds)
    check("空白占位行不计入表头层（bands=1）", _ph_hi["bands"] == 1, _ph_hi["bands"])
    check("列名完整不被空格占位行污染",
          _ph_hi["columns"] == _ph_flds, _ph_hi["columns"])
    _ph_al = C.align_to_fields(_ph_hi["columns"], _ph_flds, _ph_hi.get("pairs"))
    check("4 列全部精确对齐", _ph_al["hit"] == 4, _ph_al["map"])
    _ph_rep = R.get("8670")
    _ph_parsed, _ = C.parse_matrix_report(_ph_rep, [C.raw_grid(_PH_HTML)[0]])
    check("占位行不作为数据行混入",
          len(_ph_parsed["rows"]) == 1 and _ph_parsed["rows"][0][0] == "A",
          _ph_parsed["rows"])

    print("[4d] 合并标题行（写报表名）不污染列名")
    # 第一行是 colspan=全表宽 的标题行，内容为报表名称；它不属于列名体系，
    # 若被并入表头会让每列都挂上同一段前缀，必须被排除。
    _TT_HTML = ("<table>"
                '<tr><th colspan="4">贵金属业务（分产品明细）</th></tr>'
                "<tr><th>投资组合</th><th>名义本金折人民币</th>"
                "<th>业务损益折人民币（含内部账）</th><th>交易笔数</th></tr>"
                "<tr><td>A</td><td>1</td><td>2</td><td>3</td></tr>"
                "</table>")
    _tt_flds = ["投资组合", "名义本金折人民币", "业务损益折人民币（含内部账）", "交易笔数"]
    _tt_hi = C.build_header(C.raw_grid(_TT_HTML)[0], _tt_flds)
    check("标题行不计入表头层（bands=1）", _tt_hi["bands"] == 1, _tt_hi["bands"])
    check("列名不带报表名前缀",
          _tt_hi["columns"] == _tt_flds, _tt_hi["columns"])
    _tt_parsed, _ = C.parse_matrix_report(R.get("8670"), [C.raw_grid(_TT_HTML)[0]])
    check("标题行不混入数据行",
          len(_tt_parsed["rows"]) == 1 and _tt_parsed["rows"][0][0] == "A",
          _tt_parsed["rows"])

    print("[4e] 指标单位不被金额单位覆盖")
    # 笔数 / 期限不是金额，沿用报表的「元」会被误读成金额
    _u1 = _metric_unit("交易笔数", "965", "元")
    check("交易笔数单位为「笔」", _u1[0] == "笔" and _u1[1] == 0, _u1)
    _u2 = _metric_unit("平均持有期限", "455.90", "元")
    check("平均持有期限单位为「天」", _u2[0] == "天", _u2)
    _u3 = _metric_unit("损益合计（不含内部账）", "41,894,100.00", "元")
    check("金额类指标仍用报表单位", _u3[0] == "元", _u3)
    _u4 = _metric_unit("期末余额占比", "28.77%", "元")
    check("百分比优先于指标单位", _u4[0] == "%", _u4)

    print("[4f] 合计行识别（分区表的合计在行键末尾）")
    # 8666 是分区表，合计行的行键形如「贵金属业务损益情况/合计」，
    # 合计在末尾段而非开头，只判 startswith("合计") 会漏掉。
    _rk = ["合计//", "贵金属业务损益情况/合计", "TGLD 小计//",
           "TGLD/贵金属即远期/Au99.99现货实盘", "贵金属业务规模情况/统计维度 TGLD"]

    def _is_rollup(key: str) -> bool:
        segs = [x.strip() for x in str(key).split("/") if x.strip()]
        return any(any(k in s for k in ("小计", "总计", "合计")) for s in segs)

    check("开头的「合计//」被识别为汇总行", _is_rollup(_rk[0]))
    check("末尾的「…/合计」被识别为汇总行", _is_rollup(_rk[1]))
    check("「TGLD 小计//」被识别为汇总行", _is_rollup(_rk[2]))
    check("普通明细行不被误判", not _is_rollup(_rk[3]))
    check("普通维度行不被误判", not _is_rollup(_rk[4]))

    print("[4g] 「整个部门」类说法 = 全量口径（不是非法取值）")
    # 贵金属报表没有部门/中心维度，问「金融市场部」时应取全量而不是报「取值不合法」
    for _w in ("金融市场部", "金融市场部门", "中信银行金融市场", "资金业务部",
               "全部门", "整个部门", "分行金融市场部", "咱们部门"):
        check(f"「{_w}」识别为部门口径", _is_dept_word(_w))
    # 反向：具体维度取值/中心词绝不能被当成部门，否则会误吞成全量
    for _w in ("TGLD A", "合计", "贵金属即远期", "资金管理中心", "投资管理中心",
               "做市交易中心", "外币资产管理中心", "客户中心", "贵金属租出"):
        check(f"「{_w}」不被误判为部门口径", not _is_dept_word(_w))

    print("[5] 路由")
    rt = route("各投资组合的名义本金是多少")
    check("明细类问题路由到明细表", rt["report"] is not None and "明细" in rt["report"].name,
          rt["report"])
    rt2 = route("整体损益合计多少")
    check("整体类问题路由到整体表", rt2["report"] is not None and "整体" in rt2["report"].name,
          rt2["report"])
    rt3 = route("")
    check("空问题返回候选而非乱猜", rt3["report"] is None and rt3["ambiguous"])

    print("[5c] 五级产品层：即期/远期/掉期存在，延期是分散档位")
    _l5 = []
    for _r in R.ALL:
        _l5 += next((d.get("values") or [] for d in _r.dims
                     if d.get("name") == "五级产品"), [])
    if _l5:
        for _n in ("贵金属即期", "贵金属远期", "贵金属掉期"):
            check(f"五级产品含「{_n}」", _n in _l5)
        _delay = [v for v in _l5 if "延期" in v]
        check("「延期」是多个分散档位而非单一项", len(_delay) >= 2, _delay[:3])
        check("库里是「贵金属掉期」不是「贵金属调期」",
              "贵金属掉期" in _l5 and "贵金属调期" not in _l5)

    print("[5d] 「档位合法但本期无数据」与「指标不存在」要分开报")
    _detail = next((r for r in R.ALL if "名义本金折人民币" in r.fields), None)
    if _detail and _l5:
        # 取一个合法但大概率无数据的档位；若无此情况则跳过（不写死数据）
        _rare = next((v for v in _l5
                      if v in ("贵金属即期", "Au(T+D)延期交收")), None)
        if _rare:
            _rr = query(report=_detail.report_id, metric="名义本金折人民币",
                        **{_detail.dim_key(next(d for d in _detail.dims
                                                if d["name"] == "五级产品")): _rare})
            check("合法档位无数据时提示「本期无数据」而非「不支持」",
                  "本期无数据" in str(_rr.get("title", "")), _rr.get("title"))
        _rr2 = query(report=_detail.report_id, metric="绝不存在的指标ZZZ")
        check("真不存在的指标仍报「未命中」",
              "未命中" in str(_rr2.get("title", "")), _rr2.get("title"))

    print("[5b] 指标归属优先：「投资组合的营业净收入」必须落到整体表")
    # 「投资组合」是 8670 的维度，「营业净收入」却是 8666 的独占指标（8670 没有）。
    # 纯词频打分会判给 8670，然后报「未命中」——这是真实踩过的坑。
    # 正解：8666 的「统计维度」同样列了那些投资组合码值，按组合拆分收入本就查得到。
    # 判据用「该报表字段里到底有没有这个指标」动态判定，不写死报表号。
    _owner_rep = next((r for r in R.ALL
                       if "营业净收入" in r.fields and "投资组合" not in r.dims_names), None) \
        if hasattr(R.ALL[0], "dims_names") else None
    _rep_income = next((r for r in R.ALL if "营业净收入" in r.fields), None)
    _rep_portfolio = next((r for r in R.ALL if any("投资组合" in (d.get("name") or "")
                                                   for d in r.dims)), None)
    if _rep_income and _rep_portfolio and _rep_income.report_id != _rep_portfolio.report_id:
        for _q in ("各投资组合的营业净收入分别是多少？",
                   "各投资组合的营业净收入分别是多少",
                   "哪个投资组合贡献了最多的营业净收入？"):
            _rt = route(_q)
            check(f"「{_q[:16]}…」路由到有该指标的表",
                  _rt["report"] is not None
                  and _rt["report"].report_id == _rep_income.report_id,
                  _rt["report"])
        check("该报表确实没有这个指标时不会硬凑",
              "营业净收入" not in _rep_portfolio.fields)
    # 反向：指标只在 8670 的（如交易本金），不能因为句中有「营业净收入」的近义词被带走
    _rep_principal = next((r for r in R.ALL
                           if any("交易本金" in f for f in r.fields)), None)
    if _rep_principal:
        _rt = route("贵金属即期的BUY交易本金是多少")
        check("交易本金类问题仍留在明细表",
              _rt["report"] is not None
              and _rt["report"].report_id == _rep_principal.report_id,
              _rt["report"])

    print("[6] 工具集")
    names = [t["name"] for t in TOOLS]
    check("工具数 = 5", len(names) == 5, names)
    check("不含已删除的 rank/compare/query_detail/query_overview/query_metric",
          not any(n for n in names if any(k in n for k in
                                          ("rank", "compare", "query_detail", "query_overview",
                                           "query_metric"))), names)

    print("[7] 双层表头结构对齐（不依赖 mock 服务）")
    # 用一张最小双层表头 HTML 端到端验证：组表头 colspan + 维度列 rowspan，
    # 且故意让「组名带前缀」与「裸叶子」两列同名，考验归属裁决。
    _CHECK_HTML = """<table>
      <tr><th rowspan="2">组合</th><th colspan="2">业务损益折人民币</th>
          <th colspan="2">新发生</th><th rowspan="2">交易笔数</th></tr>
      <tr><th>含内部账</th><th>不含内部账</th><th>SELL交易本金</th><th>BUY交易本金</th></tr>
      <tr><td>A</td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr>
    </table>"""
    _flds = ["组合", "业务损益折人民币（含内部账）", "业务损益折人民币（不含内部账）",
             "新发生-SELL交易本金", "新发生-BUY交易本金", "交易笔数"]
    _ext = C.extract_tables(_CHECK_HTML)
    _hi = C.build_header(_ext["raw_tables"][0], _flds)
    check("双层表头识别为 2 层", _hi["bands"] == 2, _hi["bands"])
    check("表宽 = 6（合并单元格按列展开）", _hi["width"] == 6, _hi["width"])
    _al = C.align_to_fields(_hi["columns"], _flds, _hi["pairs"])
    check("双层表头 6 列全部对齐", _al["hit"] == 6, _hi["columns"])
    check("组名+叶子拼出含内部账两列",
          "业务损益折人民币（含内部账）" in _al["map"].values()
          and "业务损益折人民币（不含内部账）" in _al["map"].values(),
          _hi["columns"])
    check("带组名的列不被裸叶子抢走",
          _al["map"].get(3) == "新发生-SELL交易本金"
          and _al["map"].get(4) == "新发生-BUY交易本金", _al["map"])
    check("rowspan 维度列不重复拼接", _hi["columns"][0] == "组合", _hi["columns"])

    print(f"\n结果：{ok} 通过，{fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    main()
