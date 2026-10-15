"""外汇晴雨指数 MCP 工具（单表，整行 JSON，只读）。

挂载路径：``/mcp/fx_barometer/``

数据口径见 ``docs/MCP_docs/外汇晴雨指数_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
