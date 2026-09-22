# 三个页面的 SQL 参考（供 MCP 接入）

本文档整理「机构当日 / 个券机构 / 债券借贷」三个页面用到的**全部 SQL**，
每条包含：用途（业务逻辑）、参数、可直接执行的 SQL、返回字段。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 日期格式 | `YYYYMMDD` 字符串（如 `20260828`） |
| 买入 / 卖出 | `SIDE='1'` 买入，`SIDE='2'` 卖出 |
| 净买入 | 买入 − 卖出，正值=净买入，负值=净卖出 |
| 金额单位 | **机构当日**：SQL 内已转**亿元**（`ROUND(x/100000000,2)`）<br>**个券机构**：SQL 返回**元**，由应用层 ÷1e8<br>**债券借贷**：SQL 返回**元**，由应用层 ÷1e8 |
| 集中度 / 占比 | 单位 **%**（百分号前的数值，如 24.53 表示 24.53%） |
| 展示顺序 | 机构顺序、券种顺序等均由**应用层**常量控制，不在 SQL 中 |

---

## 一、机构当日

**表名**：`fmqt_bond_exec_part_data`（现券成交分机构统计）

| 字段 | 含义 |
|---|---|
| `STATISTIC_REPORT_RANGE` | 交易日 |
| `STATISTIC_DETAIL_NAME` | 机构名称（如「大型银行」「证券公司」） |
| `STATISTIC_SUBDETAIL_NAME` | 期限（如 `1-3年`、`合计`） |
| `RELATED_STATISTIC_SUBDETAIL_NAME` | 券种（如 `国债-新债`、`合计`） |
| `SIDE` | 方向：`1`=买入，`2`=卖出 |
| `TRADE_CASHAMT` | 成交金额（varchar，需 `CAST` 为 DECIMAL） |

> 口径提示：该表同时存有**明细行**与**合计行**（期限=`合计` 或 券种=`合计`）。
> 查询时必须显式指定是要合计还是明细，否则会把合计行与明细行一起聚合导致金额翻倍。

### 1.1 最新交易日

**业务**：页面默认展示的最新日期。

```sql
SELECT MAX(STATISTIC_REPORT_RANGE) AS d FROM fmqt_bond_exec_part_data;
```

### 1.2 全部交易日（倒序）

**业务**：日期选择器的可选范围（一般用 1.3/1.4 即可，不必全量）。

```sql
SELECT DISTINCT STATISTIC_REPORT_RANGE AS d
FROM fmqt_bond_exec_part_data
ORDER BY d DESC;
```

### 1.3 上一交易日

**业务**：计算「较前一日」环比。给定日期，取其之前最近的一个交易日。

```sql
SELECT MAX(STATISTIC_REPORT_RANGE) AS d
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE < '20260828';
```

> 不要用「取全部日期再在应用里找索引」的写法，那会全表扫描（约 20ms）；
> 这条可命中索引，毫秒级。

### 1.4 最近 N 个交易日（倒序）

**业务**：趋势图、堆叠图的日期轴。

```sql
SELECT DISTINCT STATISTIC_REPORT_RANGE AS d
FROM fmqt_bond_exec_part_data
ORDER BY d DESC
LIMIT 20;
```

### 1.5 当日各机构净买卖汇总

**业务**：页面顶部三个汇总框（净买入最多 / 净卖出最多 / 买方净额合计）与柱状图。

**参数**：`date`

```sql
SELECT
  STATISTIC_DETAIL_NAME AS institution,
  ROUND(SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END) / 100000000, 2) AS buy,
  ROUND(SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END) / 100000000, 2) AS sell,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE = '20260828'
  AND STATISTIC_SUBDETAIL_NAME = '合计'
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '合计'
GROUP BY STATISTIC_DETAIL_NAME
ORDER BY net DESC;
```

**返回**：`institution`、`buy`、`sell`、`net`（亿元）

### 1.6 机构 × 券种 净买入矩阵

**业务**：机构 × 券种热力矩阵。`bond_type` 已按展示口径映射（国债-新债/老债→国债）。

```sql
SELECT
  STATISTIC_DETAIL_NAME AS institution,
  CASE
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '合计' THEN '合计'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME IN ('国债-新债','国债-老债') THEN '国债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME IN ('政策性金融债-新债','政策性金融债-老债') THEN '政金债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '中期票据' THEN '中票'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '短期/超短期融资券' THEN '短融超短融'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '企业债' THEN '企业债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '地方政府债' THEN '地方债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '同业存单' THEN '同业存单'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '资产支持证券' THEN 'ABS'
    ELSE '其他'
  END AS bond_type,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE = '20260828'
  AND STATISTIC_SUBDETAIL_NAME = '合计'
GROUP BY STATISTIC_DETAIL_NAME, bond_type;
```

### 1.7 机构 × 期限 净买入矩阵

**业务**：机构 × 期限热力矩阵。

```sql
SELECT
  STATISTIC_DETAIL_NAME AS institution,
  STATISTIC_SUBDETAIL_NAME AS term,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE = '20260828'
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '合计'
GROUP BY STATISTIC_DETAIL_NAME, STATISTIC_SUBDETAIL_NAME;
```

### 1.8 近 N 日各机构净买入趋势

**业务**：机构 × 日期趋势表。可按券种、期限筛选。

**参数**：`term`（期限，如 `合计`）、`dates`（日期列表）、`bond_type`（展示券种，如 `合计`/`国债`）

```sql
SELECT
  STATISTIC_REPORT_RANGE AS date,
  STATISTIC_DETAIL_NAME AS institution,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_SUBDETAIL_NAME = '合计'
  AND STATISTIC_REPORT_RANGE IN ('20260826','20260827','20260828')
  AND (CASE
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '合计' THEN '合计'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME IN ('国债-新债','国债-老债') THEN '国债'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME IN ('政策性金融债-新债','政策性金融债-老债') THEN '政金债'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '中期票据' THEN '中票'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '短期/超短期融资券' THEN '短融超短融'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '企业债' THEN '企业债'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '地方政府债' THEN '地方债'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '同业存单' THEN '同业存单'
        WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '资产支持证券' THEN 'ABS'
        ELSE '其他'
      END) = '合计'
GROUP BY STATISTIC_REPORT_RANGE, STATISTIC_DETAIL_NAME
ORDER BY STATISTIC_REPORT_RANGE ASC;
```

### 1.9 券种 × 期限 × 机构 三维明细（热力图）

**业务**：券种 × 期限热力图，可下钻到机构。券种为**原始名称**（新债/老债不合并）。

```sql
SELECT
  RELATED_STATISTIC_SUBDETAIL_NAME AS bond_type,
  STATISTIC_SUBDETAIL_NAME AS term,
  STATISTIC_DETAIL_NAME AS institution,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE = '20260828'
  AND STATISTIC_SUBDETAIL_NAME != '合计'
  AND RELATED_STATISTIC_SUBDETAIL_NAME != '合计'
GROUP BY RELATED_STATISTIC_SUBDETAIL_NAME, STATISTIC_SUBDETAIL_NAME, STATISTIC_DETAIL_NAME;
```

### 1.10 机构净买入截面明细

**业务**：截面明细表（机构 × 期限 × 券种的买入/卖出/净额）。可按机构过滤。

```sql
SELECT
  STATISTIC_DETAIL_NAME AS institution,
  STATISTIC_SUBDETAIL_NAME AS term,
  RELATED_STATISTIC_SUBDETAIL_NAME AS bond_type,
  ROUND(SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END) / 100000000, 2) AS buy,
  ROUND(SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END) / 100000000, 2) AS sell,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE = '20260828'
  AND STATISTIC_SUBDETAIL_NAME != '合计'
  AND RELATED_STATISTIC_SUBDETAIL_NAME != '合计'
  -- 可选：AND STATISTIC_DETAIL_NAME = '大型银行'
GROUP BY STATISTIC_DETAIL_NAME, STATISTIC_SUBDETAIL_NAME, RELATED_STATISTIC_SUBDETAIL_NAME
ORDER BY net DESC;
```

### 1.11 分券种的机构 × 期限矩阵

**业务**：国债 / 政金债 / 信用债 / 地方政府债 各自的「机构 × 期限」矩阵
（国债、政金债的 7-10 年会拆为新债 / 老债 / 合计三列）。

**参数**：`date`、`bond_filter`（见下方券种过滤条件）

```sql
SELECT
  STATISTIC_DETAIL_NAME AS institution,
  STATISTIC_SUBDETAIL_NAME AS term,
  RELATED_STATISTIC_SUBDETAIL_NAME AS bond_type,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE = '20260828'
  AND STATISTIC_SUBDETAIL_NAME != '合计'
  AND RELATED_STATISTIC_SUBDETAIL_NAME IN ('国债-新债', '国债-老债')   -- 以「国债」为例
GROUP BY STATISTIC_DETAIL_NAME, STATISTIC_SUBDETAIL_NAME, RELATED_STATISTIC_SUBDETAIL_NAME;
```

**券种过滤条件（`bond_filter`）取值**：

| 券种 | 条件 |
|---|---|
| 国债 | `RELATED_STATISTIC_SUBDETAIL_NAME IN ('国债-新债', '国债-老债')` |
| 政金债 | `RELATED_STATISTIC_SUBDETAIL_NAME IN ('政策性金融债-新债', '政策性金融债-老债')` |
| 信用债 | `RELATED_STATISTIC_SUBDETAIL_NAME IN ('中期票据', '短期/超短期融资券', '企业债', '资产支持证券')` |
| 地方政府债 | `RELATED_STATISTIC_SUBDETAIL_NAME = '地方政府债'` |
| 全部 | `RELATED_STATISTIC_SUBDETAIL_NAME != '合计'` |
| 合计 | `RELATED_STATISTIC_SUBDETAIL_NAME = '合计'` |

### 1.12 机构合计净买入（环比基准）

**业务**：取若干日期、某券种的机构合计净买入，用于计算
「前一日合计」与「上周日度平均」两个环比指标。

**参数**：`dates`（日期列表）、`bond_filter`

```sql
SELECT
  STATISTIC_REPORT_RANGE AS date,
  STATISTIC_DETAIL_NAME AS institution,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE IN ('20260827')
  AND STATISTIC_SUBDETAIL_NAME = '合计'
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '合计'
GROUP BY STATISTIC_REPORT_RANGE, STATISTIC_DETAIL_NAME;
```

> 上层业务：前一日合计 = 取上一交易日单天结果；
> 上周日度平均 = 取上一自然周（周一~周日）所有交易日结果求和 ÷ 天数。

### 1.13 日期范围内的交易日

**业务**：确定「上一自然周」内实际有哪些交易日。

```sql
SELECT DISTINCT STATISTIC_REPORT_RANGE AS d
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_REPORT_RANGE BETWEEN '20260817' AND '20260823'
ORDER BY d ASC;
```

### 1.14 某机构期限堆叠（6 档）

**业务**：机构期限堆叠柱状图。10 年以上各档合并为「大于10年」。

```sql
SELECT
  STATISTIC_REPORT_RANGE AS date,
  CASE
    WHEN STATISTIC_SUBDETAIL_NAME = '1年及1年以下' THEN '1年及1年以下'
    WHEN STATISTIC_SUBDETAIL_NAME = '1-3年' THEN '1-3年'
    WHEN STATISTIC_SUBDETAIL_NAME = '3-5年' THEN '3-5年'
    WHEN STATISTIC_SUBDETAIL_NAME = '5-7年' THEN '5-7年'
    WHEN STATISTIC_SUBDETAIL_NAME = '7-10年' THEN '7-10年'
    ELSE '大于10年'
  END AS term_bucket,
  ROUND((SUM(CASE WHEN SIDE='1' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)
       - SUM(CASE WHEN SIDE='2' THEN CAST(TRADE_CASHAMT AS DECIMAL(20,2)) ELSE 0 END)) / 100000000, 2) AS net
FROM fmqt_bond_exec_part_data
WHERE STATISTIC_DETAIL_NAME = '大型银行'
  AND STATISTIC_REPORT_RANGE IN ('20260826','20260827','20260828')
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '合计'
  AND STATISTIC_SUBDETAIL_NAME != '合计'
GROUP BY STATISTIC_REPORT_RANGE, term_bucket;
```

---

## 二、个券机构

**表名**：`fmqt_bond_trade_counterpart_analysis`（个债交易对手分析）

| 字段 | 含义 |
|---|---|
| `TRANSACT_DATE` | 交易日 |
| `STATISTIC_DETAIL_ID` / `STATISTIC_DETAIL_NAME` | 债券代码 / 简称 |
| `STATISTIC_SUBDETAIL_NAME` | 机构名称 |
| `RELATED_STATISTIC_SUBDETAIL_NAME` | 固定取 `'全部'`（排除其他口径） |
| `BID_SIZE` | 净买卖金额（**元**） |
| `TRADE_QTY_RATIO` | 成交量占比（%） |

> 单位注意：本表 SQL 返回**元**，页面展示的亿元需在应用层 ÷100000000。

### 2.1 最新交易日

```sql
SELECT MAX(TRANSACT_DATE) AS d FROM fmqt_bond_trade_counterpart_analysis;
```

### 2.2 最近 N 个工作日

**业务**：日期选择器可选范围（约一个月）。SQL 先多取一些日期，
由应用层剔除周末后取前 N 个。

```sql
-- 无终点日期
SELECT DISTINCT TRANSACT_DATE AS d
FROM fmqt_bond_trade_counterpart_analysis
ORDER BY d DESC
LIMIT 44;   -- 22 * 2，多取以补偿剔除周末

-- 指定终点日期
SELECT DISTINCT TRANSACT_DATE AS d
FROM fmqt_bond_trade_counterpart_analysis
WHERE TRANSACT_DATE <= '20260827'
ORDER BY d DESC
LIMIT 44;
```

> 应用层：过滤 `weekday() < 5`（周一~周五），再 `[:N]`。

### 2.3 某交易日的个券列表

**业务**：选券下拉框。

```sql
SELECT DISTINCT STATISTIC_DETAIL_ID AS id, STATISTIC_DETAIL_NAME AS name
FROM fmqt_bond_trade_counterpart_analysis
WHERE TRANSACT_DATE = '20260827'
ORDER BY id;
```

### 2.4 债券简称

```sql
SELECT DISTINCT STATISTIC_DETAIL_NAME AS name
FROM fmqt_bond_trade_counterpart_analysis
WHERE STATISTIC_DETAIL_ID = '260205'
LIMIT 1;
```

### 2.5 某券某日各机构净买卖

**业务**：各机构净买卖柱状图 + 主力净买入 / 净卖出。

```sql
SELECT STATISTIC_SUBDETAIL_NAME AS institution, BID_SIZE AS net
FROM fmqt_bond_trade_counterpart_analysis
WHERE STATISTIC_DETAIL_ID = '260205'
  AND TRANSACT_DATE = '20260827'
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '全部';
```

**返回**：`institution`、`net`（元）。应用层取 max / min 得主力买 / 卖。

### 2.6 某券近 N 日趋势

**业务**：机构 × 日期趋势表。

```sql
SELECT TRANSACT_DATE AS date, STATISTIC_SUBDETAIL_NAME AS institution, BID_SIZE AS net
FROM fmqt_bond_trade_counterpart_analysis
WHERE STATISTIC_DETAIL_ID = '260205'
  AND TRANSACT_DATE IN ('20260825','20260826','20260827')
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '全部'
ORDER BY TRANSACT_DATE ASC;
```

### 2.7 某日全部券 × 机构（重点标的看板）

**业务**：「最新交易日重点标的看板」。一次性取当日所有券 × 机构，
在应用层按活跃度（净买卖绝对值合计）排序取前 N 个券。

```sql
SELECT
  STATISTIC_DETAIL_ID AS bond_id,
  STATISTIC_DETAIL_NAME AS bond_name,
  STATISTIC_SUBDETAIL_NAME AS institution,
  BID_SIZE AS net
FROM fmqt_bond_trade_counterpart_analysis
WHERE TRANSACT_DATE = '20260827'
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '全部';
```

> 应用层：按券聚合 → `activity = Σ|net|` → 降序取前 N（默认 8）→
> 每个券再取最大净买入 / 最大净卖出机构。

### 2.8 某券各机构交易量占比

**业务**：各机构成交量占比（进度条展示）。

```sql
SELECT STATISTIC_SUBDETAIL_NAME AS institution, TRADE_QTY_RATIO AS ratio
FROM fmqt_bond_trade_counterpart_analysis
WHERE STATISTIC_DETAIL_ID = '260205'
  AND TRANSACT_DATE = '20260827'
  AND RELATED_STATISTIC_SUBDETAIL_NAME = '全部';
```

**返回**：`institution`、`ratio`（%）

---

## 三、债券借贷

**表名**：`fmqt_bond_lending_analysis`（个债债券借贷分析）

| 字段 | 含义 |
|---|---|
| `TRANSACT_DATE` | 交易日 |
| `STATISTIC_DETAIL_ID` / `STATISTIC_DETAIL_NAME` | 债券代码 / 简称 |
| `STATISTIC_AMT_TYPE` | 金额类型，`'19'` = 借贷余额 |
| `STATISTIC_AMT_SIDE` | 方向，`'1'` = 融入 |
| `STATISTIC_AMT_VALUE` | 金额（varchar，需 CAST） |
| `BOND_CONCENTRATION` | 集中度（%） |

> **借贷余额口径**（重要）：借贷余额 = `STATISTIC_AMT_TYPE='19'`
> 且 `STATISTIC_AMT_SIDE='1'` 的 `STATISTIC_AMT_VALUE` 汇总，
> **不是**成交量字段。

### 3.1 最新交易日

```sql
SELECT MAX(TRANSACT_DATE) AS d FROM fmqt_bond_lending_analysis;
```

### 3.2 前一交易日

```sql
SELECT MAX(TRANSACT_DATE) AS d
FROM fmqt_bond_lending_analysis
WHERE TRANSACT_DATE < '20260827';
```

### 3.3 最近 N 个交易日（升序）

```sql
SELECT DISTINCT TRANSACT_DATE AS d
FROM fmqt_bond_lending_analysis
ORDER BY d DESC
LIMIT 200;
```

> SQL 取倒序后由应用层 `reverse()` 转升序。

### 3.4 某日全部券的借贷余额与集中度（快照）

**业务**：核心查询。用于：
- 顶部「集中度最高的券」
- 借贷余额较前日上升 / 下降两个列表
- 当日余额看板与明细表

```sql
SELECT
  STATISTIC_DETAIL_ID AS code,
  STATISTIC_DETAIL_NAME AS name,
  SUM(CASE WHEN STATISTIC_AMT_TYPE = '19' AND STATISTIC_AMT_SIDE = '1'
           THEN CAST(STATISTIC_AMT_VALUE AS DECIMAL(20,2)) ELSE 0 END) AS balance,
  MAX(CAST(BOND_CONCENTRATION AS DECIMAL(20,6))) AS concentration
FROM fmqt_bond_lending_analysis
WHERE TRANSACT_DATE = '20260827'
GROUP BY STATISTIC_DETAIL_ID, STATISTIC_DETAIL_NAME;
```

**返回**：`code`、`name`、`balance`（元）、`concentration`（%）

> 注意：`BOND_CONCENTRATION` **可能存在 NULL**（实测 4 行），
> 应用层需按 0 兜底，否则 `float(None)` 会报错。

**上层业务**（取当日与前一日的快照后对比）：

| 指标 | 算法 |
|---|---|
| 集中度变化 | 当日 `concentration` − 前日 `concentration`（百分点） |
| 余额变化 | (当日 `balance` − 前日 `balance`) / 1e8（亿元） |
| 上升 / 下降分组 | 按**余额变化**正负分入 `bal_up` / `bal_down` |
| 排序 | 借贷余额 ＞ 集中度 ＞ 余额变化（均降序） |

### 3.5 每个券出现的交易日数

**业务**：判断「持续更新」的券。只有出现天数等于最大天数的券，
才会进入看板与明细表（页面展示 7 只核心跟踪券）。

```sql
SELECT STATISTIC_DETAIL_ID AS code, COUNT(DISTINCT TRANSACT_DATE) AS days
FROM fmqt_bond_lending_analysis
GROUP BY STATISTIC_DETAIL_ID;
```

> 应用层：`total_days = max(days)`，仅保留 `days == total_days` 的券，
> 再按余额降序取前 7（`TRACK_COUNT`），并排除 `HIDDEN_BOND_IDS`。

### 3.6 某券借贷余额与集中度走势

**业务**：右侧走势图（借贷余额 + 集中度双轴）。

```sql
SELECT
  TRANSACT_DATE AS date,
  SUM(CASE WHEN STATISTIC_AMT_TYPE = '19' AND STATISTIC_AMT_SIDE = '1'
           THEN CAST(STATISTIC_AMT_VALUE AS DECIMAL(20,2)) ELSE 0 END) AS balance,
  MAX(CAST(BOND_CONCENTRATION AS DECIMAL(20,6))) AS concentration
FROM fmqt_bond_lending_analysis
WHERE STATISTIC_DETAIL_ID = '260205'
  AND TRANSACT_DATE IN ('20260803','20260804','20260805')
GROUP BY TRANSACT_DATE
ORDER BY TRANSACT_DATE ASC;
```

---

## 四、附：大模型分析结果表

**表名**：`fmqt_idata_llm_analysis`

| 字段 | 含义 |
|---|---|
| `SCENE` | 场景：`institution_daily` / `bond_institution` / `bond_lending` |
| `SCENE_NAME` | 场景中文名 |
| `ANALYSIS_DATE` | 分析日期 |
| `CONTENT` | 大模型生成的点评正文 |
| `PROMPT` | 使用的提示词 |
| `MODEL` | 模型名称 |
| `STATUS` | `success` / `failed` / `pending` |
| `ERROR_MSG` | 失败原因 |

**查询某场景最新成功结果**：

```sql
SELECT * FROM fmqt_idata_llm_analysis
WHERE SCENE = 'institution_daily' AND STATUS = 'success'
ORDER BY ANALYSIS_DATE DESC
LIMIT 1;
```

---



## 五、MCP 工具映射建议

下面给出把上述 SQL 封装为 MCP 工具的建议：工具划分、入参定义与实现注意点。

### 5.1 设计原则

| 原则 | 说明 |
|---|---|
| **按业务能力划分，不按接口照搬** | 一个工具对应一个「用户会问的问题」，而不是一个 HTTP 接口。例如「某券各机构净买卖」是一个工具，内部只用一条 SQL |
| **日期统一 `YYYYMMDD` 字符串** | 与数据库一致，避免时区/格式歧义；留空表示取最新交易日 |
| **枚举值收窄** | 券种、期限、机构等用枚举而非自由文本，减少模型乱填（如券种只允许 `国债/政金债/信用债/地方政府债/全部/合计`） |
| **参数一律走预编译占位符** | 绝不用字符串拼接，防止 SQL 注入 |
| **数值类参数设上下界** | 如 `days` 限 1~250、`limit` 限 1~50，避免 `LIMIT -5` 之类的语法错误 |
| **返回结构化 + 注明单位** | 金额字段明确单位（元 or 亿元），避免模型误读 |
| **单次工具调用对应 1 条 SQL** | 保持 <1 秒；不要在工具内部做多表 JOIN 或大范围循环 |

### 5.2 工具清单

统一约定：`date` 为 `YYYYMMDD` 字符串；留空取该表最新交易日。
「对应 SQL」列指向本文档前面的章节号。

#### 通用（跨表）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_latest_trade_date` | 取最新交易日 | `source`: `daily`/`bond`/`lending` | 1.1 / 2.1 / 3.1 |
| `get_prev_trade_date` | 取上一交易日（环比用） | `date`, `source` | 1.3 / 3.2 |

#### 机构当日（`source=daily`）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `list_institution_net_summary` | 各机构买入/卖出/净额（亿元） | `date?` | 1.5 |
| `get_institution_bond_matrix` | 机构 × 券种 净买入 | `date?` | 1.6 |
| `get_institution_term_matrix` | 机构 × 期限 净买入 | `date?` | 1.7 |
| `get_institution_trend` | 近 N 日各机构净买入趋势 | `days?`(1-250), `bond_type?`, `term?` | 1.8 |
| `get_institution_term_stack` | 某机构期限堆叠（6 档） | `institution`, `days?` | 1.14 |
| `get_bond_term_matrix` | 分券种的机构 × 期限矩阵 | `bond_type`(枚举), `date?` | 1.11 |
| `get_institution_cross_section` | 截面明细（机构×期限×券种） | `date?`, `institution?` | 1.10 |
| `get_institution_heatmap` | 券种×期限×机构三维明细 | `date?` | 1.9 |

#### 个券机构（`source=bond`）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `list_bonds` | 某交易日的个券列表 | `date?` | 2.3 |
| `get_bond_institution_summary` | 某券各机构净买卖（含主力买/卖） | `bond_id`, `date?` | 2.5 |
| `get_bond_institution_trend` | 某券近 N 日机构 × 日期趋势 | `bond_id`, `days?`(1-250) | 2.6 |
| `list_top_active_bonds` | 成交最活跃的前 N 个券 | `date?`, `limit?`(1-50) | 2.7 |
| `get_bond_institution_ratio` | 某券各机构成交量占比 | `bond_id`, `date?` | 2.8 |

#### 债券借贷（`source=lending`）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_lending_snapshot` | 某日全部券的借贷余额与集中度 | `date?` | 3.4 |
| `get_lending_balance_changes` | 借贷余额较前日上升/下降的券 | `date?` | 3.4（当日+前日对比） |
| `list_core_lending_bonds` | 持续更新的核心跟踪券（默认 7 只） | `date?`, `limit?` | 3.4 + 3.5 |
| `get_lending_trend` | 某券余额与集中度走势 | `code`, `months?`(1-36) | 3.6 |

#### 大模型点评

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_llm_comment` | 取某场景已生成的点评正文 | `scene`(枚举), `date?` | 第四节 |

### 5.3 工具定义示例（可直接用作 schema 模板）

```json
{
  "name": "list_institution_net_summary",
  "description": "查询指定交易日各机构在现券市场的买入、卖出与净买入金额。金额为亿元，净买入正值为买入、负值为卖出。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "pattern": "^\\d{8}$",
        "description": "交易日，格式 YYYYMMDD；留空则取最新交易日"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_bond_institution_summary",
  "description": "查询某只债券在指定交易日各交易对手方的净买卖金额，并给出主力净买入机构与主力净卖出机构。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "bond_id": {
        "type": "string",
        "description": "债券代码，如 260205"
      },
      "date": {
        "type": "string",
        "pattern": "^\\d{8}$",
        "description": "交易日 YYYYMMDD；留空取最新"
      }
    },
    "required": ["bond_id"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_lending_balance_changes",
  "description": "对比指定交易日与前一交易日，列出借贷余额增加与减少的债券。排序为借贷余额 > 集中度 > 余额变化（均降序）。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "date": {
        "type": "string",
        "pattern": "^\\d{8}$",
        "description": "交易日 YYYYMMDD；留空取最新"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

### 5.4 实现注意事项

1. **单位换算在工具层做，别让模型猜**
   - 机构当日的 SQL 已返回**亿元**，直接返回即可；
   - 个券机构、债券借贷的 SQL 返回**元**，工具层需 ÷100000000 并保留 2 位小数后再返回，并在字段说明中写明单位。

2. **`date` 留空时先取最新交易日**
   调用 `get_latest_trade_date` 拿到日期，再执行主查询（共 2 条 SQL，都在毫秒级）。

3. **债券借贷的余额口径**
   必须是 `STATISTIC_AMT_TYPE='19' AND STATISTIC_AMT_SIDE='1'` 的汇总（见 3.4），
   不要误用成交量字段。

4. **`BOND_CONCENTRATION` 可能为 NULL**
   实测有 4 行 NULL，取数后需按 0 兜底，否则类型转换会抛异常。

5. **机构当日的「合计行」陷阱**
   该表同时有明细行与合计行，工具内 SQL 必须显式指定
   （`= '合计'` 或 `!= '合计'`），否则金额会翻倍。

6. **连接池与超时**
   建议连接池 ≥10、单查询超时设 1~2 秒；本项目实测最慢查询约 41ms。

7. **不要在工具内做大范围聚合**
   若需要跨多月聚合，按 5~10 个交易日分片后在内存汇总（一次性查 12 个月约 7 秒，会超时）。

8. **只读即可**
   建议 MCP Server 使用**只读数据库账号**，这些工具全部是 SELECT，无需写权限。
