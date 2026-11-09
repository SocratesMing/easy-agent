# 数据库表使用情况（2026-10-28）

## 口径

- **代码**：分支 `refactor/audit-improvements-2026-10-18` @ `addcd67`，`SCHEMA_VERSION = 12`。之后已在 schema 13 把其中 8 张退役，见第一节的「处理结果」。
- **怎么判断「在用」**：在后端代码（`easy_agent/`，不含测试）里按表名查读写 SQL，再逐个看是哪个模块、哪个功能在用。
- **字段**：取自本地开发 MySQL 的 `information_schema`。这个库已升到 schema 12，26 张在用表的字段和代码建表语句逐列对比，完全一致。
- **行数**：本地开发库的行数，只用来判断表是不是空的，不代表现网。
- **约定**：时间字段大多是 `VARCHAR(50)`，存 ISO 时间字符串。
- **代码里没有读写的字段**：字段名在 `easy_agent/` 里只出现在建表语句中。

## 总览

本地库共 35 张表，分两类：

- **26 张在用**：后端代码都有读写。
- **9 张可以删**：后端代码不读不写。
  - `market_mcp_api_keys`、`forex_rates`、`precious_metals` 已确认可以删。
  - 另外 6 张经分析也没有代码使用。

| 分组 | 表 | 本地行数 | 结论 |
|---|---|---:|---|
| 会话与消息 | `sessions` | 1053 | 在用 |
| | `session_messages` | 2165 | 在用 |
| | `session_files` | 6 | 在用 |
| | `generated_files` | 167 | 在用 |
| | `session_runtime` | 54 | 在用 |
| | `stream_events` | 2095 | 在用 |
| | `agent_runs` | 76 | 在用 |
| 用户与密钥 | `users` | 93 | 在用 |
| | `mcp_api_keys` | 2 | 在用 |
| 专家与定时任务 | `expert_agents` | 1 | 在用 |
| | `scheduled_tasks` | 3 | 在用 |
| | `scheduled_task_runs` | 781 | 在用 |
| 基础设施 | `schema_meta` | 3 | 在用 |
| | `distributed_locks` | 0 | 在用（平时为空） |
| | `rate_limit_counters` | 0 | 在用（单进程时为空） |
| | `langgraph_store` | 0 | 在用（默认配置下为空） |
| 知识库 | `knowledge_bases` | 1 | 在用 |
| | `knowledge_folders` | 0 | 在用 |
| | `knowledge_documents` | 2 | 在用 |
| | `knowledge_document_objects` | 2 | 在用 |
| | `knowledge_operations` | 3 | 在用 |
| | `knowledge_permissions` | 0 | 在用 |
| | `knowledge_team_space_managers` | 0 | 在用 |
| | `knowledge_source_sync_items` | 0 | 在用 |
| | `knowledge_audit_events` | 1115 | 在用 |
| | `session_knowledge_scopes` | 0 | 在用 |
| 不再使用 | `market_mcp_api_keys` | 1 | schema 13 退役 |
| | `forex_rates` | 702 | schema 13 退役 |
| | `precious_metals` | 384 | schema 13 退役 |
| | `knowledge_alerts` | 0 | schema 13 退役 |
| | `knowledge_reconciliation_runs` | 119 | schema 13 退役 |
| | `knowledge_reconciliation_issues` | 4 | schema 13 退役 |
| | `knowledge_runtime_heartbeats` | 1 | schema 13 退役 |
| | `knowledge_schema_migrations` | 4 | schema 13 退役 |
| | `session_llm` | 1 | 无代码使用，归属确认前保留 |

升级到 schema 13 后，本地库是 27 张：26 张在用，加上保留的 `session_llm`。

---

## 一、代码不再使用的 9 张表

| 表 | 来源 | 本地行数 | 处理 |
|---|---|---:|---|
| `market_mcp_api_keys` | 旧的 market 业务 MCP key 表。现在统一用 `mcp_api_keys`（`business` 列区分业务） | 1 | 已确认不用，直接删。`scripts/db/migrate_market_mcp_keys.py` 随之不再需要，可以一起删 |
| `forex_rates` | 外汇牌价，只有 `tests/helpers/download_market_data.py` 建表并写入 | 702 | 已确认与后端无关，删除。这个辅助脚本再跑会重新建表，要不要一起删需要确认 |
| `precious_metals` | 贵金属行情，来源同上。mcp-server 和主应用共用一个库，但它的 market 业务用自己的 `market_quotes` / `market_positions` 表，`precious_metals` 在那边只是分类字符串 | 384 | 已确认与后端无关，删除 |
| `knowledge_alerts` | 旧版知识库模块 `easy_agent/knowledge/` 的告警表。该模块在 `6245747d`（2026-09-24）删除 | 0 | 无代码读写，可删 |
| `knowledge_reconciliation_runs` | 旧版知识库模块：本地库与 RAGFlow 的对账运行记录 | 119 | 无代码读写，可删 |
| `knowledge_reconciliation_issues` | 旧版知识库模块：对账发现的问题 | 4 | 无代码读写，可删 |
| `knowledge_runtime_heartbeats` | 旧版知识库模块：后台组件心跳 | 1 | 无代码读写，可删 |
| `knowledge_schema_migrations` | 旧版知识库模块自己的迁移账本。现在统一记在 `schema_meta` | 4 | 无代码读写，可删 |
| `session_llm` | 本仓库从来没建过这张表，git 历史里没有它的建表语句。`services/streaming/side_effects.py` 里的 `session_llm` 只是变量名。字段（`user`、`bank_id`、`llm_param`、`msg`、`ip`）看起来是另一个系统的会话表 | 1 | 后端不用。现网删之前确认没有别的系统在用 |

处理结果（schema 13）：

- `market_mcp_api_keys`、`forex_rates`、`precious_metals` 加进了 `easy_agent/db/schema.py` 的 `_RETIRED_TABLES`。
- 5 张旧版知识库表加进了 `knowledges/core/schema.py` 的 `_cleanup_legacy_schema`。`knowledge_reconciliation_issues.run_id` 有外键指向 `knowledge_reconciliation_runs`，所以先删 issues 再删 runs；顺序反了的话，MySQL 会拦下对 runs 的删除。
- `SCHEMA_VERSION` 升到 13。升级后第一次启动会跑一次完整初始化，把它们删掉。
- `scripts/db/migrate_market_mcp_keys.py` 已删除。
- `session_llm` 先保留，确认没有别的系统在用之后再处理。

<details>
<summary>9 张可删表的字段（存档用）</summary>

- `market_mcp_api_keys`：`username` varchar(64) 主键，`api_key_hash` char(64) 唯一，`created_at` datetime，`updated_at` datetime，`revoked` tinyint(1)
- `forex_rates`：`id` bigint 自增，`currency`，`date`，`buy_price`（中行汇买价），`cash_buy_price`（中行钞买价），`sell_price`（中行钞卖价/汇卖价），`central_parity`（央行中间价），`boc_rate`（中行折算价），`created_at`。唯一键 `(currency, date)`
- `precious_metals`：`id` bigint 自增，`symbol`，`date`，`open`，`high`，`low`，`close`，`volume`，`position`（持仓量），`created_at`。唯一键 `(symbol, date)`
- `knowledge_alerts`：`id`，`dedup_key` 唯一，`alert_type`，`severity`，`status`，`summary`，`details_json`，`first_seen_at`，`last_seen_at`，`resolved_at`
- `knowledge_reconciliation_runs`：`id`，`status`，`request_id`，`summary_json`，`error_message`，`started_at`，`completed_at`
- `knowledge_reconciliation_issues`：`id`，`run_id`，`issue_key` 唯一，`issue_type`，`severity`，`base_id`，`document_id`，`status`，`details_json`，`first_seen_at`，`last_seen_at`，`resolved_at`，`resolution_note`
- `knowledge_runtime_heartbeats`：`component` + `instance_id` 联合主键，`status`，`details_json`，`last_seen_at`
- `knowledge_schema_migrations`：`version` 主键，`name`，`checksum`，`applied_at`
- `session_llm`：`id` 自增，`user`，`title`，`agent`，`bank_id`，`llm_param` json，`msg` json，`ip`，`pinned`，`create_time`，`update_time`

</details>

---

## 二、在用的 26 张表

### 会话与消息

#### `sessions`：会话元数据（1053 行）

- **使用场景**：会话列表（侧边栏的标题、置顶、排序、条数）、会话工作区名、agent 的待办列表。消息正文不在这张表，在 `session_messages`。
- **读写位置**：`db/repositories/sessions.py`、`api/sessions.py`、`db/legacy_messages.py`（启动修复和迁移 6）

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | varchar(255) 主键 | 会话 ID |
| `title` | text | 会话标题 |
| `messages` | longtext | 旧版整段消息 JSON（2026-10-18 改成行表之前的写法）。新会话写空串，迁移 6 清空了已被行表覆盖的部分，现在只作为老会话的兜底读取。**本地库已全部为空** |
| `created_at` | varchar(50) | 创建时间 |
| `updated_at` | varchar(50) | 最后更新时间，会话列表按它排序 |
| `username` | varchar(255) | 所属用户 |
| `workspace_name` | varchar(255) | 会话工作区的目录名 |
| `todos` | text | agent `write_todos` 写的待办列表（JSON） |
| `pinned` | int | 是否置顶 |
| `message_count` | int | 消息条数，会话列表展示用，增删消息时同步维护 |

索引：`(username, pinned, updated_at)`、`(pinned, updated_at)`。

#### `session_messages`：消息正文（2165 行）

- **使用场景**：一条消息一行，是消息的权威来源。
- **读写位置**：`db/repositories/sessions.py`、`db/session_messages.py`、`services/streaming/persistence.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int 自增主键 | 消息行 ID，同时决定顺序 |
| `session_id` | varchar(255) | 所属会话 |
| `role` | varchar(20) | `user` / `assistant` |
| `content` | mediumtext | 消息正文 |
| `extra_data` | mediumtext | 附加 JSON：`blocks`（思考、工具调用等分块）、`thinking`、`tool_calls`、`turn_id` 等 |
| `created_at` | varchar(50) | 创建时间 |

索引：`(session_id)`。

#### `session_files`：会话上传的文件（6 行）

- **使用场景**：用户在会话里上传的文件记录（输入框上传、文件接口），工作区面板和文件读取工具都会用到。
- **读写位置**：`db/repositories/files.py`、`api/sessions.py`、`services/session_uploads.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int 自增主键 | |
| `session_id` | varchar(255) | 所属会话 |
| `filename` | text | 原文件名 |
| `file_path` | text | 存储路径：本地路径，或 `store://uploads/...`（上传存储为 store 模式时） |
| `file_type` | varchar(50) | 文件类型 |
| `size` | int | 字节数 |
| `uploaded_at` | varchar(50) | 上传时间 |
| `username` | varchar(255) | 上传人 |

#### `generated_files`：agent 生成的文件（167 行）

- **使用场景**：agent 在某条回答里生成的文件，用于消息下方的文件卡片和下载。
- **读写位置**：`db/repositories/files.py`、`services/streaming/side_effects.py`、`api/files.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int 自增主键 | |
| `session_id` | varchar(255) | 所属会话 |
| `message_id` | varchar(255) | 生成它的那条助手消息 |
| `filename` | text | 文件名 |
| `file_path` | text | 文件路径 |
| `file_type` | varchar(50) | 文件类型 |
| `size` | int | 字节数 |
| `created_at` | varchar(50) | 生成时间 |

#### `session_runtime`：会话运行时状态（54 行）

- **使用场景**：多个副本之间共享的会话状态，包括：
  - 当前由谁在推流（租约），防止同一会话被两个实例同时生成；
  - 「停止生成」请求；
  - 会话启用的连接器；
  - 专家编辑会话的目标专家。
- **读写位置**：`db/repositories/session_runtime.py`、`services/agent_manager.py`、`services/session_runtime.py`、`api/chat_live.py`、`services/run_registry.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | varchar(255) 主键 | 会话 ID |
| `connectors` | text | 本会话启用的 MCP 连接器（JSON），由 `/agent/connectors/session` 写入 |
| `edit_target` | varchar(255) | 专家编辑会话正在编辑的 `expert_agent_id` |
| `stream_owner` | varchar(255) | 当前持有推流租约的实例 |
| `stream_until` | varchar(50) | 推流租约到期时间 |
| `cancel_requested` | int | 是否请求了停止生成 |
| `updated_at` | varchar(50) | 更新时间 |

#### `stream_events`：SSE 事件缓冲（2095 行）

- **使用场景**：把推给前端的 SSE 事件按顺序存下来。断线重连或刷新页面后，前端按 `event_id` 续播。
- **清理**：只保留 24 小时，由周期恢复循环分批清理（每轮最多 20 批、每批 5000 行）。本地最早一条是 6 小时前的，清理正常。
- **读写位置**：`services/stream_events.py`、`db/repositories/stream_events.py`、`api/chat_live.py`、`services/run_registry.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `event_id` | bigint 自增主键 | 续播游标 |
| `session_id` | varchar(255) | 所属会话 |
| `payload` | longtext | 事件内容（JSON） |
| `created_at` | varchar(50) | 写入时间，24 小时清理按它判断 |

索引：`(session_id, event_id)`、`(created_at)`。

#### `agent_runs`：生成任务登记（76 行）

- **使用场景**：每次生成登记一行。多副本下靠租约（`lease_owner` / `lease_until`）认领和续租。实例宕机后，租约过期的运行会被标记为中断，前端据此提示。
- **读写位置**：`db/repositories/agent_runs.py`、`services/run_registry.py`、`api/sessions.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | varchar(255) 主键 | 运行 ID |
| `session_id` | varchar(255) | 所属会话 |
| `username` | varchar(255) | 发起人 |
| `message_id` | varchar(255) | 这次生成的助手消息 ID |
| `status` | varchar(32) | `running` / `completed` / `failed` / `cancelled` / `interrupted` |
| `request_json` | longtext | 这次生成的请求参数（JSON） |
| `lease_owner` | varchar(255) | 持有租约的实例 |
| `lease_until` | varchar(50) | 租约到期时间 |
| `error` | text | 失败原因 |
| `created_at` / `updated_at` | varchar(50) | 创建 / 更新时间 |

索引：`(session_id, created_at)`、`(status, lease_until)`。

### 用户与密钥

#### `users`：账号与人员信息（93 行）

- **使用场景**：
  - 登录：bcrypt 密码；单点登录，新登录让 `token_version` 加 1；空闲登出看 `last_activity_at`。
  - 人员管理：Excel 导入，写工号、部门、处室、团队等信息。
  - 知识库权限：按部门判断。
- **读写位置**：`db/repositories/users.py`、`api/auth.py`、`knowledges/personnel/*`、`knowledges/core/repository.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `user_id` | varchar(255) 主键 | 用户 ID |
| `username` | varchar(255) 唯一 | 登录名 |
| `password_hash` | varchar(255) | bcrypt 哈希 |
| `organization_id` | varchar(255) | 机构 ID，个人资料接口返回 |
| `email` | varchar(255) | 邮箱，可在个人资料里修改 |
| `created_at` / `updated_at` | varchar(50) | 创建 / 更新时间 |
| `bound_ip` | varchar(45) | 旧的 IP 绑定字段。仍随用户读写、在资料接口返回，但**登录已不再校验 IP**。本地有 6 个用户有值 |
| `token_version` | int | 单点登录版本号，新登录加 1，旧 token 随之失效 |
| `last_activity_at` | double | 最后活动时间，空闲登出以它为准 |
| `employee_id` | varchar(255) 唯一 | 工号，人员导入的匹配键 |
| `display_name` | varchar(127) | 姓名 |
| `department_id` / `department_name` | varchar(255) | 部门 ID / 名称 |
| `position` | varchar(255) | 职位 |
| `mobile` | varchar(64) | 手机号 |
| `account_status` | varchar(20) | 账号状态，默认 `active` |
| `personnel_source` | varchar(255) | 人员信息来源 |
| `division_name` | varchar(255) | 处室 |
| `team_name` | varchar(255) | 团队 |

#### `mcp_api_keys`：MCP API Key（2 行）

- **使用场景**：MCP API Key，按业务区分，只存 sha256 哈希。mcp-server 和主应用共用这个库，鉴权时只读这张表。
- **读写位置**：
  - 读：`mcp-server/easy_mcp_server/keys.py`（校验）。
  - 签发：主应用 `db/repositories/mcp_api_keys.py` 里签发、吊销、查询的函数还在，但**已经没有调用方**（README：设置页不再签发）。现在谁来写入这张表，需要确认。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int 自增主键 | |
| `username` | varchar(64) | 用户 |
| `business` | varchar(64) | 业务名；`(username, business)` 唯一 |
| `key_hash` | char(64) 唯一 | key 的 sha256 |
| `created_at` / `updated_at` | varchar(50) | 签发 / 更新时间 |
| `revoked` | int | 是否已吊销 |

### 专家与定时任务

#### `expert_agents`：专家智能体（1 行）

- **使用场景**：专家中心的增删改查和发布；技能 `expert-creator` 里的 `save_expert` 写入；公共专家从配置同步过来。
- **命名**：字段的 `export_` 前缀是历史命名，实际含义是 expert。
- **读写位置**：`db/expert_store.py`、`api/expert.py`、`services/public_expert_service.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `export_agent_id` | varchar(255) 主键 | 专家 ID |
| `session_id` | varchar(255) | 创建它的会话 |
| `user_id` | varchar(255) | 所有者 |
| `tag_id` | varchar(255) | 分类标签，由大模型按配置的 `expert_tags` 自动归类，不接收前端传参 |
| `export_name_en` / `export_name_cn` | varchar(255) | 英文名 / 中文名 |
| `export_prompt` | text | 专家系统提示词 |
| `export_description` | text | 简介 |
| `export_skills_names` | text | 绑定的技能名列表 |
| `export_features_prompt` | text | 特色提示语列表（公共专家取自配置的 `features_prompt`） |
| `export_file_path` | varchar(1024) | 提示词文件路径（公共专家用） |
| `agent_status` | varchar(32) | 状态，默认 `active`，可停用 |
| `export_version` | int | 版本号 |
| `create_timestamp` / `update_timestamp` | varchar(50) | 创建 / 更新时间 |

#### `scheduled_tasks`：定时任务（3 行）

- **使用场景**：用户配置的定时任务（cron 表达式加提示词）。调度器按 `next_run_at` 触发，多副本下用分布式锁保证只有一个实例执行。
- **读写位置**：`db/repositories/scheduled_tasks.py`、`services/scheduler.py`、`api/scheduled_tasks.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | varchar(255) 主键 | 任务 ID |
| `username` | varchar(255) | 所属用户 |
| `session_id` | varchar(255) | 任务绑定的会话 |
| `name` / `description` | varchar(255) / text | 名称 / 描述 |
| `schedule_cron` | varchar(255) | cron 表达式 |
| `task_prompt` | text | 每次执行发给 agent 的提示词 |
| `enabled` | int | 是否启用 |
| `created_at` / `updated_at` | varchar(50) | 创建 / 更新时间 |
| `last_run_at` / `next_run_at` | varchar(50) | 上次 / 下次执行时间 |
| `workspace_name` | varchar(255) | 任务使用的工作区名，为空时按 `session_id` 推导 |

#### `scheduled_task_runs`：定时任务执行记录（781 行）

- **使用场景**：每次执行一行，用于任务详情里的执行历史。
- **注意**：**没有清理策略**。本地 3 个任务已经积累了 781 行。
- **读写位置**：`db/repositories/scheduled_tasks.py`、`services/scheduler.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | varchar(255) 主键 | 执行 ID |
| `task_id` | varchar(255) | 所属任务 |
| `session_id` | varchar(255) | 这次执行用的会话 |
| `status` | varchar(20) | 执行状态 |
| `started_at` / `finished_at` | varchar(50) | 开始 / 结束时间 |
| `result_summary` | text | 结果摘要 |
| `error_message` | text | 错误信息 |

### 基础设施

#### `schema_meta`：库结构版本与迁移账本（3 行）

- **使用场景**：
  - `schema_version` 记录当前结构版本。启动时与代码里的版本一致就走快速路径，否则跑完整初始化。
  - `migration:<版本号>` 是已执行迁移的账本。旧的 `schema_migrations` 表已经并进来了。
- **读写位置**：`db/schema.py`、`db/migrations.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `meta_key` | varchar(64) 主键 | `schema_version`，或 `migration:<n>` |
| `meta_value` | varchar(255) | `schema_version` 存版本号；`migration:<n>` 存「执行时间 + 迁移说明」 |

#### `distributed_locks`：分布式锁（平时为空）

- **使用场景**：多副本之间互斥，包括调度器选主和单个任务互斥、知识库后台 worker，在开启分布式锁配置时生效。锁有过期时间，释放就删行，所以平时为空。启动迁移的锁用的是 MySQL `GET_LOCK`，不走这张表。
- **读写位置**：`utils/distributed_lock.py`；使用方是 `services/scheduler.py`、`knowledges/runtime/worker.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `lock_key` | varchar(255) 主键 | 锁名 |
| `owner` | varchar(255) | 持有者 |
| `acquired_at` | varchar(50) | 获取时间 |
| `expires_at` | varchar(50) | 过期时间，过期锁可被清理或抢占 |
| `updated_at` | varchar(50) | 最近续期时间 |

#### `rate_limit_counters`：接口限流计数（单进程时为空）

- **使用场景**：跨进程共享的限流计数。以下两种情况才用这张表：
  - `EASY_RATE_LIMIT_BACKEND=db`；
  - `auto`（默认值）并且多 worker。

  单进程默认用内存计数，所以本地为空。
- **读写位置**：`middleware/rate_limit.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `bucket` | varchar(512) | 限流键 |
| `window_start` | int | 时间窗口起点；与 `bucket` 组成联合主键 |
| `count` | int | 窗口内的请求数 |
| `updated_at` | varchar(50) | 更新时间 |

#### `langgraph_store`：LangGraph Store（默认配置下为空）

- **使用场景**：LangGraph Store 的业务库实现（`persistence/stores.py` 里的 SqlStore）。
  - agent 运行时会把它交给 graph（`agent/core.py`）。
  - `EASY_UPLOAD_BACKEND=store` 时，上传文件的字节存在这里，路径形如 `store://uploads/...`。
  - 默认是 `local` 上传，没有写入，所以本地为空。
- **读写位置**：`db/repositories/agent_state.py`、`persistence/stores.py`、`services/upload_storage.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `namespace` | varchar(512) | 命名空间路径 |
| `item_key` | varchar(191) | 键；191 是 MySQL utf8mb4 索引长度的上限。与 `namespace` 组成联合主键 |
| `value` | longtext | 值（JSON） |
| `created_at` / `updated_at` | varchar(50) | 创建 / 更新时间 |

### 知识库

这一组都由 `knowledges/core/schema.py` 建表，主要读写都在 `knowledges/core/repository.py`。本地多张表为空，是因为本地没用到对应功能（文件夹、授权、团队空间、来源同步、会话选库）。

#### `knowledge_bases`：知识库（1 行）

- **使用场景**：一个知识库对应 RAGFlow 里的一个 dataset，分个人、团队、共享三种空间。支持软删除。
- **访问限制**：`access_restricted=1` 的库只有所有者、空间管理员和单独授权的人能看到。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | 知识库 ID |
| `remote_dataset_id` | varchar(255) 唯一 | RAGFlow dataset ID |
| `name` / `description` | varchar(127) / text | 名称 / 描述 |
| `space_type` | varchar(20) | `personal` / `team` / `shared` |
| `owner_user_id` | varchar(255) | 所有者 |
| `department_id` | varchar(255) | 团队空间所属部门 |
| `embedding_model` | varchar(255) | 向量模型 |
| `status` | varchar(30) | `creating` / `active` / `offline` / `deleting` / `failed` / `deleted` |
| `created_at` / `updated_at` | varchar(50) | 创建 / 更新时间 |
| `deleted_at` / `deleted_by` | varchar | 软删除时间 / 操作人 |
| `purge_after` | varchar(50) | **代码里没有读写** |
| `access_restricted` | int | 是否限制访问 |

#### `knowledge_folders`：文件夹（0 行）

- **使用场景**：知识库里的文件夹树，支持文件夹导入。同一父目录下不能重名。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | 文件夹 ID |
| `base_id` | varchar(255) | 所属知识库 |
| `parent_id` | varchar(255) | 父文件夹，根级为 NULL |
| `name` | varchar(127) | 名称；`(base_id, parent_id, name)` 唯一 |
| `sort_order` | int | 排序 |
| `created_at` / `updated_at` | varchar(50) | 创建 / 更新时间 |

#### `knowledge_documents`：知识库文档（2 行）

- **使用场景**：一个文档对应 RAGFlow 里的一个 document。记录：
  - 解析状态和进度；
  - 所在文件夹；
  - 来源同步信息；
  - 报告元数据，检索时按发布日期、来源等过滤。
- **读写位置**：`knowledges/core/repository.py`、`knowledges/runtime/worker.py`、`knowledges/runtime/source_sync.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | 文档 ID |
| `remote_document_id` | varchar(255) 唯一 | RAGFlow 文档 ID |
| `base_id` / `folder_id` | varchar(255) | 所属知识库 / 文件夹 |
| `name` / `content_type` / `size_bytes` | | 文件名 / MIME 类型 / 字节数 |
| `status` | varchar(30) | `pending` / `processing` / `cancel_requested` / `cancelled` / `paused` / `ready` / `failed` / `deleting` / `deleted` / `unknown` |
| `progress` | double | 解析进度 |
| `error_code` / `error_message` | | 失败原因 |
| `created_by` / `created_at` / `updated_at` | | 上传人 / 创建 / 更新时间 |
| `deleted_at` / `deleted_by` | | 软删除时间 / 操作人 |
| `purge_after` | varchar(50) | **代码里没有读写** |
| `previous_status` | varchar(30) | **代码里没有读写** |
| `document_version` | int | **代码里没有读写** |
| `source` / `source_record_id` | | 来源系统 / 来源记录 ID（文档同步写入）；`(source, source_record_id, base_id)` 唯一 |
| `report_title` / `report_tag` / `report_second_source` / `publish_date` | | 报告元数据，SQL 批量同步写入，检索时用于过滤 |
| `parse_started_at` | varchar(50) | 开始解析的时间 |

#### `knowledge_document_objects`：文档原文件（2 行）

- **使用场景**：文档原文件的存储对象（目前是本地文件系统），供「下载原文」和重新上传使用，带 sha256 校验，按版本区分。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | |
| `document_id` / `object_version` | | 所属文档 / 版本；两者联合唯一 |
| `provider` / `storage_key` | | 存储后端（如 `filesystem`）/ 存储键；两者联合唯一 |
| `status` | varchar(30) | 存储状态 |
| `original_name` / `content_type` / `size_bytes` / `sha256` | | 原文件名 / 类型 / 大小 / 摘要 |
| `error_code` / `error_message` | | 存储失败原因 |
| `created_by` / `created_at` / `stored_at` / `deleted_at` / `updated_at` | | 时间与操作人 |
| `quarantined_at` | varchar(50) | **代码里没有读写** |
| `last_verified_at` | varchar(50) | **代码里没有读写** |

#### `knowledge_operations`：异步操作进度（3 行）

- **使用场景**：建库、上传、解析等异步操作的进度和结果，前端轮询显示。
- **注意**：没有清理策略。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | 操作 ID |
| `type` | varchar(40) | `dataset_create` / `document_upload` / `document_parse` |
| `resource_type` / `resource_id` | | 操作对象 |
| `phase` | varchar(40) | `queued` / `storing_original` / `uploading` / `configuring` / `parsing` / `deleting` / `reconciling` / `completed` / `failed` |
| `status` | varchar(40) | `accepted` / `running` / `succeeded` / `partial_failed` / `failed` / `cancel_requested` / `cancelled` / `dead_letter` |
| `current_count` / `total_count` / `progress` | | 进度 |
| `retryable` | int | 能否重试 |
| `error_code` / `error_message` | | 失败原因 |
| `created_by` / `created_at` / `updated_at` | | 发起人 / 时间 |

#### `knowledge_permissions`：知识库授权（0 行）

- **使用场景**：按用户或部门授予某个知识库的角色。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | |
| `base_id` | varchar(255) | 知识库 |
| `subject_type` / `subject_id` | | `user` / `department` 及其 ID；`(base_id, subject_type, subject_id)` 唯一 |
| `role` | varchar(20) | `viewer` / `maintainer` / `manager` |
| `created_by` / `created_at` / `updated_at` | | 授权人 / 时间 |

#### `knowledge_team_space_managers`：团队空间管理员（0 行）

- **使用场景**：在人员管理里授予「可以管理团队空间」的名单。

| 字段 | 类型 | 说明 |
|---|---|---|
| `user_id` | varchar(255) 主键 | 被授权人 |
| `granted_by` | varchar(255) | 授权人 |
| `granted_at` / `updated_at` | varchar(50) | 授权 / 更新时间 |

#### `knowledge_source_sync_items`：来源文档同步记录（0 行）

- **使用场景**：外部来源的文档同步进知识库，每条来源记录一行，用于去重和失败重试。
- **读写位置**：`knowledges/runtime/source_sync.py`；入口脚本是 `knowledges/scripts/sync_source_docs.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | |
| `source` / `source_record_id` | | 来源系统 / 来源记录 ID；两者联合唯一 |
| `document_id` | varchar(255) | 对应的本地文档 |
| `status` | varchar(30) | 同步状态 |
| `attempts` / `last_error` | | 尝试次数 / 最近一次错误 |
| `publish_date` | varchar(10) | 发布日期 |
| `created_at` / `updated_at` | varchar(50) | 时间 |

#### `knowledge_audit_events`：知识库操作审计（1115 行）

- **使用场景**：记录谁对哪个对象做了什么、结果如何。原文下载审计也并到了这里（`action` 为 `knowledge.original.*`）。
- **注意**：**没有清理策略**，本地已有 1115 行。
- **读写位置**：`knowledges/core/operations_repository.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(255) 主键 | |
| `request_id` | varchar(128) | 请求 ID |
| `actor_user_id` / `actor_username` | | 操作人 |
| `action` | varchar(80) | 动作 |
| `object_type` / `object_id` / `base_id` | | 操作对象及所属知识库 |
| `outcome` / `reason_code` | | 结果 / 原因码 |
| `details_json` | text | 详情 |
| `created_at` | varchar(50) | 时间 |

索引：`(actor_user_id, created_at)`、`(object_type, object_id, created_at)`、`(request_id, created_at)`。

#### `session_knowledge_scopes`：会话选中的知识库（0 行）

- **使用场景**：会话里勾选的知识库，即问答时的检索范围，按用户区分。

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` / `base_id` / `user_id` | varchar(255) | 联合主键 |
| `created_at` | varchar(50) | 选择时间 |
| `sort_order` | int | 排序 |

---

## 三、发现的问题与精简建议

1. **退役 8 张表**：schema 13 已处理，35 张降到 27 张。剩下的 `session_llm` 确认归属后再定，见第一节。
2. **`mcp_api_keys` 没有写入方**：主应用的签发、吊销函数已经没有调用方。要么确认 key 由别处写入，要么把这些死代码删掉。
3. **6 个字段代码里没有读写**，可以随下次表结构变更一起删：
   - `knowledge_bases.purge_after`
   - `knowledge_documents.purge_after`、`previous_status`、`document_version`
   - `knowledge_document_objects.quarantined_at`、`last_verified_at`
4. **`sessions.messages` 可以准备下线**：
   - 本地库的旧 JSON 已全部清空。
   - 现网可以先用 `SELECT COUNT(*) FROM sessions WHERE messages <> ''` 确认。
   - 为 0 之后，再删掉 `legacy_messages.py` 和 `sessions.py` 里的兜底读取，最后删列。
5. **`users.bound_ip` 已经没有作用**：登录不再按 IP 校验，这个字段只是随用户读写、在资料接口返回（本地 6 个用户有值）。删掉需要同时改资料接口的返回字段。
6. **只增不减的表**：
   - `scheduled_task_runs`（本地 3 个任务 781 行）
   - `knowledge_audit_events`（1115 行）
   - `knowledge_operations`

   都没有清理策略，建议按保留天数清理，和 `stream_events` 一样。
7. **平时为空是正常的**：
   - `distributed_locks`：锁释放就删行。
   - `rate_limit_counters`：单进程用内存计数。
   - `langgraph_store`：默认 local 上传。
