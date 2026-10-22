"""贵金属经营分析 MCP 工具（precious_metals v2.0，HTTP 报表数据源，不连库）。

挂载路径：``/mcp/precious_metals/``

文件结构与其它业务包一致：领域层放报表解析与取数逻辑，本文件只做 MCP 工具注册
（async 薄包装 + 统一异常兜底）。

领域层是 v2.0「一张报表一个模块」的架构（源文件原样保留，未做任何修改）：

    pm_core.py                 公共核心：配置、HTTP、HTML/XLSX/CSV 解析、
                               合并单元格展开、双层表头重建、返回信封
    reports/r8666_overview.py  整体情况表声明（维度/指标/关键词）
    reports/r8670_detail.py    分产品明细声明
    precious_metals.py         工具实现 + JSON-RPC 协议层

领域层用的是顶层绝对导入（``import pm_core`` / ``import reports``），因此导入前把
本包目录临时挂到 ``sys.path`` 首位、导入完成后立即摘掉——既不修改源文件，也不把
包目录长期留在进程级模块搜索路径里。

数据源是独立的贵金属报表服务，连接地址见同目录 ``config.json`` 的
``report_base_url`` / ``export_url``（部署专属，不入库；优先级高于环境变量）。

并发：MCP SDK 对同步工具函数是直接调用、不丢线程池，而 urllib 是阻塞调用，
因此工具都是 ``async def`` + ``anyio.to_thread.run_sync``。
"""

from __future__ import annotations

import functools
import logging
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

# 领域层用顶层绝对导入（import pm_core / import reports），本包内自带这两个模块。
# 临时把包目录放到 sys.path 首位，导入完成后立刻移除，避免污染进程路径。
_PKG_DIR = str(Path(__file__).resolve().parent)
_ADDED_TO_PATH = _PKG_DIR not in sys.path
if _ADDED_TO_PATH:
    sys.path.insert(0, _PKG_DIR)
try:
    from . import precious_metals as pm
finally:
    if _ADDED_TO_PATH:
        try:
            sys.path.remove(_PKG_DIR)
        except ValueError:  # pragma: no cover - 理论上不会发生
            pass

logger = logging.getLogger("easy-mcp-server")

# INSTRUCTIONS 由领域层按报表注册表动态生成（含报表名与取数硬性规则），直接复用，
# 保证与 stdio 版口径完全一致。
INSTRUCTIONS = pm.INSTRUCTIONS


def _safe(fn):
    """统一异常兜底 + 丢线程池执行。

    必须用 ``functools.wraps`` 保留签名——MCP 靠 ``inspect.signature`` 生成工具
    JSON Schema，签名退化成 ``(*args, **kwargs)`` 会被宿主参数校验拒绝。
    异常时返回与正常信封同构的 text 信封，调用方无需分辨两种失败。
    """
    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await anyio.to_thread.run_sync(functools.partial(fn, *a, **kw))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[precious_metals] {fn.__name__} 执行异常: {type(e).__name__}: {e}")
            return pm.C.envelope(
                shape="text",
                title="查询失败",
                summary=f"工具执行异常：{type(e).__name__}: {e}",
                unit="—",
                notes=["该类失败通常是报表服务不可达或页面结构变化，可先调 precious_metals_probe 体检。"],
            )
    return wrapper


def build() -> FastMCP:
    mcp = FastMCP(
        "贵金属经营分析",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    @mcp.tool()
    @_safe
    def precious_metals_list_reports() -> dict[str, Any]:
        """列出可查的贵金属报表，以及每张报表支持的维度、指标数量与口径简述。
        不确定该查哪张表、或想知道有哪些维度时先调它。"""
        return pm.list_reports()

    @mcp.tool()
    @_safe
    def precious_metals_query(
        metric: str = "",
        report: str = "",
        report_dt: str = "",
        top_n: int = 0,
        order: str = "desc",
        p8670_portfolio: str = "",
        p8670_product: str = "",
        p8670_level5: str = "",
        p8666_section: str = "",
        p8666_group: str = "",
        p8666_dim: str = "",
    ) -> dict[str, Any]:
        """【主查询】按指标名（或整句问题）取数，返回「报表/板块或行/指标/数值/数值_num/单位」。
        不传 report 时按问题内容自动路由到最匹配的报表；命中不明确会返回候选清单要求确认，不会乱猜。
        支持维度过滤参数（见各参数说明）与自动识别：问「贵金属租出-企业的损益合计」可直接传 p8670_level5，
        也可整句传给 metric 由工具识别。top_n>0 时按指标排序取前 N（排名场景）。
        report_dt 不填默认前一日，返回里的 notice 必须转告用户。

        Args:
            metric: 指标名或含维度词的整句，如「损益合计（不含内部账）」「期末余额」「TGLD 的损益合计」。留空则返回该报表全量。
            report: 报表名或编号，可留空由工具自动路由。
            report_dt: 报表日期 YYYY-MM-DD；用户未明确指定时不要填。
            top_n: 取前 N 名，0 或不填表示全部。
            order: desc（默认，高到低）或 asc。
            p8670_portfolio: 【贵金属业务（分产品明细）】投资组合，4 位英文码值，报表页面不提供中文名。只能按码值精确筛选；想按业务含义筛请改用 p8670_product 或 p8670_level5。
            p8670_product: 【贵金属业务（分产品明细）】按交易形态划分的业务大类，固定 5 项，可中文精确匹配。取值：贵金属即远期、贵金属掉期、贵金属期货延期、贵金属期权、其他。
            p8670_level5: 【贵金属业务（分产品明细）】全行统一产品字典，中文品种名，可按中文匹配。取值以页面数据为准（如 Au99.99现货实盘）。
            p8666_section: 【贵金属业务-整体情况表】报表内的分区。取值：贵金属业务规模情况、贵金属业务损益情况。
            p8666_group: 【贵金属业务-整体情况表】该行是按什么口径分组的。取值：按投资组合、按产品分类、合计。不同分组之间不可相加。
            p8666_dim: 【贵金属业务-整体情况表】分组下的具体取值：某投资组合码值（4 位英文）、某产品分类（中文）、或「合计」。含量随 p8666_group 变化，跨分组不可相加。
        """
        return pm.query(
            report=report,
            metric=metric,
            report_dt=report_dt,
            top_n=top_n,
            order=order,
            base_url=None,
            p8670_portfolio=p8670_portfolio,
            p8670_product=p8670_product,
            p8670_level5=p8670_level5,
            p8666_section=p8666_section,
            p8666_group=p8666_group,
            p8666_dim=p8666_dim,
        )

    @mcp.tool()
    @_safe
    def precious_metals_breakdown(
        metric: str = "",
        report: str = "",
        report_dt: str = "",
        top_n: int = 0,
        order: str = "desc",
        p8670_portfolio: str = "",
        p8670_product: str = "",
        p8670_level5: str = "",
        p8666_section: str = "",
        p8666_group: str = "",
        p8666_dim: str = "",
    ) -> dict[str, Any]:
        """【拆解/排名】把某个指标按本表的行维度拆开（分组合 / 分产品 / 分板块），
        并按数值排序，用于「哪个产品损益最高」「各投资组合分别多少」。已排除合计/小计行。

        Args:
            metric: 用于拆解的指标，如「损益合计（不含内部账）」「期末余额」。（必填）
            report: 报表名或编号，可留空自动路由。
            report_dt: 报表日期 YYYY-MM-DD；不填默认前一日。
            top_n: 取前 N 名，0 表示全部。
            order: desc（默认）或 asc。
            p8670_portfolio: 【贵金属业务（分产品明细）】投资组合 4 位英文码值（报表无中文名），只能按码值精确筛选。
            p8670_product: 【贵金属业务（分产品明细）】业务大类，5 项中文可精确匹配：贵金属即远期/贵金属掉期/贵金属期货延期/贵金属期权/其他。
            p8670_level5: 【贵金属业务（分产品明细）】全行统一产品字典，中文品种名，可按中文匹配。
            p8666_section: 【贵金属业务-整体情况表】分区：贵金属业务规模情况 / 贵金属业务损益情况。
            p8666_group: 【贵金属业务-整体情况表】分组口径：按投资组合 / 按产品分类 / 合计（不同分组不可相加）。
            p8666_dim: 【贵金属业务-整体情况表】分组下的具体取值，含量随分组变化，跨分组不可相加。
        """
        return pm.breakdown(
            report=report,
            metric=metric,
            report_dt=report_dt,
            top_n=top_n,
            order=order,
            base_url=None,
            p8670_portfolio=p8670_portfolio,
            p8670_product=p8670_product,
            p8670_level5=p8670_level5,
            p8666_section=p8666_section,
            p8666_group=p8666_group,
            p8666_dim=p8666_dim,
        )

    @mcp.tool()
    @_safe
    def precious_metals_describe(report: str = "", field: str = "") -> dict[str, Any]:
        """查口径与取值：某张报表支持哪些维度、每个维度有哪些取值（码值/枚举/品种名）、
        某字段的业务含义（如「含内部账和不含内部账有什么区别」）。不传参数返回全部报表总览。

        Args:
            report: 报表名或编号；可留空，只传 field 时自动在该维度所属报表里查。
            field: 维度名或字段/口径关键词，如「投资组合」「产品分类」「含内部账」「损益合计」。
        """
        return pm.describe(report, field)

    @mcp.tool()
    @_safe
    def precious_metals_probe(report: str = "", report_dt: str = "") -> dict[str, Any]:
        """页面结构体检：取不到数、或换到内网/报表改版后先调它。返回页面是否可抓、
        判定抽取方式、表头层数与字段命中数、识别出的列名、导出接口是否可用。只做诊断，不返回业务数值。

        Args:
            report: 报表名或编号；不填则体检第一张报表。
            report_dt: 报表日期 YYYY-MM-DD；不填默认前一日。
        """
        return pm.probe(report, report_dt)

    return mcp
