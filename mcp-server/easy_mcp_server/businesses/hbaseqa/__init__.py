"""外汇行情问数业务：HBase Thrift 只读扫描。

挂载路径：/mcp/hbaseqa/

与 `dataqa`（MySQL 问数）的分工：`dataqa` 面向关系型行情表并允许模型写 SQL；
本业务面向 HBase 的 K 线 / Tick 宽表，只有**参数化 scan**，没有任何"任意语句"入口。

工具分三层：

- `describe_fx_schema`：口径字典（表命名规则、字段语义、易错点）
- `get_fx_bars` / `list_contracts`：外汇行情领域工具，覆盖绝大多数问法
- `list_tables` / `describe_table` / `sample_rows`：通用探索，兜底新问法

身份与 `dataqa` 一致：复用全局 Bearer 鉴权中间件，业务代码只调
:func:`easy_mcp_server.context.current_username`。需要在主应用设置页为
`hbaseqa` 单独签发一把 Key。
"""

from .server import build

__all__ = ["build"]
