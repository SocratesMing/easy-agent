"""金融市场部经营分析 MCP 工具（baop 独立只读库，三张表，只读）。

挂载路径：``/mcp/baop/``

数据口径见 ``docs/MCP_docs/`` 下业务方提供的说明；文件结构与其它业务包一致
（领域层 :mod:`baop` + 工具层 :mod:`server`）。
"""

from .server import build

__all__ = ["build"]
