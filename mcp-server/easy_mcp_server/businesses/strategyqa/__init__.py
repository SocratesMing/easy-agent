"""策略问数业务：查询 `fmut2_strategy_manage` 策略表。

挂载路径：/mcp/strategyqa/

通用能力（SQL 只读网关、连接池、表探索）来自 `easy_mcp_server.querykit`，
本包只负责策略领域的口径与查询构造。
"""

from .server import build

__all__ = ["build"]
