"""黄金月频预测 MCP 工具（只读一张宽表）。

挂载路径：``/mcp/gold_monthly/``

数据口径见 ``docs/MCP_docs/黄金月频预测_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
