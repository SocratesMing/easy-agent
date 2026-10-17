# 架构重构设计：模块边界与循环依赖（ARCH-03 / ARCH-04）

| 项 | 内容 |
| --- | --- |
| 状态 | 设计草案（待评审后实施） |
| 依据 | `docs/audit/2026-10-17-easy-agent-audit.md` ARCH-03/04、`docs/audit/evidence/T4-architecture.md` |
| 约束 | 不改变外部 API/SSE 契约；每阶段可独立发布、可回滚；新行为必须有测试 |

## 1. 现状（实测）

- 非包模块 import 图：177 模块 / 347 条内部边，**2 个强连通分量**（39 模块 + 2 模块）。
- 关键回边：
  - `api.chat → knowledge_impl → knowledges.api → api.chat`
  - `config → services.agent_manager → agent → config`
  - `services.agent_manager ↔ tools.expert_writer`
  - `db.database ↔ knowledges.core.schema`
- 巨型模块：`db/database.py` 的 `Database` 单类 114 方法 / 2458 行；`knowledges/service.py` `KnowledgeService` 49 方法 / 2208 行；`services/streaming.py` 两个 400+ 行生成器。
- 分层倒置：`api/*` 8 个文件直接使用 `Database`；`config` 反向依赖 `services.agent_manager`；`db` 反向依赖 `personnel`；`middleware/auth` 依赖 `services`。

## 2. 目标

1. 消掉全部模块级循环依赖（SCC 从 2 降到 0），并以 CI 门禁固化。
2. `Database` 拆为按领域的 repository；`Database` 只保留连接/事务/迁移。
3. `streaming.py` 拆为「编排器 + 事件处理器 + 持久化 + 副作用适配器」。
4. 为多实例/多租户改造预留接口（见 `multi-instance-state.md`）。

**非目标**：不重写 Agent/DeepAgents 装配；不改前端双基线；不引入新框架。

## 3. 分阶段实施

### Phase 0 · 门禁（1 天，低风险）

- 新增 `scripts/import_graph.py`（复用 T4 的 AST 分析）输出 SCC/扇入扇出。
- CI 增加：`python scripts/import_graph.py --check-scc 0`（允许白名单 + 注释原因）。
- 新增 `[tool.ruff]` 与最小规则集（F/E9，后续 BLE/print 分阶段）。

### Phase 1 · 砍 4 条回边（3–5 天，风险中）

1. `config → services.agent_manager`：`config.py:932/951/979/1008` 的 lazy import 改为调用方注入；`get_user_mcp_path` 等目录方法改为接收 `AgentConfig` 的纯函数，`services` 侧包一层薄适配。
2. `services.agent_manager ↔ tools.expert_writer`：`expert_writer` 不再 import `agent_manager` 的 `_session_edit_targets`，改为在 `create_save_expert_tool(...)` 时把绑定表作为参数传入（已有参数位）。
3. `knowledges.api → api.chat`：知识面板流式下沉到 `knowledges/chat/`，路由层只做参数适配；删除 `knowledges/api.py:1113` 的反向 import。
4. `db.database ↔ knowledges.core.schema`：schema 建表函数改为注册制（`register_schema(engine)`），`Database.init_tables()` 遍历注册表，不再 import knowledges。

**验收**：import 图 SCC = 0；`pytest` 全绿；接口契约测试（OpenAPI diff）无变化。

### Phase 2 · Database 拆 repository（1–2 周，风险中高）

- 目标目录：`db/repositories/{users,sessions,messages,files,experts,tasks,runtime,knowledge_permissions}.py`。
- 方式：**先加接口后迁移调用点**；`Database` 保留同名委托方法（deprecated），逐个把 `api/services` 调用改为 repository。
- SQLite/MySQL 方言差异收敛到 `db/dialect.py`（分页/upsert/时间函数），repo 不再写双分支。
- 迁移期禁止在 `Database` 新增方法（CI 检查 diff）。

### Phase 3 · streaming 拆分（1 周，风险中）

- `streaming/orchestrator.py`：会话注册/取消/HITL 检测/事件序列。
- `streaming/persistence.py`：增量落库、最终消息、checkpoint 交互（注入 `db` 接口）。
- `streaming/side_effects.py`：workspace rename、技能入库、记忆更新、日志。
- `streaming/sse.py`：唯一 `format_sse` + 事件注册表（见 `sse-event-contract.md`）。
- 先搬代码不改逻辑（纯移动 + 导入修复），再逐块加测试。

### Phase 4 · 依赖注入收口（持续）

- `api` 层只做鉴权/参数/调用 service；`services` 不 import `api`；`middleware` 不 import `services`（用 FastAPI dependency 注入）。
- 新模块必须声明依赖方向（`docs/design/module-layers.md`），CI 校验。

## 4. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 循环依赖靠 import 顺序“碰巧”成立，移动后运行期 ImportError | 每个 PR 单独跑全量 `pytest` + `python -c "import easy_agent.app"` |
| 拆分 Database 改变事务边界 | 保持 repo 方法粒度与原方法一致；先只读后写路径；增加并发/回滚测试 |
| streaming 拆分引入行为差异 | 先纯移动；用 golden SSE 事件测试对比拆分前后输出 |
| 迁移期双实现并存 | 用 ruff 规则/CI 禁止新增旧方法，设删除截止版本 |

## 5. 验收标准

- import 图 SCC = 0（白名单为空）。
- 单文件 ≤ 800 行（`streaming.py`、`database.py` 达标）。
- `Database` ≤ 300 行，只含连接/事务/迁移。
- 新增 repository 单测覆盖读写/事务回滚/SQLite+MySQL 方言。
- 全量测试通过，SSE 契约测试通过。


## Phase 1 进展（2026-10-17）

已完成并验证（详见 `docs/audit/2026-10-17-increment4-report.md`）：

- 新增 `easy_agent/runtime_config.py`（运行期配置 + Agent 失效回调注册），
  `config.py`、`middleware/auth.py`、`utils/session_logger.py`、
  `services/session_uploads.py` 不再反向 import `services.agent_manager`。
- `tools/expert_writer.py` 的配置读取/缓存失效改走 runtime_config，去掉了
  `tools → services.agent_manager` 回边。
- `resolve_context_length` 从 `agent.py` 迁移到 `model.py`，去掉
  `agent ↔ summarization` 环。

AST import 图实测：环内模块 **25 → 18 → 16**；SCC **2 → 1**（`db.database ↔ knowledges.core.schema` 已用 schema 注册制断开；相对审计基线 39+2 累计 -59%）。
剩余：`knowledges.api → api.chat`、`db.database ↔ knowledges.core.schema`、
`tools.scheduled_task → services.scheduler`（分别对应本文件 Phase 1/4）。


## Phase 2 进展（2026-10-17）

- 新增 `easy_agent/db/repositories/`，已抽取三个领域 repository：
  `session_runtime.py`（159 行）、`agent_state.py`（148 行：checkpoint + langgraph store）、
  `scheduled_tasks.py`（~170 行）、`session_records.py`（~110 行）、
  `users.py`（~380 行）、`mcp_api_keys.py`、`files.py`（~130 行）、
  `sessions.py`（716 行）、`db/schema.py`（751 行：DDL/迁移/自愈）。
- `Database` 对应方法改为薄委托，调用方零迁移；`database.py` 2704 → **727 行（累计 -1977）**。
- `db → personnel` 反向依赖已用 `db/user_fields_registry.py` 解除（personnel import 时注册）。
- 剩余：连接/事务之外的杂项方法；目标 ≤300 行。
- 下一步：users / sessions+messages / files / experts / scheduled_tasks /
  knowledge_permissions；抽取 users 时同步处理 `db → personnel` 反向依赖。

## Phase 3 进展（2026-10-17）

- 第一步已抽取 `services/hitl_state.py`（HITL interrupt 解析纯函数），
  `streaming.py` 改为 import 重导出；后续可按同样方式拆消息重建/工具累积/持久化适配器。
- 按用户指示：多副本 Phase B、streaming 深度拆分、HITL 策略调整**暂缓**。

### Phase 3 第二步（2026-10-18）：消息重建 / 工具累积抽出

- 新增 `easy_agent/services/stream_messages.py`（226 行，纯逻辑，不依赖 DB/SSE/Agent 实例）：
  `tool_call_records_to_dicts`、`_historical_tool_records`、`_tool_call_tuples`、
  `_assistant_content_message`、`build_context_messages`、
  `build_assistant_message_dict`、`_partial_message_dict`。
- `streaming.py` 改为 `from .stream_messages import ...` 重导出，既有导入路径与运行行为不变；
  文件 1682 → 1482 行。
- 新增 `tests/services/test_stream_messages.py`（11 个用例）：工具记录正常/8 元组/截断、
  `blocks` 历史回退与缺省字段兜底、纯空白 content 块过滤、`_partial_message_dict` 的
  HITL approval 分支、`streaming` 重导出同一实现。
- 验证：目标测试 57 passed；全量 `pytest tests/` 通过（997 passed / 1 skipped）；
  `uvx ruff check .` 通过。
