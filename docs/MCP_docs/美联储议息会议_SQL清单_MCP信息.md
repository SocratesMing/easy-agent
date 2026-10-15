# 美联储三个模块的 SQL 参考（供 MCP 接入）

本文档整理「FOMC 声明对比 / 美联储货币政策观点 / 市场反应」三个模块用到的查询 SQL，
每条包含：用途（业务逻辑）、参数、可直接执行的 SQL、返回字段。

表名、字段类型、索引及状态取值以美联储项目提供的建表 SQL 为准。MCP 工具划分与定义为接入建议。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 月份格式 | `YYYY-MM` 字符串（如 `2026-06`） |
| 月份粒度 | 声明表、报告表的 `meeting_month` 均有唯一索引，每个月份各保存一条记录 |
| 上一期 | 已保存报告的上一期以 `previous_statement_id` 为准，不等同于上一自然月 |
| 报告状态 | `PROCESSING`=处理中，`READY`=生成完成，`FAILED`=生成失败 |
| 最新报告 | 最新已入库报告可能处于处理中或失败；查询已完成内容时取最新 `READY` 报告 |
| 第一部分 | `comparison_json` 保存大模型生成的声明对比 JSON，字段类型为 `LONGTEXT` |
| 第二部分 | `section=2`，美联储货币政策观点，内容以图片二进制保存 |
| 第三部分 | `section=3`，市场反应，内容以图片二进制保存 |
| 图片顺序 | 按 `section`、`display_order` 升序展示 |
| 字符集 | 三表均为 `InnoDB`、`utf8mb4`、`utf8mb4_0900_ai_ci` |
| 表关联 | 报告通过声明 ID 关联两期声明，图片通过 `report_id` 关联报告；建表 SQL 未定义外键约束 |
| 示例值 | SQL 中的 `2026-06`、ID `1` 等仅为参数示例，接入时替换为实际值并使用绑定参数 |

---

## 一、FOMC 声明对比

**表名**：`fmqt_fomc_statements`（FOMC 声明原文）、`fmqt_fomc_reports`（FOMC 报告及模型生成状态）

### 声明原文表

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INT | 自增主键 |
| `meeting_month` | VARCHAR(7) | 会议月份，格式 `YYYY-MM`，唯一 |
| `file_name` | VARCHAR(255) | TXT 文件名 |
| `file_hash` | VARCHAR(64) | TXT 文件 SHA-256 |
| `content` | LONGTEXT | 声明全文 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |

### 报告主表

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INT | 自增主键 |
| `meeting_month` | VARCHAR(7) | 报告月份，格式 `YYYY-MM`，唯一 |
| `current_statement_id` | INT | 本期声明 ID |
| `previous_statement_id` | INT | 上一期声明 ID |
| `source_signature` | VARCHAR(64) | 源文件及模型配置综合签名，用于防重 |
| `comparison_json` | LONGTEXT | 大模型生成的第一部分 JSON |
| `status` | VARCHAR(30) | `PROCESSING` / `READY` / `FAILED` |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |

> 两张表的全部字段均为 `NOT NULL`。声明表有月份唯一索引；报告表有月份唯一索引、`source_signature` 普通索引、`status` 普通索引。

### 1.1 最新声明月份

**业务**：确定已入库原文的最新月份。

**参数**：无。

```sql
SELECT MAX(meeting_month) AS month
FROM fmqt_fomc_statements;
```

**返回**：`month`；空表返回 `NULL`。

### 1.2 最新报告月份

**业务**：默认报告月份。区分「最新已入库」与「最新已完成」两个口径。

**参数**：无。

```sql
-- 最新已入库报告，包含处理中或失败的报告
SELECT MAX(meeting_month) AS month
FROM fmqt_fomc_reports;

-- 最新已完成报告，适合默认读取声明对比
SELECT MAX(meeting_month) AS month
FROM fmqt_fomc_reports
WHERE status = 'READY';
```

**返回**：`month`；没有符合条件的报告时返回 `NULL`。

### 1.3 最近 N 期报告列表（倒序）

**业务**：月份选择器、历史报告列表。

**参数**：`limit`（默认 24，建议 1～120）、`status`（可选）。

```sql
SELECT id AS report_id, meeting_month AS month, status,
       current_statement_id, previous_statement_id,
       created_at, updated_at
FROM fmqt_fomc_reports
-- 可选：WHERE status = 'READY'
ORDER BY meeting_month DESC
LIMIT 24;
```

**返回**：`report_id`、`month`、`status`、`current_statement_id`、`previous_statement_id`、`created_at`、`updated_at`。

> 列表只查报告元数据，不读取 JSON 和图片二进制。

### 1.4 某月份声明原文

**业务**：读取指定会议月份的 FOMC 声明全文。

**参数**：`month`。

```sql
SELECT id AS statement_id, meeting_month AS month,
       file_name, file_hash, content, created_at, updated_at
FROM fmqt_fomc_statements
WHERE meeting_month = '2026-06';
```

**返回**：`statement_id`、`month`、`file_name`、`file_hash`、`content`、`created_at`、`updated_at`。

### 1.5 本期之前最近一份已入库声明

**业务**：寻找数据库内的前一期声明，供历史原文查询或生成前校验使用。

**参数**：`month`。

```sql
SELECT id AS statement_id, meeting_month AS month,
       file_name, file_hash, content
FROM fmqt_fomc_statements
WHERE meeting_month < '2026-06'
ORDER BY meeting_month DESC
LIMIT 1;
```

**返回**：`statement_id`、`month`、`file_name`、`file_hash`、`content`。

> 已保存报告的两期关系使用 1.6，不能用本条查询重新推算并替换报告中的上一期。

### 1.6 某报告关联的两期声明

**业务**：声明对比的两期标题与原文，按报告保存的 ID 读取。

**参数**：`month`。

```sql
SELECT
  r.id AS report_id,
  r.meeting_month AS month,
  r.status,
  r.current_statement_id,
  c.meeting_month AS current_month,
  c.file_name AS current_file_name,
  c.content AS current_content,
  r.previous_statement_id,
  p.meeting_month AS previous_month,
  p.file_name AS previous_file_name,
  p.content AS previous_content
FROM fmqt_fomc_reports AS r
LEFT JOIN fmqt_fomc_statements AS c ON c.id = r.current_statement_id
LEFT JOIN fmqt_fomc_statements AS p ON p.id = r.previous_statement_id
WHERE r.meeting_month = '2026-06';
```

**返回**：报告 ID、月份、状态，以及两期声明 ID、月份、文件名、全文，字段名见 SQL。

> 建表 SQL 未定义外键，因此使用 `LEFT JOIN` 保留报告行。若关联声明的月份或正文为 `NULL`，应返回关联数据缺失，不能当作正常对比。

**上层业务**：根据 `current_month` / `previous_month` 去掉连字符，生成标题，例如 `FOMC声明202606`。

### 1.7 某月份已完成的声明对比

**业务**：页面第一部分「FOMC 声明对比」。

**参数**：`month`。

```sql
SELECT id AS report_id, meeting_month AS month,
       status, comparison_json, updated_at
FROM fmqt_fomc_reports
WHERE meeting_month = '2026-06'
  AND status = 'READY';
```

**返回**：`report_id`、`month`、`status`、`comparison_json`、`updated_at`。

> `comparison_json` 是文本字段，工具层需要解析 JSON。查询无结果时结合 4.1 区分报告不存在、处理中和生成失败，不能统一返回「没有变化」。

**当前应用的比较项格式**（属于应用约定，DDL 本身不约束 JSON 内部结构）：

| 字段 | 含义 |
|---|---|
| `category` | 比较类别 |
| `previous_html` | 上一期比较内容，可能包含变化高亮标签 |
| `current_html` | 本期比较内容，可能包含变化高亮标签 |
| `source` | `llm`=模型结果；`local-demo`=本地模拟结果 |

**类别顺序**：经济状况-增长、经济状况-就业、经济状况-通胀、经济状况-其他、货币政策-目标、货币政策-政策利率、货币政策-缩表、政策评估、投票结果。

**上层业务**：按类别过滤；需要纯文本时在工具层解析 HTML 并提取正文；保留 `source`，避免把模拟结果当作正式模型结果。

---

## 二、美联储货币政策观点

**表名**：`fmqt_fomc_report_images`（FOMC 报告图片）

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INT | 自增主键 |
| `report_id` | INT | 所属报告 ID |
| `section` | INT | 页面模块：`2`=货币政策观点，`3`=市场反应 |
| `display_order` | INT | 同一模块内的图片显示顺序 |
| `file_name` | VARCHAR(255) | 原图片文件名 |
| `file_hash` | VARCHAR(64) | 图片文件 SHA-256 |
| `content_type` | VARCHAR(50) | 图片 MIME 类型 |
| `image_data` | MEDIUMBLOB | 图片原始二进制，最大约 16 MB |
| `created_at` | DATETIME | 创建时间 |

> 全部字段均为 `NOT NULL`；`report_id` 有普通索引，无外键约束。第二、第三部分共用此表，通过 `section` 区分。

### 2.1 某月份货币政策观点图片列表

**业务**：页面第二部分的图片列表，按显示顺序返回。

**参数**：`month`。

```sql
SELECT
  i.id AS image_id, i.report_id, r.meeting_month AS month,
  i.section, i.display_order, i.file_name,
  i.file_hash, i.content_type, i.created_at
FROM fmqt_fomc_reports AS r
JOIN fmqt_fomc_report_images AS i ON i.report_id = r.id
WHERE r.meeting_month = '2026-06'
  AND i.section = 2
ORDER BY i.display_order ASC, i.id ASC;
```

**返回**：`image_id`、`report_id`、`month`、`section`、`display_order`、`file_name`、`file_hash`、`content_type`、`created_at`。

**上层业务**：用 `image_id` 生成图片访问地址，例如 `/api/images/1`。MCP 面向远程客户端时需补齐实际后端地址。

### 2.2 单张货币政策观点图片

**业务**：按 ID 读取第二部分的某张图片。

**参数**：`image_id`。

```sql
SELECT id AS image_id, report_id, section, display_order,
       file_name, file_hash, content_type, image_data
FROM fmqt_fomc_report_images
WHERE id = 1
  AND section = 2;
```

**返回**：所选字段；`image_data` 为图片二进制。

> 图片内容没有独立的文字字段。需要理解图片中的观点时，先获取图片，再由支持图像理解的能力读取；不能从文件名推断观点正文。

---

## 三、市场反应

**表名**：`fmqt_fomc_report_images`（与第二部分共用；字段见第二节）

> **模块口径**：市场反应必须使用 `section=3`，避免混入货币政策观点图片。

### 3.1 某月份市场反应图片列表

**业务**：页面第三部分的图片列表，按显示顺序返回。

**参数**：`month`。

```sql
SELECT
  i.id AS image_id, i.report_id, r.meeting_month AS month,
  i.section, i.display_order, i.file_name,
  i.file_hash, i.content_type, i.created_at
FROM fmqt_fomc_reports AS r
JOIN fmqt_fomc_report_images AS i ON i.report_id = r.id
WHERE r.meeting_month = '2026-06'
  AND i.section = 3
ORDER BY i.display_order ASC, i.id ASC;
```

**返回**：`image_id`、`report_id`、`month`、`section`、`display_order`、`file_name`、`file_hash`、`content_type`、`created_at`。

### 3.2 单张市场反应图片

**业务**：按 ID 读取第三部分的某张图片。

**参数**：`image_id`。

```sql
SELECT id AS image_id, report_id, section, display_order,
       file_name, file_hash, content_type, image_data
FROM fmqt_fomc_report_images
WHERE id = 1
  AND section = 3;
```

**返回**：所选字段；`image_data` 为图片二进制。

### 3.3 某报告全部图片（跨模块）

**业务**：一次查询第二、第三部分的图片元数据，在工具层分组返回。

**参数**：`report_id`。

```sql
SELECT id AS image_id, report_id, section, display_order,
       file_name, file_hash, content_type, created_at
FROM fmqt_fomc_report_images
WHERE report_id = 1
  AND section IN (2, 3)
ORDER BY section ASC, display_order ASC, id ASC;
```

**返回**：`image_id`、`report_id`、`section`、`display_order`、`file_name`、`file_hash`、`content_type`、`created_at`。

**上层业务**：`section=2` 放入 `section2_images`，`section=3` 放入 `section3_images`；不在列表查询中读取 BLOB。

---

## 四、附：报告生成状态与大模型结果

**表名**：`fmqt_fomc_reports`（字段见第一节）

### 4.1 某月份报告状态

**业务**：查看异步模型任务状态、页面状态轮询。

**参数**：`month`。

```sql
SELECT id AS report_id, meeting_month AS month, status, updated_at
FROM fmqt_fomc_reports
WHERE meeting_month = '2026-06';
```

**返回**：`report_id`、`month`、`status`、`updated_at`。

**上层业务**：

| 状态 | 返回说明 |
|---|---|
| `PROCESSING` | 正在生成声明对比；图片可单独查询 |
| `READY` | 报告生成完成，可读取声明对比 |
| `FAILED` | 生成失败；状态表没有具体失败原因字段 |
| 查询无记录 | 指定月份的报告不存在 |

可增加 `comparison_ready = (status == 'READY')` 和对应的中文 `message`。

### 4.2 最新已完成报告及模型结果

**业务**：读取最近一期已完成报告的声明对比结果。

**参数**：无。

```sql
SELECT id AS report_id, meeting_month AS month,
       current_statement_id, previous_statement_id,
       comparison_json, status, updated_at
FROM fmqt_fomc_reports
WHERE status = 'READY'
ORDER BY meeting_month DESC
LIMIT 1;
```

**返回**：所选全部字段。

> `READY` 表示任务完成，是否为真实模型结果需检查应用 JSON 内的 `source`；建表结构没有独立的模型名称、提示词或错误信息字段。

---

## 五、MCP 工具映射建议

下面给出把上述 SQL 封装为 MCP 工具的建议：工具划分、入参定义与实现注意点。

### 5.1 设计原则

| 原则 | 说明 |
|---|---|
| **按业务能力划分，不按接口照搬** | 一个工具对应一个用户问题，例如「某期声明对比」「某期市场反应图片」 |
| **月份统一 `YYYY-MM` 字符串** | 与数据库一致，月份限制为 01～12；留空时按对应工具约定取最新月份 |
| **枚举值收窄** | 状态、图片模块、比较类别等使用枚举，避免模型填入不支持的值 |
| **参数一律走预编译占位符** | 不拼接用户输入；可选条件用固定 SQL 分支并绑定值 |
| **数值类参数设上下界** | `limit` 限 1～120，图片 ID / 报告 ID 必须为正整数 |
| **返回结构化内容** | JSON 在工具层解析；图片列表返回元数据和访问地址，单张图片按需读取 |
| **大字段按需查询** | 列表不取正文、模型 JSON 或 BLOB；完整报告允许组合少量明确的查询 |
| **状态与内容分开处理** | 不存在、处理中、失败分别返回；不把空结果解释为政策内容未变化 |

### 5.2 工具清单

统一约定：`month` 为 `YYYY-MM` 字符串；除「上一期声明」工具外可省略。
声明原文留空取最新声明月份；声明对比、两期声明和完整报告留空取最新 `READY` 报告月份；状态及图片列表留空取最新已入库报告月份。响应中返回最终使用的月份。
「对应 SQL」列指向本文档前面的章节号。

#### 通用

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_latest_fomc_month` | 取最新月份 | `source`: `statement` / `report` / `ready_report` | 1.1 / 1.2 |
| `list_fomc_reports` | 最近 N 期报告及状态 | `limit?`(1～120，默认24)、`status?`(`PROCESSING`/`READY`/`FAILED`) | 1.3 |
| `get_fomc_report_status` | 某期报告生成状态 | `month?` | 4.1 |

#### FOMC 声明对比

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_fomc_statement` | 某期声明原文 | `month?` | 1.4 |
| `get_previous_fomc_statement` | 指定月份之前最近一份已入库声明 | `month`(必填) | 1.5 |
| `get_fomc_statement_pair` | 某报告实际关联的两期声明 | `month?` | 1.6 |
| `get_fomc_comparison` | 某期已完成的声明对比，可按类别过滤 | `month?`、`category?`(枚举) | 1.7；工具层解析与过滤 |

#### 美联储货币政策观点

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `list_fomc_policy_images` | 某期货币政策观点图片列表 | `month?` | 2.1 |
| `get_fomc_policy_image` | 获取单张货币政策观点图片 | `image_id`(正整数) | 2.2 |

#### 市场反应

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `list_fomc_market_images` | 某期市场反应图片列表 | `month?` | 3.1 |
| `get_fomc_market_image` | 获取单张市场反应图片 | `image_id`(正整数) | 3.2 |

#### 完整报告

| 工具名 | 说明 | 入参 | 对应 SQL |
|---|---|---|---|
| `get_fomc_report` | 报告状态、两期标题、声明对比及两模块图片列表 | `month?` | 4.1 + 1.6 + 1.7（仅 READY）+ 3.3 |

> 完整报告是组合工具，按单一报告组装少量查询，不是一条 SQL。指定月份为 PROCESSING / FAILED 时可返回状态及图片列表，并明确比较结果尚不可用。

### 5.3 工具定义示例（可直接用作 schema 模板）

```json
{
  "name": "get_fomc_comparison",
  "description": "查询指定月份已完成的FOMC声明对比，返回本期与上一期的比较内容，可按类别过滤。保留模型来源字段；月份留空取最新READY报告，不触发报告生成。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "month": {
        "type": "string",
        "pattern": "^[0-9]{4}-(0[1-9]|1[0-2])$",
        "description": "报告月份，格式YYYY-MM；留空取最新READY报告"
      },
      "category": {
        "type": "string",
        "enum": ["经济状况-增长", "经济状况-就业", "经济状况-通胀", "经济状况-其他", "货币政策-目标", "货币政策-政策利率", "货币政策-缩表", "政策评估", "投票结果"],
        "description": "比较类别；留空返回全部类别"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "list_fomc_policy_images",
  "description": "查询指定月份报告的美联储货币政策观点图片列表，仅查询section=2，按显示顺序返回图片元数据及访问地址，不返回全部图片二进制。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "month": {
        "type": "string",
        "pattern": "^[0-9]{4}-(0[1-9]|1[0-2])$",
        "description": "报告月份YYYY-MM；留空取最新已入库报告月份"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "get_fomc_market_image",
  "description": "根据图片ID获取单张市场反应图片，仅允许section=3；返回图片内容或可访问的图片资源地址。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "image_id": {
        "type": "integer",
        "minimum": 1,
        "description": "从市场反应图片列表获取的图片ID"
      }
    },
    "required": ["image_id"],
    "additionalProperties": false
  }
}
```

### 5.4 实现注意事项

1. **`month` 留空时先取对应的最新月份**  
   按 5.2 的口径选择最新声明、最新已入库报告或最新 READY 报告，再执行主查询。没有可用月份时返回无数据，不构造不存在的月份。

2. **上一期声明使用报告中的关联 ID**  
   读取已有报告必须使用 `current_statement_id` / `previous_statement_id`。月份可能不连续，不能直接把本期月份减一。

3. **校验关联数据完整性**  
   建表 SQL 没有外键约束，工具层需检查两期声明及所属报告是否存在。图片列表为空时先区分报告不存在与确实没有图片。

4. **模型 JSON 在工具层解析**  
   `comparison_json` 是 LONGTEXT；解析并校验数组及比较项格式。结果损坏时返回数据格式异常，不用空数组掩盖错误。`READY` 不等同于真实模型来源，应保留 `source`。

5. **第二、第三部分严格按模块过滤**  
   货币政策观点使用 `section=2`，市场反应使用 `section=3`。单张图片工具也验证模块，防止混用图片 ID。

6. **图片二进制按需读取**  
   列表只返回图片 ID、文件名、顺序、MIME 类型和访问地址。单张图片可通过现有 `/api/images/{image_id}` 获取，或按 MCP 图片内容格式返回；普通 JSON 不直接放 bytes。

7. **图片内容不等于结构化文字**  
   表中没有 OCR 正文、政策观点文字或市场反应文字字段。如需分析图片内容，应调用图像理解能力，并保留对应报告月份和图片来源。

8. **只读即可**  
   本文业务查询均为 SELECT，建议 MCP Server 使用只读数据库账号。报告刷新、源文件导入和模型生成有写入副作用，应作为单独能力设计。

9. **连接池、超时和返回大小**  
   根据实际数据量及部署环境配置连接池、查询超时与响应大小上限；长正文和图片避免批量读取。本文 SQL 已按提供的表结构核对，查询耗时需在实际数据库测量。

10. **建表脚本与查询接入分开**  
    提供的建表脚本包含 `DROP TABLE IF EXISTS`，重建会删除原表数据，不应放入 MCP 查询工具。本文仅整理读取 SQL，不执行建表脚本。
