# 美国CPI预测_SQL清单_MCP信息

修订版：2026-10-20（在2026-10-14审阅版上修复月份参数兼容）。**4张物理表、7个只读MCP功能场景。** 本版采用兼容旧JDBC 5.1.35的存储结构：`groups_json`、`leaves_json`、`daily_json`列名不变，类型由原生`JSON`改为`LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci`，内容仍为标准JSON数组文本。

数据库`forecast_month`的实际月份口径已明确：**字段类型为DATE，库内日期均取该月1日，显示为`YYYY-MM-01`，例如`2026-09-01`。** 同类业务月份字段（`market_month`、`strategy_month`及官方`observation_month`）按本方案也使用月首日口径。用户请求的`YYYY-MM`只是外部月份表达，不是可直接绑定DATE列的完整日期。

本次数据库端检查确认第一条分项的所属组正确存为“核心”；使用字节重建后的UTF-8文本查询也已正常显示。问题来自旧驱动读取JSON的路径，不应把正确中文反向转码后写回数据库。LONGTEXT方案是为了让固定使用5.1.35的环境直接按普通文本读取。

本文是接入契约、SQL审阅稿和MCP服务端修复要求；对应工具清单为`CPI_MCP工具定义.json`。2026-10-20的实际请求分析显示：`get_cpi_market_expectations`与`get_cpi_strategy_advice`存在月份值与校验/SQL绑定口径不一致的问题；逐日预测的默认日期分支也存在同类风险。本次仅更新文档约定，不等于已修改或部署运行中的MCP代码。LONGTEXT迁移完成情况仍未核实，不得据此认定线上表已调整。

## 一、落库范围与部署动作

| 表名                                      | 用途              | 记录口径                   |
| --------------------------------------- | --------------- | ---------------------- |
| fmqt_forecast_us_cpi_prediction_result  | 报告版本和三类预测数组     | 每个三文件组合版本一行            |
| fmqt_forecast_us_cpi_market_expectation | 机构预测和来源提供的市场中位数 | 关联report_id，按实际机构数量保存  |
| fmqt_forecast_us_cpi_strategy_advice    | 策略建议完整原文        | 关联report_id，独立保存策略所属月份 |
| fmqt_forecast_us_cpi_official_history   | 官方SA环比、NSA指数和来源 | 独立于报告，月份+指标名称为主键       |

三个结果文件分别是`model_results.json`、`market_expectations.json`、`strategy_advice.txt`。官方历史来自`CPI_nsa_monthly.xlsx`与`CPI_sa_mom.xlsx`；英文文件名下的表头仍按中文处理。只读取已生成文件，不追溯或调用后端生成逻辑，不修改前端。

- 新建库表：执行`database/美国CPI_DBeaver建表.sql`，或使用相同内容的`forecast_us_cpi_schema.sql`。
- 已建原生JSON列：执行一次`database/美国CPI_已有JSON列改LONGTEXT.sql`，转换现有三列，不删除报告或重新导入。`CREATE TABLE IF NOT EXISTS`不会修改已存在的列。
- 更新`forecast_us_db.py`：保持与中国脚本相同的公共配置导入和PyMySQL连接方式。新的列定义与DDL一致，并在写入前校验三个JSON数组，写入后核对UTF-8内容。
- 表数仍为4。列名、数组内部字段、report_id、来源摘要、版本去重口径、baseline范围不变；单纯类型迁移不产生新的报告版本。
- LONGTEXT不具备原生JSON类型的自动合法性校验。通过本脚本写入时由Python严格校验；绕过脚本直接写SQL的其他程序必须自行校验。
- **本次MCP修复**：服务端统一月份参数的校验、默认值归一化与SQL参数绑定，优先修复场景5、6，同时修复场景4的默认日期派生，并对场景1、2、3、7使用同一套月份处理；不改物理表、不重导数据、不更改7个工具名称。若工具定义文件或运行时参数Schema限制了`YYYY-MM-01`，需同步调整或在入口归一化，不能只更新MD。

## 二、预测文本数组和字段中文含义

| 数据列         | 保存内容                                   | 数量规则              |
| ----------- | -------------------------------------- | ----------------- |
| groups_json | 当前预测月全部原始组名；另保留总体、核心十二个月窗口内的main/far趋势 | 以源文件实际记录为准，不固定组数  |
| leaves_json | 当前预测月实际提供的分项、方法、权重及同比和环比               | 不强制26条，缺项不补造      |
| daily_json  | 目标月实际提供日期的总体、核心、食品、能源逐日预测              | 不强制满月、满四组，仅保留已有记录 |

组级按当前源文件实际提供的原始组名保存和查询，例如总体、核心、食品、能源、核心商品、住房、核心服务；缺少的组别不补造。顶部同比、SA环比均不保存baseline；baseline仅出现在逐日预测。结构化模型可信度不保存。策略正文作为原文可能含历史评价文字，不另提取成可信度。

趋势窗口按当前落库脚本，以预测月和源文件远期月份的最大值为终点，向前覆盖十二个自然月；不代表每个月都存在记录。查询返回实际保留的数据，不承诺完整历史分项或完整预测轨迹。

### groups_json[]

| 字段             | 中文含义                      |
| -------------- | ------------------------- |
| record_type    | 来源类别，main组级预测或far远期预测     |
| forecast_month | 该记录所属预测月份，以当月一日存储         |
| group_name     | CPI组别名称                   |
| weight         | 占总体CPI的权重，百分数口径，100代表百分之百 |
| yoy            | CPI同比预测涨幅，百分数口径           |
| nsa_mom        | 未季节调整的CPI环比预测涨幅，百分数口径     |
| sa_mom         | 季节调整后的CPI环比预测涨幅，百分数口径     |

### leaves_json[]

| 字段             | 中文含义                      |
| -------------- | ------------------------- |
| forecast_month | 该记录所属预测月份，以当月一日存储         |
| item_name      | 源文件中的分项原始名称               |
| group_name     | CPI组别名称                   |
| weight         | 占总体CPI的权重，百分数口径，100代表百分之百 |
| method         | 叶子分项预测方法名称                |
| yoy            | CPI同比预测涨幅，百分数口径           |
| nsa_mom        | 未季节调整的CPI环比预测涨幅，百分数口径     |
| sa_mom         | 季节调整后的CPI环比预测涨幅，百分数口径     |

### daily_json[]

| 字段                | 中文含义                     |
| ----------------- | ------------------------ |
| target_month      | 逐日预测的目标CPI月份             |
| observation_date  | 逐日展示日期，不等于独立预测发布时间       |
| group_name        | CPI组别名称                  |
| yoy               | CPI同比预测涨幅，百分数口径          |
| baseline          | 仅用于逐日预测的月初同比惯性基线，百分数口径   |
| correction        | 逐日同比相对月初基线的累计修正量，单位百分点   |
| event_sources     | 当日更新来源原文；同一天各组重复保存，不按组累计 |
| is_future_display | 展示日期是否晚于模型生成日，0否1是       |

## 三、通用连接、参数和返回约定

1. 5.1.35指DBeaver的MySQL JDBC驱动版本，不是MySQL服务器版本。本文主查询使用基础SELECT和服务层JSON解析，不依赖`JSON_TABLE`、CTE或MySQL 8专有语法。
2. 三个LONGTEXT列按普通UTF-8文本读取，再在MCP服务层解析为数组、筛选和排序。客户端返回`data`为数组对象，不把JSON文本重复编码成一个大字符串。
3. 示例SQL中的`:name`是逻辑参数。使用PyMySQL执行时转换为`%s`并单独传入参数元组；按出现顺序绑定，重复参数也重复传入，不拼接用户值。以下带参数SQL供接入审阅，不能原样直接交给PyMySQL。
4. **统一月份协议（本次修复）**：数据库`forecast_month`为`DATE`列，**数据库存值固定为`YYYY-MM-01`（如`2026-09-01`）**，不是`YYYY-MM`，其中`01`表示该月第一天而非任意日期。其他业务月份列遵循月首日约定。对所有「月份」参数（`forecast_month`、`start_month`、`end_month`等），对外推荐`YYYY-MM`，兼容`YYYY-MM-01`；数据库`DATE`/`DATETIME`对象和数据库返回的月份日期字符串，也必须进入同一个月份校验函数，统一得到**月首`datetime.date`（如`date(2026, 9, 1)`）**，作为SQL绑定参数与`DATE`列精确比较，或与JSON月份归一化后比较。`YYYY-MM-15`这类非月首的完整日期不得默默转换为月首，应作为非法月份拒绝。不得把裸`2026-09`直接绑定MySQL `DATE`列，也不得把库里合法的`2026-09-01`误判为非法参数。`start_date`、`end_date`属于**逐日日期**，须按完整`YYYY-MM-DD`单独处理并保留日精度。数量、名称长度和范围仍须限制。
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

报告定位中的report_id默认NULL。场景2—6的业务月份参数用于过滤已选报告里的记录，不自动寻找其他报告；需要查看旧版时明确传report_id。场景1专门用于检索版本。报告定位后立即规范化`forecast_month`、`market_month`和`strategy_month`，但保留三者原有业务含义，不以模型月份覆盖市场或策略月份。

### 月份与逐日日期的服务端实现约束（2026-10-20修复）

**故障定位**：数据库月份列实际为月首`DATE`值，例如`2026-09-01`；场景5、6的默认分支从报告表取回`market_month`/`strategy_month`（驱动可能返回`date`对象或`YYYY-MM-01`文本），但在后续仍按外部请求的`YYYY-MM`规则校验，可能抛出格式错误；显式传`YYYY-MM`若直接绑定到SQL `DATE`列，也可能出现`Incorrect date value`。场景4在默认生成日首、月末时需使用同一套已规范化的月份值。以下是服务端实现要求，并非通过改文档本身能够修复运行时错误。

```python
import calendar
import re
from datetime import date, datetime


def normalize_month(value):
    """外部 YYYY-MM / YYYY-MM-01 / 数据库月首日期 -> date；不接受 None。"""
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    elif isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}", value):
            d = date.fromisoformat(value + "-01")
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            d = date.fromisoformat(value)
        else:
            raise ValueError("month must be YYYY-MM or YYYY-MM-01")
    else:
        raise ValueError("month has unsupported type")
    if d.day != 1:
        raise ValueError("month date must be the first day (YYYY-MM-01)")
    return d


def normalize_day(value):
    """逐日展示日期 -> 精确 date，不得舍弃日期中的日。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("day must be YYYY-MM-DD")


def month_bounds(month_value):
    first = normalize_month(month_value)
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    return first, last
```

**必须遵守的执行次序**：①选定单一`ready`报告并取得其日期字段；②根据工具语义确定「外部显式月份优先、否则取报告默认月份」；③无论来源统一调用`normalize_month`；④将归一后的`date`对象作为**绑定参数**比较SQL `DATE`列，并与已规范化的JSON记录月份比较；⑤序列化返回值（可显示`YYYY-MM-DD`）与参数校验分离。仅在参数允许缺省时跳过对`None`的归一化，不能因为是数据库默认值而完全绕过日期有效性检查。

```python
# 场景5、6的关键流程；二者只在默认字段和SQL语句上不同。
raw_month = requested_forecast_month
if raw_month is None:
    raw_month = report["market_month"]  # 场景6改为 report["strategy_month"]
effective_month = normalize_month(raw_month)
# PyMySQL：SQL里的逻辑 :effective_month 应转换为 %s；传date对象。
# cursor.execute(sql, (resolved_report_id, effective_month, ...))
# 如果比较JSON记录中的月份，也先 normalize_month(row["forecast_month"])。
```

**兼容性边界**：输入`2026-09`与`2026-09-01`均绑定`date(2026, 9, 1)`，与数据库`forecast_month=2026-09-01`精确匹配；`2026-09-15`虽然是合法自然日，但不是约定的**月份存储日期**，应返回参数校验错误，不能静默改成9月1日。月份参数也不可用`2026-13`、`2026-02-30`等非法值。`normalize_day`则保留日精度。无数据应返回约定的空结果，不能以日期解析错误伪装为「无数据」。

**SQL绑定提醒**：`:effective_month`是文档逻辑占位符，实际PyMySQL必须转换为`%s`并按出现顺序绑定`date`对象；同样处理`:forecast_month`、`:start_month`、`:end_month`。不要把月份文字直接拼接到SQL，也不要依赖数据库隐式把`YYYY-MM`解释为`DATE`。

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

| 编号 | 场景             | MCP工具                       |
| -- | -------------- | --------------------------- |
| 1  | 报告版本与各来源月份     | get_cpi_reports             |
| 2  | 当前组级预测和总体、核心趋势 | get_cpi_group_forecasts     |
| 3  | 当前预测月分项明细      | get_cpi_item_forecasts      |
| 4  | 逐日预测、专用基线和累计修正 | get_cpi_daily_evolution     |
| 5  | 机构预测及同月模型比较    | get_cpi_market_expectations |
| 6  | 策略建议原文         | get_cpi_strategy_advice     |
| 7  | 官方历史SA环比及NSA指数 | get_cpi_official_history    |

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

**口径**：同月可有多个版本；按完整三文件组合及策略月份去重。月份不同如实展示，不强行统一。`forecast_month`作为可选输入时也先执行`normalize_month`，再绑定到SQL中与`DATE`列比较；不传时SQL保留原有`NULL`条件语义。

### 2. 当前组级预测和总体、核心趋势

**业务问题**：总体、核心、食品或其他原始组别预测多少？已保存的趋势如何变化？

**参数**：report_id可选；forecast_month可选；scope取main/far/all，默认main；group可选，按所选报告实际存在的中文组名精确匹配，不使用固定组名列表过滤。

```sql
SELECT report_id, forecast_month, groups_json AS groups_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```

**服务层处理**：解析groups_text；scope不是all时筛选record_type；main且月份未给时使用已归一化的报告`forecast_month`，far/all且月份未给时保留全部已存月份；传月份时先`normalize_month`再精确筛选。JSON记录中的`forecast_month`（通常为`YYYY-MM-DD`）也先`normalize_month`后比较，避免`YYYY-MM`对`YYYY-MM-DD`的字符串误比对。group精确筛选。按forecast_month、group_name、record_type排序，保持来源类别，不把main和far混成同一条。

**返回字段**：report_id及groups_json对象的全部七个字段，中文含义见第二节。这里不返回baseline或模型可信度。不存在的原始组名返回空数组并说明未提供，不把“核心服务”改名。

### 3. 当前预测月分项明细

**业务问题**：家居、汽油、运输服务等分项的预测、权重和方法是什么？

**参数**：report_id可选；group可选，按实际分项所属组名校验；item可选，精确匹配分项原名。

```sql
SELECT report_id, forecast_month, leaves_json AS leaves_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```

**服务层处理**：解析leaves_text，将每条记录`forecast_month`和报告模型`forecast_month`都归一化到月首后比较，再按group、item过滤；按weight降序、item_name升序，NULL权重排最后。返回实际分项数量，不固定26条。

**返回字段**：report_id及leaves_json对象的全部八个字段，中文含义见第二节。历史版本通过report_id查看，不承诺一份报告中包含历史分项序列。

### 4. 逐日预测、专用基线和累计修正

**业务问题**：目标月逐日同比如何变化，相对月初基线累计修正多少？

**参数**：report_id可选；group可选（总体/核心/食品/能源）；start_date、end_date可选，分别默认目标月首日、末日；include_future默认false。

```sql
SELECT report_id, forecast_month, daily_json AS daily_text
FROM fmqt_forecast_us_cpi_prediction_result
WHERE report_id = :resolved_report_id AND status = 'ready';
```

**服务层处理（修复默认日期分支）**：报告的`forecast_month`无论以数据库`DATE`对象还是日期字符串返回，先执行`normalize_month`，然后调用`month_bounds`得到该月首日与末日。`start_date`未传时取首日，`end_date`未传时取末日；显式传入时分别执行`normalize_day`，不得拿完整日期套用`YYYY-MM`校验器，也不得用`normalize_month`截掉日。解析daily_text，将`target_month`与报告月份分别`normalize_month`后比较，`observation_date`用`normalize_day`；起止日期必须属于目标月且开始不晚于结束。按日期**闭区间**筛选、再按group过滤。include_future=false时仅保留is_future_display=0。按observation_date、group_name排序，不补造缺失日期或组别。

**返回字段**：report_id及daily_json对象的全部八个字段，中文含义见第二节。baseline仅用于本场景的逐日同比月初基线，correction是相对它的累计修正，二者均非顶部SA环比基线。event_sources是来源原文，同一天在不同组重复保存，不能按组重复累计事件次数。展示日期不是独立运行或发布时间。

### 5. 机构预测及同月模型比较

**业务问题**：机构和市场中位数预测多少？同一月份模型比市场高或低多少？

**参数（本次重点修复）**：report_id可选；forecast_month可选，**未传时取所选报告的`market_month`，显式传入时优先使用输入值**；median_only默认false；institution可选。选择月份后**一律**调用`normalize_month`产生`effective_month: date`，包括数据库默认值；不允许把`market_month`的`DATE`对象或`YYYY-MM-DD`原值直接扔进只接受`YYYY-MM`的校验器，也不允许将裸`YYYY-MM`直接传入MySQL `DATE`比较。`institution_name`映射自institution，median_only绑定为0/1。

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

**服务层处理**：先用`effective_month`作为绑定的`date`参数查询机构表；解析groups_text，并将组级记录中的`forecast_month`也归一到月首，仅从record_type=main、同月数据中分别精确取“总体”和“核心”；不使用其他月份、其他报告或far记录替代。机构记录中的四个指标分别与同月模型匹配，某个模型值缺失时对应模型值和gap均返回null，并在warnings说明。缺少市场记录时返回空数组。`meta.applied_filters.forecast_month`记录本次实际查询月份，不应把模型月份误写为市场查询月份。

**返回字段与中文含义**：

| 字段                                      | 中文含义                      |
| --------------------------------------- | ------------------------- |
| report_id / forecast_month              | 报告ID / 本条市场预测所属月          |
| institution_name / is_median            | 机构原名 / 是否来源提供的市场中位数       |
| headline_yoy / headline_mom             | 市场总体同比 / 总体SA环比           |
| core_yoy / core_mom                     | 市场核心同比 / 核心SA环比           |
| model_headline_yoy / model_headline_mom | 同月模型总体同比 / 总体SA环比         |
| model_core_yoy / model_core_mom         | 同月模型核心同比 / 核心SA环比         |
| headline_yoy_gap / headline_mom_gap     | 模型减市场的总体同比差 / SA环比差，单位百分点 |
| core_yoy_gap / core_mom_gap             | 模型减市场的核心同比差 / SA环比差，单位百分点 |

若同月模型来自报告内历史预测，须在warnings说明报告生成时间，不能解释为官方公布前可用的历史交易信号。市场中位数使用源文件已提供记录，不在本工具中重新计算。

### 6. 策略建议原文

**业务问题**：已有策略建议正文及对应月份是什么？

**参数（本次重点修复）**：report_id可选；forecast_month可选，**未传时取所选报告的`strategy_month`，显式传入时优先使用输入值**；两条分支都调用`normalize_month`转换为`effective_month: date`后执行SQL绑定，不得跳过默认月份兼容处理，不得裸传`YYYY-MM`进入MySQL DATE比较。

```sql
SELECT a.report_id, a.forecast_month, a.content,
       r.strategy_sha256, r.generated_at AS model_generated_at, r.imported_at
FROM fmqt_forecast_us_cpi_strategy_advice a
JOIN fmqt_forecast_us_cpi_prediction_result r ON r.report_id = a.report_id
WHERE a.report_id = :resolved_report_id AND r.status = 'ready'
  AND a.forecast_month = :effective_month;
```

**服务层处理**：取得固定`resolved_report_id`和`effective_month`后，执行绑定查询`a.forecast_month = :effective_month`；PyMySQL实际使用`%s`并传`date`对象。即使报告默认`strategy_month`是`2026-09-01`或数据库`date`对象，也要与显式`2026-09`走同一规范化结果。返回`meta.applied_filters.forecast_month`反映策略查询月份，不替换报告模型的月份字段。

**返回字段**：report_id（报告ID）、forecast_month（策略所属月）、content（完整策略原文）、strategy_sha256（源文件摘要）、model_generated_at（模型生成时间）、imported_at（报告入库时间）。模型生成时间不是策略撰写时间。

**口径**：原文保持不变，不生成新策略、不提取历史评分为模型可信度。策略所属月份来自导入参数，不从模型月份推断。查询月份与已存策略不符时返回空数组。

### 7. 官方历史SA环比及NSA指数

**业务问题**：某月总体、核心或分项官方SA环比、NSA指数是多少？同比如何变化？

**参数**：indicator、start_month、end_month均可选；limit默认1000、最大5000。不接受report_id。indicator_name映射自indicator，使用工作簿去除指标后缀后的共同中文名称。`start_month`、`end_month`显式值及最新月份查询返回的`MAX(observation_month)`均调用`normalize_month`，统一使用月首`date`对象参与SQL日期条件和区间计算。


结束月份未给时，先查所选指标最新有值月份；指标留空时取官方表最新月：

```sql
SELECT MAX(observation_month) AS latest_month
FROM fmqt_forecast_us_cpi_official_history
WHERE (:indicator_name IS NULL OR indicator_name = :indicator_name);
```

开始月份未给时取**已规范化**的最终结束月向前11个月；对跨年、闰年转换使用自然月运算，不要用固定330天代替。开始月份不能晚于结束月份；无数据时返回空数组。主查询limit_rows绑定limit+1，多取一行判断截断，最终最多返回limit行，并设置meta.truncated。

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

| 字段                    | 存储类型                                                               | 中文注释                                        |
| --------------------- | ------------------------------------------------------------------ | ------------------------------------------- |
| report_id             | BIGINT UNSIGNED NOT NULL AUTO_INCREMENT                            | 本次三文件报告版本主键                                 |
| bundle_sha256         | CHAR(64) NOT NULL                                                  | 三文件摘要及策略所属月份的组合摘要，用于重复导入去重                  |
| schema_version        | INT NOT NULL                                                       | 模型结果JSON结构版本                                |
| official_latest_month | DATE NOT NULL                                                      | 模型运行时最新官方CPI月份                              |
| forecast_month        | DATE NOT NULL                                                      | 模型当期预测月份，库内固定存当月1日，形如YYYY-MM-01               |
| market_month          | DATE NOT NULL                                                      | 市场预期所属月份，与模型月份分别保存                          |
| strategy_month        | DATE NOT NULL                                                      | 策略建议所属月份，由导入参数指定                            |
| generated_at          | DATETIME(6) NOT NULL                                               | 模型源文件生成时间，保留来源本地时间，不自动转换时区                  |
| model_sha256          | CHAR(64) NOT NULL                                                  | 模型原文件字节的SHA256摘要                            |
| market_sha256         | CHAR(64) NOT NULL                                                  | 市场原文件字节的SHA256摘要                            |
| strategy_sha256       | CHAR(64) NOT NULL                                                  | 策略原文件字节的SHA256摘要                            |
| groups_json           | LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL | 组级预测及总体核心趋势JSON文本，包含月份、组别、权重、同比和环比，不含基线     |
| leaves_json           | LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL | 当前月实际分项JSON文本，包含名称、组别、方法、权重、同比和环比，不含基线      |
| daily_json            | LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL | 四组逐日预测JSON文本，baseline仅在此保存，包含累计修正、来源和未来展示标记 |
| status                | VARCHAR(16) NOT NULL                                               | 报告写入状态，事务内writing，全部写入后ready                |
| imported_at           | DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)                  | 本报告数据库导入时间，非业务生成时间                          |

### fmqt_forecast_us_cpi_market_expectation

| 字段               | 存储类型                     | 中文注释                    |
| ---------------- | ------------------------ | ----------------------- |
| report_id        | BIGINT UNSIGNED NOT NULL | 所属三文件报告版本主键             |
| record_key       | CHAR(64) NOT NULL        | 本表业务键组合的SHA256摘要，防止重复记录 |
| forecast_month   | DATE NOT NULL            | 市场预期所属月份，固定YYYY-MM-01月首日期 |
| institution_name | VARCHAR(255) NOT NULL    | 机构名称或市场中位数              |
| is_median        | TINYINT(1) NOT NULL      | 是否为市场中位数，0否1是           |
| headline_yoy     | DECIMAL(18,8) NULL       | 市场预计总体同比，百分数口径          |
| headline_mom     | DECIMAL(18,8) NULL       | 市场预计总体环比，业务按SA比较，百分数口径  |
| core_yoy         | DECIMAL(18,8) NULL       | 市场预计核心同比，百分数口径          |
| core_mom         | DECIMAL(18,8) NULL       | 市场预计核心环比，业务按SA比较，百分数口径  |

### fmqt_forecast_us_cpi_strategy_advice

| 字段             | 存储类型                     | 中文注释                    |
| -------------- | ------------------------ | ----------------------- |
| report_id      | BIGINT UNSIGNED NOT NULL | 所属三文件报告版本主键             |
| record_key     | CHAR(64) NOT NULL        | 本表业务键组合的SHA256摘要，防止重复记录 |
| forecast_month | DATE NOT NULL            | 策略所属月份，固定YYYY-MM-01月首日期   |
| content        | MEDIUMTEXT NOT NULL      | 策略完整原文，不自动改写源文本         |

### fmqt_forecast_us_cpi_official_history

| 字段                | 存储类型                                              | 中文注释                         |
| ----------------- | ------------------------------------------------- | ---------------------------- |
| observation_month | DATE NOT NULL                                     | 官方观测所属月份，以当月一日存储             |
| indicator_name    | VARCHAR(255) NOT NULL                             | 官方指标中文名称，去掉数值或季调环比后缀后的共同名称   |
| nsa_series_id     | VARCHAR(64) NULL                                  | 未季调指数的源指标代码                  |
| sa_series_id      | VARCHAR(64) NULL                                  | 官方季调环比的源指标代码                 |
| nsa_index         | DECIMAL(24,8) NULL                                | 官方未季调CPI指数当月值，保持原指数基期，不转成百分比 |
| sa_mom            | DECIMAL(18,8) NULL                                | 官方季节调整后月环比，百分数口径，0.4代表百分之零点四 |
| nsa_source_file   | VARCHAR(255) NOT NULL                             | NSA指数源工作簿文件名                 |
| sa_source_file    | VARCHAR(255) NOT NULL                             | SA环比源工作簿文件名                  |
| nsa_sheet         | VARCHAR(128) NULL                                 | NSA指数所在工作表名称                 |
| sa_sheet          | VARCHAR(128) NULL                                 | SA环比所在工作表名称                  |
| nsa_source_sha256 | CHAR(64) NOT NULL                                 | 本次NSA指数工作簿字节的SHA256摘要        |
| sa_source_sha256  | CHAR(64) NOT NULL                                 | 本次SA环比工作簿字节的SHA256摘要         |
| updated_at        | DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) | 该官方记录最近一次导入或修订时间，非官方发布时间     |

## 六、部署输入路径

| 文件      | 非Windows默认路径                                         |
| ------- | ---------------------------------------------------- |
| 模型结果    | /qts/research/us_cpi/result/model_results.json       |
| 机构预期    | /qts/research/us_cpi/result/market_expectations.json |
| 策略原文    | /qts/research/us_cpi/result/strategy_advice.txt      |
| 官方NSA指数 | /qts/research/us_cpi/data/CPI_nsa_monthly.xlsx       |
| 官方SA环比  | /qts/research/us_cpi/data/CPI_sa_mom.xlsx            |

Windows从落库模块目录上两级的`domain/macro/index/oversea/us_cpi`读取data/result；可通过US_CPI_DIR或既有命令行路径参数覆盖。两份官方工作簿第一行是中文指标名，第二行是来源指标ID；读取后续行已有缓存数值，不修改或计算Excel。按中文指标共同名称和月份外连接，不按行列位置配对。

## 七、日期修复回归测试（服务端落地后执行）

**先验收场景5、6，再验收其他日期相关分支。** 以下测试均针对同一固定`ready`报告（若使用示例2026-09，需先确认报告确实有相应市场/策略记录）；期望返回结构与权限约定不变，存在记录时不应因为参数格式被拒绝。每次测试同时检查MCP层错误消息、实际绑定参数类型、返回`meta.applied_filters`，不要只检查SQL是否能在DBeaver单独运行。

| 场景 | 测试输入 | 必须满足的判定条件 |
| --- | --- | --- |
| 通用月份归一化 | `2026-09`、`2026-09-01`、`date(2026,9,1)`、`datetime(2026,9,1)` | 全部得到`date(2026,9,1)`，无格式校验错误和Incorrect date value |
| 通用异常月份 | `2026-13`、`2026-02-30`、`2026-09-15`、`2026/09` | 返回明确参数校验错误；不执行SQL，不报原始数据库异常 |
| 1 报告版本 | `forecast_month=2026-09`和`2026-09-01` | 查询结果一致；SQL参数绑定为DATE，未给月份时不误过滤 |
| 2 组级预测 | `scope=main`且月份缺省；显式`forecast_month=2026-09` | 缺省读取报告模型月份，显式过滤与JSON月份比较一致；far/all原行为不变 |
| 3 分项明细 | 不传月份，仅指定`report_id` | JSON的`YYYY-MM-DD`与报告数据库日期正确匹配，不因格式差异变空 |
| 4 逐日预测 | 不传日期；显式`start_date=2026-09-01`、`end_date=2026-09-30` | 同一报告同一组结果一致，且默认日期范围为9/1—9/30；不得把9/30转成9/1 |
| 4 日期边界 | `start_date=2026-09-30`、`end_date=2026-09-01` | 返回日期区间错误；不存在时才返回空数组 |
| 5 机构预测 | 不传forecast_month；显式`2026-09`；显式`2026-09-01` | 三条路径均使用规范化DATE；若默认市场月为2026-09，则结果一致；同月模型比较不串月 |
| 6 策略建议 | 不传forecast_month；显式`2026-09`；显式`2026-09-01` | 三条路径均使用规范化DATE；若默认策略月为2026-09，则返回同一原文，不生成新策略 |
| 5、6 业务月份分离 | 同一报告的`forecast_month`、`market_month`、`strategy_month`不同 | 不传参分别取市场月/策略月，不得错误地取模型月；显式不匹配月份返回空数组及warnings |
| 7 官方历史 | `start_month=2026-08`、`end_month=2026-09`；不传日期 | 显式月份作为月首DATE绑定；默认结束月来自官方表，向前11个月，不读取报告月份 |
| SQL参数安全 | 月份字符串夹带SQL语法、无效日期 | 参数校验拒绝；所有用户值只能通过SQL绑定参数传入 |

**发布验收**：修改实际MCP服务端月份归一化/默认值处理/SQL绑定代码；如有独立的工具参数Schema与`CPI_MCP工具定义.json`，需同步接受`YYYY-MM`和`YYYY-MM-01`两种月份输入，或在Schema校验前统一归一化；部署后逐项回归测试，特别确认场景5、6不再报`Incorrect date value`。只修改本文MD不能修复已运行的接口。

## 八、审阅与验收状态

- 已验证：用户原生JSON列通过数据库端英文状态检查确认“核心”正确；旧驱动下UTF-8文本兼容查询可正常显示中文。
- 已准备：四表LONGTEXT版DDL、已有表迁移SQL、Python写入前JSON严格校验及真实LONGTEXT类型、字符集检查，以及本版7个MCP工具契约。
- 本地验证：Python 3.11.13下42项测试通过，覆盖JSON合法性、中文与数值精度、版本去重、来源匹配及事务回滚；四表DDL、三列迁移定义、工具参数和只读SQL模板已做一致性核对。未连接内网MySQL执行迁移。
- 2026-10-20新增：根据实际接口请求分析补充了默认月份分支和`YYYY-MM`/`YYYY-MM-01`/数据库日期对象的统一处理规则，并根据实际数据库确认`forecast_month`固定以每月1日存储；**尚未确认MCP服务端代码已修复或部署**。原有审阅版中的「服务尚未注册」属于此前状态记录，不作为当前实时部署事实。
- 尚待实际环境核验：现有表类型迁移状态、迁移后读取中文、真实MySQL写入、运行中工具Schema一致性，以及7个场景（尤其5、6及4默认日期）的端到端回归。本文不是服务运行成功的证明。
- MCP SQL不包含新建、删除、更新语句；类型迁移仅在单独SQL文件中，由部署时执行一次。不能用旧表的界面乱码为理由重写本来正确的中文。

审阅重点：确保**数据库`forecast_month`恒为月首日`YYYY-MM-01`，外部`YYYY-MM`被正确转换为同一个月首`date`值**；月份**外部参数、数据库默认值、SQL绑定值、JSON记录值**是否按各自语义先规范化再比较；场景5、6是否能同时处理缺省与显式月份；场景4是否保留完整日精度；场景是否覆盖实际提问；“核心服务”等原始组名是否保留；逐日baseline是否仅在场景4出现；市场差值是否只比较同一月份。
