# iData 数据分析看板 — MCP 工具设计（合并版）

> 本文是 `iData数据分析看板.md` 的**工具设计篇**：把原始的 29 条 SQL / 20 个工具建议，
> 收敛为 **10 个工具 + 一组可复用 SQL 片段**。
>
> 原始 SQL 与口径注释**完整保留在原文档**，作为核对实现的事实依据——本文只做"怎么封装"。
>
> 落地位置：`easy_mcp_server/businesses/idata/`（业务包），领域模块为 `idata.py`，
> MCP 端点为 `/mcp/idata/`。
>
> 注：本文写作时 iData 与宏观预测、利率择时、研报观点等场景同属 `investment_research`
> 一个业务包、共用一个端点。后续已拆为独立业务包，**路径与端点以拆分后为准**
> （本文其余章节提及的 `/mcp/investment_research/` 均指当时的形态）。

---

## 一、合并策略

原始设计里三处重复，对应三个合并动作。

### 重复 ①：SQL 骨架逐字重复

净买入表达式出现 15+ 次、`CAST(TRADE_CASHAMT AS DECIMAL(20,2))` 出现 20+ 次、
券种归一化的 `CASE` 逐字抄了两遍（原 1.6 / 1.8）。

→ **抽成模块级常量**，见第三节。

### 重复 ②：工具按"接口"切，导致 6 个工具其实是同一条 SQL

| 原工具 | 真实差异 |
|---|---|
| `list_institution_net_summary` | `GROUP BY institution` |
| `get_institution_bond_matrix` | `GROUP BY institution, bond_type` |
| `get_institution_term_matrix` | `GROUP BY institution, term` |
| `get_institution_cross_section` | `GROUP BY institution, term, bond_type` |
| `get_institution_heatmap` | 同上（仅合计行开关不同） |
| `get_bond_term_matrix` | `GROUP BY institution, term` + 券种过滤 |

→ **合并为 `get_institution_flow`**，维度通过 `group_by` 枚举传入（见 3.2）。

### 重复 ③：交易日查询散落 10 处

原 1.1 / 1.2 / 1.3 / 1.4 / 1.13 / 2.1 / 2.2 / 3.1 / 3.2 / 3.3 都是"在某个表上取日期"，
差别只在表、方向、是否限区间。

→ **合并为 `get_trade_dates`**，表用 `source` 枚举区分（见 3.1）。

### 合并前后对比

| | 原始 | 合并后 |
|---|---|---|
| SQL 条数 | 29 | 10 个工具内部复用少量参数化 SQL |
| 工具个数 | 20 | **10** |
| 单位口径 | 亿元 / 元 混用 | **统一亿元** |
| date 缺省行为 | 部分工具说明 | **全部**统一为"缺省取最新" |

---

## 二、工具清单（10 个）

统一约定：

- `date` / `dates` 一律 `YYYYMMDD` 字符串；**`date` 留空 → 自动取该表最新交易日**
- 金额字段一律**亿元**，字段名带 `_yi` 后缀（如 `net_yi`）
- 集中度 / 占比为 **%**（`24.53` 表示 24.53%）

| # | 工具 | 说明 | 入参 | 覆盖原 SQL |
|---|---|---|---|---|
| 1 | `describe_idata_schema` | 口径字典：表、字段、枚举、单位、陷阱 | — | — |
| 2 | `get_trade_dates` | 交易日查询统一入口 | `source`, `limit`, `before`, `start`, `end`, `ascending` | 1.1-1.4, 1.13, 2.1, 2.2, 3.1-3.3 |
| 3 | `get_institution_flow` | 机构流向（单日矩阵 / 多日趋势 / 期限堆叠） | `dates`/`date`, `group_by`, `include_total`, `bond_type`, `institution` | **1.5-1.12, 1.14** |
| 4 | `list_bonds` | 某交易日的个券列表（代码 + 简称） | `date`, `keyword` | 2.3, 2.4 |
| 5 | `get_bond_counterparts` | 某券各机构净买卖 + 成交量占比 + 主力买卖 | `bond_id`, `dates`/`date` | 2.5, 2.8 |
| 6 | `get_bond_trend` | 某券多日的机构 × 日期趋势 | `bond_id`, `dates`, `institution` | 2.6 |
| 7 | `list_top_active_bonds` | 成交最活跃的前 N 个券（含主力买卖） | `date`, `limit` | 2.7 |
| 8 | `get_lending_snapshot` | 借贷快照（余额 / 集中度 / 较前日变化 / 核心跟踪券） | `date`, `only_core`, `include_change` | 3.4, 3.5 |
| 9 | `get_lending_trend` | 某券（或全部核心券）余额与集中度走势 | `code`, `dates`/`days` | 3.6 |
| 10 | `get_llm_comment` | 取某场景已生成的大模型点评 | `scene`, `date` | 第四节 |

### 每个工具的细节

#### 2. `get_trade_dates`

| 入参 | 类型 | 说明 |
|---|---|---|
| `source` | 枚举 | `daily`(机构当日) / `bond`(个券机构) / `lending`(债券借贷)，默认 `daily` |
| `limit` | 1-250 | 返回条数，默认 1（= 最新交易日） |
| `before` | `YYYYMMDD?` | 只取该日期**之前**的（配合 `limit=1` 即"上一交易日"） |
| `start` / `end` | `YYYYMMDD?` | 区间查询（含端点），优先级高于 `limit` |
| `ascending` | bool | 默认 `false`（倒序，最新在前） |

一次覆盖：最新交易日、上一交易日、最近 N 个、指定区间内的交易日。

#### 3. `get_institution_flow`

| 入参 | 类型 | 说明 |
|---|---|---|
| `dates` | 字符串数组 | 交易日列表；与 `date` 二选一。缺省都为空 → 取最新交易日 |
| `date` | `YYYYMMDD?` | 单日快捷写法 |
| `group_by` | 枚举数组 | 见下表，默认 `["institution"]` |
| `include_total` | bool | 默认 `true`：使用合计行（单日汇总口径）；`false`：使用明细行 |
| `bond_type` | 枚举 | `国债`/`政金债`/`信用债`/`地方政府债`/`全部`/`合计`，默认不限制 |
| `institution` | 字符串? | 只看某机构（期限堆叠场景常用） |

`group_by` 取值（白名单，对应列名在代码里映射）：

| `group_by` | 语义 | 对应原 SQL |
|---|---|---|
| `["institution"]` | 各机构净买卖 | 1.5 |
| `["institution","bond_type"]` | 机构 × 券种 | 1.6 |
| `["institution","term"]` | 机构 × 期限 | 1.7 |
| `["institution","term","bond_type"]` | 三维明细 | 1.9 / 1.10 |
| `["date","institution"]` | 多日机构趋势 | 1.8 |
| `["date","term"]` | 机构期限堆叠 | 1.14 |
| `["term","bond_type"]` | 分券种的期限矩阵 | 1.11 |

返回每行都带 `buy_yi` / `sell_yi` / `net_yi`，模型按需取用。

> **合计行陷阱**：`fmqt_bond_exec_part_data` 同时有明细行与合计行。
> `include_total=true` 时固定 `STATISTIC_SUBDETAIL_NAME='合计' AND RELATED_...='合计'`；
> `false` 时固定 `!= '合计'`（并在 `group_by` 含 `bond_type` 时按展示口径归一化）。
> **两者绝不能混用**，否则金额翻倍。

#### 5. `get_bond_counterparts`

原 2.5（净买卖）与 2.8（成交量占比）是**同一条 SQL 的不同字段**，合并后一次返回：

```json
{"bond_id": "260205", "date": "20260827",
 "rows": [{"institution": "...", "net_yi": 1.23, "ratio": 24.53}],
 "top_buyer": {...}, "top_seller": {...}}
```

#### 8. `get_lending_snapshot`

原 3.4（快照）与 3.5（持续跟踪券）都基于同一份当日汇总，合并后一次给出：

- `rows`：全部券的 `balance_yi` / `concentration`
- `changes.bal_up` / `changes.bal_down`：较前一交易日上升 / 下降（`include_change=true` 时）
- `core_bonds`：持续更新且余额居前的券（`only_core=true` 时只返回这部分）

---

## 三、参数化 SQL 骨架

### 3.1 可复用片段（消除逐字重复）

```python
_AMT = "CAST(TRADE_CASHAMT AS DECIMAL(20,2))"
_NET = (f"(SUM(CASE WHEN SIDE='1' THEN {_AMT} ELSE 0 END)"
        f" - SUM(CASE WHEN SIDE='2' THEN {_AMT} ELSE 0 END))")
_NET_YI = f"ROUND({_NET} / 100000000, 2)"          # 元 → 亿元，一步到位

BOND_TYPE_CASE = """CASE
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '合计' THEN '合计'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME IN ('国债-新债','国债-老债') THEN '国债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME IN ('政策性金融债-新债','政策性金融债-老债') THEN '政金债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '中期票据' THEN '中票'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '短期/超短期融资券' THEN '短融超短融'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '企业债' THEN '企业债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '地方政府债' THEN '地方债'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '同业存单' THEN '同业存单'
    WHEN RELATED_STATISTIC_SUBDETAIL_NAME = '资产支持证券' THEN 'ABS'
    ELSE '其他' END"""

TERM_BUCKET_CASE = """CASE
    WHEN STATISTIC_SUBDETAIL_NAME IN ('1年及1年以下','1-3年','3-5年','5-7年','7-10年')
      THEN STATISTIC_SUBDETAIL_NAME
    ELSE '大于10年' END"""
```

### 3.2 `GROUP BY` 必须走白名单（不能参数化）

SQL 的列名无法用 `%s` 占位，**只能用白名单映射**——参数传的是"键名"，进 SQL 的是代码里写死的列名：

```python
DIMENSIONS = {
    "date":        "STATISTIC_REPORT_RANGE",
    "institution": "STATISTIC_DETAIL_NAME",
    "term":        "STATISTIC_SUBDETAIL_NAME",
    "bond_type":   "BOND_TYPE_EXPR",        # 用上面的 CASE 表达式
}
# group_by=[...] 里的每一项都必须命中白名单，否则直接报错（不静默忽略）
```

这与 `strategyqa` 里 `ORDER_BY_COLUMNS` 的做法一致：**用户输入只做键，不做列名**。

### 3.3 值一律预编译

日期、债券代码、机构名等全部 `%s` 传参；`dates` 列表展开成 `IN (%s,%s,...)`。
绝不 f-string 拼用户输入。

---

## 四、口径统一（与原文档的差异）

| 项 | 原文档 | 本设计 | 原因 |
|---|---|---|---|
| 金额单位 | 机构当日=亿元；另两表=元 | **全部亿元**，字段名带 `_yi` | 消除"模型猜单位"的歧义 |
| `date` 缺省 | 部分工具说明"留空取最新" | **全部工具**统一 | 行为一致，少记规则 |
| 交易日查询 | 10 条 SQL 分散 | 1 个工具 + `source` 枚举 | 同构查询收敛 |
| 矩阵类查询 | 6 个工具 | 1 个工具 + `group_by` | 本质同一条 SQL |
| `BOND_CONCENTRATION` 为 NULL | 注释提醒"应用层按 0 兜底" | **工具层兜底**（不抛给模型） | 数据缺陷不该让模型处理 |
| 借贷余额口径 | 注释强调 `TYPE='19' AND SIDE='1'` | 固化在 SQL 里，参数无法放松 | 口径错误会导致结论完全错 |

---

## 五、实现注意点

1. **单位换算在工具层完成**，字段名直接写 `_yi`，模型不需要再做除法。
2. **`date` 留空时先取最新交易日**（多一次毫秒级查询），再执行主查询。
3. **债券借贷余额**必须 `STATISTIC_AMT_TYPE='19' AND STATISTIC_AMT_SIDE='1'` 汇总，
   不要误用成交量字段。
4. **`BOND_CONCENTRATION` 可能为 NULL**（实测 4 行），工具层按 0 兜底。
5. **机构当日表的合计行陷阱**：SQL 必须显式区分合计 / 明细（见 2.3）。
6. **连接池与超时**：连接池 ≥10、单查询超时 1~2 秒（实测最慢约 41ms）。
7. **不要在工具内做大范围聚合**：跨多月按 5~10 个交易日分片，在内存汇总
   （一次性查 12 个月约 7 秒会超时）。
8. **只读账号即可**：全部是 SELECT。
9. **【新增】`group_by` 校验失败要报错**，不要静默回退成默认维度——模型会拿到
   错误维度的数据却以为是对的。
10. **【新增】`dates` 列表长度设上限**（建议 60），避免模型一次传几百个日期。

---

## 六、与内网部署的关系

本场景与 fxbar 一样，**只需要新增一个业务包**：

- 基础设施（`querykit` / `auth` / `db` / `contract` / `registry`）**零改动**
- 新增依赖：**无**（只用现有的 `anyio` / `pymysql` / `dbutils`）
- 表若不在主库，用环境变量 `IDATA_SCHEMA` 指定库名（同 fxbar 的 `FX_SCHEMA`）
- 迁移清单见交付说明
