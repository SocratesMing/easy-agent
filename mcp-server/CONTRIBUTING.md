# MCP 业务开发规范（初稿）

> **状态**：初稿，待评审。
> **适用对象**：在 `mcp-server` 子项目下新增或修改一个 MCP 业务包的人。
> **与 `README.md` 的分工**：README 描述**现状与部署**；本文描述**写代码时必须遵守的约束与流程**。
> **规范来源**：本仓库现有实现（`contract.py`、`querykit/`、`errors.py`、`app.py` 及各 `businesses/*`）。
> 本文不是理想化设计，凡与存量代码不一致之处，均在 **附录 C：已知差距** 中列为待整改项。

---  
 
## 0. 先记住这 10 条

1. 业务包名就是业务名与 URL 后缀，`businesses/foo/` → `/mcp/foo/`（**必须有尾斜杠**）。
2. 每个业务包必须暴露 `build() -> FastMCP`，否则会被静默跳过。
3. 每个业务必须提供 `describe_<domain>_schema` 口径字典工具，且要求模型先调它。
4. 禁止提供 `query` 一类任意 SQL 工具。
5. 所有 SQL 必须经 `querykit.assert_readonly` + `readonly_query`，业务代码不得 `import pymysql`。
6. `limit` 必须收敛到 `max_rows()` 以内，且**返回中必须带 `truncated`**。
7. 涉 IO 的工具一律 `async def`  + `anyio.to_thread.run_sync`。
8. 错误文案不得包含异常堆栈、DSN、密码、API Key；原始异常只写脱敏日志。
9. 单测不得连接真实数据库，不得受本地 `.env` 影响。
10. `.env` 不入库；新增环境变量必须同步 `.env.example`、`README.md` 与 `tests/conftest.py`。

---

## 1. 定位与边界

### 1.1 独立子项目

`mcp-server` 是独立部署的 MCP 服务，与主应用 `easy-agent` **解耦**。两者只通过共享 MySQL 的两张表交换契约：

| 表 | 写入方 | 读取方 | 用途 |
| --- | --- | --- | --- |
| `mcp_api_keys` | 主应用 | 本模块（只读） | API Key 校验，库里只存 sha256 |
| `mcp_businesses` | 本模块（`registry.py`） | 主应用（只读） | 业务清单，用于设置页下拉与 Key 签发白名单 |

> 改表结构时两侧必须同步。主应用那一份 DDL 在 `easy_agent/db/database.py::_create_misc_tables`。

### 1.2 唯一事实来源

对外契约全部集中在 `easy_mcp_server/contract.py`：业务清单、URL 形态、鉴权前缀、两张表的 DDL。

**禁止在别处复制业务清单**。`contract.business_names()` 只扫描目录、不 import 业务包；`app.discover_businesses()` 与管理脚本都复用它。

### 1.3 依赖方向（单向，不可反转）

```
主应用 easy-agent ──读取共享库契约──▶ mcp-server
```

- 本模块**不得** import 主应用任何代码
- 主应用**不得** import 本模块
- 新增/删除业务只改 `businesses/` 一个目录，重启本服务后主应用自动感知

### 1.4 目录即业务

包内 `build()` 由 `app.py` 自动发现并挂载，同时由 `registry.publish()` 登记到 `mcp_businesses`。

**不得**手工维护业务清单、不得为单个业务硬编码路由。

---

## 2. 业务包标准结构

### 2.1 目录树

```
easy_mcp_server/businesses/<name>/
├── __init__.py          # 导出 build
├── server.py            # 协议层：工具注册、参数校验、身份读取、返回契约
├── <name>.py            # 领域层：SQL 构造、枚举/口径、结果格式化
└── README.md            # 表、工具清单、口径、已知坑
mcp-server/tests/test_<name>.py          # 业务测试
docs/MCP_docs/<场景>_SQL清单_MCP信息.md  # 上游口径文档（需求来源）
```

复杂业务可再分子模块（参考 `precious_metals/reports/`：一张报表一个模块 + 注册表），但分层不变。

### 2.2 文件职责

| 文件 | 允许做 | 禁止做 |
| --- | --- | --- |
| `__init__.py` | `from .server import build` | 写业务逻辑 |
| `server.py` | `@mcp.tool()` 定义、`current_username()`、调用领域层、组织返回字典 | 拼 SQL、写业务口径、直接查库 |
| `<name>.py` | 构造 SQL 与参数、定义枚举与单位、格式化行 | import FastMCP、读 HTTP 上下文、写日志以外的副作用 |
| `README.md` | 描述表、工具、单位、边界、坑 | 复制大段代码 |

### 2.3 分层理由

领域层保持**纯函数**（`sql, params = xxx_sql(...)`），才能脱离 MCP/数据库做单测；`server.py` 只做薄适配。现有 17 个业务包都是这个形态，新业务不得引入新的分层方式。

---

## 3. 工具设计规范

### 3.1 命名

- `snake_case`，动词开头：`get_` / `list_` / `describe_` / `list_top_`
- 同一业务的公共数据建议带业务前缀（如 `precious_metals_*`），避免客户端合并工具表时撞名
- 名称要能自解释，不用 `query1` / `tool_a`

### 3.2 必备工具：`describe_<domain>_schema`

每个业务**必须**有一个口径字典工具，返回表、字段、枚举、单位、陷阱，且 `INSTRUCTIONS` 中要求模型先调它。

它是"模型不写错 SQL"的第一道防线，也是 README 中口径的机器可读版本。

### 3.3 数量与拆分

- 建议单业务工具数 **≤ 12**；超出应拆成独立业务包（历史教训：54 个工具挤在一个 `investment_research` 包里，每次请求都要携带全部工具定义，已拆为多个独立端点）
- 一个业务包 = 一个端点 = 一把 Key = 一套表可见性白名单

### 3.4 参数

| 要求 | 说明 |
| --- | --- |
| 必填维度 | 大表必须把高区分度维度设为必填（如 `contract_code`），否则等于整表扫描 |
| `limit` | 统一用 `limit: int | None`；领域层用 `max(1, min(limit or default, max_rows()))` 收敛 |
| 时间参数 | 明确格式与**时区**（UTC / 毫秒时间戳），不得让同一句话在不同时区查出不同结果 |
| 枚举 | 白名单校验，非法值直接返回可读错误，不要静默回退 |
| 排序 | `ORDER BY` 走字段白名单映射，禁止把模型给的字符串拼进 SQL |

### 3.5 docstring 是给模型看的规格书

每个工具的 docstring 必须写清：

1. 这个工具解决什么问题、**何时不该用**
2. 每个参数的格式、默认值、上下限
3. 返回字段的**单位与量纲**（比例 vs 百分数、元 vs 亿元、收益率 vs 价格）
4. 边界行为：无数据、超范围、被截断时返回什么
5. 已知陷阱（`"null"` 字符串、毫秒时间戳、合计行不可相加等）

`INSTRUCTIONS` 与 docstring 的语言在同一业务内保持一致（存量多为英文，中文亦可，但**不得混用**）。口径发生变更时，docstring、`INSTRUCTIONS`、`schema_help()` 和 README 必须同步。

### 3.6 禁止任意 SQL 工具

**不得**提供 `query` / `execute_sql` / `sample_rows` 一类工具。理由：

- 行级隔离业务：模型可自拼 SQL 绕开 `AUTHOR` / `AUTH_VIEW` 过滤（`strategyqa` 明确禁用）
- 大表业务：模型写出全表扫的代价不可控（`fxbar` 明确禁用）

结构探索只允许 `list_tables` / `describe_table`，且必须经 `policy_from_env` 的表级白名单收敛。

---

## 4. 返回契约

### 4.1 成功

统一返回 JSON 对象，至少含 `ok: true`。列表类工具另给 `count`；单值类工具用 `found: bool` + `data`。

```json
{"ok": true, "count": 3, "items": [...]}
{"ok": true, "found": false, "data": null}
```

`us_cpi` 使用 `{data, meta}` 契约，其 `meta` 固定含 `warnings` / `truncated` / `units` / `applied_filters`。**新业务可任选一种，但同一业务内必须统一**。

### 4.2 失败

- 参数非法 / 业务规则不满足 → `{"ok": false, "error": "<人类可读说明>"}`，或稳定错误码 `{"ok": false, "error_code": "invalid_month"}`
- 底层异常 → 使用 `errors.tool_failure(code, message, exc, tool=...)` 构造 `ToolError`，由 FastMCP 输出 `isError=true`
- 错误码用 `snake_case`，客户端可据此分支；文案可调整但**错误码不可随意变更**

**禁止**把 `str(e)` 原样回传：驱动报错常携带连接串、账号、密码。原始异常只写服务端日志，且写之前经 `errors.redact()` 脱敏、单行化、截断。

### 4.3 `truncated` 是必填字段

任何带 `limit` / 时间窗收敛的工具，返回中**必须**告知是否发生截断：

- `truncated: true` 当 `len(rows) >= limit`
- 时间窗被收敛时给 `clamped: true`（`fxbar` 做法：收敛而不是报错，避免模型反复试错）
- 同时给模型可执行的下一步提示（缩小范围 / 走聚合 / 分页），否则会诱发无效多次调用

> 缺这个字段会直接导致"前端看到的数据不全 → 模型反复调用同一工具"，这是当前线上最典型的浪费来源。

### 4.4 口径统一

- **金额**统一，字段名体现单位（如 `_yi` 表示亿元），禁止同一业务里元/亿元混用
- **比例**统一 0—1 或百分数，并在 `units` 中声明；已存数值是百分数时不得再乘 100
- **时间**统一格式与时区；毫秒时间戳要额外给可读文本字段
- **空值**：`null` 不等于 0；JSON 解出的字符串 `'null'` 要套 `NULLIF(..., 'null')`
- **口径不可跨维度相加**（如不同分组口径的合计行），必须在 docstring 里点明

### 4.5 无数据不是错误

查不到数据返回成功 + 空数组 / `found=false` + `warning`，只有参数非法、依赖故障、存储损坏才算失败。

---

## 5. 数据访问规范（querykit）

### 5.1 只能走 querykit

| 能力 | 入口 |
| --- | --- |
| SQL 校验与收敛 | `querykit.assert_readonly` |
| 执行查询 | `querykit.readonly_query` |
| 标识符校验 | `querykit.assert_identifier` |
| 序列化 | `querykit.json_safe` |
| 表探索与白名单 | `querykit.policy_from_env` / `ExplorePolicy` |
| 行数上限 | `querykit.max_rows()` |

业务包不得自建连接、不得 `import pymysql`、不得绕过 `readonly_query`。

### 5.2 SQL 收敛四层（由 `guard.py` 提供）

1. 去注释后判断，防注释绕过
2. 只允许单条语句
3. 只允许 `SELECT` / `WITH`，屏蔽写操作与系统库
4. 强制返回行数上限（缺省自动补 `LIMIT`）

第二道兜底在连接池：会话级 `TRANSACTION READ ONLY` + `MAX_EXECUTION_TIME`。

> **禁止在生成的 SQL 里使用 `LIMIT offset,count` 形式**：`_apply_limit` 对该形式不改写，而 `readonly_query` 直接 `fetchall()` 不截断，会绕过 `QUERYKIT_MAX_ROWS`。详见附录 C。

### 5.3 参数化

- 值一律用 `%s` 占位 + `params` 元组，禁止 f-string 拼接
- 表名/库名/字段名走 `assert_identifier`
- 动态 `ORDER BY`、枚举、渠道走白名单映射
- 表所在库与 `MYSQL_DATABASE` 不同时用 `<BUSINESS>_SCHEMA` 配置，不改代码

### 5.4 limit

```python
def row_limit(limit: int | None = None, default: int = 500) -> int:
    return max(1, min(int(limit) if limit else default, max_rows()))
```

`QUERYKIT_MAX_ROWS` 缺省 500，**是全局硬上限**；模型传更大的值也会被压回。放宽只能改环境变量并重启，不得在业务里写更大的常量绕过。

### 5.5 大表硬约束

凡大表业务，领域层必须固化：

- 必填高区分度维度
- 不传时间参数时只查最近窗口（`*_DEFAULT_WINDOW_*`）
- 显式范围超过上限时**收敛并标记 `clamped`**，而不是报错
- 结果强制 `LIMIT` 且按时间倒序
- 缺省只查有限渠道/枚举子集

### 5.6 列裁剪与 JSON 转换

- 不下发 text 大字段（如 `PROFIT_LOSS_CHART`）、冗余拼接主键
- 只投影需要的列
- 返回前用 `json_safe` 处理 `Decimal` / `datetime` / `bytes`
- JSON 数组展开优先在 SQL 侧物化一次（派生表），避免逐行重复解析

### 5.7 异步

`readonly_query` 是阻塞调用，而 MCP SDK 对同步工具函数直接调用、不丢线程池。因此：

```python
async def _query(sql, params):
    return await anyio.to_thread.run_sync(readonly_query, sql, params)
```

所有涉 IO 工具写成 `async def`；纯计算 / 口径工具可保持同步。

---

## 6. 鉴权与数据隔离

- 身份通过 `context.current_username()` 获取，业务工具**不得**接触 HTTP 头或自行查 `mcp_api_keys`
- 中间件在请求级 `ContextVar` 中绑定身份并在 `finally` 清理；脱离请求上下文调用会抛 `PermissionError`（单测需自行 mock 或设 identity）
- 当前实现：缺少/无效 Key 以 `anonymous` 放行，携带有效 Key 时解析为对应用户。**行级隔离业务必须自行做数据过滤**，不能依赖 401 兜底
- 需要隔离时，把过滤条件固化在领域层 SQL 中（`AUTHOR = 当前用户`、`AUTH_VIEW = 1`），**调用方无法放松**
- 每个工具记录 `username` 到日志，便于审计；日志中不得出现 Key 明文

---

## 7. 安全

| 要求 | 做法 |
| --- | --- |
| 错误脱敏 | `errors.redact()`：去掉 password/token/key 字段、Bearer 值、环境变量中的真实密码；单行化、限长 |
| 不泄漏内部信息 | 对外文案只含稳定错误码 + 人类可读说明，不含异常类型、堆栈、连接串、内部路径 |
| 不暴露敏感表 | `DEFAULT_DENY_TABLES` 含 `mcp_api_keys`；业务白名单缺省收敛到自己的表 |
| 不访问系统库 | `guard.py` 拦截 `information_schema.*` 等 schema 限定访问 |
| 日志 | 记工具名、用户、行数、错误码；不记完整入参/出参（可能含业务数据） |
| 只读 | 所有业务 SQL 均为 SELECT，账号按只读授权 |

---

## 8. 性能与资源

- 连接池是进程级单例，`QUERYKIT_POOL_SIZE` 缺省 5 且预热到上限；多进程部署时实际连接数 = 进程数 × 该值，须小于 MySQL `max_connections`
- 单次查询超时由 `QUERYKIT_TIMEOUT_MS`（缺省 10s）约束
- 优先在 SQL 侧做聚合 / 排名 / 截断，不要把明细全拉回来再算
- 大结果不要塞进模型上下文：优先返回汇总；确需全量时走"生成文件 + 下载链接"路径
- `env.load_env()` 只在进程首次加载 `.env`，改配置**必须重启进程**才生效
- 关闭：`--timeout-graceful-shutdown 5` + `MCP_SHUTDOWN_TIMEOUT`（缺省 5s）双层兜底，避免 Ctrl+C 卡在 lifespan 收尾

---

## 9. 配置规范

- 业务自有配置一律用 `<BUSINESS>_` 前缀环境变量（如 `TREASURY_1Y_SCHEMA`、`FX_MAX_WINDOW_DAYS`），避免与共享的 `QUERYKIT_*` / `MCP_*` 混淆
- `.env` 不入库；`.env.example` 里每个变量要有注释说明用途、缺省值、留空语义
- 留空表示"取代码默认值"；表名类给显式默认值，避免空串被当成表名
- 表级白名单用 `env_list()` 读取，缺省收敛到本业务自己的那几张表
- **新增环境变量必须同步三处**：`.env.example`、`README.md` 配置段、`tests/conftest.py::_ISOLATED_ENV`
  （`conftest.py` 用"设空值"而非删除来屏蔽本地 `.env`，否则 `load_env()` 会重新注入）

---

## 10. 测试规范

位置：`mcp-server/tests/test_<name>.py`。

**必测项**

| 类别 | 例子 |
| --- | --- |
| limit 收敛 | 传 9999 被压到 `max_rows()`；传负数/0 得 1 |
| 参数校验 | 非法枚举、非法日期格式、起止倒置、必填缺失 |
| 空结果 | 返回成功 + 空数组 / `found=false`，不是 `isError` |
| 截断标记 | `len(rows) >= limit` 时 `truncated=true` |
| 权限过滤 | 只能查到本人数据；不可查看记录对任何人都不返回 |
| SQL 安全 | 生成的 SQL 含白名单字段、参数化、自动补 LIMIT |
| 口径 | 单位换算、null vs 0、比例不重复乘 100 |
| 错误脱敏 | 异常含密码时不出现在对外文案 |

**约束**

- 不连真实数据库：用 `monkeypatch` 替换领域层查询函数，或使用 `create_app(verifier, businesses_override=[...])` 离线跑协议层
- 不受本地 `.env` 影响：`conftest.py` 的 `isolate_env` 已统一处理；新增变量要登记
- 环境变量用例用 `monkeypatch.setenv`，不直接改 `os.environ`
- 断言 SQL 文本时不要断言完整字符串，断言关键片段（表、白名单字段、`LIMIT`）

---

## 11. 文档规范

### 11.1 上游口径文档

`docs/MCP_docs/<场景>_SQL清单_MCP信息.md` 是需求与口径来源，必须包含：

- 通用约定（库名、语法边界、周标签/日期格式、枚举、**单位**、权限）
- 每条 SQL 的用途、参数、可直接执行的语句、返回字段
- 输出列名与 JSON 键名的对照表
- 已知陷阱清单（保留字、JSON `'null'`、量纲、不可相加的口径）
- 性能约束（强制物化、禁用的高版本语法如 `JSON_TABLE`）

### 11.2 业务包 README

每个 `businesses/<name>/README.md` 至少写：挂载路径、数据源、文件结构、工具清单（表格）、参数说明、口径与坑、配置方式、来源文档链接。

### 11.3 主 README

新增业务后更新 `mcp-server/README.md` 的"已挂载业务"表格；删除业务时按 README 的「删除业务」流程清理 `mcp_businesses` 残留记录。

---

## 12. 新增业务 Checklist

按顺序执行，逐项勾选：

- [ ] 1. 拿到上游口径文档（`docs/MCP_docs/`），确认表、单位、枚举、边界
- [ ] 2. 设计工具清单：≤ 12 个、含 `describe_<domain>_schema`、无任意 SQL 工具
- [ ] 3. 定返回契约（成功/失败/meta/`truncated`）并在 README 写死
- [ ] 4. 建 `businesses/<name>/`，写 `__init__.py` / `<name>.py` / `server.py`
- [ ] 5. 领域层实现：`schema_help()`、`row_limit()`、`*_sql()`、结果格式化；全部参数化
- [ ] 6. `server.py`：`build()` 用 `stateless_http=True` + `streamable_http_path="/"`，写 `INSTRUCTIONS`，工具 `async def` + `to_thread`
- [ ] 7. 补 `tests/test_<name>.py`，覆盖第 10 节必测项
- [ ] 8. 新增环境变量同步到 `.env.example`、`README.md`、`tests/conftest.py::_ISOLATED_ENV`
- [ ] 9. 写业务包 README；更新主 README 业务表
- [ ] 10. 本地 `uv run python -m pytest` 全绿
- [ ] 11. 启动验证：`GET /health` 列出新业务、`GET /manifest` 返回带尾斜杠的 URL
- [ ] 12. 用真实 MCP 客户端调用 `describe_*_schema` → 一个查询工具 → 一个边界用例
- [ ] 13. 确认主应用设置页出现该业务并可签发 Key（无需改主应用代码）

---

## 13. 变更与兼容性

- **契约变更**（`contract.py` 中的 URL 形态、Key 前缀、表结构、哈希算法）属于协议变更，两侧必须同步发布，并在 PR 中明确标注
- URL **尾斜杠不可省**：MCP 客户端不跟随 307
- 工具重命名/删除视为破坏性变更：客户端可能缓存工具定义，优先新增 + 标记弃用，观察一个周期后再删
- 返回字段只增不删；确需删除时先加新字段并行返回
- `INSTRUCTIONS` / docstring 中口径变更要同步 `schema_help()` 与 README，避免三处不一致
- 放宽 `QUERYKIT_MAX_ROWS` 需评估模型上下文与前端渲染，并在 PR 中写明理由（参见附录 C）

---

## 14. 红线（MUST NOT）

1. ❌ 业务包 `import` 主应用代码，或主应用 `import` 本模块
2. ❌ 在 `contract.business_names()` 之外维护第二份业务清单
3. ❌ 提供任意 SQL 工具或整表采样工具
4. ❌ 业务代码直接连接数据库 / `import pymysql` / 拼接值到 SQL
5. ❌ 用 `LIMIT offset,count` 形式绕过行数上限
6. ❌ 把 `str(e)`、堆栈、DSN、密码、API Key 返回给客户端
7. ❌ 返回大字段、系统库数据、他人数据
8. ❌ 忽略 `limit` 收敛、缺失 `truncated` 标记
9. ❌ 同步阻塞工具里直接查库
10. ❌ 单测连真库、依赖本地 `.env`、把配置写死在测试里
11. ❌ 新增环境变量不同步 `.env.example` / README / `conftest.py`
12. ❌ 用无数据当错误、用错误当"收敛提示"

---

## 附录 A：模板代码

### A.1 `businesses/foo/__init__.py`

```python
"""Foo 业务 MCP 工具。挂载路径：/mcp/foo/"""
from .server import build

__all__ = ["build"]
```

### A.2 `businesses/foo/foo.py`（领域层）

```python
from __future__ import annotations

from ...querykit import assert_identifier, max_rows

TABLE = "foo_daily"
DEFAULT_ROW_LIMIT = 100
UNITS = {"amount_yi": "亿元", "ratio": "0-1 比例"}


def row_limit(limit: int | None = None) -> int:
    return max(1, min(int(limit) if limit else DEFAULT_ROW_LIMIT, max_rows()))


def schema_help() -> dict:
    """口径字典：表、字段、单位、枚举、陷阱。"""
    return {
        "table": TABLE,
        "units": dict(UNITS),
        "enums": {"side": ["buy", "sell"]},
        "pitfalls": ["ratio 是 0-1 比例，不是百分数", "null 不代表 0"],
    }


def list_sql(date: str, limit: int | None = None) -> tuple[str, tuple]:
    table = f"`{assert_identifier(TABLE)}`"
    size = row_limit(limit)
    # 值一律参数化；ORDER BY / 枚举走白名单
    sql = (
        f"SELECT trade_date, side, amount_yi, ratio FROM {table} "
        f"WHERE trade_date = %s ORDER BY trade_date DESC LIMIT {size}"
    )
    return sql, (date,)
```

### A.3 `businesses/foo/server.py`（协议层）

```python
from __future__ import annotations

import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from ...context import current_username
from ...errors import redact, tool_failure
from ...querykit import json_safe, readonly_query
from . import foo

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "Foo read-only data assistant. Call describe_foo_schema before querying. "
    "amount_yi is in 100 million yuan, ratio is a 0-1 value - never multiply by 100. "
    "When truncated is true, narrow the date range instead of repeating the same call."
)


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


async def _domain_query(tool: str, username: str, build_query, format_rows) -> dict:
    try:
        sql, params = build_query()
        rows = await _query(sql, params)
        result = format_rows(rows)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        # 原始异常只写脱敏日志，不回传客户端
        raise tool_failure(
            "foo_query_failed", "查询失败，请稍后重试", e, tool=tool
        ) from None
    result.setdefault("ok", True)
    logger.info("[foo] %s | 用户: %s | 行数=%s", tool, username, len(rows))
    return json_safe(result)


def build() -> FastMCP:
    mcp = FastMCP(
        "Foo 业务",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    @mcp.tool()
    def describe_foo_schema() -> dict:
        """Return the data dictionary: table, units, enums and pitfalls.
        Call this before any query. ratio is a 0-1 value, not a percentage.
        """
        logger.info("[foo] describe_foo_schema | 用户: %s", current_username())
        return {"ok": True, **foo.schema_help()}

    @mcp.tool()
    async def list_foo_daily(date: str, limit: int | None = None) -> dict:
        """List daily foo rows for one date, newest first.
        date: YYYY-MM-DD, required. limit: 1-500, default 100.
        Returns truncated=true when more rows exist; narrow the range instead of
        repeating the call. No rows is a success with an empty list.
        """
        username = current_username()
        return await _domain_query(
            "list_foo_daily",
            username,
            lambda: foo.list_sql(date, limit),
            lambda rows: {
                "count": len(rows),
                "items": rows,
                "truncated": len(rows) >= foo.row_limit(limit),
            },
        )

    return mcp
```

> 说明：`tool_failure` 返回 `ToolError`，用 `raise ... from None` 抛出可避免原始异常链进入响应文本。

### A.4 `tests/test_foo.py`

```python
from __future__ import annotations

from easy_mcp_server.businesses import foo


def test_row_limit_is_capped(monkeypatch):
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "500")
    assert foo.row_limit(9999) == 500
    assert foo.row_limit(-1) == 1


def test_sql_is_parameterized_and_limited():
    sql, params = foo.list_sql("2026-11-01", limit=10)
    assert "LIMIT 10" in sql
    assert params == ("2026-11-01",)
    assert "2026-11-01" not in sql  # 值不得拼进 SQL
```

### A.5 `businesses/foo/README.md` 骨架

```markdown
# foo —— <一句话说明>

## 挂载路径
`/mcp/foo/`

## 数据源
| 表 | 说明 | 所在库 |
| --- | --- | --- |

## 工具清单
| 工具 | 作用 | 关键参数 |

## 口径与单位

## 已知坑

## 配置
| 环境变量 | 缺省 | 说明 |

## 来源文档
`docs/MCP_docs/...`
```

---

## 附录 B：建议加入 CI 的自动检查

规范里能机器检查的条款，尽量固化成测试，而不是只靠评审：

| 检查项 | 实现方式 |
| --- | --- |
| 业务包不 import 主应用 / `pymysql` | AST 或文本扫描（参考 `tests/test_architecture_gate.py`） |
| 每个业务有 `build()` | import 包并断言可调用 |
| 每个业务有 `describe_*_schema` | 检查 `FastMCP` 工具列表 |
| 工具数 ≤ 12 | 同上 |
| 业务清单只有一份 | 断言 `app.discover_businesses()` 与 `contract.business_names()` 一致 |
| 返回含 `truncated` | 契约测试：mock 查询结果后调用各列表工具 |
| 错误文案不含敏感词 | 断言 `tool_failure` 输出不含密码 / DSN |
| 环境变量已登记 | 断言 `<BUSINESS>_*` 变量都出现在 `conftest.py::_ISOLATED_ENV` 与 `.env.example` |

---

## 附录 C：已知差距（存量待整改，评审时逐项确认）

初稿按"应有状态"撰写，以下存量实现与规范不一致，**不要求一次性改完**，但新业务不得照抄：

| # | 现状 | 风险 | 建议 |
| --- | --- | --- | --- |
| C1 | `weekly_research/server.py::_domain_query` 等多数业务不返回 `truncated` | 模型不知被截断 → 反复调用同一工具 | 统一在 `_domain_query` 里按 `len(rows) >= limit` 补 `truncated`，或抽公共 helper |
| C2 | `guard.py::_apply_limit` 对 `LIMIT offset,count` 原样放行，`pool.py::readonly_query` 直接 `fetchall()` 不截断 | 可绕过 `QUERYKIT_MAX_ROWS`，大结果打爆内存 | 对 offset 形式同样收敛，或在 `readonly_query` 做兜底截断 |
| C3 | 部分 `_domain_query` 把 `str(e)` 直接放进 `{"ok": false, "error": ...}` | 驱动报错可能带 DSN / 账号 / 密码 | 改用 `redact()`；底层异常走 `tool_failure` |
| C4 | 业务间返回契约不统一（`ok/count` vs `data/meta` vs `found`） | 客户端分支复杂 | 新业务统一；存量择机迁移，不改则不破坏 |
| C5 | 工具 docstring / `INSTRUCTIONS` 中英混用 | 模型理解不稳定 | 同业务内统一语言 |
| C6 | `QUERYKIT_MAX_ROWS=500` 为全局硬上限，放宽需评估 | 全量结果进模型上下文 → token 与压缩压力 | 优先补 `truncated` 与聚合工具，再考虑调环境变量 |
| C7 | `auth.py` 对无效 Key 以 `anonymous` 放行 | 行级隔离业务若漏过滤会越权 | 隔离条件必须固化在领域层 SQL，且不依赖 401 |
| C8 | 部分业务 README 缺"来源文档"链接或配置表 | 口径变更时三处不同步 | 按 11.2 骨架补齐 |

> 本附录随整改推进逐项勾选删除，避免规范与代码长期"两张皮"。

---

## 维护约定

- 规范条款变更需在 PR 中说明影响范围，并同步更新附录 C
- 新增业务若确需突破某条红线，必须在 PR 中写明理由 + 替代措施，并在本文"例外"章节登记（当前无例外）
