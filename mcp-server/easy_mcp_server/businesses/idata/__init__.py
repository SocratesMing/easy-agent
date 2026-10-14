"""iData 数据分析看板 MCP 工具（机构流向 / 个券机构 / 债券借贷 / 大模型点评）。

挂载路径：``/mcp/idata/``
"""

from .server import build

__all__ = ["build"]
