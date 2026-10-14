"""金融市场部经营分析（baop 库）MCP 工具。

挂载路径：``/mcp/baop/``

底层查询逻辑与工具清单来自同包的 ``baop_mcp_server``（金融市场部经营分析 Skill
的取数通道）。本文件只负责把它适配成 mcp-server 的业务包约定：

* 提供 ``build() -> FastMCP`` 工厂；
* 创建符合本服务挂载约定的 FastMCP（``streamable_http_path="/"``、
  ``stateless_http=True``）；
* 复用 ``baop_mcp_server._register_tools`` 注册全部工具，**不改动原文件的一行
  逻辑**，从而保持与独立 stdio 运行版本完全一致的查询行为。

数据库连接由 ``baop_mcp_server`` 自带（读取 ``BAOP_*`` 环境变量，直连 baop 只读
库），不经由本服务的统一 querykit 通道——baop 库是独立的数据源。

鉴权沿用本服务的 Bearer ApiKey 中间件（与其他业务一致）。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .baop_mcp_server import INSTRUCTIONS, _register_tools


def build() -> FastMCP:
    """构造并注册金融市场部经营分析业务，返回 FastMCP 实例。"""
    mcp = FastMCP(
        "baop-analysis",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )
    _register_tools(mcp)
    return mcp
