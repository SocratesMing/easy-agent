"""利率择时模型 MCP 工具（只读已保存的预测与运行审计）。

挂载路径：``/mcp/rates_timing/``
"""

from .server import build

__all__ = ["build"]
