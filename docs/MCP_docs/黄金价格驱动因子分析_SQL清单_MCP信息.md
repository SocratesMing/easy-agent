# 黄金价格多因子归因 — SQL 清单（供 MCP 接入）

本文档整理「黄金价格多因子归因」用到的**表结构与全部查询 SQL**（只含 SELECT 语句），
每条包含：用途（业务逻辑）、参数、可直接执行的 SQL、返回字段。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 表名 | `fmqt_gold_analysis_result`（一张宽表，一行 = 一个模型在某一天的一条记录） |
| 日期格式 | `creation_time` 列为 `DATE` 类型；SQL 里用 `'YYYY-MM-DD'` 字符串比较/赋值（如 `'2026-09-28'`） |
| 模型取值 | `model_name`：`多元线性回归`（滚动 126 日 OLS） / `随机森林`（滚动 150 日 + SHAP），与页面选项一致 |
| 数值单位 | **小数（收益率口径）**：`0.0185` 表示 1.85%，**不是**百分点；页面展示时 ×100 保留 2 位 |
| 数值语义 | 每个分组 = 该组因子在**截至该日、近 30 个交易日**的**累计**贡献（含当日） |
| 正负含义 | 正 = 该组因子拉动金价上涨，负 = 拖累；六列相加 ≈ 模型拟合的累计收益 |
| 去重口径 | 同一 `model_name` + `creation_time` 至多一条（唯一索引 `uk_model_date` + 应用层写入前查重） |
| 条数上限 | 无，历史记录长期保留 |
| 写入时点 | 页面「刷新分析」按钮触发；切换模型只算不存 |
| 时区 | 无时区概念，日期即交易数据日期（Wind 行情日期） |

> 单位换算提醒：本表数值**已是小数**，若页面要显示「+1.85%」，
> 在应用层 `value * 100` 保留 2 位即可，SQL 内不必再换算。

---

## 一、归因结果表

**表名**：`fmqt_gold_analysis_result`（黄金价格多因子归因结果）

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | `INT UNSIGNED` | 自增主键 |
| `model_name` | `VARCHAR(32)` | 模型名称：`多元线性回归` / `随机森林` |
| `creation_time` | `DATE` | 数据日期（展示区间截止日，即窗口内最后一个交易日） |
| `comex_volume` | `DECIMAL(18,6)` | COMEX成交量（COMEX 黄金期货成交量） |
| `fund_flow` | `DECIMAL(18,6)` | 资金流向（SPDR 黄金 ETF 持有量） |
| `usd_factor` | `DECIMAL(18,6)` | 美元因素（美元指数 + 美元兑日元 + 欧元兑美元） |
| `treasury_factor` | `DECIMAL(18,6)` | 美债因素（10 年美债实际收益率 + 10 年盈亏通胀率） |
| `risk_sentiment` | `DECIMAL(18,6)` | 风险情绪（WTI 原油 + VIX 波动率） |
| `commodity_factor` | `DECIMAL(18,6)` | 商品（LME 铜） |

> **口径提示 1（累计而非单日）**：表内是**窗口累计贡献**——截至该日、近 30 个交易日的合计。
> 本表**没有**单日贡献，需要日度分解请另取接口数据，不要拿本表相邻两行直接当"某日贡献"用。
> **口径提示 2（两个模型各一行）**：`model_name` 是中文，查询务必带上，
> 否则同一天会出现两行（回归 + 随机森林）导致数值翻倍。
> **口径提示 3（观察窗口）**：示例数据对应的展示窗口是 `2026-08-14 ~ 2026-09-28`（30 个交易日）。

**示例数据**（近 30 个交易日累计，窗口 2026-08-14 ~ 2026-09-28）：

| model_name | creation_time | comex_volume | fund_flow | usd_factor | treasury_factor | risk_sentiment | commodity_factor |
|---|---|---|---|---|---|---|---|
| 多元线性回归 | 2026-09-28 | -0.0002 | 0.0185 | -0.0108 | 0.0057 | -0.0080 | 0.0143 |
| 随机森林 | 2026-09-28 | 0.0002 | 0.0148 | 0.0052 | -0.0018 | -0.0023 | 0.0039 |
| 多元线性回归 | 2026-09-25 | -0.0014 | 0.0163 | -0.0094 | 0.0044 | -0.0084 | 0.0201 |

### 1.1 最新数据日期

**业务**：页面默认展示的最新日期；`creation_time` 参数留空时先取它。

```sql
SELECT MAX(`creation_time`) AS d FROM `fmqt_gold_analysis_result`;
```

**返回**：`d`（`DATE`）

### 1.2 某模型的最新数据日期

**业务**：某个模型单独查询（两模型入库进度可能不同步）。

**参数**：`model_name`

```sql
SELECT MAX(`creation_time`) AS d
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归';
```

### 1.3 上一可用日期

**业务**：计算「较前一日」环比。给定日期，取其之前最近的一个交易日。

**参数**：`model_name`、`creation_time`

```sql
SELECT MAX(`creation_time`) AS d
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归'
  AND `creation_time` < '2026-09-28';
```

> 不要用「取全部日期再在应用里找索引」的写法，那会全表扫描；
> 这条走 `uk_model_date` 索引，毫秒级。

### 1.4 最近 N 个交易日（倒序）

**业务**：趋势图、堆叠图的日期轴；日期选择器可选范围。

**参数**：`model_name`、`dates_limit`

```sql
SELECT DISTINCT `creation_time` AS d
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归'
ORDER BY d DESC
LIMIT 30;
```

### 1.5 某模型最近 N 条记录（明细）

**业务**：历史明细表（按写入顺序倒序，最常用的"看最近发生了什么"）。

**参数**：`model_name`、`limit`

```sql
SELECT `creation_time`, `comex_volume`, `fund_flow`, `usd_factor`,
       `treasury_factor`, `risk_sentiment`, `commodity_factor`
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归'
ORDER BY `creation_time` DESC
LIMIT 30;
```

**返回**：`creation_time` + 6 个分组列（小数）

### 1.6 某日各分组累计贡献

**业务**：核心查询。页面上「各因素贡献」柱状图 / 明细表的数据源。

**参数**：`model_name`、`creation_time`

```sql
SELECT `creation_time`,
       `comex_volume`, `fund_flow`, `usd_factor`,
       `treasury_factor`, `risk_sentiment`, `commodity_factor`
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归'
  AND `creation_time` = '2026-09-28';
```

### 1.7 同日两模型对照

**业务**：同一天的回归 vs 随机森林对比（差异即模型不确定性）。

**参数**：`creation_time`

```sql
SELECT `model_name`,
       `comex_volume`, `fund_flow`, `usd_factor`,
       `treasury_factor`, `risk_sentiment`, `commodity_factor`
FROM `fmqt_gold_analysis_result`
WHERE `creation_time` = '2026-09-28'
ORDER BY `model_name`;
```

### 1.8 分组贡献趋势（宽表转长表）

**业务**：堆叠图 / 分面趋势图的取数。MySQL 无 `UNPIVOT`，用 `UNION ALL` 展开。

**参数**：`model_name`、`days`

```sql
SELECT t.`creation_time`, t.group_key, t.value
FROM (
  SELECT `creation_time`, 'comex_volume'     AS group_key, `comex_volume`     AS value FROM `fmqt_gold_analysis_result` WHERE `model_name` = '多元线性回归'
  UNION ALL
  SELECT `creation_time`, 'fund_flow',        `fund_flow`        FROM `fmqt_gold_analysis_result` WHERE `model_name` = '多元线性回归'
  UNION ALL
  SELECT `creation_time`, 'usd_factor',       `usd_factor`       FROM `fmqt_gold_analysis_result` WHERE `model_name` = '多元线性回归'
  UNION ALL
  SELECT `creation_time`, 'treasury_factor',  `treasury_factor`  FROM `fmqt_gold_analysis_result` WHERE `model_name` = '多元线性回归'
  UNION ALL
  SELECT `creation_time`, 'risk_sentiment',   `risk_sentiment`   FROM `fmqt_gold_analysis_result` WHERE `model_name` = '多元线性回归'
  UNION ALL
  SELECT `creation_time`, 'commodity_factor', `commodity_factor` FROM `fmqt_gold_analysis_result` WHERE `model_name` = '多元线性回归'
) t
JOIN (
  SELECT `creation_time` FROM `fmqt_gold_analysis_result`
  WHERE `model_name` = '多元线性回归'
  ORDER BY `creation_time` DESC
  LIMIT 30
) d ON d.`creation_time` = t.`creation_time`
ORDER BY t.`creation_time` ASC, t.group_key;
```

**返回**：`creation_time`、`group_key`（6 个英文分组标识）、`value`（小数）

> 展示中文名时用本章开头字段表里给出的对照关系，不要在 SQL 里做名称映射。

### 1.9 近 N 日单个分组走势

**业务**：单因素走势小图。比 1.8 更轻，只需一列。

**参数**：`model_name`、`group`（枚举，取值见本章开头字段表的 6 个英文列名）、`days`

```sql
SELECT `creation_time`, `commodity_factor` AS value
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归'
ORDER BY `creation_time` DESC
LIMIT 30;
```

> 把 `` `commodity_factor` `` 换成目标分组列即可，取值**必须来自枚举白名单**，
> 不要接受自由文本拼接（列名无法用预编译占位符保护）。

### 1.10 各模型记录条数与覆盖区间

**业务**：确认两模型入库是否同步、历史有多长。

```sql
SELECT `model_name`,
       COUNT(*)                AS rows_count,
       MIN(`creation_time`)    AS first_date,
       MAX(`creation_time`)    AS last_date
FROM `fmqt_gold_analysis_result`
GROUP BY `model_name`;
```

**返回**：`model_name`、`rows_count`、`first_date`、`last_date`

### 1.11 相邻两日环比变化

**业务**：某分组「较前一日」的增减，用于变动提示（如资金流转强/转弱）。

**参数**：`model_name`、`days`（MySQL 8.0+）

```sql
SELECT `creation_time`,
       ROUND(`fund_flow`      - LAG(`fund_flow`)      OVER (ORDER BY `creation_time`), 4) AS fund_flow_chg,
       ROUND(`usd_factor`     - LAG(`usd_factor`)     OVER (ORDER BY `creation_time`), 4) AS usd_factor_chg,
       ROUND(`commodity_factor` - LAG(`commodity_factor`) OVER (ORDER BY `creation_time`), 4) AS commodity_factor_chg
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归'
ORDER BY `creation_time` DESC
LIMIT 30;
```

**MySQL 5.7 兼容写法**（无窗口函数时用自连接）：

```sql
SELECT c.`creation_time`,
       ROUND(c.`fund_flow` - p.`fund_flow`, 4) AS fund_flow_chg
FROM `fmqt_gold_analysis_result` c
JOIN `fmqt_gold_analysis_result` p
  ON p.`model_name` = c.`model_name`
 AND p.`creation_time` = (SELECT MAX(`creation_time`) FROM `fmqt_gold_analysis_result`
                 WHERE `model_name` = c.`model_name` AND `creation_time` < c.`creation_time`)
WHERE c.`model_name` = '多元线性回归'
ORDER BY c.`creation_time` DESC;
```

> 注意：这是**窗口累计值**之间的差，不等于"当天该因子的单日贡献"。

### 1.12 近 N 日分组区间统计

**业务**：一个分组在近 N 日里最强 / 最弱的水平，用于给当日数值定"高低"。

**参数**：`model_name`、`days`

```sql
SELECT
  ROUND(MIN(`comex_volume`),     4) AS comex_volume_min,
  ROUND(MAX(`comex_volume`),     4) AS comex_volume_max,
  ROUND(AVG(`comex_volume`),     4) AS comex_volume_avg,
  ROUND(MIN(`commodity_factor`), 4) AS commodity_factor_min,
  ROUND(MAX(`commodity_factor`), 4) AS commodity_factor_max,
  ROUND(AVG(`commodity_factor`), 4) AS commodity_factor_avg
FROM (
  SELECT * FROM `fmqt_gold_analysis_result`
  WHERE `model_name` = '多元线性回归'
  ORDER BY `creation_time` DESC
  LIMIT 30
) t;
```

### 1.13 六组合计（模型拟合的累计收益）

**业务**：把 6 个分组加总，得到模型口径下"六类因素合计解释的累计收益"。

**参数**：`model_name`、`days`

```sql
SELECT `creation_time`,
  ROUND(`comex_volume` + `fund_flow` + `usd_factor`
      + `treasury_factor` + `risk_sentiment` + `commodity_factor`, 4) AS total_contribution
FROM `fmqt_gold_analysis_result`
WHERE `model_name` = '多元线性回归'
ORDER BY `creation_time` DESC
LIMIT 30;
```

> 该合计 = 模型拟合收益的累计值，与金价真实涨跌幅有残差，
> 不要把 `total_contribution` 直接当成"金价涨了多少"。

### 1.14 重复数据自检

**业务**：唯一索引的冗余校验，用于数据治理（正常返回空集）。

```sql
SELECT `model_name`, `creation_time`, COUNT(*) AS n
FROM `fmqt_gold_analysis_result`
GROUP BY `model_name`, `creation_time`
HAVING COUNT(*) > 1;
```

---

## 二、MCP 工具映射建议

把上述 SQL 封装为 MCP 工具的建议：工具划分、入参定义与实现注意点。

### 2.1 设计原则

| 原则 | 说明 |
|---|---|
| **按业务能力划分，不按接口照搬** | 一个工具对应一个「用户会问的问题」。例如「某日各因素贡献」是一个工具，内部只用一条 SQL |
| **日期统一 `YYYY-MM-DD` 字符串** | 与 `DATE` 列一致；留空表示取最新交易日 || **枚举值收窄** | `model` 只允许 `多元线性回归`/`随机森林`；`group` 只允许 6 个英文标识。列名无法参数化，必须白名单校验 |
| **参数一律走预编译占位符** | 绝不用字符串拼接，防止 SQL 注入（仅**列名**需白名单映射） |
| **数值类参数设上下界** | 如 `days` 限 1~250、`limit` 限 1~50，避免 `LIMIT -5` 之类的语法错误 |
| **返回结构化 + 注明单位** | 数值字段明确标注"小数（收益率口径）"，避免模型当成百分比或亿元 |
| **单次工具调用对应 1 条 SQL** | 保持 <1 秒；不要在一个工具里做多表 JOIN 或大范围循环 |

### 2.2 工具清单

统一约定：`creation_time` 为 `YYYY-MM-DD` 字符串，留空取该模型最新日期；
`model` 留空默认 `多元线性回归`。「对应 SQL」列指向本文档前面的章节号。

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_gold_latest_date` | 取最新数据日期（可分模型） | `model?` | 1.1 / 1.2 |
| `get_gold_prev_date` | 取上一可用日期（环比用） | `model?`, `creation_time?` | 1.3 |
| `list_gold_dates` | 最近 N 个交易日 | `model?`, `days?`(1-250) | 1.4 |
| `list_gold_attribution_history` | 归因记录明细（倒序） | `model?`, `limit?`(1-50) | 1.5 |
| `get_gold_attribution` | 某日各分组累计贡献 | `model?`, `creation_time?` | 1.6 |
| `compare_gold_models` | 同日两模型对照 | `creation_time?` | 1.7 |
| `get_gold_group_trend` | 近 N 日分组贡献趋势（长表） | `model?`, `days?`(1-250) | 1.8 |
| `get_gold_group_series` | 近 N 日单个分组走势 | `model?`, `group`(枚举), `days?`(1-250) | 1.9 |
| `get_gold_model_coverage` | 各模型条数与覆盖区间 | — | 1.10 |
| `get_gold_group_change` | 相邻两日环比变化 | `model?`, `days?`(1-250) | 1.11 |
| `get_gold_group_stats` | 近 N 日分组区间统计 | `model?`, `days?`(1-250) | 1.12 |
| `get_gold_total_contribution` | 六组合计（拟合累计收益） | `model?`, `days?`(1-250) | 1.13 |

### 2.3 工具定义示例（可直接用作 schema 模板）

```json
{
  "name": "get_gold_attribution",
  "description": "查询指定交易日黄金价格的多因子归因结果，返回 6 个因子分组的累计贡献。数值为收益率口径的小数（0.0185 表示 1.85%），正值表示该组因子拉动金价上涨、负值表示拖累。creation_time 留空则取最新交易日。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "model": {
        "type": "string",
        "enum": ["多元线性回归", "随机森林"],
        "description": "归因模型；留空默认多元线性回归"
      },
      "creation_time": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "数据日期，格式 YYYY-MM-DD；留空则取最新交易日"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_gold_group_series",
  "description": "查询某一因子分组在最近 N 个交易日的累计贡献走势，用于画单因素趋势图。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "model": {
        "type": "string",
        "enum": ["多元线性回归", "随机森林"]
      },
      "group": {
        "type": "string",
        "enum": ["comex_volume", "fund_flow", "usd_factor", "treasury_factor", "risk_sentiment", "commodity_factor"],
        "description": "因子分组：comex_volume=COMEX成交量、fund_flow=资金流向(ETF持仓)、usd_factor=美元因素、treasury_factor=美债因素、risk_sentiment=风险情绪(WTI+VIX)、commodity_factor=商品(LME铜)"
      },
      "days": {
        "type": "integer",
        "minimum": 1,
        "maximum": 250,
        "description": "回溯多少个交易日，默认 30"
      }
    },
    "required": ["group"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_gold_group_change",
  "description": "查询最近 N 个交易日各因子分组累计贡献的相邻两日变化（当日 − 前一日）。注意这是窗口累计值之间的差，不等于当天该因子的单日贡献。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "model": {
        "type": "string",
        "enum": ["多元线性回归", "随机森林"]
      },
      "days": {
        "type": "integer",
        "minimum": 1,
        "maximum": 250,
        "description": "回溯多少个交易日，默认 30"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

### 2.4 实现注意事项

1. **单位在工具层说清，别让模型猜**
   - 本表数值是**小数**（收益率口径），不是亿元、不是百分点；
   - 若返回给模型用，建议在字段说明里写明"0.0185 = 1.85%"，或在工具层就 ×100 转成百分比后返回，二选一并保持一致。

2. **`creation_time` 留空时先取最新交易日**
   调用 `get_gold_latest_date` 拿到日期，再执行主查询（共 2 条 SQL，都在毫秒级）。

3. **`model_name` 一定要带上**
   两个模型同一天各一行，漏掉 `WHERE model_name = ?` 会让金额/贡献翻倍。
   中文枚举建议在应用层做映射（不要接受自由文本）。

4. **`group` 是列名，必须白名单**
   `get_gold_group_series` 的 `group` 直接决定 SELECT 哪一列，**无法用占位符**，
   必须校验取值属于那 6 个英文标识，否则可被注入。

5. **表内是窗口累计值，不是单日值**
   本表用于"近 30 个交易日各因素贡献了多少"这类问题；
   用户若问"昨天哪个因素贡献最大"，需说明这里是累计口径。

6. **连接池与超时**
   建议连接池 ≥10、单查询超时设 1~2 秒。本表是单表查询且 `uk_model_date` 可命中，
   实测最慢在毫秒级。

7. **只读即可**
   建议 MCP Server 使用**只读数据库账号**：本文档全部是 SELECT 查询语句，
   数据写入由站点自身的「刷新分析」动作完成，MCP 侧不需要写权限。

8. **表名前缀**
   同一实例中本表以 `fmqt_` 前缀命名，与 iData 看板各表保持一致的命名风格。

---

## 附：与站点接口的对应关系

| 站点接口 | 作用 | 对应本文档 |
|---|---|---|
| `GET /research/gold_factor_analysis?model=&tail=` | 计算归因并返回图表数据，**不写库** | 计算逻辑，不入库 |
| `POST /research/gold_snapshot?model=&tail=` | 重算并落库（补录新增交易日，按「模型 + 数据日期」去重） | 表数据的来源，本身不出现在本清单 |
| `GET /research/gold_history?limit=` | 查询已落库记录（按写入顺序倒序） | 1.5 |
| `GET /research/gold_data_source` | 查看当前数据文件路径与更新时间 | — |

> 页面展示区间固定为近 30 个交易日（`tail=30`），落库数值与页面看到的口径一致。
