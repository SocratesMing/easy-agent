"""贵金属业务 MCP 工具（precious_metals，HTTP 报表查询，只读）。

挂载路径：``/mcp/precious_metals/``

数据口径见 ``dim_dictionary.json``（维度码值字典）；文件结构与其它业务包一致
（领域层 :mod:`precious_metals` + 工具层 :mod:`server`）。
"""

from .server import build

__all__ = ["build"]
