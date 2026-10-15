"""美联储议息会议 MCP 工具（三张表，含图片二进制，只读）。

挂载路径：``/mcp/fomc/``

数据口径见 ``docs/MCP_docs/美联储议息会议_SQL清单_MCP信息.md``。
"""

from .server import build

__all__ = ["build"]
