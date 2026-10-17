# Increment 44 执行报告（C1 剩余域：API 去 Database 门面）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-18（C1 剩余域子任务） |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 C1 |
| 设计文档 | `docs/design/2026-10-18-c1-remaining-domains.md` |
| 范围 | `easy_agent/api/auth.py`、`files.py`、`scheduled_tasks.py`、`expert.py`、`chat.py`；`easy_agent/db/repositories/` 新增 5 个域门面；`tests/db/` 新增 5 个 repository 测试文件；`tests/api/test_c1_remaining_domains.py`；本报告 |
| 约束遵守 | 未改前端；未改 `services/streaming.py`、`checkpoints.py`、`app.py`、`agent_manager.py`、`hitl.py`、`services/mcp.py`、`scripts/`、CI、`tests/conftest.py`；未执行 Batch 0/1 安全项；未改 Web Terminal；未 commit |
| 结论 | 5 个目标 API 文件已无 `from ..db import Database`、无 `Annotated[Database`、无直接 `db.*` 业务调用；路由统一经 `Depends(get_xxx_repository)` 注入域门面；目标测试 **199 passed**，新增测试 **13 passed**，门禁与 ruff 全部通过；**全量测试未跑** |

## 1. 改动概览

### 1.1 新增 5 个域门面（`easy_agent/db/repositories/`）

所有门面构造入参为 `Database`，只薄委托既有
`db/repositories/*` / `db/expert_store.py` / `utils.session` 函数，不新增 SQL。
模块导入期不 import FastAPI；`ExpertStore` 在方法内 lazy import，避免
`db.database -> repositories.__init__ -> expert_store -> db.database` 导入环。

| 文件 | 类 | 方法数 | 覆盖 |
| --- | --- | --- | --- |
| `user_repository.py` | `UserRepository` | 14 | 注册/登录/资料/管理员用户列表/密码重置/token_version |
| `file_repository.py` | `FileRepository` | 7 | 会话文件登记/查询/删除、生成文件、会话归属、默认用户 |
| `scheduled_task_repository.py` | `ScheduledTaskRepository` | 6 | 任务查询/列表/删除/启停/运行时间/运行记录 |
| `expert_repository.py` | `ExpertRepository` | 16 | 专家 CRUD/归属/重名/分页 + 专家会话与消息 + 流锁续约 |
| `chat_repository.py` | `ChatRepository` | 7 | 会话创建/查询/归属/元信息/消息/流锁续约 |

`easy_agent/db/repositories/__init__.py` 同步导出 5 个门面模块。

### 1.2 API 改造

每个目标文件新增：

```python
def get_xxx_repository(db: Annotated[object, Depends(get_database)]) -> XxxRepository:
    return XxxRepository(db)


def _as_xxx_repository(value: object) -> XxxRepository:
    if isinstance(value, XxxRepository):
        return value
    return XxxRepository(value)
```

- 路由参数名保留 `db`（兼容 `tests/api/test_auth.py`、
  `tests/integration/test_session_ownership.py` 等直接调用处理函数并传
  `Database` 的既有测试），类型改为域 repository，函数体第一行
  `repo = _as_xxx_repository(db)`，之后只用 `repo.*`；
- `get_database` 只出现在 provider 的 `Depends(get_database)` 中；
- 删除 `from ..db import Database`、`Annotated[Database`；
- 路径、HTTP 方法、`response_model`、状态码、JSON 字段未改。

| 文件 | 路由/方法改造 | 说明 |
| --- | --- | --- |
| `auth.py` | 9 条路由改 `get_user_repository`；`logout`/`login`/`passwordless`/`register` 的活跃时间仍传 `repo.database` 给 middleware 既有包装函数；`admin_reset_password` 的 `KnowledgeRepository` 仍接收 `repo.database` | `get_auth_config` 不碰 DB，未加依赖 |
| `files.py` | 6 条路由改 `get_file_repository`；`verify_token_sso(token, repo.database)`；`get_owned_session`（全量加载）改为 `repo.get_owned_session_meta`（轻量） | `download_file` / `parse_file` 不碰 DB，未加依赖 |
| `scheduled_tasks.py` | 7 条路由改 `get_scheduled_task_repository`；`_resolve_task_workspace_dir` 改为接收 repository；`get_task_workspace_name(repo.database, task)` 与 `verify_token_sso` 为服务边界 | 404/403 文案与顺序不变 |
| `expert.py` | 5 条路由改 `get_expert_repository`；`_current_user_id` / `_generate_and_store` / `_load_expert_prompt` / `_expert_meta_for_message` / `_expert_profile` / `_persist_user_turn` / `_recent_context` / `_relevance_gate` / `_refusal_event_stream` 全部改为接收 `ExpertRepository`；`ExpertStore(db)` 改为 `repo` | `list_public_experts` 不碰库，未加依赖 |
| `chat.py` | 4 条路由改 `get_chat_repository`；`create_new_session` 改为接收 repository；`cancel_chat` 删除原本未使用的 DB 依赖；`_detached_event_stream` 心跳续约改为调用方传入 `repo.renew_stream`，不再在函数内 `from ..db import get_database` | 状态/live/resume 的归属校验改用轻量 `get_owned_session_meta`；流式/HITL/SSE 语义不变 |

## 2. 剩余服务层边界（API 层已无 `db.*`）

以下调用点仍把底层 `Database` 实例交给尚未迁移的服务；API 层不对其调用任何
`Database` 业务方法。后续 C1 批次把服务改为依赖 repository 后即可移除
`database` property。

| API | 边界 | 原因 |
| --- | --- | --- |
| `auth.py` | `middleware.auth.touch_user_activity` / `get_user_activity_time` / `clear_user_activity` | 节流与异常吞掉语义在 middleware；本批不改 middleware |
| `files.py` | `middleware.auth.verify_token_sso(token, db)` | iframe/查询参数 token 认证依赖 user/token_version/活跃时间 |
| `scheduled_tasks.py` | `services.scheduler.get_task_workspace_name(db, task)`；`verify_token_sso` | 任务目录定位规则在 scheduler |
| `expert.py` | `chat_stream_generator(db=repo.database, ...)` | streaming 写路径强依赖 `Database`，本批禁改 `services/streaming.py` |
| `chat.py` | `authorize_scoped_chat` / `prepare_knowledge_chat` / `chat_stream_generator` / `resume_stream_generator` / `session_uploads.build_turn_preview` / `tools.expert_writer.resolve_owned_expert` | 知识库鉴权、文件预览、流式持久化、专家绑定均在服务层 |

## 3. 测试与门禁

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新增 repository + API 静态测试 | `uv run pytest tests/db/test_user_repository.py tests/db/test_file_repository.py tests/db/test_scheduled_task_repository.py tests/db/test_expert_repository.py tests/db/test_chat_repository.py tests/api/test_c1_remaining_domains.py -q` | **13 passed, 1 warning**（2.92s） |
| 任务指定套件 | `uv run pytest tests/api/test_auth.py tests/api/test_files.py tests/api/test_scheduled_tasks.py tests/api/test_expert.py tests/api/test_chat.py tests/db -q` | **199 passed, 1 warning**（96.67s） |
| 直接调用兼容 + hub（额外） | `uv run pytest tests/integration/test_session_ownership.py tests/api/test_stream_hub.py tests/services/test_stream_hub.py -q` | **19 passed, 1 warning**（2.60s） |
| import SCC 门禁 | `python3 scripts/import_graph.py --check-scc` | **OK** — 211 modules / 473 edges / 1 SCC（16 modules），与白名单完全一致 |
| 双写门禁 | `python3 scripts/check_double_write.py` | **OK** — sessions.py / database.py 均 OK；Database 141 methods / 106 pure delegations |
| Lint | `uvx ruff check .` | **All checks passed!** |

说明：唯一 warning 为既有 `StarletteDeprecationWarning`（httpx/TestClient），
与本次改动无关。**未执行全量 `pytest tests/`**，按任务约定由 Lead 执行。

## 4. 新增测试覆盖

| 文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `tests/db/test_user_repository.py` | 2 | 注册/查询/密码校验/token_version/密码更新/列表计数；免密登录 user_id 占用冲突 |
| `tests/db/test_file_repository.py` | 2 | 会话文件登记/查询/按 username 删除权限、生成文件与文件名集合；会话归属 404 |
| `tests/db/test_scheduled_task_repository.py` | 2 | 任务 CRUD/启停/运行时间/运行记录/删除幂等；按 username 的列表隔离 |
| `tests/db/test_expert_repository.py` | 2 | 专家创建/更新/重名/分页/软删/硬删/跨用户不可见；专家会话与消息落库、流续约缺失返回 False |
| `tests/db/test_chat_repository.py` | 2 | 会话创建/重命名/消息追加/元信息更新；归属 404 与流续约缺失返回 False |
| `tests/api/test_c1_remaining_domains.py` | 3 | 5 个文件无 Database 门面/无 `db.*`；每域路由使用对应 provider；覆盖 `get_user_repository` 的依赖替换黑盒 |

## 5. API 契约不变量

- `sessions.messages` 仍不写正文：所有消息仍走
  `repositories.sessions.add_message` → `session_messages` 行表；
- 会话归属不存在/跨用户仍统一 404「会话不存在」；
- 定时任务不存在 404「任务不存在」、跨用户 403「无权…」文案与顺序不变；
- 专家归属仍按 `user_id` 校验，越权 404；
- 文件上传仍先落盘再登记，413 / 解析失败降级顺序不变；
- `chat.py` 的 HITL resume、流锁、hub、SSE 事件格式不变；`_detached_event_stream`
  仅把心跳续约来源从内部 `get_database()` 改为调用方注入的
  `repo.renew_stream`（直接单测不传时跳过心跳，300s 内本来也不会触发）；
- `expert.py` 的专家提示词加载、相关性预检、短路拒答落库顺序不变。

## 6. 风险与遗留

1. **`database` property 仍有使用点**：5 个域门面都暴露 `database`，仅用于上表
   列出的服务边界；API 层不对它调用业务方法。后续迁移服务后可删除。
2. **`_as_xxx_repository` 窄兼容**：直接调用路由函数并传 `Database` 的既有测试
   继续可用；FastAPI 注入路径不经过包装。
3. **`create_new_session` 类型标注为 `ChatRepository`**：expert.py 传入
   `ExpertRepository`，两者都实现 `create_session` /
   `update_session_workspace_name`，运行时为鸭子类型；未引入 Protocol 以保持改动最小。
4. **`cancel_chat` 删除未使用的 DB 依赖**：该路由原本未使用 `db`；删除后路径、
   响应与鉴权不变。
5. **未迁移文件**：`easy_agent/api/settings.py`、`connectors.py`、`completion.py`、
   `prompts.py`、`terminal.py`、`skill_center.py` 等仍有各自的 DB 使用，不在本批
   允许范围；`knowledges/`、`personnel/` 使用自己的 repository。
6. **全量测试未跑**：仅跑任务指定套件与额外相关套件；全量由 Lead 执行。

## 7. 变更文件

| 文件 | 变更 |
| --- | --- |
| `easy_agent/db/repositories/user_repository.py` | 新增 `UserRepository` |
| `easy_agent/db/repositories/file_repository.py` | 新增 `FileRepository` |
| `easy_agent/db/repositories/scheduled_task_repository.py` | 新增 `ScheduledTaskRepository` |
| `easy_agent/db/repositories/expert_repository.py` | 新增 `ExpertRepository` |
| `easy_agent/db/repositories/chat_repository.py` | 新增 `ChatRepository` |
| `easy_agent/db/repositories/__init__.py` | 导出 5 个门面 |
| `easy_agent/api/auth.py` | 9 路由改 repository provider，移除 Database import/`db.*` |
| `easy_agent/api/files.py` | 6 路由改 repository provider，归属校验改 repository |
| `easy_agent/api/scheduled_tasks.py` | 7 路由改 repository provider，helper 接收 repository |
| `easy_agent/api/expert.py` | 5 路由改 repository provider，内部 helper 全部改 repository |
| `easy_agent/api/chat.py` | 4 路由改 repository provider，`create_new_session`/心跳续约改造 |
| `tests/db/test_user_repository.py` | 新增 |
| `tests/db/test_file_repository.py` | 新增 |
| `tests/db/test_scheduled_task_repository.py` | 新增 |
| `tests/db/test_expert_repository.py` | 新增 |
| `tests/db/test_chat_repository.py` | 新增 |
| `tests/api/test_c1_remaining_domains.py` | 新增静态/依赖注入回归 |
| `docs/design/2026-10-18-c1-remaining-domains.md` | 设计文档 |
| `docs/audit/2026-10-18-increment44-c1-domains-report.md` | 本报告 |
