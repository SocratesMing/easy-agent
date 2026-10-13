"""投资研究问数业务。

挂载路径：/mcp/investment_research/

包含的场景（各占包内一个领域模块）：

- `idata.py` —— 机构当日 / 个券机构 / 债券借贷 三个看板 + 大模型点评（四张表）
- `domestic_macro.py` —— 国内宏观经济指标预测报告、模型预测与特征归因
- `rates_timing.py` —— 利率择时模型的已存预测、运行审计与因子解释（只读）
- `weekly_research.py` —— 周度研报观点矩阵与机构宏观预测

MCP 工具统一注册在 ``server.py``；业务清单仍是 ``investment_research``，无需改主应用。

通用能力（SQL 只读网关、连接池、表探索）来自 `easy_mcp_server.querykit`。
"""

from .server import build

__all__ = ["build"]
