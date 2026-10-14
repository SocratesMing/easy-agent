"""1 年期国债模型 MCP 工具（单表，结果存在 JSON 字段里，只读）。

挂载路径：``/mcp/treasury_1y/``

数据口径见 ``docs/MCP_docs/1年期国债模型_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
