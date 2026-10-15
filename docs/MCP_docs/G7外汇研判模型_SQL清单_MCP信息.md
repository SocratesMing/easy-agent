# G7外汇研判模型的 SQL 参考（供 MCP 接入）

本文档整理G7外汇研判模型已落库结果涉及的**全部查询 SQL 场景**，涵盖「最新研判 / 历史研判 / 六币种概率与信号 / 历史汇价走势 / 回测指标 / 已存因子解释」。
每条包含：用途（业务逻辑）、参数、可执行的参数化 SQL、返回字段，并给出对应的 MCP 工具定义建议。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 数据库 | SQL按公司MySQL 8.0编写；本地服务为SQLite仿真，DATE_SUB、JSON_TABLE等MySQL函数不能直接照搬到SQLite |
| 物理表名 | `fmqt_g7_fx_history`，与仓储 `TABLE_NAME` 及建表脚本一致 |
| 存储方式 | 一份“信号日期＋预测期限”快照占一行；六币种完整结果保存于 `result_json` 的 `records` 数组，不是六行数据库记录 |
| 唯一键 | `(signal_date, horizon_days)` 为复合主键；当前不保存同键多个模型版本的独立历史快照 |
| 日期格式 | `YYYY-MM-DD`；外层SQL DATE与每条JSON记录的signal_date应一致 |
| 最新定义 | 对指定horizon_days取最大signal_date，不按系统今天、文件修改时间或updated_at取最新 |
| 正式期限 | 当前只保存22交易日结果；5/10期限在现有服务中直接返回空，不查询数据库，也不借用22日结果 |
| 预测到期日期 | forecast_date为上游已存日期；随包结果注明按工作日估算，不能解释为严格外汇市场交易日历或已兑现日期 |
| 页面历史窗口 | 对该期限库内最新信号日向前回溯一个自然月，包含下界；不是最近30天、30条记录或系统当前月份 |
| 月底边界 | 3月31日回溯至2月28/29日；仓储用Python日历计算floor，本文MySQL DATE_SUB模板与其等价 |
| 存档保留 | 旧结果仍在数据库；超出最近月窗口只是不在现页面展示，没有删除记录 |
| 六币种顺序 | EURUSD、GBPUSD、AUDUSD、USDJPY、USDCAD、USDCHF；按Symbol识别，不假设records[0]永久代表EURUSD |
| 汇价单位 | Close为对应货币对报价，如EURUSD=1.1155；不是收益率百分数 |
| 概率及比率 | prob_up、return5、rv20、test_accuracy等为小数比率，展示百分数时乘100；AUC通常保留0—1 |
| 信号含义 | 强偏多、偏多、中性、偏空、强偏空描述该货币对报价方向；读取保存值，不根据概率重新推算分档 |
| 美元方向 | EURUSD/GBPUSD/AUDUSD上涨是非美元货币对美元走强；USDJPY/USDCAD/USDCHF上涨是美元对后者走强，不能统一称美元上涨 |
| 因子影响 | impact_pp已是概率百分点，不再乘100；为固定模型中位数置换敏感度，不可相加成收益贡献，不是经济因果 |
| 空值 | 缺失字段与JSON null表示没有该数据，不改成0；没有归因数组不等于因子影响为零 |
| 完整与展示 | 完整JSON保留所有扩展字段和显式null；现页面响应仅对可选文本/数组补展示默认值，不反写数据库 |
| 时间 | created_at、updated_at以UTC写入无时区DATETIME(6)，现接口返回带+00:00的ISO字符串 |
| 参数绑定 | `:horizon`、`:signal_date`等为SQLAlchemy text(sql)参数，使用conn.execute(text(sql), params)，业务值不拼接SQL |
| 可空参数 | 未传入的可空参数绑定Python None；不能省略SQL要求的绑定键，也不能传字符串“null” |
| 手工SQL执行 | MySQL客户端不直接识别:参数；手工核验时替换成实际字面量，应用程序使用绑定参数 |
| 多条查询 | 同一代码块若列出多条SELECT，应拆开逐条conn.execute，不作为一个多语句SQL字符串执行 |

**接入范围**：MCP仅读取已有数据库结果，不读取NAS或快照文件，不抓取彭博，不训练模型，不重算回测，不调用大模型。本文SQL代码块只包含SELECT查询，不包含新增、更新、删除或建表操作，也不注册写入工具。

**解释边界**：历史回溯恢复快照不是当日正式发布存档，已有AUC和命中率也不代表独立前瞻验证。始终保留snapshot_kind、evaluation_note和source_*。maturity_copy可能说明此前信号的到期验证，不表示当前forecast_date对应预测已经兑现。

---

## 一、表结构与结果字段

**表名**：`fmqt_g7_fx_history`。无ORM、无数据库外键。

### 1.1 数据库字段

| 字段 | 类型 | 含义 |
|---|---|---|
| signal_date | DATE，非空 | 信号参考日期，复合主键的一部分 |
| horizon_days | SMALLINT UNSIGNED，非空 | 预测期限，复合主键的一部分，当前正式结果为22 |
| result_json | JSON，非空 | 完整快照文档，包括六币种records和所有扩展内容 |
| created_at | DATETIME(6)，非空 | 该日期/期限首次写入时间，UTC |
| updated_at | DATETIME(6)，非空 | 最近写入时间，UTC；只读查询不改变 |

一行是一个日期/期限的完整快照，不能把COUNT(*)直接说成货币对记录数。每份正常快照含6条币种记录，因此随包21行对应126条币种结果。

### 1.2 result_json 顶层结构

| 字段 | 含义 |
|---|---|
| schema_version | 当前结构版本1；保存时缺省按1处理 |
| snapshot_kind | 已存快照类型；historical_reconstruction表示历史回溯恢复 |
| records | 六个货币对的完整结果数组 |
| 其他字段 | 上游扩展元信息，完整查询须保留 |

最新随包文档只有schema_version和records，没有snapshot_kind，现服务在展示时默认archived_snapshot；不能因此判定数据缺失，也不把该默认值反写原始JSON。其它类型字符串按已存原文返回，不凭名称推断样本是独立前瞻验证。

### 1.3 records 的28个既有字段

以下是随包数据实际字段清单，不是裁剪白名单。完整查询应保留后来增加的记录及嵌套扩展字段。

| 字段 | 类型与单位 | 含义 |
|---|---|---|
| signal_date | 日期字符串 | 该条币种结果的参考日，与外层主键一致 |
| horizon_days | 整数 | 当前22交易日 |
| forecast_date | 日期字符串 | 上游估算的预测到期日，不是已验证标记 |
| Symbol | 字符串 | 六个货币对之一，大小写及报价方向按已存值 |
| Close | 正数，汇价 | 信号日当前即期 |
| prob_up | 数值，0—1 | 该货币对汇价上涨概率，不是历史命中率 |
| signal | 字符串 | 已存强偏多/偏多/中性/偏空/强偏空信号 |
| return5 | 数值或null，小数比率 | 上游已算的近5期变动，不能再次用最新价格重算 |
| rv20 | 非负数或null，小数比率 | 上游已算的20期年化波动 |
| test_auc | 数值或null，0—1 | 已存回溯滚动AUC |
| test_accuracy | 数值或null，0—1 | 已存测试期命中率 |
| auc_ci_low | 数值或null，0—1 | AUC区间下界 |
| auc_ci_high | 数值或null，0—1 | AUC区间上界 |
| recent45_majority_accuracy | 数值或null，0—1 | 近期多数方向基准命中率 |
| recent45_hits | 非负整数或null | 近期命中数 |
| recent45_n | 非负整数或null | 近期实际样本数，不能强制称为45 |
| test_n | 非负整数或null | 已存测试样本数 |
| test_origin_first | 日期或null | 回溯测试信号起始日 |
| test_origin_last | 日期或null | 回溯测试信号结束日 |
| test_maturity_last | 日期或null | 已计入评价的最晚样本到期日，不晚于当前信号日 |
| factor_explanation | 文本或null | 已存当期因子解释原文 |
| maturity_copy | 文本或null | 已存到期验证说明，可能属于此前发出的信号 |
| evaluation_note | 文本或null | 回溯限制、重叠目标、估算日历等口径说明 |
| source_generation | 文本或null | 上游模型/结果来源版本标记 |
| source_name | 文本或null | 数据及模型结果来源说明 |
| source_file | 文本或null | 上游来源文件标记，不代表MCP可访问该文件 |
| price_history | 数组或null | 该快照已保存的历史行情点 |
| factor_impacts | 数组或null | 该快照已保存的数值归因，不保证每个历史日都有 |

price_history元素至少含 `date`、`close`，日期递增且不晚于信号日。factor_impacts元素至少含 `name`、`impact_pp`。空数组、缺失键或null应如实表达“没有已存数据”，不伪造一条零行情或零归因。

---

## 二、最新研判与历史查询

以下SQL的horizon为预测期限，当前正式工具传22；工具接到5/10时应在执行SQL之前直接返回空，保持现服务行为。

### 2.1 全库结果范围与数量

**业务**：回答“该期限结果有多少期、从哪天到哪天”，核对数据存量。

**来源**：现有count_snapshots与最新日期读取的按期限汇总建议。

**参数**：horizon，默认22。

```sql
SELECT COUNT(*) AS snapshot_count,
       MIN(signal_date) AS first_date,
       MAX(signal_date) AS latest_date
FROM fmqt_g7_fx_history
WHERE horizon_days = :horizon;
```

**返回**：snapshot_count、first_date、latest_date。空表返回一行，数量0，日期NULL；数量不受页面最近月窗口限制。现count_snapshots()本身统计所有期限，等价原SQL为：

```sql
SELECT COUNT(*) AS total_count
FROM fmqt_g7_fx_history;
```

### 2.2 最新日期与最近自然月下界

**业务**：确定所有页面兼容查询的最新锚点和floor。

**来源**：现有G7FXRepository._latest()及_month_floor()的MySQL等价模板。

**参数**：horizon，默认22。

```sql
SELECT MAX(signal_date) AS latest_date,
       DATE_SUB(MAX(signal_date), INTERVAL 1 MONTH) AS floor_date
FROM fmqt_g7_fx_history
WHERE horizon_days = :horizon;
```

**返回**：latest_date、floor_date。空表均为NULL，此时直接返回无结果，不继续做行情查询。后续SQL中的floor绑定本查询floor_date；例如最新2026-10-12对应floor=2026-09-12。

现仓储先用SELECT MAX取日期，再在Python按日历计算floor，以使SQLite与MySQL月底口径一致，不使用SQLite原生“-1 month”替代。

### 2.3 页面可查询日期清单

**业务**：最近一个自然月的有效日期列表，供日期选择器或问答选择。

**来源**：现有G7FXRepository.list_dates()。

**参数**：horizon和2.2得到的floor。

```sql
SELECT signal_date
FROM fmqt_g7_fx_history
WHERE horizon_days = :horizon
  AND signal_date >= :floor
ORDER BY signal_date DESC;
```

**返回**：日期降序列表。只查询日期，不加载JSON。latest为列表首项，空列表时为null。现HTTP返回dates、latest、storage_mode，没有全库total_count字段。

### 2.4 上一个可查询结果日

**业务**：与页面“前一研判日期”保持一致，查找指定日之前最近的可展示结果。

**来源**：现页面按dates列表移动的SQL等价建议。

**参数**：signal_date必填；horizon默认22；floor来自2.2。

```sql
SELECT MAX(signal_date) AS previous_date
FROM fmqt_g7_fx_history
WHERE horizon_days = :horizon
  AND signal_date < :signal_date
  AND signal_date >= :floor;
```

**返回**：previous_date，没有更早的可展示记录则为NULL；不是日期减一天，也不是保证的市场上一交易日。

### 2.5 最新完整快照

**业务**：回答“G7模型最新一期研判是什么”，获取完整六币种原始文档。

**来源**：现有get_document(day=None, horizon=22)的等价最新查询。

**参数**：horizon，默认22。

```sql
SELECT signal_date, horizon_days, result_json, created_at, updated_at
FROM fmqt_g7_fx_history
WHERE horizon_days = :horizon
ORDER BY signal_date DESC
LIMIT 1;
```

**返回**：五个数据库字段，至多一行。result_json解码为对象，所有原字段与扩展保留。无记录时返回未找到，不构造样例预测。

### 2.6 指定日完整快照：页面兼容口径

**业务**：精确查询一个历史日期，保持现网页最近月边界。

**来源**：现有get_document(day, horizon, enforce_window=True)。

**参数**：signal_date、horizon、floor。

```sql
SELECT signal_date, horizon_days, result_json, created_at, updated_at
FROM fmqt_g7_fx_history
WHERE signal_date = :signal_date
  AND horizon_days = :horizon
  AND signal_date >= :floor;
```

**返回**：同2.5，最多一条。指定日期不存在或早于floor均无记录，不回退到前日或最新日。HTTP结果接口用date参数名，工具层映射到SQL的signal_date。

### 2.7 最近月范围内最近N份快照

**业务**：按日期范围查询近期各期研判，比较币种概率、信号和指标变化。

**来源**：MCP扩展建议；当前网页仅查询单份快照，没有历史序列HTTP接口。

**参数**：horizon、floor；start_date/end_date可空，包含两端；limit正整数，建议默认20、最多100。两端非空时start_date不得晚于end_date。

```sql
SELECT COUNT(*) AS matched_count
FROM fmqt_g7_fx_history
WHERE horizon_days = :horizon
  AND signal_date >= :floor
  AND (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date);

SELECT signal_date, horizon_days, result_json, created_at, updated_at
FROM fmqt_g7_fx_history
WHERE horizon_days = :horizon
  AND signal_date >= :floor
  AND (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date)
ORDER BY signal_date DESC
LIMIT :limit;
```

**返回**：matched_count为筛选范围总数，明细为最近N份快照，SQL降序。工具绘图或时间序列展示时反转升序，returned_count为实际明细条数。

快照列表与某份快照内部price_history是两种历史：前者比较模型各期研判，后者是该期已有行情观测。历史工具默认只输出日期、币种、概率、信号和必要指标，不重复返回各期长解释及行情数组；单份完整快照工具保留全部JSON。

### 2.8 全部存档的指定日查询：可选扩展

**业务**：明确需要查询早于页面最近月的仍存库结果。

**来源**：get_document(enforce_window=False)的只读SQL；现公开results接口不开放此模式。

**参数**：signal_date、horizon。

```sql
SELECT signal_date, horizon_days, result_json, created_at, updated_at
FROM fmqt_g7_fx_history
WHERE signal_date = :signal_date
  AND horizon_days = :horizon;
```

**返回**：该存档完整记录；不存在则为空。工具若启用此扩展应独立命名或明确archive_scope，不能悄悄改变默认页面兼容口径。首批MCP无需开放全存档工具。

---

## 三、币种明细、指标、走势与解释

### 3.1 最新或指定日的单币种/六币种原始明细

**业务**：按Symbol查找结果，而不是把数组下标固定为某个币种。

**来源**：MCP投影建议。当前Service读取完整快照后按Symbol分组；也可直接复用2.5/2.6，在工具层按Symbol提取，二选一，不重复查询。

**参数**：horizon、floor；signal_date可空，None表示最新；symbol可空，None表示六币种，非空仅接受六币种之一。

```sql
SELECT
  h.signal_date,
  h.horizon_days,
  p.pair_symbol AS Symbol,
  JSON_EXTRACT(
    h.result_json,
    CONCAT('$.records[', p.pair_ordinality - 1, ']')
  ) AS pair_result,
  JSON_EXTRACT(h.result_json, '$.snapshot_kind') AS snapshot_kind,
  h.created_at,
  h.updated_at
FROM (
  SELECT signal_date, horizon_days, result_json, created_at, updated_at
  FROM fmqt_g7_fx_history
  WHERE horizon_days = :horizon
    AND signal_date >= :floor
    AND (:signal_date IS NULL OR signal_date = :signal_date)
  ORDER BY signal_date DESC
  LIMIT 1
) AS h
CROSS JOIN JSON_TABLE(
  h.result_json,
  '$.records[*]' COLUMNS (
    pair_ordinality FOR ORDINALITY,
    pair_symbol VARCHAR(10) PATH '$.Symbol'
  )
) AS p
WHERE (:symbol IS NULL OR p.pair_symbol = :symbol)
ORDER BY FIELD(
  p.pair_symbol,
  'EURUSD', 'GBPUSD', 'AUDUSD', 'USDJPY', 'USDCAD', 'USDCHF'
);
```

**返回**：signal_date、horizon_days、Symbol、完整pair_result、原snapshot_kind及两个时间字段。pair_result是完整币种对象，保留数字精度、null及嵌套扩展字段。JSON_EXTRACT返回JSON文本时应按JSON解码，不能直接把带引号文本或“null”作为展示值。

FOR ORDINALITY从1计数，JSON数组下标从0计数，所以表达式减1。必须先选定一份快照再展开，不能先展开六币种后LIMIT 1而误删五个币种。snapshot_kind字段缺失时展示层默认archived_snapshot，但原JSON缺省状态仍保留。

### 3.2 汇价、概率与回测指标

**业务**：回答“EURUSD当前上涨概率和历史AUC是多少”。

**来源及SQL**：复用3.1的pair_result，或2.5/2.6后按Symbol过滤。不需要执行第二条指标SQL。

**参数**：date可选、horizon默认22、symbol必填。

**返回字段建议**：signal_date、Symbol、Close、prob_up、signal、return5、rv20、test_auc、test_accuracy、auc_ci_low、auc_ci_high、recent45_hits、recent45_n、test_n、test_origin_first、test_origin_last、test_maturity_last、recent45_majority_accuracy、evaluation_note、source_generation和snapshot_kind。

概率展示可加prob_up_pct=round(prob_up*100, 2)，保留原prob_up。AUC不乘100。recent45_n为0/null时近期命中率返回null，不除零；n并非总是45。指标为空时明确未保存，不能改为0、重新回测或根据概率推算准确率。

当前没有每条最新预测的实际未来标签列或“是否正确”列；test_accuracy是上游评价区间指标，不能当作某条当前预测已验证的结果。

### 3.3 选中快照内的历史行情

**业务**：回答“该信号日所能看到的EURUSD历史收盘走势”。

**来源及SQL**：复用3.1提取pair_result.price_history，不额外读取行情文件。

**参数**：date可选、horizon默认22、symbol必填；observations建议30或60，默认30，与现页面一致。

**返回**：信号日期、货币对、升序行情点date/close、实际观测数量。先确认price_history为数组，再取最后N个已存观测；null、缺失或空数组返回空列表，不伪造行情点。

30/60是行情观测数，不是自然日跨度，也不同于2.7的N份模型快照。所有行情点必须不晚于所选信号日；不从最新快照搬运之后的行情来填历史。

### 3.4 已存因子影响与解释

**业务**：回答“本期研判有哪些驱动，已有解释是什么”。

**来源及SQL**：复用3.1的pair_result，不重新生成解释。

**参数**：date可选、horizon默认22、symbol必填。

**返回字段建议**：factor_impacts、factor_explanation、maturity_copy、evaluation_note、source_generation、source_name、source_file、snapshot_kind和信号日期/货币对。

factor_impacts元素包括name与impact_pp。保留正负号，可按绝对值排序展示，但不能求和成总收益或概率变化；没有当日数值归因时明确没有已存归因，不套用最新日数组补旧日。

解释为空时明确没有已存解释。source_file是来源标记，不触发读取。updated_at是整份快照入库时间，不是独立解释生成时间。20份historical_reconstruction必须保留历史恢复属性和evaluation_note，不改写成正式前瞻结论。

---

## 四、MCP工具映射建议

### 4.1 接入原则

| 原则 | 说明 |
|---|---|
| 仅查询已存数据 | 不暴露任意SQL、写入、训练、取数或大模型生成工具 |
| 复用公司连接 | 使用现成SQLAlchemy Engine，不新增数据库.env或ORM |
| 日期和期限明确 | 返回实际signal_date与horizon_days，默认22；5/10直接空且不执行SQL |
| 默认页面兼容 | 使用最近自然月floor，指定日不回退；全存档为明确的可选扩展 |
| 按Symbol识别 | 验证六币种枚举，不假设JSON数组顺序 |
| 固定SQL绑定参数 | 表名来自代码常量，日期、币种、数量绑定，不接受用户SQL或JSON路径 |
| 保留原始单位与空值 | 汇价不是收益率，概率0—1，因子impact_pp已是百分点，缺值不变0 |
| 控制返回量 | 历史默认20份、最多100，默认输出摘要，不重复展开各期长文本和行情 |
| 说明工具状态 | 以下为建议定义，当前代码没有注册或部署MCP Server |

### 4.2 工具清单

| 工具名 | 用途 | 入参 | SQL/取数方式 |
|---|---|---|---|
| get_g7_fx_data_range | 全库该期限的结果范围和快照数量 | horizon? | 2.1 |
| list_g7_fx_dates | 最近自然月可查询日期 | horizon? | 2.2＋2.3 |
| get_g7_fx_snapshot | 最新或指定日完整六币种快照 | date?、horizon? | 2.5或2.2＋2.6 |
| get_g7_fx_pair_result | 某币种最新或指定日完整研判 | symbol、date?、horizon? | 3.1，或2.5/2.6后提取 |
| list_g7_fx_history | 最近月范围内最近N份研判摘要 | start_date?、end_date?、horizon?、limit? | 2.2＋2.7 |
| get_g7_fx_price_history | 选中快照的已存行情点 | symbol、date?、horizon?、observations? | 3.1后提取price_history |
| get_g7_fx_factor_explanation | 某币种已存解释与数值归因 | symbol、date?、horizon? | 3.1后提取解释字段 |

首批可只接入“完整快照、单币种研判、已存解释”三个工具，其余按需求加入。3.2至3.4是对同一币种对象的不同投影，不需要每次重复读取快照。

### 4.3 工具定义示例

日期正则仅限制格式，工具实现还需解析有效日期；未提供的日期对应SQL None，不能把空字符串当合法日期。

```json
{
  "name": "get_g7_fx_snapshot",
  "description": "查询G7外汇研判模型最新或指定日期的完整六币种快照。默认22交易日；5/10交易日暂无正式结果，直接返回空且不查询数据库。指定日期受库内最新日期向前一个自然月窗口约束，不存在不回退。完整JSON保留扩展字段及null，不训练、不取新行情。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "信号参考日；省略取最新"
      },
      "horizon": {"type": "integer", "enum": [5, 10, 22], "default": 22}
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_g7_fx_pair_result",
  "description": "按Symbol查询G7外汇研判模型最新或指定日的单货币对完整结果。prob_up是该货币对报价上涨概率0至1，不是统一美元上涨概率或命中率；signal读取原值。指标与历史恢复标记如实返回，null不改0。日期范围与现页面一致。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol": {"type": "string", "enum": ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD", "USDCHF"]},
      "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
      "horizon": {"type": "integer", "enum": [5, 10, 22], "default": 22}
    },
    "required": ["symbol"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "list_g7_fx_history",
  "description": "查询G7外汇研判模型最近自然月内日期闭区间的最近N份模型研判，升序返回摘要。窗口锚定库内最新信号日，不按今天日期。N为模型快照数，不是某份快照内的行情观测数。返回matched_count、returned_count和items，不默认输出各期长解释。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
      "end_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
      "horizon": {"type": "integer", "enum": [5, 10, 22], "default": 22},
      "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_g7_fx_factor_explanation",
  "description": "读取指定货币对最新或指定日已有的因子解释、数值归因、到期说明、来源和评价限制。impact_pp已经是概率百分点，不再乘100且不可相加成收益贡献；历史没有归因时不补最新数据。只读数据库，不调用大模型、不重新生成解释。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol": {"type": "string", "enum": ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD", "USDCHF"]},
      "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
      "horizon": {"type": "integer", "enum": [5, 10, 22], "default": 22}
    },
    "required": ["symbol"],
    "additionalProperties": false
  }
}
```

行情工具复用单币种Schema，并加observations枚举[30,60]、默认30。日期清单和范围工具只需horizon参数。

### 4.4 建议返回结构

单日无记录返回found=false、data=null，列表无结果返回空items。5/10期限可增加reason="NO_FORMAL_RESULT_FOR_HORIZON"，不得自动查22。

下例只说明单币种返回格式，数据为随包2026-10-12的部分原值，不是实时查询；完整单币种工具必须返回全部字段。

```json
{
  "found": true,
  "model_name": "G7外汇研判模型",
  "signal_date": "2026-10-12",
  "horizon_days": 22,
  "snapshot_kind": "archived_snapshot",
  "units": {
    "Close": "currency_pair_quote",
    "prob_up": "ratio_0_1",
    "test_auc": "ratio_0_1",
    "impact_pp": "probability_percentage_points"
  },
  "data": {
    "Symbol": "EURUSD",
    "Close": 1.1155,
    "prob_up": 0.4635343558347869,
    "signal": "中性",
    "test_auc": 0.6004599447222398,
    "test_accuracy": 0.5678733031674208,
    "recent45_hits": 29,
    "recent45_n": 45,
    "forecast_date": "2026-11-11"
  }
}
```

完整快照工具返回五个数据库字段，其中result_json必须保留原对象及扩展，不用上述摘要替代。可以额外加展示百分数，不改原概率。MCP需要文本内容块时序列化为JSON，支持结构化返回时直接返回对象。

### 4.5 与现有FastAPI的对应关系

| 当前接口 | 当前用途 | MCP处理 |
|---|---|---|
| GET /research/g7fx.health | 连接和表可访问性 | 可用于部署检查，不是模型结果业务工具 |
| GET /research/g7fx.dates | 最近月日期，5/10空 | 可复用Service.list_dates() |
| GET /research/g7fx.results | 最新或指定日六币种展示结果 | 可复用Service.get_results()并按Symbol筛选 |
| POST /research/g7fx.importHistory | 固定目录落库 | 不接入只读MCP |
| POST /research/g7fx.saveResult | 保存原始快照 | 不接入只读MCP |

HTTP results返回pairs等展示包装，不返回原始document顶层所有扩展，也会为可选空值补展示默认值；需要原始完整快照时，应复用Repository.get_document()或执行2.5/2.6，而非把HTTP展示包装声称为原始完整JSON。

公司选择HTTP封装或直接复用数据库连接即可。MCP Server协议地址及注册方式由公司提供，网页/Swagger地址本身不是MCP协议地址，本文不假定已经部署MCP工具。

---

## 五、接入执行与核验

### 5.1 公司Engine只读执行示例

以下直接执行2.6，不创建新Engine、不配置数据库.env、不使用ORM。floor由2.2取得；示例明确使用2026-10-12对应的2026-09-12。

```python
import json
from datetime import date, timezone

from sqlalchemy import text
from infrastructure.repository import engine  # 公司已有初始化导出的Engine

sql = """
SELECT signal_date, horizon_days, result_json, created_at, updated_at
FROM fmqt_g7_fx_history
WHERE signal_date = :signal_date
  AND horizon_days = :horizon
  AND signal_date >= :floor
"""
params = {
    "signal_date": date.fromisoformat("2026-10-12"),
    "horizon": 22,
    "floor": date.fromisoformat("2026-09-12"),
}
with engine.connect() as conn:
    row = conn.execute(text(sql), params).mappings().first()

if row is None:
    payload = {"found": False, "model_name": "G7外汇研判模型", "data": None}
else:
    record = dict(row)
    raw = record["result_json"]
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8")
    record["result_json"] = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(record["result_json"], dict):
        raise ValueError("result_json必须为完整快照对象")
    record["signal_date"] = record["signal_date"].isoformat()
    for field in ("created_at", "updated_at"):
        value = record[field]
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        record[field] = value.astimezone(timezone.utc).isoformat()
    payload = {"found": True, "model_name": "G7外汇研判模型", "data": record}
```

示例针对公司MySQL驱动返回DATE/DATETIME对象的情况；复用现Repository可同时处理SQLite文本时间。只读查询不提交写事务，MCP不会调用保存函数。

### 5.2 查询验收清单

| 场景 | 操作 | 示例包预期 |
|---|---|---|
| 全库范围 | 2.1，horizon=22 | 21快照，首日2026-09-14、末日2026-10-12 |
| 月度下界 | 2.2 | latest_date=2026-10-12，floor_date=2026-09-12 |
| 最新快照 | 2.5 | 六币种完整document，缺snapshot_kind展示默认archived_snapshot |
| 历史日期 | 2.6，2026-10-06 | historical_reconstruction，保留该日原值与解释 |
| 缺失日期 | 2.6，2026-10-11 | 未找到，不回退到10月9日 |
| 旧存档 | 超出floor | 默认口径未找到；2.8仅在库内确有该日时可查到 |
| 币种过滤 | 3.1，symbol=USDJPY | 仅USDJPY原对象，不能拿EURUSD的数组位置替代 |
| 六币种顺序 | 3.1，symbol=None | 固定六币种顺序，即使源records顺序变化也正确 |
| 近期比较 | 2.7，end_date=2026-10-06、limit=3 | 最近三份，反转升序后截止10月6日，不含之后快照 |
| 行情边界 | 3.3，历史日、30/60观测 | 仅该快照已有行情，不晚于信号日期 |
| 因子空值 | 历史factor_impacts为空 | 明确无当日数值归因，不补最新日归因 |
| 5/10期限 | 工具参数为5/10 | 空结果且不执行SQL |
| 月末和闰年 | 独立测试日期3月31日 | 下界2月28/29，不按30天 |
| 只读性 | 查询前后核对时间/数量 | 记录数量、updated_at不变 |
| 数据库故障 | 独立测试环境模拟 | 报服务错误，不当作found=false或空数组 |
| 空库 | 独立空测试库 | 统计0，日期NULL，单日未找到，列表为空 |

空库、月末、排序及错误场景用独立测试库验证，不修改正式模型历史制造测试数据。本文依据当前源码和随包JSON整理，未连接公司MySQL、未注册MCP工具；公司需验证真实SQL函数、Engine、参数绑定及协议调用。

### 5.3 SQL与代码位置对应

以下路径相对于适配版根目录：

| 位置/方法 | 本文对应 |
|---|---|
| infrastructure/repository/g7_fx_repository.py::count_snapshots | 2.1全表total_count |
| infrastructure/repository/g7_fx_repository.py::_latest / _month_floor | 2.2，实际日历下界在Python计算 |
| infrastructure/repository/g7_fx_repository.py::list_dates | 2.3 |
| infrastructure/repository/g7_fx_repository.py::get_document | 2.5、2.6、2.8 |
| domain/rates/g7_fx_service.py::get_results | 解析records、固定币种顺序、仅展示默认值及故障区分 |
| interface/routes/g7_fx.py | 4.5现有5个HTTP接口 |
| 2.4、2.7、3.1 | MCP查询建议，当前没有独立同名HTTP接口或已注册工具 |

详细HTTP测试见 [接口测试说明](API_TESTING.md)，公司Engine、表名、路径与路由修改清单见 [迁移说明](MIGRATION_GUIDE.md)。
