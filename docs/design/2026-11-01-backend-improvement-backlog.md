# 后端剩余改进任务清单（供执行智能体领取）

- 日期：2026-11-01
- 依据代码：`6150ca1`（分支 `claude/unruffled-mcnulty-7daf87`）。后端代码 = 共享分支 `origin/refactor/audit-improvements-2026-10-18`（`c82f112`）+ 本分支的事件循环改造。
- 范围：后端 `easy_agent/`；安全项来自 `docs/audit/2026-10-17-easy-agent-audit.md`；前端只列一处脚本问题（T16）。前端结构没有纳入这次分析。
- 用法：一个任务对应一个分支和一组提交，不要把多个任务混在一起。动手前先读第 1 节「通用约定」。文中行号以 `6150ca1` 为准，改动前按函数名重新定位。
- 2026-11-02 更新：各任务的完成情况见第 2 节「状态」列；复核结论、T8 重做方案和剩余小问题见第 6 节。`rag-1018` 已合入共享分支，yongzheng 改为基于共享分支开发，切换说明见 §6.5。

## 0. 已经完成的（不要重复做）

- import 图 0 个强连通分量；分层方向（`scripts/quality/import_layers.json`，例外 1 条）与包入口（`init_allow` 14 个）都有 CI 门禁。
- 根包、`services`、`utils` 的 `__init__.py` 已瘦身；streaming 已拆成 orchestrator / projector / persistence / side_effects。
- 事件循环阻塞（`6150ca1`）：`knowledges/` 以外 async 代码里的同步查库已挪进线程。`scripts/quality/check_async_blocking.py` 按基线 `async_blocking_whitelist.json` 检查（剩 45 处，每个文件都写了原因），基线只减不增。
- 安全：SEC-01 / 02 / 04 / 05 已由 `9a98040` 修复。
- 全量测试基线：2248 passed / 5 skipped（按 `uv.lock` 构建的干净环境）。

## 1. 通用约定（所有任务都适用）

1. 先读 `CLAUDE.md`、`docs/architecture-current.md`；涉及 services 分层再读 `docs/design/2026-10-20-services-subpackages-and-layering.md`。
2. **分支**：从最新的 `origin/refactor/audit-improvements-2026-10-18` 拉分支。这是共享分支，同事也在推送：提交前 `git fetch origin`，确认没人已经做了同样的改动；改 `SCHEMA_VERSION` 之前先看 origin 上的值。
3. **工作区可能同时有其他会话**：只按路径 `git add <文件>`；不要 `git add -A`、`git commit -a`、`git stash`。
4. **测试环境**：仓库共用的 `.venv` 会被别的分支同步成其他依赖版本，用 `uv.lock` 建独立环境再跑：

   ```bash
   UV_PROJECT_ENVIRONMENT=/tmp/<你的目录>/venv uv sync --frozen --group dev --extra docs
   /tmp/<你的目录>/venv/bin/python -m pytest -q
   ```

5. **提交前必须全部通过**（与 CI 同口径）：

   ```bash
   uvx ruff check .
   python scripts/quality/import_graph.py --check-scc --check-layers --check-inits
   python scripts/skills/sync_skills.py --check
   python scripts/quality/check_double_write.py
   python scripts/quality/check_async_blocking.py --check
   python scripts/quality/check_config_schema.py --check
   python scripts/quality/check_env_usage.py --check
   python scripts/quality/check_multi_replica.py
   python scripts/docs/generate_api_docs.py --check
   pytest -q
   ```

   基线类门禁（`check_async_blocking`、`check_env_usage`、import 白名单）只许收缩：修掉的条目用 `--update` 删除；新增条目必须写原因。
6. **代码风格**：注释、日志用中文，跟周围代码保持一致。后端文件是 LF；`frontend/` 下 CRLF 与 LF 混用，改哪个文件就保持它原来的行尾（看 `git diff --stat` 有没有整文件改动）。
7. **数据库层是同步的**：
   - 只查库的路由直接写成 `def`；
   - async 路由里查库用 `asyncio.to_thread`；
   - 服务 / 工具里的写库用 `easy_agent.utils.blocking.run_blocking`，被取消时它会等线程里的写入做完。
   - 改进程内状态的代码留在事件循环上，包括 Agent 缓存、调度器，以及 `builtin-skills.json` 这类无锁 JSON 存储。
8. 纯重构不改 SSE 事件契约（`docs/sse-events.md`，契约测试 `tests/services/test_sse_contract.py`）。
9. **活跃分支**：改文件前看对方是否也在改同一个文件，必要时等合并或先沟通。

   | 分支 | 负责人 | 在改 |
   | --- | --- | --- |
   | 共享分支（`rag-1018` 已于 2026-11-02 合入，截到 `213d96b`，`30340c1` 的 `config.yaml` 不合） | yongzheng | 之后直接基于共享分支开发（切换说明见 §6.5）；`knowledges/` 仍是他的主要改动区，动这个目录前先和他沟通 |
   | `origin/vue26-1015`、`origin/feat/frontend-guocl`、`origin/zhq/dt-1016`（基于 10-17） | 郭春龙 | `api/chat.py`、`api/expert.py`、`api/sessions.py`、`services/agent_manager.py`、`services/expert_runtime.py` 等 |
   | `origin/feat/mcp`、`origin/audit-improvement`（基于 10-14 的旧快照） | lyhistudy | 几乎覆盖全部 api / services 文件 |

10. **交付物**：一个任务对应一组提交，提交说明写清动机、改动和验证结果。行为有变化（接口开始要求登录、返回码变化）的，在提交说明里单独列出。

## 2. 任务总览

| ID | 任务 | 优先级 | 规模 | 依赖 / 协调 | 状态（2026-11-02） |
| --- | --- | --- | --- | --- | --- |
| S1 | 安全：未认证读写与任意文件解析（SEC-08 / 09 / 07 / 06） | P0 | S–M | 加鉴权前先查前端和宿主的调用方 | ✅ 已完成 |
| S2 | 安全：JWT 过期、免密登录、默认口令等（SEC-03 / 10 / 12 及 P2 / P3） | P1 | M | 影响登录行为，需负责人先确认策略 | ⏳ 待负责人确认策略 |
| T1 | 吞错修复（审计写入、迁移 DDL）+ ruff `S110` | P1 | S | 无 | ✅ 已完成（与 T13 同一提交） |
| T2 | 文档事实刷新 | P1 | S | 无 | ✅ 已完成 |
| T3 | 数据访问层收敛：按领域 repository，删 `Database` 转发 | P1 | M–L | 分领域多次提交 | ✅ 已完成（分三批） |
| T4 | `chat_stream` / `chat_resume` 的准备逻辑下沉为服务 | P2 | M | 郭春龙的分支在改 `api/chat.py` | ✅ 已完成 |
| T5 | chat / resume 流主循环合并 | P2 | M | SSE 契约测试兜底 | ✅ 已完成 |
| T6 | `agent_manager` 会话状态收拢为 `SessionState` | P2 | S–M | 多个分支在改该文件 | ✅ 已完成 |
| T7 | 知识库域的事件循环阻塞（27 处） | P2 | S | 等 `rag-1018` 合并 | ✅ 已完成 |
| T8 | `knowledges/` 拆分与内部分层 | P2 | L | 与 yongzheng 约好窗口 | 🔁 已撤出，`rag-1018` 合并后重做（§6.3） |
| T9 | 其余超长函数按热度拆分 | P3 | M | 一个函数一组提交 | ✅ 已完成 |
| T10 | 函数内导入清理 | P3 | S–M | 注意导入成本测试 | ✅ 已完成 |
| T11 | 数据库层 / 服务层不再抛 `HTTPException` | P3 | S | 改测试断言 | ✅ 已完成 |
| T12 | 类型检查按目录接入 | P3 | M | 不改 `uv.lock` 的接入方式 | ✅ 已完成 |
| T13 | ruff `ASYNC` 规则开启 | P3 | S | 无 | ✅ 已完成 |
| T14 | 阻塞门禁基线里的「设计例外」逐个消解 | P3 | M | 每项单独评估 | ✅ 第 1–3 条已完成；第 4 条保持现状 |
| T15 | `services/` 领域子包（分层设计 §8） | P3 | M | 活跃分支合并后再做 | ✅ 已完成 |
| T16 | 前端运行期配置的值没有 JS 转义 | P3 | S | 无 | ✅ 已完成 |
| P\* | 既有规划：多副本 B2 / B4、共享限流计数、FW-11 | — | L | 需要单独设计决策 | — 未开始 |

## 3. 任务详情

### S1 安全：未认证读写与任意文件解析（P0）

**证据**：以下是对 `6150ca1` 的静态核对，动手前请按审计报告里的 PoC 实测一次。

| 编号 | 位置 | 现状 |
| --- | --- | --- |
| SEC-08 | `api/prompts.py`：`/agent/prompts/query`、`read`、`update` | 路由、router、app 三层都没有鉴权。`read_prompt` / `update_prompt` 用 `os.path.join(prompts_base, f"{path}.md")` 拼路径，没有 `resolve` 校验，**未登录即可读写 prompts 目录以外的 `.md` 文件** |
| SEC-09 | `api/completion.py`：`POST /agent/completion/chat` | 无鉴权、无限流，可调用任意已配置模型 |
| SEC-07 | `api/files.py:preview_file` | 没有有效 token 时退回 `get_or_create_default_user()` 继续处理，未登录可读默认用户的工作区 |
| SEC-06 | `api/files.py:parse_file` | 任意 `file_path` 直接交给 `parse_file_content`，登录用户可读到服务进程能读的任意文件 |

**改法**：

1. 先找调用方：`rg -n "agent/prompts|agent/completion|files/parse|files/preview" frontend-vue2/src frontend/src`，确认前端是否带 token、是否有 iframe 场景。
2. **prompts**：三个路由加 `Depends(get_current_username)` 和管理员校验（参照 `api/terminal.py` 的 `_ADMIN_USERNAME`，以及 `api/auth.py` 里管理员接口的判断方式）。`read` / `update` 先 `Path(prompts_base, f"{path}.md").resolve()`，再要求 `is_relative_to(Path(prompts_base).resolve())`，不满足返回 400；`update` 记日志。
3. **completion**：加 `Depends(get_current_username)`；在 `middleware/rate_limit.py` 的 `_DEFAULT_RULES` 加限流规则；限制输入长度和 `max_tokens`。
4. **preview**：没有有效 token 直接返回 401，删掉默认用户回退。`token` 查询参数保留给 iframe 场景，同样走 `verify_token_sso`。
5. **parse**：只允许解析 store URI，或「本用户在 `session_files` 里登记过的路径」，其余返回 403。可复用 `files.py` 里已有的 `resolve_workspace_file` / `_resolve_download_file`。

**验收**：每个 SEC 项补回归测试（未认证 401、越权 403、路径穿越 400）；审计报告里的 PoC 实测失败；前端对应页面手测可用。

### S2 安全：登录与凭据相关（P1，先出方案再改）

| 编号 | 位置 | 现状 | 方向（来自审计报告） |
| --- | --- | --- | --- |
| SEC-10 | `middleware/auth.py:113,159`、`api/auth.py:337` | `decode_access_token(..., verify_exp=False)`，过期时间不校验，只靠 `token_version` 和空闲登出兜底 | 校验 `exp` 并做续期，与前端 `frontend-vue2/src/packages/easyAgent/utils/request.js` 的续期钩子对齐 |
| SEC-03 | `api/auth.py:login_passwordless` | 用户名存在就签发 token（忽略 `user_id`），不存在就自动建号，只受登录策略开关控制 | 生产默认关闭；开启时要求外部系统的签名断言（HMAC + 时间戳 / nonce） |
| SEC-12 | `knowledges/personnel/repository.py:15`、`knowledges/personnel/models.py:128`、`api/auth.py:49`（`EASY_DEFAULT_RESET_PASSWORD` 的默认值） | 默认口令 `123456` | 随机初始口令，首次登录强制改密 |
| SEC-13 ~ 27 | 见审计报告 §3 | 状态未逐项核对；SEC-27 已部分修复（生产环境必须显式设置 `EASY_CORS_ALLOW_ORIGINS`） | 先逐项核对，更新状态表，再决定修复顺序 |

这些改动影响登录体验和内网宿主集成，必须先由负责人确认策略：先写方案文档，确认后再改代码。

### T1 吞错修复 + ruff `S110`（P1）

**问题**：

- `knowledges/chat/original_tool.py`：知识库原文访问的审计写入（`KnowledgeOperationsRepository(db).record_audit(...)`，约 272 行）外面是 `except Exception: pass`。原注释的本意只是「不向模型泄露细节」，结果服务端也完全不留痕。
- `db/schema.py` 的 MySQL 列加宽：`sessions.messages` 改为 LONGTEXT（约 112–118 行），`session_messages.content` / `extra_data` 改为 MEDIUMTEXT（约 155–170 行）。这几处吞掉所有失败：锁等待超时、权限不足时列没加宽也看不出来，要到之后写入长消息才报错。
- 全仓 `try/except/pass` 共 31 处（`uvx ruff check easy_agent --select S110`）；宽泛的 except（`Exception` / `BaseException` / 裸 `except`）共 361 处，其中 ruff `BLE001` 命中 155 处。

**改法**：

1. 审计写入：改成 `except Exception as exc: logger.warning("知识库原文访问审计写入失败 | action=%s | document=%s | %s", ...)`，仍然不把细节返回给模型。
2. 列加宽：失败时记 warning，写明表名、列名和异常；如果只想忽略可预期的情况，就按 MySQL 错误码判断。
3. 删冗余索引（`db/schema.py` 约 470 行）已有 `noqa: BLE001`，可以保留，建议补一条 debug 日志。
4. 31 处 `S110` 逐个判断：确实无害的写 `# noqa: S110 - 原因`，需要留痕的补日志；然后在 `pyproject.toml` 的 `[tool.ruff.lint] select` 加上 `S110`。`BLE001` 先不整体开启，可以用 `per-file-ignores` 逐个目录收紧。

**验收**：`uvx ruff check .`（含 `S110`）通过；为列加宽失败补一条测试（cursor 抛错时断言有 warning 日志）。

### T2 文档事实刷新（P1）

**`docs/architecture-current.md`**：

- §5：「当前基线：933+ passed」改为 2248 passed / 5 skipped（`6150ca1`）。
- §6 第 1 条：「剩余 1 个 16 模块强连通分量」，实际是 0 个（`import_graph.py --check-scc` 输出 0 SCC）。
- §6 第 4 条：「安全：4 个 P0 / 8 个 P1 尚未修复」，实际 SEC-01 / 02 / 04 / 05 已修（`9a98040`），其余按 S1 / S2 的核对结果更新。
- §6 补一条：async 阻塞基线还剩 45 处（`scripts/quality/async_blocking_whitelist.json`）。

**其他位置**：

- `README.md:479`：「当前 1607 passed / 1 skipped」，同 §5 更新。
- `services/agent_manager.py` 第 37–43 行的注释说连接器开关「不落库」，实际已经落在 `session_runtime` 表（`load_connectors` / `save_connectors`）。改注释，也可以并进 T6 一起做。

**验收**：`tests/test_docs_facts.py` 通过。

### T3 数据访问层收敛（P1）

**问题**：同一批 SQL 函数（`db/repositories/*.py`）有三条并行入口：

1. **`Database` 转发方法**：`db/database.py` 的 `Database` 有 121 个方法，`check_double_write` 统计其中 86 个是纯委托；75 个签名是 `(*args, **kwargs)`，IDE 和类型检查都看不到参数。
2. **按 API 划分的门面**：共 6 个，分别是 `db/repositories/` 下的 `chat_repository.py`、`expert_repository.py`、`file_repository.py`、`scheduled_task_repository.py`、`user_repository.py`，以及放在 `db/` 根下的 `db/session_repository.py`。它们的方法互相重复：

   | 方法 | 出现在几个门面里 |
   | --- | --- |
   | `get_session_files` | 4 |
   | `create_session`、`add_message`、`get_session`、`update_session_workspace_name` | 各 3 |
   | `get_owned_session_meta`、`get_user_by_username`、`get_or_create_default_user`、`renew_stream`、`update_session_meta`、`update_last_assistant_message` | 各 2 |

3. **服务层直接拿 `Database`**：`get_database()` 在 `db/` 以外有 26 处，分布在 13 个文件：

   | 目录 | 文件 |
   | --- | --- |
   | `services/` | `run_registry`、`agent_manager`、`skill_provision`、`session_uploads`、`scheduler` |
   | `persistence/` | `stores`、`distributed_lock`、`checkpoints` |
   | `tools/` | `session_file_reader`、`scheduled_task`、`expert_writer` |
   | 其他 | `middleware/rate_limit`、`knowledges/chat/original_tool` |

   另外，门面上的 `.database` 逃生口还被用了 24 处（10 个文件）。

**目标**：每个领域一个 repository（会话、消息、文件、用户、定时任务、专家、运行记录），方法签名写全；API 与服务只依赖领域 repository；`Database` 只负责连接、事务和迁移。

**改法**：一个领域一组提交，会话域先做。

1. 在 `db/repositories/` 下建（或扩充）领域类，签名照抄 `db/repositories/sessions.py` 等文件里的函数，不用 `(*args, **kwargs)`。`db/session_repository.py` 挪进 `db/repositories/`，旧路径留一行 re-export，或者直接改调用方。
2. 按 API 划分的门面改为组合领域 repository，或者直接删掉，让路由依赖领域 repository（FastAPI 依赖在 `api/deps.py`）。
3. 服务层的 `get_database().xxx(...)` 改用领域 repository。`.database` 只在流式引擎确实需要连接的地方保留，并列出清单。
4. 调用方迁完后删掉 `Database` 上对应的转发方法。在 `scripts/quality/check_double_write.py`（`double_write_allowlists.py`）里加两个只减不增的计数：纯委托方法数、`get_database()` 在 `db/` 以外的调用数。

**不要做**：不改 SQL、事务边界和表结构。A2 定下的「消息只写 `session_messages`」语义不能变，`check_double_write` 会拦。

**验收**：门禁全过；两个计数下降并被锁住；全量测试通过。

**协调**：`api/sessions.py`、`api/chat.py`、`services/agent_manager.py` 在郭春龙的分支上也有改动；按领域小步提交，合并冲突更小。

### T4 `chat_stream` / `chat_resume` 的准备逻辑下沉为服务（P2）

**问题**：`api/chat.py:chat_stream`（第 579 行起，284 行）在路由里串起了一整轮的准备工作：

- 知识库会话鉴权、技能解析、会话打开 / 新建、并发互斥；
- 专家分支：提示词、依赖技能补装、相关性预检与拒答流；
- 待修改专家绑定、知识库检索、上传文件预览、提示词拼装；
- 用户消息落库、作废待审批、建 Agent、登记 run。

这样只能通过 HTTP 级测试覆盖。`chat_resume`（第 913 行起，123 行）是同样的问题。

**改法**：

1. 新建 `services/chat_turn.py`（应用层 L4），提供 `async def prepare_turn(request, http_request, repo, username) -> TurnPlan | RefusalPlan`。
   - `TurnPlan` 是 dataclass，带上后面 `chat_stream_generator` 需要的字段：`session_id`、`message_id`、`workspace_name`、`agent`、`run_id`、`parsed_content`、`knowledge_chat` 等。
   - 拒答分支返回 `RefusalPlan`。
2. 路由只剩：参数校验 → `prepare_turn` → 按结果返回 `sse_response(...)`。`chat_resume` 同样抽出 `prepare_resume`。
3. 现有注释里写明的顺序约束必须保持：
   - 专家相关性预检早于文件解析和知识库检索；
   - `reject_concurrent_stream` 在用户消息落库之前；
   - `provision_expert_skills` 留在事件循环上（无锁 JSON 存储）；
   - 其余查库保持 `asyncio.to_thread`。

**验收**：`tests/api/test_chat*.py`、`tests/api/test_chat_turn_prep.py` 和 SSE 契约测试全过。新增不经 HTTP 的 `prepare_turn` 单元测试，覆盖专家拒答、并发 409、待修改专家越权三种情况。`check_async_blocking --check` 不新增命中。

### T5 chat / resume 流主循环合并（P2）

**问题**：`services/streaming/orchestrator.py` 里的 `chat_stream_generator`（240 行）和 `resume_stream_generator`（260 行），去掉空行和注释后分别是 192 行、207 行，其中 91 行完全相同（相似度 0.46）。相同部分集中在：

- 流配置；
- 构造 `IncrementalPersister` / `StreamProcessor`；
- 保存部分回复；
- `finalize_stream` 收尾（C2 已经把它抽成了函数）。

**改法**：抽出 `_run_stream(...)`（或一个小类），承载共同的主循环和异常 / 取消处理。两个生成器只负责构造各自的输入（新消息 vs `Command(resume=...)`）和不同的元数据。

**不要做**：不改事件顺序和字段（`docs/sse-events.md`）。不能丢掉取消时「先等线程里的写入完成，再保存部分回复」的语义，`tests/services/test_stream_offload.py` 会测这一点。

**验收**：SSE 契约测试、golden 字节比对、`tests/services/test_stream_offload.py`、resume 相关测试全过。

### T6 `agent_manager` 会话状态收拢为 `SessionState`（P2）

**问题**：`services/agent_manager.py` 用 8 个模块级字典按 `session_id` 存状态：`_session_agents`（LRU）、`_session_create_locks`、`_session_expert`、`_session_connectors`、`_session_stream_tasks`、`_session_edit_targets`、`_session_skill_scope`、`_session_turn_skills`。它们混着三类状态：

- 落库数据的缓存：连接器、待修改专家，存在 `session_runtime` 表；
- 只在进程内的状态：专家、技能作用域、本轮技能；
- 锁和任务。

清理路径有 `_evict_agent_cache`、`remove_session_agent`、`invalidate_user_agents`、`clear_session_connectors`、`_apply_session_connectors`，各自挑着清哪几个字典，靠注释解释例外。目前核对过没有泄漏 bug（LRU 清掉的连接器会从库里重新读回），但很难推理；多副本改造时也看不清哪些状态只在进程内。

**改法**：

1. 定义 `@dataclass class SessionState`，字段为 `agent`、`expert_key`、`connectors`、`edit_target`、`skill_scope`、`turn_skills`、`stream_task`、`create_lock`。每个字段注释写明生命周期：随 Agent / 随会话 / 落库缓存。
2. 用一个注册表 `_sessions: OrderedDict[str, SessionState]` 承担 LRU，提供 `drop(session_id, scope)`。scope 分三种：
   - 只丢 Agent：记忆更新、取消生成、定时任务结束；
   - 会话终结：删除会话；
   - 用户级失效：改了 mcp.json 或专家。
3. 保留现有的公开函数名（`remove_session_agent`、`invalidate_user_agents`、`get_session_connectors` 等），内部改用注册表。测试里直接替换模块字典的地方要一并改，例如 `tests/services/test_agent_manager_offload.py` 的 `_isolated_state`。
4. 顺手修正第 37–43 行过时的「不落库」注释。

**验收**：`tests/services/test_agent_manager*.py` 全过；新增测试覆盖三种 drop scope 各自清哪些字段。

### T7 知识库域的事件循环阻塞（P2，等 `rag-1018` 合并）

**范围**：用 `python scripts/quality/check_async_blocking.py --list | grep knowledges` 列出。

- `knowledges/api.py`（20 处）：
  - 优先改 `get_knowledge_principal`。它是每个知识库请求都要过的鉴权依赖，里面调用 `db.get_user_by_username`，可以直接改成 `def`。
  - 其余是路由里直接调用 `KnowledgeService` 的同步方法。这些方法还会请求 RAGFlow HTTP，阻塞时间更长。
- `knowledges/chat/chat_bridge.py`（2 处）：`authorize_scoped_chat` / `prepare_knowledge_chat` 里的 `repository.has_session_scope`，在 `/agent/chat/stream` 主路径上。
- `knowledges/chat/streaming.py`（5 处）：知识库面板聊天流里的落库，改用 `run_blocking`。
- `knowledges/scripts/*`（7 处）：命令行脚本，留在基线里。

**改法**：按第 1 节第 7 条。只查库、只调 RAGFlow 的路由改成 `def`，混合逻辑用 `asyncio.to_thread`；改完执行 `--update` 收缩基线，并删掉对应的 reasons。

**验收**：`check_async_blocking --check`、`tests/knowledges`、`tests/api/test_knowledge_routes.py` 通过。

### T8 `knowledges/` 拆分与内部分层（P2，先和 yongzheng 约好）

**问题**：`knowledges/` 共 1.31 万行。

- `service.py` 3015 行，`KnowledgeService` 有 70 个方法，覆盖知识库、文件夹、文档上传与状态、上游 RAGFlow 同步、检索与证据、会话范围、权限。
- `core/repository.py` 1119 行，`KnowledgeRepository` 有 60 个方法。
- `api.py` 1333 行。

分层模型把整个 `knowledges` 当作 L4 一层，内部依赖方向没有检查。

**改法**：

1. 先画依赖图：`python scripts/quality/import_graph.py --json`，过滤 `easy_agent.knowledges`。
2. 按子域把 `KnowledgeService` 拆成 `knowledges/services/{bases,documents,upstream_sync,retrieval,session_scope}.py`。`KnowledgeService` 暂时保留为组合这些子服务的门面，对外方法名不变，调用方逐步迁移。
3. `KnowledgeRepository` 按表分组，同样拆分。
4. 在 `import_layers.json` 里给 knowledges 加子前缀规则：ragflow 客户端 → core → services → runtime / chat → api。先登记现有违规，之后只减不增。这可能需要让 `import_graph.py` 支持同一层内的子层，动手前先读 `scripts/quality/import_graph_core.py`。

**协调**：yongzheng 正在这个目录密集开发（`rag-1018`）。纯移动的提交要短、尽快合并。

**验收**：行为不变；`tests/knowledges` 全过；新的层规则进 CI。

### T9 其余超长函数（P3）

**清单**：`6150ca1` 时 ≥120 行的函数（knowledges 里的见 T8）：

| 函数 | 行数 |
| --- | --- |
| `services/scheduler.py:_execute_task` | 242 |
| `services/agent_manager.py:_get_or_create_agent_unlocked` | 229 |
| `agent/core.py:_create_agent` / `__init__` | 219 / 164 |
| `agent/backend.py:_build_backend_for_context` | 192 |
| `services/skill_provision.py:provision_expert_skills` | 145 |
| `services/streaming/orchestrator.py:finalize_stream` | 144 |
| `services/stream_processor.py:_handle_messages` | 135 |
| `sandboxing/sandbox.py:run_sandboxed` / `run_landlocked` | 131 / 121 |
| `agent/workspace.py:_build_permissions` | 130 |
| `api/terminal.py:_run_shell_session` | 129 |
| `services/mcp.py:get_mcp_tools` | 128 |
| `services/memory_manager.py:update_long_term_memory_after_session` | 123 |
| `tools/expert_writer.py:_update` | 122 |

ruff 统计：C901 64 个、PLR0912 30 个、PLR0915 24 个。

**改法**：一个函数一组提交，把「无副作用的计算」和「完整的一段步骤」抽成私有函数。先拆 `_execute_task`（拆成建档 / 执行 / 收尾三段），注意 `6150ca1` 引入的顺序约束：先写库建档，然后才登记 `running_tasks`、启动锁心跳（见函数里的注释）。

**验收**：对应模块的测试通过。可选：把 `C901` 加进 ruff，用 `per-file-ignores` 冻结现状。

### T10 函数内导入清理（P3）

**问题**：函数内 import 项目模块共 127 处，分布在 47 个文件。最多的是 `app.py`（18）、`services/agent_manager.py`（14）、`persistence/checkpoints.py`（9）、`services/streaming/side_effects.py`（6）。很多是当年为了绕开循环导入；现在 import 图已经 0 个环，分层也有门禁。

**改法**：逐个文件把不需要延迟的导入提到模块顶部。以下三类保留，并在行尾注释原因：

- 避免导入期加载重依赖（deepagents、langchain 模型、文档解析库）；
- 依赖启动顺序（lifespan 里才初始化的东西）；
- 测试依赖按模块属性做 monkeypatch。

**约束**：`tests/test_architecture_gate.py` 里的导入隔离用例必须保持通过：`import easy_agent.bootstrap.env` 不加载 agent / deepagents，`services.health` 不加载 `agent_manager`。`import_graph.py --check-layers` 不能新增违规。

### T11 数据库层 / 服务层不再抛 `HTTPException`（P3）

**证据**：

- `db/session_ownership.py:35,50`：会话不存在或越权时统一抛 `HTTPException(404)`。
- `services/expert_runtime.py:127,147`：抛 `HTTPException`（404 等）。

**改法**：在 `models`（L0）里定义领域异常 `NotFoundError` / `ConflictError`；在 `app.py` 注册 exception handler，映射成 404 / 409，响应体保持现状（`tests/api/test_error_contract.py`）。路由层不必再关心这些异常。

**注意**：直接调用路由函数的测试（如 `tests/integration/test_session_ownership.py`）断言的是 `HTTPException`。要么改成断言领域异常，要么改用 TestClient 断言状态码。

### T12 类型检查按目录接入（P3）

**现状**：CI 没有类型检查，只有 ruff 的致命规则和「未使用名字」规则。再加上 T3 里 `(*args, **kwargs)` 的转发，参数传错只能在运行期发现。

**改法**：用 `uvx` 跑 pyright（basic 模式）或 mypy，这样不改 `uv.lock`。先只覆盖 `easy_agent/db/repositories/`、`easy_agent/utils/`、`easy_agent/services/streaming/`；配置写在 `pyproject.toml`；CI 的 backend-tests job 加一步。之后按目录逐步扩大。

**验收**：选定的目录 0 错误，并进入 CI。

### T13 ruff `ASYNC` 规则开启（P3）

**现状**：`uvx ruff check easy_agent --select ASYNC` 共 18 处。大多是 async 函数里的 `Path.exists()` 这类路径操作；此外还有：

- `api/terminal.py` 的 `os.waitpid`：在 SIGKILL 之后，实际很快；
- `services/session_uploads.py` 的阻塞 `open`；
- `knowledges/scripts` 里的 `input()`。

**改法**：能放进线程的放进线程；确实无害的写 `# noqa: ASYNC240 - 原因`；最后把 `ASYNC` 加进 `[tool.ruff.lint] select`。

### T14 阻塞门禁基线里的设计例外（P3）

清单见 `scripts/quality/async_blocking_whitelist.json` 的 `reasons`。

1. **`reconcile_scheduled_tasks`**（`services/scheduler.py`；被 `_reconcile_loop`、`api/scheduled_tasks.py`、`tools/scheduled_task.py` 调用）
   - 问题：读库后立即对齐本进程调度器。
   - 改法：拆成「读启用任务」（线程）+「对齐」（事件循环）。对齐前按任务 ID 再复核 `stopping_tasks` 和最新启用状态，避免把刚停用的任务装回去。`_execute_task` 执行前也会再查一次库。
2. **`provision_expert_skills`**（由 `api/chat.py` 调用）
   - 问题：对 `builtin-skills.json` 做无锁的读改写。
   - 改法：给 `agent/builtin_skills.py` 的 `add` / `set_enabled` / `remove` 加按用户的文件锁（可参考 `services/memory_locks.py` 的跨进程锁），之后整段放进线程。
3. **`persistence/checkpoints.py` 的 legacy `PersistentMemorySaver._flush`**
   - 改法：在事件循环上做快照（复制 dict），写库放进线程。它只在回退模式下启用，优先级最低。
4. **保持现状即可**：`middleware/rate_limit.py:_allow` 的进程内计数器分支、`app.py` 的 lifespan 启动。

**验收**：对应条目从基线删除（`--update`）；新增并发测试，证明没有丢更新，也没有把停用的任务注册回去。

### T15 `services/` 领域子包（P3）

**依据**：`docs/design/2026-10-20-services-subpackages-and-layering.md` §8 / §10（已评审）。把 `services/` 根下 33 个业务模块收进 7 个子包（agents / streaming / sessions / mcp / memory / experts / skills），一个子包一个纯移动提交。

**前置**：近 10 天有 5 个远端分支在改 `services/agent_manager.py`、`expert_*`、`mcp`、`memory_manager` 等文件。先确认这些分支的合并计划，否则纯移动会造成大量冲突。

### T16 前端运行期配置的值没有 JS 转义（P3）

**问题**：`frontend/scripts/generate-runtime-config.sh` 把 `API_BASE_URL`、`APP_TITLE`、`APP_WELCOME_TITLE`、`AGENT_ENV` 原样拼进 JS 字符串字面量。值里有 `"`、`\` 或换行时，生成的文件不是合法 JS。现在脚本末尾的 `node --check` 会让构建失败，而不是静默产出坏文件。

**改法**：

1. 改用 node 生成，值从环境变量传入：`node -e 'process.stdout.write("window.__RUNTIME_CONFIG__ = " + JSON.stringify(cfg, null, 2) + ";\n")'`。
2. 同步修改 `frontend/scripts/print-serve-env.mjs`：它用正则 `KEY:\s*"([^"]*)"` 解析这个文件，换成 JSON 写法后键会带引号，要同步改解析（去掉前后缀后 `JSON.parse` 即可）。
3. 脚本本身是 LF、`.env.*` 是 CRLF，保持不变。

**验收**：`APP_TITLE='A "B" \ C' npm run build` 成功，`node --check dist/runtime-config.js` 通过；`npm run serve` 打印的启动信息正确。

### P\* 既有规划（需要单独做设计决策，本清单不展开）

- **多副本**：B2（SSE hub 外置）、B4（调度单领袖）、B3 剩余部分（共享限流计数）。见 `docs/design/2026-10-18-multi-replica-target.md` 与 `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 B。进程内状态清单：
  - `services/agent_manager.py` 的会话字典；
  - `services/stream_hub.py:_session_stream_hubs`；
  - `services/run_registry.py`；
  - `services/scheduler.py` 的 `running_tasks` 等；
  - `api/auth.py` 的 `_login_time_cache` / `_active_login_ip`；
  - `services/mcp.py` 的工具缓存。
- **FW-11**：SQL Store 没有向量索引，`query` 语义受限（架构改进计划「第三批」里标了 ⚠）。

## 4. 建议顺序

1. S1（P0 安全）；T1、T2（一天内能做完）。
2. T3，会话域先做。
3. `rag-1018` 合并后马上做 T7。
4. T4，然后 T5、T6。
5. T9 – T13 穿插进行。
6. T8、T15：相关分支合并后，与同事约好窗口再做。
7. S2：等负责人确认策略。

## 5. 附：复核本文数字的命令

```bash
python scripts/quality/check_async_blocking.py --list        # async 阻塞命中（基线 45 处）
python scripts/quality/check_double_write.py                 # Database 方法数 / 纯委托数
uvx ruff check easy_agent --select C901,PLR0912,PLR0915 --statistics
uvx ruff check easy_agent --select BLE001,S110,ASYNC --statistics
grep -rn "get_database()" easy_agent --include=*.py | grep -v "^easy_agent/db/" | wc -l
grep -rnE "\b(repo|repository|db)\.database\b" easy_agent --include=*.py | grep -v "^easy_agent/db/" | wc -l
python scripts/quality/import_graph.py --json > /tmp/import_graph.json
```

## 6. 2026-11-02 复核结论与返工

### 6.1 分支现状

- 执行智能体在本地分支 `feat/backend-backlog-2026-11-01`（基于 `f589e04`）上完成了 S1 和 T1–T16，共 22 个提交；之后又追加了一个脱敏提交 `06be86d`。逐提交复核的结果是重构前后行为一致：SQL 字符串、OpenAPI、沙箱 argv 都和基线逐项相同，端到端冒烟（含 HITL 审批恢复）也一致。
- 问题出在 T7/T8：它们在 `rag-1018` 合并前就做了。这个分支和 `rag-1018` 合并时冲突区约 3,300 行，yongzheng 在 `service.py`、`api.py`、`core/repository.py` 上的改动需要手工搬进拆分后的文件。
- 处理方式（2026-11-02 定）：先合 `rag-1018`，再在它上面重做 T8。
- 已按这个方式重建分支 `feat/backend-backlog-2026-11-02`：基于 `rag-1018` 的 `213d96b`（不含 `30340c1` 提交的 `config.yaml`），去掉 T8 的两个提交（`739fe5d`、`de33a21`），其余提交（含 `06be86d`）顺序不变。重建时做了这些改动：
  1. **解决 4 处冲突**：
     - T16：`frontend/package.json`；
     - T1/T13：`source_sync.py`、`test_original_tool.py`；
     - T7/T14.3：`knowledges/api.py`、`chat_bridge.py`、`persistence/checkpoints.py`，其中 checkpoint 把 `rag-1018` 的体积上限和按序号落库合在一起；
     - T15：`chat_bridge.py` 的导入，`get_knowledge_principal` 仍从 `knowledges.api` 导入。
  2. **新增一个提交**，放在事件循环那个提交之前：`rag-1018` 新增的原文 `extract` 路径在事件循环上直接查库和写审计，不改的话阻塞门禁过不了。现在这些调用放进线程；`_principal` 改为接收 `db`，`get_database()` 的调用数不变。
  3. **挪动测试改动**：T15 里对 `tests/api/test_c1_remaining_domains.py` 期望值的改动挪进 T3c。原来 T3c、T9a、T9b 三个提交的测试不过。
  4. **合并 pyc 提交**：删除 pyc 的那个提交并进 T15，历史里不再出现 `.pyc`。
  5. **补回提交说明**：T10、T12 的提交说明里被 shell 吃掉的 `# 延迟导入：<原因>` 和 `uvx pyright` 已补回。
- 重建后的验证：
  - 代码提交逐个跑了 CI 同口径的门禁和全量测试。门禁都通过；前两个提交早于阻塞门禁脚本，没有这一项。
  - 测试只在 T3b 上失败过一次，是 `tests/middleware/test_rate_limit_shared.py` 的并发用例。单独重跑 8 次都通过，属于偶发失败，见 §6.4 第 10 条。
  - 末端 2374 passed / 5 skipped，pyright 0 errors。
  - 端到端冒烟（普通对话、HITL 审批恢复、21 个请求）和原分支一致。
- 旧分支保留到 T8 重做完成，重做时要参照它的 `739fe5d` 和 `de33a21`。

### 6.2 合入顺序

1. ✅ 已完成（2026-11-02）：共享分支快进到 `rag-1018` 的 `213d96b`。没有带上 `30340c1`，原因有两个：
   - `config.yaml` 在 `.gitignore` 里，一旦被跟踪，就会和每个人本地的同名文件冲突；
   - 带上它会让 `tests/integration/test_v3_streaming.py` 的 3 个用例失败。
2. ✅ 已完成（2026-11-02，`a8c411e`）：合入 `feat/backend-backlog-2026-11-02`。之后又合入了 `09cdaca`（AI 侧边栏知识库子面板对齐）和 `33864f8`（Vue 2 模块的知识库、人员接口统一走 `/agent`）。
3. ⏳ 待办：和 yongzheng 约好窗口后重做 T8（§6.3）。窗口期内，`knowledges/service.py`、`api.py`、`core/repository.py` 不要再有并行改动。

### 6.3 T8 重做要点

- 方案沿用原来的两个提交：
  - `739fe5d`：`KnowledgeService` 拆成 `knowledges/services/{_base,bases,documents,upstream_sync,retrieval,session_scope}.py`；
  - `de33a21`：`KnowledgeRepository` 拆成 `core/repositories/*`，`api.py` 拆成 `routes/*`，`KnowledgePrincipal` 和 `get_knowledge_principal` 下沉到 `core`。
- 相比原来拆分时的输入，`rag-1018` 改动不小，重跑拆分脚本时要处理：
  - `KnowledgeService`：新增 12 个方法，删除 5 个，改动 28 个。
    - 新增：`_delete_remote_documents`、`_error_response`、`_folder_summary`、`_is_folder_conflict`、`_match_upstream_document`、`_normalized_progress`、`_permission_directory_response`、`_permission_list_response`、`_require_document`、`_require_folder`、`_require_session`、`_stale_dataset_model`；
    - 删除：`_chunk_id`、`_chunk_text`、`_dataset_model`、`_dataset_needs_model_switch`、`_ordered_chunks`。
  - `KnowledgeRepository`：改动 1 个（`update_sync_item`）。
  - `api.py`：新增 `knowledge_http_error`（`chat_bridge.py` 在用）；T7 的 `_call(method, *args)` 写法要保留。
- 拆出来的文件按 T15 之后的路径导入，例如 `services.streaming.sse`；函数内导入沿用 T10 的 `# 延迟导入：<原因>` 注释。
- 验收：
  - 方法体除了跨子服务调用的改写，AST 与拆分前一致；
  - OpenAPI 与拆分前完全一致；
  - knowledges 子层规则写进 `import_layers.json`；
  - 每个提交的门禁和全量测试都通过。

### 6.4 还要改的小问题（追加新提交即可，不用改历史）

1. `scripts/quality/double_write_allowlists.py`：`MAX_DATABASE_ESCAPE_HATCH_REFS` 先写成 5，下一行又写成 12，实际生效的是 12。删掉 `= 12` 那一行。
2. `scripts/quality/check_double_write.py`：计数类违规的路径是 `easy_agent`，不在文件列表里，所以文本模式只打印「N violation(s)」，不说是哪一条。要把这类违规单独打印出来。
3. `pyproject.toml`：ruff 把整个 `skills/**` 都排除了，注释还把 canonical 和副本写反了。改成用 `per-file-ignores`，只对 `skills/**` 放开 `S110` 和 `ASYNC`。
4. `persistence/checkpoints.py`（T14.3）：线程写库期间一直持有 `_persist_lock`，事件循环上的 `_snapshot` 要等这把锁。实测 3 个并发 flush、每次写库 0.3 s 时，事件循环最长卡 596 ms。快照改用一把短锁，写库用另一把锁。
5. CI 里的 `uvx pyright` 锁定版本，当前是 1.1.414。
6. `scripts/skills/migrate_builtin_skills.py`：调用 `migrate_user_copies` 之前先取 `store_lock`。
7. `_ADMIN_USERNAME` 在 `api/prompts.py` 和 `api/terminal.py` 里各定义了一次，收成一处。
8. 文档：
   - `docs/README.md` 和 `docs/design/2026-10-20-services-subpackages-and-layering.md` 的头部还写着 §8 未实施；
   - T3 要求列出剩余 5 处 `.database` 的清单，目前还没有。
9. `services/agents/chat_turn.py:prepare_turn` 有 306 行，是现在最长的函数，可以作为下一轮拆分的对象。
10. `tests/middleware/test_rate_limit_shared.py::test_db_counter_is_exact_under_concurrent_threads`：用 60 秒固定窗口连发 20 次。恰好跨过整分钟边界时，两个窗口各放行 5 次，断言就会失败（实测放行 10 次）。机器负载高时更容易碰上。测试里应该固定时钟。

### 6.5 yongzheng 切到共享分支（2026-11-02）

`rag-1018` 已合入共享分支，之后直接基于 `origin/refactor/audit-improvements-2026-10-18` 开发。

**切换步骤**

1. 先把本地 `easy_agent/config/config.yaml` 复制到仓库外备份。这个文件在 `rag-1018` 上是被提交跟踪的（`30340c1`），在共享分支上没有被跟踪：切换时 git 会把它从工作区删掉；如果它有未提交的修改，git 会直接拒绝切换。
2. 从共享分支拉新分支：

   ```bash
   git fetch origin && git switch -c <新分支名> origin/refactor/audit-improvements-2026-10-18
   ```

3. 把备份的 `config.yaml` 放回原位。它在 `.gitignore` 里，之后不会再被误提交。研报同步的开关和路径建议写在 `.env.dev`：`KNOWLEDGE_SOURCE_SYNC_ENABLED`、`SOURCE_SYNC_FILE_ROOT`、`SOURCE_SYNC_STAGING_DIR`、`COL_SQL_TABLE`。`base_name`、`lookback_days`、`max_inflight` 在模板里是写死的，要改的话，在 `config-example.yaml` 里加环境变量占位，或者用 `EASY_CONFIG` 指向自己的配置文件。
4. `rag-1018` 上没进共享分支的只有 `30340c1`，不用再合。

**knowledges 相关代码的变化**

- `knowledges/api.py`：
  - `_call` 改成传「方法 + 参数」，例如 `_call(service.list_bases, principal, page=page)`，不再先构造协程；
  - `get_knowledge_principal` 改成了同步函数。

  新增的路由沿用这两种写法。
- 导入路径：`services/` 收进了子包（T15）。例如 `services.sse` 改为 `services.streaming.sse`，`services.agent_manager` 改为 `services.agents.manager`，`services.session_thread` 改为 `services.sessions.thread`，`services.mcp` 改为 `services.mcp.tools`。
- 数据访问（T3）：`Database` 上大部分转发方法已经删了，只保留 knowledges / personnel 还在用的 12 个。新代码不要再调用已删除的方法；`get_database()` 在 `db/` 之外的调用上限是 4 处，现在已经用满。
- `knowledges/chat/original_tool.py`：`_principal` 现在要传入 `db`；extract 路径的查库和审计写入已经放进线程执行。
- `persistence/checkpoints.py`：12MB 快照上限已经和 T14.3 的「按序号落库」合在一起。
- 前端：Vue 2 模块的知识库、人员接口已统一走 `/agent`（`33864f8`）。`edc55ab` 当时改的是搬家前的旧路径，前端部分没有合全。

**新增的 CI 门禁**（新代码也要过）

- async 函数里不能直接查库，要用 `asyncio.to_thread` 或 `run_blocking`（`scripts/quality/check_async_blocking.py`）。阻塞基线只许减少，不许增加。
- ruff 开启了 `S110`（`try/except/pass` 要么补日志，要么写 `noqa` 并注明原因）和 `ASYNC` 规则。
- `scripts/quality/check_double_write.py` 里的数据访问计数只许减少，不许增加。

**T8 重做**：拆分 `knowledges/service.py`、`api.py`、`core/repository.py` 前要先约好时间窗口；窗口期内这三个文件不要并行修改（§6.3）。
