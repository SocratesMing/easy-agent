# 黄金月频预测 — SQL 清单（供 MCP 接入）

本文档整理「黄金月频预测」用到的**表结构与全部查询 SQL**（只含 SELECT 语句），
每条包含：用途（业务逻辑）、参数、可直接执行的 SQL、返回字段。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 表名 | `fmqt_gold_monthly_forecast`（一张宽表，一行 = 一个预测基准日的一期预测 + 事后验证） |
| 日期格式 | `predict_date` 为 `DATE` 类型；SQL 里用 `'YYYY-MM-DD'` 字符串比较（如 `'2026-10-09'`） |
| 标的代码 | `symbol` 为 `VARCHAR(32)`，当前只有 `XAUUSD`（伦敦金现货，美元/盎司）；查询建议显式带上 |
| 概率单位 | `prob_up` 是**小数（概率口径）**：`0.229329` 表示 **22.93%**，**不是**百分点；页面展示时 ×100 保留 1~2 位 |
| 方向编码 | `pred_direction` / `actual_direction`：**`1` = 涨，`0` = 跌**（不是 `+1/-1`，也不是 `true/false`） |
| 收益单位 | `actual_return` 是 **T+22 日对数收益率**（小数）：`-0.086108` = 区间跌 8.61%；是对数收益，**不是**简单收益率 |
| 命中口径 | `hit` = 预测方向与实际方向是否一致：`1` 命中 / `0` 未命中；**未兑现为 `NULL`** |
| 未兑现 | 预测日之后还不足 22 个交易日的期数，`actual_direction / actual_return / hit` 三列全为 `NULL`（正常现象，不要当 0） |
| 去重口径 | **`symbol` 相同时 `predict_date` 唯一**（唯一索引 `uk_predict_date` + 应用层 `ON DUPLICATE KEY UPDATE`） |
| 条数上限 | 无，历史长期保留（当前 **1443 条**，覆盖 `2020-02-20 ~ 2026-10-09`） |
| 写入时点 | `GET /research/gold_monthly_forecast/latest`（每次请求发现库落后于结果 csv 就自动补一次，幂等 upsert）、或手动 `python backend/init_db.py`；**值没变时 `updated_at` 不会被刷新** |
| 时间戳 | `created_at` / `updated_at` 为 `DATETIME`（本机时区，无时区字段），由 MySQL 自动维护，不参与业务 |
| 时区 | 无时区概念，日期即行情数据日期（伦敦金交易日） |

> 单位换算提醒：本表概率**已是小数**，若页面要显示「22.93%」，
> 在应用层 `prob_up * 100` 保留 2 位即可，SQL 内不必再换算。
> `actual_return` 若要以「-8.61%」形式展示，同样 ×100，但务必标注是**对数收益**口径。

---

## 一、预测记录表

**表名**：`fmqt_gold_monthly_forecast`（黄金月频预测记录）

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | `INT UNSIGNED` | 自增主键 |
| `predict_date` | `DATE` | 预测基准日（数据日期，= 结果 csv 的日期，也是窗口内最后一个交易日） |
| `symbol` | `VARCHAR(32)` | 标的代码，默认 `XAUUSD` |
| `prob_up` | `DECIMAL(18,6)` | 未来一个月（T+22）上涨概率，`0 ~ 1` 小数 |
| `pred_direction` | `TINYINT` | 预测方向：`1` 看涨 / `0` 看跌 |
| `actual_direction` | `TINYINT` | 实际方向：`1` 涨 / `0` 跌（未兑现 `NULL`） |
| `actual_return` | `DECIMAL(18,6)` | 实际收益（T+22 对数收益率，未兑现 `NULL`） |
| `hit` | `TINYINT` | 是否命中：`1` 命中 / `0` 未命中（未兑现 `NULL`） |
| `created_at` | `DATETIME` | 首次落库时间（库自动维护） |
| `updated_at` | `DATETIME` | 最后更新时间（库自动维护，值不变时不刷新） |

索引：`PRIMARY KEY (id)`、`UNIQUE KEY uk_predict_date (predict_date)`、
`KEY ix_symbol_predict_date (symbol, predict_date DESC)`。

> **口径提示 1（NULL ≠ 未命中）**：统计命中率**必须**排除未兑现的期数——
> 用 `WHERE hit IS NOT NULL`，或用 `SUM(hit IS NOT NULL)` 当分母。
> 直接把 `hit` 当 0/1 全量求和会把待验证的期数算成"未命中"，命中率被人为拉低。
> 当前 1443 期里有 1421 期已兑现、22 期待验证。
>
> **口径提示 2（概率 ≠ 涨幅）**：`prob_up` 是模型输出的**方向概率**，
> 只能回答"涨不涨"的置信度，**不是预期涨幅**。要收益口径请用 `actual_return`
> （且只有已兑现的记录才有值）。
>
> **口径提示 3（样本量与波动）**：全样本命中率 **65.31%**（1421 期已兑现），
> 但分年区间为 **57.0% ~ 73.4%**，最近 30 期只有 **46.7%**——单看某一小段波动极大，
> 回答"模型准不准"时应说明观察区间与样本量，不要用几期数据下结论。
>
> **口径提示 4（最早一期是 2020-02-20）**：行情表从 2000 年就有数据，但预测记录
> 从 `2020-02-20` 起——模型需要先攒够训练样本才会给出第一条**样本外**预测，
> 它之前的日期**本来就没有预测记录**（结果 csv 里更早的行 `PRED_PROB` 为空，同理不入库），
> 不要当成数据缺失。（训练窗口等参数在**跑批侧**配置，本只读服务不涉及。）

**示例数据**（同时包含已兑现与未兑现两种形态）：

| predict_date | symbol | prob_up | pred_direction | actual_direction | actual_return | hit |
|---|---|---|---|---|---|---|
| 2026-10-09 | XAUUSD | 0.229329 | 0 | NULL | NULL | NULL |
| 2026-10-08 | XAUUSD | 0.206729 | 0 | NULL | NULL | NULL |
| 2026-09-30 | XAUUSD | 0.188703 | 0 | NULL | NULL | NULL |
| 2026-09-29 | XAUUSD | 0.197746 | 0 | NULL | NULL | NULL |
| 2026-09-28 | XAUUSD | 0.211097 | 0 | NULL | NULL | NULL |
| 2026-08-28 | XAUUSD | 0.249741 | 0 | 0 | -0.086108 | 1 |
| 2020-02-20 | XAUUSD | 0.852407 | 1 | 0 | -0.059552 | 0 |

### 1.1 最新预测日

**业务**：页面默认展示的最新一期；`predict_date` 参数留空时先取它。

**参数**：`symbol`（可选，默认 `XAUUSD`）

```sql
SELECT MAX(`predict_date`) AS d
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD';
```

**返回**：`d`（`DATE`）

> `MAX(predict_date)` 走 `uk_predict_date` 索引，毫秒级；**不要** `ORDER BY ... LIMIT 1` 全表扫描。

### 1.2 最新已兑现预测日

**业务**：做「最近实际表现」类回答时的"最新可用验证日期"——它通常**早于**最新预测日。

**参数**：`symbol`（可选）

```sql
SELECT MAX(`predict_date`) AS d
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NOT NULL;
```

**返回**：`d`（`DATE`）。示例库：最新预测日 `2026-10-09`，最新已兑现日 `2026-08-28`。

> 月频窗口是 22 个交易日，所以两者相差约 22 个交易日（≈ 一个月），比周频大得多；
> 这一段时间里的预测**只有概率和方向、没有实际值**。

### 1.3 上一可用预测日

**业务**：计算「较上一期」的环比。给定日期，取其之前最近的一个预测日。

**参数**：`symbol`（可选）、`predict_date`

```sql
SELECT MAX(`predict_date`) AS d
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `predict_date` < '2026-10-09';
```

> 不要用「取全部日期再在应用里找索引」的写法，那会全表扫描；
> 这条走 `ix_symbol_predict_date` 索引，毫秒级。
> 另外「上一期」是**交易日相邻**，会跨周末、假期、月份（如 `2026-09-30` → `2026-10-08`），
> 不能用 `DATE_SUB(?, INTERVAL 1 DAY)` 推算。

### 1.4 最近 N 个预测日（倒序）

**业务**：趋势图 / 时间轴的日期轴；日期选择器的可选范围。

**参数**：`symbol`（可选）、`days`

```sql
SELECT `predict_date` AS d
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
ORDER BY `predict_date` DESC
LIMIT 30;
```

**返回**：`d`（`DATE`）

### 1.5 最近 N 期明细（倒序）

**业务**：历史明细表（最常用的"最近几期预测成什么样"）。

**参数**：`symbol`（可选）、`limit`

```sql
SELECT `predict_date`, `prob_up`, `pred_direction`,
       `actual_direction`, `actual_return`, `hit`
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
ORDER BY `predict_date` DESC
LIMIT 30;
```

**返回**：`predict_date` + 概率 / 预测方向 / 实际方向 / 实际收益 / 是否命中

> 未兑现的期数后三列为 `NULL`，前端展示「待验证」；不要在 SQL 里用 `IFNULL(...,0)` 抹平。

### 1.6 某一期的预测详情

**业务**：核心查询。点击某一期展开详情，或"某天模型怎么说"。

**参数**：`predict_date`、`symbol`（可选）

```sql
SELECT `predict_date`, `prob_up`, `pred_direction`, `actual_direction`, `actual_return`,
       `hit`, `created_at`, `updated_at`
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `predict_date` = '2026-08-28';
```

### 1.7 未兑现（待验证）的预测

**业务**：回答"模型最近怎么看"——最近这几期还没有 22 个交易日的实际数据。

**参数**：`symbol`（可选）、`limit`

```sql
SELECT `predict_date`, `prob_up`, `pred_direction`
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NULL
ORDER BY `predict_date` DESC
LIMIT 10;
```

**返回**：`predict_date`、`prob_up`、`pred_direction`（无实际值，故不返回实际列）

> 当前库里有 22 期待验证（最新 `2026-10-09`，`prob_up = 0.229329`，预测看跌）。

### 1.8 整体命中率

**业务**：最常被问的"这个模型准不准"。

**参数**：`symbol`（可选）

```sql
SELECT COUNT(*)                    AS total,
       SUM(`hit` IS NOT NULL)      AS verified,
       SUM(`hit` = 1)              AS hits,
       ROUND(SUM(`hit` = 1) / NULLIF(SUM(`hit` IS NOT NULL), 0), 4) AS hit_rate
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD';
```

**返回**：`total`（全部期数）、`verified`（已兑现）、`hits`、`hit_rate`（小数）

示例结果：`total=1443`、`verified=1421`、`hits=928`、`hit_rate=0.6531`

> `NULLIF(...,0)` 不能省：表为空或全部未兑现时避免除零。
> `SUM(hit IS NOT NULL)` 里 `hit` 为 `NULL` 时该表达式是 `0`，正好当分母，不必写 `SUM(hit=1)+SUM(hit=0)`。

### 1.9 按年命中率

**业务**：看模型稳定性 / 是否某段时间失效。

**参数**：`symbol`（可选）

```sql
SELECT YEAR(`predict_date`)     AS yr,
       COUNT(*)                 AS total,
       SUM(`hit` IS NOT NULL)   AS verified,
       ROUND(AVG(`hit`), 4)     AS hit_rate
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
GROUP BY yr
ORDER BY yr DESC;
```

**返回**：`yr`、`total`、`verified`、`hit_rate`

示例结果：

| yr | total | verified | hit_rate |
|---|---|---|---|
| 2026 | 176 | 154 | 0.7338 |
| 2025 | 215 | 215 | 0.7209 |
| 2024 | 222 | 222 | 0.6847 |
| 2023 | 225 | 225 | 0.5733 |
| 2022 | 219 | 219 | 0.6804 |

> `AVG(hit)` 天然跳过 `NULL`，所以这里的 `hit_rate` 分母是**已兑现**期数，
> 与 `SUM(hit=1)/COUNT(*)` 不同——后者会把待验证期数当未命中。

### 1.10 概率分档命中率（模型校准）

**业务**：检查概率是否"可用"——高概率档的命中率是否真的更高。

**参数**：`symbol`（可选）

```sql
SELECT CASE
         WHEN `prob_up` <  0.30 THEN '1 [0.00, 0.30)'
         WHEN `prob_up` <  0.50 THEN '2 [0.30, 0.50)'
         WHEN `prob_up` <  0.70 THEN '3 [0.50, 0.70)'
         ELSE                        '4 [0.70, 1.00]' END AS prob_bucket,
       COUNT(*)              AS n,
       ROUND(AVG(`hit`), 4)  AS hit_rate
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NOT NULL
GROUP BY prob_bucket
ORDER BY prob_bucket;
```

**返回**：`prob_bucket`、`n`、`hit_rate`

示例结果：

| prob_bucket | n | hit_rate |
|---|---|---|
| 1 [0.00, 0.30) | 136 | 0.6691 |
| 2 [0.30, 0.50) | 345 | 0.6058 |
| 3 [0.50, 0.70) | 646 | 0.6238 |
| 4 [0.70, 1.00] | 294 | 0.7653 |

> 分档边界 `0.30 / 0.70` 取的是月频页面「强势下跌 / 强势上涨」的分档线（固定口径），
> 改边界只影响呈现，不影响模型本身。
> 注意这不是单调曲线：最低档（很强的看跌信号）与最高档（很强的看涨信号）都高于中间档，
> 最不稳的是概率接近 0.5 的中间档——**两端的高置信度区间才是概率的价值所在**。

### 1.11 多空分组统计

**业务**：模型是偏多还是偏空、两个方向各自的准确率。

**参数**：`symbol`（可选）　**要求 MySQL 8.0+**（用了窗口函数算占比）

```sql
SELECT `pred_direction`,
       COUNT(*)                                   AS n,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS share,
       ROUND(SUM(`hit` = 1) / COUNT(*), 4)        AS hit_rate
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NOT NULL
GROUP BY `pred_direction`
ORDER BY `pred_direction` DESC;
```

**MySQL 5.7 兼容写法**（把窗口函数换成标量子查询）：

```sql
SELECT `pred_direction`,
       COUNT(*)                                   AS n,
       ROUND(COUNT(*) / (SELECT COUNT(*) FROM `fmqt_gold_monthly_forecast`
                         WHERE `symbol` = 'XAUUSD' AND `hit` IS NOT NULL), 4) AS share,
       ROUND(SUM(`hit` = 1) / COUNT(*), 4)        AS hit_rate
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NOT NULL
GROUP BY `pred_direction`
ORDER BY `pred_direction` DESC;
```

**返回**：`pred_direction`、`n`、`share`、`hit_rate`

示例结果：（1 = 看涨，0 = 看跌）

| pred_direction | n | share | hit_rate |
|---|---|---|---|
| 1（看涨） | 911 | 0.6411 | 0.6970 |
| 0（看跌） | 510 | 0.3589 | 0.5745 |

> 不要写成 `SUM(hit = pred_direction)`：当 `pred_direction = 0`（看跌）时，
> 未命中的行 `hit = 0` 也等于 `pred_direction`，会被错算成 1，看跌那一组的命中率直接虚高。
> 命中与否只看 `hit = 1`。

### 1.12 高置信度子集命中率（阈值敏感性）

**业务**：回答"只在模型很有把握时跟，准确率多少"——即"要不要设阈值"。

**参数**：`symbol`（可选）、`hi`（看涨阈值，示例 0.70）、`lo`（看跌阈值，示例 0.30）

```sql
SELECT CASE
         WHEN `prob_up` >= 0.70 THEN 'prob_up >= 0.70'
         WHEN `prob_up` <= 0.30 THEN 'prob_up <= 0.30'
         ELSE                        '其它' END  AS subset,
       COUNT(*)                                 AS n,
       ROUND(SUM(`hit` = 1) / COUNT(*), 4)      AS hit_rate
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NOT NULL
GROUP BY subset
ORDER BY subset;
```

**返回**：`subset`、`n`、`hit_rate`

示例结果：

| subset | n | hit_rate |
|---|---|---|
| prob_up <= 0.30 | 136 | 0.6691 |
| prob_up >= 0.70 | 294 | 0.7653 |
| 其它 | 991 | 0.6176 |

> 阈值是**口径**不是结论：`0.70 / 0.30` 就是月频页面强弱信号的分档线，
> 反复调阈值刷同一批历史数据会过拟合，回答时要说清这是事后统计，不是实盘表现。

### 1.13 最近 N 期滚动命中率

**业务**：看模型"最近状态"——有没有突然变差。**要求 MySQL 8.0+**。

**参数**：`symbol`（可选）、`window`（滚动窗口，示例 50）

```sql
SELECT `predict_date`,
       `hit`,
       ROUND(AVG(`hit`) OVER (ORDER BY `predict_date`
                              ROWS BETWEEN 49 PRECEDING AND CURRENT ROW), 4) AS hit_rate_50
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NOT NULL
ORDER BY `predict_date` DESC
LIMIT 30;
```

**返回**：`predict_date`、`hit`、`hit_rate_50`（含当期在内的前 50 期命中率）

示例结果（最近 3 期）：

| predict_date | hit | hit_rate_50 |
|---|---|---|
| 2026-08-28 | 1 | 0.5200 |
| 2026-08-27 | 1 | 0.5000 |
| 2026-08-26 | 1 | 0.4800 |

> 窗口大小改了就换了口径：50 期约等于 2.4 个月（每期是 22 个交易日的预测，但**每期一行**）。
> 首 49 期只有部分前置数据，结果会偏抖动，不要拿开头几期下结论。

### 1.14 连续命中 / 连续失误区间

**业务**：「最近是不是一直在错」——连击与连错的最长纪录。**要求 MySQL 8.0+**。

**参数**：`symbol`（可选）、`limit`

```sql
SELECT `hit`,
       MIN(`predict_date`) AS run_start,
       MAX(`predict_date`) AS run_end,
       COUNT(*)            AS run_len
FROM (
  SELECT `predict_date`, `hit`,
         ROW_NUMBER() OVER (ORDER BY `predict_date`)
       - ROW_NUMBER() OVER (PARTITION BY `hit` ORDER BY `predict_date`) AS grp
  FROM `fmqt_gold_monthly_forecast`
  WHERE `symbol` = 'XAUUSD'
    AND `hit` IS NOT NULL
) t
GROUP BY `hit`, grp
ORDER BY run_len DESC, run_end DESC
LIMIT 10;
```

**返回**：`hit`、`run_start`、`run_end`、`run_len`

示例结果（最长连击 60 期）：

| hit | run_start | run_end | run_len |
|---|---|---|---|
| 1 | 2025-10-27 | 2026-01-27 | 60 |
| 1 | 2023-09-06 | 2023-11-28 | 52 |
| 1 | 2024-01-17 | 2024-04-03 | 48 |
| 1 | 2021-03-02 | 2021-05-13 | 46 |
| 1 | 2026-04-08 | 2026-06-15 | 45 |

**当前连续期数**（同一个思路，取最后一组）：

```sql
SELECT `hit`, COUNT(*) AS cur_len, MAX(`predict_date`) AS through_date
FROM (
  SELECT `predict_date`, `hit`,
         @g := IF(@prev = `hit`, @g, @g + 1) AS grp,
         @prev := `hit`                       AS _prev
  FROM `fmqt_gold_monthly_forecast`, (SELECT @g := 0, @prev := NULL) v
  WHERE `symbol` = 'XAUUSD'
    AND `hit` IS NOT NULL
  ORDER BY `predict_date`
) t
GROUP BY `hit`, grp
ORDER BY through_date DESC
LIMIT 1;
```

示例结果：`hit=1`、`cur_len=14`、`through_date=2026-08-28`（当前连续命中 14 期）

> 上面这段用会话变量，**MySQL 5.7 / 8.0 都能跑**，但依赖执行顺序，
> 不适合放进并发连接池里复用；优先用窗口函数版本。
> 连击长不代表模型变强：月频样本本身高度重叠（相邻两期的预测窗口重合 21 个交易日），
> 所以"连续命中 60 期"约等于"同一波行情判断对了 3 个月"，不是 60 个独立事件。

### 1.15 实际收益分档分布

**业务**：模型在什么行情里更容易对——大涨大跌 vs 窄幅震荡。

**参数**：`symbol`（可选）、分档阈值（示例 ±5%）

```sql
SELECT CASE
         WHEN `actual_return` < -0.05 THEN '1 跌超 5%'
         WHEN `actual_return` <  0.00 THEN '2 小跌'
         WHEN `actual_return` <  0.05 THEN '3 小涨'
         ELSE                              '4 涨超 5%' END AS bucket,
       COUNT(*)             AS n,
       ROUND(AVG(`hit`), 4) AS hit_rate
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD'
  AND `hit` IS NOT NULL
GROUP BY bucket
ORDER BY bucket;
```

**返回**：`bucket`、`n`、`hit_rate`

示例结果：

| bucket | n | hit_rate |
|---|---|---|
| 1 跌超 5% | 137 | 0.7007 |
| 2 小跌 | 432 | 0.4560 |
| 3 小涨 | 498 | 0.6988 |
| 4 涨超 5% | 354 | 0.8107 |

> 阈值里的 `0.05` 是**对数收益率**口径；月频是 22 个交易日的累计波动，
> `±5%` 大致就是"小涨小跌"与"大涨大跌"的分界（全样本实际收益区间约 `-14.9% ~ +19.5%`）。
> 两端的命中率明显高于"小跌"档——趋势行情里模型更可靠，窄幅震荡里最容易判错方向。

### 1.16 库覆盖区间与更新时点

**业务**：确认"数据到哪天""什么时候写进去的"，用于回答数据新鲜度。

**参数**：`symbol`（可选）

```sql
SELECT COUNT(*)               AS total,
       SUM(`hit` IS NOT NULL) AS verified,
       MIN(`predict_date`)    AS first_date,
       MAX(`predict_date`)    AS last_date,
       MAX(`updated_at`)      AS last_written
FROM `fmqt_gold_monthly_forecast`
WHERE `symbol` = 'XAUUSD';
```

**返回**：`total`、`verified`、`first_date`、`last_date`、`last_written`

示例结果：`1443 / 1421 / 2020-02-20 / 2026-10-09 / 2026-10-14 10:16:22`

> 判断"库是不是落后于跑批产物"时，用 `MAX(predict_date)` 与结果文件名
> `result_XAU_monthly_<END_DATE>.csv` 里的日期比对，而不是看 `updated_at`。

### 1.17 重复数据自检

**业务**：唯一索引的冗余校验，用于数据治理（正常返回空集）。

```sql
SELECT `symbol`, `predict_date`, COUNT(*) AS n
FROM `fmqt_gold_monthly_forecast`
GROUP BY `symbol`, `predict_date`
HAVING COUNT(*) > 1;
```

**返回**：空集表示无重复。

---

## 二、MCP 工具映射建议

把上述 SQL 封装为 MCP 工具的建议：工具划分、入参定义与实现注意点。

### 2.1 设计原则

| 原则 | 说明 |
|---|---|
| **按业务能力划分，不按接口照搬** | 一个工具对应一个「用户会问的问题」。例如"这模型准不准"是一个工具，内部只用一条 SQL |
| **日期统一 `YYYY-MM-DD` 字符串** | 与 `DATE` 列一致；留空表示取最新预测日 |
| **枚举值收窄** | `symbol` 目前只有 `XAUUSD`；`direction` 只允许 `1`/`0`。凡是要拼进 SQL 的**列名**必须白名单校验 |
| **参数一律走预编译占位符** | 绝不用字符串拼接，防止 SQL 注入（仅**列名 / 排序方向**需白名单映射） |
| **数值类参数设上下界** | 如 `days` 限 1~250、`limit` 限 1~200，避免 `LIMIT -5` 之类的语法错误 |
| **返回结构化 + 注明单位** | 明确标注 `prob_up` 是"小数（概率口径）"、`actual_return` 是"T+22 对数收益率"，避免模型当成亿元或百分点 |
| **默认排除未兑现记录** | 任何涉及 `hit` 的统计都要带 `hit IS NOT NULL`，由工具内部固化，不交给调用方决定 |
| **单次工具调用对应 1 条 SQL** | 保持 <1 秒；不要在一个工具里做多表 JOIN 或大范围循环 |

### 2.2 工具清单

统一约定：`predict_date` 为 `YYYY-MM-DD` 字符串，留空取该标的**最新预测日**；
`symbol` 留空默认 `XAUUSD`。「对应 SQL」列指向本文档前面的章节号。

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_gold_monthly_latest` | 最新一期预测（日期 + 概率 + 方向） | `symbol?` | 1.1 → 1.6 |
| `get_gold_monthly_verified_date` | 最新**已兑现**预测日 | `symbol?` | 1.2 |
| `get_gold_monthly_prev_date` | 上一可用预测日（环比用） | `symbol?`, `predict_date?` | 1.3 |
| `list_gold_monthly_dates` | 最近 N 个预测日 | `symbol?`, `days?`(1-250) | 1.4 |
| `list_gold_monthly_history` | 预测明细（倒序） | `symbol?`, `limit?`(1-200) | 1.5 |
| `get_gold_monthly` | 某一期预测详情（含实际验证） | `symbol?`, `predict_date?` | 1.6 |
| `list_gold_monthly_pending` | 未兑现（待验证）的预测 | `symbol?`, `limit?`(1-50) | 1.7 |
| `get_gold_monthly_accuracy` | 整体命中率（含样本量） | `symbol?` | 1.8 |
| `get_gold_monthly_accuracy_by_year` | 按年命中率 | `symbol?` | 1.9 |
| `get_gold_monthly_calibration` | 概率分档 vs 命中率（校准） | `symbol?` | 1.10 |
| `get_gold_monthly_by_direction` | 多空分组命中率与占比 | `symbol?` | 1.11 |
| `get_gold_monthly_confidence` | 高置信度子集命中率（阈值敏感性） | `symbol?`, `hi?`(0.5-1), `lo?`(0-0.5) | 1.12 |
| `get_gold_monthly_rolling_accuracy` | 滚动 N 期命中率 | `symbol?`, `window?`(5-250), `limit?` | 1.13 |
| `get_gold_monthly_streak` | 连续命中 / 连续失误区间 | `symbol?`, `limit?`(1-50) | 1.14 |
| `get_gold_monthly_return_buckets` | 实际收益分档命中率 | `symbol?` | 1.15 |
| `get_gold_monthly_coverage` | 库覆盖区间与更新时点 | `symbol?` | 1.16 |

### 2.3 工具定义示例（可直接用作 schema 模板）

```json
{
  "name": "get_gold_monthly",
  "description": "查询黄金（XAUUSD）某一期的月频方向预测及事后验证结果。prob_up 为未来一个月（22 个交易日）上涨概率（小数，0.229329 表示 22.93%）；pred_direction / actual_direction 中 1 表示涨、0 表示跌；actual_return 为 T+22 对数收益率（小数，-0.086108 表示跌 8.61%）；hit 为是否命中，未兑现时为 null。predict_date 留空则取最新预测日。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol": {
        "type": "string",
        "enum": ["XAUUSD"],
        "description": "标的代码；留空默认 XAUUSD"
      },
      "predict_date": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "预测基准日，格式 YYYY-MM-DD；留空则取最新预测日"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_gold_monthly_accuracy",
  "description": "查询黄金月频预测模型的历史方向命中率。返回全部期数、已兑现期数、命中数与命中率。命中率只在已兑现（hit 非 null）的期数上计算，因为最近约 22 期尚未满 22 个交易日、还没有实际结果。注意：全样本命中率约 0.65，但分年区间为 0.57~0.73、最近 30 期只有 0.47，回答时应同时给出样本量与观察区间。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol": {
        "type": "string",
        "enum": ["XAUUSD"],
        "description": "标的代码；留空默认 XAUUSD"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_gold_monthly_calibration",
  "description": "查询不同上涨概率档次对应的实际命中率，用于判断预测概率是否具备区分度。按 prob_up 分成 [0,0.30)、[0.30,0.50)、[0.50,0.70)、[0.70,1.00] 四档，返回每档样本数与命中率。注意曲线不是单调的：两端（很强的看跌 / 看涨信号）命中率都高于中间档，越靠近 0.5 越不稳。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol": {
        "type": "string",
        "enum": ["XAUUSD"],
        "description": "标的代码；留空默认 XAUUSD"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

### 2.4 实现注意事项

1. **NULL ≠ 未命中，工具层必须固化过滤**
   最近约 22 期没有 `hit` 值（还没满 22 个交易日）。所有命中率类 SQL 都要带
   `hit IS NOT NULL`。这件事由工具内部完成，不要暴露成调用方的可选项——
   否则很容易算出"65% 掉到 59%"的假信号。

2. **`predict_date` 数值列用占位符，列名 / 排序方向用白名单**
   `predict_date`、`symbol`、`days`、`limit` 全部走 `%s` 预编译。
   如果工具开放了"按某列排序"，那是**列名拼进 SQL**，必须映射白名单。

3. **单位在工具层说清，别让模型猜**
   - `prob_up`：小数概率，`0.229329 = 22.93%`；
   - `actual_return`：**对数收益率**，`-0.086108 = 跌 8.61%`；
   - `hit` / `pred_direction`：`1`/`0`，不是布尔、不是 `+1/-1`。
   建议在字段说明里写清示例，二选一并保持一致；不要一处返回小数、一处返回百分数。

4. **区分"最新预测日"与"最新已兑现日"**
   月频两者相差约 22 个交易日（≈ 一个月）。用户问"最近预测怎么样"→ 1.1 + 1.7；
   问"最近准不准"→ 1.2 + 1.13。混用会答错。

5. **不要用 LIMIT 兜底全量**
   "历史命中率"必须是全表统计（1.8），不能拿 `LIMIT 100` 的结果冒充；
   而"最近几期明细"才用 `LIMIT`。两者语义不同，工具也要分成两个。

6. **连接池与超时**
   建议连接池 ≥10、单查询超时 1~2 秒。本表规模小（约 1500 行）且
   `uk_predict_date` / `ix_symbol_predict_date` 可命中，实测全部 SQL 在毫秒级。

7. **只读即可**
   建议 MCP Server 使用**只读数据库账号**：本文档全部是 SELECT 查询语句，
   数据写入由站点自身的 `GET /research/gold_monthly_forecast/latest`（读产物时顺手落库）
   或手动 `python backend/init_db.py` 完成，MCP 侧不需要写权限。

8. **表名前缀与跨表注意**
   同一实例中本表以 `fmqt_gold_` 前缀命名（`fmqt_gold_monthly_forecast`），
   与平台其它表（如 `fmqt_gold_weekly_forecast` 黄金周频预测、`fmqt_gold_analysis_result`
   黄金多因子归因）保持一致的命名风格。这几张表的日期列都是交易日、可以按日期关联，
   但**口径不同**：本表一行 = 一个预测基准日（预测窗口 22 个交易日）、周频表一行 = 一个预测基准日
   （窗口 5 个交易日）、归因表一行 = 一个「模型 + 数据日期」，
   所以按日期 JOIN 前**必须先过滤标的 / 模型**，否则行数会翻倍、口径也会串。

---

## 附：与站点接口的对应关系

站点后端只有**两个接口**：

| 站点接口 | 作用 | 对应本文档 |
|---|---|---|
| `GET /research/gold_monthly_forecast/latest` | **给前端**：读产物目录拼出最新一期预测（含走势图与概率区间），并顺手把结果 csv 落一次库（**表数据的写入来源**） | 1.1 + 1.6 的数据来源；落库后的数据对应 1.5 + 1.8 |
| `GET /research/gold_monthly_forecast/records?limit=` | **读数据库**：历史预测验证记录（纯读 MySQL，不读 csv、不写库；库连不上返回 503） | 1.5 + 1.8 |

> 落库口径与页面展示口径一致：结果 csv 的一行对应本表一行，
> 按 `predict_date` upsert（`ON DUPLICATE KEY UPDATE`），重复落库不会产生重复行。
> 库里数据的**新鲜度**取决于 `/latest` 有没有被请求过 —— MCP 侧若发现库落后于
> 结果 csv，调一次 `/latest`（或跑 `python backend/init_db.py`）即可补齐。
