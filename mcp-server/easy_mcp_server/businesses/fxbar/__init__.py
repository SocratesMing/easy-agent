"""外汇 K 线问数业务：查询 `HSDC_HDS2_BAR_FX` 外汇行情（公共数据，不做行级隔离）。

挂载路径：/mcp/fxbar/

表是高频 K 线**大表**，因此工具层强制「一个合约 + 一个频率 + 有限时间窗 + LIMIT」，
缺省只返回最近一段（``FX_DEFAULT_WINDOW_HOURS``）；需要其他时间段时由调用方显式
指定 ``start_time`` / ``end_time`` 或 ``days``。

通用能力（SQL 只读网关、连接池、表探索）来自 `easy_mcp_server.querykit`，
本包只负责外汇领域的口径与查询构造。
"""

from .server import build

__all__ = ["build"]
