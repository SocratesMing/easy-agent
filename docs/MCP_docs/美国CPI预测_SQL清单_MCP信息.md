# 美国CPI预测_SQL清单_MCP信息

> **文档性质**：美国 CPI 预测数据入库及 MCP 只读查询技术规范。本文定义数据库数据契约、7 个 MCP 工具、SQL、服务端处理、错误约定和验收要求；开发实现以本文为业务接口规范。

## 1. 系统范围

### 1.1 实现范围

- **数据库**：4 张业务表；不增加其他表。
- **MCP**：7 个只读工具；工具名、既有参数和字段名保持一致，参考 `CPI_MCP工具定义.json`。
- **数据输入**：读取已经生成的模型结果、机构预期、策略文本及官方历史工作簿；不触发预测模型、不重新生成策略、不修改前端。
- **数据库权限**：MCP 使用只读数据库账号；不暴露任意 SQL、写入或执行策略的工具。
- **数据库兼容性**：`groups_json`、`leaves_json`、`daily_json` 使用 `LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci`，存储**合法 JSON 数组文本**；服务端负责 JSON 解析。主查询不依赖 MySQL 8 专有语法。JDBC 5.1.35 是客户端驱动版本，不代表数据库服务器版本。

### 1.2 表及业务关系

| 表 | 内容 | 数据关联 |
|---|---|---|
| `fmqt_forecast_us_cpi_prediction_result` | 报告版本、组级/分项/逐日三套预测数组 | 每个完整三文件组合版本一行；主键 `report_id` |
| `fmqt_forecast_us_cpi_market_expectation` | 机构预测、来源提供的市场中位数 | `report_id` 关联报告，按源文件实际记录入库 |
| `fmqt_forecast_us_cpi_strategy_advice` | 策略建议全文 | `report_id` 关联报告；保存独立的策略月份 |
| `fmqt_forecast_us_cpi_official_history` | 官方 NSA 指数、SA 环比及来源 | 独立于报告；`observation_month` + `indicator_name` 为业务主键 |

### 1.3 输入文件及默认路径

| 用途 | 输入文件 | 非 Windows 默认路径 |
|---|---|---|
| 模型预测 | `model_results.json` | `/qts/research/us_cpi/result/model_results.json` |
| 市场预期 | `market_expectations.json` | `/qts/research/us_cpi/result/market_expectations.json` |
| 策略建议 | `strategy_advice.txt` | `/qts/research/us_cpi/result/strategy_advice.txt` |
| 官方 NSA 指数 | `CPI_nsa_monthly.xlsx` | `/qts/research/us_cpi/data/CPI_nsa_monthly.xlsx` |
| 官方 SA 环比 | `CPI_sa_mom.xlsx` | `/qts/research/us_cpi/data/CPI_sa_mom.xlsx` |

Windows 默认数据目录从落库模块目录上两级的 `domain/macro/index/oversea/us_cpi` 下读取 `data/`、`result/`；可通过 `US_CPI_DIR` 或既有命令行路径参数覆盖。官方工作簿第一行为中文指标名、第二行为来源指标 ID；使用后续数据行的已有缓存值，不调用 Excel 计算。NSA 与 SA 按**指标共同中文名 + 月份**外连接，不按列序或行序配对。

## 2. 存储数据契约

### 2.1 预测数组保留规则

| 列 | 内容 | 约束 |
|---|---|---|
| `groups_json` | 当前预测月各原始组名的 `main` 预测；总体、核心的 `main`/`far` 趋势 | 按源记录存储，不固定组数；不保证远期各月连续 |
| `leaves_json` | 当前预测月分项、方法、权重及同环比 | 不要求固定 26 条；缺项不补造 |
| `daily_json` | 当前目标月已有日期的总体、核心、食品、能源逐日预测 | 不补造缺失日期/组别，可能包含未来展示记录 |

趋势保留窗口以预测月及源文件远期月份的最晚月份为终点，向前覆盖 12 个自然月，实际返回有记录的月份。保留来源原始组名，例如“总体”“核心”“食品”“能源”“核心商品”“住房”“核心服务”；**不推断或改写分组名称**。

顶部同比与环比不保存 `baseline`；`baseline` 只属于逐日同比。模型结构化可信度不入库。`event_sources` 原样保存，策略正文不解析成新的模型可信度字段。

### 2.2 `groups_json[]` 对象

| 字段 | 类型/含义 |
|---|---|
| `record_type` | `main` 或 `far` |
| `forecast_month` | 该条预测月；月首日期 |
| `group_name` | 原始 CPI 组名 |
| `weight` | 占总体 CPI 权重；百分数数值 |
| `yoy` | NSA 同比涨幅；百分数数值 |
| `nsa_mom` | NSA 环比涨幅；百分数数值 |
| `sa_mom` | SA 环比涨幅；百分数数值 |

### 2.3 `leaves_json[]` 对象

| 字段 | 类型/含义 |
|---|---|
| `forecast_month` | 分项所属预测月；月首日期 |
| `item_name` | 分项原名 |
| `group_name` | 所属组名 |
| `weight` | 占总体 CPI 权重；百分数数值 |
| `method` | 分项预测方法原名 |
| `yoy` | NSA 同比涨幅；百分数数值 |
| `nsa_mom` | NSA 环比涨幅；百分数数值 |
| `sa_mom` | SA 环比涨幅；百分数数值 |

### 2.4 `daily_json[]` 对象

| 字段 | 类型/含义 |
|---|---|
| `target_month` | 逐日预测目标月 |
| `observation_date` | 逐日展示日期，**不是模型独立发布时间** |
| `group_name` | 总体/核心/食品/能源（以实际记录为准） |
| `yoy` | 当日同比预测；百分数数值 |
| `baseline` | 逐日同比月初惯性基线；百分数数值 |
| `correction` | 相对基线的累计同比修正；单位百分点 |
| `event_sources` | 来源原文，同一天可能在多个组重复保存 |
| `is_future_display` | 未来展示标识：`0` / `1` |

### 2.5 数值和空值

- `yoy`、`nsa_mom`、`sa_mom`、`weight`、机构预测：均返回百分号前数值；例如 `0.4` 代表 **0.4%**。
- `gap = model - market`、`daily.correction`：单位**百分点**；NSA 指数保持源指数基期。
- 数据库 `NULL` / JSON `null` 直接返回 `null`，不替换成 `0`；真实零值保留 `0`。
- 建议内部使用 `Decimal`；对外 JSON 数值仍应是数字，不用 `default=str` 将数值编码为字符串；不得输出 `NaN`、`Infinity`，不得在业务计算前无依据地四舍五入。

## 3. 数据库字段清单

> 字段类型是入库模块与 SQL 表定义的契约。实际建表与索引约束以配套 `database/美国CPI_DBeaver建表.sql` 或 `forecast_us_cpi_schema.sql` 为准；本文件不虚构未提供的索引、外键或唯一键 DDL。

### 3.1 `fmqt_forecast_us_cpi_prediction_result`

| 字段 | SQL 类型 | 含义 |
|---|---|---|
| `report_id` | `BIGINT UNSIGNED NOT NULL AUTO_INCREMENT` | 报告主键 |
| `bundle_sha256` | `CHAR(64) NOT NULL` | 三文件摘要与策略月份的组合摘要，用于去重 |
| `schema_version` | `INT NOT NULL` | 模型 JSON 结构版本 |
| `official_latest_month` | `DATE NOT NULL` | 模型运行时最新官方月 |
| `forecast_month` | `DATE NOT NULL` | 模型预测目标月 |
| `market_month` | `DATE NOT NULL` | 市场预期所属月 |
| `strategy_month` | `DATE NOT NULL` | 策略所属月 |
| `generated_at` | `DATETIME(6) NOT NULL` | 模型文件生成时间；保留源本地时间 |
| `model_sha256` | `CHAR(64) NOT NULL` | 模型文件字节摘要 |
| `market_sha256` | `CHAR(64) NOT NULL` | 机构预期文件字节摘要 |
| `strategy_sha256` | `CHAR(64) NOT NULL` | 策略文件字节摘要 |
| `groups_json` | `LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL` | 组级预测 JSON 数组文本 |
| `leaves_json` | `LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL` | 分项预测 JSON 数组文本 |
| `daily_json` | `LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL` | 逐日预测 JSON 数组文本 |
| `status` | `VARCHAR(16) NOT NULL` | `writing` → `ready` |
| `imported_at` | `DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)` | 入库时间 |

### 3.2 `fmqt_forecast_us_cpi_market_expectation`

| 字段 | SQL 类型 | 含义 |
|---|---|---|
| `report_id` | `BIGINT UNSIGNED NOT NULL` | 关联报告 |
| `record_key` | `CHAR(64) NOT NULL` | 业务键 SHA256 去重摘要 |
| `forecast_month` | `DATE NOT NULL` | 市场预测所属月 |
| `institution_name` | `VARCHAR(255) NOT NULL` | 机构或市场中位数名称 |
| `is_median` | `TINYINT(1) NOT NULL` | 是否为源数据中位数记录 |
| `headline_yoy` | `DECIMAL(18,8) NULL` | 总体同比预测 |
| `headline_mom` | `DECIMAL(18,8) NULL` | 总体 SA 环比预测 |
| `core_yoy` | `DECIMAL(18,8) NULL` | 核心同比预测 |
| `core_mom` | `DECIMAL(18,8) NULL` | 核心 SA 环比预测 |

### 3.3 `fmqt_forecast_us_cpi_strategy_advice`

| 字段 | SQL 类型 | 含义 |
|---|---|---|
| `report_id` | `BIGINT UNSIGNED NOT NULL` | 关联报告 |
| `record_key` | `CHAR(64) NOT NULL` | 业务键 SHA256 去重摘要 |
| `forecast_month` | `DATE NOT NULL` | 策略所属月 |
| `content` | `MEDIUMTEXT NOT NULL` | 策略全文，不改写 |

### 3.4 `fmqt_forecast_us_cpi_official_history`

| 字段 | SQL 类型 | 含义 |
|---|---|---|
| `observation_month` | `DATE NOT NULL` | 官方观测月份；月首日 |
| `indicator_name` | `VARCHAR(255) NOT NULL` | 归一的中文指标名称 |
| `nsa_series_id` | `VARCHAR(64) NULL` | NSA 来源指标 ID |
| `sa_series_id` | `VARCHAR(64) NULL` | SA 来源指标 ID |
| `nsa_index` | `DECIMAL(24,8) NULL` | NSA 指数当月值 |
| `sa_mom` | `DECIMAL(18,8) NULL` | 官方 SA 月环比 |
| `nsa_source_file` | `VARCHAR(255) NOT NULL` | NSA 工作簿名 |
| `sa_source_file` | `VARCHAR(255) NOT NULL` | SA 工作簿名 |
| `nsa_sheet` | `VARCHAR(128) NULL` | NSA 工作表名 |
| `sa_sheet` | `VARCHAR(128) NULL` | SA 工作表名 |
| `nsa_source_sha256` | `CHAR(64) NOT NULL` | NSA 工作簿字节摘要 |
| `sa_source_sha256` | `CHAR(64) NOT NULL` | SA 工作簿字节摘要 |
| `updated_at` | `DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)` | 本地导入/修订时间；非官方公布时间 |

## 4. MCP 公共实现规范

### 4.1 日期类型与参数绑定

1. **外部月参数**（如 `forecast_month`、`start_month`、`end_month`）：仅接受真实日历中的 `YYYY-MM` 字符串；进入查询前转换为月首 `date(YYYY, MM, 1)`。
2. **外部日参数**（`start_date`、`end_date`）：严格 `YYYY-MM-DD`，必须是有效日历日期。
3. **数据库/JSON 内部月份**：允许 Python `date`、`datetime` 或合法月首 `YYYY-MM-DD`，统一解析为 `date`；不得用外部 `YYYY-MM` 格式检查内部 `2026-08-01`。月首日必须为 1。
4. **月运算**：默认 12 个月窗口使用日历月加减，不使用 `11*30` 天；比较之前先统一月份类型。
5. SQL 中的 `:name` 是**说明用逻辑占位符**。PyMySQL 实际调用应改为 `%s`，通过独立参数元组绑定；重复占位符必须重复传入，不可插值拼接用户输入。
6. **百分号兼容**：参数化执行器可能将 SQL 中 `DATE_FORMAT(..., '%Y-%m')` 当 Python `%` 模板解析。优先 SELECT 原生 `DATE`、在 Python 格式化；确需 DB-API 插值路径的日期格式字面量时使用 `%%Y-%%m`，**不能**转义 `%s`。不要将已转义格式直接当通用 MySQL SQL 文本使用。
7. `limit` 均按**返回记录条数**解释，不能按月份数解释；强制工具自身上下限。枚举、布尔、名称长度、日期范围都必须先校验。

**日期解析参考实现**：

```python
from datetime import date, datetime
import re


def parse_user_month(raw: str | None) -> date | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
        raise ValueError("invalid_month")
    return date.fromisoformat(raw + "-01")


def normalize_internal_month(raw) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        raw = raw.date()
    if isinstance(raw, str):
        raw = date.fromisoformat(raw)  # 内部 DATE 字符串
    if not isinstance(raw, date) or raw.day != 1:
        raise ValueError("invalid_internal_month")
    return raw
```

### 4.2 报告默认定位

工具 2—6 默认先选一条 `status='ready'` 报告；显式 `report_id` 则只选指定版本，不存在或非 `ready` 返回 `report_not_found`，不得悄然换成最新报告。

排序：**`forecast_month DESC` → `generated_at DESC` → `imported_at DESC` → `report_id DESC`**。优先最新**预测目标月**，不是最近运行过的一份旧月份报告。一次调用固定同一 `report_id`；工具内部多个子查询不得重复独立选择最新版本。工具 7 不关联报告。

```sql
SELECT report_id, forecast_month, official_latest_month,
       market_month, strategy_month, generated_at, imported_at
FROM fmqt_forecast_us_cpi_prediction_result
WHERE status = 'ready'
  AND (:report_id IS NULL OR report_id = :report_id)
ORDER BY forecast_month DESC, generated_at DESC, imported_at DESC, report_id DESC
LIMIT 1;
```

### 4.3 JSON 读取、筛选与排序

- 直接 SELECT LONGTEXT 列并 `json.loads`；结果必须是数组，数组元素必须是对象，关键字段类型正确。非法 JSON、错误编码或关键类型异常返回错误，不能静默跳过坏数据。
- 可使用 `json.loads(raw, parse_float=Decimal)` 保留数值精度；转换出站 JSON 时保持合法数值语义。中文按原文返回。
- 内部日期先归一化再做过滤/排序；保留 `record_type`，不把 `main` 和 `far` 合并。
- 所有数据缺失以空数组及 `warnings` 表示；SQL/连接/解析异常不是“无数据”。
- 查询设置合理超时、返回上限；多步读取尽量使用一致性读视图，避免在入库过程出现版本或月份漂移。

### 4.4 统一响应和错误

成功响应：

```json
{
  "data": [],
  "meta": {
    "report_id": null,
    "forecast_month": null,
    "market_month": null,
    "strategy_month": null,
    "generated_at": null,
    "applied_filters": {},
    "units": {},
    "warnings": [],
    "truncated": false
  }
}
```

`meta` 中报告字段按工具实际适用情况填入；官方历史工具的报告字段为 `null`，并返回 `data_source="local_database"`、`effective_end_month`、`latest_observation_month`（有值时）和 `freshness_status="unverified"`（未独立核对 BLS 时）。各业务记录的 `forecast_month` 保留该记录自身月份，不以模型月份覆盖市场或策略月份。

失败响应：

```json
{"ok": false, "error": "可理解的错误描述", "error_code": "invalid_month"}
```

建议稳定错误码：`invalid_month`、`invalid_date`、`invalid_parameter`、`report_not_found`、`db_unavailable`、`db_timeout`、`sql_execution_failed`、`invalid_stored_json`、`internal_error`；无数据一般返回成功空数组和 warning。不能向调用方泄露密码、连接串或敏感 SQL 参数。MCP 协议 `content` 外层封装由协议层处理，业务层不重复将 JSON 对象编码成 JSON 字符串。

## 5. 七个 MCP 工具与 SQL

> 下列 SQL 中 `:param` 为逻辑参数。接入 PyMySQL 时统一改成绑定占位符；各工具先完成参数校验，再执行只读查询。返回 `data` 为结构化对象数组。

### 5.1 `get_cpi_reports` —— 报告版本

**参数**：`report_id?`、`forecast_month?`、`limit=1`（最大 50）。`forecast_month` 为模型目标月筛选；`limit` 为最多报告条数。

```sql
SELECT report_id, forecast_month, official_latest_month,
       market_month, strategy_month, generated_at, imported_at,
       model_sha256, market_sha256, strategy_sha256
FROM fmqt_forecast_us_cpi_prediction_result
WHERE status = 'ready'
  AND (:report_id IS NULL OR report_id = :report_id)
  AND (:forecast_month IS NULL OR forecast_month = :forecast_month)
ORDER BY forecast_month DESC, generated_at DESC, imported_at DESC, report_id DESC
LIMIT :limit_rows;
```

**返回**：SQL 全部列。完整三文件组合和策略月份决定版本去重；同月允许多个版本。市场月、策略月与模型月可以不同，必须分别展示。显式指定无效 `report_id` 不回退到其他版本。

### 5.2 `get_cpi_group_forecasts` —— 组级预测与趋势

**参数**：`report_id?`、`forecast_month?`、`scope=main`（`main` / `far` / `all`）、`group?`（源组名精确匹配）。

```sql
SELECT report_id, forecast_month, groups_json AS groups_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```

**服务层**：解析 `groups_text`。`scope=main` 且不传月时默认报告 `forecast_month`；`scope=far/all` 且不传月时保留全部已存月份；传月则精确匹配该月。`group` 精确匹配原名。按 `forecast_month`、`group_name`、`record_type` 排序；`main/far` 分开保留。返回 `groups_json[]` 原有七字段及所属 `report_id`，不返回 baseline/可信度。缺少组或月份返回 `[]` 和 warning，不生成缺月数据。

### 5.3 `get_cpi_item_forecasts` —— 分项预测

**参数**：`report_id?`、`group?`、`item?`（源分项名精确匹配）。

```sql
SELECT report_id, forecast_month, leaves_json AS leaves_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```

**服务层**：只保留 `leaves_json[].forecast_month == 报告forecast_month`；按 `group`、`item` 精确筛选；按 `weight` **降序**、`item_name` 升序，`weight=null` 排末尾，不能按零处理。返回实际 `leaves_json[]` 八字段和 `report_id`；不存在的分项给出 warning，不做模糊匹配或其他分项回退。

### 5.4 `get_cpi_daily_evolution` —— 逐日预测变化

**参数**：`report_id?`、`group?`（总体/核心/食品/能源）、`start_date?`、`end_date?`、`include_future=false`。

```sql
SELECT report_id, forecast_month, daily_json AS daily_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```

**服务层**：仅保留 `target_month == 报告forecast_month`；日期默认目标月首/末日，且必须位于目标月、`start_date <= end_date`。按 `observation_date` **闭区间**、`group` 精确过滤；默认排除 `is_future_display=1`。严格解析布尔，字符串 `"false"` 不得被当作真。按日期、组名排序。返回 `daily_json[]` 原八字段及 `report_id`；不补造日期。`event_sources` 跨组重复不代表新增事件；`baseline` 仅用于逐日同比，不用于顶部同比/SA 环比。目标月外的展示日期不自动扩大筛选窗口。

### 5.5 `get_cpi_market_expectations` —— 机构预测及模型差值

**参数**：`report_id?`、`forecast_month?`（默认报告 `market_month`）、`median_only=false`、`institution?`。绑定 `effective_month`、`institution_name`、`median_only` (`0/1`)。

机构记录：

```sql
SELECT report_id, forecast_month, institution_name, is_median,
       headline_yoy, headline_mom, core_yoy, core_mom
FROM fmqt_forecast_us_cpi_market_expectation
WHERE report_id = :resolved_report_id
  AND forecast_month = :effective_month
  AND (:median_only = 0 OR is_median = 1)
  AND (:institution_name IS NULL OR institution_name = :institution_name)
ORDER BY is_median DESC, institution_name;
```

同报告模型组预测：

```sql
SELECT report_id, groups_json AS groups_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```

**服务层**：只使用同一报告中 `record_type=main`、`forecast_month=effective_month` 的“总体”和“核心”记录。**同比对同比、SA 环比对 SA 环比**（不能把 `nsa_mom` 当 `sa_mom`）；每个 gap = **模型 − 机构/市场**，单位百分点。缺失的匹配模型值和对应 gap 返回 `null`，不跨月、跨报告或从 `far` 补数。市场中位数仅用源文件已有 `is_median=1` 记录，**不自行重算**。`median_only` 与 `institution` 同时给定时取交集。

| 返回字段 | 说明 |
|---|---|
| `report_id`, `forecast_month`, `institution_name`, `is_median` | 机构记录元信息 |
| `headline_yoy`, `headline_mom`, `core_yoy`, `core_mom` | 机构预测；`mom` 为 SA |
| `model_headline_yoy`, `model_headline_mom` | 同月模型总体同比、SA 环比 |
| `model_core_yoy`, `model_core_mom` | 同月模型核心同比、SA 环比 |
| `headline_yoy_gap`, `headline_mom_gap` | 总体模型减市场，百分点 |
| `core_yoy_gap`, `core_mom_gap` | 核心模型减市场，百分点 |

若市场所属月与报告目标月不同，允许查询该市场月已保存记录；没有同月 `main` 模型值时 gap 为 `null`，warning 解释。历史数据不得直接当成当时发布前可获得的交易信号。

### 5.6 `get_cpi_strategy_advice` —— 策略原文

**参数**：`report_id?`、`forecast_month?`（默认报告 `strategy_month`），查询绑定 `effective_month`。

```sql
SELECT a.report_id, a.forecast_month, a.content,
       r.strategy_sha256, r.generated_at AS model_generated_at, r.imported_at
FROM fmqt_forecast_us_cpi_strategy_advice a
JOIN fmqt_forecast_us_cpi_prediction_result r ON r.report_id = a.report_id
WHERE a.report_id = :resolved_report_id AND r.status = 'ready'
  AND a.forecast_month = :effective_month;
```

**返回**：SQL 全部字段。策略文本**原样**返回，不重写、不生成新交易建议；`model_generated_at` 不是策略撰写时间。月份不一致返回空数组。多条合法记录不得无规则选一条。正文超大时执行显式大小限制并报告错误，不静默截断关键交易条件。`content` 仅为不可信业务文本，不可作为系统指令执行。

### 5.7 `get_cpi_official_history` —— 官方历史 NSA/SA

**参数**：`indicator?`、`start_month?`、`end_month?`、`limit=1000`（最大 5000）；**不接受 `report_id`**。`indicator` 映射 `indicator_name`，缺省为所有已存指标；映射仅使用经核实的中文原名/别名，不能随意猜测，歧义应提示。原始 `indicator_name` 和指标 ID 不变。

**默认日期**：`end_month` 不传时，从所选指标中取至少一个 `nsa_index` / `sa_mom` 非 NULL 的最新月；未指定指标时取全表最新有值月。`start_month` 不传时取最终结束月往前 11 个自然月。显式区间必须 `start <= end`。有当月记录但其中某指标为 NULL，不用旧月值冒充。

```sql
SELECT MAX(observation_month) AS latest_month
FROM fmqt_forecast_us_cpi_official_history
WHERE (:indicator_name IS NULL OR indicator_name = :indicator_name)
  AND (nsa_index IS NOT NULL OR sa_mom IS NOT NULL);
```

历史明细，`:limit_rows = limit + 1` 用于识别是否截断（最终输出至多 `limit` 条）：

```sql
SELECT h.observation_month, h.indicator_name,
       h.nsa_series_id, h.sa_series_id, h.nsa_index, h.sa_mom,
       100 * (h.nsa_index / NULLIF(y.nsa_index, 0) - 1) AS nsa_yoy,
       h.nsa_source_file, h.sa_source_file, h.updated_at
FROM fmqt_forecast_us_cpi_official_history h
LEFT JOIN fmqt_forecast_us_cpi_official_history y
  ON y.indicator_name = h.indicator_name
 AND y.observation_month = DATE_SUB(h.observation_month, INTERVAL 1 YEAR)
WHERE h.observation_month BETWEEN :start_month AND :end_month
  AND (:indicator_name IS NULL OR h.indicator_name = :indicator_name)
ORDER BY h.observation_month DESC, h.indicator_name ASC
LIMIT :limit_rows;
```

**返回**：明细 SQL 全部字段。`nsa_yoy` 是基于当前库中当月/上年同月 **NSA 指数**计算的派生值，不是表中持久化的历史发布同比；缺失上年同月或分母为 0 时为 `null`。只返回实际已入库值，不补零、不插值。`limit=12` 意为**最多 12 条指标-月份记录**，不等于最近 12 个月；排序最新月优先，`meta.truncated` 指示截断。

**来源元信息**：`meta.data_source="local_database"`，`effective_end_month` 为实际查询结束月，`latest_observation_month` 为所选指标最新有值月（与用户指定结束月可不同）；独立核对 BLS 前 `freshness_status="unverified"`。`updated_at` 不是官方发布日期。仅凭本地库最新观测月**不能宣称 BLS 官网实时最新**；对于“官网最新数据”请求需要另外核验 BLS 发布日期和官方数字。当前表不保留历次公布的历史快照，不能直接做严格无前视偏差事件回测。

## 6. 入库和部署要求

1. 建表使用配套 `database/美国CPI_DBeaver建表.sql` 或 `forecast_us_cpi_schema.sql`；四张表字段与第 3 节一致。
2. 若现有三列仍为原生 JSON、项目要求兼容 JDBC 5.1.35 的 LONGTEXT 路径，则由**数据库部署流程**执行 `database/美国CPI_已有JSON列改LONGTEXT.sql`，只转换三列类型，不删除历史报告或重新导入。`CREATE TABLE IF NOT EXISTS` 不会改变已建表的列类型。
3. `forecast_us_db.py` 继续使用项目既有公共配置和 PyMySQL 连接方式。写入 `groups_json`、`leaves_json`、`daily_json` 前校验均为合法 JSON 对象数组，写后验证 UTF-8 中文及精度；绕过入库脚本直接写 SQL 的调用方也必须执行等价校验。
4. 报告按完整三文件及策略月份去重；`report_id`、源文件摘要、各来源月份和 `status=ready` 的事务边界保持一致。类型转换不生成新业务版本。
5. MCP 独立使用只读账号，统一数据库连接/超时/参数校验/JSON 解析/错误处理；不应把查询工具当成迁移或修改库结构的入口。
6. 配置 `CPI_MCP工具定义.json` 与 7 个工具实现的名称、可选参数、类型、默认值和上限；注册服务后逐工具进行端到端验收。

## 7. 验收测试矩阵

| 编号 | 工具/类别 | 输入或数据构造 | 必须通过的结果 |
|---|---|---|---|
| T01 | 官方历史：默认月 | `get_cpi_official_history({"limit":12})` | 不误把内部 `YYYY-MM-DD` 当外部月参数；最多 12 行，按新→旧，`truncated` 正确 |
| T02 | 官方历史：指定范围 | `{"start_month":"2025-01","end_month":"2026-01","limit":12}` | SQL `%` 格式安全；月份闭区间正确，不出现 `%Y` 参数格式冲突 |
| T03 | 官方历史：空值 | 最新自然月全 NULL；上年同月缺失/为零 | 默认最新月取有值月；`nsa_yoy=null`；部分缺项 warning |
| T04 | 官方历史：多指标与截断 | 多个月、多指标，数量大于 limit | 最新记录优先、限行不限制月份数；正确 `truncated` |
| T05 | 报告：版本选择 | 近期生成旧目标月、较早生成新目标月 | 默认先取较新**目标月**，同月按生成/导入时间；无效显式 ID 不回退 |
| T06 | 组级预测 | `scope=main/far/all`、指定组/月份、远期缺月 | 类型归一，原组名准确，main/far 不混，不补缺月 |
| T07 | 分项预测 | `weight=null`/`0`、不存在分项、group/item 条件 | null 排最后且不等于零；精确筛选；无匹配 warning |
| T08 | 逐日预测 | 无效日期、跨目标月、`include_future` 真假 | 日期合法且闭区间；正确排除未来展示；来源不重复累计 |
| T09 | 市场预期 | 市场月≠模型月、缺模型值、机构/中位数筛选 | gap 只在同月可计算；SA 对 SA；中位数不重算；条件正确 |
| T10 | 策略原文 | 非策略月、超长文本、包含指令性正文 | 不跨月；原文仅作为数据；超限明确错误，不执行正文 |
| T11 | 所有工具：非法参数 | `2026-02-30`、非法月、超限 limit、SQL 特殊字符 | 统一错误码和安全绑定，无 SQL 注入/敏感信息泄露 |
| T12 | 所有工具：故障/编码 | 断网、数据库超时、坏 JSON、中文、Decimal/null | 故障显式报告；中文/数值/null 正确，不伪装空数据 |
| T13 | 所有工具：一致性 | 单次调用中插入新报告/新官方月份 | report_id、默认月份在同次查询中保持一致 |

**验收交付**：记录每个用例的调用参数、关键测试数据、实际返回、预期返回、通过/失败、对应代码提交。先使用隔离测试数据，再在授权的只读真实环境验证；测试通过不得以静态 SQL 审阅或数据库已有数据代替。

## 8. 开发交付清单

- [ ] 4 张表结构与存储类型核对；三个 JSON 数组列为 utf8mb4 LONGTEXT。
- [ ] 落库 JSON 校验、中文编码、数值精度、来源月份和报告去重正确。
- [ ] 7 个工具已注册，参数与 `CPI_MCP工具定义.json` 一致。
- [ ] 外部日期校验、内部 DATE 归一化、SQL `%s` 参数绑定统一实现。
- [ ] 默认报告按目标月优先且一次调用锁定报告版本。
- [ ] 7 个工具均保留来源口径、空值、SA/NSA 区别和只读边界。
- [ ] 返回 `{data,meta}` / `{ok:false,error,error_code}`，错误不伪装空数组。
- [ ] T01—T13 记录测试结果；附变更文件、函数、配置项和未覆盖环境限制。

**实现限制**：不得未经授权修改已有数据库历史记录、预测结果、策略正文、来源指标名称、文件摘要和业务公式；不得将入库最新月份无依据地称作 BLS 实时最新月份。
