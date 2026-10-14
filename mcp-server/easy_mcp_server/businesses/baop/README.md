# 金市经营分析 · MCP Server（`baop-analysis`）

把 MySQL `baop` 库中**三张 FPP 业务表**的数据，以**业务语义化工具**的形式暴露给支持 MCP 的宿主，
供自然语言问答调用。每个工具直接返回**文字结论 + 表格 + ECharts option**。

| 项 | 值 |
|---|---|
| Server 名 | `baop-analysis` |
| 脚本 | `baop_mcp_server.py` |
| 数据库 | `baop` |
| 数据底座 | `fpp_fin_mkt_bizline_income_sum_t1`（营收非息） / `fpp_core_cap_rsv_t1`（资本公积） / `fpp_whi_lib_biz_situ_t1`（负债业务） |
| 单位口径 | 三表底层统一 **万元**，工具输出 **亿元** |
| 工具数 | 9 |
| 配套 Skill | `baop-analysis`（项目级） |

> **当前宿主：WorkBuddy**。自定义 MCP 须在「连接器管理 → 自定义连接器」里添加（见第 5 节）。
> easyagent 的接法保留在第 5 节末尾备用。

---

## 1. 目录与依赖

```
baop_mcp/
├── baop_mcp_server.py     # MCP Server：baop 库（9 工具）
├── baop_cli.py            # 命令行兜底通道（MCP 不可用时降级）
├── test_baop_mcp.py       # 端到端回归（48 项，断言全部动态化）
├── requirements.txt       # mcp / pymysql
└── README.md              # 本文件
```

> 上面 5 个文件**都是必需资产**：`baop_mcp_server.py` 是宿主连接的靶子，`baop_cli.py` 是
> MCP 不可用时的降级通道（Skill 内已引用），`test_baop_mcp.py` 是回归，`requirements.txt`
> 是给别人部署时唯一的环境说明。只有 `__pycache__/` 可以删（Python 会自动重建）。

**配套 Skill 的位置**（不在 `baop_mcp/` 内，随项目走）：

| Skill | 路径 | 作用范围 |
|---|---|---|
| `baop-analysis` | `E:\中信银行\经营分析\.workbuddy\skills\baop-analysis\SKILL.md` | **项目级**（仅本工作区加载） |

> 迁移到别的机器：把 `.workbuddy\skills\` 整个目录一起拷过去，并在**该目录作为工作区**打开客户端，
> Skill 才会被加载。只拷 `baop_mcp/` 和 `mysql/` 拿不到 Skill。

**数据脚本**在 `mysql/`：

```
mysql/
├── baop_tables.sql                  # 建库 + 建 3 张表（含 RANGE 分区）
├── baop_testdata.sql                # 全量 INSERT
├── export_baop_testdata.py          # 从库导出数据 → baop_testdata.sql
├── gen_baop_testdata.py             # 生成测试数据（金额列按列名折算万元）
├── fix_baop_unit_to_wan.py          # 单位修正（幂等 + 备份 + 逐列对账）
└── baop_*backup*.sql                # 修正前备份
```

依赖已装在**独立虚拟环境**（避免与其他项目冲突）：

```
C:\Users\issuser\.workbuddy\binaries\python\envs\baop-mcp\Scripts\python.exe
```

> venv 目录名 `baop-mcp` 与本 Server 同名（**2026-10-14 由历史名 `jmfx-mcp` 重命名而来**，
> 已实测重命名后解释器、依赖与 MCP 握手全部正常）。
> ⚠️ 此后**再改名会导致已注册连接器的 `command` 失效**，如无必要不要动。
>
> **重命名 venv 的完整清单**（本次已照此执行）：除目录本身外，`Scripts/*.exe`（启动器尾部
> shebang）、`Scripts/activate*`、`pyvenv.cfg`、`Scripts/__pycache__/*.pyc` 里也写死了旧绝对路径。
> `pip.exe` 尤其会直接失效（无输出）。因 `jmfx-mcp` 与 `baop-mcp` **等长（各 8 字节）**，
> 可直接做等长字节替换（文件大小不变，安全）；**若新旧名不等长则必须改用重建 venv 的方式**。

如需重建：

```bash
python -m venv envs/baop-mcp
envs/baop-baop_mcp/Scripts/pip.exe install -r baop_mcp/requirements.txt
```

> **SDK 版本兼容**：官方 Python SDK 在 **2.0** 起把 `FastMCP` 改名为 `MCPServer`
> （`mcp.server.mcpserver`），且 `host/port` 从构造参数改为 `run()` 参数。
> 本服务已做**双版本兼容**（自动探测 1.x / 2.x），当前环境为 `mcp 2.3.0`。
> 无需因 SDK 升级改代码。

---

## 2. 数据库前置

`baop` 库是**三张 FPP 业务表**，交付脚本拆成两个（不含任何 Python 依赖）：

| 顺序 | 脚本 | 内容 |
|---|---|---|
| 1 | `mysql/baop_tables.sql` | 建库 + 建 3 张表（含 13 个 RANGE 分区、34 个金额列注释标注「（万元）」） |
| 2 | `mysql/baop_testdata.sql` | 全量 INSERT（15 + 54 + 255 行，**由 `export_baop_testdata.py` 从库导出**） |

```bash
mysql -u root -p --default-character-set=utf8mb4 < mysql/baop_tables.sql
mysql -u root -p --default-character-set=utf8mb4 < mysql/baop_testdata.sql
```

导入后会创建只读账号 `baop_ro`（**仅 SELECT**，限定 `baop` 库）。建账号的语句见
`mysql/fix_baop_unit_to_wan.py` 的部署说明或直接 `CREATE USER` + `GRANT SELECT`。

> **单位**：三张表底层统一**万元**（最终口径，2026-10-14 业务方确认，**勿再变更**）。
> 若曾误改为「元」，用 `mysql/fix_baop_unit_to_wan.py --apply` 修正（幂等，含全量备份与逐列对账）。
> 不要手写 UPDATE——34 个金额列与 22 个百分比列必须严格区分。

---

## 3. 连接配置（环境变量）

代码内置默认值仅供**本机开发**；部署到其他环境时用环境变量覆盖即可，**无需改代码**。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BAOP_HOST` | `127.0.0.1` | |
| `BAOP_PORT` | `3306` | |
| `BAOP_USER` | `baop_ro` | 只读账号 |
| `BAOP_PASSWORD` | 内置（仅供本机开发） | 生产务必显式设置 |
| `BAOP_DB` | `baop` | 三张表所在库 |

> ⚠️ **变量在进程启动时读取**（`CFG` 是模块级常量）。运行中修改环境变量无效，
> 改完必须重启进程；MCP 场景下需在「连接器管理」里关闭再启用 + 新开会话。

**示例：连服务器库（已实测连通）**

```bash
export BAOP_HOST=183.196.130.56
export BAOP_PORT=22133
export BAOP_USER=root
export BAOP_PASSWORD='******'      # 含特殊字符务必用单引号
export BAOP_DB=baop                # 三张表在 baop 库，不是 fmqt
```

> 密码含 `#`，在 shell 中必须用**单引号**包裹，否则 `#` 之后会被当注释截断。
> 现成配置文件见 `baop-analysis.server.mcp.json`（本机 MCP + 服务器库）。

---

## 4. 启动方式

```bash
PY="C:\Users\issuser\.workbuddy\binaries\python\envs\baop-mcp\Scripts\python.exe"

# ① stdio —— 宿主与本服务同机（**WorkBuddy 走这条**；进程由宿主按需拉起，无需手动启动）
"$PY" baop_mcp_server.py

# ② SSE —— 宿主在另一台机器 / 需要 HTTP 长连接（推荐给 easyagent 前台）
"$PY" baop_mcp_server.py --transport sse --port 8766

# ③ Streamable HTTP —— 新版 MCP 传输
"$PY" baop_mcp_server.py --transport streamable-http --port 8766

# ④ 自检（不启动服务，只跑一遍所有查询，用于排错）
"$PY" baop_mcp_server.py --selftest
```

SSE 启动后地址：`http://<服务器IP>:8766/sse`（默认只监听 `127.0.0.1`，
跨机访问请把 `--host` 改为 `0.0.0.0` 并放通端口）。

---

## 5. 在宿主里配置

### WorkBuddy（**5.7.7 起必须走「连接器管理」界面**）

> ⚠️ **旧方法已失效**：直接编辑 `~/.workbuddy/mcp.json` 在桌面端 **5.7.7 的严格模式下不生效**
> （会报 `scope=user/project/plugin/local blocked by strict MCP config`）。
> 手写 `mcp.json` 仅在 5.7.7 之前的版本可用，**现存文件里的配置也不再被读取**。
>
> **正确做法**：打开「连接器管理」→ **自定义连接器** → 添加，把下表三项填进去。

| 项 | 值 |
|---|---|
| ① command | `C:\Users\issuser\.workbuddy\binaries\python\envs\baop-mcp\Scripts\python.exe` |
| ② args | `E:\中信银行\经营分析\baop_mcp\baop_mcp_server.py` |
| ③ env | `BAOP_HOST/PORT/USER/PASSWORD/DB`（见下） |
| ④ 类型 | stdio |

**环境变量**（迁移到其他机器时必填）：

```
BAOP_HOST=127.0.0.1
BAOP_PORT=3306
BAOP_USER=baop_ro
BAOP_PASSWORD=Baop_RO@2026
BAOP_DB=baop
```

> 代码内已内置上述默认值，本机部署时 **env 可以留空**；换机器/换密码才需要显式填。

**启用步骤（缺一步都不生效）**

| 步骤 | 操作 |
|---|---|
| 1 | 「连接器管理 → 自定义连接器」添加，填 ①②③，保持 JSON 合法（引号成对、无尾随逗号） |
| 2 | 保存后在列表里点 **信任**（未信任是灰点，AI 调不动） |
| 3 | **新开一个会话**，用自然语言提问验证（如"哪个中心营业净收入最高"） |
| 4 | 改了 `baop_mcp_server.py` 后：**关闭再启用该连接器**（或重启客户端）+ **新开会话** —— MCP 进程不会热更新 |

**Windows 注意**：`command` 必须写 Python 解释器的**绝对路径**，不能写 `python` 或相对路径，否则启动失败亮红灯。

### 旧版 WorkBuddy（< 5.7.7）配置文件方式（**留档，勿用于 5.7.7+**）

> 文件是本机的 **`~/.workbuddy/mcp.json`**，不是 `~/.workbuddy/.mcp.json`（后者是内部聚合文件，不要改）。
> 已存在则**合并进 `mcpServers`，不要覆盖**。

```json
{
  "mcpServers": {
    "baop-analysis": {
      "command": "C:\\Users\\issuser\\.workbuddy\\binaries\\python\\envs\\baop-mcp\\Scripts\\python.exe",
      "args": ["E:\\中信银行\\经营分析\\baop_mcp\\baop_mcp_server.py"],
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "BAOP_HOST": "127.0.0.1",
        "BAOP_PORT": "3306",
        "BAOP_USER": "baop_ro",
        "BAOP_PASSWORD": "Baop_RO@2026",
        "BAOP_DB": "baop"
      }
    }
  }
}
```

### easyagent（备用）

按你平台的 MCP 配置方式二选一：

- **本机 stdio**：命令填上面的 python 路径，参数填 `baop_mcp_server.py`，环境变量同上；
- **远程 SSE**：先启动 `--transport sse --host 0.0.0.0 --port 8766`，
  再在 easyagent 里填 SSE 地址 `http://<服务器IP>:8766/sse`。

---

## 6. 工具清单（9 个）

| 工具 | 用途 | 关键参数 | 返回形态 |
|---|---|---|---|
| `list_dimensions_tool` | 有哪些中心/业务线/负债大类/期次/指标/元数据 | `dimension`（`all`/`center`/`biz_line`/`lib_cls`/`prod`/`cur`/`period`/`metric`/`meta`） | 文字 + 表格 |
| `get_income_overview_tool` | 收入总览（营业净收入 / 非息 / 息差） | `period`、`top_n` | 文字 + 表格 + 条形图 |
| `rank_income_tool` | 收入排名（T3 专表） | `metric`、`by=core\|biz_line`、`order`、`top_n` | 表格 + **横向条形图** |
| `get_income_detail_tool` | 某中心/业务线的收入明细 | `core` 或 `biz_line` | 表格 |
| `get_capital_reserve_tool` | 资本公积（可供出售账户） | `core`、`basis` | 表格 + **柱状图** |
| `get_liability_overview_tool` | 负债规模与构成 | `lib_cls`、`cur`、`basis` | 表格 + **饼图/柱图** |
| `rank_liability_products_tool` | 负债产品排名 | `lib_cls`、`metric`、`top_n` | 表格 + 条形图 |
| `compare_periods_tool` | 多期趋势对比 | `object=income\|liability\|capital`、`keys`、`n_periods` | 表格 + **折线图** |
| `run_readonly_sql_tool` | 只读 SQL 兜底（7 条护栏） | `sql` | 表格 |

**参数取值**

- `metric`（⚠️ **每个指标口径不同，勿混用**）：
  `net_opr_incom`(营业净收入，默认) / `not_int_incom`(非息收入) / `net_int`(息差) /
  `plan`(营业净收入-经营计划) / `comp_rate`(**营业净收入**-计划完成率) /
  `tm_prog`(**息差**-序时进度) / `curr_week`(营业净收入-当周)
  > 库中**只有**「营业净收入完成率」与「息差序时进度」，**没有营业净收入口径的序时进度**。
- `basis`（对比基准＝**预计算列选择器**，不是时间算术）——**两张表键名不同**：

  | 表 | 可用 basis |
  |---|---|
  | 资本公积 T1 | `none`(默认) / `yr_bgn` / `last_mth`(**较上月末**) / `last_fri` / `last_day` / `last_quar` / `last_year_dt` |
  | 负债 T2 | `none`(默认) / `yr_bgn` / `last_mth`(较上月) / `last_mth_end`(**较上月末**) / `last_fri` / `avg_yr_bgn` / `avg_last_mth` / `cost_yr_bgn` / `cost_last_mth` / `cost_last_fri` |

  > ⚠️「较上月末」在 T1 是 `last_mth`、在 T2 是 `last_mth_end`，传错报 `NOT_FOUND`。
  > ⚠️ `cost_*` 三个基准输出**百分点**（成本率差值），其余基准输出**亿元**。
- `object`：`income`(默认) / `liability` / `capital`

> 入参 schema 由 MCP 依据函数签名**自动生成**，宿主侧看到的就是标准 JSON Schema
> （`{"type":"object","properties":{...},"required":[...]}`）。

**Server 级 `INSTRUCTIONS`（随连接下发，模型最先读到）**：`baop_mcp_server.py:1414`。
内含「问题 → 工具速查表」「必须先取码值」「能力边界（哪些指标库里没有）」「派生指标由上层计算」四段，
是提升工具命中率的主要抓手；改它**必须**重连 MCP（见 §5）。

**`rank_income_tool` 摘要口径**：降序时摘要依次给出「最高 / **次高 + 领先差距** / 末位」，
存在负值时报「其中 N 个为**负值**」——即"领先第二名多少""有没有中心亏损"可**直答**，无需翻表格。
`top_n` 只影响展示行数，统计口径始终是全量（摘要同时给「共 N 个」与展示条数）。

**单位**：三张源表底层统一**万元**（最终口径，已锁定），工具统一输出**亿元**（万元 ÷ 1e4）。
百分比列（占比 / 成本率 / 完成率 / 序时进度）不做折算；**唯例外：`cost_*` 对比列单位为百分点，也不折算**。

**返回信封**：`shape | title | summary | unit | stat_date | table | chart | notes`，
另加 4 个状态字段——

| 字段 | 含义 |
|---|---|
| `period_resolved` | 实际使用的期次 |
| `resolved` | 码值标准化结果（口语 → 库内标准值） |
| `candidates` | 歧义/零命中的候选清单，非空时上层**必须反问** |
| `result_state` | `OK` / `ZERO` / `NULL` / `NOT_FOUND` / `AMBIGUOUS` |

> **歧义不由 MCP 反问**：MCP 无法反问，遇到歧义返回 `table: null` + `candidates`，
> 由上层（Agent/Skill）发起第二轮澄清。这条是硬边界。

---

## 7. 统一返回结构

所有工具返回**同一套 JSON 信封**，前端只需按 `shape` 分支渲染：

```json
{
  "shape": "table+chart",          // text | table | table+chart
  "title": "各中心收入总览（亿元）",
  "summary": "数据期次 2026-10-14：全部门营业净收入合计 338.21 亿元……",
  "unit": "亿元",
  "stat_date": "2026-10-14",
  "table": { "columns": ["中心","营业净收入（亿元）","..."], "rows": [["资金管理中心",80.85,"..."]] },
  "chart": { "title": {...}, "series": [...] },   // ECharts option
  "notes": ["单位口径：源表为万元，已统一折算为亿元展示。", "..."],
  "period_resolved": "2026-10-14",
  "resolved": {},
  "candidates": null,
  "result_state": "OK"
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

**协议层形态（重要）**：MCP 的工具返回类型是 `text`——服务端把整个信封序列化成一个
**JSON 字符串**，宿主实际收到的是：

```json
{ "content": [ { "type": "text", "text": "{\"shape\":\"table+chart\", ...}" } ], "isError": false }
```

因此**宿主侧需要先对 `text` 做一次 `JSON.parse`**，拿到对象后再按 `shape` 分支渲染。
（WorkBuddy 内置该处理，无需额外开发；自建前端需自行实现。）

---

## 8. 口径约定（重要）

### 三张表底层统一「万元」（**最终口径，已锁定**）

| 数据表 | 底层存储单位 | 工具输出单位 |
|---|---|---|
| `fpp_fin_mkt_bizline_income_sum_t1`（金市营收非息） | **万元** | 亿元 |
| `fpp_core_cap_rsv_t1`（资本公积） | **万元** | 亿元 |
| `fpp_whi_lib_biz_situ_t1`（负债业务） | **万元** | 亿元 |

- 换算：**万元 ÷ 1e4 = 亿元**（`_to_yi` 内固定实现）；**1 亿元 = 10000 万元**。
- **34 个金额列**（T1 15 / T2 10 / T3 9）参与换算；**22 个百分比列**
  （占比 / 成本率 / 完成率 / 序时进度 / 增速 / 较上月末率）**绝不参与**。
- 口径来源：2026-10-14 业务方**最终确认**。此前一天内曾往返一次（误改为「元」），
  已用 `mysql/fix_baop_unit_to_wan.py` 修正并锁定，**后续不得再变更**。
- ⚠️ **改口径必须同时 grep 常量名（`UNIT_WAN`）与数值字面量（`/1e4`、`1e8`）**——
  历史上两处都漏过（`_COMPARE` 字典写成 `, UNIT_WAN,`；质量校验里的字面量 `/1e4`）。
- ⚠️ **`_to_yi` 只能用于金额列**。T2 的 `AVG_COST_RATE_VS_*`（成本率对比）是**百分点**，
  曾误套 `_to_yi` 被 ÷1e4 抹成 `0.00`（1.91 个百分点 → 0.00），并配上错误列名「（亿元）」。
  已加常量 `RATE_BASIS_COLS` 分流，列名改标「（百分点）」，并加 3 条回归断言防复发。
  **新增 basis 列时，先确认它是金额还是比率。**
- 零值（`0`）与「无数据（`NULL`）」是**两种不同状态**，不可混用。
- 配色遵循国内惯例：**正值红色、负值绿色**。

### 期次以库为准，不以时钟为准

- 未指定期次 → `MAX(DATA_DT)`；所问日期无数据 → 回落最近可用期次并在 `notes` 说明。
- 相对词（"本月/最新"→最新期；"上月/环比"→按最新期算上一自然月末）由 `_resolve_period` 解析。
- 超范围 → `result_state=NOT_FOUND` + 给出可用期次清单。
- 源数据为**日频**（每日一期），**库里新增期次后无需改任何代码或测试**。

---

## 9. 安全设计

1. **只读账号**：`baop_ro`，数据库层面仅授 `SELECT`，限定 `baop` 库。
2. **兜底 SQL 三重校验**：仅允许 `SELECT/WITH` → 禁止关键字黑名单 → 表白名单（仅 3 张事实表）。
3. **强制 LIMIT**：未写 `LIMIT` 时自动追加，上限 1000 行。
4. **超时保护**：连接 5s、读 15s。
5. **异常兜底**：任何工具异常都会返回结构化错误信息，不会打断宿主会话。

---

## 10. 测试

### 10.1 离线自检（不依赖 mcp 包、不依赖宿主）

直接校验 9 个工具的查询逻辑、单位换算、NULL 处理、越权拦截：

```bash
PY="C:/Users/issuser/.workbuddy/binaries/python/envs/baop-baop_mcp/Scripts/python.exe"
"$PY" baop_mcp/baop_mcp_server.py --selftest        # 期望 23/23 通过
```

### 10.2 端到端回归（走完整 MCP 协议）

按真实连接参数拉起服务，握手 → 列工具 → 逐条调用并断言：

```bash
"$PY" baop_mcp/test_baop_mcp.py --bare     # 期望 48/48 通过；退出码 0
```

> **断言纪律（2026-10-14 改造）：全部动态化，不写死任何业务数值/期次。**
> 期次取自 `list_dimensions_tool` 返回的 `period_resolved`；维度条数取自返回的「维度」行；
> 金额断言一律走 **summary ↔ 表格 自洽**（如「负债合计 = 三大构成之和」）或量级校验
> （`0<v<1e5`，可抓住漏 ÷1e4）。**库里新增期次后回归仍应全绿，无需改测试。**
> 新增断言时务必照此办理，勿再引入数据快照式硬编码。

覆盖：9 个工具各调用一次 + 期次回显与跨工具一致 + 单位自洽 + `top_n` 只影响展示行数 +
码值标准化（口语→标准值）与回显 + 仲裁三出口（零命中/歧义）+ 多期降级声明 +
超范围报边界（动态构造未来日期）+ 越权拦截（DELETE / mysql.user）。

### 10.3 宿主内冒烟（最终验收）

注册连接器 → 点**信任** → 新开会话问实测问题：

| 测试问 | 期望 |
|---|---|
| 哪个中心营业净收入最高？ | 表格 + 条形图，首位 资金管理中心 |
| 非息收入由哪些中心贡献？ | 表格（+ 占比，未给占比时按单项÷合计计算） |
| 资本公积现在多少？ | 人民币合计 **948.66 亿元**，最高资金管理中心 297.79 亿 |
| 负债规模多大？由什么构成？ | **848.24 亿元** = 同业负债 663.76 + 本币司库 104.79 + 外币司库 79.69 |
| 近几期的收入走势如何？ | 折线图，284.53 → 320.97 → 338.21 亿元 |
| 帮我把某个中心的数据删掉 | **拒绝**（只读账号 + 护栏），不得执行 |

> 上述数值为当前库实测值（2026-10-14 期次），仅用于冒烟核对；接入新数据后应随库变化。

---

## 11. 命令行通道（`baop_cli.py`）

> **当前状态（2026-10-14）**：`baop-analysis` **尚未注册**进「连接器管理」（参数见第 5 节，
> 注册后需新开会话）。本节通道保留为**兜底**——万一某次会话看不到 MCP 工具时改走它，取数结果完全一致。

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

`baop_cli.py` 让同一套查询逻辑绕过 MCP 通道，用命令行直接调用，**输出内容与 MCP 工具完全一致**。

```bash
"$PY" baop_cli.py list                                              # 列出 9 个工具
"$PY" baop_cli.py list_dimensions --dimension all --text            # 可查询范围与数据字典
"$PY" baop_cli.py get_income_overview --text                        # 收入总览
"$PY" baop_cli.py rank_income --metric not_int_incom --by core --text
"$PY" baop_cli.py compare_periods --object liability --text
"$PY" baop_cli.py run_readonly_sql --sql "SELECT * FROM fpp_whi_lib_biz_situ_t1 LIMIT 3" --text
```

- 工具名 = MCP 工具名去掉 `_tool` 后缀，参数与类型完全一致。
- `--text` 人类可读；`--raw` 只输出表格数据行；默认输出完整 JSON 信封。
- 退出码 0 成功 / 1 失败。
- 安全护栏与 MCP 一致（只读账号 + 三表白名单 + 强制 LIMIT），因为它调用的是同一批函数。
