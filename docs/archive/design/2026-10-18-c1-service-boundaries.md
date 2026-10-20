# C1b：服务层边界收口（API 不再向服务层传 Database 句柄）设计

| 项 | 内容 |
| --- | --- |
| 状态 | 设计 + 实现（仅后端） |
| 日期 | 2026-10-18 |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 C1；`docs/design/2026-10-18-c1-remaining-domains.md` §4「services 层剩余边界」 |
| 前置 | C1 五域门面（User / File / ScheduledTask / Expert / Chat Repository）已完成；API 层已无直接 `db.*`；阶段 0 门禁（SCC=0、双写冻结）保持 |
| 范围 | `easy_agent/middleware/auth.py`、`easy_agent/db/repositories/{user,file,scheduled_task,expert,chat}_repository.py`、`easy_agent/api/{auth,files,scheduled_tasks,expert,chat}.py` 的服务边界段、`easy_agent/services/session_uploads.py`、对应测试与本文档 |
| 非目标 | 不改 streaming 包、`agent.py`、`agent_manager`、`db/database.py`、checkpoints、mcp、前端、`tests/conftest.py`；不执行 Batch 0/1；不 commit；不改 `easy_agent/api/sessions.py`（不在本批允许清单） |

## 1. 背景

C1 第一批把 API 层的 `Database` 门面换成 5 个域 repository，但仍有 19 处
`repo.database` 把底层实例透传给服务层（middleware / services / tools /
knowledges）。这些点虽然满足了「API 不直接调 `db.*`」，但没有真正收口：

- 服务层签名以 `Database` 为入参，repository 变成「只做包装、随后又漏回底层」；
- `Database` 门面在 API → 服务的链路上继续传播，删除门面时无法收尾；
- 认证辅助（活跃时间、SSO 校验）与 repository 已经查同一张 `users` 表，却要绕
  一次底层实例。

C1b 的目标：能通过 repository 方法 / 端口消除的全部消除；确实属于其他
bounded context（知识域）或流式引擎的边界，明确登记为「保留」并说明原因，不做
高风险硬改。

## 2. 目标与硬约束

1. API 层（`auth/files/scheduled_tasks/expert/chat`）不再为了「认证辅助 / 工作目录
   解析 / 上传文件解析」拿 `repo.database`；
2. 鉴权语义（disabled 账号 403、token_version 不匹配 401、空闲登出 401、SSO
   节流 30s、异常吞掉转日志）、HTTP 状态码、detail 文案、SSE 事件、落库内容
   完全不变；
3. 不新增 SQL；repository 方法仍是薄委托；
4. SCC 保持 0（`scripts/quality/import_graph.py --check-scc`），双写冻结不变；
5. 不跑全量测试，只跑任务约定子集 + 门禁。

## 3. 端口设计

### 3.1 `UserActivityStore`：middleware 活跃时间 / SSO 校验端口

`middleware/auth.py` 的 4 个函数（`touch_user_activity`、`clear_user_activity`、
`get_user_activity_time`、`verify_token_sso`）只依赖「用户存储」上的同名方法：

```
touch_user_activity(username, timestamp)
get_user_activity_time(username)
clear_user_activity(username)
get_user_by_username(username)
```

`Database` 与 `UserRepository` 都满足这套方法（后者为本批新增的 3 个委托方法），
因此类型标注收敛为：

```python
UserActivityStore = Database | UserRepository
```

节流字典 `_activity_write_throttle` 与异常吞掉逻辑留在 middleware（行为不变），
API 层改为传 repository，不再传 `repo.database`。

同时把 `get_user_repository` provider 从 `api/auth.py` 上移到
`middleware/auth.py`：auth / files / scheduled_tasks 三个模块共用同一个
`Depends(get_user_repository)`，`get_database` 仍只出现在 provider 内。

### 3.2 repository 新增方法

| repository | 新方法 | 委托 | 用途 |
| --- | --- | --- | --- |
| `UserRepository` | `touch_user_activity` / `get_user_activity_time` / `clear_user_activity` | `db/repositories/users.py` 同名函数 | 登录/注册/免密登录写活跃时间；登出读+清 |
| `ScheduledTaskRepository` | `get_workspace_name(task)` | 自身 `_db` + `repositories/sessions.get_session` | 定时任务工作目录名解析 |
| `ChatRepository` | `get_session_files(session_id)` | `db/repositories/files.get_session_files` | `session_uploads` 数据端口 |
| `ExpertRepository` | `get_session_files(session_id)` | 同上 | 同上 |

`ScheduledTaskRepository.get_workspace_name` 与
`services/scheduler.get_task_workspace_name` 口径逐条一致（任务自带
`workspace_name` → 来源会话 `workspace_name` → `scheduled_{task_id}`）。
scheduler 仍保留自己的函数（引擎侧调用，本批禁改 services），两条实现的等价性
由 `tests/db/test_scheduled_task_repository.py` 的 parity 用例守住。

### 3.3 `session_uploads` 的会话数据端口

`store_parsed_content` / `append_manifest` / `ensure_parsed_text` /
`build_turn_preview` 等函数原本接受 `Database`，内部只用两个方法：
`get_session` 与 `get_session_files`。新增内部解析器：

```python
def _session_store(db=None):
    if db is not None:
        return db
    return get_database()
```

因此实参可以是 `Database`（tools / sessions API / 既有测试不变），也可以是
`ChatRepository` / `ExpertRepository`（`api/chat.py`、`api/expert.py` 改传
repository）。`build_turn_preview` 的签名与返回值不变，`db=None` 的兜底语义
不变。

### 3.4 删除已无调用的 `database` property

`FileRepository.database`（原给 `verify_token_sso`）与
`ScheduledTaskRepository.database`（原给 `get_task_workspace_name` /
`verify_token_sso`）在本批后已无任何调用点，直接删除；对应 repository 测试
不再断言该 property。`UserRepository` / `ChatRepository` / `ExpertRepository`
仍各有保留边界（知识域、流式引擎），property 保留并在 docstring 写明用途。

## 4. 消除清单

| 文件 | 原调用 | 现调用 |
| --- | --- | --- |
| `api/auth.py` | `touch_user_activity(repo.database, ...)` ×3 | `touch_user_activity(repo, ...)` |
| `api/auth.py` | `get_user_activity_time(repo.database, ...)` / `clear_user_activity(repo.database, ...)` | 传 `repo` |
| `api/files.py` | `verify_token_sso(token, repo.database)` ×2 | `verify_token_sso(token, user_repo)`（`Depends(get_user_repository)`） |
| `api/scheduled_tasks.py` | `get_task_workspace_name(repo.database, task)` | `repo.get_workspace_name(task)` |
| `api/scheduled_tasks.py` | `verify_token_sso(token, repo.database)` ×2 | `verify_token_sso(token, user_repo)` |
| `api/chat.py` | `build_turn_preview(..., repo.database)` | `build_turn_preview(..., repo)` |
| `api/expert.py` | `build_turn_preview(..., repo.database)` | `build_turn_preview(..., repo)` |

全仓 `easy_agent/api/*.py` 的 `repo.database` 从 21 处降到 9 处；本批允许清单内
的 5 个文件从 19 处降到 7 处（剩余 7 处见 §5）。

## 5. 仍保留的边界与原因

| 文件 | 保留点 | 原因 |
| --- | --- | --- |
| `api/auth.py`（1 处） | `KnowledgeRepository(repo.database).is_team_space_manager(...)` | `KnowledgeRepository` 属于知识域 bounded context（`knowledges/core/repository.py`），构造入参就是 `Database`；本批不允许改 `knowledges/`。 |
| `api/chat.py`（5 处） | `authorize_scoped_chat(db=...)`、`prepare_knowledge_chat(db=...)` | 知识域独立 fail-closed 鉴权与编排，同上，改签名要动 `knowledges/`。 |
| `api/chat.py`（1 处） | `resolve_owned_expert(repo.database, ...)` | 函数在 `tools/expert_writer.py`（不在允许清单）；且 `api/expert.py` 已反向依赖 `api.chat`，chat 再 import `api.expert` 会成环，`ChatRepository` 不应跨域承载专家归属校验。 |
| `api/chat.py`（2 处） | `chat_stream_generator(db=...)`、`resume_stream_generator(db=...)` | **流式引擎边界**：`services/streaming` 的持久化路径强依赖 `Database`，按任务约定签名改动风险大，保留不硬改。 |
| `api/expert.py`（1 处） | `chat_stream_generator(db=...)` | 同上。 |
| `api/sessions.py`（2 处，未改动） | `store_parsed_content(db=...)`、`append_manifest(..., repo.database)` | `api/sessions.py` 不在 C1b 允许改动清单，留待后续批次；`session_uploads` 已同时兼容 `Database`，届时改传 repository 即可。 |

## 6. 不变量

- 认证：disabled 账号 403「账号已停用」、token_version 不匹配 401「您的账号在其他设备登录…」、空闲过期 401「登录已过期或未登录…」、`verify_token_sso` 失败返回 `None` 由调用方给 401，全部保持；
- 活跃时间：30s 写节流、写失败只告警、登出清空并弹 throttle，全部保持；
- 定时任务工作目录：解析规则、404/403 区分、路径拼接方式不变；
- `session_uploads`：落盘文件名、清单格式、预览块文案、`preview_max_chars` 截断不变；
- SSE：`chat_stream_generator` / `resume_stream_generator` 入参仍是同一 `Database`
  实例，事件序列不变；
- 数据库：仍只写 `session_messages` 行表；无新增 SQL、无新增写路径。

## 7. 测试策略

1. `tests/db/test_*_repository.py` 新增 5 个用例：活跃时间委托、`get_session_files`
   端口（chat/expert）、`session_uploads` 工作区端口、定时任务工作目录名解析
   （含与 scheduler 函数的 parity 断言）；
2. `tests/api/test_c1_remaining_domains.py` 升级为 C1b 门禁：
   - 4 个域仍有本地 provider；`auth.py` 的 provider 与 middleware 同一对象；
   - 各文件 `repo.database` 调用点数 == §5 登记值（消除的不许长回来，保留的
     不许悄悄消失）；
3. `tests/api/test_scheduled_tasks.py` 新增工作区文件 SSO 路径：坏 token → 401，
   有效 token → 404（认证已通过）；
4. 既有 `tests/api/test_files.py::test_preview_workspace_file` 继续覆盖
   token 查询参数 + 活跃时间路径；
5. 运行子集：`tests/api/test_auth.py test_files.py test_scheduled_tasks.py
   test_expert.py test_chat.py tests/db -q`；门禁：`uvx ruff check .`、
   `scripts/quality/import_graph.py --check-scc`、`scripts/quality/check_double_write.py`。

## 8. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| `verify_token_sso` 改为端口后语义漂移 | 端口方法名与 `Database` 完全同名；节流/异常/返回 `None` 逻辑未改；既有 token 预览用例覆盖 |
| `get_user_repository` 上移后依赖覆盖失效 | provider 函数对象在 `api.auth` 与 `middleware.auth` 是同一个；门禁测试断言 `is` 同一对象 |
| 工作目录名两份实现漂移 | parity 用例逐 case 对比 `ScheduledTaskRepository.get_workspace_name` 与 scheduler 函数 |
| `session_uploads` 端口不兼容旧调用 | `_session_store` 对 `None` 回退 `get_database()`，`Database` 直传行为不变（tools、sessions API、既有测试） |
| 删除 property 影响未知调用 | 全仓 grep 确认仅测试断言使用；两个 property 删除后 `ruff`/子集测试全绿 |
| 直接调用路由的旧测试传 `Database` | 新参数均为 FastAPI 依赖（`Depends`），直接调用方集中在 auth 路由，签名未变 |
