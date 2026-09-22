"""投资研究问数业务（当前场景：iData 数据分析看板）。

挂载路径：/mcp/investment_research/

包含的场景（各占包内一个领域模块）：

- `idata.py` —— 机构当日 / 个券机构 / 债券借贷 三个看板 + 大模型点评（四张表）

**新增场景的方式**：在本包内加一个领域模块（如 ``xxx.py``），在 ``server.py`` 里挂上
对应工具即可——不需要新建业务包，也不需要改主应用。

通用能力（SQL 只读网关、连接池、表探索）来自 `easy_mcp_server.querykit`。
"""

from .server import build

__all__ = ["build"]
