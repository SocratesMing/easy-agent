"""金融市场部经营分析 MCP 工具（baop 独立只读库，三张表，只读）。

挂载路径：``/mcp/baop/``

文件结构与其它业务包一致：领域层 :mod:`baop` 放表字典、单位折算、ECharts
option、返回信封、期次解析、码值标准化与全部业务逻辑；本文件只做 MCP 工具
注册（async 薄包装 + 统一异常兜底）。

**数据源是独立的 baop 库**，不走本服务统一的 querykit 通道，连接配置见
:func:`baop.cfg`（``BAOP_HOST`` / ``BAOP_PORT`` / ``BAOP_USER`` /
``BAOP_PASSWORD`` / ``BAOP_DB``，均有默认值、函数式读取，改环境变量后下次查询
即生效，无需重启进程）。

并发：MCP SDK 对同步工具函数是直接调用、不丢线程池，而 pymysql 是阻塞调用，
因此工具都是 ``async def`` + ``anyio.to_thread.run_sync``。
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from ...context import current_username
from ...querykit import json_safe
from . import baop

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "金融市场部经营分析问答服务（baop 库，只读）。覆盖三块业务："
    "①营业净收入/非息/息差（T3，中心×业务线）；②资本公积变动（T1，按中心）；③负债规模（T2）。"
    "三张源表底层单位均为「万元」，本服务统一折算并输出「亿元」，请勿自行换算。"
    "源数据为日频（每日一期）：未指定期次时自动取最新期次并在 summary 首句回显；"
    "所问日期无数据时会回落到最近可用期次并在 notes 中声明。"
    "维度参数可用口语（如「投资中心」「外币」），服务内部会做码值标准化并在 resolved 中回显映射。"
    "先调用 describe_baop_schema 可拿到表、维度、指标与全部口径说明。"
    "返回值 result_state ∈ OK/ZERO/NULL/NOT_FOUND/AMBIGUOUS："
    "candidates 非空表示需要向用户澄清，此时 table 为空、不要编造数据；"
    "ZERO 表示真实为零（如外币司库负债已退出），NULL 表示无数据，两者不可混为一谈。"
    "\n【问题 → 工具速查】"
    "整体收入/各中心对比/非息占比 → get_income_overview；"
    "排名/最高最低/谁贡献最多/亏损筛查/完成率/序时进度/当周 → rank_income；"
    "单中心或单业务线明细/由哪些业务线构成 → get_income_detail；"
    "资本公积（含浮亏/负数）→ get_capital_reserve；"
    "负债规模与构成/同业·本币·外币司库/较年初·较上月末变化 → get_liability_overview；"
    "负债产品排名/哪类规模最大·成本率最高 → rank_liability_products；"
    "走势·趋势·最近几期·多期对比 → compare_periods；"
    "有哪些中心/业务线/维度、数据截止到什么时候、单位是什么、是否每天更新 → list_dimensions。"
    "\n【必须先取码值】用户用简称/别名/口语（「资金中心」「外汇」「同业」「外币」「贵金属」）时，"
    "先调 list_dimensions 对齐标准码值再传参；拿不准就把候选项列给用户确认，不要猜。"
    "\n【能力边界（超范围一律如实说明，不要编造）】"
    "库中只有上述三张表，**没有**净利润率、不良率/不良贷款率、客户数、人均产能、存款付息率等指标；"
    "不做预测/外推（如「年底能到多少」），不做写操作（改计划、加数据一律拒绝）。"
    "\n【派生指标】占比、增速、计划缺口、跨指标比对等派生值，优先对语义化工具的返回结果自行计算并注明；"
    "仅在取不到原始数据时才使用 run_readonly_sql。"
)


def _safe(fn):
    """统一异常兜底 + 丢线程池执行。

    必须用 ``functools.wraps`` 保留签名——MCP 靠 ``inspect.signature`` 生成工具
    JSON Schema，签名退化成 ``(*args, **kwargs)`` 会被宿主参数校验拒绝。
    异常时返回与正常信封同构的错误信封，调用方无需分辨两种失败。
    """
    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await anyio.to_thread.run_sync(functools.partial(fn, *a, **kw))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[baop] {fn.__name__} 执行异常: {type(e).__name__}: {e}")
            return baop.env(
                "查询失败", f"工具执行异常：{type(e).__name__}: {e}", "—",
                table=None, result_state="NOT_FOUND",
            )
    return wrapper


def build() -> FastMCP:
    mcp = FastMCP(
        "金融市场部经营分析",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    @_safe
    def describe_baop_schema() -> dict[str, Any]:
        """Return the data dictionary for baop: the three tables, dimensions, metrics,
        basis columns, units and the pitfalls (zero vs null, percentage-point basis
        columns, precomputed comparison columns). Call this before any query.
        """
        username = current_username()
        logger.info(f"[baop] describe_baop_schema | 用户: {username}")
        return {"ok": True, **baop.schema_help()}

    # ── 元数据与维度 ────────────────────────────────────────────────────

    @mcp.tool()
    @_safe
    def list_dimensions(dimension: str = "all") -> dict[str, Any]:
        """列出可查询的范围与数据字典：中心 / 业务线 / 负债大类 / 业务类别 / 币种 /
        可用期次 / 指标 / 元数据。当用户问「有哪些中心」「有哪些业务线 / 维度」
        「数据截止到什么时候」「最新期次是几号」「单位是什么」「有哪些数据表」
        「数据是每天更新吗」时使用。dimension 可选 all / center / biz_line /
        lib_cls / prod / cur / period / metric / meta。
        """
        return baop.list_dimensions(dimension)

    # ── 收入（T3）──────────────────────────────────────────────────────

    @mcp.tool()
    @_safe
    def get_income_overview(period: str | None = None,
                            top_n: int = 0) -> dict[str, Any]:
        """收入总览：各中心营业净收入 / 非息收入 / 息差对比（T3）。period 留空取最新
        期次；top_n > 0 时只展示前 N 个中心（统计口径仍是全量）。用于「整体收入怎么样」
        「各中心对比」「非息占比」类问题。
        """
        return baop.get_income_overview(period, top_n)

    @mcp.tool()
    @_safe
    def rank_income(metric: str = "net_opr_incom", by: str = "core",
                    period: str | None = None, top_n: int = 10,
                    order: str = "desc") -> dict[str, Any]:
        """收入排名：按中心或业务线对某个指标排序（T3）。metric 可选
        net_opr_incom / not_int_incom / net_int / plan / comp_rate / tm_prog /
        curr_week；by 可选 core / biz_line；order 可选 desc / asc。用于「排名」
        「最高最低」「谁贡献最多」「亏损筛查」「完成率」「序时进度」类问题。
        """
        return baop.rank_income(metric, by, period, top_n, order)

    @mcp.tool()
    @_safe
    def get_income_detail(core: str | None = None, biz_line: str | None = None,
                          period: str | None = None,
                          metric: str | None = None) -> dict[str, Any]:
        """收入明细：锁定某个中心或业务线后的逐行指标（T3）。core / biz_line 都留空时
        退化为按中心汇总。用于「某中心/某业务线怎么样」「由哪些业务线构成」类问题。
        """
        return baop.get_income_detail(core, biz_line, period, metric)

    # ── 资本公积（T1）──────────────────────────────────────────────────

    @mcp.tool()
    @_safe
    def get_capital_reserve(core: str | None = None, period: str | None = None,
                            basis: str = "none") -> dict[str, Any]:
        """资本公积：各中心人民币 / 外币 / 可供出售账户余额（T1）。basis 可选
        none / last_day / last_fri / last_mth / last_quar / last_year_dt / yr_bgn，
        读取源表**预计算**的对比列。资本公积可为负值（浮亏），属正常业务现象。
        """
        return baop.get_capital_reserve(core, period, basis)

    # ── 负债（T2）──────────────────────────────────────────────────────

    @mcp.tool()
    @_safe
    def get_liability_overview(lib_cls: str = "合计", cur: str = "",
                               period: str | None = None,
                               basis: str = "none") -> dict[str, Any]:
        """负债总览：负债合计构成，或某个负债大类下的业务类别明细（T2）。lib_cls 留空
        或「合计」返回构成（同业负债 / 本币司库 / 外币司库）；cur 可选 01 / 02 /
        合计。⚠️ 余额为 0 是**真实业务事实**（ZERO 态），不是无数据，切勿表述为
        「无数据」或「减少 0 亿」。basis 见 get_capital_reserve 的说明。
        """
        return baop.get_liability_overview(lib_cls, cur, period, basis)

    @mcp.tool()
    @_safe
    def rank_liability_products(lib_cls: str = "同业负债", metric: str = "acct_bal",
                                period: str | None = None, top_n: int = 10,
                                order: str = "desc") -> dict[str, Any]:
        """负债产品排名：某个负债大类下各业务类别的规模 / 占比 / 成本率排序（T2）。
        metric 可选 acct_bal / bal_per / avg_bal / avg_bal_per / cost_rate。
        「合计」行已排除在明细之外。
        """
        return baop.rank_liability_products(lib_cls, metric, period, top_n, order)

    # ── 跨表趋势 ───────────────────────────────────────────────────────

    @mcp.tool()
    @_safe
    def compare_periods(object: str = "income", keys: list[str] | None = None,
                        n_periods: int = 3,
                        period_to: str | None = None) -> dict[str, Any]:
        """多期对比：跨期趋势（收入 / 负债 / 资本公积三选一）。object 可选 income /
        liability / capital；keys 限定要对比的对象（中心或负债大类）；n_periods
        取 2~12 期，period_to 为结束期次（留空取最新）。用于「走势」「趋势」
        「最近几期」类问题。
        """
        return baop.compare_periods(object, keys, n_periods, period_to)

    # ── 只读 SQL 兜底 ──────────────────────────────────────────────────

    @mcp.tool()
    @_safe
    def run_readonly_sql(sql: str, max_rows: int = 200) -> dict[str, Any]:
        """执行一条自定义只读 SELECT（7 条护栏：单条语句、表名白名单、强制 LIMIT、
        5 秒超时、行数上限、空结果是 NOT_FOUND 而非 0、单位未知不折算）。仅在语义化
        工具取不到原始数据时使用；⚠️ 源表底层是「万元」，结果不做单位折算。
        """
        return baop.run_readonly_sql(sql, max_rows)

    return mcp
