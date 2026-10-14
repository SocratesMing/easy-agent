"""黄金价格多因子归因 MCP 工具（单表宽表，只读）。

挂载路径：``/mcp/gold_analysis/``

数据口径见 ``docs/MCP_docs/黄金价格驱动因子分析_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
