"""G7 外汇研判模型 MCP 工具（单表，六币种结果存在 JSON 字段里，只读）。

挂载路径：``/mcp/g7_fx/``

数据口径见 ``docs/MCP_docs/G7外汇研判模型_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
