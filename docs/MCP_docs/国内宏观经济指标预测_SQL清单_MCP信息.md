# 国内宏观经济指标预测看板 · SQL 参考（供 MCP 接入）

本文档整理「国内宏观经济指标预测」平台用到的**全部查询 SQL**，按**业务场景**组织。
每个场景包含：详细业务介绍、涉及表与字段、可直接执行的 SQL、返回字段。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 预测月份格式 | `YYYY-MM` 字符串（如 `2026-06`），**业务主键**，一个月一份报告 |
| 报告 / 指标 ID | `report_id`（报告主键）、`indicator_id`（指标主键），均由数据库自增生成 |
| 预测值口径 | `pred` = 模型预测值；`prior` = 前值（上一期实际值）；`market_pred` = 市场一致预期；`zhangyu` = 张瑜团队预测值 |
| 模型拟合度 | `r2` = Top1 模型的 R²，越接近 1 表示模型对该指标的解释力越强 |
| 板块取值 | `增长` / `通胀` / `信用` / `外贸` / `其他`，**展示顺序固定**为 增长→通胀→信用→外贸→其他 |
| 数值类型 | `DECIMAL(20,4)`，SQL 内统一 `CAST(... AS DOUBLE)`，避免 Decimal 无法 JSON 序列化 |
| 空值语义 | `market_pred`、`zhangyu` 为手填列，可为空（NULL）；`pred` 为空表示模型未产出 |
| 生效标记 | `is_active = 1` 生效、`0` 失效，查询时建议带上 `is_active = 1` |
| 大模型分析状态 | `pending`（生成中）/ `done`（已完成）/ `error`（失败），取用时**必须先看状态** |
| 展示顺序 | 板块顺序由 SQL 内的 `CASE` 控制，不要改成字典序 |

> 库内 6 张表统一前缀 `fmqt_forecast_`，建表语句见 `schema.sql`，SQL 实现见 `db.py`。
> 表间**无外键约束**，关联靠 `report_id` / `indicator_id` 列约定，由应用层保证完整性。

---

## 一、报告检索与定位

**业务背景**：，一份报告对应一个**预测月份**（如 2026-06），
包含该月全部宏观指标的预测结果。预测月份是**业务主键**（唯一），用户和 AI 侧的提问通常都从「某年某月」开始，
因此第一步永远是**把预测月份解析成 report_id**，后续所有指标查询都以 report_id 为入口。
本场景回答：「有哪些月份的预测报告」「2026-06 的报告有没有」「这份报告的数据截止到什么时候」「数据是全的吗」。

**表名**：`fmqt_forecast_report_meta`（报告元数据）

| 字段 | 含义 |
|---|---|
| `id` | 报告主键（即 report_id） |
| `title` | 报告标题 |
| `forecast_month` | 预测目标月份 `YYYY-MM`，**唯一** |
| `data_cutoff` | 数据截止月（口径说明：预测所基于的数据最新到哪个月） |
| `generated_at` | 报告生成时间 |
| `indicator_count` | 指标数量（落库时冗余计数） |
| `model_pool` | 模型池描述（本次预测使用了哪些模型） |
| `narrative_summary` | 报告摘要（预留字段） |
| `the_status` | 状态：`ingesting`（解析中）/ `done`（完成可用） |

> 口径提示：只有 `the_status = 'done'` 的报告才代表数据落库完整，回答结论前建议先确认状态。

### 1.1 全部报告月份清单（倒序）

**业务**：列出库中所有已生成的报告，供 AI 回答「有哪些月份的数据可用」「最近一次报告是哪个月」。
只返回清单，不含指标明细；拿到 report_id 或 forecast_month 后再查指标。

```sql
SELECT id AS report_id, forecast_month, title, indicator_count,
       data_cutoff, generated_at, the_status AS status
FROM fmqt_forecast_report_meta
ORDER BY forecast_month DESC;
```

### 1.2 按预测月份定位报告（★ 主入口）

**业务**：把「预测月份」解析为内部 `report_id`，这是**查询某月数据的第一步**。
前端「一图速览」接口正是以 `forecast_month` 为入参，后端先用这条 SQL 定位报告，
再用 report_id 一次性拉取该月全部指标与宏观综览。若查不到，说明该月尚未上传数据。

```sql
SELECT id AS report_id, forecast_month, title, data_cutoff,
       generated_at, indicator_count, model_pool, the_status AS status
FROM fmqt_forecast_report_meta
WHERE forecast_month = '2026-06'
ORDER BY id DESC
LIMIT 1;
```

**参数**：`forecast_month` = `'2026-06'`

### 1.3 按 report_id 查报告元信息

**业务**：已知 report_id 时查看报告的元信息与数据完备程度，用于说明
「这份报告的数据截止到几月」「这次预测用了哪些模型」「数据是完整的吗」。

```sql
SELECT id AS report_id, title, forecast_month, data_cutoff, generated_at,
       indicator_count, model_pool, narrative_summary, the_status AS status
FROM fmqt_forecast_report_meta
WHERE id = 1;
```

**参数**：`id` = `1`

**返回**：`report_id`、`forecast_month`、`data_cutoff`、`generated_at`、`model_pool`、`indicator_count`、`status`

---

## 二、一图速览：某月全部指标

**业务背景**：「一图速览」是平台的主页面，用一组指标卡片展示**某个月全部宏观指标的预测全貌**：
每个指标同时给出前值、模型预测值、市场一致预期、张瑜团队预测值和模型拟合优度 R²，
让分析师一眼看出「模型预测与市场预期差多少」「相比前值是升还是降」。
指标按板块（增长 / 通胀 / 信用 / 外贸）分组展示，**顺序固定**。
本场景回答：「2026-06 的整体预测情况如何」「各项指标的预测值分别是多少」「哪些指标模型预测高于市场预期」。

**表名**：`fmqt_forecast_indicator`（指标，一图速览的每个卡片）

| 字段 | 含义 |
|---|---|
| `id` | 指标主键（即 indicator_id） |
| `report_id` | 所属报告 id |
| `target_name` | 指标名（业务关联键，wind target 名，单报告内唯一） |
| `group_cat` | 板块：`增长` / `通胀` / `信用` / `外贸` / `其他` |
| `market_pred` | 市场一致预期（手填列，可为空） |
| `the_prior` | 前值（上一期实际值） |
| `pred` | 模型预测值 |
| `r2` | Top1 模型 R² |
| `zhangyu` | 张瑜团队预测值（手填列，可为空） |
| `is_active` | 是否生效：`1` 生效，`0` 失效 |

### 2.1 某月全部指标（按板块固定顺序）

**业务**：取一份报告的全部指标卡片，结果已按 增长→通胀→信用→外贸→其他 排序，
可直接用于生成看板式总结。**排序必须用 `CASE`，不能用字典序**，否则板块顺序会乱。

```sql
SELECT id AS indicator_id, target_name AS name, group_cat,
       CAST(market_pred AS DOUBLE) AS market_pred,
       CAST(the_prior   AS DOUBLE) AS prior,
       CAST(pred        AS DOUBLE) AS pred,
       CAST(r2          AS DOUBLE) AS r2,
       CAST(zhangyu     AS DOUBLE) AS zhangyu
FROM fmqt_forecast_indicator
WHERE report_id = 1 AND is_active = 1
ORDER BY
  CASE group_cat
    WHEN '增长' THEN 1
    WHEN '通胀' THEN 2
    WHEN '信用' THEN 3
    WHEN '外贸' THEN 4
    ELSE 5
  END,
  id;
```

**参数**：`report_id` = `1`

**返回**：`indicator_id`、`name`、`group_cat`、`market_pred`、`prior`、`pred`、`r2`、`zhangyu`

### 2.2 报告指标分布统计

**业务**：统计某份报告覆盖多少指标、各板块分别多少个，用于给出概括性结论
（如「这份报告覆盖 32 个指标，其中增长 10 个、通胀 8 个……」）。也可用于校验
`report_meta.indicator_count` 冗余字段是否与实际一致。

```sql
SELECT group_cat, COUNT(*) AS cnt
FROM fmqt_forecast_indicator
WHERE report_id = 1 AND is_active = 1
GROUP BY group_cat
ORDER BY CASE group_cat
           WHEN '增长' THEN 1 WHEN '通胀' THEN 2
           WHEN '信用' THEN 3 WHEN '外贸' THEN 4 ELSE 5 END;
```

**参数**：`report_id` = `1`

**返回**：`group_cat`、`cnt`

---

## 三、指标筛选：按板块 / 按名称定位指标

**业务背景**：指标的内部主键是 `indicator_id`，但用户和 AI 通常只知道**板块**（「看看通胀这组」）
或**指标名称**（「CPI 预测多少」「社零怎么样」）。本场景提供两条定位路径：
按板块过滤、按名称模糊搜索。定位到 `indicator_id` 后，才能进入第四、五、六场景查看明细。
典型问法：「2026-06 的通胀指标有哪些？」「帮我找一下社零这个指标」「有哪些和出口相关的指标？」

**表名**：`fmqt_forecast_indicator`（字段见第二章），按名称搜索时需 JOIN `fmqt_forecast_report_meta`

### 3.1 按板块筛选指标

**业务**：取某份报告中某个板块（增长 / 通胀 / 信用 / 外贸 / 其他）下的全部指标，
避免 AI 拉全量后自行过滤。用于回答「某月的通胀类指标有哪些」「外贸这组预测如何」。

```sql
SELECT id AS indicator_id, target_name AS name, group_cat,
       CAST(market_pred AS DOUBLE) AS market_pred,
       CAST(the_prior   AS DOUBLE) AS prior,
       CAST(pred        AS DOUBLE) AS pred,
       CAST(r2          AS DOUBLE) AS r2,
       CAST(zhangyu     AS DOUBLE) AS zhangyu
FROM fmqt_forecast_indicator
WHERE report_id = 1 AND group_cat = '通胀' AND is_active = 1
ORDER BY id;
```

**参数**：`report_id` = `1`、`group_cat` = `'通胀'`（可选 增长 / 通胀 / 信用 / 外贸 / 其他）

### 3.2 按指标名称模糊搜索（跨报告）

**业务**：提供「指标名称 → indicator_id」的检索入口，支持跨报告、模糊匹配。
由于一份报告包含一组完整指标，同一指标名会在多个月份各出现一次，因此结果**按预测月份倒序**返回，
调用方需按 `forecast_month` 选择目标月份（通常取第一行 = 最新月份）。
建议先如实匹配名称，再用返回的 indicator_id 查明细。

```sql
SELECT i.id AS indicator_id, i.report_id, m.forecast_month,
       i.target_name AS name, i.group_cat,
       CAST(i.market_pred AS DOUBLE) AS market_pred,
       CAST(i.the_prior   AS DOUBLE) AS prior,
       CAST(i.pred        AS DOUBLE) AS pred,
       CAST(i.r2          AS DOUBLE) AS r2,
       CAST(i.zhangyu     AS DOUBLE) AS zhangyu
FROM fmqt_forecast_indicator i
JOIN fmqt_forecast_report_meta m ON m.id = i.report_id
WHERE i.target_name LIKE '%CPI%' AND i.is_active = 1
ORDER BY m.forecast_month DESC, i.id;
```

**参数**：`target_name` = `'%CPI%'`（支持 `%` 通配符，如 `CPI`、`%PPI%`、`%出口%`）

**返回**：`indicator_id`、`report_id`、`forecast_month`、`name`、`group_cat`、`market_pred`、`prior`、`pred`、`r2`、`zhangyu`

---

## 四、单指标快照：这个指标本月预测是多少

**业务背景**：分析师最常问的是「某个具体指标这个月预测多少」。本场景返回单个指标的**完整数值快照**：
前值（上一期实际）、模型预测值、市场一致预期、张瑜团队预测值，以及模型拟合优度 R²。
这四个数值放在一起来看才有意义：
`pred` 与 `market_pred` 的差 = 模型与市场预期的分歧；
`pred` 与 `prior` 的差 = 预测的方向性变动（升 / 降）；
`r2` 低则说明该指标模型解释力弱，结论需谨慎。
前端点击指标卡片时，**会同时取 4.1 + 5.1 + 6.1 + 6.2 四条 SQL 组装成完整详情**返回。

**表名**：`fmqt_forecast_indicator`（字段见第二章）

### 4.1 单指标基础信息

**业务**：按下钻某指标时取该指标行本身的全部数值字段。

```sql
SELECT id AS indicator_id, report_id, target_name AS name, group_cat,
       CAST(market_pred AS DOUBLE) AS market_pred,
       CAST(the_prior   AS DOUBLE) AS prior,
       CAST(pred        AS DOUBLE) AS pred,
       CAST(r2          AS DOUBLE) AS r2,
       CAST(zhangyu     AS DOUBLE) AS zhangyu,
       is_active
FROM fmqt_forecast_indicator
WHERE id = 1;
```

**参数**：`indicator_id` = `1`

**返回**：`indicator_id`、`name`、`group_cat`、`market_pred`、`prior`、`pred`、`r2`、`zhangyu`、`is_active`

---

## 五、多模型预测对比

**业务背景**：每个指标并非由单一模型预测，而是由**模型池中的多个模型分别预测**后按综合表现排名。
本场景展示该指标下各模型的预测结果：模型名、综合排名、各自预测值、前值、以及预测变动 `delta`（= pred − prior）。
排第 1 的模型即为该指标的最优模型，`indicator.r2` 记录的正是它的拟合优度。
用于回答：「哪些模型参与了这个指标的预测」「各模型预测差多少」「最优模型预测多少」「预测相比前值是升还是降」。

**表名**：`fmqt_forecast_prediction`（预测明细）

| 字段 | 含义 |
|---|---|
| `indicator_id` | 所属指标 id |
| `model_rank` | 模型综合排名，**1 为最优** |
| `model` | 模型名 |
| `pred` | 该模型的预测值 |
| `the_prior` | 前值 |
| `delta` | 预测变动 = `pred` − `prior`，正值=预测上行，负值=下行 |

### 5.1 某指标的各模型预测明细

**业务**：按模型排名升序列出全部参与预测的模型结果。

```sql
SELECT model_rank, model,
       CAST(pred      AS DOUBLE) AS pred,
       CAST(the_prior AS DOUBLE) AS prior,
       CAST(delta     AS DOUBLE) AS delta
FROM fmqt_forecast_prediction
WHERE indicator_id = 1
ORDER BY model_rank;
```

**参数**：`indicator_id` = `1`

**返回**：`model_rank`、`model`、`pred`、`prior`、`delta`

---

## 六、特征归因：模型为什么这么预测

**业务背景**：这是平台最具解释力的部分。模型给出预测值后，会用 **SHAP 归因**拆解
「哪些特征把这个预测值推高了、哪些压低了」，并按影响力占比排序。
归因针对该指标 **Top-3 的模型**分别给出，因此同一指标会有三组特征贡献。
另外还记录了模型**最终入选的特征清单**（建模实际使用的变量）。
本场景回答：「这个指标为什么预测上行」「哪些因素在推动预测值」「影响最大的因素是什么」「模型用了哪些变量」。

**表名**：`fmqt_forecast_feature_contribution`（SHAP 归因）

| 字段 | 含义 |
|---|---|
| `indicator_id` | 所属指标 id |
| `model_rank` | Top-3 模型排名：`1` / `2` / `3` |
| `model` | 模型名 |
| `feature_rank` | 特征重要性排名（越小越重要） |
| `feature` | 特征名 |
| `shap` | SHAP 贡献值，**正值=抬升预测，负值=压低预测** |
| `share` | 影响力占比 `0~1` |

**表名**：`fmqt_forecast_selected_feature`（入选特征）

| 字段 | 含义 |
|---|---|
| `indicator_id` | 所属指标 id |
| `feature` | 入选特征名（模型最终使用的变量） |

> 口径提示：`shap` 与 `share` 只在 `feature_contribution` 里有；
> `selected_feature` 只回答「用了哪些特征」，不含贡献度。

### 6.1 完整 SHAP 归因明细

**业务**：取某指标 Top-3 模型各自的完整归因表，按 模型排名 → 特征重要性 排序，适合逐条展示归因过程。若只需结论请用 6.3。

```sql
SELECT model_rank, model, feature_rank, feature,
       CAST(shap  AS DOUBLE) AS shap,
       CAST(share AS DOUBLE) AS share
FROM fmqt_forecast_feature_contribution
WHERE indicator_id = 1
ORDER BY model_rank, feature_rank;
```

**参数**：`indicator_id` = `1`

### 6.2 入选特征清单

**业务**：取该指标模型最终筛选使用的全部特征名，回答「建模包含哪些变量」。

```sql
SELECT feature
FROM fmqt_forecast_selected_feature
WHERE indicator_id = 1;
```

**参数**：`indicator_id` = `1`

### 6.3 影响力 Top-N 特征（结论用）

**业务**：按 SHAP 绝对值降序取影响最大的前 N 个特征，供 AI 直接给出结论（无需自行排序截断）。与 6.1 的区别：6.1 是完整明细表，6.3 是已排序的 Top-N 结论。

```sql
SELECT feature, model, model_rank, feature_rank,
       CAST(shap  AS DOUBLE) AS shap,
       CAST(share AS DOUBLE) AS share
FROM fmqt_forecast_feature_contribution
WHERE indicator_id = 1
ORDER BY ABS(shap) DESC
LIMIT 5;
```

**参数**：`indicator_id` = `1`、`LIMIT` = `5`

---

## 七、跨月份趋势对比

**业务背景**：由于**预测月份唯一、一份报告包含一组完整指标**，
同一个指标名在不同月份的报告中就会各出现一次，天然构成**时间序列**。
把同一个指标按月份串起来，就能看出模型对它的预测是在**上调还是下调**，
以及预测值与市场预期的分歧如何演变。
本场景回答：「CPI 近几个月的预测值怎么变化」「这个指标最近被上调还是下调了」「这个月比上个月调了多少」。

**表名**：`fmqt_forecast_indicator` JOIN `fmqt_forecast_report_meta`

### 7.1 同一指标跨月份的预测序列

**业务**：按预测月份升序列出某指标在各月报告中的模型预测值、前值、市场预期、张瑜团队预测与 R²。
对比相邻月份的 `pred` 即可得出「上调 / 下调」结论；`pred − prior` 可看每期预测的方向性变动。

```sql
SELECT m.forecast_month, i.id AS indicator_id, i.target_name AS name,
       CAST(i.pred        AS DOUBLE) AS pred,
       CAST(i.the_prior   AS DOUBLE) AS prior,
       CAST(i.market_pred AS DOUBLE) AS market_pred,
       CAST(i.zhangyu     AS DOUBLE) AS zhangyu,
       CAST(i.r2          AS DOUBLE) AS r2
FROM fmqt_forecast_indicator i
JOIN fmqt_forecast_report_meta m ON m.id = i.report_id
WHERE i.target_name = 'CPI:同比' AND i.is_active = 1
ORDER BY m.forecast_month;
```

**参数**：`target_name` = `'CPI:同比'`（指标全名，可先用 3.2 确认准确名称）

**返回**：`forecast_month`、`indicator_id`、`name`、`pred`、`prior`、`market_pred`、`zhangyu`、`r2`

---

## 八、大模型解读文案

**业务背景**：除数值外，平台还会调用大模型生成**投研解读文案**并落库，
供前端展示与 AI 直接引用。分析任务为**异步生成**：先写入一条 `pending` 记录，
大模型完成后回写 `done` 与正文，失败则写 `error` 与错误信息，前端通过轮询获取结果。
支持三种场景：`SC_GLOBAL`（宏观综览，对整份报告做整体研判）、
`SC_FEATURE`（单指标的特征归因解读）、`SC_INDICATOR`（单指标解读）。
本场景回答：「AI 对 2026-06 怎么看」「宏观综览的解读」「这个指标的特征归因说明了什么」。

**表名**：`fmqt_forecast_analysis`（大模型分析落库）

| 字段 | 含义 |
|---|---|
| `report_id` | 所属报告 id |
| `scenario` | 场景：`SC_GLOBAL` / `SC_FEATURE` / `SC_INDICATOR` |
| `indicator_id` | 所属指标 id（`SC_GLOBAL` 场景为 NULL） |
| `title` | 分析标题 |
| `content` | 大模型返回正文（**JSON 文本**，需解析后使用） |
| `the_status` | `pending` / `done` / `error` |
| `err_msg` | 失败原因 |
| `created_at` | 创建时间 |

> **口径提示（重要）**：取到结果后**必须先看 `the_status`**——
> 只有 `done` 的 `content` 才可用；`pending` 表示仍在生成，应稍后重试；
> `error` 时读 `err_msg`（常见原因是未配置大模型 API Key）。

### 8.1 宏观综览（整份报告的解读）

**业务**：取某份报告的整体研判文案，对应前端一图速览页顶部的「宏观综览」区块。

```sql
SELECT id, report_id, scenario, title, content,
       the_status AS status, err_msg, created_at
FROM fmqt_forecast_analysis
WHERE report_id = 1 AND scenario = 'SC_GLOBAL' AND indicator_id IS NULL
ORDER BY id DESC
LIMIT 1;
```

**参数**：`report_id` = `1`

### 8.2 单指标的特征归因解读

**业务**：取某个指标的特征归因文案，需要同时指定 `scenario` 与 `indicator_id`。

```sql
SELECT id, report_id, scenario, indicator_id, title, content,
       the_status AS status, err_msg, created_at
FROM fmqt_forecast_analysis
WHERE report_id = 1 AND scenario = 'SC_FEATURE' AND indicator_id = 1
ORDER BY id DESC
LIMIT 1;
```

**参数**：`report_id` = `1`、`indicator_id` = `1`

**返回**：`id`、`scenario`、`indicator_id`、`title`、`content`、`status`、`err_msg`、`created_at`

---

## 附：数据落库流程（仅供理解，不建议 MCP 开放）

上述 8 个场景全部是 **SELECT**。数据的产生由平台后端完成，流程如下（实现见 `db.py`）：

1. 用户上传 Excel → 解析 → 写入 `report_meta`（一份报告一行，`forecast_month` 唯一）；
2. 逐个写入 `indicator`，并**建立「指标名称 → indicator_id」映射**；
3. 依据映射，把 Excel 子表按 `indicator_id` 写入 `prediction`、`feature_contribution`、`selected_feature`；
4. 以上在一个事务内 `commit()`，报告状态置 `done`；
5. 后台异步触发大模型分析：先写一条 `pending` 的 `analysis` 记录，完成后回写 `done` + 正文，失败写 `error` + 原因。

> ⚠️ **给 MCP 的建议**：写入涉及业务回写，**不要对模型开放写权限**。
> 另外 `forecast_month` 上有唯一约束，重复上传同一预测月份会触发唯一键冲突（当前应用层未做覆盖写入）。

---

## 九、MCP 工具映射建议

下面给出把上述 SQL 封装为 MCP 工具的建议：设计原则、工具清单、schema 模板与实现注意点。

### 9.1 设计原则

| 原则 | 说明 |
|---|---|
| **按业务场景划分，不按接口照搬** | 一个工具对应一个「分析师会问的问题」，而不是一个 HTTP 接口；一个场景可对应多个工具 |
| **预测月份统一 `YYYY-MM` 字符串** | 与数据库一致，避免格式歧义；这是业务主键，也是推荐的查询入口 |
| **枚举值收窄** | 板块只允许 `增长/通胀/信用/外贸/其他`，场景只允许 `SC_GLOBAL/SC_FEATURE/SC_INDICATOR`，减少模型乱填 |
| **参数一律走预编译占位符** | 绝不用字符串拼接，防止 SQL 注入 |
| **数值类参数设上下界** | 如 `limit` 限 1~50，避免 `LIMIT -5` 之类的语法错误 |
| **返回结构化 + 说明口径** | 明确 `pred / prior / market_pred / zhangyu` 的含义，避免模型误读 |
| **单次工具调用对应 1 条 SQL** | 保持毫秒级；不要在一个工具里做多轮循环 |
| **只读即可** | 建议 MCP Server 使用**只读数据库账号**，全部工具都是 SELECT |

### 9.2 工具清单

统一约定：`forecast_month` 为 `YYYY-MM` 字符串。「对应 SQL」列指向本文档前面的小节号。

#### 报告检索（场景一、二）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `list_reports` | 列出所有可用报告的预测月份与概况 | 无 | 1.1 |
| `get_report_by_month` | 按预测月份定位报告，返回 report_id 与元信息（**主入口**） | `forecast_month` | 1.2 |
| `get_report_meta` | 按 report_id 查报告元信息（数据截止月、模型池、状态） | `report_id` | 1.3 |
| `get_overview_indicators` | 某月全部指标卡片（已按板块排序） | `report_id` | 2.1 |
| `get_report_summary` | 某月指标的板块分布统计 | `report_id` | 2.2 |

#### 指标定位（场景三、四）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_indicators_by_group` | 按板块筛选某月的指标 | `report_id`, `group_cat`（枚举） | 3.1 |
| `search_indicator_by_name` | 按指标名称模糊搜索（跨月份），返回 indicator_id | `keyword` | 3.2 |
| `get_indicator_snapshot` | 单指标数值快照（前值/模型预测/市场预期/张瑜预测/R²） | `indicator_id` | 4.1 |

#### 预测与归因（场景五、六）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_model_predictions` | 某指标各模型的预测对比（含最优模型与变动方向） | `indicator_id` | 5.1 |
| `get_feature_contributions` | 完整 SHAP 归因明细（Top-3 模型） | `indicator_id` | 6.1 |
| `get_selected_features` | 模型最终入选的特征清单 | `indicator_id` | 6.2 |
| `get_top_shap_features` | 影响力最大的 Top-N 特征（结论用） | `indicator_id`, `limit?`(1-50) | 6.3 |

#### 趋势与解读（场景七、八）

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `compare_indicator_months` | 同一指标跨月份的预测变化（上调/下调） | `indicator_name` | 7.1 |
| `get_llm_analysis` | 取大模型解读文案（宏观综览 / 单指标归因） | `report_id`, `scenario`(枚举), `indicator_id?` | 8.1 / 8.2 |

### 9.3 工具定义示例（可直接用作 schema 模板）

```json
{
  "name": "get_report_by_month",
  "description": "按预测月份查询宏观经济指标预测报告，返回该报告的 report_id、数据截止月、生成时间、指标数量、模型池与落库状态。这是查询某月数据的第一步，用户提到「某年某月的预测/报告/展望」时应先调用本工具拿到 report_id，再用 get_overview_indicators 拉取该月全部指标。预测月份格式为 YYYY-MM，一个月只有一份报告；若查不到说明该月尚未上传数据。本工具不含指标明细。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "forecast_month": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}$",
        "description": "预测月份，格式 YYYY-MM，如 2026-06"
      }
    },
    "required": ["forecast_month"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "search_indicator_by_name",
  "description": "按指标名称模糊搜索宏观指标，支持跨报告检索，返回命中的指标名称、所属板块、所在报告月份、report_id 与 indicator_id，以及模型预测值等核心数值，按预测月份倒序排列（第一行为最新月份）。当用户直接说出指标名称（如 CPI、PPI、社零、出口、社融）时使用本工具定位指标，再用 get_indicator_snapshot 或 get_feature_contributions 查看明细。支持 % 通配符模糊匹配。注意同一指标名会在多个月份各出现一次，请按 forecast_month 选择目标月份。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "keyword": {
        "type": "string",
        "description": "指标名称关键词，支持模糊匹配，如 CPI、%PPI%、%出口%"
      }
    },
    "required": ["keyword"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_llm_analysis",
  "description": "查询大模型生成的投研解读文案。SC_GLOBAL 返回整份报告的宏观综览研判；SC_FEATURE 返回单个指标的特征归因解读（需配合 indicator_id）。返回前必须先检查 status：只有 done 的 content 才可用（JSON 文本，需解析）；pending 表示仍在生成，应稍后重试；error 时读 err_msg（常见原因是未配置大模型 API Key）。本工具返回的是文字解读，若要原始数值请用指标查询类工具。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "report_id": { "type": "integer", "description": "报告 id" },
      "scenario": {
        "type": "string",
        "enum": ["SC_GLOBAL", "SC_FEATURE", "SC_INDICATOR"],
        "description": "分析场景：SC_GLOBAL 宏观综览 / SC_FEATURE 特征归因 / SC_INDICATOR 单指标解读"
      },
      "indicator_id": { "type": "integer", "description": "指标 id，SC_FEATURE、SC_INDICATOR 场景必填" }
    },
    "required": ["report_id", "scenario"],
    "additionalProperties": false
  }
}
```

### 9.4 问法 → 工具路由（帮助模型选对工具）

| 用户问法 | 调用链路 |
|---|---|
| 「有哪些月份的预测报告？」 | `list_reports` |
| 「2026-06 的整体预测情况？」 | `get_report_by_month` → `get_overview_indicators` |
| 「2026-06 的通胀指标有哪些？」 | `get_report_by_month` → `get_indicators_by_group(group_cat='通胀')` |
| 「CPI 这个月预测多少？」 | `search_indicator_by_name` → `get_indicator_snapshot` |
| 「CPI 为什么预测上行？」 | `search_indicator_by_name` → `get_top_shap_features` |
| 「这个指标的完整归因？」 | `get_feature_contributions` + `get_selected_features` |
| 「哪些模型预测了它、差多少？」 | `get_model_predictions` |
| 「CPI 最近几个月是上调还是下调？」 | `compare_indicator_months` |
| 「AI 对这份报告怎么看？」 | `get_llm_analysis(SC_GLOBAL)` |
| 「这份报告数据截止到几月、用了哪些模型？」 | `get_report_meta` |

### 9.5 实现注意事项

1. **入口用预测月份，不要让用户猜 report_id**
   分析师只会说「2026 年 6 月」，工具层应先用 `get_report_by_month` 把月份解析成 report_id，再查指标（共 2 条 SQL，均毫秒级）。

2. **Decimal 必须转 DOUBLE**
   `pred / prior / r2 / shap / share` 等列是 `DECIMAL(20,4)`，读出来是 Decimal 对象，无法直接 JSON 序列化。本文 SQL 已统一用 `CAST(... AS DOUBLE)`。

3. **板块顺序靠 `CASE`，不能改成字典序**
   增长→通胀→信用→外贸→其他 是业务约定顺序，直接 `ORDER BY group_cat` 会导致板块乱序。

4. **大模型文案必须先看 status**
   `pending` 不代表没有数据，应提示稍后重试；`error` 时读 `err_msg`；只有 `done` 才解析 `content`。

5. **手填列可能为 NULL**
   `market_pred`（市场预期）、`zhangyu`（张瑜团队预测）是人工填写列，为空时应表述为「无数据」，**不要当成 0** 参与比较。

6. **趋势对比要求指标名精确匹配**
   `compare_indicator_months` 按 `target_name` 全等匹配，建议先调 `search_indicator_by_name` 拿到准确的指标全名。

7. **参数预编译 + 枚举收窄 + 格式校验**
   `forecast_month` 用 `^\d{4}-\d{2}$` 校验，板块与场景用 `enum` 限制，`limit` 设 1~50 上下界。

8. **只读账号 + 连接释放**
   全部工具都是 SELECT，建议 MCP Server 使用只读数据库账号；项目应用层无连接池，每次查询结束需释放连接（自建 Server 建议配 ≥5 的连接池，单查询超时 1~2 秒）。

