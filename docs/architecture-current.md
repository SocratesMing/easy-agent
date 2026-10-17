# 当前架构速览（2026-10-17 审计后）

> 本文件取代 `CLAUDE.md` / `AGENTS.md` 中已过时的模块地图；发现不一致时以本文件与代码为准。
> 详细审计与改进清单：`docs/audit/2026-10-17-easy-agent-audit.md`。

## 1. 运行时入口

- `main.py` / `easy-web`（`easy_agent/web_runner.py`）→ `easy_agent.app:app`（FastAPI）。
- `app.py` lifespan：初始化环境/日志/配置 → 启动知识库 → DB → 提示词/技能发现 → Agent 配置 → 移植专家目录扫描 → 可选 Postgres checkpointer → 调度器（多 worker 保护）。
- 配置：`easy_agent/config/config.yaml`（gitignored，单文件）；模板 `config-example.yaml`；环境变量来自 `.env.{AGENT_ENV}`；`EASY_CONFIG` 可覆盖路径。
- 运行期配置持有者：`easy_agent/runtime_config.py`（基础层只读，避免 config → services 循环）。

## 2. 后端模块地图（`easy_agent/`）

| 模块 | 职责 |
| --- | --- |
| `agent.py` | `EasyAgent` 封装 `create_deep_agent`（skills/memory/permissions/interrupt_on/subagents/checkpointer/store）；backend 装配、HITL 中断配置、子代理 specs |
| `backends/shell.py` | `LocalShellBackend` + 虚拟路径翻译 + bwrap/Landlock/fail-closed 策略 |
| `sandbox.py` / `landlock.py` / `fs_safety.py` | 命令沙箱、Landlock 降级、外部目录挂载安全校验 |
| `hitl.py` | 破坏性命令识别、execute 工具描述、HITL 默认工具 |
| `checkpoints.py` | 自研 `PersistentMemorySaver`（裁剪后写业务库）与官方 Postgres saver |
| `stores.py` | 自研 LangGraph `BaseStore`（SQLite/MySQL） |
| `summarization.py` | 建图期替换官方摘要工厂 + 缺失降级护栏 |
| `subagents.py` / `skills.py` | 子代理定义；技能发现（根 `skills/` 为 canonical，包内副本回退） |
| `runtime_config.py` | 运行期配置 + Agent 失效回调注册（架构解耦用） |
| `api/` | chat / auth / sessions / files / expert / connectors / scheduled_tasks / settings / skill_center / completion / prompts / terminal（POSIX，HITL 无关） |
| `services/` | streaming（SSE 编排）、agent_manager（会话 Agent 缓存/流锁）、scheduler、mcp（连接器工具）、session_uploads、skill_library、expert/public_expert、memory_manager、session_runtime、sse（事件注册表） |
| `db/` | `database.py`（连接/迁移/核心表）、`session_messages.py`、`expert_store.py`、`schema_registry.py` |
| `middleware/` | JWT 鉴权（`auth.py`）、进程内限流（`rate_limit.py`） |
| `knowledges/` | 知识库 v2：service / api / ragflow client / runtime worker、catalog sync、observability |
| `personnel/` | 人员管理、Excel 导入、用户扩展字段 |
| `initialization/` | 环境/日志/运行期初始化（config/log/environment/runtime） |
| `tools/` | web_search、scheduled_task、expert_writer、session_file_reader |
| `models/` | API / DB Pydantic 模型 |

## 3. 关键数据流

### 聊天流式（SSE）
`POST /agent/chat/stream` → 会话归属校验/创建 → `db.add_message`（行表）→
`agent_manager.get_or_create_agent_for_session`（按 user/session/model/expert/connectors 缓存，`OrderedDict` LRU）→
`services/streaming.chat_stream_generator` → DeepAgents `astream(stream_mode=["messages","updates"])` →
`StreamProcessor` 产生事件 → `format_sse`（`services/sse.py`）→ `_StreamHub` 广播/回放（单会话并发返回 409）→
最终写入 `session_messages`；可选 HITL 中断 `approval_required`，`/chat/resume` 恢复。

事件契约：`docs/sse-events.md`（15 个事件，契约测试 `tests/services/test_sse_contract.py`）。

### Agent 工具与沙箱
- 文件工具走 CompositeBackend 路由（`/workspace/`、`/user-skills/`、`/memories/`、外部目录）。
- `execute` 默认经 bwrap；不可用降级 Landlock；都不可用 fail-closed（127）。
- HITL：删除类命令/专家保存/定时任务/知识原文 materialize 等触发审批。
- 子代理（office-writer / knowledge-researcher）显式 `skills=["/user-skills/"]` + 异常兜底 middleware。

### 技能
- canonical：仓库根 `skills/`（7 个运行时技能：docx/pptx/xlsx/pdf/mcp-builder/skill-creator/expert-creator）。
- 包内 `easy_agent/skills/` 为 pip 安装回退副本；`scripts/sync_skills.py` 同步，`--check` 可在 CI 校验。
- 用户技能：`workspace/{username}/skills/` → 虚拟路径 `/user-skills/`。

### 数据库
- SQLite（单机开发，启用 WAL/busy_timeout/synchronous=NORMAL）或 MySQL（多副本生产，代码禁止静默降级）。
- 会话消息行表 `session_messages`；`sessions.messages` 为 legacy 大字段（仅历史会话回退读取）。
- 领域 schema 通过 `db/schema_registry.py` 注册，`Database.init_tables()` 统一执行。
- LangGraph checkpointer：默认 `PersistentMemorySaver`（按 thread 裁剪，最多 3 thread × 2 checkpoint）；生产可配 `EASY_CHECKPOINT_POSTGRES_URI`。

## 4. 前端

- `frontend-vue2/`：Vue 2.7 + Element UI（后端 `app.py` 与 Dockerfile 当前托管/构建的基线）。
- `frontend/`：Vue 3.5 + Vite（并行维护的第二基线）。
- 两者都**不是** `npm run dev` 自动代理到 8080 的旧 CLI 结构（已改为 Vite 脚本）；构建命令见各自 `package.json`。
- 无路由/状态库（Vue2 侧）：视图由 `App.vue` 布尔开关切换；**SSE 解析已统一**为各基线的 `src/api/sse.js`（唯一 `readSSE`），chat/expert/knowledge 只注册回调（审计项 ARCH-05 第一步完成）。

## 5. 命令与质量门禁

```bash
uv sync                                  # 安装依赖（dependency-groups dev / extra docs）
uv run pytest tests/ -q                  # 全量测试（当前基线：933+ passed）
uvx ruff check .                         # 致命规则（E9/F63/F7/F82）
python scripts/sync_skills.py --check    # 技能副本一致性
cd frontend-vue2 && npm ci && npm run build
cd frontend && npm ci && npm run build
```

CI：`.github/workflows/ci.yml`（后端 ruff + pytest collect/test + 双前端 build）。

## 6. 已知架构债（详见审计报告）

- 剩余 1 个 16 模块强连通分量（`api.chat ↔ knowledges.* ↔ services`），按 `docs/design/2026-10-17-module-refactor-plan.md` 分阶段处理。
- `Database` 仍较大（待 Phase 2 拆 repository）；`streaming.py` 巨型生成器待 Phase 3 拆。
- 多副本状态仍在进程内（agent 缓存/SSE hub/调度）；设计见 `docs/design/2026-10-17-multi-instance-state.md`。
- 安全：审计报告中的 4 个 P0 / 8 个 P1 尚未修复（Batch 0/1 未执行）。
