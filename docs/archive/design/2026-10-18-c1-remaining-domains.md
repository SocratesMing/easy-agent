# C1 剩余域：API 去 Database 门面设计

| 项 | 内容 |
| --- | --- |
| 状态 | 设计 + 实现（auth/users、files、scheduled_tasks、expert、chat） |
| 日期 | 2026-10-18 |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 C1 |
| 前置 | C1 第一批 sessions 域（`SessionRepository` + `Depends` provider）已完成；A2 消息单写路径、阶段 0 门禁未变 |
| 范围 | `easy_agent/api/auth.py`、`files.py`、`scheduled_tasks.py`、`expert.py`、`chat.py`；`easy_agent/db/repositories/` 下新增 5 个域门面；`tests/db/`、`tests/api/` 对应测试；本设计文档与执行报告 |
| 非目标 | 不改前端、不改 `services/streaming.py` / `checkpoints.py` / `app.py` / `agent_manager.py` / `hitl.py` / `services/mcp.py` / `scripts/` / CI / `tests/conftest.py`；不执行 Batch 0/1 安全项；不改 Web Terminal；不 commit |

## 1. 目标与约束

C1 第一批已完成 sessions 域：`api/sessions.py` 只依赖 `SessionRepository`，通过
`Depends(get_session_repository)` 注入，`get_database` 仅出现在 provider 内。
本批把同样模式推广到剩余 5 个 API 文件：

1. `api/auth.py`（users 域，含登录/注册/资料/管理员用户列表/密码重置）；
2. `api/files.py`（上传文件、会话文件、生成文件、工作区树/预览）；
3. `api/scheduled_tasks.py`（定时任务 CRUD、运行记录、工作区文件）；
4. `api/expert.py`（我的专家 CRUD、专家执行 SSE）；
5. `api/chat.py`（会话创建、消息落库、HITL resume、SSE 流式入口）。

硬约束：

- 目标 API 文件内删除 `from ..db import Database` 与 `Annotated[Database, ...]`；
- 目标 API 文件内不出现 `db.<业务方法>`（参数名保留 `db` 只为兼容既有直接调用测试，
  函数体第一行立即 `repo = _as_xxx_repository(db)`，之后只用 `repo.*`）；
- `get_database` 只允许出现在 `get_xxx_repository` provider 内；
- 路径、HTTP 方法、`response_model`、状态码、JSON 字段不变；
- 门面薄委托既有 `db/repositories/*` / `db/expert_store.py` 方法，不新增任何 SQL；
- 不修改 `services/`；服务层仍以 `Database` 为入参的边界通过 `repo.database`
  单点传递，并在报告列明。

## 2. 域、类、方法

所有门面放在 `easy_agent/db/repositories/`，与既有 SQL repository 同目录；
构造入参统一为 `Database`，模块导入期不 import FastAPI / services（避免环）。
`database` property 仅为尚未迁移的服务保留，API 层不对它调用业务方法。

### 2.1 `UserRepository`（`user_repository.py`）

覆盖 `api/auth.py` 实际调用：

| 方法 | 委托 |
| --- | --- |
| `get_user_by_username` / `get_user_by_employee_id` / `get_user_by_account` | `repositories.users` 同名函数 |
| `register_user` / `verify_user_password` / `get_or_create_passwordless_user` | 同名函数 |
| `increment_user_token_version` / `update_user_password` | 同名函数 |
| `get_user_token_version` / `update_user` / `list_users` / `count_users` | 同名函数 |

活跃时间（`touch_user_activity` / `get_user_activity_time` / `clear_user_activity`）
仍调用 `middleware.auth` 的既有包装函数（节流 + 异常吞掉语义），API 层传
`repo.database`。这是本域唯一服务边界。

### 2.2 `FileRepository`（`file_repository.py`）

| 方法 | 委托 |
| --- | --- |
| `add_session_file` / `get_session_files` / `delete_file` | `repositories.files` 同名函数 |
| `get_generated_files` / `get_generated_filenames` | 同名函数 |
| `get_owned_session_meta` | lazy import `utils.session.get_owned_session_meta`，保持 404 语义 |
| `get_or_create_default_user` | `repositories.users.get_or_create_default_user`（预览匿名兜底） |

`verify_token_sso(token, repo.database)` 仍由 API 调用 middleware 既有函数：
它依赖 user、token_version 与活跃时间，属于认证服务边界。

### 2.3 `ScheduledTaskRepository`（`scheduled_task_repository.py`）

| 方法 | 委托 |
| --- | --- |
| `get_scheduled_task` / `list_scheduled_tasks` | `repositories.scheduled_tasks` 同名函数 |
| `delete_scheduled_task` / `update_scheduled_task_status` / `update_scheduled_task_run_times` | 同名函数 |
| `list_scheduled_task_runs` | 同名函数 |
| `database` | 供 `get_task_workspace_name(db, task)`、`verify_token_sso` 两个服务调用 |

### 2.4 `ExpertRepository`（`expert_repository.py`）

`ExpertStore` 在方法内 lazy import / 实例化，避免
`db.database -> repositories.__init__ -> expert_store -> db.database` 的导入期环。

| 方法 | 委托 |
| --- | --- |
| `get_user_by_username` | `repositories.users.get_user_by_username` |
| `get` / `get_owned` / `create` / `update_fields` / `find_by_name_cn` / `list_by_user` / `delete` | `ExpertStore` 同名方法 |
| `get_session` / `create_session` / `update_session_workspace_name` / `update_session_meta` / `add_message` | `repositories.sessions` 同名函数 |
| `update_last_assistant_message` / `update_last_assistant_message_row` | 同名函数（拒答短路落库） |
| `renew_stream` | lazy import `services.session_runtime.renew_stream`（无回边，不进入现有 SCC） |
| `database` | 仅给 `chat_stream_generator` 等未迁移服务 |

### 2.5 `ChatRepository`（`chat_repository.py`）

| 方法 | 委托 |
| --- | --- |
| `create_session` / `update_session_workspace_name` / `get_session` / `update_session_meta` / `add_message` | `repositories.sessions` 同名函数 |
| `get_owned_session_meta` | lazy import `utils.session.get_owned_session_meta` |
| `renew_stream` | lazy import `services.session_runtime.renew_stream` |
| `database` | 仅给 `authorize_scoped_chat` / `prepare_knowledge_chat` / `chat_stream_generator` / `resume_stream_generator` / `session_uploads.build_turn_preview` / `resolve_owned_expert` |

## 3. 依赖注入与兼容

每个 API 文件新增：

```python
def get_xxx_repository(
    db: Annotated[object, Depends(get_database)],
) -> XxxRepository:
    return XxxRepository(db)


def _as_xxx_repository(value: object) -> XxxRepository:
    if isinstance(value, XxxRepository):
        return value
    return XxxRepository(value)
```

- 路由签名保留参数名 `db`、类型改为 `Annotated[XxxRepository, Depends(get_xxx_repository)]`；
  函数体第一行 `repo = _as_xxx_repository(db)`，之后无 `db.*`；
- provider 内是唯一出现 `get_database` 的位置；`tests/conftest.py` 对
  `get_database` 的 override 继续生效；
- 兼容 shim 让既有直接调用处理函数并传 `Database` 的测试
  （`tests/api/test_auth.py`、`tests/integration/test_session_ownership.py`）无需改签名；
- `get_auth_config` / `list_public_experts` 等本来不碰 DB 的路由不加依赖，保持不变。

## 4. services 层剩余边界

| API | 边界 | 原因 |
| --- | --- | --- |
| `auth.py` | `middleware.auth.touch_user_activity` / `get_user_activity_time` / `clear_user_activity` 接收 `repo.database` | 节流与异常语义在 middleware；本批不允许改 middleware |
| `files.py` | `middleware.auth.verify_token_sso(token, repo.database)` | iframe/token 查询参数认证依赖 user/token_version/活跃时间 |
| `scheduled_tasks.py` | `services.scheduler.get_task_workspace_name(repo.database, task)`；`verify_token_sso` | 任务目录定位规则在 scheduler |
| `expert.py` | `chat_stream_generator(db=repo.database, ...)` | streaming 写路径强依赖 `Database`，本批禁改 `services/streaming.py` |
| `chat.py` | `authorize_scoped_chat` / `prepare_knowledge_chat` / `chat_stream_generator` / `resume_stream_generator` / `session_uploads.build_turn_preview` / `resolve_owned_expert` 接收 `repo.database` | 知识库鉴权、文件预览、流式持久化、专家绑定均在服务层，且本批禁改服务层 |

这些边界只传递底层实例，API 层不对其调用 `Database` 业务方法；后续 C1 批次把
服务改为依赖 repository 后即可删除 `database` property。

## 5. 兼容与不变量

- `sessions.messages` 仍不写正文：所有消息仍走 `repositories.sessions.add_message`
  → `session_messages` 行表；
- 会话归属错误仍统一 404「会话不存在」，定时任务仍保留 404/403 区分；
- 专家归属仍按 `user_id` 校验，越权 404；
- 文件上传仍先落盘再登记，413 / 解析失败降级顺序不变；
- `api/chat.py` 的 HITL resume、流锁、hub、SSE 事件格式、`_detached_event_stream`
  的后台驱动语义不变；仅把心跳续约从 `get_database()` 改为由调用方传入
  `repo.renew_stream`（直接单测不传时跳过心跳，本来也不会在 300s 内触发）；
- `api/expert.py` 的专家提示词加载、相关性预检、短路拒答落库顺序不变。

## 6. 测试策略

1. 每域新增 2 个以上 `tests/db/test_*_repository.py`：
   - 委托结果与既有 `Database` 门面一致；
   - 归属/权限语义（404 / 403 / 本人可见）。
2. 新增 `tests/api/test_c1_remaining_domains.py` 静态回归：
   - 5 个目标文件无 `from ..db import Database`、无 `Annotated[Database`；
   - 每个文件有 `get_xxx_repository` provider，路由使用
     `Depends(get_xxx_repository)`；
   - 文件内不存在 `db.<业务方法>` 文本调用（排除 `db/repositories` 等非目标）。
3. 既有 API 测试全部保留并运行：
   `tests/api/test_auth.py test_files.py test_scheduled_tasks.py test_expert.py
   test_chat.py tests/db -q`。
4. 门禁：`python3 scripts/quality/import_graph.py --check-scc`、
   `python3 scripts/quality/check_double_write.py`、`uvx ruff check .`。
5. 不跑全量测试（按任务约定），报告中注明。

## 7. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 直接调用路由的旧测试传 `Database` | `_as_xxx_repository` 窄包装；参数名保留 `db` |
| `ExpertStore` 导入环 | 门面内 lazy import，模块级只 import models |
| `database` property 被滥用 | 仅服务调用点使用，API 层无 `db.*`；报告列明剩余边界 |
| 流式/HITL 行为漂移 | 不改 `services/streaming.py`；`chat_stream_generator` 入参仍是同一 `Database` 实例；仅心跳续约改为绑定方法 |
| SCC 变化 | 门面不 import SCC 内的 services 包；`session_runtime` 为无回边叶子模块 |
| HTTP 契约变化 | 不改路径/response_model/状态码；测试覆盖既有契约 |
