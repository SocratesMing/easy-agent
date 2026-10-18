# Increment 61 — C1b 服务层边界收口 执行报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 阶段 | C1b（C1 后续批次：把 API → 服务的 `Database` 透传收口） |
| 设计 | `docs/design/2026-10-18-c1-service-boundaries.md` |
| 范围 | 仅后端：`middleware/auth.py`、5 个 domain repository、5 个 API 文件的服务边界段、`services/session_uploads.py`、对应测试 |
| 非目标 | 未改前端、未改 streaming 包 / `agent.py` / `agent_manager` / `db/database.py` / checkpoints / mcp / `tests/conftest.py` / `api/sessions.py`；未执行 Batch 0/1；未 commit；未跑全量 1338 |
| 基线 | 改动前同命令子集 199 passed；working tree 保留 wave1/2 未提交改动（未回退） |

## 1. 结论

- API 层允许清单内 5 个文件的 `repo.database`：**19 → 7**；全仓 `easy_agent/api/*.py`：**21 → 9**（剩余 2 处在 `api/sessions.py`，不在本批允许清单）。
- 认证辅助（活跃时间 / SSO token 校验）与应用层解耦：`middleware.auth` 接受
  `Database | UserRepository` 端口，`get_user_repository` provider 上移到
  middleware 供 auth / files / scheduled_tasks 共用。
- 定时任务工作目录名、上传文件解析（`session_uploads`）改由 repository 方法 /
  端口提供，不再把 `Database` 传进 scheduler / file_context。
- 2 个已无调用者的 `database` property（File / ScheduledTask）删除；
  3 个仍有保留边界的 property（User / Chat / Expert）保留并注明用途。
- 鉴权/节流/异常/HTTP 字段/SSE/落库行为均未改变；新增 7 个 repository/API
  用例 + 2 个 C1b 门禁静态用例，指定子集 206 passed，含门禁文件 211 passed，
  另跑 2 个直接相关的 integration 文件 4 passed / 1 skipped。

## 2. 改动文件

| 文件 | 改动 |
| --- | --- |
| `easy_agent/middleware/auth.py` | 新增 `UserActivityStore = Database \| UserRepository` 端口；4 个活跃时间/SSO 函数改收端口；新增 `get_user_repository` provider（`middleware/__init__.py` 未改，API 模块直接从 `middleware.auth` 导入） |
| `easy_agent/db/repositories/user_repository.py` | 新增 `touch_user_activity` / `get_user_activity_time` / `clear_user_activity` 委托；property 用途注释收窄 |
| `easy_agent/db/repositories/scheduled_task_repository.py` | 新增 `get_workspace_name(task)`；删除已无调用的 `database` property |
| `easy_agent/db/repositories/file_repository.py` | 删除已无调用的 `database` property |
| `easy_agent/db/repositories/chat_repository.py` | 新增 `get_session_files(session_id)`（session_uploads 端口） |
| `easy_agent/db/repositories/expert_repository.py` | 同上 |
| `easy_agent/api/auth.py` | provider 改为从 `middleware` 导入（去重）；5 处活跃时间调用改传 `repo`；`get_database` import 移除 |
| `easy_agent/api/files.py` | `preview_file` 注入 `UserRepository`；2 处 `verify_token_sso` 改传 repository |
| `easy_agent/api/scheduled_tasks.py` | 工作目录名改 `repo.get_workspace_name`；移除 scheduler 函数 import；`get_task_workspace_file` 注入 `UserRepository`；2 处 `verify_token_sso` 改传 repository |
| `easy_agent/api/chat.py` | `build_turn_preview(..., repo)`（1 处边界消除；其余 5 处保留） |
| `easy_agent/api/expert.py` | `build_turn_preview(..., repo)`（1 处边界消除；流式 1 处保留） |
| `easy_agent/services/session_uploads.py` | 新增 `_session_store` 端口解析；2 处 `db` 直取改为端口调用；docstring 更新 |
| `tests/api/test_c1_remaining_domains.py` | 升级为 C1b 门禁：provider 同一性 + `repo.database` 边界点数冻结 |
| `tests/api/test_scheduled_tasks.py` | 新增工作区文件预览的 SSO 认证路径用例（401 / 有效 token） |
| `tests/db/test_*_repository.py` | 新增活跃时间、`get_session_files`、session_uploads 端口、工作目录名 parity 用例；移除两个已删 property 的断言 |

## 3. 消除的边界（逐条）

| 文件 | 位置 | 原 | 现 |
| --- | --- | --- | --- |
| `api/auth.py` | 注册 | `touch_user_activity(repo.database, user.username)` | `touch_user_activity(repo, ...)` |
| `api/auth.py` | 密码登录 | 同上 | 同上 |
| `api/auth.py` | 免密登录 | 同上 | 同上 |
| `api/auth.py` | 登出 | `get_user_activity_time(repo.database, ...)` | `get_user_activity_time(repo, ...)` |
| `api/auth.py` | 登出 | `clear_user_activity(repo.database, ...)` | `clear_user_activity(repo, ...)` |
| `api/files.py` | 文件预览 Header token | `verify_token_sso(token, repo.database)` | `verify_token_sso(token, user_repo)` |
| `api/files.py` | 文件预览 query token | 同上 | 同上 |
| `api/scheduled_tasks.py` | 任务工作区 | `get_task_workspace_name(repo.database, task)` | `repo.get_workspace_name(task)` |
| `api/scheduled_tasks.py` | 工作区文件 Header token | `verify_token_sso(..., repo.database)` | `verify_token_sso(..., user_repo)` |
| `api/scheduled_tasks.py` | 工作区文件 query token | 同上 | 同上 |
| `api/chat.py` | 本轮文件预览 | `build_turn_preview(..., repo.database)` | `build_turn_preview(..., repo)` |
| `api/expert.py` | 本轮文件预览 | 同上 | 同上 |

共消除 12 处透传调用点（auth 5 + files 2 + scheduled 3 + chat 1 + expert 1）。

## 4. 仍保留的边界与原因（诚实清单）

| 文件 | 处数 | 保留点 | 原因 |
| --- | --- | --- | --- |
| `api/auth.py` | 1 | `KnowledgeRepository(repo.database).is_team_space_manager(...)` | 知识域独立 bounded context（`knowledges/core/repository.py` 构造入参即 `Database`），本批不允许改 `knowledges/` |
| `api/chat.py` | 1 | `authorize_scoped_chat(db=repo.database, ...)` | 知识域 fail-closed 鉴权，同上 |
| `api/chat.py` | 1 | `prepare_knowledge_chat(db=repo.database, ...)` | 知识域检索/编排，同上 |
| `api/chat.py` | 1 | `resolve_owned_expert(repo.database, ...)` | 函数在 `tools/expert_writer.py`（不在允许清单）；且 `api/expert.py` 已反向依赖 `api.chat`，chat 反向 import expert 会成环；`ChatRepository` 不跨域承载专家归属校验 |
| `api/chat.py` | 2 | `chat_stream_generator(db=...)`、`resume_stream_generator(db=...)` | **流式引擎边界**：`services/streaming` 持久化路径强依赖 `Database`，按任务约定签名改动风险大，不硬改 |
| `api/expert.py` | 1 | `chat_stream_generator(db=...)` | 同上 |
| `api/sessions.py` | 2 | `store_parsed_content(db=repo.database)`、`append_manifest(..., repo.database)` | `api/sessions.py` 不在 C1b 允许清单；`session_uploads` 已同时兼容 `Database`，后续批次可零成本改传 repository |

另有 2 处**非 API** 的 `Database` 服务入口保持原样（不在本批范围）：
`services/scheduler.get_task_workspace_name(db, task)`（引擎侧调用，本批禁改
services）与 `tools/session_file_reader` 直传 `Database`（工具层，禁改）。

## 5. 测试结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/api/test_auth.py tests/api/test_files.py tests/api/test_scheduled_tasks.py tests/api/test_expert.py tests/api/test_chat.py tests/db -q` | **206 passed**（基线同命令 199 passed；新增 7 个用例） |
| 追加 `tests/api/test_c1_remaining_domains.py` 后同批 | **211 passed** |
| `pytest tests/integration/test_auth_session_expiry.py tests/integration/test_multi_worker_auth.py -q` | **4 passed, 1 skipped**（middleware 端口对 `Database` 入参向后兼容） |
| `uvx ruff check .` | **All checks passed!** |
| `python3 scripts/import_graph.py --check-scc` | **OK**：217 modules / 494 internal edges / 0 SCC |
| `python3 scripts/check_double_write.py` | **OK**：legacy blob 与 Database 业务方法均冻结（141 methods / 106 pure delegations） |

新增用例明细：

- `tests/db/test_user_repository.py::test_repository_delegates_activity_timestamps`
- `tests/db/test_scheduled_task_repository.py::test_repository_resolves_workspace_name_like_scheduler`（与 scheduler 函数逐 case parity）
- `tests/db/test_chat_repository.py::test_repository_exposes_session_files_for_upload_ports`
- `tests/db/test_chat_repository.py::test_repository_satisfies_session_uploads_workspace_port`
- `tests/db/test_expert_repository.py::test_repository_exposes_session_files_for_upload_ports`
- `tests/api/test_scheduled_tasks.py::test_task_workspace_file_rejects_bad_token`
- `tests/api/test_scheduled_tasks.py::test_task_workspace_file_accepts_valid_token`
- `tests/api/test_c1_remaining_domains.py`：`test_user_repository_provider_is_shared_from_middleware`、`test_repo_database_usage_shrinks_to_documented_boundaries`（升级门禁）

既有 `tests/api/test_files.py::test_preview_workspace_file` 继续覆盖「真实注册
token → 预览 200」，实际走通 `verify_token_sso(token, UserRepository)` 与
repository 活跃时间读写。

## 6. 不变量核对（人工）

| 项 | 核对 |
| --- | --- |
| 401/403 状态码与 detail 文案 | 未改；`verify_token_sso` 失败仍返回 `None`，调用方决定 401 |
| 活跃时间 30s 节流、异常转 warning | 逻辑仍在 middleware，未改 |
| 空闲登出判定 | `_user_session_is_active` 逻辑未改（只换类型标注） |
| 定时任务 404「任务不存在」/403「无权访问此任务」 | 未改 |
| 工作目录路径拼接 | 未改（仅名称来源改为 repository 方法） |
| `session_uploads` 文件落盘 / 清单格式 / 预览文案 | 未改；仅数据源端口化 |
| SSE 事件与顺序 | 未改（`chat_stream_generator` 入参仍是同一 `Database` 实例） |
| 落库路径 | 未改（仍只写 `session_messages` 行表）；双写门禁通过 |
| HTTP 路由/方法/response_model | 未改；新增的仅是 DI 依赖参数 |

## 7. 风险与说明

1. **工作目录名两份实现**：scheduler 侧函数保留（引擎调用，本批禁改），
   repository 侧为等价实现；已加 parity 用例，任一侧改动导致不一致会失败。
2. **`get_user_repository` 上移**：`api/auth.py` 不再定义 provider，改用
   `middleware.auth` 的单一实现；既有 `from easy_agent.api.auth import
   get_user_repository` 仍可导入且是同一对象（FastAPI 依赖覆盖/既有测试不受影响），
   门禁测试用 `is` 断言防回退。
3. **删除 property**：`FileRepository.database` / `ScheduledTaskRepository.database`
   已无调用者（全仓 grep + 测试）；若后续有代码需要底层实例，应新增显式方法而非
   恢复 property。
4. **未跑全量**：按任务约定只跑指定子集、门禁，以及 2 个直接相关的
   integration 文件（`test_auth_session_expiry.py` / `test_multi_worker_auth.py`，
   验证 middleware 端口对 `Database` 入参的向后兼容）；其余 `tests/integration`
   与全量 1338 未在本轮运行。
5. **未 commit**：working tree 保留 wave1/2 与本次改动，未提交。

## 8. 后续建议

1. 知识域（`knowledges/`）定义自己的 `KnowledgePrincipal` / 仓储端口后，
   可消除 `api/auth.py` 1 处与 `api/chat.py` 2 处知识边界。
2. `services/streaming` 若接受「持久化端口」而非 `Database`，可消除 chat/expert
   剩余 3 处流式边界（本批按约定不动）。
3. `api/sessions.py` 的两处 `repo.database` 可照本批模式改为传 repository。
4. `resolve_owned_expert` 可迁到 `ExpertRepository` 或去掉 tools 层对 `Database`
   的直接依赖后消除（需解开 `api.chat ↔ api.expert` 的单向依赖设计）。
