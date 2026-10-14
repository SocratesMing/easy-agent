# 10年期国债趋势判断的 SQL 参考（供 MCP 接入）

本文档整理10年期国债趋势判断涉及的**查询及新增 SQL**，涵盖「最新预测 / 历史预测 / 模型运行与因子筛选 / 回测指标 / 因子解释」及相关新增记录流程。
每条包含：用途（业务逻辑）、参数、可执行的参数化 SQL、返回字段，并给出对应的 MCP 工具定义建议。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 数据库 | 公司生产环境按 MySQL 8.0 编写；当前本地启动使用 SQLite 仿真 |
| 物理表名 | 运行记录表为 `fmqt_prediction_runs_rates`，预测结果表为 `fmqt_predictions_rates`；本文 SQL、table_config.py 和建表脚本使用同一名称 |
| 日期格式 | `YYYY-MM-DD`，例如 `2026-09-22`；两张表的业务日期类型均为 SQL `DATE` |
| 参考日期 T | `fmqt_predictions_rates.signal_date` 是因子行的参考日期，不等于网页点击时间或下一日上午的实际执行时间 |
| 预测目标 | `10年期国债收益率(T+26) − 10年期国债收益率(T+5)`；当前实现按有序输入数据的行位置位移，应使用交易日数据，不按自然日直接加日期 |
| 真实类别 | 差值大于或等于 0 为 `actual=1`；小于 0 为 `actual=0`；缺少未来数据时为 `NULL` |
| 预测类别 | `probability >= threshold` 为 `prediction=1`，否则为 `0`；真实差值为零归入类别 1 |
| 概率单位 | `probability` 存 0—1 的原始概率；展示 `62.34%` 时使用 `probability × 100` 并保留两位小数 |
| 指标单位 | `accuracy`、`auc`、`high_confidence_accuracy`、`high_confidence_coverage` 存 0—1；准确率、覆盖率可展示百分数，AUC 通常保留 0—1 |
| 局部贡献 | 标准化因子值乘逻辑回归系数，属于对数胜算贡献，不是概率百分点、收益率基点或严格经济因果 |
| 历史分位 | 解释证据的 `percentile_3y` 已经是 0—100，不能再次乘 100 |
| 行位置 | `screening_*`、`train_*`、`cycle_anchor` 是从 0 开始的模型输入行位置；窗口采用左闭右开 `[start, end_exclusive)` |
| 唯一结果 | `fmqt_predictions_rates.signal_date` 唯一；同一天重新写入会更新原行，当前表不是每次模型版本的完整快照历史 |
| 表间关系 | `fmqt_predictions_rates.run_id` 逻辑关联 `fmqt_prediction_runs_rates.id`，没有数据库外键 |
| 参数写法 | `:run_id`、`:signal_date` 等为 SQLAlchemy `text(sql)` 的绑定参数；通过 `conn.execute(text(sql), params)` 执行，不拼接业务值 |
| SQL 客户端执行 | 带 `:参数` 的 SQL 应通过绑定参数执行；在纯 MySQL 客户端手工核验时，替换成实际字面量，例如 `:run_id` 替换为 `1` |
| 时间字段 | 当前运行表和预测表使用 `datetime.utcnow()` 写无时区 `DATETIME`，按 UTC 解释；解释元数据的 `generated_at` 使用带时区的 UTC ISO 字符串 |
| 大字段 | MCP 常规查询全部排除 `csv_gzip`，不使用 `SELECT *`，不读取或解压原始因子 CSV |
| 最新定义 | 最新预测按 `signal_date DESC, id DESC`；最新成功运行按 `id DESC`；最新指标按“成功且 metrics 非空”的 `id DESC`；三者可能不是同一轮 |

**接入范围**：MCP 查询已落库的预测、运行审计、已选因子、已存回测指标及最新一期解释。常规取数不读取 NAS，不训练模型，也不调用大模型。第五节只列出内部新增记录 SQL，供核对数据来源，不列入本次建议的 MCP 工具。

---

## 一、预测结果

**表名**：`fmqt_predictions_rates`（历史及最新预测结果）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INT | 预测记录自增主键 |
| `signal_date` | DATE | 预测参考日期 T，唯一 |
| `run_id` | INT | 写入该记录的运行编号，逻辑关联运行表 |
| `probability` | FLOAT | 收益率上行概率，0—1 |
| `prediction` | INT | 模型分类：1 为上行类别，0 为下行类别 |
| `threshold` | FLOAT | 该条预测实际使用的决策阈值 |
| `strong_signal` | INT，可空 | 1 强看上行；0 强看下行；-1 震荡上行；-2 震荡下行 |
| `signal_label` | VARCHAR(64) | 已生成的中文信号名称 |
| `validation_status` | VARCHAR(16) | `pending` 标签未成熟；`realized` 标签已成熟 |
| `actual` | INT，可空 | 成熟真实类别，1 表示收益率不下降，0 表示收益率下降 |
| `is_correct` | BOOLEAN，可空 | 1 预测正确，0 预测错误，未成熟时为 NULL |
| `factor_version` | VARCHAR(64) | 回测中为筛选版本递增序号；最新预测中为周期锚点，不能统一理解为运行编号 |
| `model_version` | VARCHAR(64) | 回测中为重训版本递增序号；最新预测中为运行编号，不能跨运行单独充当全局唯一模型键 |
| `created_at` | DATETIME | 该日期预测记录首次创建时间 |
| `updated_at` | DATETIME | 最近更新，包括预测覆盖和成熟标签回填 |

> `signal_label` 的分档基于 0.4、0.5、0.6；`prediction` 则基于该条记录的 `threshold`。当前阈值可为 0.4734，因此 0.4734—0.5 之间可能出现 `prediction=1` 而文字为“收益率震荡下行”。MCP 应分别返回原始分类、阈值和中文信号，不擅自把二者改成一致。

### 1.1 最新预测参考日期

**业务**：回答“当前结果更新到了哪一天”。

**来源**：MCP 建议；基于当前预测结果表。

**参数**：无。

```sql
SELECT MAX(signal_date) AS latest_signal_date
FROM fmqt_predictions_rates;
```

**返回**：`latest_signal_date`，无数据时为 NULL。它不是源 CSV 的最新日期；后者见运行表的 `latest_data_date`。

### 1.2 上一个有预测结果的日期

**业务**：比较相邻两期信号。

**来源**：MCP 建议。

**参数**：`signal_date`，必填，`YYYY-MM-DD`。

```sql
SELECT MAX(signal_date) AS previous_signal_date
FROM fmqt_predictions_rates
WHERE signal_date < :signal_date;
```

**返回**：`previous_signal_date`，没有更早记录时为 NULL。取的是“上一个已落库预测日”，不保证等于完整市场日历的上一交易日。

### 1.3 最近 N 个预测日期

**业务**：日期选择、可用数据范围。

**来源**：MCP 建议。

**参数**：`limit`，默认 20，建议 1—250。

```sql
SELECT signal_date
FROM fmqt_predictions_rates
ORDER BY signal_date DESC, id DESC
LIMIT :limit;
```

**返回**：`signal_date` 列表，倒序。日期有唯一约束，不必加 DISTINCT。

### 1.4 最新一期预测

**业务**：最新预测卡片、回答“当前模型看上行还是下行”。

**来源**：当前 `RatesRepository.get_dashboard()` 的最新预测查询。

**参数**：无。

```sql
SELECT
  id, signal_date, run_id, probability, prediction, threshold,
  strong_signal, signal_label, validation_status, actual, is_correct,
  factor_version, model_version, created_at, updated_at
FROM fmqt_predictions_rates
ORDER BY signal_date DESC, id DESC
LIMIT 1;
```

**返回**：上述 15 个字段；空表返回空结果。工具层可额外提供 `probability_up_pct=round(probability*100, 2)`，但仍保留原始 `probability`。

### 1.5 指定日期预测

**业务**：单日历史预测查询；查无该日则明确返回空，不自动换成最近日期。

**来源**：当前 `list_prediction_history(signal_date=...)` 的条件分支。

**参数**：`signal_date`，必填。

```sql
SELECT
  id, signal_date, run_id, probability, prediction, threshold,
  strong_signal, signal_label, validation_status, actual, is_correct,
  factor_version, model_version, created_at, updated_at
FROM fmqt_predictions_rates
WHERE signal_date = :signal_date
ORDER BY signal_date DESC, id DESC
LIMIT 1;
```

**返回**：同 1.4，至多一条。当前 HTTP 接口使用 `date` 参数名，工具层映射为 SQL 的 `signal_date`。

### 1.6 历史预测条数

**业务**：分页总数；启动预测前检查结果表是否为空。

**来源**：当前 `count_predictions()` 与 `list_prediction_history()`。

**参数**：全表统计无参数；日期范围统计传 `start_date`、`end_date`，可为空。

```sql
-- 全表条数，与 count_predictions() 一致。
SELECT COUNT(id) AS total
FROM fmqt_predictions_rates;

-- 日期范围统计；以下是便于 MCP 绑定可空参数的等价模板。
SELECT COUNT(id) AS total
FROM fmqt_predictions_rates
WHERE (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date);
```

**返回**：`total`。范围包括起止日期。当前代码是在参数非空时拼入固定条件，业务值仍绑定；生产实现可沿用该方式以方便优化器使用日期索引。

### 1.7 历史预测分页明细

**业务**：按日期范围查询历史预测。

**来源**：当前 `list_prediction_history()` 的等价可空参数模板。

**参数**：`start_date?`、`end_date?`、`page_size`、`offset`。`page_size` 默认 20，范围 1—100；`offset=(page-1)*page_size`，从 0 开始。空日期必须绑定 `None`，不能省略该 SQL 的绑定值。

```sql
SELECT
  id, signal_date, run_id, probability, prediction, threshold,
  strong_signal, signal_label, validation_status, actual, is_correct,
  factor_version, model_version, created_at, updated_at
FROM fmqt_predictions_rates
WHERE (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date)
ORDER BY signal_date DESC, id DESC
LIMIT :page_size OFFSET :offset;
```

**返回**：15 个预测字段。工具组合 1.6 的总数后返回 `total`、`page`、`page_size`、`items`。传入单日 `date` 时与现接口一致，优先按 1.5 查询，忽略起止日期；该情况下 `total` 为 0 或 1。

### 1.8 最近 N 期预测趋势

**业务**：收益率上行概率与中文信号的近期变化。

**来源**：MCP 建议。

**参数**：`limit`，默认 20，建议 1—250。

```sql
SELECT signal_date, probability, prediction, threshold,
       signal_label, validation_status, actual, is_correct, run_id
FROM fmqt_predictions_rates
ORDER BY signal_date DESC, id DESC
LIMIT :limit;
```

**返回**：上述字段，SQL 为倒序；需要绘图时由工具层反转为升序。未成熟标签不补成 0。

### 1.9 尚未验证的预测

**业务**：回答“目前有哪些预测还没有到验证期”。

**来源**：MCP 建议。

**参数**：`limit`，默认 30，建议 1—100。

```sql
SELECT signal_date, probability, prediction, threshold,
       signal_label, validation_status, actual, is_correct, run_id
FROM fmqt_predictions_rates
WHERE validation_status = 'pending'
ORDER BY signal_date DESC, id DESC
LIMIT :limit;
```

**返回**：未成熟预测列表。不要把它们作为错误预测或加入准确率分母。

---

## 二、模型运行、筛选因子与训练审计

**表名**：`fmqt_prediction_runs_rates`（运行记录及审计信息）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INT | 运行自增主键；查询结果通常别名为 `run_id` |
| `run_type` | VARCHAR(16) | `BOOTSTRAP` 全量回测；`LATEST` 最新一期预测 |
| `status` | VARCHAR(16) | `QUEUED`、`RUNNING`、`SUCCEEDED`、`FAILED` |
| `stage` | VARCHAR(64) | 等待执行、READING_CSV、BACKTESTING、TRAINING、EXPLAINING_FACTORS、COMPLETED、FAILED 等阶段 |
| `error_message` | TEXT，可空 | 模型运行错误摘要 |
| `source_file_name` | VARCHAR(512) | 原始因子 CSV 文件名 |
| `file_sha256` | VARCHAR(64) | 原始 CSV 内容摘要，唯一 |
| `source_size_bytes` | INT | 原始 CSV 字节数 |
| `csv_gzip` | LONGBLOB，可空 | CSV 的 gzip 二进制留档；不用于 MCP 常规取数 |
| `row_count` | INT，可空 | 配置起始日期过滤后的模型输入行数 |
| `column_count` | INT，可空 | 当前代码的审计列数，具体口径见下方提示 |
| `latest_data_date` | DATE，可空 | 本次模型输入的最新参考日期 |
| `cycle_anchor` | INT，可空 | 当前筛选周期锚点行位置 |
| `screening_start` | INT，可空 | 筛选窗口起始位置，含该行 |
| `screening_end_exclusive` | INT，可空 | 筛选窗口结束位置，不含该行 |
| `mature_screening_count` | INT，可空 | 当前保存的筛选窗口成熟样本数 |
| `train_start` | INT，可空 | 训练窗口起始位置，含该行 |
| `train_end_exclusive` | INT，可空 | 训练窗口结束位置，不含该行 |
| `train_end_date` | DATE，可空 | 训练窗口最后一行日期 |
| `selected_factor_count` | INT，可空 | 最后一轮模型入选因子数 |
| `selected_factors` | JSON，可空 | 最后一轮模型入选因子名称数组 |
| `best_params` | JSON，可空 | 最后一轮训练的最优超参数 |
| `decision_threshold` | FLOAT，可空 | 最后一轮模型分类阈值 |
| `config_hash` | VARCHAR(64)，可空 | 配置内容摘要 |
| `code_hash` | VARCHAR(64)，可空 | 核心代码摘要 |
| `metrics` | JSON，可空 | 该次全量回测保存的评价指标；LATEST 当前写 NULL |
| `factor_explanation` | TEXT，可空 | 该次运行最新一期的解释正文 |
| `explanation_metadata` | JSON，可空 | 解释状态、模型名、生成时间、证据等 |
| `created_at` | DATETIME | 运行记录创建时间 |
| `started_at` | DATETIME，可空 | 运行开始时间 |
| `finished_at` | DATETIME，可空 | 运行结束时间 |

> 当前 `column_count` 按带标签 DataFrame 的 `len(frame.columns)-2` 写入，扣除了新增的差值列与标签列，但原收益率列仍保留；不能直接称为“纯因子数量”。实际入模因子数量使用 `selected_factor_count`。本文按代码实际口径说明，没有调整现有计算。

> 全量回测通常把许多历史预测写到同一个 `run_id` 下，而运行表只保留回测最后一轮的因子、超参数和训练窗口。按某条早期历史预测的 `run_id` 查询，得到的是该运行末轮审计，不能声称得到该历史日期的精确入模因子。最新一期 LATEST 一轮一条预测时可以直接对应。

### 2.1 按运行编号查询状态

**业务**：任务轮询、查询失败原因和解释状态。

**来源**：当前 `get_run_detail()`。

**参数**：`run_id`，正整数。

```sql
SELECT
  id, run_type, status, stage, error_message, source_file_name,
  latest_data_date, row_count, column_count, cycle_anchor,
  mature_screening_count, train_end_date, selected_factor_count,
  explanation_metadata, created_at, started_at, finished_at
FROM fmqt_prediction_runs_rates
WHERE id = :run_id;
```

**返回**：上述字段。现接口将 `id` 映射为 `run_id`，并从元数据提取 `explanation_status`。预测任务的终态为 `SUCCEEDED` 或 `FAILED`；解释有自己的状态，不能只看 `stage` 判断整体是否仍在训练。

### 2.2 最新成功运行

**业务**：看板的 `latest_run`。

**来源**：当前 `get_dashboard()`。

**参数**：无；代码中的 `status` 固定绑定为 `SUCCEEDED`。

```sql
SELECT
  id, run_type, status, stage, error_message, source_file_name,
  latest_data_date, row_count, column_count, cycle_anchor,
  mature_screening_count, train_end_date, selected_factor_count,
  explanation_metadata, created_at, started_at, finished_at
FROM fmqt_prediction_runs_rates
WHERE status = 'SUCCEEDED'
ORDER BY id DESC
LIMIT 1;
```

**返回**：同 2.1。正在运行或失败的任务不会在该查询中出现。

### 2.3 最新活动任务

**业务**：点击预测前防重，避免重复排队。

**来源**：当前 `find_active_run()`。

**参数**：无。

```sql
SELECT id, status, run_type
FROM fmqt_prediction_runs_rates
WHERE status IN ('QUEUED', 'RUNNING')
ORDER BY id DESC
LIMIT 1;
```

**返回**：`id`、`status`、`run_type`。Repository 对外将 `id` 改名为 `run_id`。

### 2.4 按文件内容摘要查询已有任务

**业务**：相同 CSV 内容复用已有运行；失败任务可重新排队。

**来源**：当前 `find_run_by_file_hash()`。

**参数**：`file_sha256`，64 位十六进制摘要。

```sql
SELECT id, status, run_type
FROM fmqt_prediction_runs_rates
WHERE file_sha256 = :file_sha256;
```

**返回**：`id`、`status`、`run_type`。该查询用于内部去重，不要求作为首批 MCP 工具开放。

### 2.5 内部状态与存在性检查

**业务**：按运行编号读取状态、文件摘要和解释元数据。

**来源**：当前 `_require_run()`。

**参数**：`run_id`。

```sql
SELECT id, run_type, status, stage, file_sha256, selected_factor_count,
       selected_factors, factor_explanation, explanation_metadata
FROM fmqt_prediction_runs_rates
WHERE id = :run_id;
```

**返回**：上述字段；无记录时 Repository 抛出 `RepositoryNotFoundError`。

### 2.6 最新成功运行的入选因子

**业务**：查询当前最近一次保存的因子清单；模型增量筛选时复用上一轮因子。

**来源**：当前 `get_latest_successful_factors()`。

**参数**：无。

```sql
SELECT selected_factors
FROM fmqt_prediction_runs_rates
WHERE status = 'SUCCEEDED'
  AND selected_factor_count IS NOT NULL
  AND selected_factor_count > 0
ORDER BY id DESC
LIMIT 1;
```

**返回**：`selected_factors`，工具层解码为字符串数组；无记录返回空数组。

MCP 若需要明确来源编号，可采用以下建议查询：

```sql
SELECT id AS run_id, run_type, latest_data_date,
       selected_factor_count, selected_factors
FROM fmqt_prediction_runs_rates
WHERE status = 'SUCCEEDED'
  AND selected_factor_count IS NOT NULL
  AND selected_factor_count > 0
ORDER BY id DESC
LIMIT 1;
```

### 2.7 指定运行的因子与训练审计

**业务**：回答“本轮用哪些因子、用多少成熟样本、训练截止到哪天”。

**来源**：MCP 建议；字段已经在当前运行完成时落库。

**参数**：`run_id`。

```sql
SELECT
  id AS run_id, run_type, status, stage,
  source_file_name, file_sha256, source_size_bytes,
  latest_data_date, row_count, column_count,
  cycle_anchor, screening_start, screening_end_exclusive,
  mature_screening_count, train_start, train_end_exclusive, train_end_date,
  selected_factor_count, selected_factors, best_params, decision_threshold,
  config_hash, code_hash, created_at, started_at, finished_at
FROM fmqt_prediction_runs_rates
WHERE id = :run_id;
```

**返回**：上述审计字段，不含压缩文件、解释证据或正文。失败或未完成任务的部分字段可能为空；不把空值替换成 0。

### 2.8 最新预测所属运行

**业务**：把“最新预测”准确关联到因子解释和运行审计。

**来源**：当前 `prepare_explanation_regeneration()` 的最新运行检查。

**参数**：无。

```sql
SELECT run_id
FROM fmqt_predictions_rates
ORDER BY signal_date DESC, id DESC
LIMIT 1;
```

**返回**：`run_id`。不要直接使用 `MAX(fmqt_prediction_runs_rates.id)` 代替，最大运行可能失败或尚未产生结果。

### 2.9 看板关联的运行展示信息

**业务**：最新预测的因子数、训练截止日和解释数据。

**来源**：当前 `get_dashboard()` 的 `prediction_run` 查询。

**参数**：`run_id`，来自 1.4 或 2.8。

```sql
SELECT id, selected_factor_count, train_end_date,
       factor_explanation, explanation_metadata
FROM fmqt_prediction_runs_rates
WHERE id = :run_id;
```

**返回**：上述字段。没有关联记录时保留预测并说明运行审计缺失；不得按另一轮成功记录补解释。

---

## 三、回测指标

**存储位置**：`fmqt_prediction_runs_rates.metrics`，JSON 对象。没有单独的指标表。

| JSON 字段 | 含义与单位 |
|---|---|
| `total_count` | 该次回测预测总行数 |
| `realized_count` | `status=realized` 且 `actual` 非空的样本数 |
| `pending_count` | 尚未成熟预测数 |
| `accuracy` | 成熟样本分类准确率，0—1，无成熟样本为 NULL |
| `auc` | 成熟样本真实类别与原始上行概率的 ROC AUC，0—1；成熟标签不足两类为 NULL |
| `high_confidence_count` | 成熟样本中 `probability>0.6` 或 `<0.4` 的样本数 |
| `high_confidence_accuracy` | 上述强信号样本的分类准确率，0—1；无强信号成熟样本为 NULL |
| `high_confidence_coverage` | 强信号成熟样本数除以全部成熟样本数，0—1；无成熟样本为 0 |

### 3.1 最近一次保存的成功回测指标

**业务**：看板“回测 AUC、准确率等指标”。

**来源**：当前 `get_dashboard()`。

**参数**：无。

```sql
SELECT metrics
FROM fmqt_prediction_runs_rates
WHERE status = 'SUCCEEDED' AND metrics IS NOT NULL
ORDER BY id DESC
LIMIT 1;
```

**返回**：`metrics`，工具层解码 JSON。MCP 建议同时返回指标来源，避免误认成最新预测轮次：

```sql
SELECT id AS metrics_run_id, run_type, latest_data_date AS metrics_data_end,
       finished_at AS metrics_recorded_at, metrics
FROM fmqt_prediction_runs_rates
WHERE status = 'SUCCEEDED' AND metrics IS NOT NULL
ORDER BY id DESC
LIMIT 1;
```

> LATEST 成功运行当前写 `metrics=NULL`，成熟标签回填不会重新计算之前保存的 metrics。上述结果是最近一次保存的回测快照，不是“截至当前数据库最新状态重新计算的累计指标”。

### 3.2 指定运行保存的指标

**业务**：核对某次全量回测指标。

**来源**：MCP 建议。

**参数**：`run_id`。

```sql
SELECT id AS metrics_run_id, run_type, status, latest_data_date, metrics
FROM fmqt_prediction_runs_rates
WHERE id = :run_id;
```

**返回**：运行标识及 `metrics`。任务存在但指标为空，与任务不存在是不同情况；空指标不能转换为 AUC=0 或准确率=0。

### 3.3 重新计算指标所需的成熟样本（可选扩展）

**业务**：后续若需要按期间重新计算表现，可取实际成熟样本。

**来源**：MCP 建议；当前看板没有使用该查询重新计算指标。

**参数**：`start_date`、`end_date`，必填闭区间；扩展工具应明确期间，避免误称为原回测快照。

```sql
SELECT signal_date, probability, prediction, actual, is_correct,
       threshold, run_id, factor_version, model_version
FROM fmqt_predictions_rates
WHERE signal_date >= :start_date
  AND signal_date <= :end_date
  AND validation_status = 'realized'
  AND actual IS NOT NULL
ORDER BY signal_date ASC, id ASC;
```

**返回**：成熟样本。准确率使用 `prediction==actual`；AUC 使用 `actual` 和 `probability`，不能用硬分类的平均值或准确率替代 AUC。只有一种真实类别时 AUC 为 NULL。强信号采用严格 `>0.6`、`<0.4`，等于边界不算强信号。

该扩展不在首批工具中；若后续实现，应在工具内计算后返回汇总，不把大批样本全部交给模型上下文。当前表已覆盖同日旧版本，重算代表当前表中的有效预测，不保证恢复某次旧运行当时的全部样本快照。

---

## 四、最新一期因子解释与量化证据

**存储位置**：`fmqt_prediction_runs_rates.factor_explanation` 和 `explanation_metadata`。解释正文及其元数据保存在运行记录表中，无需单独的解释表。

### 4.1 最新预测对应的已生成解释

**业务**：回答“当前预测有哪些因子驱动”；只读取已有解释，保持现有页面只展示最新一期的口径。

**来源**：当前看板查询的单条 SQL 等价组合；MCP 建议。

**参数**：无，不提供历史解释日期参数。

```sql
SELECT id AS run_id, factor_explanation, explanation_metadata
FROM fmqt_prediction_runs_rates
WHERE id = (
  SELECT run_id
  FROM fmqt_predictions_rates
  ORDER BY signal_date DESC, id DESC
  LIMIT 1
);
```

**返回**：`run_id`、`factor_explanation`、`explanation_metadata`。工具层建议整理成 `run_id`、`status`、`content`、`generated_at`、`has_content`、`is_retained_content`；其中后两个是建议新增的展示字段。

| 元数据字段 | 含义 |
|---|---|
| `status` | QUEUED、RUNNING、SUCCEEDED、FAILED、DISABLED；缺少旧版元数据时展示层使用 LEGACY |
| `model` | 解释使用的大模型标识 |
| `provider_mode` | 当前完整文本返回模式 `non_streaming` |
| `prompt_sha256` | 使用提示词的内容摘要 |
| `generated_at` | 最近一次解释生成尝试记录的 UTC ISO 时间，不一定是数据库保留正文的成功生成时间 |
| `evidence` | 结构化量化证据，JSON 对象；禁用解释或证据提取失败时可能为空 |
| `error` | 解释失败的原因，可能为空；常规取数不必返回供应商错误全文 |

**状态处理**：

| 情况 | 工具应表达的含义 |
|---|---|
| 最新预测不存在 | 没有预测，也没有该期解释 |
| 运行存在但没有解释元数据 | LEGACY，旧记录没有该功能数据 |
| status=SUCCEEDED | 最新解释调用成功，返回已存正文 |
| status=QUEUED/RUNNING | 解释正在排队或生成；如正文非空，它可能是上一次成功内容 |
| status=FAILED 且正文非空 | 最新尝试失败，保留上次成功正文；不得把该正文称为本次新生成内容 |
| status=FAILED 且正文为空 | 当前没有可展示的成功正文 |
| status=DISABLED | 解释功能未启用，不自动补写市场结论 |

`is_retained_content` 可按“正文非空且当前解释状态不是 SUCCEEDED”给出保守提示。现有数据没有单独保存成功正文时间；不要根据失败尝试的 `generated_at` 推断旧正文生成时间。此查询不触发重新生成，不回退到其他预测日期的解释。

### 4.2 最新一期解释证据

**业务**：给 MCP 返回因子归类、近三年历史分位及同向局部贡献。

**来源**：MCP 建议。证据已经随当前运行审计保存。

**参数**：无。

```sql
SELECT id AS run_id,
       JSON_EXTRACT(explanation_metadata, '$.evidence') AS evidence
FROM fmqt_prediction_runs_rates
WHERE id = (
  SELECT run_id
  FROM fmqt_predictions_rates
  ORDER BY signal_date DESC, id DESC
  LIMIT 1
);
```

**返回**：`run_id`、`evidence`。该 SQL 使用 MySQL JSON 函数；也可直接使用 4.1，工具层解析 `explanation_metadata.evidence`，不依赖数据库 JSON 函数。

**证据对象**：

| 字段 | 含义 |
|---|---|
| `signal_date` | 证据所属预测参考日期 |
| `target` | 锚定 T 的 T+26 相对 T+5 收益率涨跌 |
| `probability_up` | 收益率上行概率，0—1 |
| `prediction` | 模型分类，0 或 1 |
| `signal_label` | 中文信号 |
| `factors` | 已保存的候选因子数组 |

**`factors` 数组元素**：

| 字段 | 含义 |
|---|---|
| `name` | 完整模型因子列名，保留英文后缀，不能改写 |
| `display_name` | 因子中文说明 |
| `category` | 政策面、资金面、宏观基本面、供需面、情绪与技术面之一 |
| `operator` | 原始算子标识，如 raw、mean、deviation、quantile_pos |
| `window` | 衍生窗口长度，可能为 NULL |
| `percentile_3y` | 截至该期最近最多 756 行的平均排名百分位，0—100 |
| `local_contribution` | 标准化值乘系数的局部对数胜算贡献，保留正负号 |
| `supports_signal` | 是否支持模型分类方向；当前候选只保留同向因子 |
| `contribution_rank` | 在全部同向因子中的贡献绝对值排名，旧版证据可能没有该字段 |

> 当前证据先取全部同向因子绝对贡献前 20，再删除不能可靠归类的项，不从后续名次补位。因此候选可少于 20、排名可不连续，也可能为空。它不是全部入模因子，不是大模型正文最终选用因子的独立结构化清单。

> “每个维度最多三个因子”和“不超过 750 字”约束发生在解释提示词中，证据数组本身并不按每个维度三个截断。MCP 查询应按已存证据如实返回，不在取数时把候选重新生成或当成最终经济因果。

**只读要求**：不要调用现有 `RatesRepository.get_explanation_evidence()` 来实现 MCP 读取。该方法会把解释状态写成 RUNNING，并把运行阶段改为 EXPLAINING_FACTORS；MCP 应执行本节 SELECT，或者复用 4.1 的结果。

### 4.3 衍生方法中文展示

**业务**：保留因子原名，同时让模型或用户理解其衍生方法。

**来源**：当前 `_PROMPT_OPERATOR_DESCRIPTIONS`；无需执行额外 SQL。

| operator | 中文含义 |
|---|---|
| raw | 原始值 |
| mean | 滚动均值 |
| mean_diff | 滚动均值的一阶变化 |
| std | 滚动标准差 |
| diff | 单期绝对变化 |
| fdiff | 0.8 阶分数差分 |
| slope | 线性趋势斜率 |
| ema | 指数移动均值 |
| deviation | 相对滚动均值的标准化偏离 |
| cum_sum | 窗口内单期变化累计值 |
| range | 窗口最大值减窗口均值 |
| upratio | 窗口内上涨观测占比 |
| downratio | 窗口内下跌观测占比 |
| quantile_pos | 滚动窗口平均排名分位 |
| zscore | 偏离滚动均值的标准差倍数 |
| robust_scaled | 相对滚动中位数和四分位距的稳健标准化位置 |

---

## 五、附：模型内部新增 SQL 清单

本节只列出新增记录的 SQL，供核对现有数据来源；不注册为本次只读取数的 MCP 工具，也不要求 MCP Server 执行。以下固定状态值与当前代码绑定值等价，业务参数仍通过绑定参数传入。

### 5.1 创建任务

**业务**：新增一条排队任务记录。

**来源**：`create_run()`。

**参数**：`run_type`、`source_file_name`、`file_sha256`、`source_size_bytes`、`created_at`，其中时间为 UTC。

```sql
INSERT INTO fmqt_prediction_runs_rates (
  run_type, status, stage, source_file_name, file_sha256,
  source_size_bytes, created_at
) VALUES (
  :run_type, 'QUEUED', '等待执行', :source_file_name, :file_sha256,
  :source_size_bytes, :created_at
);
```

**返回**：`result.lastrowid` 为 `run_id`；正常退出连接上下文时提交。

### 5.2 新增预测记录

**业务**：保存尚未存在的信号日期所对应的预测。

**来源**：`_upsert_prediction()` 中的新增记录分支；本节仅列新增 SQL。

**参数**：`signal_date`、`run_id`、`probability`、`prediction`、`threshold`、`strong_signal`、`signal_label`、`validation_status`、`actual`、`is_correct`、`factor_version`、`model_version`、`created_at`、`updated_at`。字段含义及单位见第一章。

```sql
INSERT INTO fmqt_predictions_rates (
  signal_date, run_id, probability, prediction, threshold,
  strong_signal, signal_label, validation_status, actual, is_correct,
  factor_version, model_version, created_at, updated_at
) VALUES (
  :signal_date, :run_id, :probability, :prediction, :threshold,
  :strong_signal, :signal_label, :validation_status, :actual, :is_correct,
  :factor_version, :model_version, :created_at, :updated_at
);
```

**返回**：无对外业务行。该 SQL 只适用于尚无记录的信号日期；`signal_date` 有唯一约束，不能对已存在日期重复执行新增。实际预测服务负责判断新增分支，全量回测可逐条新增多期预测，最新预测可新增一期。

### 5.3 本文 SQL 与 Repository 方法对应

| 方法 | 本文所列 SQL |
|---|---|
| `find_active_run` | 2.3 |
| `find_run_by_file_hash` | 2.4 |
| `count_predictions` | 1.6 全表条数 |
| `create_run` | 5.1 |
| `get_run_detail` | 2.1 |
| `get_latest_successful_factors` | 2.6 |
| `get_dashboard` | 1.4、2.2、3.1、2.9 |
| `list_prediction_history` | 1.5、1.6、1.7 |
| `_require_run` | 2.5 的状态读取 |
| `_upsert_prediction` | 5.2，仅对应新增分支 |

---

## 六、MCP 工具映射建议

### 6.1 设计原则

| 原则 | 说明 |
|---|---|
| 按问题划分能力 | 查询最新预测、历史预测、运行审计、回测指标和最新解释分别提供工具 |
| 日期统一 | 使用当前数据库的 `YYYY-MM-DD`；缺省日期只在明确约定的工具中表示最新 |
| 读取已存数据 | 不自动开始预测、训练或解释生成，不提供任意 SQL 输入参数 |
| 明确来源 | 返回 `run_id`、`signal_date` 或 `metrics_run_id`，区分不同轮次的数据 |
| 保留原始数值 | 概率保留 0—1，展示可加百分数；证据分位已经是 0—100 |
| 有限返回 | 历史分页每页不超过 100，近期趋势不超过 250；解释证据最多为已保存候选池 |
| 复用连接 | 使用公司已有 SQLAlchemy Engine，通过 `engine.connect()` 取连接 |
| 固定 SQL 和绑定参数 | 表名来自已配置常量；日期、编号、分页值通过绑定参数，不接受模型传表名或 SET 字段 |
| 按需查询 | 不返回 csv_gzip，不解压 CSV；通常一条 SQL，分页总数与明细允许两条查询 |
| 实际测试性能 | 根据实际数据量、索引和公司网络环境测量查询耗时，确定连接池配置 |

### 6.2 工具清单

以下全部为建议工具名，当前代码尚未注册。首批可先实现“预测、历史、运行状态、已存指标、最新解释”五项，其余按需要加入。

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_rates_latest_signal_date` | 最新已落库预测参考日期 | 无 | 1.1 |
| `get_rates_previous_signal_date` | 指定日期之前最近的预测日期 | `date` | 1.2 |
| `get_rates_prediction` | 最新或指定参考日期预测 | `date?` | 1.4 / 1.5 |
| `list_rates_predictions` | 日期区间分页历史预测 | `date?`、`start_date?`、`end_date?`、`page?`、`page_size?` | 1.5 / 1.6 + 1.7 |
| `get_rates_run_status` | 指定运行的任务状态 | `run_id` | 2.1 |
| `get_rates_run_audit` | 指定运行的因子与训练审计 | `run_id` | 2.7 |
| `get_rates_selected_factors` | 最近成功运行的入选因子 | 无 | 2.6 MCP 建议查询 |
| `get_rates_backtest_metrics` | 最近保存的成功回测指标及来源 | 无 | 3.1 MCP 建议查询 |
| `get_rates_latest_explanation` | 最新预测对应的已存解释及状态 | 无 | 4.1 |
| `get_rates_latest_factor_evidence` | 最新预测对应的因子候选证据 | 无 | 4.2，或 4.1 后解析 JSON |

1.3、1.8、1.9、3.2、3.3 为备选 SQL，首批无需为每条另建工具。分页接口已有历史查询能力，是否新增趋势或未成熟查询工具由实际问答需求决定。

### 6.3 工具定义示例

以下 JSON 可直接用作工具注册的 `name`、`description`、`inputSchema` 模板。日期正则只约束格式，工具实现还需解析为有效日期。

```json
{
  "name": "get_rates_prediction",
  "description": "查询10年期国债收益率择时模型在指定参考日T的预测；date省略则取最新已落库预测。目标为T+26相对T+5的收益率涨跌，T+k按输入交易日行位置计算。probability为0至1的收益率上行概率，prediction为按threshold分类的0或1，signal_label为已存中文信号。仅读取结果，不训练模型。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "预测参考日期YYYY-MM-DD；省略取最新，指定日期无数据则返回空"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "list_rates_predictions",
  "description": "分页查询已落库的历史预测，返回total、page、page_size和items。date优先于日期区间；范围包含两端。pending表示真实标签尚未成熟，不计入预测正确率。概率单位为0至1。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "单日预测参考日期；提供时忽略start_date和end_date"
      },
      "start_date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "起始参考日期，包含该日"
      },
      "end_date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "结束参考日期，包含该日"
      },
      "page": {"type": "integer", "minimum": 1, "default": 1},
      "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_rates_run_status",
  "description": "查询指定模型运行的状态、阶段、失败原因及解释状态。status的终态为SUCCEEDED或FAILED，解释状态独立。仅读取，不重启任务。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "run_id": {"type": "integer", "minimum": 1, "description": "预测运行编号"}
    },
    "required": ["run_id"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_rates_latest_explanation",
  "description": "读取最新已落库预测对应的内部因子解释正文及当前解释状态。失败或生成中时可能保留上一次成功正文，应明确标识为保留内容。不会重新生成解释，不查询其他历史日期的解释。",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_rates_latest_factor_evidence",
  "description": "读取最新预测已保存的同向因子候选证据，包括完整因子名、维度、近三年百分位及局部贡献。percentile_3y已经为0至100；local_contribution为对数胜算贡献，不是收益率变化或概率百分点。候选不是全部入模因子，也不保证全部用于解释正文。不会修改解释状态或调用大模型。",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": false
  }
}
```

其余工具参数遵循 6.2：无参工具使用最后两个示例的空对象 schema；`get_rates_run_audit` 使用与 `get_rates_run_status` 相同的正整数 run_id schema；`get_rates_previous_signal_date` 的 date 为必填。

### 6.4 建议返回结构

常规工具统一返回 JSON 对象；无数据时明确 `found=false`，不编造预测。下例只说明格式，不代表数据库实测结果：

```json
{
  "found": true,
  "target": "锚定参考日T，预测T+26相对T+5的10年期国债收益率涨跌",
  "probability_unit": "ratio_0_1",
  "data": {
    "signal_date": "2026-09-22",
    "run_id": 12,
    "probability": 0.6234,
    "probability_up_pct": 62.34,
    "prediction": 1,
    "threshold": 0.4734,
    "signal_label": "收益率强看上行",
    "validation_status": "pending",
    "actual": null,
    "is_correct": null
  }
}
```

MCP SDK 如要求返回文本内容块，可以把该对象序列化成 JSON 文本；支持结构化内容时直接返回对象。无需让 MCP 再把解释正文改写一遍。协议传输地址、注册方式及公司 MCP Server 的具体配置由接入方提供，本地 FastAPI 地址不是 MCP 服务地址。

### 6.5 与现有 FastAPI 的对应关系

| 当前接口 | 业务 | MCP 接入处理 |
|---|---|---|
| GET `/research/rates.dashboard` | 最新预测、最近保存指标、最新成功运行、最新解释 | 可复用只读 Repository；或者按本文件拆成业务工具，明确各部分来源不同 |
| GET `/research/rates.history` | 单日或范围历史查询 | 可复用 `list_prediction_history()` |
| GET `/research/rates.runStatus` | 运行状态 | 可复用 `get_run_detail()` |
| POST `/research/rates.predict` | 读取 CSV、排队训练和预测 | 不属于本次数据查询工具 |
| POST `/research/rates.regenerateExplanation` | 重新调用大模型并覆盖解释 | 不属于本次数据查询工具 |

通过 HTTP 封装与直接查询数据库二选一即可。公司已有 Engine 和 SQL 执行约定时，直接使用固定 SELECT 能复用现有基础设施；不需要为了 MCP 改模型或业务表。
