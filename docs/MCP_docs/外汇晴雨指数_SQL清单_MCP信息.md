# 外汇晴雨指数的SQL清单与MCP信息

本文整理外汇晴雨指数历史页面、日期选择与3M/6M研判查询所用的只读SQL。每条给出用途、参数、可执行的绑定参数SQL和返回字段，并提供MCP工具定义与接口对应关系。

## 通用约定

- 表名为 `fmqt_fx_barometer_usdcny_history`；公司更名时同步修改全部查询。业务仅USDCNY，一日一条，signal_date主键，result_json保存完整单日JSON。
- 只使用SELECT，不含数据写入、建表、修改或删除语句。MCP账户仅需SELECT权限。
- `:name`为绑定参数，交给公司SQL执行层/SQLAlchemy text处理，不用字符串替换拼接用户输入。
- 日期为真实YYYY-MM-DD；可选参数缺省传SQL NULL，不传空字符串。应用需做日历合法性校验。
- page从1开始，limit为1至200整数，offset=(page-1)*limit；SQL中的LIMIT/OFFSET均绑定整数。
- 数据截至2026-09-14，并非实时行情。历史JSON是重算研判，不是当时发布档案。缺少交易日返回空，不能沿用另一日预测。
- MySQL返回原生JSON对象或字符串均可，先解析JSON再按字段取值；不能把格式化后的展示值覆盖原数据。
- 当前nodes顺序由导入门禁固定为3M、6M，所以数组0/1分别对应3M/6M。若改动此契约，查询与门禁需要一起调整。

## 一、状态与交易日

### Q1 数据范围

用途：查看条数及起止日期；无参数。

```sql
SELECT COUNT(*) AS total_count,
       MIN(signal_date) AS first_date,
       MAX(signal_date) AS latest_date
FROM fmqt_fx_barometer_usdcny_history;
```

返回total_count（整数）、first_date/latest_date（日期或空）。空库count=0；不表示数据已经更新到今日。

### Q2 日期列表与预测准备期

用途：日期选择器与首个有预测的日期；无参数。

```sql
SELECT signal_date,
       JSON_TYPE(JSON_EXTRACT(result_json, '$.forecast')) = 'OBJECT' AS has_forecast
FROM fmqt_fx_barometer_usdcny_history
ORDER BY signal_date;
```

返回signal_date与has_forecast（0/1）。第一条has_forecast=1即firstForecastDate。准备期JSON null不会被误判为有预测。

## 二、完整单日结果

### Q3 最新完整JSON

用途：首次打开页面默认日期；无参数。

```sql
SELECT signal_date, result_json
FROM fmqt_fx_barometer_usdcny_history
ORDER BY signal_date DESC
LIMIT 1;
```

### Q4 指定日期完整JSON

参数day：交易日字符串。不存在该日返回零行。

```sql
SELECT signal_date, result_json
FROM fmqt_fx_barometer_usdcny_history
WHERE signal_date = :day
ORDER BY signal_date DESC
LIMIT 1;
```

Q3/Q4返回signal_date及完整result_json，应用应核对result_json.date等于主键。对应GET `/research/fxbarometer.result`。

## 三、区间与分页历史

### Q5 区间总数

参数start_date/end_date可为NULL；闭区间；用于分页total。

```sql
SELECT COUNT(*) AS total_count
FROM fmqt_fx_barometer_usdcny_history
WHERE (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date);
```

### Q6 区间倒序分页

参数start_date/end_date/limit/offset。返回完整历史JSON，不自动限制最近一个月。

```sql
SELECT signal_date, result_json
FROM fmqt_fx_barometer_usdcny_history
WHERE (:start_date IS NULL OR signal_date >= :start_date)
  AND (:end_date IS NULL OR signal_date <= :end_date)
ORDER BY signal_date DESC
LIMIT :limit OFFSET :offset;
```

对应GET `/research/fxbarometer.history`，返回items、total、page、page_size。仓储实现对非空过滤条件生成等价参数化WHERE，避免不必要的NULL分支。

## 四、走势图与日期边界

### Q7 选定日期及之前的完整历史

参数day。应用从末行取得该日预测并组装历史OHLC，先核对末行日期恰好等于day；若day无记录，不能将前一日冒充所选日期。

```sql
SELECT signal_date, result_json
FROM fmqt_fx_barometer_usdcny_history
WHERE signal_date <= :day
ORDER BY signal_date;
```

对应GET `/research/fxbarometer.dashboard?date=...`。一条SELECT取得历史及选定行，避免多次查询夹入不同批次数据。K线不包含选定日之后的行情；3M/6M取选定行自身forecast。

### Q8 不限截止日的全部历史

无参数。省略dashboard日期时用末行作为最新结果。

```sql
SELECT signal_date, result_json
FROM fmqt_fx_barometer_usdcny_history
ORDER BY signal_date;
```

### Q9 近N条原始行情

参数day、limit（60/180或业务允许值）。MCP可用于轻量近期查询；应用将结果反转成升序。MA20计算需要额外前19条热身行情，不能用只有N条的首行推算历史均线；当前正式dashboard用Q7保留完整历史，前端再选显示窗口。

```sql
SELECT signal_date,
       JSON_EXTRACT(result_json, '$.ohlc') AS ohlc
FROM fmqt_fx_barometer_usdcny_history
WHERE signal_date <= :day
ORDER BY signal_date DESC
LIMIT :limit;
```

## 五、研判区间与模型信息

### Q10 指定日期的3M/6M节点

参数day。返回节点JSON；准备期节点为NULL，不应生成虚构区间。

```sql
SELECT signal_date,
       JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.forecast.basis')) AS basis,
       JSON_EXTRACT(result_json, '$.forecast.nodes[0]') AS node_3m,
       JSON_EXTRACT(result_json, '$.forecast.nodes[1]') AS node_6m
FROM fmqt_fx_barometer_usdcny_history
WHERE signal_date = :day;
```

### Q11 指定日期的模型元数据

参数day。返回完整model JSON，保留数值处理与标签截止标记。

```sql
SELECT signal_date,
       JSON_EXTRACT(result_json, '$.forecast.model') AS model_json
FROM fmqt_fx_barometer_usdcny_history
WHERE signal_date = :day;
```

### 返回JSON字段

| 字段 | 类型/单位/解释 |
|---|---|
| schemaVersion | 整数1 |
| symbol | USDCNY |
| date | 日期，等于signal_date |
| ohlc.open/high/low/close | 汇率，CNY/USD |
| forecast | 对象或null |
| forecast.basis | original-model |
| nodes[].label | 3M/6M |
| nodes[].horizon | 66/132交易日 |
| nodes[].date | 近似期限工作日 |
| nodes[].p05/p25/median/p75/p95 | 汇率分位数/中位数 |
| model.trainingDate | 训练特征截止日 |
| model.fitAsOf | 拟合截至日 |
| model.annualMu | 年化漂移，小数，不是保证收益 |
| model.annualSigma | 年化波动率，小数 |
| model.averageR2 | 测试评分，可为负，不是预测概率 |
| model.sigmaMethod | 原始拟合或不收敛后的等价单位缩放 |
| model.paths/seed | 20000/42 |
| model.refresh | monthly-mu-daily-sigma |
| model.latestRefit | 末日额外拟合布尔标记 |

内层25%–75%、外层5%–95%为名义分位区间，没有经过实际覆盖率验证；3M/6M走势不是自动交易指令。未知扩展字段以原JSON为准，不丢弃。

## 六、MCP工具定义与接口映射

下列是供公司MCP平台注册的工具定义，不表示本包运行了MCP服务器或提供/mcp端点。各工具只读数据库或上述GET接口，不调用维护API。

```json
{
  "tools": [
    {
      "name": "fx_barometer_status",
      "description": "查询USDCNY历史数据数量与可用日期范围，不能视为实时行情。",
      "inputSchema": {"type": "object", "properties": {}, "additionalProperties": false}
    },
    {
      "name": "fx_barometer_dates",
      "description": "查询全部可选交易日和预测准备期标记。",
      "inputSchema": {"type": "object", "properties": {}, "additionalProperties": false}
    },
    {
      "name": "fx_barometer_result",
      "description": "查询指定日期完整结果；省略日期查询库中最新结果。",
      "inputSchema": {"type": "object", "properties": {"date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}}, "additionalProperties": false}
    },
    {
      "name": "fx_barometer_history",
      "description": "闭区间倒序分页查询历史完整JSON。",
      "inputSchema": {"type": "object", "properties": {"start_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}, "end_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}, "page": {"type": "integer", "minimum": 1, "default": 1}, "page_size": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}, "additionalProperties": false}
    },
    {
      "name": "fx_barometer_dashboard",
      "description": "返回选定日及之前的历史行情和该日自身3M/6M，不混用最新预测。",
      "inputSchema": {"type": "object", "properties": {"date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}}, "additionalProperties": false}
    },
    {
      "name": "fx_barometer_intervals",
      "description": "读取指定日3M/6M分位节点及模型参数，非买卖指令。",
      "inputSchema": {"type": "object", "properties": {"date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}}, "required": ["date"], "additionalProperties": false}
    }
  ]
}
```

| MCP工具 | SQL | GET接口与处理 |
|---|---|---|
| fx_barometer_status | Q1 | health；查询范围，不宣称实时 |
| fx_barometer_dates | Q2 | dates |
| fx_barometer_result | Q3/Q4 | result，可选date |
| fx_barometer_history | Q5/Q6 | history，校验分页与日期起止 |
| fx_barometer_dashboard | Q7/Q8 | dashboard，可选date；按原JSON组装 |
| fx_barometer_intervals | Q10/Q11 | result后取forecast.nodes/model |

实现前校验真实日期、参数范围和账户权限。无法查询时保留明确错误，不把其他日期或模型值当作替代结果。MCP文本不得把R²改写为可信概率，也不得将名义分位范围表述为经验证的90%覆盖率。
