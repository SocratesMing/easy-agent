"""黄金周频预测 MCP 工具（单表宽表，含事后验证，只读）。

挂载路径：``/mcp/gold_weekly/``

数据口径见 ``docs/MCP_docs/黄金周频预测_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
