"""Market 业务：行情查询与当前用户持仓。

挂载路径：/mcp/market/
"""

from mcp.server.fastmcp import FastMCP

from .server import build

__all__ = ["build"]


def business_name() -> str:
    return "market"
