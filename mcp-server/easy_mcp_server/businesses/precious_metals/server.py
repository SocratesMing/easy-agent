"""贵金属业务 MCP 工具（precious_metals，HTTP 报表查询，只读）。

挂载路径：``/mcp/precious_metals/``

文件结构与其它业务包一致：领域层 :mod:`precious_metals` 放报表定义、维度字典、
HTTP 抓取、HTML / 导出文件解析与全部业务逻辑；本文件只做 MCP 工具注册
（async 薄包装 + 统一异常兜底）。

数据源是独立的贵金属报表服务，连接地址见同目录 ``config.json`` 的
``report_base_url``（部署专属，不入库）。

并发：MCP SDK 对同步工具函数是直接调用、不丢线程池，而 urllib 是阻塞调用，
因此工具都是 ``async def`` + ``anyio.to_thread.run_sync``。
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from . import precious_metals as pm

logger = logging.getLogger("easy-mcp-server")

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


def _err_env(title: str, summary: str) -> dict:
    """异常兜底时返回与正常信封同构的最小错误信封。"""
    return {
        "shape": "text",
        "title": title,
        "summary": summary,
        "result_state": "NOT_FOUND",
    }


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
            logger.warning(f"[precious_metals] {fn.__name__} 执行异常: {type(e).__name__}: {e}")
            return _err_env("查询失败", f"工具执行异常：{type(e).__name__}: {e}")
    return wrapper


def build() -> FastMCP:
    mcp = FastMCP(
        "贵金属业务数据查询",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    @mcp.tool()
    @_safe
    def precious_metals_list_reports() -> dict[str, Any]:
        """列出贵金属业务两张报表（分产品明细表、整体情况表）及全部指标字段、维度取值与默认查询日期。不确定指标名时先调它。"""
        return pm.list_reports()

    @mcp.tool()
    @_safe
    def precious_metals_query_detail(report_dt: str = "") -> dict[str, Any]:
        """【贵金属业务（分产品明细）】按投资组合/产品分类/五级产品列示本金、损益、公允价值、交易笔数、持有期限等 22 项指标（含/不含内部账双口径）。用于分产品、分组合的明细问题。report_dt 不填则默认前一日，返回会给出 notice，必须转告用户实际查询日期。"""
        return pm.query_report("8670", report_dt)

    @mcp.tool()
    @_safe
    def precious_metals_query_overview(report_dt: str = "") -> dict[str, Any]:
        """【贵金属业务-整体情况（全业务合计）】规模板块（期末余额、占比、当期发生额、交易笔数）与损益板块（营业净收入、非息收入、业务损益、公允价值变动、评价损益、其他损益、损益合计）。用于总量、占比、整体损益问题。report_dt 不填则默认前一日。"""
        return pm.query_report("8666", report_dt)

    @mcp.tool()
    @_safe
    def precious_metals_query_metric(metric: str = "", report_dt: str = "", portfolio: str = "",
                                      product: str = "", level5: str = "", section: str = "") -> dict[str, Any]:
        """按指标名查具体数值，返回「报表/板块或行/指标/数值/数值_num/单位」。支持维度过滤与口语问法：问「贵金属租出-企业的损益合计」可传 level5，也可整句传给 metric 由工具自动识别维度。注意：投资组合是 4 位英文码值（如 FBHF），只能按码值筛；按业务含义筛请用 level5/product。某指标在明细层不存在时会明确说明，不会用整体表合计值冒充分项值。report_dt 不填默认前一日。"""
        return pm.query_metric(metric, report_dt, portfolio, product, level5, section)

    @mcp.tool()
    @_safe
    def precious_metals_rank(metric: str, top_n: int = 0, order: str = "desc",
                             report_dt: str = "", portfolio: str = "", product: str = "") -> dict[str, Any]:
        """按明细指标排序/排名（如「哪个产品损益最高」）。返回排名/投资组合/产品分类/五级产品/数值，已排除合计行。top_n=0 表示全部。仅支持分产品明细表的指标。"""
        return pm.rank(metric, top_n, order, report_dt, portfolio, product)

    @mcp.tool()
    @_safe
    def precious_metals_compare(metric: str, report_dt: str = "", report_dt_prev: str = "",
                                portfolio: str = "", product: str = "", level5: str = "") -> dict[str, Any]:
        """对比两个日期的同一指标（环比/同比），返回每行两期数值、差额、变动率。report_dt_prev 不填时默认取上一月同日。用于「和上个月比怎么样」「环比变化」。"""
        return pm.compare(metric, report_dt, report_dt_prev, portfolio, product, level5)

    @mcp.tool()
    @_safe
    def precious_metals_describe(field: str = "") -> dict[str, Any]:
        """返回字段/口径的业务含义，如「含内部账和不含内部账有什么区别」「占比的分母是什么」。也用于查维度取值清单：field 传「投资组合」返回 35 个码值、传「产品分类」返回 5 项枚举、传「五级产品」返回 159 项完整品种字典。不传 field 则返回全部口径说明。"""
        return pm.describe(field)

    @mcp.tool()
    @_safe
    def precious_metals_probe(report_id: str = "8666", report_dt: str = "") -> dict[str, Any]:
        """页面结构体检：取不到数、或换到内网/报表改版后先调它。返回页面体积、可见文字量、<table> 与 role=row 数量、判定的抽取方式（标准表格 / div 栅格 / 重复块 / 文本行 / 未识别）、是否需要无头浏览器渲染、页面上发现的导出链接，以及导出接口（export_url）是否可用。只做诊断，不返回业务数值。"""
        return pm.probe(report_id, report_dt)

    return mcp
