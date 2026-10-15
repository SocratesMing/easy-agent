# 美国CPI预测_SQL清单_MCP信息

## 一、落库范围与部署动作

| 表名 | 用途 | 记录口径 |
|---|---|---|
| fmqt_forecast_us_cpi_prediction_result | 报告版本和三类预测数组 | 每个三文件组合版本一行 |
| fmqt_forecast_us_cpi_market_expectation | 机构预测和来源提供的市场中位数 | 关联report_id，按实际机构数量保存 |
| fmqt_forecast_us_cpi_strategy_advice | 策略建议完整原文 | 关联report_id，独立保存策略所属月份 |
| fmqt_forecast_us_cpi_official_history | 官方SA环比、NSA指数和来源 | 独立于报告，月份+指标名称为主键 |

三个结果文件分别是`model_results.json`、`market_expectations.json`、`strategy_advice.txt`。官方历史来自`CPI_nsa_monthly.xlsx`与`CPI_sa_mom.xlsx`；英文文件名下的表头仍按中文处理。只读取已生成文件，不追溯或调用后端生成逻辑，不修改前端。

- 新建库表：执行`database/美国CPI_DBeaver建表.sql`，或使用相同内容的`forecast_us_cpi_schema.sql`。
- 已建原生JSON列：执行一次`database/美国CPI_已有JSON列改LONGTEXT.sql`，转换现有三列，不删除报告或重新导入。`CREATE TABLE IF NOT EXISTS`不会修改已存在的列。
- 更新`forecast_us_db.py`：保持与中国脚本相同的公共配置导入和PyMySQL连接方式。新的列定义与DDL一致，并在写入前校验三个JSON数组，写入后核对UTF-8内容。
- 表数仍为4。列名、数组内部字段、report_id、来源摘要、版本去重口径、baseline范围不变；单纯类型迁移不产生新的报告版本。
- LONGTEXT不具备原生JSON类型的自动合法性校验。通过本脚本写入时由Python严格校验；绕过脚本直接写SQL的其他程序必须自行校验。

## 二、预测文本数组和字段中文含义

| 数据列 | 保存内容 | 数量规则 |
|---|---|---|
| groups_json | 当前预测月全部原始组名；另保留总体、核心十二个月窗口内的main/far趋势 | 以源文件实际记录为准，不固定组数 |
| leaves_json | 当前预测月实际提供的分项、方法、权重及同比和环比 | 不强制26条，缺项不补造 |
| daily_json | 目标月实际提供日期的总体、核心、食品、能源逐日预测 | 不强制满月、满四组，仅保留已有记录 |

组级按当前源文件实际提供的原始组名保存和查询，例如总体、核心、食品、能源、核心商品、住房、核心服务；缺少的组别不补造。顶部同比、SA环比均不保存baseline；baseline仅出现在逐日预测。结构化模型可信度不保存。策略正文作为原文可能含历史评价文字，不另提取成可信度。

趋势窗口按当前落库脚本，以预测月和源文件远期月份的最大值为终点，向前覆盖十二个自然月；不代表每个月都存在记录。查询返回实际保留的数据，不承诺完整历史分项或完整预测轨迹。

### groups_json[]

| 字段 | 中文含义 |
|---|---|
| record_type | 来源类别，main组级预测或far远期预测 |
| forecast_month | 该记录所属预测月份，以当月一日存储 |
| group_name | CPI组别名称 |
| weight | 占总体CPI的权重，百分数口径，100代表百分之百 |
| yoy | CPI同比预测涨幅，百分数口径 |
| nsa_mom | 未季节调整的CPI环比预测涨幅，百分数口径 |
| sa_mom | 季节调整后的CPI环比预测涨幅，百分数口径 |

### leaves_json[]

| 字段 | 中文含义 |
|---|---|
| forecast_month | 该记录所属预测月份，以当月一日存储 |
| item_name | 源文件中的分项原始名称 |
| group_name | CPI组别名称 |
| weight | 占总体CPI的权重，百分数口径，100代表百分之百 |
| method | 叶子分项预测方法名称 |
| yoy | CPI同比预测涨幅，百分数口径 |
| nsa_mom | 未季节调整的CPI环比预测涨幅，百分数口径 |
| sa_mom | 季节调整后的CPI环比预测涨幅，百分数口径 |

### daily_json[]

| 字段 | 中文含义 |
|---|---|
| target_month | 逐日预测的目标CPI月份 |
| observation_date | 逐日展示日期，不等于独立预测发布时间 |
| group_name | CPI组别名称 |
| yoy | CPI同比预测涨幅，百分数口径 |
| baseline | 仅用于逐日预测的月初同比惯性基线，百分数口径 |
| correction | 逐日同比相对月初基线的累计修正量，单位百分点 |
| event_sources | 当日更新来源原文；同一天各组重复保存，不按组累计 |
| is_future_display | 展示日期是否晚于模型生成日，0否1是 |

## 三、通用连接、参数和返回约定

1. 5.1.35指DBeaver的MySQL JDBC驱动版本，不是MySQL服务器版本。本文主查询使用基础SELECT和服务层JSON解析，不依赖`JSON_TABLE`、CTE或MySQL 8专有语法。
2. 三个LONGTEXT列按普通UTF-8文本读取，再在MCP服务层解析为数组、筛选和排序。客户端返回`data`为数组对象，不把JSON文本重复编码成一个大字符串。
3. 示例SQL中的`:name`是逻辑参数。使用PyMySQL执行时转换为`%s`并单独传入参数元组；按出现顺序绑定，重复参数也重复传入，不拼接用户值。以下带参数SQL供接入审阅，不能原样直接交给PyMySQL。
4. 月份输入`YYYY-MM`，工具层校验后转换为月首DATE；数组内月份为`YYYY-MM-DD`。日期必须通过真实日历校验，不能只检查正则表达式。数量、名称长度和范围必须受工具参数限制。
5. 除官方历史外，先定位`status='ready'`的报告。显式report_id只查询该ID，不存在则返回`report_not_found`；未给ID时按generated_at、imported_at、report_id倒序定位最新版本。一次工具调用固定同一ID，不能在多个子查询之间重新选择最新版本。
6. 查询结果统一为`{data, meta}`。meta包含report_id、模型forecast_month、market_month、strategy_month、generated_at、applied_filters、units、warnings及truncated；官方历史的报告元信息为null。各业务记录中的forecast_month保留其自身含义，不能用报告模型月份覆盖市场或策略月份。
7. 数据为空返回`data: []`并在warnings说明缺项或无同月数据。数值NULL保留null，真实0保留0。查询层可依据实际数组识别缺项，但不能声称读取了未持久化的原导入warnings。
8. yoy、nsa_mom、sa_mom和weight是百分号前的数值；例如0.4表示0.4%。市场差值gap与daily.correction单位为百分点。NSA指数保持原基期。内部建议使用Decimal，MCP输出转换为JSON数值，不转成带百分号字符串。
9. MCP使用只读数据库账号。只提供本文7个业务工具，不开放任意SQL、写表或生成新策略点评的接口。连接凭据由现有项目配置提供，不写入本文或工具定义。

共享报告定位SQL（不另算MCP工具）：

```sql
SELECT report_id, forecast_month, official_latest_month,
       market_month, strategy_month, generated_at, imported_at
FROM fmqt_forecast_us_cpi_prediction_result
WHERE status = 'ready'
  AND (:report_id IS NULL OR report_id = :report_id)
ORDER BY generated_at DESC, imported_at DESC, report_id DESC
LIMIT 1;
```
报告定位中的report_id默认NULL。场景2—6的业务月份参数用于过滤已选报告里的记录，不自动寻找其他报告；需要查看旧版时明确传report_id。场景1专门用于检索版本。

LONGTEXT读取示例（服务层逻辑）：

```python
from decimal import Decimal
import json

rows = json.loads(db_row["leaves_text"], parse_float=Decimal)
# 随后按工具的group、item筛选；保留原始中文、数值和null。
```

若尚未执行类型迁移，仍使用已验证的旧表兼容表达式读取：

```sql
SELECT report_id,
       CONVERT(UNHEX(HEX(leaves_json)) USING utf8mb4)
           COLLATE utf8mb4_general_ci AS leaves_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = 1;
```

此表达式仅用于读取旧原生JSON列，不是写回转码。迁移后的LONGTEXT列可直接SELECT。

## 四、7个功能场景

| 编号 | 场景 | MCP工具 |
|---|---|---|
| 1 | 报告版本与各来源月份 | get_cpi_reports |
| 2 | 当前组级预测和总体、核心趋势 | get_cpi_group_forecasts |
| 3 | 当前预测月分项明细 | get_cpi_item_forecasts |
| 4 | 逐日预测、专用基线和累计修正 | get_cpi_daily_evolution |
| 5 | 机构预测及同月模型比较 | get_cpi_market_expectations |
| 6 | 策略建议原文 | get_cpi_strategy_advice |
| 7 | 官方历史SA环比及NSA指数 | get_cpi_official_history |

### 1. 报告版本与各来源月份

**业务问题**：最新预测是哪一版？模型、市场预期、策略分别属于哪个月？

**参数**：report_id可选；forecast_month可选；limit默认1、最大50。forecast_month在本工具中过滤报告的模型预测月份，limit_rows等于limit。

```sql
SELECT report_id, forecast_month, official_latest_month,
       market_month, strategy_month, generated_at, imported_at,
       model_sha256, market_sha256, strategy_sha256
FROM fmqt_forecast_us_cpi_prediction_result
WHERE status = 'ready'
  AND (:report_id IS NULL OR report_id = :report_id)
  AND (:forecast_month IS NULL OR forecast_month = :forecast_month)
ORDER BY generated_at DESC, imported_at DESC, report_id DESC
LIMIT :limit_rows;
```
**返回字段**：report_id（报告主键）、forecast_month（模型预测月）、official_latest_month（模型当时最新官方月）、market_month（市场预期月）、strategy_month（策略月）、generated_at（模型生成时间）、imported_at（入库时间），以及三个来源文件摘要。

**口径**：同月可有多个版本；按完整三文件组合及策略月份去重。月份不同如实展示，不强行统一。

### 2. 当前组级预测和总体、核心趋势

**业务问题**：总体、核心、食品或其他原始组别预测多少？已保存的趋势如何变化？

**参数**：report_id可选；forecast_month可选；scope取main/far/all，默认main；group可选，按所选报告实际存在的中文组名精确匹配，不使用固定组名列表过滤。

```sql
SELECT report_id, forecast_month, groups_json AS groups_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```
**服务层处理**：解析groups_text；scope不是all时筛选record_type；main且月份未给时使用报告forecast_month，far/all且月份未给时保留全部已存月份；传月份时精确筛选。group精确筛选。按forecast_month、group_name、record_type排序，保持来源类别，不把main和far混成同一条。

**返回字段**：report_id及groups_json对象的全部七个字段，中文含义见第二节。这里不返回baseline或模型可信度。不存在的原始组名返回空数组并说明未提供，不把“核心服务”改名。

### 3. 当前预测月分项明细

**业务问题**：家居、汽油、运输服务等分项的预测、权重和方法是什么？

**参数**：report_id可选；group可选，按实际分项所属组名校验；item可选，精确匹配分项原名。

```sql
SELECT report_id, forecast_month, leaves_json AS leaves_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```
**服务层处理**：解析leaves_text，筛选forecast_month等于报告模型forecast_month，再按group、item过滤；按weight降序、item_name升序，NULL权重排最后。返回实际分项数量，不固定26条。

**返回字段**：report_id及leaves_json对象的全部八个字段，中文含义见第二节。历史版本通过report_id查看，不承诺一份报告中包含历史分项序列。

### 4. 逐日预测、专用基线和累计修正

**业务问题**：目标月逐日同比如何变化，相对月初基线累计修正多少？

**参数**：report_id可选；group可选（总体/核心/食品/能源）；start_date、end_date可选，分别默认目标月首日、末日；include_future默认false。

```sql
SELECT report_id, forecast_month, daily_json AS daily_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```
**服务层处理**：解析daily_text，保留target_month等于报告forecast_month的记录。开始、结束日期必须属于该目标月且开始不晚于结束；按闭区间筛选observation_date、按group过滤。include_future=false时仅保留is_future_display=0。按observation_date、group_name排序，不补造缺失日期或组别。

**返回字段**：report_id及daily_json对象的全部八个字段，中文含义见第二节。baseline仅用于本场景的逐日同比月初基线，correction是相对它的累计修正，二者均非顶部SA环比基线。event_sources是来源原文，同一天在不同组重复保存，不能按组重复累计事件次数。展示日期不是独立运行或发布时间。

### 5. 机构预测及同月模型比较

**业务问题**：机构和市场中位数预测多少？同一月份模型比市场高或低多少？

**参数**：report_id可选；forecast_month可选，默认所选报告market_month；median_only默认false；institution可选。effective_month为最终查询月份，institution_name映射自institution，median_only绑定为0/1。

机构SQL：

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

同一报告内读取组级预测：

```sql
SELECT report_id, groups_json AS groups_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```
**服务层处理**：解析groups_text，仅从record_type=main、forecast_month=effective_month中分别精确取“总体”和“核心”；不使用其他月份、其他报告或far记录替代。机构记录中的四个指标分别与同月模型匹配，某个模型值缺失时对应模型值和gap均返回null，并在warnings说明。缺少市场记录时返回空数组。

**返回字段与中文含义**：

| 字段 | 中文含义 |
|---|---|
| report_id / forecast_month | 报告ID / 本条市场预测所属月 |
| institution_name / is_median | 机构原名 / 是否来源提供的市场中位数 |
| headline_yoy / headline_mom | 市场总体同比 / 总体SA环比 |
| core_yoy / core_mom | 市场核心同比 / 核心SA环比 |
| model_headline_yoy / model_headline_mom | 同月模型总体同比 / 总体SA环比 |
| model_core_yoy / model_core_mom | 同月模型核心同比 / 核心SA环比 |
| headline_yoy_gap / headline_mom_gap | 模型减市场的总体同比差 / SA环比差，单位百分点 |
| core_yoy_gap / core_mom_gap | 模型减市场的核心同比差 / SA环比差，单位百分点 |

若同月模型来自报告内历史预测，须在warnings说明报告生成时间，不能解释为官方公布前可用的历史交易信号。市场中位数使用源文件已提供记录，不在本工具中重新计算。

### 6. 策略建议原文

**业务问题**：已有策略建议正文及对应月份是什么？

**参数**：report_id可选；forecast_month可选，默认所选报告strategy_month，转换后绑定effective_month。

```sql
SELECT a.report_id, a.forecast_month, a.content,
       r.strategy_sha256, r.generated_at AS model_generated_at, r.imported_at
FROM fmqt_forecast_us_cpi_strategy_advice a
JOIN fmqt_forecast_us_cpi_prediction_result r ON r.report_id = a.report_id
WHERE a.report_id = :resolved_report_id AND r.status = 'ready'
  AND a.forecast_month = :effective_month;
```
**返回字段**：report_id（报告ID）、forecast_month（策略所属月）、content（完整策略原文）、strategy_sha256（源文件摘要）、model_generated_at（模型生成时间）、imported_at（报告入库时间）。模型生成时间不是策略撰写时间。

**口径**：原文保持不变，不生成新策略、不提取历史评分为模型可信度。策略所属月份来自导入参数，不从模型月份推断。查询月份与已存策略不符时返回空数组。

### 7. 官方历史SA环比及NSA指数

**业务问题**：某月总体、核心或分项官方SA环比、NSA指数是多少？同比如何变化？

**参数**：indicator、start_month、end_month均可选；limit默认1000、最大5000。不接受report_id。indicator_name映射自indicator，使用工作簿去除指标后缀后的共同中文名称。

结束月份未给时，先查所选指标最新有值月份；指标留空时取官方表最新月：

```sql
SELECT MAX(observation_month) AS latest_month
FROM fmqt_forecast_us_cpi_official_history
WHERE (:indicator_name IS NULL OR indicator_name = :indicator_name);
```
开始月份未给时取最终结束月向前11个月。开始月份不能晚于结束月份；无数据时返回空数组。主查询limit_rows绑定limit+1，多取一行判断截断，最终最多返回limit行，并设置meta.truncated。

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
ORDER BY h.observation_month, h.indicator_name
LIMIT :limit_rows;
```
**返回字段**：observation_month（官方月份）、indicator_name（指标中文名）、nsa_series_id/sa_series_id（两套来源指标ID）、nsa_index（NSA指数）、sa_mom（官方SA环比）、nsa_yoy（查询时由上年同月NSA指数派生的同比）、nsa_source_file/sa_source_file（来源文件）、updated_at（导入修订时间）。

**口径**：nsa_yoy不是新增落库字段，上年同月缺失或指数为0时返回null。源值缺失不补0、不插值。updated_at不是官方公布时间。指标例如“美国:CPI”“美国:核心CPI(不含食物、能源)”，按数据库实际原名选择；官方指标名称与预测组名分别使用各自来源口径，不进行推断映射。

## 五、数据库字段中文注释清单

以下由配套建表SQL整理；实际COMMENT以同版本DDL和落库模块为准。JSON对象内部没有数据库列COMMENT，内部字段含义见第二节。

### fmqt_forecast_us_cpi_prediction_result

| 字段 | 存储类型 | 中文注释 |
|---|---|---|
| report_id | BIGINT UNSIGNED NOT NULL AUTO_INCREMENT | 本次三文件报告版本主键 |
| bundle_sha256 | CHAR(64) NOT NULL | 三文件摘要及策略所属月份的组合摘要，用于重复导入去重 |
| schema_version | INT NOT NULL | 模型结果JSON结构版本 |
| official_latest_month | DATE NOT NULL | 模型运行时最新官方CPI月份 |
| forecast_month | DATE NOT NULL | 模型当期预测月份 |
| market_month | DATE NOT NULL | 市场预期所属月份，与模型月份分别保存 |
| strategy_month | DATE NOT NULL | 策略建议所属月份，由导入参数指定 |
| generated_at | DATETIME(6) NOT NULL | 模型源文件生成时间，保留来源本地时间，不自动转换时区 |
| model_sha256 | CHAR(64) NOT NULL | 模型原文件字节的SHA256摘要 |
| market_sha256 | CHAR(64) NOT NULL | 市场原文件字节的SHA256摘要 |
| strategy_sha256 | CHAR(64) NOT NULL | 策略原文件字节的SHA256摘要 |
| groups_json | LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL | 组级预测及总体核心趋势JSON文本，包含月份、组别、权重、同比和环比，不含基线 |
| leaves_json | LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL | 当前月实际分项JSON文本，包含名称、组别、方法、权重、同比和环比，不含基线 |
| daily_json | LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL | 四组逐日预测JSON文本，baseline仅在此保存，包含累计修正、来源和未来展示标记 |
| status | VARCHAR(16) NOT NULL | 报告写入状态，事务内writing，全部写入后ready |
| imported_at | DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) | 本报告数据库导入时间，非业务生成时间 |

### fmqt_forecast_us_cpi_market_expectation

| 字段 | 存储类型 | 中文注释 |
|---|---|---|
| report_id | BIGINT UNSIGNED NOT NULL | 所属三文件报告版本主键 |
| record_key | CHAR(64) NOT NULL | 本表业务键组合的SHA256摘要，防止重复记录 |
| forecast_month | DATE NOT NULL | 市场预期所属月份，以当月一日存储 |
| institution_name | VARCHAR(255) NOT NULL | 机构名称或市场中位数 |
| is_median | TINYINT(1) NOT NULL | 是否为市场中位数，0否1是 |
| headline_yoy | DECIMAL(18,8) NULL | 市场预计总体同比，百分数口径 |
| headline_mom | DECIMAL(18,8) NULL | 市场预计总体环比，业务按SA比较，百分数口径 |
| core_yoy | DECIMAL(18,8) NULL | 市场预计核心同比，百分数口径 |
| core_mom | DECIMAL(18,8) NULL | 市场预计核心环比，业务按SA比较，百分数口径 |

### fmqt_forecast_us_cpi_strategy_advice

| 字段 | 存储类型 | 中文注释 |
|---|---|---|
| report_id | BIGINT UNSIGNED NOT NULL | 所属三文件报告版本主键 |
| record_key | CHAR(64) NOT NULL | 本表业务键组合的SHA256摘要，防止重复记录 |
| forecast_month | DATE NOT NULL | 策略所属月份，以当月一日存储 |
| content | MEDIUMTEXT NOT NULL | 策略完整原文，不自动改写源文本 |

### fmqt_forecast_us_cpi_official_history

| 字段 | 存储类型 | 中文注释 |
|---|---|---|
| observation_month | DATE NOT NULL | 官方观测所属月份，以当月一日存储 |
| indicator_name | VARCHAR(255) NOT NULL | 官方指标中文名称，去掉数值或季调环比后缀后的共同名称 |
| nsa_series_id | VARCHAR(64) NULL | 未季调指数的源指标代码 |
| sa_series_id | VARCHAR(64) NULL | 官方季调环比的源指标代码 |
| nsa_index | DECIMAL(24,8) NULL | 官方未季调CPI指数当月值，保持原指数基期，不转成百分比 |
| sa_mom | DECIMAL(18,8) NULL | 官方季节调整后月环比，百分数口径，0.4代表百分之零点四 |
| nsa_source_file | VARCHAR(255) NOT NULL | NSA指数源工作簿文件名 |
| sa_source_file | VARCHAR(255) NOT NULL | SA环比源工作簿文件名 |
| nsa_sheet | VARCHAR(128) NULL | NSA指数所在工作表名称 |
| sa_sheet | VARCHAR(128) NULL | SA环比所在工作表名称 |
| nsa_source_sha256 | CHAR(64) NOT NULL | 本次NSA指数工作簿字节的SHA256摘要 |
| sa_source_sha256 | CHAR(64) NOT NULL | 本次SA环比工作簿字节的SHA256摘要 |
| updated_at | DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) | 该官方记录最近一次导入或修订时间，非官方发布时间 |

## 六、部署输入路径

| 文件 | 非Windows默认路径 |
|---|---|
| 模型结果 | /qts/research/us_cpi/result/model_results.json |
| 机构预期 | /qts/research/us_cpi/result/market_expectations.json |
| 策略原文 | /qts/research/us_cpi/result/strategy_advice.txt |
| 官方NSA指数 | /qts/research/us_cpi/data/CPI_nsa_monthly.xlsx |
| 官方SA环比 | /qts/research/us_cpi/data/CPI_sa_mom.xlsx |

Windows从落库模块目录上两级的`domain/macro/index/oversea/us_cpi`读取data/result；可通过US_CPI_DIR或既有命令行路径参数覆盖。两份官方工作簿第一行是中文指标名，第二行是来源指标ID；读取后续行已有缓存数值，不修改或计算Excel。按中文指标共同名称和月份外连接，不按行列位置配对。

## 七、审阅与验收状态

- 已验证：用户原生JSON列通过数据库端英文状态检查确认“核心”正确；旧驱动下UTF-8文本兼容查询可正常显示中文。
- 已准备：四表LONGTEXT版DDL、已有表迁移SQL、Python写入前JSON严格校验及真实LONGTEXT类型、字符集检查，以及本版7个MCP工具契约。
- 本地验证：Python 3.11.13下42项测试通过，覆盖JSON合法性、中文与数值精度、版本去重、来源匹配及事务回滚；四表DDL、三列迁移定义、工具参数和只读SQL模板已做一致性核对。未连接内网MySQL执行迁移。
- 尚待实际环境执行：现有表类型迁移、迁移后直接读取中文、真实MySQL写入，以及MCP服务注册和7个场景端到端验收。文档不是已运行服务。
- MCP SQL不包含新建、删除、更新语句；类型迁移仅在单独SQL文件中，由部署时执行一次。不能用旧表的界面乱码为理由重写本来正确的中文。

审阅重点：场景是否覆盖实际提问；月份默认值是否符合使用习惯；“核心服务”等原始组名是否保留；逐日baseline是否仅在场景4出现；市场差值是否只比较同一月份。
