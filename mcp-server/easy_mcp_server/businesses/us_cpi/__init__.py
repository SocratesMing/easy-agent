"""美国 CPI 预测 MCP 工具（四张表，含 JSON 文本列，只读）。

挂载路径：``/mcp/us_cpi/``

数据口径见 ``docs/MCP_docs/美国CPI预测_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
