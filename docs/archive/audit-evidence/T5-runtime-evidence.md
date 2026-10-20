# easy-agent 代码审计 · T5 运行验证与证据审计报告

- 审计员：T5（运行验证与证据审计员）
- 审计对象：`/home/sututu/code/easy-agent`（git HEAD `1f04992`，工作区 50 项未提交改动，以实际代码为准）
- 审计方式：**只读运行验证**。所有实验使用 `/tmp/ea-audit/**` 临时配置/数据库/工作区；仓库只写入了本报告（`_analysis/`）。
- 证据文件：原始 JSON/日志在 `/tmp/ea-audit/`，脚本在 `/tmp/ea-audit/scripts/`，并已复制到 `_analysis/t5-runtime-scripts/`。

> 敏感内容一律脱敏：不粘贴 `.env` 值、JWT 密钥、token、数据库内容；只记录长度/状态码/命中标记。

## 0. 结论摘要（10 条）

1. **P0 已实锤（真实 uvicorn 复现）**：SPA 兜底路由 `GET /{full_path:path}` 可被 `..%2f` 穿越，未认证即可读取 `.env`/`.env.test`/`.env.dev`、`data/.jwt_secret`（JWT 签名密钥，43B）、`data/easy_agent.db`（SQLite，2.6MB，Range 206）、`easy_agent/config/config.yaml`、`/etc/passwd`。
2. **P0 已实锤**：`GET /terminal` 与 `ws://…/agent/terminal/ws` 完全无鉴权；实测无 token 建立 WS 并执行 `id`，返回 `uid=1000(sututu) … groups=…,27(sudo),…,1001(docker)`，即未认证远程 shell（宿主机用户权限）。
3. **P1 已实锤**：`GET /agent/files/preview` 无 auth dependency；无 token 或伪造 token 时回退 `db.get_or_create_default_user()`（=admin），未认证读到 admin 工作区文件；路径穿越已被 `resolve_workspace_file` 拦截（400/404）。
4. **P1 已实锤**：`POST /agent/prompts/update` 无鉴权，且 `path` 支持绝对路径注入，实测未认证写入 `/tmp/t5_prompts_abs_write_probe.md`（任意可写目录的 `*.md` 写入）。
5. **P1 已实锤**：限流实现失效——第 20 次登录后返回 **500**（`HTTPException(429)` 从 `BaseHTTPMiddleware` 逃逸，被全局异常处理器转 500），真实 uvicorn 上随后新连接 `/api/health` **ReadTimeout（≥20s 无响应）**；2 次独立复现。
6. **P1 已实锤**：默认配置 `idle_logout_minutes: 0` → 登录 token **无 `exp` 声明**；伪造“2 小时前已过期”但签名正确、版本正确的 token 访问 `/agent/auth/profile` 返回 **200**（`decode_access_token(verify_exp=False)`），token 实际永不过期。
7. **P2 已实锤**：测试基线非绿——`904 tests: 890 passed, 10 failed, 13 errors, 0 skipped, 276.7s`；默认 `pytest tests/` 会因 `test_sandbox_isolation.py` 收集错误 **0.78s 即 Interrupted**（导入已删除的 `easy_agent.agent._is_within`），不加 `--continue-on-collection-errors` 整套测试根本不执行。
8. **P2 已实锤**：SQLite 单连接 + 全局 RLock + 无 WAL；async 端点里同步 DB 调用**阻塞事件循环**（50 次 `add_message` 造成 129.7ms 循环停顿，`to_thread` 对照 7.3ms）；4 进程并发写同一 SQLite 不丢消息（200/200）但单次写尾延迟达 840ms。
9. **P2 已实锤**：流式增量落库为 **O(n²) 写放大**（每 500 字符重写全量内容）：2k/10k/20k 字符回复分别写入 5.7×/21.7×/41.7× SQL 字符（20k 字 → 834KB SQL）。而 `add_message` 是行级 INSERT，**不是** JSON blob 全量重写（1k/5k 行时均 296 SQL 字符/次）。
10. **P3 已实锤**：冷启动到首个健康请求约 **3.0–3.4s**（import 1.9–2.5s + lifespan 0.87s，`EASY_SKIP_LLM_PROBE=1`）；默认非 prod 会真打一次 LLM 连接探测，模型不可达时 lifespan 拖到 **12.4s**；首次 `/openapi.json` 同步生成 **283–390ms** 阻塞事件循环。

## 1. 运行环境与可复现命令

| 项 | 值 |
|---|---|
| OS / 内核 | Ubuntu 24.04 (WSL2), `5.15.167.4-microsoft-standard-WSL2` |
| Python | `.venv/bin/python` 3.11.15（uv 管理，venv 内无 pip） |
| 关键包 | deepagents 0.6.8 / langchain 1.3.4 / langgraph 1.2.4 / fastapi 0.136.3 / pytest 9.0.3 / pytest-asyncio 1.4.0 |
| 全局工具 | uv（`.local/bin/uv`）、ruff 0.15.12、bwrap 0.9.0、node |
| Landlock | 可用，ABI 版本 1；bwrap 可用（本机） |
| 测试插件 | 无 pytest-timeout / xdist / coverage；无 venv pip → 依赖检查用 `uv pip check` |

**复现命令（全部在 Linux 侧执行）**：

```bash
cd /home/sututu/code/easy-agent
bash /tmp/ea-audit/scripts/01_pytest.sh      # 全量测试（隔离 TMPDIR，--continue-on-collection-errors）
bash /tmp/ea-audit/scripts/02_runner.sh      # compile/AST 静态检查 + uv pip check + ruff 前置信息
bash /tmp/ea-audit/scripts/03_runner.sh      # 启动探针：冷启动、路由/auth 矩阵、OpenAPI、SPA 穿越
bash /tmp/ea-audit/scripts/04_runner.sh      # 无认证 WebSocket 终端实测（只跑 pwd/id/echo）
bash /tmp/ea-audit/scripts/05_runner.sh      # 鉴权冒烟矩阵（真依赖，无 pytest override）
bash /tmp/ea-audit/scripts/06_runner.sh      # SQLite 多进程/锁/agent_manager/分布式锁
bash /tmp/ea-audit/scripts/06b_runner.sh     # 事件循环阻塞 / 写放大 / detached stream
bash /tmp/ea-audit/scripts/07_runner.sh      # bwrap/Landlock/fs_safety 沙箱矩阵
bash /tmp/ea-audit/scripts/08_runner.sh      # SPA 穿越读敏感文件（脱敏输出）
bash /tmp/ea-audit/scripts/09_runner.sh      # 冷启动分阶段计时
bash /tmp/ea-audit/scripts/10_runner.sh      # 真实 uvicorn HTTP 复核（127.0.0.1:18099）
bash /tmp/ea-audit/scripts/12_runner.sh      # prompts 未授权写 / completion / settings 公共面
bash /tmp/ea-audit/scripts/13_runner.sh      # startup_knowledge 重复调用证据
```

隔离措施：每个实验由脚本自行生成 `tests/test_config.yaml` 的“已解析”副本（全部路径指向 `/tmp/ea-audit/<case>/`），设置 `EASY_CONFIG`/`EASY_JWT_SECRET`/`EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD`/`AGENT_ENV=test`，不触碰仓库 `data/`、`workspace/`、`sessions/`、`memories/`。

污染检查（测试基线前后对仓库运行时目录做 mtime/size 快照 diff）：`data/`、`workspace/`、`memories/`、`sessions/` 无任何变化；但 `logs/` 被追加（见 §2.4，属被验证的缺陷本身）。

## 2. 测试基线

### 2.1 数字

| 指标 | 值 |
|---|---|
| junit testcase 数 | 904 |
| passed（pytest 终端汇总） | 890 |
| failed | 10 |
| errors（13 = 12 个 teardown error + 1 个 collection error；teardown 与同批 failed 重叠） | 13 |
| skipped | 0 |
| pytest 自身耗时 | 276.3s（墙钟 297s，含启动/收尾） |
| 退出码 | 1 |

（pytest 终端原文：`10 failed, 890 passed, 4 warnings, 13 errors in 276.73s`；junit 把同一用例的 failed+teardown error 记为两个 testcase 条目，故 testcase 总数 904 与 890+10+13 口径不同。）

**关键发现：默认命令 `pytest`（AGENTS.md 的 `pytest tests/ -v`）在 0.78s 内 `Interrupted: 1 error during collection`，一个测试都不跑。** 必须加 `--continue-on-collection-errors` 才能得到上面的数字。

```
ERROR collecting tests/integration/test_sandbox_isolation.py
tests/integration/test_sandbox_isolation.py:166: in <module>
    from easy_agent.agent import _check_external_dir_safety, _is_within, EasyAgent
E   ImportError: cannot import name '_is_within' from 'easy_agent.agent'

!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=========================== short test summary info ============================
ERROR tests/integration/test_sandbox_isolation.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

根因：`_is_within` / `_check_external_dir_safety` 已迁移到 `easy_agent/fs_safety.py`（agent.py 不再导出），测试文件未同步 → **测试漂移（代码/测试不一致），非环境问题**。

### 2.2 失败/错误清单（10 failed + 13 errors；下表 22 行含 teardown error）

| # | 用例 | 类别 | 原因摘要 | 归因 |
|---|---|---|---|---|
| 1 | `tests/integration/test_auth_session_expiry::test_recent_api_call_renews_expired_token_session` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 2 | `tests/integration/test_auth_session_expiry::test_expired_token_fails_after_idle_timeout` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 3 | `tests/integration/test_auth_session_expiry::test_unexpired_token_fails_after_idle_timeout` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 4 | `tests/integration/test_auth_session_expiry::test_recent_signed_token_restores_idle_cache_after_process_restart` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 5 | `tests/integration/test_auth_session_expiry::test_stale_token_issue_time_cannot_reopen_idle_window` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 6 | `tests/integration/test_auth_session_expiry::test_verify_token_sso_rejects_after_idle_timeout` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 7 | `tests/integration/test_auth_session_expiry::test_main_auth_rejects_disabled_user_even_with_current_token` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 8 | `tests/integration/test_auth_session_expiry::test_query_token_auth_rejects_disabled_user` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 9 | `tests/integration/test_auth_session_expiry::test_zero_idle_timeout_allows_expired_token_without_cache` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 10 | `tests/integration/test_auth_session_expiry::test_zero_idle_timeout_issues_non_expiring_token` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 11 | `tests/integration/test_auth_session_expiry::test_logout_invalidates_never_expiring_token` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 12 | `tests/integration/test_auth_session_expiry::test_logout_logs_first_login_and_last_activity` | teardown error | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移 |
| 13 | `tests/integration/test_auth_session_expiry::test_recent_api_call_renews_expired_token_session` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 14 | `tests/integration/test_auth_session_expiry::test_expired_token_fails_after_idle_timeout` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 15 | `tests/integration/test_auth_session_expiry::test_unexpired_token_fails_after_idle_timeout` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 16 | `tests/integration/test_auth_session_expiry::test_recent_signed_token_restores_idle_cache_after_process_restart` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 17 | `tests/integration/test_auth_session_expiry::test_stale_token_issue_time_cannot_reopen_idle_window` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 18 | `tests/integration/test_auth_session_expiry::test_verify_token_sso_rejects_after_idle_timeout` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 19 | `tests/integration/test_auth_session_expiry::test_zero_idle_timeout_allows_expired_token_without_cache` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 20 | `tests/integration/test_auth_session_expiry::test_logout_invalidates_never_expiring_token` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 21 | `tests/integration/test_auth_session_expiry::test_logout_logs_first_login_and_last_activity` | failed | `E   AttributeError: module 'easy_agent.middleware.auth' has no attribute '_user_activity_cache'` | 测试漂移（`middleware/auth.py` 已改为 DB 存储活跃时间，无 `_user_activity_cache`） |
| 22 | `tests/services/test_mcp_config::test_stdio_precheck_reports_missing_workdir` | failed | `E   AssertionError: assert '工作目录不存在' in '命令不存在: uv（请确认该命令在后端进程的 PATH 中，或填写绝对路径）'` | 环境相关（子进程 PATH 找不到 `uv`）；非产品缺陷 |

- 13 个 error 中 12 个是 `tests/integration/test_auth_session_expiry.py` 的 teardown error：测试仍操作已删除的模块级属性 `_user_activity_cache`（`tests/integration/test_auth_session_expiry.py:58-231`），同一原因也让该文件 9 个用例 failed。**结论：代码已重构、测试未同步。**
- 唯一环境相关失败：`tests/services/test_mcp_config.py::test_stdio_precheck_reports_missing_workdir` 断言 `工作目录不存在`，实际报 `命令不存在: uv` → 测试机/子进程 PATH 问题，非产品代码缺陷。
- 无 skip：本环境没有出现“因缺 MySQL/PostgreSQL/OCR/soffice/网络而 skip”的用例（套件本身没有 skip 标记）；外部依赖路径未被运行验证覆盖（见 §12）。

### 2.3 测试覆盖缺口（与运行证据相关）

- `middleware/rate_limit.py:54-55` 在 `PYTEST_CURRENT_TEST` 存在时**直接跳过限流**，而套件没有直接测试 429 路径 → 限流失效（§6.3）长期未被测试发现。
- `test_sandbox_isolation.py` 收集失败 → 沙箱隔离回归测试实际未执行。

### 2.4 测试对仓库的副作用（实锤缺陷）

运行 `pytest` 后，仓库 `logs/` 被追加写入（文件数与 git status 不变，仅内容增长）：

| 文件 | 运行前 | 运行后 | 增量 |
|---|---|---|---|
| `logs/proc-v01-easy-agent.log` | 18,928,321 B | 21,355,301 B | +2,426,980 B |
| `logs/err-v01-easy-agent.log` | 5,341,714 B | 5,351,561 B | +9,847 B |
| `logs/comm-v01-easy-agent.log` | 8,842 B | 10,725 B | +1,883 B |

原因：`easy_agent/initialization/log.py:17` 把 `LOG_DIR` **硬编码为 `./logs`**，`setup_logging()` 在 `initialize_runtime()`（import 期）执行，既不读 `config.log_dir` 也不读 conftest 的 `TEST_LOG_DIR`；测试/工具从仓库根启动即写入生产日志文件。

## 3. 路由鉴权矩阵（硬数据）

`app.routes` 反射结果：**总数 139（HTTP 138 + WebSocket 1）**。

鉴权判定方法：递归遍历 `route.dependant.dependencies` 的 `call.__name__`；另对 33 条 knowledge 路由做了人工分类（它们使用自定义依赖 `get_knowledge_principal` / `_admin`）。**本矩阵区分三类**：`JWT`=有 `get_current_username`；`KNOWLEDGE`=仅 `get_knowledge_principal`；`—`=无任何鉴权依赖。

- 有 `get_current_username`（JWT）的路由：**78**
- 无 JWT 依赖的 HTTP 路由：**60**，其中 knowledge 自定义鉴权 **33** 条、真正无鉴权 **27** 条
- WebSocket 路由 1：`/agent/terminal/ws`，**无鉴权**
- 限流覆盖：全站仅 8 条规则（前缀匹配），实际覆盖 6 条路由（见下表与 §6.3）

中间件顺序（`app.user_middleware`，后加者在外层）：`['LegacyHostRoutes', 'BaseHTTPMiddleware', 'BaseHTTPMiddleware', 'RateLimitMiddleware', 'CORSMiddleware']`

### 3.1 全量路由表

| # | path | methods | auth dependency | 限流覆盖 | 备注 |
|---|---|---|---|---|---|
| 1 | `/` | GET | — | — |  |
| 2 | `/agent/auth/login` | POST | — | /agent/auth/login | 限流 20/60s |
| 3 | `/agent/auth/login-passwordless` | POST | — | /agent/auth/login-passwordless | 限流 20/60s；用户不存在自动注册（需人工复核滥用面） |
| 4 | `/agent/auth/register` | POST | — | — |  |
| 5 | `/agent/completion/chat` | POST | — | — | 无鉴权 LLM 透传；无限流（§6.2） |
| 6 | `/agent/config` | GET | — | — | 无鉴权；泄漏 provider/model/system_prompt 前 100 字 |
| 7 | `/agent/files/preview` | GET | — | — | 无 auth dep；无/伪造 token 回退 admin（§6.1） |
| 8 | `/agent/health` | GET | — | — |  |
| 9 | `/agent/live` | GET | — | — |  |
| 10 | `/agent/prompts/query` | POST | — | — | 无鉴权（读提示词目录列表） |
| 11 | `/agent/prompts/read` | POST | — | — | 无鉴权（读提示词内容） |
| 12 | `/agent/prompts/update` | POST | — | — | 无鉴权；path 绝对路径注入可写任意 *.md（§6.2） |
| 13 | `/agent/public-list` | GET | — | — | 无鉴权；泄漏宿主路径 `/home/.../experts` |
| 14 | `/agent/ready` | GET | — | — |  |
| 15 | `/agent/scheduled-tasks/{task_id}/workspace/file` | GET | — | — | 无 auth dep，但 handler 手工 verify_token_sso → 实测 401（正确） |
| 16 | `/agent/settings/models` | GET | — | — | 无鉴权；模型清单（含 context_length/is_active） |
| 17 | `/agent/settings/upload-limits` | GET | — | — | 无鉴权；上传限制配置 |
| 18 | `/agent/settings/web-search` | GET | — | — | 无鉴权；搜索配置状态 |
| 19 | `/agent/terminal/ws` | WS | — | — | WebSocket 终端，**无鉴权**（§5） |
| 20 | `/api/config` | GET | — | — | 同 /agent/config |
| 21 | `/api/health` | GET | — | — |  |
| 22 | `/api/live` | GET | — | — |  |
| 23 | `/api/personnel/login-policy` | GET | — | — |  |
| 24 | `/api/ready` | GET | — | — |  |
| 25 | `/docs` | GET,HEAD | — | — | Redoc 文档公开 |
| 26 | `/openapi.json` | GET,HEAD | — | — | OpenAPI 公开（Pydantic 422 细节放大攻击面） |
| 27 | `/terminal` | GET | — | — | Web 终端页面，**无鉴权** |
| 28 | `/{full_path:path}` | GET | — | — | SPA 兜底；**os.path.join 穿越（P0）** |
| 29 | `/agent/knowledge/v1/admin/team-space-managers` | GET | KNOWLEDGE | — |  |
| 30 | `/agent/knowledge/v1/admin/team-space-managers/{user_id}` | PUT | KNOWLEDGE | — |  |
| 31 | `/agent/knowledge/v1/bases` | GET | KNOWLEDGE | — |  |
| 32 | `/agent/knowledge/v1/bases` | POST | KNOWLEDGE | — |  |
| 33 | `/agent/knowledge/v1/bases/permission-directory` | GET | KNOWLEDGE | — |  |
| 34 | `/agent/knowledge/v1/bases/{base_id}` | PATCH | KNOWLEDGE | — |  |
| 35 | `/agent/knowledge/v1/bases/{base_id}` | DELETE | KNOWLEDGE | — |  |
| 36 | `/agent/knowledge/v1/bases/{base_id}/ask` | POST | KNOWLEDGE | — |  |
| 37 | `/agent/knowledge/v1/bases/{base_id}/ask/cancel` | POST | KNOWLEDGE | — |  |
| 38 | `/agent/knowledge/v1/bases/{base_id}/ask/stream` | POST | KNOWLEDGE | — |  |
| 39 | `/agent/knowledge/v1/bases/{base_id}/chat-session` | POST | KNOWLEDGE | — |  |
| 40 | `/agent/knowledge/v1/bases/{base_id}/chat-session/new` | POST | KNOWLEDGE | — |  |
| 41 | `/agent/knowledge/v1/bases/{base_id}/documents` | GET | KNOWLEDGE | — |  |
| 42 | `/agent/knowledge/v1/bases/{base_id}/documents` | POST | KNOWLEDGE | — |  |
| 43 | `/agent/knowledge/v1/bases/{base_id}/folders` | GET | KNOWLEDGE | — |  |
| 44 | `/agent/knowledge/v1/bases/{base_id}/folders` | POST | KNOWLEDGE | — |  |
| 45 | `/agent/knowledge/v1/bases/{base_id}/folders/ensure` | POST | KNOWLEDGE | — |  |
| 46 | `/agent/knowledge/v1/bases/{base_id}/permission-directory` | GET | KNOWLEDGE | — |  |
| 47 | `/agent/knowledge/v1/bases/{base_id}/permissions` | GET | KNOWLEDGE | — |  |
| 48 | `/agent/knowledge/v1/bases/{base_id}/permissions` | PUT | KNOWLEDGE | — |  |
| 49 | `/agent/knowledge/v1/capabilities` | GET | KNOWLEDGE | — |  |
| 50 | `/agent/knowledge/v1/documents/{document_id}` | PATCH | KNOWLEDGE | — |  |
| 51 | `/agent/knowledge/v1/documents/{document_id}` | DELETE | KNOWLEDGE | — |  |
| 52 | `/agent/knowledge/v1/documents/{document_id}/content` | GET | KNOWLEDGE | — |  |
| 53 | `/agent/knowledge/v1/documents/{document_id}/pause` | POST | KNOWLEDGE | — |  |
| 54 | `/agent/knowledge/v1/documents/{document_id}/resume` | POST | KNOWLEDGE | — |  |
| 55 | `/agent/knowledge/v1/documents/{document_id}/retry` | POST | KNOWLEDGE | — |  |
| 56 | `/agent/knowledge/v1/folders/{folder_id}` | PATCH | KNOWLEDGE | — |  |
| 57 | `/agent/knowledge/v1/folders/{folder_id}` | DELETE | KNOWLEDGE | — |  |
| 58 | `/agent/knowledge/v1/operations` | GET | KNOWLEDGE | — |  |
| 59 | `/agent/knowledge/v1/sessions/{session_id}/knowledge-scope` | GET | KNOWLEDGE | — |  |
| 60 | `/agent/knowledge/v1/sessions/{session_id}/knowledge-scope` | PUT | KNOWLEDGE | — |  |
| 61 | `/agent/knowledge/v1/status` | GET | KNOWLEDGE | — |  |
| 62 | `/agent/add` | POST | JWT | — |  |
| 63 | `/agent/auth/admin/users` | GET | JWT | — |  |
| 64 | `/agent/auth/admin/users/{username}/reset-password` | POST | JWT | — |  |
| 65 | `/agent/auth/config` | GET | JWT | — |  |
| 66 | `/agent/auth/logout` | POST | JWT | — |  |
| 67 | `/agent/auth/profile` | GET | JWT | — |  |
| 68 | `/agent/auth/profile` | PUT | JWT | — |  |
| 69 | `/agent/auth/reset-password` | POST | JWT | — |  |
| 70 | `/agent/chat/cancel` | POST | JWT | — |  |
| 71 | `/agent/chat/resume` | POST | JWT | /agent/chat/resume | 限流 30/60s |
| 72 | `/agent/chat/stream` | POST | JWT | /agent/chat/stream | 限流 30/60s（前缀匹配 stream/status、stream/live 也被覆盖） |
| 73 | `/agent/chat/stream/live` | GET | JWT | /agent/chat/stream |  |
| 74 | `/agent/chat/stream/status` | GET | JWT | /agent/chat/stream |  |
| 75 | `/agent/connectors` | GET | JWT | — |  |
| 76 | `/agent/connectors/config` | PUT | JWT | — |  |
| 77 | `/agent/connectors/install` | POST | JWT | — |  |
| 78 | `/agent/connectors/mcp` | POST | JWT | — |  |
| 79 | `/agent/connectors/mcp/raw` | GET | JWT | — |  |
| 80 | `/agent/connectors/mcp/raw` | PUT | JWT | — |  |
| 81 | `/agent/connectors/mcp/{name}` | DELETE | JWT | — |  |
| 82 | `/agent/connectors/session` | GET | JWT | — |  |
| 83 | `/agent/connectors/session` | POST | JWT | — |  |
| 84 | `/agent/connectors/status` | GET | JWT | — |  |
| 85 | `/agent/connectors/toggle` | POST | JWT | — |  |
| 86 | `/agent/connectors/tools` | GET | JWT | — |  |
| 87 | `/agent/connectors/uninstall` | POST | JWT | — |  |
| 88 | `/agent/del` | POST | JWT | — |  |
| 89 | `/agent/files/download/{file_path:path}` | GET | JWT | — |  |
| 90 | `/agent/files/list` | GET | JWT | — |  |
| 91 | `/agent/files/parse` | POST | JWT | — |  |
| 92 | `/agent/files/session/{session_id}` | GET | JWT | — |  |
| 93 | `/agent/files/upload` | POST | JWT | — |  |
| 94 | `/agent/files/workspace/tree` | GET | JWT | — |  |
| 95 | `/agent/files/{file_id}` | DELETE | JWT | — |  |
| 96 | `/agent/list` | GET | JWT | — |  |
| 97 | `/agent/scheduled-tasks` | GET | JWT | — |  |
| 98 | `/agent/scheduled-tasks/{task_id}` | DELETE | JWT | — |  |
| 99 | `/agent/scheduled-tasks/{task_id}/run` | POST | JWT | — |  |
| 100 | `/agent/scheduled-tasks/{task_id}/runs` | GET | JWT | — |  |
| 101 | `/agent/scheduled-tasks/{task_id}/toggle` | PATCH | JWT | — |  |
| 102 | `/agent/scheduled-tasks/{task_id}/workspace` | GET | JWT | — |  |
| 103 | `/agent/sessions` | POST | JWT | — |  |
| 104 | `/agent/sessions` | GET | JWT | — |  |
| 105 | `/agent/sessions/count` | GET | JWT | — |  |
| 106 | `/agent/sessions/{session_id}` | GET | JWT | — |  |
| 107 | `/agent/sessions/{session_id}` | DELETE | JWT | — |  |
| 108 | `/agent/sessions/{session_id}/history` | GET | JWT | — |  |
| 109 | `/agent/sessions/{session_id}/messages` | POST | JWT | — |  |
| 110 | `/agent/sessions/{session_id}/pin` | PUT | JWT | — |  |
| 111 | `/agent/sessions/{session_id}/title` | PUT | JWT | — |  |
| 112 | `/agent/sessions/{session_id}/upload` | POST | JWT | — |  |
| 113 | `/agent/settings/memory` | GET | JWT | — |  |
| 114 | `/agent/settings/memory` | PUT | JWT | — |  |
| 115 | `/agent/settings/skills` | GET | JWT | — |  |
| 116 | `/agent/settings/system-prompt` | GET | JWT | — |  |
| 117 | `/agent/skill-center/add-skill` | POST | JWT | — |  |
| 118 | `/agent/skill-center/assign-category` | POST | JWT | — |  |
| 119 | `/agent/skill-center/categories` | GET | JWT | — |  |
| 120 | `/agent/skill-center/create-category` | POST | JWT | — |  |
| 121 | `/agent/skill-center/delete-category` | POST | JWT | — |  |
| 122 | `/agent/skill-center/disable-skill` | POST | JWT | — |  |
| 123 | `/agent/skill-center/download-skill/{dir_name}` | GET | JWT | — |  |
| 124 | `/agent/skill-center/enable-skill` | POST | JWT | — |  |
| 125 | `/agent/skill-center/import-skill` | POST | JWT | — |  |
| 126 | `/agent/skill-center/public-skills` | GET | JWT | — |  |
| 127 | `/agent/skill-center/remove-skill` | POST | JWT | — |  |
| 128 | `/agent/skill-center/rename-category` | POST | JWT | — |  |
| 129 | `/agent/skill-center/reorder-categories` | POST | JWT | — |  |
| 130 | `/agent/skill-center/skill-detail` | GET | JWT | — |  |
| 131 | `/agent/skill-center/user-skills` | GET | JWT | — |  |
| 132 | `/agent/stream_export_run` | POST | JWT | — |  |
| 133 | `/agent/update` | POST | JWT | — |  |
| 134 | `/api/personnel/import` | POST | JWT | — |  |
| 135 | `/api/personnel/import-template` | GET | JWT | — |  |
| 136 | `/api/personnel/users` | GET | JWT | — |  |
| 137 | `/api/personnel/users` | POST | JWT | — |  |
| 138 | `/api/personnel/users/{user_id}` | PUT | JWT | — |  |
| 139 | `/api/personnel/users/{user_id}` | DELETE | JWT | — |  |

> 限制：`auth dependency` 只识别 FastAPI 依赖注入层；`/agent/files/preview`、`/agent/scheduled-tasks/{task_id}/workspace/file` 属“手工在 handler 内校验 token”，需结合代码/实测（见 §6.1）。

### 3.2 鉴权冒烟矩阵（真实 auth 依赖，TestClient，未使用 pytest override）

| 请求 | 结果 |
|---|---|
| login admin (correct password) | 200 |
| profile: no auth | 401 |
| profile: X-Username: admin (no token) | 401 |
| profile: X-Username: default (no token) | 401 |
| profile: forged garbage bearer | 401 |
| profile: valid-shape JWT signed with WRONG secret | 401 |
| profile: JWT signed with empty secret | 401 |
| profile: EXPIRED token signed with real secret (exp 2h ago) | 200 |
| profile: valid signature but token_version+1 | 401 |
| profile: valid signature but missing v | 401 |
| profile: real valid token | 200 |
| public GET /api/health | 200 |
| public GET /agent/live | 200 |
| public GET /agent/config | 200 |
| public GET /api/config | 200 |
| public GET /terminal | 200 |
| public GET / | 200 |
| preview: no token (admin fallback?) | 200（**返回 admin 工作区标记文件**） |
| preview: garbage token= (fallback?) | 200（**返回 admin 工作区标记文件**） |
| preview: traversal ../../.env | 404 |
| preview: absolute /etc/passwd | 400 |
| preview: download=1 no token | 200（**返回 admin 工作区标记文件**） |
| POST /agent/chat/stream no auth | 401 |
| POST /agent/sessions no auth | 401 |
| public-ish file endpoint GET /agent/files/download/{file_path:path} | 401 |
| public-ish file endpoint GET /agent/files/preview | 422 |
| public-ish file endpoint GET /agent/skill-center/download-skill/{dir_name} | 401 |

- 受保护 GET 路由批量扫描：**32/32 全部 401**（无 token 时无越权）。
- `X-Username: admin` / `X-Username: default` 无 token：均 401 → **CLAUDE.md 所述“X-Username → default 回退”已被移除，文档过时**。
- 有效签名但 `token_version+1` / 缺 `v`：401（单点登录版本校验有效）。
- **过期 2h 的 token（真签名、真 v）→ 200**：`/agent/auth/profile` 通过；原因见 §3.3。

### 3.3 JWT 过期语义（配置相关，已实测）

- `easy_agent/middleware/auth.py:150` 使用 `decode_access_token(token, verify_exp=False)` —— **不校验 exp**。
- 登录 token 的 `exp` 是否签发取决于 `idle_logout_minutes`：`0` → `never_expires=True`，token 载荷只有 `iat/sub/v`，**无 exp**。
- 仓库实际配置 `easy_agent/config/config.yaml:62`：`idle_logout_minutes: ${IDLE_LOGOUT_MINUTES:-0}` → 默认 **0（永不过期）**；运行探针实测 `idle_logout_minutes=0`、login token `token_has_exp=false`。
- 结果：默认部署下，**token 一旦泄漏即永久有效**（除非用户重新登录/登出使 `token_version` 变化）；即使配置了 idle 超时，只要未超过空闲窗口，已过期 token 仍被接受（滑动会话设计，但 `exp` 实际失效）。

## 4. SPA 兜底路径穿越（P0，实测）

代码：`easy_agent/app.py:644-666`：

```python
    static_file = os.path.join(frontend_dist, full_path)   # app.py:653
    if os.path.exists(static_file) and os.path.isfile(static_file):
        return FileResponse(static_file, headers={"Cache-Control": "no-cache"})
```

`frontend_dist = <repo>/frontend-vue2/dist`；`{full_path:path}` 会拿到 **URL 解码后的**路径，`%2f` 变成 `/` 后 `..` 生效；`os.path.join` 对绝对路径参数还会丢弃 base。TestClient（ASGI）与真实 uvicorn 均复现：

### 4.1 TestClient 结果（脱敏）

| 请求 | 状态 | Content-Type | 长度 | 命中敏感标记 | 备注 |
|---|---|---|---|---|---|
| `etc/passwd (6x .., ASCII)` | 200 | application/octet-stream | 1472 | etc_passwd | root:x:0:0:root:/root:/b |
| `etc/passwd (%2e%2e variant)` | 200 | application/octet-stream | 1472 | etc_passwd | root:x:0:0:root:/root:/b |
| `repo .env (2x ..)` | 200 | application/octet-stream | 1492 | deepseek_key | <masked-sensitive> |
| `repo .env.test (2x ..)` | 200 | application/octet-stream | 2263 | env_jwt,deepseek_key,minimax_key,environ_env | <masked-sensitive> |
| `repo .env.dev (2x ..)` | 200 | application/octet-stream | 2792 | env_jwt,deepseek_key,minimax_key,environ_env | <masked-sensitive> |
| `data/.jwt_secret (2x ..)` | 200 | application/octet-stream | 43 | — | <masked: JWT signing secret> |
| `data/easy_agent.db SQLite header (2x ..)` | 206 | application/octet-stream | 64 | sqlite_header | <masked: binary header> |
| `repo config.yaml (2x ..)` | 200 | text/html; charset=utf-8 | 395 | spa_index | <!doctype html> <html la |
| `easy_agent/config/config.yaml (2x ..)` | 200 | application/yaml | 13260 | deepseek_key | <masked-sensitive> |
| `start.prod.sh (2x ..)` | 200 | text/x-sh; charset=utf-8 | 4806 | — | #!/bin/bash # ========== |
| `etc/shadow (6x ..)` | EXC | — | — | — | PermissionError(13, 'Permission denied') |
| `proc/self/environ (6x ..)` | 416 | text/plain; charset=utf-8 | 0 | — |  |
| `absolute-path injection //etc/passwd` | 200 | text/html; charset=utf-8 | 395 | spa_index | <!doctype html> <html la |
| `encoded absolute /%2Fetc%2Fpasswd` | 200 | application/octet-stream | 1472 | etc_passwd | root:x:0:0:root:/root:/b |
| `7x .. /etc/passwd` | 200 | application/octet-stream | 1472 | etc_passwd | root:x:0:0:root:/root:/b |
| `double-encoded %252e%252e .env` | 200 | application/octet-stream | 1492 | deepseek_key | <masked-sensitive> |

关键行：

- `GET /..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd` → **200**，1472B，命中 `root:x:0:0`（应用以 uid=1000 运行，/etc/passwd 可读）。
- `GET /..%2f..%2f.env` → **200**，1492B，命中 `DEEPSEEK…` 密钥标记。
- `GET /..%2f..%2f.env.test` / `.env.dev` → 200，命中 `EASY_JWT_SECRET=`、模型 key 标记。
- `GET /..%2f..%2fdata%2f.jwt_secret` → **200，43B（JWT 签名密钥原文，报告不粘贴内容）**。
- `GET /..%2f..%2fdata%2feasy_agent.db` + `Range: bytes=0-63` → **206**，`Content-Range: bytes 0-63/2605056`，命中 `SQLite format 3` → 整个业务库可被未认证下载。
- `GET /..%2f..%2feasy_agent%2fconfig%2fconfig.yaml` → **200**，13260B，`application/yaml`，命中密钥标记。
- `GET /..%2f..%2fstart.prod.sh` → 200（脚本内容可读）。
- `GET /%2Fetc%2Fpasswd`（编码后的绝对路径）→ 200 /etc/passwd（`os.path.join` 绝对路径覆盖 base）。
- `GET /%252e%252e%2f%252e%252e%2f.env`（二次编码）→ 200 读 `.env`。
- `//etc/passwd` 被 httpx 归一化为 `/passwd`，未命中（真实浏览器/curl 行为可能不同，未验证）。

### 4.2 真实 uvicorn 复核（127.0.0.1:18099，单 worker）

| 请求 | 状态 | 长度 | 结果 |
|---|---|---|---|
| `/..%2f..%2f.env` | 200 | 1492 | deepseek_key <masked> |
| `/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd` | 200 | 1472 | etc_passwd root:x:0:0:root:/root:/bin/bash  |
| `/..%2f..%2fdata%2f.jwt_secret` | 200 | 43 | — <masked> |
| `/agent/config` | 200 | 318 | — {"system_prompt":"\n你是 Easy  |
| `/terminal` | 200 | 7555 | — <!DOCTYPE html> <html lang="zh-C |
| `/api/health` | 200 | 131 | — {"status":"live","agent_initiali |
| `/api/auth/login` | 404 | 22 | — {"detail":"Not Found"} |

- 结论：**未认证任意文件读取在真实 HTTP 服务上成立**；`data/.jwt_secret` 泄漏意味着攻击者可离线签发合法 JWT（`EASY_JWT_SECRET` 未设置时服务用的就是该文件密钥，见 `utils/auth.py:22-54`），与 §5/§6.1 叠加可直接接管服务。

## 5. Web Terminal 无认证（P0，实测）

代码：`easy_agent/api/terminal.py:225-232`（`/terminal` 页面与 `/agent/terminal/ws` 均无鉴权）；`terminal.py:263-283` fork+pty 后 `execvpe("bash", ["bash", "--login"])`。

实测（`04_runner.sh`，只执行 `pwd`/`id`/`echo`，进程已回收）：

- `GET /terminal` 无鉴权：status=200, content_type=text/html; charset=utf-8, contains_xterm=True, contains_ws_target=True
- `ws://…/agent/terminal/ws` 无 token 连接：**connected=True**
- 建立连接后收到 PTY 字节：len=5，内容='pwd\n'
- 无认证执行 `id`：uid_seen=**True**；输出（去 ANSI）：`id
bash: warning: setlocale: LC_ALL: cannot change locale (en_US.UTF-8)
]0;sututu@DESKTOP-B7CINV9: ~sututu@DESKTOP-B7CINV9:~$ pwd
/home/sututu
]0;sututu@DESKT`
- 无认证执行 `echo T5WS$((6*7))`：marker_seen=**True**；输出 `]0;sututu@DESKTOP-B7CINV9: ~sututu@DESKTOP-B7CINV9:~$ echo T5WS$((6*7))
T5WS42
]0;sututu@DESKTOP-B7CINV9: ~sututu@DESKTOP-B7CINV9:~$ `

输出可见：`uid=1000(sututu) gid=1000(sututu) groups=1000(sututu),…,27(sudo),…,1001(docker)`，cwd=`/home/sututu`。**未认证任意命令执行（宿主机用户权限；本机用户还在 sudo/docker 组）。**

另：`GET /terminal` 返回 200 + xterm 页面（无鉴权），页面 JS 直连 `/agent/terminal/ws`。

## 6. 其他无鉴权公共面（实测）

### 6.1 `/agent/files/preview` 回退 admin（P1）

代码 `easy_agent/api/files.py:343-354`：先试 Authorization，再试 `?token=`；**两者都失败时 `username = db.get_or_create_default_user().username`**（= admin），然后在该用户工作区解析文件。

| 请求（无鉴权） | 结果 |
|---|---|
| preview: no token (admin fallback?) | 200（**返回 admin 工作区标记文件内容**） |
| preview: garbage token= (fallback?) | 200（**返回 admin 工作区标记文件内容**） |
| preview: traversal ../../.env | 404 |
| preview: absolute /etc/passwd | 400 |
| preview: download=1 no token | 200（**返回 admin 工作区标记文件内容**） |

- 路径穿越已被 `resolve_workspace_file`（files.py:117-125）拦截：`../../.env` → 404 / `/etc/passwd` → 400。**但“默认 admin 身份”本身即越权**。
- 对比：`/agent/scheduled-tasks/{task_id}/workspace/file` 同样是手工校验，但无 token 时 401（正确）。

### 6.2 未鉴权写提示词 / 未鉴权 LLM 透传（P1）

| 请求（无鉴权） | 状态 | 结果 |
|---|---|---|
| `prompts/query no auth` | 200 | {"data":["fx/demo.md"]} |
| `prompts/read no auth` | 200 | {"path":"fx/demo","content":"seed prompt\n"} |
| `prompts/update no auth: ABSOLUTE path injection` | 200 | "[/tmp/t5_prompts_abs_write_probe]写入完成" |
| `prompts/update no auth: ../ traversal` | 200 | "[../../../tmp/t5_prompts_trav_write_probe]写入完成" |
| `settings/models no auth` | 200 | {"models":[{"name":"deepseek","model":"deepseek-v4-flash","provider":"deepseek","protocol":"openai","context_length":200000,"is_active":true |
| `settings/web-search no auth` | 200 | {"enabled":true,"configured":false,"reason":"not_configured","provider":"tavily","default_time_range":"NoLimit","time_ranges":["NoLimit","On |
| `settings/upload-limits no auth` | 200 | {"enabled":true,"max_file_size_mb":10,"max_files_per_message":5,"max_total_size_mb":20} |
| `public-list no auth` | 200 | {"total":5,"tags":[],"dir":"/home/sututu/code/easy-agent/experts","items":[{"export_agent_id":"preset_rate_bond","session_id":"","tag_id":"b |
| `completion/chat no auth invalid body` | 422 | {"detail":[{"type":"missing","loc":["body","messages"],"msg":"Field required","input":{}}]} |
| `scheduled-tasks/xxx workspace file no token` | 401 | {"detail":"登录已过期或未登录，请重新登录"} |

- `prompts/update`：绝对路径注入成功（`/tmp/t5_prompts_abs_write_probe.md` 实际创建，已清理）；`../../../` 相对路径也被接受（写入位置按 join 解析）。代码 `easy_agent/api/prompts.py:52-68` 无任何鉴权/路径校验。
- `completion/chat`：无鉴权、无限流；空 body 只触发 422，说明请求会进入 handler——有效 body 将直接调用大模型（未实际调用，避免联网/计费）。
- `settings/*`、`public-list`：配置/路径信息泄漏（`public-list` 泄漏 `/home/sututu/code/easy-agent/experts`）。

## 7. 沙箱行为验证（只读/无害命令）

### 7.1 环境与可用性

| 项 | 值 |
|---|---|
| kernel | `5.15.167.4-microsoft-standard-WSL2` |
| bwrap | `/usr/bin/bwrap` (bubblewrap 0.9.0)，**usable=True**（探测 0.01s） |
| Landlock | usable=True（ABI v1） |
| venv | `/home/sututu/code/easy-agent/.venv` |
| system python | `/usr/bin/python3` |

> 本机 bwrap 可用，因此 `backends/shell.py` 实际走 **bwrap** 模式；Landlock 是降级路径，下面两条路径都实测了。

### 7.2 输入拦截矩阵

| 输入 | `sandbox.check_command_paths`（仅测试使用的死代码） | Landlock 实测 | bwrap 实测 |
|---|---|---|---|
| `cat /etc/passwd` | ALLOW（/etc 在 safe 前缀） | 可读 | 可读（但映射为中性 passwd） |
| `cat /etc/shadow` | **ALLOW**（安全前缀包含 /etc） | Permission denied | Permission denied |
| `cat /home/<user>/.ssh/id_rsa` | BLOCK | Permission denied（/home 不可见） | 路径不存在（/home 未挂载） |
| `cat ../../etc/passwd`（相对路径） | **ALLOW**（正则只匹配绝对路径） | 工作区内相对路径解析，无法逃逸到 /etc（cwd=工作区） | 同左 |
| `C:\Windows\win.ini` | ALLOW（未识别 Windows 路径） | 作为普通文件名/不存在 | 同左 |
| `/workspace/../easy_agent/config/config.yaml` | BLOCK | 拒绝（真实路径不在允许根） | 路径不存在 |
| `/workspace/../../.env.dev` | BLOCK | 拒绝 | 不存在 |
| `/skills/../..` | BLOCK | — | — |
| `~/.ssh/id_rsa` | BLOCK（检测 `~/`） | — | — |
| `ls /proc/self/environ` | BLOCK | 不可见 | 不可见 |
| `cat /dev/null` | ALLOW | 允许（/dev/null 白名单） | 允许（tmpfs /dev） |

**`check_command_paths` 在 `easy_agent/**` 中没有任何调用点（仅 `tests/integration/test_sandbox.py` 使用）**——生产实际走 bwrap/Landlock/fail-closed，该“best-effort 路径白名单”是死代码，且其白名单策略本身会放过 `/etc/shadow`、相对 `../../` 与 Windows 路径，与文档承诺不符。

### 7.3 fs_safety 外部目录挂载策略

| 输入 | 允许? | 说明 |
|---|---|---|
| `workspace root itself` | False | 目标路径 /tmp/ea-audit/sandbox/wsroot 是工作区根目录 /tmp/ea-audit/sandbox/wsroot 本身或其祖先，挂载后将暴露所有用户的会话目录 |
| `other user's dir` | False | 目标路径 /tmp/ea-audit/sandbox/wsroot/bob 位于工作区根目录 /tmp/ea-audit/sandbox/wsroot 下但不在当前用户(alice)的子树内，挂载后将暴露其他用户数据 |
| `own user dir` | True |  |
| `/etc` | True |  |
| `symlink outside` | True |  |
| `ws root via ..` | False | 目标路径 /tmp/ea-audit/sandbox/wsroot 是工作区根目录 /tmp/ea-audit/sandbox/wsroot 本身或其祖先，挂载后将暴露所有用户的会话目录 |
| `relative traversal` | False | 目标路径 /tmp/ea-audit 是工作区根目录 /tmp/ea-audit/sandbox/wsroot 本身或其祖先，挂载后将暴露所有用户的会话目录 |

- 允许自己用户目录；拒绝工作区根/他人目录/`..` 回退；`/etc` 被允许；**符号链接 resolve 到工作区外时放行**（若 external_dirs 指向符号链接可绕过“工作区”语义，需管理员配置约束）。

### 7.4 Landlock 降级路径（实测强制隔离）

```
/tmp/ea-audit/sandbox/ws
hello-t5
---READ_REPO_ENV---
cat: /home/sututu/code/easy-agent/.env.dev: Permission denied
---READ_ETC_PASSWD---
root:x:0:0:root:/root:/bin/bash
---READ_ETC_SHADOW---
head: cannot open '/etc/shadow' for reading: Permission denied
---WRITE_TMP---
touch: cannot touch '/tmp/t5_landlock_escape.txt': Permission denied
write_tmp_rc=1
---SYMLINK_ESCAPE---
cat: escape_link: Permission denied
---ENV---
env_secret_lines=0
HOME=/tmp/ea-audit/sandbox/ws
TMPDIR=/tmp/ea-audit/sandbox/ws
PATH=/usr/local/bin:/usr/bin:/bin
---SYSTEM_PY---
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'openpyxl'

[stderr] sh: 1: cannot create /workspace_file_test: Permission denied
```

| 测试 | 结果 |
|---|---|
| 读宿主仓库 `.env.dev` | `Permission denied`（fail-closed） |
| 读 `/etc/passwd` | 可读（/etc 只读挂载，符合设计） |
| 读 `/etc/shadow` | Permission denied |
| 写 `/tmp/...` | Permission denied |
| 符号链接逃逸（工作区内 symlink → 宿主文件） | Permission denied |
| 环境变量 | 秘密行数=0；HOME/TMPDIR=工作区；PATH 无 venv |
| `python3 -c 'import openpyxl'` | **ModuleNotFoundError** → Landlock 模式用系统 python，技能脚本依赖的 venv 包不可用（功能性缺陷/降级代价） |
| timeout=1 执行 `sleep 10` | exit=124，输出 `Error: Command timed out after 1 seconds.` |
| 输出截断 max_output_bytes=200 | `truncated=true`，236B，附 `... Output truncated` |
| Landlock 不可用时（monkeypatch 探测）| exit=127，**命令未执行**（fail-closed 实测：目标文件未创建） |

另：日志中看到沙箱初始化告警——`已配置禁止沙箱内命令访问网络，但当前环境只能使用 Landlock —— Landlock 仅限制文件系统，无法隔离网络，命令仍可外联`（见 `/tmp/ea-audit/dup-init` 及仓库历史日志）。

### 7.5 bwrap 强制隔离（实测，无害命令）

```
/workspace
uid=1000(agent) gid=1000(agent) groups=1000(agent),65534(nogroup)
agent
---PASSWD---
root:x:0:0:root:/root:/bin/sh
agent:x:1000:1000:agent:/tmp:/bin/sh
---SHADOW---
head: cannot open '/etc/shadow' for reading: Permission denied
---HOME_LS---
ls: cannot access '/home': No such file or directory
---PROJECT_ENV---
cat: /home/sututu/code/easy-agent/.env.dev: No such file or directory
---WRITE_WS---
bwrap_ws_write
---WRITE_TMP---
write_tmp_rc=0
---ENV---
env_secret_lines=0
HOME=/tmp
PATH=/venv/bin:/usr/local/bin:/usr/bin:/bin
---PROC_ENV---
0

```

- `/home` 不可见、`/etc/shadow` 拒绝、宿主仓库路径不存在、环境变量秘密行数=0、`/proc/self/environ` 秘密行数=0；workspace 可写；`/tmp` 是沙箱内 tmpfs；`id` 映射为 `agent` 身份（/etc/passwd overlay 生效）。
- 超时（exit 124）与输出截断（`truncated=true`）在 `backends/shell.py` 的 bwrap 路径同样生效。
- `PathTranslatingShell` 实测模式 `bwrap`；`rm -rf` 目录删除被 HITL 规则拒绝。虚拟路径 `/workspace` 输出回译为虚拟路径。

### 7.6 沙箱结论

- bwrap/Landlock 的**文件系统隔离是真实有效的**（与 T3 静态结论互证），且不可用时 fail-closed。
- 但 Web Terminal（§5）完全绕过该沙箱：`/agent/terminal/ws` 直接 `execvpe` 宿主 bash，无 bwrap/Landlock、无鉴权。
- Landlock 降级模式的功能代价（系统 python、无 venv 包、网络仍可外联）未在前端/文档体现。

## 8. 并发/锁/性能实测

### 8.1 SQLite 配置与多进程写

- 连接（`db/database.py:199-247`）：单连接 `check_same_thread=False` + `threading.RLock` 串行化；无 WAL。
- PRAGMA 实测：`journal_mode=delete`，`busy_timeout=5000`（= sqlite3 默认 timeout）。

| 实验 | 结果 |
|---|---|
| 4 进程 × 50 次 `add_message` 同一 DB 文件 | 期望 200 行，实际 **200 行**，丢消息 0，重复 0，错误 **0**；单次写 max 延迟 **840ms**（avg 4.9–21.5ms） |
| 单实例 20 线程 × 20 次写 | 期望 400，实际 **400**，错误 0，耗时 1.09s（RLock 串行化正确） |
| 合成场景：连接 A 持写事务 7s，连接 B 写 | B 等待 **5.01s** 后 `OperationalError: database is locked`（busy_timeout=5s，无重试/无 WAL） |
| MySQL 配置不可达 | 0.02s 抛 `RuntimeError: MySQL 连接失败，已禁止降级到 SQLite`（无静默降级，符合多副本设计） |

> 解读：常规追加**不丢消息**（`session_messages` 行级 INSERT）；但多 worker/多进程竞争下写延迟抖动极大（尾延迟 0.5–0.84s），且任何超过 5s 的写事务会让其他写直接失败。

### 8.2 事件循环阻塞（async 端点同步调 DB）

| 指标 | 阻塞式（现状） | `asyncio.to_thread` 对照 |
|---|---|---|
| 50 次 `add_message` 总耗时 | 130.0ms | 136.3ms |
| 事件循环单次最大停顿 | **129.7ms** | 7.3ms |

含义：所有 async 端点在事件循环线程里同步访问 DB，一次 100ms 级 DB 操作会让**整个进程**的 SSE/健康检查/其他请求一起卡住。

### 8.3 写放大（O(n²) 实测）

| 消息规模 | 单次 append 耗时 | 单次 SQL 字符数 | `get_session()` 载入耗时 |
|---|---|---|---|
| 1000 条已存在消息 | 2.28ms | [296, 296, 296] | 2.2ms（载入 1010 条） |
| 5000 条已存在消息 | 2.92ms | [296, 296, 296] | 10.3ms（载入 5010 条） |

→ `add_message` 是 O(1) INSERT（**并非** JSON blob 全量重写）；但会话历史读取是 O(n)。

流式增量落库（`streaming.py:963-965` 每 +500 字符调用 `_persist_incremental` → `update_last_assistant_message` 整行重写 content+blocks，`db/database.py:1417-1460`）：

| 最终回复长度 | 增量写次数 | 最终行大小 | 累计写入 SQL 字符 | 放大倍数 | 总耗时 |
|---|---|---|---|---|---|
| 2000 字符 | 4 | 2000 B | **11412** | **5.7×** | 11.7ms |
| 10000 字符 | 20 | 10000 B | **217060** | **21.7×** | 63.0ms |
| 20000 字符 | 40 | 20000 B | **834120** | **41.7×** | 128.6ms |

→ **O(n²) 写放大成立**（20k 字回复写 834KB；n 翻倍放大近似翻倍）。这是性能审计的关键实锤，但位置是 **流式增量持久化**，不是每条 `add_message`。

### 8.4 客户端断开后的后台流（detached stream）

| 观测点 | 值 |
|---|---|
| 客户端取消时 state | `{"consumed": 7, "finished": false}` |
| 取消 1s 后 state | `{"consumed": 39, "finished": true}` |
| 后台继续跑完整流 | **True** |
| 残留 `_session_stream_hubs` | 1（TTL 后清理） |

→ `api/chat.py:256-305` 的设计是“客户端断开不取消后台任务”，实测确认生成器 40/40 全部消费完。**符合注释描述，但意味着用户关页/登出后 LLM token 与工具执行仍全额消耗**（/cancel 是唯一停止手段），多用户场景需评估成本）。

### 8.5 agent 缓存与锁

| 场景 | 结果 |
|---|---|
| 同一 session 20 个并发 `get_or_create_agent_for_session` | 实际创建 **1** 次（asyncio.Lock 生效） |
| 10 个不同 session 并发 | 创建 10 次（预期） |
| 200 个不同 session 之后 `_session_create_locks` 大小 | **200**（只增不删 → 无界增长） |
| `_AGENT_CACHE_MAX` | 100（agent 本体有 LRU 上限，锁字典没有） |

→ 单进程内 get-or-create 无竞态；但 **asyncio.Lock 只在进程内有效**，uvicorn `--workers N` 下同一 session 会被每个 worker 各建一个 Agent（LLM 成本/checkpoint 隔离风险），且锁字典随 session 数无界增长。

### 8.6 分布式锁（`utils/distributed_lock.py`）

| 语义 | 实测 |
|---|---|
| A 首次 acquire | `True` |
| 持锁时 B acquire | `False`（互斥） |
| 同实例重复 acquire | `False`（不可重入） |
| 释放后 B 再次 acquire | `True` |
| TTL 过期后 A 抢占 | `True` |
| 4 进程同时抢锁 | 赢家 **1**（=1，符合预期） |
| 关闭开关 | `acquired=True, enabled=False`（空操作） |

→ 实现语义正确；`config.yaml:168` 默认 `DISTRIBUTED_LOCK_ENABLED=false` → 多 pod 部署若忘开，定时任务会重复执行（T2 关注点）。

### 8.7 流式双实现重复度（代码度量）

| 函数 | 起止行 | LOC | 归一化行数 |
|---|---|---|---|
| `chat_stream_generator` | 808 | 411 | 324 |
| `resume_stream_generator` | 1221 | 489 | 408 |

- 序列相似度（difflib.SequenceMatcher，去空行/注释后）：**0.347**，逐行匹配 **127** 行；`streaming.py` 共 1709 行。
- 结论：两份长实现确有大量重复结构（约 127 行逐行相同），但业务分支差异也真实存在；“抽公共层”需谨慎回归（T2/T4 负责给出重构方案）。

## 9. 静态/依赖检查

### 9.1 编译与依赖

| 检查 | 结果 |
|---|---|
| 全量语法编译（177 个 `easy_agent/**/*.py`，内存 compile，不写 `__pycache__`） | **0 errors**，51,283 LOC |
| `uv pip check --python .venv/bin/python` | `All installed packages are compatible`（102 包） |
| venv 内 pip | **不存在**（uv 管理）→ `python -m pip check` 不可用，已用 uv 等价检查 |
| `uv lock --check` | OK（Resolved 117 packages） |
| ruff | **项目未配置**（pyproject 无 `[tool.ruff]`）；系统 ruff 0.15.12 默认规则：**50 errors**（E402 22 / F401 14 / F841 11 / F541 3） |
| ruff `--select S`（flake8-bandit） | **187 findings**：S110 try-except-pass 43、S603 subprocess 27、S608 SQL 表达式 27、S101 assert 25、S607 部分路径调用 21、S314 xml 17、S108 临时文件 7、S311 非加密随机 6、S106 硬编码密码参数 3、S112 3、S301 pickle 1 等 |
| pyflakes / pytest-timeout / xdist | 未安装（按要求不安装） |

### 9.2 危险模式清单（AST 扫描，`easy_agent/**` 177 文件）

| 模式 | 数量 | 备注 / 代表性 file:line |
|---|---|---|
| 裸 `except:` | **0** | — |
| `except Exception: pass` 类（body 仅 pass） | **92** | 33 在 `easy_agent/skills/**` 内置技能脚本；59 在应用代码。例：`easy_agent/services/streaming.py:190` 附近、`easy_agent/api/expert.py:317/506/532`、`easy_agent/db/database.py:300/392/401`、`easy_agent/api/terminal.py:323/361` |
| `shell=True` | **0** | — |
| `os.system` | **0** | — |
| `eval(` / `exec(` | **0** | — |
| `pickle.loads` | **1** | `easy_agent/checkpoints.py:42`（从 DB `agent_checkpoints` 反序列化；需 DB 写权限才可利用，标 P2 供 T3 复核） |
| `yaml.load`（非 safe） | **0** | — |
| `print(` | **438** | 416 在 `easy_agent/skills/**` 脚本；22 在库代码：18 在 `knowledges/scripts/migrate_knowledge_to_shared.py`，4 在 `easy_agent/agent.py:945-999`（CLI 流式打印路径） |
| `# type: ignore` | **3** | `knowledges/runtime/catalog_sync.py:28,52`、`tools/expert_writer.py:332` |
| `TODO/FIXME/HACK/XXX` | **0** | — |

ruff 默认 50 条明细（前 5）：

```
easy_agent/services/streaming.py:1563:13: F841 Local variable `status` is assigned to but never used
easy_agent/tools/expert_writer.py:42:5: F401 `..db.expert_store.normalize_name_cn` imported but unused
easy_agent/tools/web_search.py:424:13: F541 f-string without any placeholders
... (共 50 条：E402 22 / F401 14 / F841 11 / F541 3，均非安全规则)
```

> 结论：无 `shell=True`/`os.system`/`eval`/`exec`/裸 except；但 `except-pass` 面较广、`assert` 在非测试代码出现 25 处（`-O` 运行时语义变化）、pickle 反序列化 1 处，需要 T3 结合可达性分级。

## 10. 启动耗时与初始化

### 10.1 冷启动分阶段（子进程实测，隔离配置）

| 变体 | import app | lifespan | 首个 /api/health | 首个 /openapi.json | 总到首个健康请求 | 关闭耗时 |
|---|---|---|---|---|---|---|
| `EASY_SKIP_LLM_PROBE=1`（test 环境） | 2195ms | **881ms** | 3.93ms | 390ms | **3358ms** | 422ms |
| 默认（非 prod，真打 LLM 探测；模型指向 127.0.0.1:1 不可达） | 2488ms | **12395ms** | 2.89ms | 283ms | **15206ms** | 312ms |
| `AGENT_ENV=prod`（+CORS 白名单） | 1942ms | 867ms | 3.56ms | 317ms | 3063ms | 347ms |

关键结论：

- 正常冷启动到可服务约 **3.0–3.4s**；`import easy_agent.app` 占 1.9–2.5s（含 logging/env/config），lifespan 约 0.87s。
- **`_check_llm_connection`（app.py:248）在非 prod 默认真打一次 LLM**：模型端点不可达时 lifespan 从 0.87s → **12.4s**（OpenAI 客户端重试/退避）。dev/test 每次重启都有一次真实模型调用。
- 首次 `/openapi.json` 同步生成 **283–390ms**，期间阻塞事件循环（可用缓存/预热消除）。
- `AGENT_ENV=prod` 未设置 `EASY_CORS_ALLOW_ORIGINS` 时 **import 直接 RuntimeError**（fail-fast，已实测，见 `initialization/environment.py:28-30`）。

启动日志阶段（skip 变体，probe 日志时间线）：

| 阶段 | 相对 lifespan 开始 |
|---|---|
| logging_ready | -1484ms |
| env_loaded | -1478ms |
| config_loaded | -1478ms |
| lifespan_begin | +0ms |
| ensure_directories | +1ms |
| knowledge_init | +6ms |
| db_init | +7ms |
| agent_config_init | +1760ms |
| scheduler_started | +1785ms |
| startup_done | +1790ms |
| lifespan_total | +1790ms |

> 注：另一份 TestClient 计时显示 lifespan≈0.87s，与日志时间线的 1.79s 存在差异（本机抖动/日志时间线含 skills/expert 扫描），两者都记录在此；以“到首个健康请求 3.0–3.4s”为准。

### 10.2 `startup_knowledge` 被调用两次的验证

- `app.py:245` 与 `app.py:347` 各调用一次；`knowledges/runtime/lifecycle.py:27-28` 有幂等守卫（`app.state.knowledge_config is not None` 则 return）。
- 实测（干净临时 cwd + 绝对日志路径）：**一次 lifespan 内“知识库配置加载成功”只出现 1 次**（`knowledge_load_ok_count=1`），两次调用未造成重复初始化。
- 但守卫状态挂在 `app.state` 且 shutdown 不清除：同一进程内**第二次 lifespan 时第一次调用直接 return，知识库初始化被跳过**（实测两个 lifespan 后 `startup_done=2`、knowledge init=1）→ 进程内重启/测试复用 app 时知识库模块不重启（状态泄漏）。

### 10.3 日志目录硬编码（与 §2.4 同源）

- `initialization/log.py:17` `LOG_DIR = "./logs"`；`setup_logging()` 在 import 期执行。
- 运行探针 `runtime_initialization.log_files` 返回相对路径 `logs/proc-v01-easy-agent.log`；配置里的 `log_dir`（temp）被忽略。
- 影响：从不同 cwd 启动会写不同位置；测试/pytest 从仓库根启动会污染生产 `logs/`；容器里若 WORKDIR 不同则日志散落。

## 11. 结论：哪些审计结论被证实 / 证伪

### 11.1 被运行证据证实

| # | 结论 | 证据 |
|---|---|---|
| 1 | SPA 兜底任意文件读（P0） | §4：TestClient + 真实 uvicorn 均 200，读 `.env`/`.env.*`/`data/.jwt_secret`/`easy_agent.db`/`config.yaml`/`/etc/passwd` |
| 2 | Web Terminal 无认证 RCE（P0） | §5：`GET /terminal` 200；WS 无 token 执行 `id` 得到 uid=1000(sututu)+sudo/docker 组 |
| 3 | 文件预览未认证回退 admin（P1） | §6.1：无 token / 伪造 token 均 200，返回 admin 工作区标记文件 |
| 4 | 提示词接口未认证任意写（P1） | §6.2：绝对路径注入创建 `/tmp/*.md`（已清理） |
| 5 | 限流失效 → 500 且服务可能无响应（P1） | §6.3 与 §4.2/实时复核：20 次后 500，随后新连接 ReadTimeout |
| 6 | JWT 过期不生效 / 默认永不过期（P1） | §3.3：过期 token 200；login token 无 exp |
| 7 | 测试基线不绿 + 默认 pytest 直接中断（P2） | §2：890/904；collection error Interrupted 0.78s |
| 8 | 测试/启动污染仓库 logs（P2） | §2.4：+2.4MB 日志；硬编码 LOG_DIR |
| 9 | SQLite 单连接串行 + 无 WAL + async 阻塞事件循环（P2） | §8.1/8.2：循环停顿 129.7ms；database is locked 5.01s；尾延迟 840ms |
| 10 | 流式增量落库 O(n²) 写放大（P2） | §8.3：20k 字 41.7×、834KB SQL |
| 11 | 客户端断开后台流继续跑（P2，设计如此但成本风险） | §8.4：40/40 消费完 |
| 12 | 冷启动 3.0–3.4s；LLM 探测使非 prod 启动 +12s（P3） | §10 |
| 13 | bwrap/Landlock 文件隔离有效，Landlock 模式 fail-closed（正面结论） | §7 |
| 14 | 分布式锁语义正确（正面结论） | §8.6 |
| 15 | MySQL 不再静默降级 SQLite（正面结论） | §8.1 |

### 11.2 被运行证据证伪 / 修正

| # | 原假设（来自文档或线索） | 运行结论 |
|---|---|---|
| 1 | CLAUDE.md：`get_current_username` 依次回退 `Authorization` → `X-Username` → `"default"` | **已移除**：X-Username/default 无 token 均 401（§3.2）。文档需更新。 |
| 2 | “JWT secret 每次启动随机，重启即失效”（CLAUDE.md） | 实际持久化到 `data/.jwt_secret`（`utils/auth.py:22-54`），重启后 token 仍有效；且该文件可被 §4 未认证读取。 |
| 3 | “每次 `add_message` 都 read-modify-write 全量 `sessions.messages` JSON blob（O(n²)）” | 当前代码消息体在 `session_messages` 行表，append 为 296 SQL 字符的 O(1) INSERT（1k/5k 行相同）；O(n²) 存在于**流式增量持久化**（§8.3）与 `get_session` O(n) 读取。 |
| 4 | “`startup_knowledge` 调用两次 = 重复初始化” | 幂等守卫使其只初始化 1 次；真正问题是跨 lifespan 的守卫状态泄漏（§10.2）。 |
| 5 | “`check_command_paths` 是 bwrap 不可用时的兜底路径白名单” | 生产代码无调用点（死代码）；实际兜底是 Landlock 或 fail-closed（§7.2）。 |
| 6 | “测试套件可作为回归基线” | 默认命令直接 collection 中断；13 error/10 failed 主要是测试漂移（§2）。 |

### 11.3 对 T3 的硬数据交付

- 路由矩阵：`JWT` 78 条 / `KNOWLEDGE` 33 条 / 真正的无鉴权 HTTP 27 条 + WS 1 条（§3.1 全表）。
- 无鉴权高危端点：`/terminal`、`/agent/terminal/ws`、`/{full_path:path}`、`/agent/files/preview`、`/agent/prompts/update|query|read`、`/agent/completion/chat`、`/agent/config`、`/api/config`、`/agent/settings/*`、`/agent/public-list`、`/openapi.json`、`/docs`。
- 限流仅覆盖 6 条路由（§3.1）；其余端点（包括 completion、terminal、prompts、files preview）**无限流**。
- 认证侧：X-Username 已移除；token_version 单点登录有效；但 exp 不校验、默认永不过期、密钥文件可读。

## 12. 不确定项 / 需 Lead 复核

1. **§4.2 限流失效后的“服务无响应”**：真实 uvicorn 上 2 次复现（2×500 后新连接 ReadTimeout ≥20s），但根因未定位（BaseHTTPMiddleware 异常逃逸 + 连接状态/事件循环）。建议 T3/Lead 用 `--workers 1` 独立脚本再复现并抓 `py-spy`/堆栈；本报告只陈述现象。
2. **LLM 探测的真实网络时延未测**：本环境禁止外网，用 `127.0.0.1:1` 不可达端点测得 12.4s（客户端重试/退避路径）。真实公网 RTT/失败模式（DNS 慢、代理）可能不同；但“非 prod 默认真打一次模型”已由代码+日志确认。
3. **生产配置差异**：仓库 `easy_agent/config/config.yaml` 默认 `IDLE_LOGOUT_MINUTES=0`；若部署环境设置了 `>0`，token 仍不校验 exp，但空闲超时会由 DB `last_activity` 触发 401。未拿到生产 env 值。
4. **多 worker / 多 pod**：所有实验单进程；`asyncio.Lock`、agent 缓存、限流计数均为进程内，多 worker 下行为（重复 Agent、限流放大）未运行验证。
5. **容器内 bwrap**：本机 bwrap 可用；日志显示历史上同一主机也曾落到 Landlock/fail-closed（Docker 默认 seccomp 下 bwrap 可能不可用）。生产容器是否启用 bwrap 未验证（Dockerfile/部署参数见 T4）。
6. **`/agent/completion/chat` 有效请求未实际调用**（避免外网/计费）；其“无鉴权即可消费 LLM”的风险由路由矩阵推断，未计入实际计费证据。
7. **外部依赖用例**（MySQL/PostgreSQL/OCR/soffice/RAGFlow/网络）：套件无 skip 标记，但相关集成未被本环境实际打通；`test_mcp_config` 的 1 个失败是 PATH 环境问题；其余外部依赖路径未验证。
8. **前端构建/静态产物**：未运行 `npm run build`（会改 `frontend-vue2/dist`，违反只读）；前端 API 调用与后端签名一致性未验证（T4）。
9. **`pickle.loads`（checkpoints.py:42）可利用性**：需能写 `agent_checkpoints` 表；当前未找到未认证写库路径，标“不确定/待 T3 评估”。
10. **`X-Username` 残留引用**：全仓仅 `api/chat.py:405` 注释提及，无实际读取；但测试/文档仍可能假设其存在（需 T4 统一清理文档）。

## 13. 证据文件与脚本清单

原始结果（`/tmp/ea-audit/`）：

| 文件 | 内容 |
|---|---|
| `junit.xml / pytest-summary.json / pytest-full.log` | pytest 全量结果与失败详情 |
| `static-audit.json` | AST 静态危险模式清单（file:line） |
| `app-probe-result.json` | 启动探针：路由/auth 矩阵、OpenAPI、穿越、中间件 |
| `traversal-result.json` | SPA 穿越读敏感文件（脱敏） |
| `live-http-result.json` | 真实 uvicorn 复核（穿越 + 限流） |
| `auth-smoke-result.json` | 鉴权矩阵、过期 token、preview 回退、限流码序列 |
| `ws-probe-result.json` | 无认证 WS 终端证据 |
| `sandbox-result.json` | bwrap/Landlock/fs_safety/PathTranslatingShell |
| `concurrency-result.json` | SQLite/agent_manager/分布式锁 |
| `perf-result.json` | 事件循环阻塞/写放大/detached stream/重复度 |
| `coldstart-result.json` | 冷启动分阶段 |
| `dup-init-result.json` | startup_knowledge 幂等/跨 lifespan 泄漏 |
| `public-risk-result.json` | prompts 未授权写/completion/settings |

脚本：`_analysis/t5-runtime-scripts/`（与 `/tmp/ea-audit/scripts/` 同步；均为只读脚本，运行时会自建 `/tmp/ea-audit/<case>/` 临时目录）。

---

*报告由 T5 生成；所有数字可用 §1 命令复现。默认敏感信息已脱敏。*
