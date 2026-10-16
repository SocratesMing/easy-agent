# 观点矩阵 / 美国宏观资产研判 的 SQL 参考（供 MCP 接入）

本文档整理「观点矩阵」与「美国宏观资产研判」两个模块用到的**全部 SQL**：
每条给出用途（业务逻辑）、参数、可直接执行的 SQL、返回字段。

两个模块的数据来自同一次研报分析流程，但**分别存放在两张互不关联的表**：
观点矩阵 = `weekly_analyzer_data`，美国宏观资产研判 = `weekly_macro_data`（各自以 `week` 为唯一键，一周一行）。

> ### 本版要点
> 1. **面向 MySQL 5.7 编写**：不使用 `JSON_TABLE`（8.0 才有，5.7 上直接语法报错），数组展开统一用「序号表 seq」。
> 2. 模块名：原「宏观预测表」→ **「美国宏观资产研判」**。**只是名字**：表名 `weekly_macro_data`、字段、数据都没变。
> 3. 「观点矩阵」**只统计资产类观点**：`category` 为 `宏观指标`（历史还有 `宏观`）的观点一律排除。
>    宏观类观点仍在库里，只是本模块不返回（页面头部「M 条观点」之类的数字要按同一口径）。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 库名 | `research_analyzer`（连接地址 / 账号按目标服务器配置） |
| 语法边界 | 只用 MySQL 5.7 已有的：`JSON_EXTRACT` / `JSON_UNQUOTE` / `JSON_KEYS` / `JSON_LENGTH` / `JSON_TYPE` / `NULLIF` / 派生表。**不用** `JSON_TABLE`、递归 CTE（`WITH`）、窗口函数、`LATERAL` |
| 周标签 | `YYYYMMDD-YYYYMMDD` 字符串，周一到周日，如 `20260928-20261004`；两表都用它作唯一键 |
| 观点方向 | `看涨` / `看跌` / `中性`（中文枚举） |
| 观点矩阵口径 | 只含资产类观点，`category` 取值 `境内利率` / `境外利率` / `外汇` / `全球股指` / `大宗`；**`宏观指标`（历史还有 `宏观`）一律排除** |
| 宏观预测值 | 多数是**数值**，少数是**文字**（如 `12月` / `10月` / `不动`）；处理方式见 2.6 / 2.7 |
| 单位 | 由 `unit` 给出：`%` / `千人` / `次` / `bp` / `时点` |
| 资产范围 | 固定白名单，六大类：境内利率 / 境外利率 / 外汇 / 全球股指 / 大宗 / 宏观指标；观点矩阵只涉及前五类。**SQL 里不排序**，展示顺序由应用层控制 |
| JSON null | JSON 里的 `null` 被 `JSON_UNQUOTE` 解出来是**字符串 `'null'`**（不是 SQL NULL）→ 所有可空字段统一套 `NULLIF(..., 'null')` |
| 性能 | 展开前先把子数组放进带 `LIMIT 1`（单周）或 `DISTINCT`（跨周）的派生表，**这是强制物化**：整份 JSON 只解析一次。不要当冗余优化删掉 |
| 写入方式 | 研报分析流程按周写入，一周一行，重复跑同一周为覆盖更新 |
| 查询权限 | 下列 SQL 全是 SELECT，只读账号即可 |

---

## 零、共享块（整套 SQL 里唯一重复的代码）

整套查询只有 4 处「展开 JSON 数组」的代码。抽出来放在这里，
下面各条 SQL 用 `{{SEQ}}` / `{{V}}` / `{{M}}` / `{{B}}` 引用它们——
**实现时建议在服务端把这几块定义成 SQL 常量，拼进去就是完整 SQL**（也便于将来只改一处）。

### 0.1 序号表 `{{SEQ}}`（0–39 的下标）

```sql
(SELECT a.i + b.i*10 AS i
      FROM (SELECT 0 i UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a,
           (SELECT 0 i UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
            UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b
      WHERE a.i + b.i*10 < 40)
```

用途：把 JSON 数组按下标逐行取出来，例如 `{{SEQ}} sv` + `sv.i < JSON_LENGTH(数组)` + `JSON_EXTRACT(数组, CONCAT('$[', sv.i, ']'))`。
它是一个**派生表片段**（用的时候自己带括号，`… FROM {{SEQ}} sv`），不是完整语句；不用建表、不用写权限。
上限取 40，够用两个模块的所有数组；**超过上限不会报错，只会少行** → 见 §0.6 自检。

### 0.2 观点展开块 `{{V}}`（一行一个观点，已排除宏观类）

```sql
SELECT v.week, v.institution,
       JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].category')))  AS category,
       JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].asset')))     AS asset,
       JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].direction'))) AS direction,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].price'))), 'null')      AS price,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].price_date'))), 'null') AS price_date
FROM (
  SELECT w.week,
         JSON_UNQUOTE(JSON_EXTRACT(w.views_json, CONCAT('$[',sv.i,'].institution'))) AS institution,
         JSON_EXTRACT(w.views_json, CONCAT('$[',sv.i,'].opinions'))                  AS ops_json
  FROM (SELECT week, JSON_EXTRACT(data_json,'$.views') AS views_json FROM weekly_analyzer_data WHERE week = '20260928-20261004' LIMIT 1) w, {{SEQ}} sv
  WHERE sv.i < JSON_LENGTH(w.views_json)
) v, {{SEQ}} so
WHERE so.i < JSON_LENGTH(v.ops_json)
  AND JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].category'))) NOT IN ('宏观指标','宏观')
```

**参数**：`week`（示例 `20260928-20261004`）。**返回**：`week` / `institution` / `category` / `asset` / `direction` / `price`（可空）/ `price_date`（可空）。

### 0.3 宏观指标展开块 `{{M}}`（一行一个指标）

```sql
SELECT m.week,
       JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].indicator')))    AS indicator,
       JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].category')))     AS category,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].release_date'))), 'null') AS release_date,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].period'))), 'null')       AS period,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].prior'))), 'null')        AS prior,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].consensus'))), 'null')    AS consensus,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].actual'))), 'null')       AS actual,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].unit'))), 'null')         AS unit,
       JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].forecasts'))                  AS forecasts
FROM (SELECT week, JSON_EXTRACT(macro_json,'$.items') AS items_json FROM weekly_macro_data WHERE week = '20260928-20261004' LIMIT 1) m, {{SEQ}} si
WHERE si.i < JSON_LENGTH(m.items_json)
```

**参数**：`week`。**返回**：`week` / `indicator` / `category` / `release_date` / `period` / `prior` / `consensus` / `actual` / `unit` / `forecasts`（机构值 JSON 对象）。

### 0.4 宏观机构值展开块 `{{B}}`（一行一个机构预测）

```sql
SELECT b.week, b.indicator, b.consensus, b.actual, b.inst,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(b.forecasts, CONCAT('$."', b.inst, '"'))), 'null') AS val,
       JSON_TYPE(JSON_EXTRACT(b.forecasts, CONCAT('$."', b.inst, '"')))                    AS jtype
FROM (
  SELECT m.week,
         JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].indicator'))) AS indicator,
         NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].consensus'))), 'null') AS consensus,
         NULLIF(JSON_UNQUOTE(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].actual'))), 'null')    AS actual,
         JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].forecasts'))               AS forecasts,
         JSON_UNQUOTE(JSON_EXTRACT(JSON_KEYS(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].forecasts'))),
                                   CONCAT('$[',sk.i,']')))                         AS inst
  FROM (SELECT week, JSON_EXTRACT(macro_json,'$.items') AS items_json FROM weekly_macro_data WHERE week = '20260928-20261004' LIMIT 1) m, {{SEQ}} si, {{SEQ}} sk
  WHERE si.i < JSON_LENGTH(m.items_json)
    AND sk.i < COALESCE(JSON_LENGTH(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].forecasts'))), 0)
) b
```

**参数**：`week`。**返回**：`week` / `indicator` / `consensus` / `actual` / `inst`（机构）/ `val`（该机构预测值，可能是数值也可能是文字）/ `jtype`（该值的 JSON 类型）。

### 0.5 跨周用哪个版本

`{{V}}` 和 `{{M}}` 是**单周**版（`… WHERE week = '…' LIMIT 1`）。跨周查询（1.7 / 2.4）把这层子查询换成：

```sql
SELECT DISTINCT week, JSON_EXTRACT(data_json,'$.views') AS views_json FROM weekly_analyzer_data
```

`DISTINCT` 是**强制物化**开关（见「通用约定 · 性能」），别删。换好之后就是把 `{{V_ALLWK}}` / `{{M_ALLWK}}` 当 `{{V}}` / `{{M}}` 用，只是不能再按 `week` 过滤。

### 0.6 序号表上限自检（数据有新增周后跑一次）

```sql
SELECT 'views' AS arr, MAX(JSON_LENGTH(v.views_json)) AS max_len
FROM (SELECT DISTINCT week, JSON_EXTRACT(data_json,'$.views') AS views_json FROM weekly_analyzer_data) v
UNION ALL
SELECT 'opinions', MAX(JSON_LENGTH(JSON_EXTRACT(v.views_json, CONCAT('$[',sv.i,'].opinions'))))
FROM (SELECT DISTINCT week, JSON_EXTRACT(data_json,'$.views') AS views_json FROM weekly_analyzer_data) v, {{SEQ}} sv
WHERE sv.i < JSON_LENGTH(v.views_json)
UNION ALL
SELECT 'sources', MAX(JSON_LENGTH(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].sources'))))
FROM (SELECT DISTINCT week, JSON_EXTRACT(w.views_json, CONCAT('$[',sv.i,'].opinions')) AS ops_json
      FROM (SELECT DISTINCT week, JSON_EXTRACT(data_json,'$.views') AS views_json FROM weekly_analyzer_data) w, {{SEQ}} sv
      WHERE sv.i < JSON_LENGTH(w.views_json)) v, {{SEQ}} so
WHERE so.i < JSON_LENGTH(v.ops_json)
UNION ALL
SELECT 'items', MAX(JSON_LENGTH(JSON_EXTRACT(macro_json,'$.items'))) FROM weekly_macro_data
UNION ALL
SELECT 'forecasts', MAX(JSON_LENGTH(JSON_EXTRACT(m.items_json, CONCAT('$[',si.i,'].forecasts'))))
FROM (SELECT DISTINCT week, JSON_EXTRACT(macro_json,'$.items') AS items_json FROM weekly_macro_data) m, {{SEQ}} si
WHERE si.i < JSON_LENGTH(m.items_json);
```

**返回**：`arr`（数组名）、`max_len`（该数组当前最长元素个数）。任何一个 ≥ 40 就要把序号表上限改大（同时改 `{{SEQ}}` 里的 `< 40` 和交叉连接位数）。

---

## 一、观点矩阵

**表名**：`weekly_analyzer_data`（一周一行）

| 字段 | 含义 |
|---|---|
| `week` | 周标签，主键，如 `20260928-20261004` |
| `data_json` | 观点正文 JSON：`{"views":[{"institution":…,"opinions":[…]}]}` |
| `market_overview` | 周度综述 Markdown 正文 |
| `created_at` | 写入时间（覆盖写时会被刷新，语义是「最后一次写入时间」） |

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
          "price": 3945.4003,        // 该资产当时价格锚点（可选）
          "price_date": "2026-09-04", // 可选
          "price_change": -0.75,     // 可选
          "note": "内部分歧：2篇看涨 vs 3篇看跌"   // 可选，仅当同一机构内部分歧时出现
        }
      ]
    }
  ]
}
```

> 三条口径注意：
> - `category` 只会有 `境内利率` / `境外利率` / `外汇` / `全球股指` / `大宗` 五类；**宏观类观点已被排除**。
> - `price` / `price_date` / `note` 是**可选的**：JSON 里没有这个键时取出来是 NULL（不是 0，也不是空串）。
> - `sources` 是数组：一条观点可能由多篇研报支撑，展开时会变成多行。

### 1.1 全部周（倒序）

**业务**：周选择器的可选范围。

```sql
SELECT week FROM weekly_analyzer_data ORDER BY week DESC;
```

### 1.2 某周 机构数 / 观点数（⚠️ 不含宏观）

**业务**：页面头部「覆盖 N 家机构 / M 条观点」。

**参数**：`week`

```sql
SELECT x.week,
       COUNT(DISTINCT x.institution) AS institutions,
       COUNT(*)                      AS opinions
FROM (
{{V}}
) x
GROUP BY x.week;
```

**返回**：`week`、`institutions`（有几家机构给了资产类观点）、`opinions`（观点条数，**已排除宏观类**）

> ⚠️ **口径注意**：这条排除宏观类观点，所以数字会比库里 `data_json` 的观点总数小。
> 若要「含宏观」的总数，把那行 `WHERE … NOT IN ('宏观指标','宏观')` 去掉即可。
> 某周一条非宏观观点都没有时，这条返回 **0 行**（不是一行 0），调用方要判空。

### 1.3 展开成「一行一个观点」（核心查询）

**业务**：明细表、导出，以及 1.4–1.7 的基础（后面几条都是在它之上加 `GROUP BY` / `WHERE`）。

**参数**：`week`

```sql
{{V}};
```

### 1.4 某周 机构 × 方向 计数

**参数**：`week`

```sql
SELECT x.institution, x.direction, COUNT(*) AS n
FROM (
{{V}}
) x
GROUP BY x.institution, x.direction
ORDER BY x.institution, x.direction;
```

**返回**：`institution`、`direction`、`n`

### 1.5 某周 资产 × 方向分布（看共识 / 分歧）

**业务**：共识与分歧榜——某资产看涨 N 家、看跌 M 家。

**参数**：`week`

```sql
SELECT x.asset,
       SUM(x.direction = '看涨') AS bull,
       SUM(x.direction = '看跌') AS bear,
       SUM(x.direction = '中性') AS neutral
FROM (
{{V}}
) x
GROUP BY x.asset
ORDER BY (bull + bear) DESC, x.asset;
```

**返回**：`asset`、`bull`（看涨家数）、`bear`（看跌家数）、`neutral`（中性家数）

> `bull` 与 `bear` 同时 > 0 即「有分歧」；某机构的**内部分歧**另有 `opinions[].note` 字段标注。

### 1.6 观点原文出处（可追溯到研报标题 + 段落）

**参数**：`week`、`asset`（示例 `美元指数(DXY)`）

```sql
SELECT y.asset, y.direction, y.report, LEFT(y.passage, 120) AS passage_head
FROM (
  SELECT JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].category')))  AS category,
         JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].asset')))     AS asset,
         JSON_UNQUOTE(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].direction'))) AS direction,
         JSON_UNQUOTE(JSON_EXTRACT(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].sources')), CONCAT('$[',s.i,'].report')))  AS report,
         JSON_UNQUOTE(JSON_EXTRACT(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].sources')), CONCAT('$[',s.i,'].passage'))) AS passage
  FROM (
    SELECT w.week, JSON_EXTRACT(w.views_json, CONCAT('$[',sv.i,'].opinions')) AS ops_json
    FROM (SELECT week, JSON_EXTRACT(data_json,'$.views') AS views_json FROM weekly_analyzer_data WHERE week = '20260928-20261004' LIMIT 1) w, {{SEQ}} sv
    WHERE sv.i < JSON_LENGTH(w.views_json)
  ) v, {{SEQ}} so, {{SEQ}} s
  WHERE so.i < JSON_LENGTH(v.ops_json)
    AND s.i < COALESCE(JSON_LENGTH(JSON_EXTRACT(v.ops_json, CONCAT('$[',so.i,'].sources'))), 0)
) y
WHERE y.category NOT IN ('宏观指标','宏观')
  AND y.asset = '美元指数(DXY)';
```

**返回**：`asset`、`direction`、`report`、`passage_head`（原文前 120 字；`sources` 里还有 `rationale` 判定理由、`date`）

> 这条比别的多一层（`views → opinions → sources`），所以自带展开代码。
> 一条观点有 N 个 `sources` 就展开成 N 行（**展开行数 ≠ 观点数**）；返回给模型时建议只取首条 + 计数。
> 换资产：把 `y.asset = '美元指数(DXY)'` 换成目标资产名（名字必须与白名单完全一致）。

### 1.7 某资产跨周方向 + 现价

**业务**：资产观点的时间序列（方向变化 + 当时价格锚点）。

**参数**：`asset`（示例 `美元指数(DXY)`）；用**跨周版** `{{V_ALLWK}}`（见 §0.5）

```sql
SELECT DISTINCT x.week, x.direction, x.price, x.price_date
FROM (
{{V_ALLWK}}
) x
WHERE x.asset = '美元指数(DXY)'
ORDER BY x.week DESC;
```

**返回**：`week`、`direction`、`price`、`price_date`

### 1.8 周度综述正文

```sql
SELECT week, market_overview FROM weekly_analyzer_data WHERE week = '20260928-20261004';
```

**返回**：`week`、`market_overview`（Markdown 正文）

---

## 二、美国宏观资产研判

**表名**：`weekly_macro_data`（一周一行）

| 字段 | 含义 |
|---|---|
| `week` | 周标签，主键 |
| `macro_json` | 宏观正文 JSON：`{"as_of":…, "items":[…]}` |
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
      "release_date": "已更新",               // 或 "9/30 20:30公布" 这类待公布时间
      "period": "2026-07",
      "prior": 3.3,                          // 上期值（可能是 null）
      "consensus": 3.4,                      // 共识（彭博 / 市场一致预期）
      "actual": 3.4,                         // 实际值（未发布时为 null）
      "unit": "%",
      "forecasts": {"高盛": 3.4, "野村": 3.34, "中信证券": 3.4},   // 机构预测，**值可能是文字**
      "source": "fred+csv"
    }
  ]
}
```

> `forecasts` 的 key 是机构名，value 通常是数值，**少数是文字**——例如
> `美联储下次加息预期`：`{"汇丰":"12月","瑞银":"12月","花旗":"不动","高盛":"10月",…}`。文字值属正常数据，见 2.6 / 2.7。
>
> **取宏观一律用这张表**：`weekly_analyzer_data.data_json` 里还留着一份 `macro_forecasts`（历史兼容字段），不要读它。

### 2.1 有宏观数据的周（倒序）

```sql
SELECT week, as_of, indicator_count FROM weekly_macro_data ORDER BY week DESC;
```

**返回**：`week`、`as_of`、`indicator_count`

### 2.2 展开成「一行一个指标」

**业务**：宏观表主体（就是 §0.3 的 `{{M}}`）。

**参数**：`week`

```sql
{{M}};
```

### 2.3 展开成「一行一个机构预测」（机构 × 指标）

**业务**：宏观表的机构列（就是 §0.4 的 `{{B}}`，加个排序）。

**参数**：`week`

```sql
{{B}}
ORDER BY b.indicator, b.inst;
```

> `forecasts` 的 key（机构名）每期都会变，不能写死：用 `JSON_KEYS()` 取 key 数组，再用序号表按下标取名字，最后按 `'$."' + 机构名 + '"'` 回取数值。
> `val` 是文本，要比较大小请 `CAST(x.val AS DECIMAL(14,4))`。

### 2.4 某指标跨周走势

**业务**：某个指标（共识 / 实际）的历史对比。用**跨周版** `{{M_ALLWK}}`（见 §0.5）。

```sql
SELECT x.week, x.period, x.consensus, x.actual
FROM (
{{M_ALLWK}}
) x
WHERE x.indicator = '美国CPI(同比)'
ORDER BY x.week;
```

### 2.5 某周每个指标的机构覆盖数

**业务**：看某行有几家机构给了预测（页面「覆盖 X/Y」）。

**参数**：`week`

```sql
SELECT x.indicator,
       COALESCE(JSON_LENGTH(x.forecasts), 0) AS filled,
       x.consensus, x.actual
FROM (
{{M}}
) x
ORDER BY filled DESC, x.indicator;
```

**返回**：`indicator`、`filled`（填报机构数）、`consensus`、`actual`

### 2.6 数值行：机构算术平均（⚠️ 只算数值）

**业务**：页面「机构均值」列——**数值行**取算术平均。

**参数**：`week`

```sql
SELECT x.indicator,
       ROUND(AVG(CAST(x.val AS DECIMAL(14,4))), 2) AS bank_avg_num,
       COUNT(*) AS n_num, x.consensus, x.actual
FROM (
{{B}}
) x
WHERE x.jtype IN ('INTEGER','DOUBLE')
GROUP BY x.indicator, x.consensus, x.actual
ORDER BY n_num DESC, x.indicator;
```

**返回**：`indicator`、`bank_avg_num`、`n_num`（参与平均的家数）、`consensus`、`actual`

> `jtype IN ('INTEGER','DOUBLE')` 是**必需**条件：机构值里混有文字（见 2.7），不过滤会把文字当数字算，
> `CAST` 后得到 0 或直接报错。判类型用 `JSON_TYPE()`（比正则干净，也对应后端 `is_text_row` 的判据）。

### 2.7 文字行：机构口径取众数

**业务**：`美联储下次加息预期` 这类行的机构值形如 `12月` / `10月` / `不动`，不能求平均，取**众数**（多数机构的口径）。

```sql
SELECT x.indicator, x.val, COUNT(*) AS n
FROM (
{{B}}
) x
WHERE x.indicator = '美联储下次加息预期'
  AND x.jtype = 'STRING'
GROUP BY x.indicator, x.val
ORDER BY n DESC;
```

**返回**：`val`（机构口径文字）、`n`（家数）；取 `n` 最大的那条即众数

> ⚠️ 别在 SQL / 应用层做 `replace(/[^\d.\-]/g,'')` 把 `12月` 当 12 算：会产生假均值 / 假偏差，后端 `sum(int)+str` 还会直接报错让整表 500。

---

## 三、场景 → SQL 对照

按「用户会问的问题」列出取数用哪条 SQL（不涉及代码，仅供查阅）。

### 观点矩阵（只含资产类观点）

| 场景（用户会问什么） | 对应 SQL |
|---|---|
| 有哪些周的研报观点？ | 1.1 |
| 这周覆盖多少家机构、多少条观点？顺便看周度综述 | 1.2 + 1.8 |
| 这周各家机构的全部观点明细 | 1.3 |
| 某家机构这周的观点、看多看空各几条 | 1.4 |
| 这周各资产的共识与分歧（几家看涨几家看跌） | 1.5 |
| 这个观点的依据出自哪篇研报、原文怎么写的 | 1.6 |
| 某资产的方向这几周怎么变、当时什么价位 | 1.7 |

### 美国宏观资产研判

| 场景（用户会问什么） | 对应 SQL |
|---|---|
| 有哪些周有宏观数据？ | 2.1 |
| 某周宏观表全貌（指标、上期、共识、实际、单位） | 2.2 |
| 某周机构 × 指标预测矩阵（每家预测多少） | 2.3 |
| 某指标这几周的共识与实际怎么变 | 2.4 |
| 某周每个指标有几家机构给了预测 | 2.5 |
| 某周机构预测的均值（数值行平均 / 文字行众数） | 2.6 / 2.7 |

---

## 四、口径与常见问题

| # | 坑 | 说明 · 正确做法 |
|---|---|---|
| 1 | **MySQL 5.7 没有 `JSON_TABLE`** | 5.7 上直接语法报错（1064）。本文档所有数组展开都用「序号表 seq + `CONCAT` 拼下标」；`JSON_KEYS` / `JSON_LENGTH` / `JSON_TYPE` / `JSON_UNQUOTE` 5.7 都有 |
| 2 | **JSON 里的 `null` 会变成字符串 `'null'`** | `JSON_UNQUOTE(JSON_EXTRACT(doc,'$.actual'))` 在值是 `null` 时返回 4 个字符的 `'null'`（不是 SQL NULL）→ 可空字段都要 `NULLIF(..., 'null')` |
| 3 | **`LIMIT 1` / `DISTINCT` 是「强制物化」开关** | 先把子数组抽进派生表，整份 JSON 才只解析一次；删掉它们会让跨周查询慢两个数量级 |
| 4 | **序号表上限 40 是硬上限** | 数组比 40 长时**不会报错，只会静默少行**；有新增周后跑 §0.6 自检，要调大就同时改 `< 40` 和交叉连接位数 |
| 5 | `prior` / `consensus` / `actual` 是**字符串** | JSON 数字取出来是文本，要比大小先 `CAST(... AS DECIMAL(14,4))` |
| 6 | `sources` 一对多 | 展开行数 ≠ 观点数；返回给模型时只取首条 + 计数，或按观点聚合 |
| 7 | **观点矩阵不含宏观类观点** | `category IN ('宏观指标','宏观')` 一律排除（宏观类观点仍在库里，只是本模块不返回）；要「含宏观」的数字就去掉该过滤 |
| 8 | 宏观周数少于观点周数 | 宏观数据起步晚于观点，**不要假设每周都有宏观**；没有就返回 0 行 |
| 9 | `price` 只是价格锚点 | 表示「分析时该资产的水平」，**不可与 `direction` 推导涨跌幅**（业务约定） |
| 10 | 两个模块的 `category` 含义不同 | 观点里是资产分类（外汇 / 大宗…），宏观 `items[].category` 是指标分类（通胀 / 就业…），别混用 |
| 11 | 最早几周没有 `views` 键 | 早期周 `data_json` 顶层是数组（结构不同），第一章的查询在这几周返回 0 行，属历史遗留 |
| 12 | 跨表 JOIN 报 `Illegal mix of collations (1267)` | 两表 `week` 列排序规则可能不一致 → `ON m.week = w.week COLLATE utf8mb4_unicode_ci`（或与部署环境一致的排序规则） |
| 13 | 宏观数据有历史兼容字段 | `weekly_analyzer_data.data_json.macro_forecasts` 是旧的兼容字段，**取宏观一律用 `weekly_macro_data`** |
| 14 | JSON 路径里的引号不要加反斜杠 | 写 `CONCAT('$."', 机构名, '"')`：单引号串里的双引号无需转义；加反斜杠在 `NO_BACKSLASH_ESCAPES` 模式下会失效 |
| 15 | `ONLY_FULL_GROUP_BY`（5.7 默认开启） | 本文档的聚合查询都把非聚合列写进了 `GROUP BY`，照抄即可 |
