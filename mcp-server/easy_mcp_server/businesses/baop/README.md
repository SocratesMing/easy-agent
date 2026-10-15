# 金融市场部经营分析（baop）MCP 业务包

金融市场部经营分析的只读取数通道，**作为 mcp-server 的一个业务包**运行
（挂载路径 `/mcp/baop/`），不再以独立 stdio 服务方式启动。

覆盖三块业务：①营业净收入/非息/息差（T3，中心×业务线）；②资本公积变动（T1，按中心）；
③负债规模（T2，负债大类×业务类别×币种）。

---

## 1. 文件结构

```
baop/
├── __init__.py     包入口（build 工厂）
├── baop.py         领域层：表/维度/指标字典、单位折算、ECharts option、
│                   返回信封、期次解析、码值标准化、只读 SQL 护栏、9 个业务函数
├── server.py       工具层：INSTRUCTIONS + 10 个工具注册（async 薄包装）
└── README.md       本文件
```

领域层与工具层分离，和其它业务包（`gold_monthly`、`fx_barometer` 等）结构一致。
`baop.py` 自带数据库连接，**不经由**本服务统一的 querykit 通道。

---

## 2. 数据源与连接

三张源表（底层单位统一为「万元」，2026-10-14 业务方确认）：

| 表 | 业务 |
|---|---|
| `fpp_fin_mkt_bizline_income_sum_t1` | T3 损益（中心 × 业务线） |
| `fpp_core_cap_rsv_t1` | T1 资本公积（按中心） |
| `fpp_whi_lib_biz_situ_t1` | T2 负债（负债大类 × 业务类别 × 币种） |

**这是独立的 baop 库**，与其它业务共用的 `MYSQL_*` 不是同一个数据源。
连接配置读环境变量（均有默认值，**函数式读取，改完下次查询即生效，无需重启进程**）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BAOP_HOST` | `127.0.0.1` | |
| `BAOP_PORT` | `3306` | |
| `BAOP_USER` | `baop_ro` | 只读账号 |
| `BAOP_PASSWORD` | 空 | 生产务必显式设置 |
| `BAOP_DB` | `baop` | 三张表所在库 |

> ⚠️ `db` 必须是 `baop`：三张 `fpp_*` 表建在 baop 库内，表名均为裸表名、不带库前缀，
> 连错库必然 `table doesn't exist`。
>
> 连接是**每次查询新建**（独立数据源，不与其它业务共用连接池），因此显式设了
> `connect_timeout=5` / `read_timeout=8`，避免拖垮请求。

内网部署时把上表五个变量写进 `.env` 即可，**不要改代码**。

---

## 3. 部署

baop 只是 `businesses/` 下的一个目录，`app.py` 会自动发现，**不需要单独注册**：

```bash
cp -a mcp-server mcp-server.bak.$(date +%Y%m%d)
tar -xzf mcp-server-baop.tar.gz      # 只含 easy_mcp_server/businesses/baop/
# 在 .env 里填 BAOP_HOST/PORT/USER/PASSWORD/DB
# 重启 mcp-server
```

起来后 `GET /health` 应能看到 `baop`；鉴权沿用本服务统一的 Bearer ApiKey 中间件。

依赖只有 `mcp` 与 `pymysql`，mcp-server 本身已安装，无需额外动作。

---

## 4. 工具清单（10 个）

| 工具 | 用途 |
|---|---|
| `describe_baop_schema` | 数据字典：表、维度、指标、单位、全部口径与陷阱 |
| `list_dimensions` | 可查询范围与数据字典（中心/业务线/负债大类/业务类别/币种/期次/指标） |
| `get_income_overview` | 各中心营业净收入 / 非息 / 息差对比 |
| `rank_income` | 收入排名（按中心或业务线，7 个指标可选） |
| `get_income_detail` | 单中心或单业务线明细 |
| `get_capital_reserve` | 资本公积（含较上日/上周/上月末/较年初等预计算对比列） |
| `get_liability_overview` | 负债总览与构成，或某负债大类明细 |
| `rank_liability_products` | 负债产品排名（余额/占比/年均余额/成本率） |
| `compare_periods` | 多期趋势对比（收入/负债/资本公积，2~12 期） |
| `run_readonly_sql` | 只读 SQL 兜底（7 条护栏，仅三张表白名单） |

返回值是统一信封：`shape / title / summary / unit / stat_date / table / chart /
notes / period_resolved / resolved / candidates / result_state`。

- `table` 是已折算为「亿元」的中文表格，可直接渲染；
- `chart` 是 **ECharts option**，前端可直接用；
- `summary` 是人读的中文结论；
- `result_state ∈ OK / ZERO / NULL / NOT_FOUND / AMBIGUOUS`。

---

## 5. 业务口径速查

**期次**
- 源数据为日频快照（每日一期）；未指定期次取最新一期，并在 `summary` 首句回显；
- 所问日期无数据时回落到最近可用期次，并在 `notes` 明确声明；
- 相对词（本月/上月/环比/同比/较年初…）按关键词解析，解析结果写在 `period_resolved`。

**码值标准化（四级流水线 + 仲裁三出口）**
维度参数可用口语（「投资中心」「外币」「外汇」「贵金属」），内部依次做
归一化 → 别名映射 → 精确匹配 → 模糊召回打分，命中的规范值回显在 `resolved`。

三出口：唯一命中返回数据；**多候选**返回 `AMBIGUOUS` + `candidates`（须反问，
此时不返回数据）；**零命中**返回 `NOT_FOUND` + 候选池。

**单位**
- 三张源表底层统一为「万元」，本服务统一折算为「亿元」展示（1 亿元 = 10000 万元）；
- 完成率 / 序时进度 / 占比 / 成本率是百分比，**不做折算**。

**⚠️ 量纲陷阱**
`AVG_COST_RATE_VS_*` 是「成本率差值」，单位**百分点**，绝不能 ÷1e4；
其余 `*_VS_*` 是金额（万元），需折算亿元。分档标签也会跟着写成「个百分点」。

**零值 ≠ 无数据**
`ZERO` 是真实业务事实（如外币司库负债已全部退出），`NULL` 才是无数据，
两者不可混用，也不要表述成「减少 0 亿」。

**其它**
- 对比基准列（`*_VS_*`）是源表**预计算**的，直接读取，未做两期相减（口径与报表一致）；
- 「其中」类子项（如「其中：结算性存款」）**不计入合计**，明细加总会重复计算；
- 统计口径基于**全量**，展示条数是另一回事，两者不能混；
- 资本公积可为负值（浮亏），属正常业务现象。

---

## 6. 能力边界

库中只有上述三张表，**没有**净利润率、不良率/不良贷款率、客户数、人均产能、
存款付息率等指标；不做预测/外推；不做任何写操作（改计划、加数据一律拒绝）。

派生指标（占比、增速、计划缺口、跨指标比对）优先对语义化工具的返回结果自行计算
并注明；仅在取不到原始数据时才用 `run_readonly_sql`。
