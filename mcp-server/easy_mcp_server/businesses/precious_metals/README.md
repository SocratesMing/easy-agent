# precious_metals —— 贵金属业务数据查询 MCP

查询贵金属业务的两张报表（分产品明细表、整体情况表），结构化返回数值。

## 挂载路径
`/mcp/precious_metals/`

## 数据源
独立的贵金属报表服务（HTTP），连接地址见同目录 `config.json` 的 `report_base_url`。
当前部署地址：`http://29.241.91.84:9080`（换环境只改 `config.json`，不用动代码）。

## 文件结构
与 mcp-server 其它业务包一致：
- `precious_metals.py` —— 领域层：报表定义、维度字典、HTTP 抓取、HTML / 导出文件解析与全部业务逻辑（原 `precious_metals_query_mcp.py` 整体迁入，零三方依赖，仅用标准库）。
- `server.py` —— 工具层：`build() -> FastMCP`，把 8 个处理函数包成 MCP 工具（async 薄包装 + 统一异常兜底）。
- `config.json` —— 报表服务地址、超时、渲染模式、导出接口模板。
- `dim_dictionary.json` —— 维度码值字典（投资组合 4 位码值 / 产品分类 5 项 / 五级产品全行字典）。

## 工具清单
| 工具 | 作用 |
|---|---|
| `precious_metals_list_reports` | 列出两张报表及全部指标字段、维度取值、默认查询日期 |
| `precious_metals_query_detail` | 分产品明细表（8670）：按投资组合/产品分类/五级产品列示 22 项指标 |
| `precious_metals_query_overview` | 整体情况表（8666）：规模板块与损益板块总量 |
| `precious_metals_query_metric` | 按指标名查具体数值，支持维度过滤与口语问法 |
| `precious_metals_rank` | 按明细指标排序/排名 |
| `precious_metals_compare` | 对比两个日期的同一指标（环比/同比） |
| `precious_metals_describe` | 字段/口径业务含义，或查维度取值清单 |
| `precious_metals_probe` | 页面结构体检（取不到数时先调它诊断） |

## 原始实现说明
领域层直接沿用 `E:/zx1/precious-metals-mcp/precious_metals_query_mcp.py`（v1.5），
能力：优先读报表「导出」文件（CSV/XLSX），失败再解析 ereport 页面表格；
页面结构自适应（标准表格 / div 栅格 / 重复块 / 文本行四级降级）；
可选本机 Edge/Chrome 无头渲染；维度码值字典校验。
与仓库集成时**未改动其业务逻辑**，仅把它作为领域层模块挂到 FastMCP 之下。
