"""问数业务：自然语言 → 只读 SQL → 结果。

挂载路径：/mcp/dataqa/

与主应用共用同一个数据库（MYSQL_*），所有查询强制单条只读 SELECT。
"""

from .server import build

__all__ = ["build"]
