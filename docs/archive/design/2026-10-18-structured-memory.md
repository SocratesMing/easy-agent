# 用户记忆结构化（Structured Memory）设计

| 项 | 内容 |
| --- | --- |
| 状态 | 已实现（默认开启，可用环境变量回退旧行为） |
| 日期 | 2026-10-18 |
| 范围 | 新增 `easy_agent/services/memory_store.py`；集成 `easy_agent/services/memory_manager.py`；新增 `tests/services/test_memory_store.py` |
| 约束 | 不改现有函数签名；Markdown 仍是权威读取文件；不改 DB schema；不改 `agent.py` / `streaming.py` 调用方式；多租户不在本阶段 |

## 1. 背景与问题

现状（改造前）：

- `memory_manager.update_memory_after_session` / `update_long_term_memory_after_session`
  让 LLM 直接输出整篇 Markdown（会话 `memory.md`、用户级 `AGENTS.md`），整文件覆盖写；
- 无结构化校验、无来源、无版本、无冲突处理：同一条记忆被改写时无法判断是"新增"还是"覆盖"；
- INFO 日志会打印完整记忆正文，存在敏感信息落日志的风险；
- 长度上限、密钥过滤、重复条目合并完全依赖提示词，代码侧没有兜底。

本设计在**不改变读取方**的前提下，为每份 Markdown 记忆增加一个 JSONL sidecar，
把"渲染视图（Markdown）"与"结构化事实（条目）"分开，并在写路径上补齐校验、脱敏、去重与版本。

## 2. 目标与非目标

目标：

1. 条目模型 `{id, key, value, category, source, confidence?, updated_at, version}`；
2. JSONL 原子写（临时文件 + `os.replace`），带 schema / revision / 更新时间；
3. 写前校验：长度上限、密钥 / PII 简单扫描、空条目拒绝；
4. 相同 key 去重合并，内容变化时 `version + 1`（supersede）；
5. 由条目渲染 Markdown，保持 `# 标题` + `## 分组` + `- 条目` 结构兼容；
6. 从旧 Markdown 首次导入（best effort），旧文件不改写；
7. 解析失败 / 任何异常回退旧 Markdown 写入路径，不丢数据；
8. 日志只记录长度、条目数、内容 hash，不打印记忆正文；
9. 环境变量 `EASY_STRUCTURED_MEMORY=0` 可完全回退旧行为，默认开启。

非目标：

- 不迁移数据库、不新增表（多实例共享仍依赖文件系统，见 §8）；
- 不做用户级隔离改造（沿用 `memories/{username}/` 路径）；
- 不做历史版本回放（只保留当前值与版本号，不保留旧值正文）；
- 不修改提示词、工具、SSE、前端；
- 不引入跨进程文件锁。

## 3. 数据模型与文件布局

### 3.1 条目

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str | 由规范化 `key` 派生：`mem_ + sha1(key)[:16]`，跨进程 / 跨导入稳定 |
| `key` | str | 去重键。取条目文本第一处分隔符（`——`/`—`/`：`/`:`/`，`/`,`/`；`/`。`）之前的部分，去标点、空白并 casefold，上限 80 字符 |
| `value` | str | 条目正文，换行/制表符折叠为空格，脱敏 + 截断后写入 |
| `category` | str | Markdown 二级标题（如 `决策与理由`、`工作背景`），缺省 `未分类` |
| `source` | str | `llm:session` / `llm:long_term` / `import:markdown` / `markdown:direct` / `unknown` |
| `confidence` | float? | 可选，0.0–1.0，当前 LLM 不产出，仅为后续扩展预留 |
| `updated_at` | str | UTC ISO 8601（秒级） |
| `version` | int | 同一 key 内容变化时 +1；内容相同再写入保持幂等 |

### 3.2 文件布局

| Markdown（权威） | JSONL sidecar |
| --- | --- |
| `workspace/{user}/session/{name}/memory.md` | 同目录 `memory.jsonl` |
| `memories/{user}/AGENTS.md` | 同目录 `AGENTS.jsonl` |

sidecar 路径 = Markdown 路径替换后缀（`memory.md -> memory.jsonl`）。没有新增目录；
运行时目录 `workspace/`、`memories/` 已在 `.gitignore` 中。

### 3.3 JSONL 格式

首行 meta，其后每条记忆一行（UTF-8，`ensure_ascii=False`）：

```json
{"type": "meta", "schema": 1, "revision": 3, "title": "会话记忆", "updated_at": "2026-10-18T00:00:00+00:00", "count": 2, "markdown_sha256": "..."}
{"type": "entry", "id": "mem_...", "key": "数据库统一用mysql", "value": "数据库统一用 MySQL —— 开发与生产一致", "category": "决策与理由", "source": "llm:session", "confidence": null, "updated_at": "2026-10-18T00:00:00+00:00", "version": 1}
```

- `revision`：sidecar 每次保存 +1（store 级单调计数，不复用 Markdown 行号）；
- `markdown_sha256`：与 sidecar 对应的 Markdown 内容 hash，用于检测"Markdown 被外部改动"；
- 读取容错：缺失文件视为空；损坏行 / 非 dict / 未知 `type` 跳过并计数；重复 key 以文件中靠后的记录为准（版本取最大）。

## 4. 写入流程

`update_memory_after_session` / `update_long_term_memory_after_session`（签名不变）：

1. 读取当前 Markdown（`read_memory` 不变）；
2. 若 sidecar 缺失或内容 hash 不一致，先按 Markdown 导入（首次迁移 / 外部编辑回灌，幂等）；
3. 调用 LLM 生成 Markdown（提示词、压缩、硬截断逻辑全部保持原样）；
4. `write_structured_memory`：
   - 解析 Markdown → 条目（best effort）；
   - 补齐 key/id/category/source/updated_at，做脱敏与长度校验；
   - 与 sidecar 既有条目按 key 合并：内容相同幂等，内容变化 `version + 1`；
   - 按预算渲染并裁剪（见 §5），返回渲染后的 Markdown；
   - 原子写 JSONL（`revision + 1`）；
5. 用渲染结果覆盖写 Markdown（`_write_markdown_file`，不再触发二次导入）；
6. 任意步骤异常 / 解析不出条目 → 回退旧路径 `write_memory(markdown)`，Markdown 原文落盘，数据不丢。

顺序说明：先写 JSONL 再写 Markdown。若写 Markdown 失败，下一次同步会因 hash 不一致
以磁盘上的 Markdown 为准重建 sidecar（Markdown 始终是权威）；若 JSONL 写失败，Markdown
由调用方回退路径写入，最坏退化为旧行为。

## 5. 校验、去重与预算

### 5.1 校验规则

- 空内容（折叠空白后为空）拒绝；
- `value` 单条上限：默认 `max(64, max_chars - 16)`（会话级约 584，长期级约 1484），超出截断并追加 `…`；
- `key` 上限 80；`category` 缺省 `未分类`；`source` 缺省 `unknown`；
- 条目总数上限 50（`MAX_ENTRIES`），超限丢弃**最早写入**的条目（新写入优先保留）；
- 密钥 / PII 简单扫描后**脱敏**而不是整条丢弃（保留可用信息，避免明文落盘）：
  - `password|passwd|pwd|secret|token|api_key|access_key|private_key|client_secret|authorization` + `:`/`=`/`：` + 值；
  - 中文 `密码|密钥|口令|令牌` + 分隔符 + 值；
  - `Bearer <token>`、`-----BEGIN ... PRIVATE KEY-----`；
  - 12 位 Aadhaar（允许 `1234 5678 9012` 分组）、18 位中国身份证号；
  - 命中值替换为 `[REDACTED:<kind>]`，日志只输出类型与内容 hash。
- 扫描是启发式的：会漏掉变形密钥，也可能误伤形如 12 位数字的业务编号（设计上接受，见 §8）。

### 5.2 去重 / supersede

- 规范化 key 相同即视为同一条记忆；
- 内容与分组都相同：幂等，保持原 `version` / `updated_at` / `source`；
- 内容或分组变化：以新值为准，`version = 旧 version + 1`，`updated_at` 更新，`id` 不变；
- 版本号在 sidecar 中保留，因此连续多轮更新可看出哪些条目被反复改写；
- 解析出的重复条目（LLM 同一轮输出两条同 key）在 `apply_entries` 中合并，Markdown 只渲染最新一条。

### 5.3 预算裁剪

`fit_entries_to_budget` 保证渲染结果长度 `<= max_chars`（会话 600 / 长期 1500）：

1. 整篇渲染，未超限直接返回；
2. 超限时从文档尾部开始，先截短最后一条的 `value`（低于 16 字符则整条丢弃）；
3. 截短后按新内容重算 `key`，保证 sidecar 与重新解析 Markdown 得到的 key 一致；
4. 极端情况下（标题本身超预算）返回空条目与截断后的标题。

## 6. 兼容策略

| 关注点 | 兼容方式 |
| --- | --- |
| 函数签名 | `read_memory` / `write_memory` / `enforce_memory_limit` / `update_memory_after_session` / `update_long_term_memory_after_session` 参数与返回值不变 |
| Markdown 读取 | 读取路径、虚拟文件挂载（`/workspace/memory.md`、`/memories/AGENTS.md`）不变；Markdown 仍由 `read_memory` 与 agent 直接读取 |
| Markdown 结构 | 渲染仍是 `# 会话记忆` / `# 用户长期记忆` + `## 分组` + `- 条目`，旧文件可被同一解析器读回 |
| 提示词 | `memory_update` / `long_term_memory` / `memory_compress` 模板不改；LLM 输出格式不变 |
| 日志 | INFO 不再打印正文，改为 `长度 + 条目数 + sha256 + 耗时`（store 日志同样不含正文） |
| 旧文件 | 首次更新 / `write_memory` 时自动导入 sidecar；导入只读 Markdown，不改写原文 |
| 旧代码 / 其他模块 | 不感知 sidecar；删除 sidecar 不影响任何读取 |
| 异常 | 解析空、写入异常均回退旧 Markdown 路径 |

注意：旧 Markdown 首次导入时只做解析，**不会清洗 Markdown 原文**；若历史文件里已有明文密钥，
要等下一次结构化写入（由条目重新渲染）才会被脱敏。这是"不破坏旧文件"的取舍，见 §8。

## 7. 迁移与回退

### 迁移

1. 部署新版本，无需改配置、无需数据迁移；
2. 首次 `update_*` 或 `write_memory` 时，从现有 Markdown 导入条目（`revision = 1`）；
3. 读取方始终读 Markdown，所以升级过程中不存在"读到空记忆"的窗口；
4. 若 sidecar 丢失，下一次写入会重新导入，只是 `version` 从头计数。

### 回退

- 关闭结构化：`EASY_STRUCTURED_MEMORY=0`（也接受 `false/off/no/disabled`），
  写路径完全走旧逻辑，不创建 / 不读取 sidecar；
- 彻底回退：删除 `*.jsonl` sidecar 即可，Markdown 不受影响；
- 代码回退：旧版本代码不读 sidecar，新文件被忽略；
- 损坏修复：手工删掉 sidecar 或删除损坏行；损坏行在读取时已跳过，不会阻塞。

## 8. 风险与边界

1. **并发**：会话级与会话级、用户级与用户级之间可能并发写同一文件（记忆更新跑在
   2 线程的 `_MEMORY_UPDATE_EXECUTOR` 中；同一用户多个会话共享 `AGENTS.md`）。
   本实现只依赖 `os.replace` 的原子性，未加文件锁，最后写入者胜出——这是改造前就存在的风险；
2. **多实例**：sidecar 与 Markdown 都在本地文件系统，多副本部署时各实例各写各的，
   与现有工作区/记忆目录的假设一致，不在本阶段解决；
3. **敏感扫描**：启发式规则，存在漏报（变形密钥、图片/编码内容）与误报（12 位业务编号被脱敏）；
   脱敏后内容仍进入 Markdown（这是记忆本身要保存的信息），但日志不再输出正文；
4. **历史明文**：旧 Markdown 导入不清洗原文（见 §6 注意事项）；
5. **预算裁剪**：超预算时可能截短或丢弃文档尾部条目，属于有损压缩；LLM 压缩仍是主要手段，
   代码侧裁剪只是硬保底；
6. **版本语义**：`version` 只表示"该 key 被改写的次数"，不保留旧值正文，不能当完整审计日志；
7. **外部直接编辑 Markdown**：agent 的 `write_file` 或用户手工修改后，内容 hash 不匹配，
   下次同步会以 Markdown 为准重建 sidecar，此时版本号会重置；
8. **key 碰撞**：不同条目若第一处分隔符之前完全相同（如 `数据库：MySQL` / `数据库：PostgreSQL`），
   会被视为同一条记忆并保留最新值；需要区分时应让 LLM 写出不同前缀；
9. **sidecar 可见性**：`memory.jsonl` / `AGENTS.jsonl` 位于 agent 可访问的工作区 / 记忆目录，
   可能被 `ls` 看到或误删；误删后下一次写入会从 Markdown 重建，不影响读取。

## 9. 测试策略

单测文件 `tests/services/test_memory_store.py`，全部使用 `tmp_path` 与 fake LLM，不联网、不依赖数据库：

- key 规范化与稳定性、空 key 兜底；
- 脱敏（password/token/Aadhaar）、截断、空白折叠、空内容拒绝；
- 同 key supersede（version+1、id 稳定、幂等重复写、同批次重复 key 合并）；
- JSONL 原子写（无 `.tmp` 残留）、读回、损坏行容错、`max_entries` 裁剪；
- Markdown 渲染顺序、旧文件解析（分组 / 续行 / 代码块 / 水平线）、标题提取；
- `write_structured_memory` 去重、预算裁剪、无条目返回 `None`；
- `sync_from_markdown` 首次导入 + 幂等；
- 环境开关映射；
- `memory_manager` 集成：Markdown 与 JSONL 一致、旧文件迁移后版本延续、解析失败回退原文、
  开关关闭不产生 sidecar、密钥不落 Markdown/JSONL、长期记忆分组与 source、直接 `write_memory` 回灌 sidecar、
  日志不含全文且包含 hash/条目数。

验证命令（本次只跑相关用例，未跑全量，全量由 Lead 统一执行）：

```bash
python -m py_compile easy_agent/services/memory_store.py easy_agent/services/memory_manager.py
python -m pytest tests/services/test_memory_store.py tests/services/test_memory_manager.py \
  tests/services/test_memory_executor.py -q
uvx ruff check .
```

结果：新增 26 个用例全部通过，三个相关文件共 36 passed；`ruff check .` 通过。

## 10. 后续可做（不在本阶段）

- 记忆入 DB（与多租户、跨副本共享一起做）；
- 保留 superseded 历史值 / 审计事件；
- 跨进程文件锁或单写者队列；
- 基于 embedding 的语义去重（当前只按 key 精确去重）；
- confidence 由 LLM 输出并在渲染时过滤低置信条目；
- sidecar 指标（条目数、脱敏次数、回退次数）接入现有 metrics。
