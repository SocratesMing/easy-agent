# C1 第一批：sessions 域 API 去 Database 门面

| 项 | 内容 |
| --- | --- |
| 状态 | 第一批已实现（sessions 域）；其他 API 域未动 |
| 日期 | 2026-10-18 |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 C1 |
| 范围 | `easy_agent/db/session_repository.py`（新增）、`easy_agent/api/sessions.py`、`tests/db/test_session_repository.py`、`tests/api/test_sessions.py` |
| 前置 | A2（会话消息单一写路径）、阶段 0（import SCC / 双写门禁）、B3 已完成；本批不重做、不改其文件 |
| 报告 | `docs/audit/2026-10-18-increment42-c1-sessions-report.md` |

## 1. 问题与目标

改造前 `easy_agent/api/sessions.py` 的 10 个路由全部声明
`db: Annotated[Database, Depends(get_database)]`，并直接调用
`Database` 门面上的业务方法（`create_session`、`list_sessions`、
`get_session`、`get_messages`、`update_session_title`、`toggle_session_pin`、
`delete_session`、`count_sessions`、`add_message`、`add_session_file`、
`get_session_files`、`update_session_workspace_name`），owner 校验则调用
`utils.get_owned_session(db, ...)`。

C1 的目标是让 API 层只依赖领域 repository、停止直接使用 `Database` 门面。
本批只做 sessions 域，保持对外 HTTP/JSON 契约与 A2 的写路径语义不变。

## 2. Repository 边界

### 2.1 新类

新增 `easy_agent/db/session_repository.py`，类 `SessionRepository`：

- **构造**：`SessionRepository(db)`，`db` 是现有 `Database` 实例；
- **不写 SQL**：每个方法都是对现有
  `db/repositories/sessions.py` / `db/repositories/files.py` 函数的薄委托，
  不复制、不改写 SQL，避免出现第二条会话写路径；
- **不 import FastAPI**：`get_owned_session` 在方法内 lazy import
  `utils.session.get_owned_session`，让 db 层在导入期不依赖 Web 框架；
- **`database` property**：暴露底层 `Database`，仅作为尚未迁移服务的边界例外
  （见 2.3）。

本批方法（覆盖 API 当前实际使用）：

| 方法 | 委托目标 | 说明 |
| --- | --- | --- |
| `create_session(session_data)` | `repositories.sessions.create_session` | A2：只写元数据 |
| `update_session_workspace_name(session_id, name)` | 同名函数 | 创建后补工作区名 |
| `list_sessions(limit, offset, username)` | 同名函数 | 列表仍按 pinned/updated_at 排序 |
| `get_session(session_id)` | 同名函数 | 行表优先 + legacy 只读回退 |
| `get_session_owner(session_id)` | 同名函数 | 轻量归属查询 |
| `get_owned_session(session_id, username)` | `utils.session.get_owned_session` | 404 语义不变 |
| `get_messages(session_id)` | 同名函数 | 只读行表 |
| `count_sessions(username)` | 同名函数 | 列表同口径 |
| `update_session_title(session_id, title)` | 同名函数 | 不碰 messages |
| `toggle_session_pin(session_id)` | 同名函数 | 返回新的 pinned |
| `delete_session(session_id)` | 同名函数 | 连带删除 session_messages |
| `add_message(session_id, message)` | 同名函数 | A2：只写行表 |
| `add_session_file(...)` | `repositories.files.add_session_file` | 上传登记 |
| `get_session_files(session_id)` | `repositories.files.get_session_files` | 删除会话时清文件 |

### 2.2 为什么不直接搬 SQL

`db/repositories/*` 已经是 SQL 归属地（ARCH-03 Phase 2），且阶段 0 双写门禁
（`scripts/quality/check_double_write.py`）只冻结 `repositories/sessions.py` 的写入方式。
把 SQL 再复制进 `SessionRepository` 会制造第二处实现，反而扩大双写面。
因此本批采用「对象 + 委托」而不是「重写 SQL」。

### 2.3 边界例外：上传解析服务

`services/session_uploads.py` 的 `store_parsed_content` / `append_manifest`
仍以 `db=` 参数接收 `Database`（内部调用 `Database.get_session` /
`get_session_files`）。本批不修改 `services/`（不在允许范围），因此上传路由
通过 `repo.database` 把底层实例交给这两个函数。

约束：API 层除这一处「把实例交给未迁移服务」外，不再对 `repo.database`
调用任何业务方法；C1 后续批次把 `session_uploads` 改为依赖 repository 后，
删除该 property 的最后使用点。

## 3. API 依赖注入方式

### 3.1 Provider

`api/sessions.py` 内新增：

```python
def get_session_repository(
    db: Annotated[object, Depends(get_database)],
) -> SessionRepository:
    return SessionRepository(db)
```

- 路由统一改为
  `db: Annotated[SessionRepository, Depends(get_session_repository)]`；
- `api/sessions.py` 不再 `from ..db import Database`，只保留
  `from ..db import get_database`，且 `get_database` 只出现在 provider 内；
- 参数名保留 `db` 是为了兼容直接调用处理函数的既有测试；函数体内立即
  `repo = _as_session_repository(db)`，**不出现任何 `db.*` 业务调用**。

### 3.2 兼容 shim

`_as_session_repository(value)`：FastAPI 注入时 `value` 已是
`SessionRepository`，直接返回；直接调用处理函数并传入 `Database` 时（如
`tests/integration/test_session_ownership.py`）做一次窄包装。这样不修改既有
测试即可保持直接调用路径可用。

### 3.3 与现有 fixture 的兼容

- `tests/conftest.py` 通过 `app.dependency_overrides[get_database] = lambda: db`
  注入临时库；provider 内部走 `Depends(get_database)`，override 继续生效。
- 测试也可直接
  `app.dependency_overrides[get_session_repository] = lambda: repo`
  替换整个领域依赖（新增 API 测试即用此方式）。
- `get_database` 仍只用于测试/生命周期，不新增其他业务调用点。

## 4. 错误与权限兼容

| 场景 | 改造前 | 改造后 |
| --- | --- | --- |
| 会话不存在 | `get_owned_session` 抛 404 | repository 委托同一函数，404 不变 |
| 跨用户访问 | 404（不泄露存在性） | 同上 |
| 列表/计数过滤 | `username` + `[定时任务]%` 排除 | 委托同一 SQL，不变 |
| 标题/置顶/消息/删除返回 | 原返回类型/字段 | 不变 |
| 删除会话 | 先删物理文件/缓存/工作区，再删 DB | 顺序不变，仅 DB 调用改 repository |
| 上传 413/解析失败降级 | 原有逻辑 | 不变；仅 DB 登记改 repository |

repository 不做任何新的异常包装或权限判断，`HTTPException(404, "会话不存在")`
文本与状态码保持原样。

## 5. 测试策略

1. **Repository 单测**（`tests/db/test_session_repository.py`）：
   临时 SQLite `Database` + `init_tables()`，覆盖 CRUD、消息、上传文件、
   owner/404、删除幂等；断言 A2 下 `sessions.messages` 仍为空串。
2. **API 黑盒**（`tests/api/test_sessions.py`）：
   既有创建/列表/详情/标题/置顶/消息/历史/删除/上传用例全部保留；
   新增两个回归：
   - 静态断言路由文件无 `from ..db import Database`、无
     `Annotated[Database`，且 10 个路由都使用
     `Depends(get_session_repository)`；
   - 用 `_SpyRepository` 覆盖 `get_session_repository`，断言创建/详情/列表
     确实经过 repository 方法，路由行为不变。
3. **门禁**：`scripts/quality/check_double_write.py`、`scripts/quality/import_graph.py
   --check-scc`、`uvx ruff check .`。
4. **全量测试**：不在本子任务执行，由 Lead 统一跑（见报告）。

## 6. 未覆盖域与后续批次

本批只做 sessions 域。仍直接使用 `Database` 门面的 API 文件（按当前 grep）：

- `easy_agent/api/chat.py`（会话创建/消息写入/读会话）
- `easy_agent/api/files.py`
- `easy_agent/api/auth.py`（含 users 域与 `KnowledgeRepository`）
- `easy_agent/api/scheduled_tasks.py`
- `easy_agent/api/expert.py`
- `easy_agent/api/settings.py`（按实际实现复核）
- `knowledges/`、`personnel/` 等已有自己的 repository，但不在本次 sessions 范围

建议的 C1 后续批次顺序：users/auth → files → chat（依赖 sessions 与
streaming 的写路径，风险最高，放后）→ scheduled_tasks/expert。每批保持
「repository 委托既有函数、不改 HTTP 契约」的同一策略。

## 7. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 路由参数名仍叫 `db`，易被误读为 Database | 类型标注是 `SessionRepository`，函数体立即转 `repo`，文件内无 `db.*` 调用；报告显式说明 |
| 直接调用处理函数的旧测试传入 Database | `_as_session_repository` 窄包装；只影响直接调用，不影响 FastAPI |
| 上传服务仍拿到底层 Database | 限定为 `repo.database` 单点，文档标注为 C1 后续迁移项 |
| 委托层与门面行为漂移 | 不复制 SQL；测试覆盖全部 API 使用的方法与 404 语义 |
| 其他 API 域仍在门面上 | 明确列在未覆盖域，后续批次单独验收；本批不宣称 C1 全部完成 |

## 8. 验收口径

- `easy_agent/api/sessions.py` 无 `from ..db import Database`、无
  `Annotated[Database`、无 `db.<业务方法>` 调用；
- 10 个路由均使用 `Depends(get_session_repository)`；
- `tests/api/test_sessions.py`、`tests/api/test_chat.py`、`tests/db` 通过；
- 双写门禁、import SCC 门禁、ruff 通过；
- HTTP 路径、响应模型、状态码、JSON 字段不变；
- 全量测试由 Lead 执行，本报告不声称全量通过。
