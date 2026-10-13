# 观点矩阵 / 宏观预测表 的 SQL 参考（供 MCP 接入）

本文档整理「观点矩阵」与「宏观预测表」两个页面用到的**全部 SQL**，
每条包含：用途（业务逻辑）、参数、可直接执行的 SQL、返回字段。

两页数据来自同一次研报分析流程，**分别存放在两张互不关联的表**：
观点矩阵 = `weekly_analyzer_data`，宏观预测表 = `weekly_macro_data`（各自以 `week` 为唯一键，一周一行）。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 周标签 | `YYYYMMDD-YYYYMMDD` 字符串，周一到周日，如 `20260928-20261004`；两表都用它作唯一键 |
| 观点方向 | `看涨` / `看跌` / `中性`（中文枚举） |
| 宏观预测值 | 多数是**数值**，少数是**文字**（如 `12月` / `10月` / `不动`）；处理方式见 2.6 / 2.7 |
| 单位 | 由 `macro_json.items[].unit` 给出：`%` / `千人` / `次` / `bp` / `时点` |
| 资产范围 | 固定白名单，31 个资产，分五类：境内利率 / 境外利率 / 外汇 / 全球股指 / 大宗 / 宏观指标；**SQL 里不排序**，展示顺序由应用层控制 |
| 写入方式 | 研报分析流程按周写入，一周一行，重复跑同一周为覆盖更新 |
| 查询权限 | 下列 SQL 全是 SELECT，只读账号即可 |


---

## 一、观点矩阵

**表名**：`weekly_analyzer_data`（一周一行）

| 字段 | 含义 |
|---|---|
| `week` | 周标签，主键，如 `20260928-20261004` |
| `data_json` | 观点正文 JSON：`{"views":[{"institution":...,"opinions":[...]}]}` |
| `market_overview` | 周度综述 Markdown 正文 |
| `created_at` | 写入时间 |

`data_json` 结构：

```json
{
  "views": [
    {
      "institution": "中信证券",
      "opinions": [
        {
          "category": "全球股指",
          "asset": "上证指数/A股",
          "direction": "看跌",
          "sources": [
            {"report": "研报标题", "date": "2026-10-02",
             "passage": "英文/中文原文摘录", "rationale": "判定理由"}
          ],
          "price": 3945.4003,        // 该资产当时价格（可选）
          "price_date": "2026-09-04",
          "price_change": -0.75,     // 可选
          "note": "内部分歧：2篇看涨 vs 3篇看跌"   // 可选，仅当同一机构内部分歧时出现
        }
      ]
    }
  ]
}
```

> `category` 取值：`境内利率` / `境外利率` / `外汇` / `全球股指` / `大宗` / `宏观指标`。
> 「宏观」分页用 `category IN ('宏观指标','宏观')` 过滤，其余为「资产」分页。

### 1.1 全部周（倒序）

**业务**：周选择器可选范围。

```sql
SELECT week FROM weekly_analyzer_data
ORDER BY week DESC;
```


### 1.2 某周机构数 / 观点数

**业务**：页面头部「覆盖 N 家机构 / M 条观点」。

**参数**：`week`

```sql
SELECT w.week,
       JSON_LENGTH(JSON_EXTRACT(w.data_json,'$.views')) AS institutions,
       (SELECT SUM(JSON_LENGTH(JSON_EXTRACT(o.opinions,'$')))
          FROM JSON_TABLE(JSON_EXTRACT(w.data_json,'$.views'),'$[*]'
               COLUMNS(opinions JSON PATH '$.opinions')) o) AS opinions
FROM (SELECT week, data_json FROM weekly_analyzer_data WHERE week = '20260928-20261004') w;
```

**返回**：`institutions`（机构数）、`opinions`（观点数）

### 1.3 展开成「一行一个观点」（核心查询）

**业务**：明细表、导出、任何按资产/机构/方向的聚合都基于这条。

**参数**：`week`

```sql
SELECT w.week, jt.institution, op.category, op.asset, op.direction
FROM weekly_analyzer_data w,
     JSON_TABLE(JSON_EXTRACT(w.data_json,'$.views'), '$[*]' COLUMNS(
         institution VARCHAR(64) PATH '$.institution',
         opinions    JSON        PATH '$.opinions')) jt,
     JSON_TABLE(jt.opinions, '$[*]' COLUMNS(
         category  VARCHAR(32) PATH '$.category',
         asset     VARCHAR(64) PATH '$.asset',
         direction VARCHAR(16) PATH '$.direction')) op
WHERE w.week = '20260928-20261004';
```

**返回**：`week`、`institution`、`category`、`asset`、`direction`

### 1.4 只看宏观指标类观点（「宏观」分页口径）

**参数**：`week`

```sql
SELECT w.week, jt.institution, op.asset, op.direction
FROM weekly_analyzer_data w,
     JSON_TABLE(JSON_EXTRACT(w.data_json,'$.views'),'$[*]' COLUMNS(
         institution VARCHAR(64) PATH '$.institution',
         opinions    JSON        PATH '$.opinions')) jt,
     JSON_TABLE(jt.opinions,'$[*]' COLUMNS(
         category  VARCHAR(32) PATH '$.category',
         asset     VARCHAR(64) PATH '$.asset',
         direction VARCHAR(16) PATH '$.direction')) op
WHERE w.week = '20260928-20261004'
  AND op.category IN ('宏观指标','宏观');
```

> 注意区分：这里过滤的是**观点**里的宏观指标（机构怎么看美国CPI），
> 与第二章的**宏观预测表**（机构预测值 vs 共识 vs 实际值）是两回事。

### 1.5 某周 机构 × 方向 计数

**参数**：`week`

```sql
SELECT jt.institution, op.direction, COUNT(*) AS n
FROM weekly_analyzer_data w,
     JSON_TABLE(JSON_EXTRACT(w.data_json,'$.views'),'$[*]' COLUMNS(
         institution VARCHAR(64) PATH '$.institution',
         opinions    JSON        PATH '$.opinions')) jt,
     JSON_TABLE(jt.opinions,'$[*]' COLUMNS(direction VARCHAR(16) PATH '$.direction')) op
WHERE w.week = '20260928-20261004'
GROUP BY jt.institution, op.direction
ORDER BY jt.institution, op.direction;
```

**返回**：`institution`、`direction`、`n`

### 1.6 某周 资产 × 方向分布（看共识 / 分歧）

**业务**：共识与分歧榜——某资产看涨 N 家、看跌 M 家。

**参数**：`week`

```sql
SELECT op.asset,
       SUM(op.direction='看涨') AS bull,
       SUM(op.direction='看跌') AS bear,
       SUM(op.direction='中性') AS neutral
FROM weekly_analyzer_data w,
     JSON_TABLE(JSON_EXTRACT(w.data_json,'$.views'),'$[*]' COLUMNS(opinions JSON PATH '$.opinions')) jt,
     JSON_TABLE(jt.opinions,'$[*]' COLUMNS(
         asset VARCHAR(64) PATH '$.asset', direction VARCHAR(16) PATH '$.direction')) op
WHERE w.week = '20260928-20261004'
GROUP BY op.asset
ORDER BY (bull+bear) DESC;
```

**返回**：`asset`、`bull`（看涨家数）、`bear`（看跌家数）、`neutral`（中性家数）

> `bull` 与 `bear` 同时 > 0 即「有分歧」；某机构的**内部分歧**另有 `opinions[].note` 字段标注。

### 1.7 观点原文出处（可追溯到研报标题 + 段落）

**参数**：`week`、`asset`

```sql
SELECT op.asset, op.direction, s.report, LEFT(s.passage, 120) AS passage_head
FROM weekly_analyzer_data w,
     JSON_TABLE(JSON_EXTRACT(w.data_json,'$.views'),'$[*]' COLUMNS(opinions JSON PATH '$.opinions')) jt,
     JSON_TABLE(jt.opinions,'$[*]' COLUMNS(
         asset     VARCHAR(64) PATH '$.asset',
         direction VARCHAR(16) PATH '$.direction',
         sources   JSON        PATH '$.sources')) op,
     JSON_TABLE(op.sources,'$[*]' COLUMNS(
         report  VARCHAR(128) PATH '$.report',
         passage VARCHAR(512) PATH '$.passage')) s
WHERE w.week = '20260928-20261004' AND op.asset = '美元指数(DXY)';
```

**返回**：`asset`、`direction`、`report`、`passage_head`（`sources` 里还有 `rationale` 判定理由、`date`）

> 一个「观点」由多篇研报支撑时，`sources` 是数组；**一条观点有 N 个 source，展开就会变成 N 行**。

### 1.8 某资产跨周方向 + 现价

**业务**：资产观点的时间序列（方向变化 + 当时价格锚点）。

```sql
SELECT DISTINCT w.week, op.direction, op.price, op.price_date
FROM weekly_analyzer_data w,
     JSON_TABLE(JSON_EXTRACT(w.data_json,'$.views'),'$[*]' COLUMNS(opinions JSON PATH '$.opinions')) jt,
     JSON_TABLE(jt.opinions,'$[*]' COLUMNS(
         asset      VARCHAR(64) PATH '$.asset',
         direction  VARCHAR(16) PATH '$.direction',
         price      VARCHAR(24) PATH '$.price',
         price_date VARCHAR(24) PATH '$.price_date')) op
WHERE op.asset = '美元指数(DXY)' AND w.week <> 'test'
ORDER BY w.week DESC;
```

**返回**：`week`、`direction`、`price`、`price_date`

> `price` 是**当时价格锚点**，只表示「分析时该资产的水平」，**不可用它和 `direction` 推导涨跌幅**（业务约定）。

### 1.9 周度综述正文

```sql
SELECT week, market_overview
FROM weekly_analyzer_data
WHERE week = '20260928-20261004';
```

---

## 二、宏观预测表

**表名**：`weekly_macro_data`（一周一行）

| 字段 | 含义 |
|---|---|
| `week` | 周标签，主键 |
| `macro_json` | 宏观正文 JSON：`{"as_of":..., "items":[...]}` |
| `as_of` | 宏观数据生成时间（冗余列，便于列表直显） |
| `indicator_count` | 指标行数（冗余列，便于巡检） |
| `created_at` / `updated_at` | 首次写入 / 最近更新 |

`macro_json` 结构：

```json
{
  "as_of": "2026-09-30T12:55:21",
  "items": [
    {
      "indicator": "美国CPI(同比)",
      "category": "通胀",                    // 通胀 / 增长 / 就业 / 消费 / 货币政策
      "release_date": "2026-08-12 20:30",    // 或 "已更新"
      "period": "2026-07",
      "prior": 3.3,                          // 上期值
      "consensus": 3.4,                      // 共识（市场一致预期）
      "actual": 3.4,                         // 实际值
      "unit": "%",
      "forecasts": {"高盛": 3.4, "野村": 3.34, "中信证券": 3.4},   // 机构预测，**值可能是文字**
      "source": "fred+csv"
    }
  ]
}
```

> `forecasts` 的 key 是机构名，value 通常是数值，**少数是文字**——例如
> `美联储下次加息预期`：`{"汇丰":"12月","瑞银":"12月","花旗":"不动","高盛":"10月",...}`。
> 文字值属于正常数据，处理方式见 2.6 / 2.7。

### 2.1 有宏观数据的周（倒序）

```sql
SELECT week, as_of, indicator_count
FROM weekly_macro_data
ORDER BY week DESC;
```

**返回**：`week`、`as_of`、`indicator_count`

### 2.2 展开成「一行一个指标」

**业务**：宏观表主体。`bank_avg`（机构均值）见 2.6 / 2.7。

**参数**：`week`

```sql
SELECT m.week, jt.indicator, jt.category, jt.release_date, jt.period,
       jt.prior, jt.consensus, jt.actual, jt.unit
FROM weekly_macro_data m,
     JSON_TABLE(m.macro_json,'$.items[*]' COLUMNS(
         indicator    VARCHAR(64) PATH '$.indicator',
         category     VARCHAR(32) PATH '$.category',
         release_date VARCHAR(32) PATH '$.release_date',
         period       VARCHAR(16) PATH '$.period',
         prior        VARCHAR(16) PATH '$.prior',
         consensus    VARCHAR(16) PATH '$.consensus',
         actual       VARCHAR(16) PATH '$.actual',
         unit         VARCHAR(16) PATH '$.unit')) jt
WHERE m.week = '20260928-20261004';
```

**返回**：与页面列一一对应；`prior/consensus/actual` 取出是**字符串**，要比大小请 `CAST(... AS DECIMAL)`。

> `release_date` 出现 `已更新` 表示该指标的实际值已回填（数据发布后重跑过），
> 此时 `actual` 与 `consensus` 通常已能对比；尚未发布时 `actual` 为空。

### 2.3 展开成「一行一个机构预测」（机构 × 指标）

**业务**：宏观表的机构列。

**参数**：`week`

```sql
SELECT m.week, jt.indicator, kj.inst,
       JSON_UNQUOTE(JSON_EXTRACT(jt.forecasts_json, CONCAT('$."', kj.inst, '"'))) AS val
FROM weekly_macro_data m,
     JSON_TABLE(m.macro_json,'$.items[*]' COLUMNS(
         indicator      VARCHAR(64) PATH '$.indicator',
         forecasts_json JSON        PATH '$.forecasts')) jt,
     JSON_TABLE(JSON_KEYS(jt.forecasts_json),'$[*]' COLUMNS(inst VARCHAR(64) PATH '$')) kj
WHERE m.week = '20260928-20261004';
```

> `forecasts` 的 key 是动态的（机构数量、名称每期都可能变），
> 所以必须 `JSON_KEYS()` → `JSON_TABLE` → 按 key 回取，**不能写死机构名**。

### 2.4 某指标跨周走势

**业务**：某个指标（共识 / 实际）的历史对比。

```sql
SELECT m.week, jt.period, jt.consensus, jt.actual
FROM weekly_macro_data m,
     JSON_TABLE(m.macro_json,'$.items[*]' COLUMNS(
         indicator VARCHAR(64) PATH '$.indicator',
         period    VARCHAR(16) PATH '$.period',
         consensus VARCHAR(16) PATH '$.consensus',
         actual    VARCHAR(16) PATH '$.actual')) jt
WHERE jt.indicator = '美国CPI(同比)'
ORDER BY m.week;
```

### 2.5 某周每个指标的机构覆盖数

**业务**：看某行有几家机构给了预测（页面「覆盖 X/Y」）。

```sql
SELECT jt.indicator,
       COALESCE(JSON_LENGTH(JSON_EXTRACT(jt.forecasts_json,'$')),0) AS filled,
       jt.consensus, jt.actual
FROM weekly_macro_data m,
     JSON_TABLE(m.macro_json,'$.items[*]' COLUMNS(
         indicator      VARCHAR(64) PATH '$.indicator',
         consensus      VARCHAR(16) PATH '$.consensus',
         actual         VARCHAR(16) PATH '$.actual',
         forecasts_json JSON        PATH '$.forecasts')) jt
WHERE m.week = '20260928-20261004'
ORDER BY filled DESC;
```

### 2.6 数值行：机构算术平均（⚠️ 只算数值）

**业务**：页面「机构均值」列——**数值行**取算术平均。

```sql
SELECT jt.indicator,
       ROUND(AVG(CAST(JSON_UNQUOTE(JSON_EXTRACT(jt.forecasts_json, CONCAT('$."', kj.inst, '"')))
                          AS DECIMAL(14,4))), 2) AS bank_avg_num,
       COUNT(*) AS n_num, jt.consensus, jt.actual
FROM weekly_macro_data m,
     JSON_TABLE(m.macro_json,'$.items[*]' COLUMNS(
         indicator VARCHAR(64) PATH '$.indicator',
         consensus VARCHAR(16) PATH '$.consensus',
         actual    VARCHAR(16) PATH '$.actual',
         forecasts_json JSON PATH '$.forecasts')) jt,
     JSON_TABLE(JSON_KEYS(jt.forecasts_json),'$[*]' COLUMNS(inst VARCHAR(64) PATH '$')) kj
WHERE m.week = '20260928-20261004'
  AND JSON_TYPE(JSON_EXTRACT(jt.forecasts_json, CONCAT('$."', kj.inst, '"'))) IN ('INTEGER','DOUBLE')
GROUP BY jt.indicator, jt.consensus, jt.actual
ORDER BY n_num DESC;
```

**返回**：`indicator`、`bank_avg_num`、`n_num`、`consensus`、`actual`

> `JSON_TYPE(...) IN ('INTEGER','DOUBLE')` 这个条件是**必需的**：机构值里混有文字（见下条），
> 不加过滤会把文字当数字处理导致报错。

### 2.7 文字行：机构口径取众数

**业务**：`美联储下次加息预期` 这类行的机构值形如 `12月`/`10月`/`不动`，不能求平均，
取**众数**（多数机构的口径）。

```sql
SELECT jt.indicator,
       JSON_UNQUOTE(JSON_EXTRACT(jt.forecasts_json, CONCAT('$."', kj.inst, '"'))) AS val,
       COUNT(*) AS n
FROM weekly_macro_data m,
     JSON_TABLE(m.macro_json,'$.items[*]' COLUMNS(
         indicator VARCHAR(64) PATH '$.indicator', forecasts_json JSON PATH '$.forecasts')) jt,
     JSON_TABLE(JSON_KEYS(jt.forecasts_json),'$[*]' COLUMNS(inst VARCHAR(64) PATH '$')) kj
WHERE m.week = '20260928-20261004'
  AND jt.indicator = '美联储下次加息预期'
  AND JSON_TYPE(JSON_EXTRACT(jt.forecasts_json, CONCAT('$."', kj.inst, '"'))) = 'STRING'
GROUP BY jt.indicator, val
ORDER BY n DESC;
```

**返回**：`val`（机构口径文字）、`n`（家数）；取 `n` 最大的那条即众数（示例：`12月`）

---

## 三、场景 → SQL 对照

按「用户会问的问题」列出取数用哪条 SQL（不涉及代码，仅供查阅）。

### 观点矩阵

| 场景（用户会问什么） | 对应 SQL |
|---|---|
| 有哪些周的研报观点？ | 1.1 |
| 这周覆盖多少家机构、多少条观点？顺便看周度综述 | 1.2 + 1.9 |
| 这周各家机构的全部观点明细 | 1.3 |
| 这周各家怎么看宏观指标（CPI/非农/加息…） | 1.4 |
| 某家机构这周的观点、看多看空各几条 | 1.5 |
| 这周各资产的共识与分歧（几家看涨几家看跌） | 1.6 |
| 这个观点的依据出自哪篇研报、原文怎么写的 | 1.7 |
| 某资产的方向这几周怎么变、当时什么价位 | 1.8 |

### 宏观预测表

| 场景（用户会问什么） | 对应 SQL |
|---|---|
| 有哪些周有宏观预测数据？ | 2.1 |
| 某周宏观表全貌（指标、上期、共识、实际、单位） | 2.2 |
| 某周机构 × 指标预测矩阵（每家预测多少） | 2.3 |
| 某指标这几周的共识与实际怎么变 | 2.4 |
| 某周每个指标有几家机构给了预测 | 2.5 |
| 某周机构预测的均值（数值行平均 / 文字行众数） | 2.6 / 2.7 |
