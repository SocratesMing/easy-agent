# 1年期国债模型的 SQL 参考（供 MCP 接入）

本文档整理1年期国债模型已落库结果涉及的**全部查询 SQL 场景**，涵盖「最新结果 / 历史结果 / 资金面与技术面信号 / 历史走势图 / 已存解释」。
每条包含：用途（业务逻辑）、参数、可执行的参数化 SQL、返回字段，并给出对应的 MCP 工具定义建议。

---

## 通用约定 

| 项 | 说明 |
|---|---|
| 数据库 | SQL 按公司 MySQL 8.0 编写；本机服务使用 SQLite 仿真，MySQL JSON 函数不能直接照搬到 SQLite |
| 物理表名 | `fmqt_treasury_1y_history`，与当前仓储 `TABLE_NAME` 和 `schema.sql` 一致 |
| 存储方式 | 每个日期一行；完整日结果存放在 `result_json` 一个 JSON 字段内，不是20个平铺数据库列 |
| 日期格式 | `YYYY-MM-DD`；外层 `signal_date` 为 SQL DATE，JSON 内同名字段为日期字符串，二者应一致 |
| 日期含义 | 上游结果参考日期，不等于导入时间、数据库写入时间或当前系统日期 |
| 最新定义 | 按 `signal_date DESC LIMIT 1` 获取，不按 `updated_at` 判断最新 |
| 已存日期 | 保留上游已有周末、补班日等日期；不自行过滤工作日，不补不存在的日期 |
| 预测期限 | `prob_up_7d`、`prob_down_7d` 是上游已有7日期限预测，字段说明采用“7个数据交易行后”；本服务无训练或交易日历代码，不据此承诺七个自然日或真实市场七个交易日 |
| 收益率单位 | `yield_pct=1.2147` 表示1.2147%，不能再乘100 |
| 概率与分位 | `prob_up_7d`、`prob_down_7d`、`yield_percentile_756`、`trade_threshold` 为0—1小数；展示百分数时乘100 |
| 变动单位 | `yield_change_2d_bp` 为bp；允许 JSON null，不能当作0 |
| 信号方向 | `1` 为收益率上行，`-1` 为收益率下行，`0` 为观察或方向不明确；收益率方向不是债券价格方向 |
| 历史口径 | 使用每条记录自己的阈值、信号、建议和解释，不用最新阈值重算历史，不由 MCP 根据概率重算信号 |
| 唯一结果 | `signal_date` 为唯一主键；当前为单策略结果表，同日覆盖不是版本快照，不能保存同日多个策略的独立结果 |
| 时间字段 | `created_at`、`updated_at` 按 UTC 写入无时区 DATETIME；现 HTTP 响应不附时区后缀，应按 UTC 理解 |
| JSON 空值 | 缺失键或 JSON null 表示无值，保留为 null，不用 `COALESCE(..., 0)` 改成零 |
| JSON 解码 | `result_json` 恢复为对象；`JSON_EXTRACT` 的返回值可能是 JSON 文本，工具层应按 JSON 解码，而非一律当字符串或浮点数 |
| 范围与窗口 | 起止日期均包含；先筛选范围，再倒序取最近N条，绘图时反转为升序 |
| 参数写法 | `:signal_date` 等为 SQLAlchemy `text(sql)` 绑定参数，通过 `conn.execute(text(sql), params)` 执行，业务值不拼接进 SQL |
| 可空参数 | 本文可空条件模板必须绑定全部命名参数；未指定日期绑定 Python `None`，不是空字符串或字符串 `"null"` |
| SQL客户端 | 手工在 MySQL 客户端核验时，将绑定参数替换为实际字面量；`:参数` 不是 MySQL 客户端直接识别的语法 |

**接入范围**：MCP 仅读取数据库已有结果，不读取导入源文件，不训练模型，不生成预测，不调用大模型。本文不提供新增、更新、删除或建表场景，也不注册写入工具。

**结果边界**：当前表没有真实未来标签、预测对错、回测AUC、准确率、入模因子清单或训练审计字段，不能从本表查询或虚构这些内容；资金面和技术面解释是已经保存的原文。概率之和是否严格为1、分位的具体计算算法由上游决定，当前存取服务不验证或重新计算。

---

## 一、表结构与结果字段

**表名**：`fmqt_treasury_1y_history`。

### 1.1 数据库字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `signal_date` | DATE，主键 | 结果参考日期，同一天只有一行 |
| `result_json` | JSON，非空 | 当日完整原始结果对象，初始20字段及后续扩展字段 |
| `created_at` | DATETIME(6)，非空 | 首次写入时间，UTC；同日覆盖时保留 |
| `updated_at` | DATETIME(6)，非空 | 最近一次写入时间，UTC；只读查询不改变 |

当前没有 ORM Model 或数据库外键。表结构以现有 `schema.sql` 为准，不能把导出字段说明中的旧平铺字段描述或联合主键说明当作实际表结构。

### 1.2 result_json 完整字段

下表列出原始20字段。完整查询必须保留未知扩展字段，不按下表裁剪数据库 JSON。

| JSON字段 | 值类型与单位 | 业务含义 |
|---|---|---|
| `signal_date` | 日期字符串 | 与外层主键相同的结果日期 |
| `strategy_id` | 字符串 | 上游策略标识；随包数据为 `curve_funding_7d_mtht_v3` |
| `yield_pct` | 数值，% | 1年期国债收益率，1.2147即1.2147% |
| `yield_percentile_756` | 数值，0—1 | 上游已算的756数据观测窗口收益率分位 |
| `prob_up_7d` | 数值，0—1 | 上游7日期限收益率上行概率 |
| `prob_down_7d` | 数值，0—1 | 上游7日期限收益率下行概率 |
| `trade_threshold` | 数值，0—1 | 当日实际保存的交易阈值 |
| `signal_blocked` | 0/1或布尔值 | 1或true表示保护条件阻止开仓，0或false表示未阻止 |
| `production_guard` | 保护说明 | 已保存的保护状态或原因，不自动替换为新规则 |
| `funding_signal` | -1/0/1 | 资金面信号；1上行、-1下行、0观察 |
| `funding_advice` | 建议文本 | 已保存的资金面操作建议 |
| `mt_signal` | -1/0/1 | 趋势动量方向；0表示不清晰 |
| `ht_signal` | -1/0/1 | 周期位置方向；0表示不明确 |
| `technical_signal` | -1/0/1 | 已保存的技术综合信号 |
| `technical_advice` | 建议文本 | 已保存的技术面操作建议 |
| `yield_change_2d_bp` | 数值或null，bp | 上游已算的两期收益率变化 |
| `funding_explanation` | 解释文本 | 已保存的资金面解释原文 |
| `technical_explanation` | 解释文本 | 已保存的技术面解释原文 |
| `funding_source` | 来源标记 | 上游资金面结果来源，不代表 MCP 可读取该工作簿 |
| `technical_source` | 来源标记 | 上游技术面结果来源，不代表 MCP 可读取该工作簿 |

所有信号均以**收益率方向**解释。收益率上行通常对应债券价格偏空，收益率下行通常对应债券价格偏多；不能把 `funding_signal=1` 描述成“债券价格上涨”。

---

## 二、最新结果与历史查询

### 2.1 数据范围与总记录数

**业务**：回答“模型结果更新到了哪天、历史从哪天开始、共有多少期”。

**来源**：当前 `list_dates()` 返回元数据的 SQL 汇总等价查询。

**参数**：无。

```sql
SELECT COUNT(*) AS total_count,
       MIN(signal_date) AS first_date,
       MAX(signal_date) AS last_date
FROM fmqt_treasury_1y_history;
```

**返回**：`total_count`、`first_date`、`last_date`。空表仍返回一行，数量为0，首末日期为NULL。日期范围不是市场交易日历的完整范围。

### 2.2 上一个有结果的日期

**业务**：比较相邻两期结果，确定指定日期之前最近的已存日期。

**来源**：MCP建议查询。

**参数**：`signal_date`，必填。

```sql
SELECT MAX(signal_date) AS previous_signal_date
FROM fmqt_treasury_1y_history
WHERE signal_date < :signal_date;
```

**返回**：`previous_signal_date`；不存在更早结果时为NULL。不是简单减一天，也不保证为市场上一交易日。

### 2.3 可查询日期清单

**业务**：日期选择器、确定某日是否已有结果。

**来源**：当前 `TreasuryHistoryRepository.list_dates()`。

**参数**：无。

```sql
SELECT signal_date
FROM fmqt_treasury_1y_history
ORDER BY signal_date ASC;
```

**返回**：升序日期清单。只读取主键，不加载 JSON。现 HTTP 接口在应用层据此计算 `first_date`、`last_date`、`total_count`。

只需近期日期时可使用以下有限返回模板，不必把全量日期交给模型：

```sql
SELECT signal_date
FROM fmqt_treasury_1y_history
ORDER BY signal_date DESC
LIMIT :limit;
```

**参数与返回**：`limit` 为正整数，建议默认20、最多250；返回最近N个日期，倒序。此限制是建议工具约定，不是现有 `dates` HTTP 接口参数。

### 2.4 最新一期完整结果

**业务**：回答“1年期国债模型当前结果是什么”，获得最新卡片全部数据。

**来源**：当前 `TreasuryHistoryRepository.get_result()` 省略日期的分支。

**参数**：无。

```sql
SELECT signal_date, result_json, created_at, updated_at
FROM fmqt_treasury_1y_history
ORDER BY signal_date DESC
LIMIT 1;
```

**返回**：四个数据库字段，至多一行。工具层解码 `result_json` 为对象。空表返回无记录，不制造默认收益率、概率或信号。不能按今天日期直接查询并把无数据误报为模型失败。

### 2.5 指定日期完整结果

**业务**：查询一个历史日期的原始完整结果。

**来源**：当前 `TreasuryHistoryRepository.get_result(signal_date)`。

**参数**：`signal_date`，必填，合法日期。

```sql
SELECT signal_date, result_json, created_at, updated_at
FROM fmqt_treasury_1y_history
WHERE signal_date = :signal_date
ORDER BY signal_date DESC
LIMIT 1;
```

**返回**：同2.4。指定日期不存在时返回无记录，不回退到前一天或最新日。现 HTTP 参数名为 `date`，映射到 SQL 的 `signal_date`。

### 2.6 日期区间与最近N期历史

**业务**：查询某个闭区间的完整结果，或截至某日最近N期，用于问答和走势图。

**来源**：当前 `TreasuryHistoryRepository.list_results()` 的等价可空参数模板。实际代码只在日期非空时加入固定条件，业务值仍绑定。

**参数**：`start_date`、`end_date` 可空；`limit` 为正整数。两端都存在时要求 `start_date <= end_date`。

```sql
-- 范围内总数：不受 limit 影响。
SELECT COUNT(*) AS matched_count
FROM fmqt_treasury_1y_history
WHERE (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date);

-- 先过滤，再取范围内最近 N 条。
SELECT signal_date, result_json, created_at, updated_at
FROM fmqt_treasury_1y_history
WHERE (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date)
ORDER BY signal_date DESC
LIMIT :limit;
```

**返回**：明细为四个数据库字段，SQL倒序；工具层反转为升序用于展示。完整日对象与原 JSON 一致。范围内没有结果时返回空列表；`matched_count` 为0。

建议 MCP 默认 `limit=30`、最大250，返回 `matched_count`、`returned_count`、`items`，区分范围总数和实际返回数。当前 HTTP `history` 的 `limit` 允许1—10000，省略返回全部；它的 `total_count` 是**本次实际返回数量**，不是这里的 `matched_count`。

需要与 HTTP 省略 `limit` 的行为完全一致时，使用以下无数量限制的版本，不把无数量限制设为 MCP 默认：

```sql
SELECT signal_date, result_json, created_at, updated_at
FROM fmqt_treasury_1y_history
WHERE (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date)
ORDER BY signal_date ASC;
```

**参数与返回**：绑定两个可空日期，返回范围内全部完整记录，升序。数据量大时按需使用，不默认把全部 JSON 和长解释放入模型上下文。

### 2.7 指定日页面与历史走势图

**业务**：取得指定日期的当前结果，以及不晚于该日的历史序列，与现有网页口径一致。

**来源**：当前 `TreasuryHistoryService.get_dashboard()`。

**参数**：`date` 可选；`window` 为30、60、120、9999，默认30。

执行顺序：

1. 用2.4或2.5获取当前记录；省略日期取最新，指定日不存在则明确返回未找到。
2. 将实际命中的日期作为下面的 `end_date`；不能直接按当前系统日期或前端旧日期切窗口。
3. 用2.3取得可选日期，或2.1取得全表范围。日期清单独立于图表窗口。

```sql
-- window 为 30/60/120 时，limit 等于 window。
SELECT signal_date, result_json, created_at, updated_at
FROM fmqt_treasury_1y_history
WHERE signal_date <= :end_date
ORDER BY signal_date DESC
LIMIT :limit;
```

```sql
-- window=9999 表示截至该日的全部历史，不是 LIMIT 9999。
SELECT signal_date, result_json, created_at, updated_at
FROM fmqt_treasury_1y_history
WHERE signal_date <= :end_date
ORDER BY signal_date ASC;
```

**返回**：`current_result` 为当前完整日对象；`rows` 为历史日对象映射，绘图升序；`selected_date` 为命中日期；`threshold` 来自当前日 `trade_threshold`。

**边界**：30/60/120是记录条数，不是自然日长度；不足N条只返回已有记录。`rows` 不含选中日之后的数据。现 `dashboard.dates` 和 `total_count` 是全表元数据，允许包含选中日之后的可选日期，不能误当作未来结果混入走势图。

**空库行为**：省略日期时，现 HTTP `dashboard` 返回200，`selected_date`、`current_result`、`threshold` 为null，`rows`、`dates`为空，`total_count=0`；指定日期不存在则返回404。单日 `result` 在空库时同样返回404。

现页面字段映射如下，MCP 也可直接返回原始 JSON 字段名，避免不必要转换：

| 页面字段 | JSON字段 | 页面字段 | JSON字段 |
|---|---|---|---|
| `date` | `signal_date` | `yieldValue` | `yield_pct` |
| `percentile` | `yield_percentile_756` | `up` | `prob_up_7d` |
| `down` | `prob_down_7d` | `threshold` | `trade_threshold` |
| `blocked` | `signal_blocked`转布尔值 | `guard` | `production_guard` |
| `fundingSignal` | `funding_signal` | `mt` | `mt_signal` |
| `ht` | `ht_signal` | `total` | `technical_signal` |
| `change` | `yield_change_2d_bp` | `fundingAdvice` | `funding_advice` |
| `technicalAdvice` | `technical_advice` | `fundingExplanation` | `funding_explanation` |
| `technicalExplanation` | `technical_explanation` | | |

---

## 三、信号摘要与已存解释

以下 SQL 使用 MySQL JSON 函数做按需投影，减少历史结果中重复长文本的传输。工具只需完整结果时，直接复用2.4/2.5并在应用层提取字段，无需额外执行本章查询。

### 3.1 最新或指定日期的收益率、概率与信号

**业务**：回答“当前收益率多少、上行概率多大、资金面和技术面是否同向”。

**来源**：当前保存字段的 MCP 投影建议；不重新运行模型。

**参数**：`signal_date` 可空，None表示最新；非空时精确匹配。

```sql
SELECT
  signal_date,
  JSON_EXTRACT(result_json, '$.yield_pct') AS yield_pct,
  JSON_EXTRACT(result_json, '$.yield_percentile_756') AS yield_percentile_756,
  JSON_EXTRACT(result_json, '$.prob_up_7d') AS prob_up_7d,
  JSON_EXTRACT(result_json, '$.prob_down_7d') AS prob_down_7d,
  JSON_EXTRACT(result_json, '$.trade_threshold') AS trade_threshold,
  JSON_EXTRACT(result_json, '$.signal_blocked') AS signal_blocked,
  JSON_EXTRACT(result_json, '$.production_guard') AS production_guard,
  JSON_EXTRACT(result_json, '$.funding_signal') AS funding_signal,
  JSON_EXTRACT(result_json, '$.funding_advice') AS funding_advice,
  JSON_EXTRACT(result_json, '$.mt_signal') AS mt_signal,
  JSON_EXTRACT(result_json, '$.ht_signal') AS ht_signal,
  JSON_EXTRACT(result_json, '$.technical_signal') AS technical_signal,
  JSON_EXTRACT(result_json, '$.technical_advice') AS technical_advice,
  JSON_EXTRACT(result_json, '$.yield_change_2d_bp') AS yield_change_2d_bp
FROM fmqt_treasury_1y_history
WHERE (:signal_date IS NULL OR signal_date = :signal_date)
ORDER BY signal_date DESC
LIMIT 1;
```

**返回**：日期及14个投影字段。`JSON_EXTRACT` 保留原 JSON 数值、布尔、字符串和null，驱动若返回 JSON 文本应解码。`signal_blocked` 必须兼容0/1和false/true，不能直接将字符串 `"true"` 强制转换为数值0。

资金面、趋势动量、周期位置和技术综合方向分别解释，即使不一致也如实展示。不能根据 `prob_up_7d > trade_threshold` 擅自覆盖 `funding_signal`；保护状态可能影响建议，交易条件的完整上游逻辑不在当前存取服务内。

### 3.2 最新或指定日期的资金面、技术面解释

**业务**：读取当日已经保存的解释、操作建议和来源，回答“为什么给出该信号”。

**来源**：当前日结果中的已存文本，MCP按需投影建议。

**参数**：`signal_date` 可空，None表示最新；指定日不存在时不改查其他日期。

```sql
SELECT
  signal_date,
  JSON_EXTRACT(result_json, '$.strategy_id') AS strategy_id,
  JSON_EXTRACT(result_json, '$.funding_signal') AS funding_signal,
  JSON_EXTRACT(result_json, '$.funding_advice') AS funding_advice,
  JSON_EXTRACT(result_json, '$.funding_explanation') AS funding_explanation,
  JSON_EXTRACT(result_json, '$.funding_source') AS funding_source,
  JSON_EXTRACT(result_json, '$.mt_signal') AS mt_signal,
  JSON_EXTRACT(result_json, '$.ht_signal') AS ht_signal,
  JSON_EXTRACT(result_json, '$.technical_signal') AS technical_signal,
  JSON_EXTRACT(result_json, '$.technical_advice') AS technical_advice,
  JSON_EXTRACT(result_json, '$.technical_explanation') AS technical_explanation,
  JSON_EXTRACT(result_json, '$.technical_source') AS technical_source,
  JSON_EXTRACT(result_json, '$.signal_blocked') AS signal_blocked,
  JSON_EXTRACT(result_json, '$.production_guard') AS production_guard,
  created_at,
  updated_at
FROM fmqt_treasury_1y_history
WHERE (:signal_date IS NULL OR signal_date = :signal_date)
ORDER BY signal_date DESC
LIMIT 1;
```

**返回**：上述字段。解释为空时明确无已存解释，不补写新的经济结论，不自动读取来源工作簿，不调用大模型。

`created_at`、`updated_at` 是结果记录写入时间，不是独立的解释生成时间。当前没有解释状态、生成模型或独立生成时间字段，不能把10Y解释元数据字段套用到本表。

---

## 四、MCP 工具映射建议

### 4.1 接入原则

| 原则 | 说明 |
|---|---|
| 仅查询已存结果 | 不注册导入、保存、训练、生成解释工具 |
| 固定 SQL 与参数绑定 | 日期、数量通过绑定参数；不接受任意SQL、表名或JSON路径作为工具参数 |
| 复用公司数据库连接 | 使用公司现有 SQLAlchemy Engine，无需新增数据库环境变量或ORM |
| 先明确日期 | 返回实际 `signal_date`；缺省表示最新，指定日无数据不回退 |
| 保留原值 | 概率0—1、收益率为百分数原值、变动为bp，null不改成0 |
| 控制历史返回量 | 建议默认30条、最多250；走势图只返回所需字段，默认不返回每期长解释 |
| 不混淆指标 | 没有真实标签或回测指标时明确不支持，不把概率当成准确率 |
| 工具状态明确 | 以下为注册建议，当前代码尚未实现或部署 MCP Server |

### 4.2 工具清单

| 工具名 | 用途 | 入参 | 对应SQL |
|---|---|---|---|
| `get_treasury_1y_data_range` | 结果起止日期和总量 | 无 | 2.1 |
| `get_treasury_1y_previous_date` | 指定日之前最近的已存结果日 | `date`必填 | 2.2 |
| `list_treasury_1y_dates` | 最近N个可用结果日期 | `limit?`，默认20、最大250 | 2.3有限返回版本 |
| `get_treasury_1y_result` | 最新或指定日完整结果 | `date?` | 2.4 / 2.5 |
| `list_treasury_1y_history` | 日期范围最近N期，升序 | `start_date?`、`end_date?`、`limit?`，默认30、最大250 | 2.6数量及明细 |
| `get_treasury_1y_signal_summary` | 收益率、概率、保护与信号摘要 | `date?` | 3.1，或2.4/2.5后提取 |
| `get_treasury_1y_explanation` | 当日已有资金面与技术面解释 | `date?` | 3.2，或2.4/2.5后提取 |

首批可只接入“完整结果、历史序列、已存解释”三个工具；范围、日期和信号摘要按实际问答需求增加。不必为相同数据的每个 SQL 单独注册工具。

历史工具执行2.6后，应在工具层按白名单仅输出日期、收益率、分位、上下行概率、当日阈值、保护状态及资金面/技术面信号等摘要字段，默认不输出每期长解释和来源文本。SQL读取完整JSON与工具对外精简返回是两个步骤；单日完整结果工具仍需保留全部原字段及扩展字段。

### 4.3 工具定义示例

以下 JSON 为 `name`、`description`、`inputSchema` 模板。日期正则只限制格式，工具实现还需解析有效日期并检查起止顺序。

```json
{
  "name": "get_treasury_1y_result",
  "description": "查询1年期国债模型最新或指定参考日期的完整已存结果。date省略取最大signal_date，指定日期不存在则明确未找到。yield_pct为百分比原值，prob_up_7d和prob_down_7d为0至1；资金面和技术面信号均以收益率方向解释。仅读取数据库，不训练、不重算信号。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "参考日期YYYY-MM-DD；省略取最新"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "list_treasury_1y_history",
  "description": "查询1年期国债模型日期闭区间内最近N期已存结果，按日期升序返回。先过滤日期再取最近N条；N表示记录数，不是自然日数。返回matched_count、returned_count和items；历史默认只返回收益率、概率、阈值、保护与信号，不重复返回长解释。null保持为空，不重算历史建议。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "start_date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "起始日期，包含该日；省略不限制起点"
      },
      "end_date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "截止日期，包含该日；省略不限制终点"
      },
      "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 250,
        "default": 30,
        "description": "范围内最近N条结果"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_treasury_1y_explanation",
  "description": "读取1年期国债模型最新或指定日期已经保存的资金面与技术面解释、建议及来源。不调用大模型、不生成新解释、不读取上游来源工作簿；无记录或无解释须明确说明。记录更新时间不等于解释生成时间。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "参考日期；省略取最新，指定日期无数据不回退"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

其他工具可复用上述日期 Schema；无参工具使用空 `properties`，必填日期工具把 `date` 加入 `required`；`list_treasury_1y_dates` 的 `limit` 默认为20、最大250。

### 4.4 建议返回结构

单日工具无数据返回 `found=false`、`data=null`；历史无数据返回空 `items` 和零计数，不制造预测。

以下只展示返回格式，非数据库实时查询结果；为减少篇幅，`result_json` 仅展示部分字段，完整结果工具必须返回完整对象。

```json
{
  "found": true,
  "model_name": "1年期国债模型",
  "units": {
    "yield_pct": "percent",
    "prob_up_7d": "ratio_0_1",
    "prob_down_7d": "ratio_0_1",
    "yield_percentile_756": "ratio_0_1",
    "trade_threshold": "ratio_0_1",
    "yield_change_2d_bp": "bp"
  },
  "data": {
    "signal_date": "2026-10-10",
    "result_json": {
      "signal_date": "2026-10-10",
      "yield_pct": 1.2147,
      "prob_up_7d": 0.6058425728798332,
      "prob_down_7d": 0.3941574271201668,
      "trade_threshold": 0.65,
      "funding_signal": 0,
      "technical_signal": 0
    }
  }
}
```

可额外提供 `prob_up_pct=round(prob_up_7d*100, 2)`、`prob_down_pct` 等展示字段，但保留原始值和明确单位。展示字段不反写数据库，也不能被理解为概率原字段已经改为0—100。

MCP SDK 要求文本内容块时，将返回对象序列化成 JSON 文本；支持结构化内容时直接返回对象。MCP传输地址和注册方式由公司 MCP Server 提供，现有 FastAPI 网页或 Swagger 地址本身不是 MCP 协议地址。

### 4.5 与现有 FastAPI 的对应关系

| 当前接口 | 业务 | MCP处理 |
|---|---|---|
| GET `/research/treasury1y.result` | 指定日或最新完整结果 | 可复用 `get_result()`，把无记录404转换为明确的未找到结果 |
| GET `/research/treasury1y.dates` | 全部日期及范围、数量 | 可复用 `list_dates()`；近期N日工具需自行截取或执行有限SQL |
| GET `/research/treasury1y.history` | 范围历史、最近N条 | 可复用 `list_history()`；`total_count`仅为返回条数，范围总数需额外统计 |
| GET `/research/treasury1y.dashboard` | 完整页面、所选日前走势图 | 可复用 `get_dashboard()`；当前窗口仅30/60/120/9999 |
| POST `/research/treasury1y.importHistory` | 从固定文件导入历史 | 不接入本次只读MCP |
| POST `/research/treasury1y.saveResult` | 保存完整日结果 | 不接入本次只读MCP |

通过 HTTP 封装与直接查询数据库选择一种即可。公司已有标准 Engine 时可直接复用 Repository 的只读方法，或执行本文固定 SELECT；无需修改业务表或模型计算。接口均为同步查询或保存，不涉及202排队、训练状态或轮询。

---

## 五、接入执行与核验

### 5.1 复用公司 SQLAlchemy Engine

下面是2.5的执行示例，不创建新 Engine，不读取数据库环境变量，不使用 ORM：

```python
import json
from datetime import date

from sqlalchemy import text
from infrastructure.repository import engine  # 公司现有 __init__.py 导出的 Engine

sql = """
SELECT signal_date, result_json, created_at, updated_at
FROM fmqt_treasury_1y_history
WHERE signal_date = :signal_date
ORDER BY signal_date DESC
LIMIT 1
"""

# 日期由工具参数解析；不把用户输入拼进SQL。
params = {"signal_date": date.fromisoformat("2026-10-10")}
with engine.connect() as conn:
    row = conn.execute(text(sql), params).mappings().first()

if row is None:
    payload = {"found": False, "model_name": "1年期国债模型", "data": None}
else:
    record = dict(row)
    raw = record["result_json"]
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8")
    record["result_json"] = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(record["result_json"], dict):
        raise ValueError("result_json必须为单日结果对象")
    # SQL DATE/DATETIME转换为可序列化字符串；UTC口径不变。
    for field in ("signal_date", "created_at", "updated_at"):
        if hasattr(record[field], "isoformat"):
            record[field] = record[field].isoformat()
    payload = {"found": True, "model_name": "1年期国债模型", "data": record}
```

只读查询不需要提交写事务。历史 JSON 数字保持原精度；只对额外展示字段四舍五入，不改写已存原值。

### 5.2 查询验收清单

以下以随包124条数据为基线，生产有新增数据时按实际范围核对，不固定最新日期。

| 场景 | 参数或动作 | 预期 |
|---|---|---|
| 范围 | 执行2.1 | 示例包124条，2026-04-10至2026-10-10 |
| 最新完整结果 | 执行2.4 | 最新日期2026-10-10，收益率1.2147%，上行概率约60.58% |
| 指定历史日 | `date=2026-06-30` | 只返回该日已存结果，不套用最新日阈值 |
| 未找到日期 | `date=1900-01-01` | 单日工具 `found=false`；不自动回退 |
| 截止窗口 | `end_date=2026-06-30`、`limit=30` | 升序30条，最后一条2026-06-30，不含后续日期 |
| 页面窗口 | 最新日30/60/120/9999 | 示例包分别返回30/60/120/124条 |
| 周末日期 | `date=2026-09-20` | 示例包可命中，不因周末删除 |
| 解释 | 执行3.2 | 与当日JSON原文一致，不触发生成 |
| 空值 | 抽取存在null的 `yield_change_2d_bp` | 返回null，不返回0或字符串 `"null"` |
| 布尔值 | 独立测试数据分别为0/1/false/true | 正确解释保护状态，true不转成0 |
| 只读性 | 对比查询前后记录时间、数量 | 不新增记录，不改变 `updated_at` |
| 空库 | 在独立空测试库执行 | 统计0、首末日期NULL、列表空、单日未找到 |

空库、布尔和空值边界用独立测试库或固定测试数据验证，不修改正式历史来制造测试场景。当前文档依据源码整理，未连接公司 MySQL 或注册 MCP 工具；真实数据库查询和 MCP 协议测试需在公司环境执行。

### 5.3 SQL与代码位置对应

以下路径相对于当前项目的 `database_service/`：

| 位置或方法 | 本文SQL |
|---|---|
| `infrastructure/repository/treasury_history_repository.py::get_result` | 2.4、2.5 |
| `infrastructure/repository/treasury_history_repository.py::list_dates` | 2.3；2.1为汇总等价模板 |
| `infrastructure/repository/treasury_history_repository.py::list_results` | 2.6、2.7历史部分 |
| `domain/rates/treasury_history_service.py::get_dashboard` | 2.1/2.3 + 2.4/2.5 + 2.7 |
| `domain/rates/treasury_history_service.py::to_page_row` | 2.7页面字段映射，不额外执行SQL |
| `interface/routes/treasury_history.py` | 4.5的6个HTTP接口，查询统一委托Service/Repository |
| `infrastructure/repository/schema.sql` | 1.1实际表结构和中文字段注释 |
| 3.1、3.2 | MCP按需投影建议，当前仓储没有单独实现同名方法 |

详细 HTTP 测试见 [接口测试说明](API_TESTING.md)，公司 Engine、路径和路由迁移见 [迁移代码说明](MIGRATION_GUIDE.md)。
