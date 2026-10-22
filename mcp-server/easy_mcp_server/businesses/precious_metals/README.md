# precious_metals —— 贵金属业务数据查询 MCP

查询贵金属业务的两张报表（分产品明细表、整体情况表），结构化返回数值。

## 挂载路径
`/mcp/precious_metals/`

## 数据源
独立的贵金属报表服务（HTTP），连接地址见同目录 `config.json` 的 `report_base_url`。
当前部署地址：`http://29.241.91.84:9080`（换环境只改 `config.json`，不用动代码）。
`config.json` 优先级高于环境变量；`PRECIOUS_METALS_*` 环境变量仅为兼容旧部署保留。

## 文件结构（v2.0：一张报表 = 一个模块）
与 mcp-server 其它业务包一致：领域层放业务逻辑，`server.py` 只做工具注册。

- `pm_core.py` —— 公共核心：配置读取、HTTP 取数、HTML/XLSX/CSV 解析、合并单元格展开、
  双层表头重建、日期规则、结果信封（零三方依赖，仅标准库）。
- `reports/r8670_detail.py` —— 分产品明细表（8670）声明：维度、指标字段、口语别名、路由关键词。
- `reports/r8666_overview.py` —— 整体情况表（8666）声明。
- `reports/__init__.py` —— 报表注册表；新增一张报表 = 加一个模块 + 在此 import 一次。
- `precious_metals.py` —— 工具实现 + JSON-RPC 协议层（原 `precious_metals_query_mcp.py` 整体迁入）。
- `server.py` —— 工具层：`build() -> FastMCP`，把 5 个处理函数包成 MCP 工具
  （async 薄包装 + 统一异常兜底）。
- `config.json` —— 报表服务地址、超时、渲染模式、报表编号参数名、导出接口模板。
- `dim_dictionary.json` —— 维度码值字典（投资组合 4 位码值 / 产品分类 5 项 / 五级产品全行字典）。

> 领域层四个 Python 文件与 `dim_dictionary.json` 均与源目录
> `E:/zx1/precious-metals-mcp/` **逐字节一致**，未改动业务逻辑；只有 `config.json`
> 按部署环境覆盖了服务地址。

## 工具清单（5 个）

| 工具 | 作用 |
|---|---|
| `precious_metals_list_reports` | 列出可查报表及各自支持的维度、指标数量与口径简述 |
| `precious_metals_query` | **主查询**：按指标名或整句问题取数，`report` 留空自动路由；支持维度过滤与 `top_n` 排名 |
| `precious_metals_breakdown` | 按行维度（分组合 / 分产品 / 分板块）拆解并排序，已排除合计/小计行 |
| `precious_metals_describe` | 查维度取值（码值/枚举/品种名）与字段口径含义 |
| `precious_metals_probe` | 页面结构体检：取不到数 / 报表改版 / 换内网时先调它诊断 |

维度过滤参数（`query` / `breakdown` 共用，参数名带报表前缀避免撞名）：

- 8670：`p8670_portfolio`（投资组合，4 位英文码值）、`p8670_product`（业务大类，5 项中文）、
  `p8670_level5`（全行产品字典，中文品种名）
- 8666：`p8666_section`（分区：规模情况 / 损益情况）、`p8666_group`（分组口径：按投资组合 /
  按产品分类 / 合计）、`p8666_dim`（分组下的具体取值）

> ⚠️ `p8666_group` 的不同分组之间**不可相加**（9 个组合行之和 ≠ 4 个产品分类行之和）。

## 取数与路由要点
- 取数优先解析报表「导出」文件（CSV/XLSX），失败再解析 ereport 页面表格；
  页面结构自适应（标准表格 / div 栅格 / 重复块 / 文本行四级降级）；可选本机 Edge/Chrome 无头渲染。
- v2.0 按报表编号直达对应模块，不再逐表试探；跨表问题由 `route()` 按各表自声明关键词打分选表，
  命中不明确时返回候选清单要求上层确认，不擅自猜。
- 报表编号参数名固定为双下划线 `__reportId`（内网平台实测），见 `config.json` 的 `report_id_param`。
- 日期：用户未明确给出时交给工具默认取前一日，返回里的 `notice` 需转告用户。

## 模块导入说明
`precious_metals.py` / `reports/*` 使用顶层绝对导入（`import pm_core` / `import reports`）。
`server.py` 在导入领域层前把本包目录临时加入 `sys.path` 首位，导入完成后立即移除，
因此**源文件无需任何改动**，也不会把包目录长期留在进程级模块搜索路径中。
