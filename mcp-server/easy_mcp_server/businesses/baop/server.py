"""金融市场部经营分析 MCP 工具（baop 独立只读库，三张表，只读）。

挂载路径：``/mcp/baop/``

文件结构与其它业务包一致：领域层 :mod:`baop` 放表字典、单位折算、ECharts
option、返回信封、期次解析、码值标准化与全部业务逻辑；本文件只做 MCP 工具
注册（async 薄包装 + 统一异常兜底）。

数据源是独立的 baop 库，连接配置见 :func:`baop.CFG`（已写死部署连接，不依赖
环境变量）。

并发：MCP SDK 对同步工具函数是直接调用、不丢线程池，而 pymysql 是阻塞调用，
因此工具都是 ``async def`` + ``anyio.to_thread.run_sync``。
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from . import baop

logger = logging.getLogger("easy-mcp-server")

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
    "整体收入/各中心对比/非息占比 → get_income_overview；"
    "排名/最高最低/谁贡献最多/亏损筛查 → rank_income（可传 core 限定在某个中心内排名）；"
    "单中心或单业务线的完整画像/由哪些业务线构成 → get_income_detail"
    "（带「营业缺口」「非息缺口」列）；"
    "计划完成率/完成得好不好/进度是否落后/非息·息差完成率/缺口 → get_plan_completion"
    "（三口径加权完成率 + 缺口 + 日历进度，一次给全，优先用它而不是拿两个工具相除）；"
    "各中心×各业务线交叉/某业务线在各中心怎么分布 → get_income_matrix；"
    "这周挣了多少/当周收入/某条业务线当周多少/比上期多挣多少 → get_income_flow；"
    "资本公积（含浮亏/负数/增速）→ get_capital_reserve（growth=True 出增速列）；"
    "负债规模与构成/同业·本币·外币司库/较年初·较上月末变化 → get_liability_overview；"
    "负债逐条明细（币种/产品编码/增速）→ get_liability_detail；"
    "负债产品排名/哪类规模最大·成本率最高 → rank_liability_products；"
    "走势·趋势·最近几期·多期对比 → compare_periods；"
    "有哪些中心/业务线/维度、数据截止到什么时候、单位是什么、是否每天更新 → list_dimensions。"
    "\n【必须先取码值】用户用简称/别名/口语（「资金中心」「外汇」「同业」「外币」「贵金属」）时，"
    "先调 list_dimensions 对齐标准码值再传参；拿不准就把候选项列给用户确认，不要猜。"
    "\n【能力边界（超范围一律如实说明，不要编造）】"
    "库中只有上述三张表，**没有**净利润率、不良率/不良贷款率、客户数、人均产能、存款付息率等指标；"
    "不做预测/外推（如「年底能到多少」），不做写操作（改计划、加数据一律拒绝）。"
    "\n【派生指标（口径已内置工具，勿自行重算）】"
    "完成率 = Σ实际÷Σ计划（加权，禁止对行内比率求平均）→ get_plan_completion；"
    "缺口 = 计划或分解目标 − 实际累计 → 同上，或 get_income_detail 的「营业缺口」「非息缺口」；"
    "期间发生额 = 本期累计 − 上一期累计 → get_income_flow；"
    "增速 = 源表预计算列 → get_capital_reserve(growth=True) / get_liability_detail；"
    "非息占营业比 → get_income_overview 已给出。"
    "\n【无自由 SQL】本服务不提供任意 SQL 查询。若上述工具都覆盖不了某个取数需求，"
    "如实说明「当前工具覆盖不到」，不要编造数据，也不要尝试拼 SQL。"
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

    @mcp.tool()
    @_safe
    def list_dimensions(dimension: str = "all") -> dict[str, Any]:
        """列出本数据服务可查询的范围与数据字典：中心 / 业务线 / 负债大类 / 业务类别 / 币种 / 可用日期 / 指标 / 元数据。
        适合「有哪些中心」「有哪些业务线」「有哪些维度」「数据截止到什么时候」「最新期次是几号」「能查哪几天」
        「单位是什么」「有哪些数据表」「数据是每天更新吗」这类**范围与字典类**问题。
        返回的「最新数据日期（DATA_DT）」与「T3 可查业务日期（BIZ_DT）」是两个概念：前者是跑批快照日，
        后者才是 period 参数能传的值。
        也用于**传参前先取标准码值**：用户说的是简称/别名/口语（如「资金中心」「外汇」「同业」「外币」）时，
        先调本工具拿到标准写法，再传给其他工具。
        dimension 可选 all(默认)/center/biz_line/lib_cls/prod/cur/period/metric/meta。"""
        return baop.list_dimensions(dimension)

    @mcp.tool()
    @_safe
    def get_income_overview(period: str = "", top_n: int = 0) -> dict[str, Any]:
        """各中心收入总览：营业净收入 / 非息收入 / 息差，并给出「非息占营业比」，返回分组柱状图。
        适合「收入整体怎么样」「全部门收入多少」「各中心收入对比」「谁强谁弱」「收入结构如何」
        「非息占比多少」「息差什么水平」这类总览问题（一次拿全全部中心，含合计行）。
        period 传**业务日期（BIZ_DT）**，如 2026-09-30；不传则取最新数据日期下的最大业务日期。
        （数据日期 DATA_DT 由工具固定取最新快照，不需要也不应该传。）
        top_n 只控制展示行数（默认 0 = 全部中心），统计口径与合计行始终是全量。
        ⚠️ 要严格排序 / 单项排名 / 最高最低 / 亏损筛查时请改用 rank_income，本工具不做排序结论。"""
        return baop.get_income_overview(period or None, top_n or 0)

    @mcp.tool()
    @_safe
    def rank_income(metric: str = "net_opr_incom", by: str = "core",
                         period: str = "", top_n: int = 10, order: str = "desc",
                         core: str = "") -> dict[str, Any]:
        """收入类指标排名：各中心、各业务线谁最高、谁最低、有没有亏损，返回横向条形图。
        摘要给出「最高 / 次高 + 领先差距 / 末位 / 负值个数」。
        适合「谁贡献最多」「哪个中心最高」「哪个业务线收入最高」「排个序看看」「领先第二名多少」
        「有没有中心是亏损的」「各中心收入占比 / 结构贡献」。
        metric 可选（注意口径不同，勿混用）：
          net_opr_incom(营业净收入，默认) / not_int_incom(非息收入) / net_int(息差)
          / plan(营业净收入-经营计划) / comp_rate(营业净收入-计划完成率)
          / tm_prog(息差-序时进度) / curr_week(营业净收入-当周)
        by 可选 core(按中心，默认) / biz_line(按业务线)；order 为 desc(高→低) / asc(低→高)。
        core 可选：限定某个中心内部排名（如「投资管理中心里哪条业务线收入最高」→ by=biz_line, core=投资管理中心）；
        也可传部门名「金融市场部」（= 全部中心，等价于不传）；不传则全部门范围排名。
        period 传业务日期（BIZ_DT）；不传则取最新数据日期下的最大业务日期（DATA_DT 恒取最新）。
        ⚠️ 缺口类问题（「离全年计划还差多少」「完成率是否低于序时进度」）需要两个指标相减/相比，
           本工具一次只返回一个 metric：请分别取 plan 与 net_opr_incom（或 comp_rate 与 tm_prog）后自行计算；
           已知中心的**绝对缺口**可直接用 get_income_detail 的「营业缺口」列。
        ⚠️ 「利率相关 / 跨境类 / 外币类」等**同类业务线聚合**：先 by=biz_line、top_n=0 取全量清单，再按名称归并。
        ⚠️ top_n 只影响展示行数，统计口径始终是全量（摘要中同时给出「共 N 个」与展示条数）。
        ⚠️ 要看「中心 × 业务线」的交叉结构 → get_income_matrix；
           要完成率 / 缺口的完整口径（含日历进度）→ get_plan_completion；本工具只负责「排序与极值」。"""
        return baop.rank_income(metric, by, period or None, top_n or 0, order, core or None)

    @mcp.tool()
    @_safe
    def get_income_detail(core: str = "", biz_line: str = "", period: str = "") -> dict[str, Any]:
        """查询**单个中心或单条业务线**的收入画像：累计实际、计划、缺口、完成情况，一屏看全（12 列明细）。
        返回列：营业净收入 / 非息 / 息差 / 经营计划 / 营业缺口 / 营业完成率 /
        非息分解目标 / 非息缺口 / 非息完成率 / 非息序时进度。
        适合「XX 中心收入怎么样」「XX 中心现在什么情况」「外汇这块收入有多少」「XX 由哪些业务线构成」
        「XX 靠哪些业务线挣钱」「某条业务线做得怎么样」「哪条业务线缺口最大」。
        「营业缺口」= 经营计划 − 营业净收入累计，「非息缺口」= 非息分解目标 − 非息收入累计，均为正数表示尚未完成。
        所有金额指标都是**截止期末的年初至今累计值**，不是当期发生额。
        core 与 biz_line 可直接传用户原话（如「投资中心」「外汇」「贵金属」），工具会做码值标准化并在 resolved 中回显；
        部门名「金融市场部」也是合法的 core 取值：按全部中心汇总（源数据即金融市场部口径，不含合计行），
        摘要直接给出全口径加权计划完成率（Σ实际÷Σ计划，非各行完成率平均）。
        两者都不传则按中心级汇总（等价于金融市场部口径）。
        ⚠️ core 与 biz_line 是两个不同维度，不要同时传。
        ⚠️ 本工具是「**单个对象的完整画像**」：只问完成率 / 缺口数值 → 优先 get_plan_completion；
           只问谁高谁低 → rank_income；要看中心 × 业务线交叉 → get_income_matrix。
        period 传业务日期（BIZ_DT）；不传则取最新数据日期下的最大业务日期（DATA_DT 恒取最新）。
        ⚠️ 要对比两个对象（如「客户中心 vs 投资管理中心谁贡献更大」）请用 rank_income(by='core') 一次拿全再比。"""
        return baop.get_income_detail(core or None, biz_line or None, period or None)

    @mcp.tool()
    @_safe
    def get_plan_completion(metric: str = "net_opr_incom", by: str = "core",
                                 core: str = "", period: str = "",
                                 top_n: int = 0, order: str = "desc") -> dict[str, Any]:
        """**计划完成率与缺口专用工具**（营业净收入 / 非息 / 息差三口径，口径已统一，优先用它，不要自己拿两个工具相除）。
        一次给出：实际累计 / 计划或分解目标 / 缺口 / 完成率（加权）/ 日历进度 / 完成率−日历进度。
        适合「计划完成率是多少」「完成得怎么样」「离全年计划还差多少」「进度是否落后」
        「哪个中心完成得好」「哪条业务线缺口最大」「非息完成率」「息差完成率」。
        metric 三口径：
          net_opr_incom(营业净收入，默认，计划=经营计划) /
          not_int_incom(非息收入，目标=非息分解目标) /
          net_int(息差，目标=息差分解目标)。
        by 可选 core(按中心，默认) / biz_line(按业务线)；core 可传中心名，
        也可传「金融市场部」（= 全部中心，返回各部门汇总行）。
        ⚠️ 完成率一律 Σ实际÷Σ计划 加权，**不是**各行完成率的平均；
        ⚠️ 营业净收入无源表序时进度列，用日历进度做时间基准；非息/息差另给源表序时进度。
        top_n 只影响展示行数（0 = 全部）；order 为 desc（默认，完成率由高到低）/ asc。
        ⚠️ 要单个对象的**逐项明细**（非息目标、缺口列、业务线构成）→ get_income_detail；
           只要排序或极值 → rank_income。
        period 传业务日期（BIZ_DT）；不传取最新。"""
        return baop.get_plan_completion(metric, by, core or None, period or None,
                                   top_n or 0, order)

    @mcp.tool()
    @_safe
    def get_income_matrix(metric: str = "net_opr_incom", core: str = "",
                               biz_line: str = "", period: str = "") -> dict[str, Any]:
        """**中心 × 业务线交叉矩阵**：一次返回各中心 × 各业务线的完整十字表（5 × 17）。
        适合「各中心各业务线分别多少」「某条业务线在各中心怎么分布」「交叉看一下」
        「哪个中心哪块业务最强」「业务线和中心的结构关系」，以及自定义组合的取数需求。
        metric 可选 net_opr_incom(默认) / not_int_incom / net_int / plan / curr_week / comp_rate。
        core 可限定某个中心（也可传「金融市场部」），biz_line 可限定某条业务线；
        两者都可省略，省略即全量矩阵。行/列合计均为加权重算（比率口径为 Σ实际÷Σ计划）。
        period 传业务日期（BIZ_DT）；不传取最新。"""
        return baop.get_income_matrix(metric, core or None, biz_line or None, period or None)

    @mcp.tool()
    @_safe
    def get_income_flow(metric: str = "net_opr_incom", by: str = "core",
                             core: str = "", period: str = "") -> dict[str, Any]:
        """**当周与期间发生额**（源表只有年初至今累计，本工具负责还原「这段时间挣了多少」）。
        返回：本期累计 / 上期累计 / 期间发生额（两期相减）/ 当周。
        适合「这周挣了多少」「当周收入」「比上期多挣多少」「这一周各中心分别多少」
        「某条业务线这周挣了多少」（如「跨境服务业务当周营业净收入」）「本周息差多少」
        「本周非息多少」「增长动能怎么样」——凡是问**当周/本周/这段时间的发生额**都用它。
        metric 可选 net_opr_incom(默认) / not_int_incom / net_int；
        by 可选 core(默认) / biz_line；core 可传中心名或「金融市场部」。
        ⚠️ 「期间发生额」= 本期累计 − 上一可用期次累计；「当周」是源表 CURR_WEEK 列，两者口径不同。
        ⚠️ 问「累计做到多少」不要用本工具，用 get_income_overview / get_income_detail。
        period 传业务日期（BIZ_DT）；不传取最新。"""
        return baop.get_income_flow(metric, by, core or None, period or None)

    @mcp.tool()
    @_safe
    def get_capital_reserve(core: str = "", period: str = "", basis: str = "none",
                                 growth: bool = False, cur: str = "rmb") -> dict[str, Any]:
        """查询资本公积（可供出售账户）情况，返回柱状图；含人民币 / 外币 / 可供出售账户资产余额。
        适合「资本公积多少」「资本公积合计」「各中心资本公积分别是多少」「谁占用最多」
        「有没有中心的资本公积是负数 / 浮亏」「资本公积较年初 / 比上月末变化多少」。
        core 可传口语（如「资金中心」）；basis 为对比基准（本表全部为金额，单位亿元）：
          none(默认) / yr_bgn(较年初) / last_mth(较上月末) / last_fri(较上周五)
          / last_day(较上日) / last_quar(较上季末) / last_year_dt(较上年同期)。
        cur 选对比基准的币种口径：rmb(人民币，默认) / fx(外币)——
        问「外币资本公积比上月末多了多少」「外币较年初变化」时传 fx。
        ⚠️ 不传 core 时，源表里码值为「合计」的自带小计行会被排除，不与各中心重复加总；
           要总量时传 core='合计'：有源表合计行就用该行，没有则按各中心加总（并在 notes 说明）。
        ⚠️ 本表「较上月末」的键名是 last_mth；last_mth_end 是**负债表**的键名，传到这里会报错。
        period 传数据日期（DATA_DT，本表为单日期表）；不传取最新。
        growth 传 True 时追加源表预计算的增速列（人民币/外币的环比、较年初、同比，单位 %）——
        问「资本公积增速多少」「比去年增长几个点」「环比涨了还是跌了」时打开。"""
        return baop.get_capital_reserve(core or None, period or None, basis, bool(growth), cur)

    @mcp.tool()
    @_safe
    def get_liability_overview(lib_cls: str = "合计", cur: str = "",
                                    period: str = "", basis: str = "none",
                                    group_by: str = "detail") -> dict[str, Any]:
        """查询负债规模与构成，返回饼图 + 明细表（余额 / 余额占比 / 年均余额 / 成本率），末行给出**合计**。
        适合「负债规模多大」「负债主要由哪几块构成」「各自占比多少」「同业负债规模多少」
        「本币司库 / 外币司库什么规模」「哪个规模更大」「全部负债平均成本率是多少」
        「较年初 / 比上月末 / 较上周五变化多少」「成本率较年初是升还是降」
        「年均余额口径与期末余额差多少」「本币和外币各占多少」。
        lib_cls 可传「合计 / 同业 / 外币 / 本币 / 司库」等口语（缺省「合计」= 返回三大构成）；
        cur 不传即为全部币种，也可传 01(本币)/02(外币)；
        group_by 可选 detail(明细，默认) / cur(按币种汇总)——问「本币外币各多少」用 cur；
        basis 可选 none(默认)/yr_bgn/last_mth/last_mth_end/last_fri/avg_yr_bgn
          /avg_last_mth/avg_last_mth_end/avg_last_fri/cost_yr_bgn/cost_last_mth
          /cost_last_mth_end/cost_last_fri。
        period 传**业务日期（DATA_PRDC_DT）**；不传则取最新数据日期下的最大业务日期（DATA_DT 恒取最新）。
        ⚠️ 末行「合计」：有源表汇总行时直接读取（成本率为源值），否则按可见行聚合、
           成本率按日均余额加权估算，摘要会注明来源。
        ⚠️ cost_* 类基准（成本率对比）的单位是**百分点**，其余基准是**亿元**；工具已按此标注列名，引用时勿写成「亿元」。
        ⚠️ 要看**逐条产品明细**（产品编码 / 增速 / 较上月末三件套）→ get_liability_detail；
           要按某指标取 **TopN**（谁最大、成本率最高）→ rank_liability_products。"""
        return baop.get_liability_overview(lib_cls, cur, period or None, basis, group_by)

    @mcp.tool()
    @_safe
    def get_liability_detail(lib_cls: str = "合计", cur: str = "", period: str = "",
                                  top_n: int = 0, growth: bool = True,
                                  vs: str = "none") -> dict[str, Any]:
        """**负债交叉明细**：大类 × 币种 × 业务类别逐条清单，含产品编码、占比、年均余额、成本率与增速。
        适合「同业负债里各产品分别多少」「本币和外币各是多少」「哪类产品在涨哪类在跌」
        「各产品的成本率分别是多少」「三级目录 / 产品编码是什么」「外币负债结构」
        「某大类下面有哪些产品」这类需要**逐条看明细**的问题。
        lib_cls 可传「合计/同业负债/本币司库/外币司库」等口语，缺省「合计」= 返回全部产品的明细；
        cur 不传即全部币种，也可传 01(本币)/02(外币)；
        growth 默认 True，展示源表预计算的四个增速(%)：余额环比/同比、年均余额环比/同比；
        top_n 只展示余额前 N 条（0 = 全部），统计口径不受影响；
        vs 可选 none(默认) / last_mth_end(较上月末) / last_fri(较上周五)，
          打开后追加「余额变动(亿元) / 年均余额变动(亿元) / 成本率变动(百分点)」三列；
        period 传业务日期（DATA_PRDC_DT）；不传取最新。
        ⚠️ 余额占比是相对全部负债的占比，不是相对本大类。
        ⚠️ 只要「谁最大 / 谁成本率最高」这类排名 → rank_liability_products；
           只要大类构成与合计 → get_liability_overview。
        ⚠️ vs 的成本率变动单位是**百分点**，不是亿元。"""
        return baop.get_liability_detail(lib_cls, cur, period or None, top_n or 0,
                                    bool(growth), vs)

    @mcp.tool()
    @_safe
    def rank_liability_products(lib_cls: str = "同业负债", metric: str = "acct_bal",
                                     period: str = "", top_n: int = 10, order: str = "desc",
                                     cur: str = "") -> dict[str, Any]:
        """负债各业务类别排名（规模 / 成本率 / 余额占比），返回横向条形图。
        适合「同业负债里哪类产品规模最大」「哪类负债产品成本率最高」「余额占比最大的产品是什么」。
        lib_cls 指定负债大类（缺省同业负债）；metric 可选 acct_bal(余额，默认)/bal_per(余额占比)
          /avg_bal(年均余额)/avg_bal_per(年均余额占比)/cost_rate(成本率)；order 为 desc/asc。
        cur 可限定币种（01 本币 / 02 外币），不传为全部币种。
        period 传业务日期（DATA_PRDC_DT）；不传则取最新数据日期下的最大业务日期（DATA_DT 恒取最新）。
        ⚠️ top_n 只影响展示行数，统计口径始终是全量。
        ⚠️ 只要大类构成 / 合计 / 币种占比 → get_liability_overview；
           要逐条明细（含产品编码、年均余额、增速）→ get_liability_detail。"""
        return baop.rank_liability_products(lib_cls, metric, period or None, top_n or 0, order,
                                       cur or None)

    @mcp.tool()
    @_safe
    def compare_periods(object: str = "income", keys: str = "", n_periods: int = 3,
                             period_to: str = "") -> dict[str, Any]:
        """多期对比与趋势，返回折线图。
        适合「最近几期走势如何」「趋势怎么样」「是升还是降」「比上月 / 较年初变化多少」
        「近三期累计变化了多少」「把各对象的多期趋势都列出来」。
        object 可选 income(收入，默认) / liability(负债) / capital(资本公积)；
        keys 为逗号分隔的对象清单（如「资金管理中心,客户中心」），不传则全部对象；
        n_periods 回溯期数(2–12，默认 3；**以源数据的期数为单位，不是月份**)；period_to 为结束期次，不传即最新。
        income 的期次是**业务日期 BIZ_DT**、liability 是**业务日期 DATA_PRDC_DT**（两者数据日期 DATA_DT 恒取最新快照），
        所以「多期」= 同一快照下的多个业务日；capital 只有 DATA_DT，期次即数据日期。
        ⚠️ 摘要只给「合计口径」的变化幅度；单个对象的增速 / 累计变化请按「末期 ÷ 首期 - 1」自行计算后给出。
        ⚠️ 只问某一期的数值 / 构成 / 排名，不要用本工具，用对应的单期工具
           （overview / rank_income / capital_reserve / liability_overview）；本工具只回答「随时间怎么变」。
        ⚠️ 用户说「近半年 / 近一季度」时不要换算成 180 / 90 期，按可得期数返回；
           期数不足时工具会在 notes 里给出「需 N 期，实际可用 M 期」的降级说明，照抄即可。
        ⚠️ 若该对象只有 1 个可用期次（当前负债/资本即如此），工具会直接报边界并返回 NOT_FOUND，
           不会给出「持平 0.00」的假趋势；此时改用对应的单期工具（overview / capital）。"""
        ks = [x.strip() for x in (keys or "").split(",") if x.strip()]
        return baop.compare_periods(object, ks or None, n_periods or 3, period_to or None)


    return mcp
