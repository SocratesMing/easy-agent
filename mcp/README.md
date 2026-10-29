# 金融市场部经营分析 · MCP Server

把 MySQL 库 `jmfx_analysis` 中的数据，以**业务语义化工具**的形式暴露给支持 MCP 的宿主，
供自然语言问答调用。每个工具直接返回**文字结论 + 表格 + ECharts option**。

> **当前宿主：WorkBuddy**（已配置完成，见第 5 节）。easyagent 的接法保留在第 5 节末尾备用。

---

## 1. 目录与依赖

```
mcp/
├── jmfx_mcp_server.py     # MCP Server 主程序
├── requirements.txt       # mcp / pymysql
└── README.md              # 本文件
```

依赖已装在**独立虚拟环境**（避免与其他项目冲突）：

```
C:\Users\issuser\.workbuddy\binaries\python\envs\jmfx-mcp\Scripts\python.exe
```

如需重建：

```bash
python -m venv envs/jmfx-mcp
envs/jmfx-mcp/Scripts/pip.exe install -r mcp/requirements.txt
```

> **SDK 版本兼容**：官方 Python SDK 在 **2.0** 起把 `FastMCP` 改名为 `MCPServer`
> （`mcp.server.mcpserver`），且 `host/port` 从构造参数改为 `run()` 参数。
> 本服务已做**双版本兼容**（自动探测 1.x / 2.x），当前环境为 `mcp 2.3.0`。
> 无需因 SDK 升级改代码。

---

## 2. 数据库前置

先在 MySQL 中执行 `mysql/readonly_and_views.sql`，它会：

| 动作 | 说明 |
|---|---|
| 建只读账号 | `jmfx_ro` / `Jmfx_RO@2026`，**仅授 SELECT**，限定在 `jmfx_analysis` |
| 建口径视图 | `v_team_pl` / `v_portfolio_pl` / `v_total_pl`（元 → **万元**） |
| 建指标视图 | `v_indicator_std`（元原值 + `*_wan` 万元 + `*_yi` 亿元）、`v_liability`（万元） |
| 建排名视图 | `v_team_rank_month` |

> ⚠️ 生产环境请修改 `jmfx_ro` 的密码，并同步改下面的 `JM_A_PASSWORD`。

---

## 3. 连接配置（环境变量）

全部有默认值，不设也能跑（默认连本机只读账号）：

| 变量 | 默认值 |
|---|---|
| `JM_A_HOST` | `127.0.0.1` |
| `JM_A_PORT` | `3306` |
| `JM_A_USER` | `jmfx_ro` |
| `JM_A_PASSWORD` | `Jmfx_RO@2026` |
| `JM_A_DB` | `jmfx_analysis` |

---

## 4. 启动方式

```bash
PY="C:\Users\issuser\.workbuddy\binaries\python\envs\jmfx-mcp\Scripts\python.exe"

# ① stdio —— 宿主与本服务同机（**WorkBuddy 走这条**；进程由宿主按需拉起，无需手动启动）
"$PY" jmfx_mcp_server.py

# ② SSE —— 宿主在另一台机器 / 需要 HTTP 长连接（推荐给 easyagent 前台）
"$PY" jmfx_mcp_server.py --transport sse --port 8765

# ③ Streamable HTTP —— 新版 MCP 传输
"$PY" jmfx_mcp_server.py --transport streamable-http --port 8765

# ④ 自检（不启动服务，只跑一遍所有查询，用于排错）
"$PY" jmfx_mcp_server.py --selftest
```

SSE 启动后地址：`http://<服务器IP>:8765/sse`（默认只监听 `127.0.0.1`，
跨机访问请把 `--host` 改为 `0.0.0.0` 并放通端口）。

---

## 5. 在宿主里配置

### WorkBuddy（配置文件 `~/.workbuddy/mcp.json`）

> ⚠️ 是 **`mcp.json`**，不是 `~/.workbuddy/.mcp.json`（后者是 WorkBuddy 内部聚合文件，不要改）。
> 文件不存在就新建；已存在则**合并进 `mcpServers`，不要覆盖**。

当前实际配置（已写入 `C:\Users\issuser\.workbuddy\mcp.json`）：

```json
{
  "mcpServers": {
    "jmfx-analysis": {
      "command": "C:\\Users\\issuser\\.workbuddy\\binaries\\python\\envs\\jmfx-mcp\\Scripts\\python.exe",
      "args": ["E:\\中信银行\\经营分析\\mcp\\jmfx_mcp_server.py"],
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "JM_A_HOST": "127.0.0.1",
        "JM_A_PORT": "3306",
        "JM_A_USER": "jmfx_ro",
        "JM_A_PASSWORD": "Jmfx_RO@2026",
        "JM_A_DB": "jmfx_analysis"
      }
    }
  }
}
```

**启用步骤（缺一步都不生效）**

| 步骤 | 操作 |
|---|---|
| 1 | 保存 `mcp.json`（JSON 必须合法：引号成对、无尾随逗号） |
| 2 | **完全退出并重启 WorkBuddy**（改配置后不重启不加载） |
| 3 | 进入「连接器管理」→ 找到 `jmfx-analysis` → 点 **信任**（未信任是灰点，AI 调不动） |
| 4 | **新开一个会话**，用自然语言提问验证（如"哪个团队本月损益最高"） |

**Windows 注意**：`command` 必须写 Python 解释器的**绝对路径**，不能写 `python` 或相对路径，否则启动失败亮红灯。

### easyagent（备用）

按你平台的 MCP 配置方式二选一：

- **本机 stdio**：命令填上面的 python 路径，参数填 `jmfx_mcp_server.py`，环境变量同上；
- **远程 SSE**：先启动 `--transport sse --host 0.0.0.0 --port 8765`，
  再在 easyagent 里填 SSE 地址 `http://<服务器IP>:8765/sse`。

---

## 6. 工具清单

| 工具 | 用途 | 返回形态 |
|---|---|---|
| `list_dimensions_tool` | 有哪些团队/中心/期次/指标大类 | 文字 + 表格 |
| `rank_teams_tool` | 各团队业绩排名 | 表格 + **横向条形图** |
| `get_team_pl_tool` | 单个团队整体经营情况 | 文字 + 表格 |
| `get_portfolio_pl_tool` | 团队下各组合损益明细 | 表格 + **横向条形图** |
| `get_indicator_tool` | 经营指标查询（亿元） | 表格 + 条形图 |
| `get_composition_tool` | 某指标下各中心构成占比 | 表格 + **饼图** |
| `get_liability_tool` | 负债规模及同比 | 表格 + **双柱对比图** |
| `get_revenue_vs_noninterest_tool` | 营业收入 vs 非息收入 | 表格 + **分组柱状图** |
| `run_readonly_sql_tool` | 只读 SQL 兜底 | 表格 |

### 6.1 入参明细

| 工具 | 参数 | 类型 | 必填 | 默认 | 取值 / 说明 |
|---|---|---|---|---|---|
| `list_dimensions_tool` | — | — | — | — | 无参数 |
| `rank_teams_tool` | `metric` | string | 否 | `month_cum_pl` | `month_cum_pl` 本月累计损益 / `daily_pl` 当日损益 / `wow_change` 本周五对上周五 / `month_cum_vat` 本月累计增值税 / `bal_end` 期末值 |
| | `report_source` | string | 否 | `""` | 空=全部；`非息损益表` / `外币营收表` |
| | `top_n` | integer | 否 | `0` | >0 时只取前 N 名；0=全部 |
| | `order` | string | 否 | `desc` | `desc` 降序 / `asc` 升序 |
| `get_team_pl_tool` | `team_name` | string | **是** | — | 团队**全称**，如 `贵金属及商品交易团队` |
| | `report_source` | string | 否 | `""` | 同上 |
| `get_portfolio_pl_tool` | `team_name` | string | **是** | — | 团队全称 |
| | `metric` | string | 否 | `month_cum_pl` | 同 `rank_teams` |
| | `top_n` | integer | 否 | `0` | >0 时只取前 N 个组合 |
| `get_indicator_tool` | `indicator_group` | string | 否 | `""` | `营业收入` / `非息收入` / `可供出售账户资本公积变动` / `负债规模指标` |
| | `indicator_name` | string | 否 | `""` | 指标名称，模糊匹配 |
| | `include_total` | boolean | 否 | `true` | 是否包含合计行 |
| `get_composition_tool` | `indicator_group` | string | 否 | `非息收入` | 指标大类 |
| `get_liability_tool` | — | — | — | — | 无参数 |
| `get_revenue_vs_noninterest_tool` | — | — | — | — | 无参数 |
| `run_readonly_sql_tool` | `sql` | string | **是** | — | 仅 `SELECT` / `WITH` |
| | `max_rows` | integer | 否 | `200` | 1~1000 |

> 入参 schema 由 MCP 依据函数签名**自动生成**，宿主侧看到的就是标准 JSON Schema
> （`{"type":"object","properties":{...},"required":[...]}`）。除标 **是** 的参数外其余可省略。

---

## 7. 统一返回结构

所有工具返回**同一套 JSON 信封**，easyagent 前台只需按 `shape` 分支渲染：

```json
{
  "shape": "table+chart",          // text | table | table+chart
  "title": "各团队本月累计损益排名（万元）",
  "summary": "共 8 个团队。本月累计损益最高的为「贵金属及商品交易团队」……",
  "unit": "万元",
  "stat_date": "2026-10-09",
  "table": { "columns": ["排名","团队","..."], "rows": [[1,"贵金属及商品交易团队",...]] },
  "chart": { "title": {...}, "series": [...] },   // ECharts option，可直接 setOption
  "notes": ["正值红色、负值绿色（国内惯例）。", "..."]
}
```

渲染方式按宿主分两种：

| `shape` | WorkBuddy（当前宿主） | 自建前端（如 easyagent） |
|---|---|---|
| `text` | 只输出 `summary` 结论文字 | 同左 |
| `table` | 结论 + **Markdown 表格** | 结论 + 表格组件 |
| `table+chart` | 结论 → **用可视化能力在本轮内联渲染图表** → Markdown 表格 | 结论 + `chart`（ECharts `setOption`）+ 表格 |

> **WorkBuddy 对话区没有 ECharts 运行时**，不能直接 `setOption`。正确做法：以 `table.rows` 为数据、
> 参考 `chart.series[].type` 选图型（bar → 条形/柱状，pie → 饼图，line → 折线），由模型在本轮对话里
> 内联渲染；配色仍遵循国内惯例（**正红负绿**），主色可用中信红 `#C8102E`。
> 详见 Skill `jmfx-analysis` 第三节。

**协议层形态（重要）**：MCP 的工具返回类型是 `text`——服务端把整个信封序列化成一个
**JSON 字符串**，宿主实际收到的是：

```json
{ "content": [ { "type": "text", "text": "{\"shape\":\"table+chart\", ...}" } ], "isError": false }
```

因此**宿主侧需要先对 `text` 做一次 `JSON.parse`**，拿到对象后再按 `shape` 分支渲染。
（WorkBuddy 内置该处理，无需额外开发；自建前端需自行实现。）
（若后续想把出参升级为 MCP 结构化输出 `structuredContent`，可按需改造。）

---

## 8. 口径约定（重要）

**底层表金额已全部统一为「元」**（2026-10-13 改造）：

| 数据表 | 原始报表单位 | 库内存储单位 |
|---|---|---|
| `fact_portfolio_pl`（图1、图3） | 元 | **元** |
| `fact_indicator`（图2） | 亿元 | **元**（入库时 ×1e8） |

展示层用视图折算，**本服务输出单位不变**：

| 视图 / 列 | 折算方式 | 单位 |
|---|---|---|
| `v_team_pl` / `v_portfolio_pl` / `v_total_pl` / `v_team_rank_month` 的 `*_wan` | 元 ÷ 10000 | 万元 |
| `v_indicator_std` 的 `*_wan` | 元 ÷ 10000 | 万元 |
| `v_indicator_std` 的 `*_yi` | 元 ÷ 1e8 | 亿元 |
| `v_liability`（取自 `*_wan`） | 元 ÷ 10000 | 万元 |

- 换算关系：**1 亿元 = 10000 万元 = 1e8 元**。
- 底层「元」原值原样保留在无后缀的同名列中（如 `current_period`），便于核对。
- 原表中「-」存为 `NULL`，工具会以「无数据」表述，不会当作 0。
- 配色遵循国内惯例：**正值红色、负值绿色**。
- 指标子项因亿元四舍五入，加总可能与合计略有差异（工具会在 `notes` 中提示）。

---

## 9. 安全设计

1. **只读账号**：默认 `jmfx_ro`，数据库层面禁写。
2. **兜底 SQL 三重校验**：仅允许 `SELECT/WITH` → 禁止关键字黑名单 → 表白名单（9 个允许对象）。
3. **强制 LIMIT**：未写 `LIMIT` 时自动追加，上限 1000 行。
4. **超时保护**：连接 5s、读 15s。
5. **异常兜底**：任何工具异常都会返回结构化错误信息，不会打断宿主会话。

## 10. 测试

### 10.1 离线自检（不依赖 mcp 包、不依赖宿主）

直接校验 9 个工具的查询逻辑、单位换算、NULL 处理、越权拦截：

```bash
"$PY" jmfx_mcp_server.py --selftest        # 期望 13/13 通过
```

### 10.2 端到端回归（走完整 MCP 协议）

按 `~/.workbuddy/mcp.json` 的**真实配置**拉起服务，握手 → 列工具 → 逐条调用并断言：

```bash
"$PY" test_mcp.py            # 期望 21/21 通过；退出码 0
"$PY" test_mcp.py --bare     # 不读 mcp.json，用当前解释器直跑
```

覆盖：9 个工具各调用一次 + `top_n` 摘要措辞 + **组合明细全量/截断口径一致性** + 越权拦截（DELETE / mysql.user）。

> 其中「组合明细口径」3 项是回归防护：`get_portfolio_pl` 曾在 `top_n` 生效时
> 用**截断后**的行统计盈亏个数，导致亏损段整段丢失（摘要以「；」断尾）、
> 条数较大时还会把「前 N 名里有 X 个盈利」误报成「一共 X 个盈利」。现已改为基于全量统计。

### 10.3 宿主内冒烟（最终验收）

重启 WorkBuddy → 连接器管理点**信任** → 新开会话问实测问题：

| 测试问 | 期望 |
|---|---|
| 哪个团队本月累计损益最高？ | 表格 + 条形图，首为贵金属及商品交易团队 5,940.29 万元 |
| 非息收入主要是谁贡献的？ | 饼图 + 表格，投资管理中心 142.46 亿元占 61.9% |
| 负债规模比去年是涨是跌？ | 双柱图，合计 21,477.00 亿元 / 上年末 22,987.00 亿元 |
| 外汇交易团队是哪个组合拖累的？ | 表格 + 条形图，共 17 个组合、6 盈 11 亏，亏损最大 TTFX -4,297.54 万元 |
| 随便编一个不存在的团队问损益 | 如实回「未查询到」，**不得**编造数字 |

## 11. 命令行通道（`jmfx_cli.py`）

> **当前状态（2026-10-13）**：MCP 已通过客户端「连接器管理 → 自定义连接器」界面成功启用，
> 9 个工具实测全部可用。本节通道保留为**兜底**——万一某次会话看不到 MCP 工具时改走它，取数结果完全一致。

**为什么需要它**：WorkBuddy 桌面端 5.7.x 以**严格 MCP 模式**启动 Agent 运行时。启动日志中可见：

```
[Startup] McpConfigManager.list: scope=user blocked by strict MCP config
[Startup] McpConfigManager.list: scope=project blocked by strict MCP config
[Startup] McpConfigManager.list: scope=plugin blocked by strict MCP config
[Startup] McpConfigManager.list: scope=local blocked by strict MCP config
```

严格模式会屏蔽 `USER / PROJECT / LOCAL / PLUGIN` 四个**文件作用域**（源码 `isStrictMcpScopeBlocked`），
因此**手写 `~/.workbuddy/mcp.json` 在该客户端不生效，也不会出现在「自定义连接器」列表中**。
在这类宿主里，自定义 MCP 只能通过客户端界面添加（走应用自身注册通道）。

`jmfx_cli.py` 让同一套查询逻辑绕过 MCP 通道，用命令行直接调用，**输出内容与 MCP 工具完全一致**。

```bash
"$PY" jmfx_cli.py list                                        # 列出工具
"$PY" jmfx_cli.py rank_teams --metric month_cum_pl --text     # 文本输出
"$PY" jmfx_cli.py rank_teams --metric month_cum_pl            # JSON 信封
"$PY" jmfx_cli.py get_team_pl --team_name 外汇交易团队 --text
"$PY" jmfx_cli.py run_readonly_sql --sql "SELECT * FROM v_team_pl LIMIT 5" --text
```

- 工具名 = MCP 工具名去掉 `_tool` 后缀，参数与类型完全一致。
- `--text` 人类可读；`--raw` 只输出表格数据行；默认输出完整 JSON 信封。
- 退出码 0 成功 / 1 失败。
- 安全护栏与 MCP 一致（只读账号 + 白名单 + 强制 LIMIT），因为它调用的是同一批函数。
