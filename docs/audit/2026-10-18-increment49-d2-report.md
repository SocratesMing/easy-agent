# Increment 49：D2 编译图按「模型 + 工具集」复用（2026-10-18）

| 项 | 内容 |
| --- | --- |
| 目标 | 同一 model/tools/prompt 的多个 session 复用一张 `CompiledStateGraph`；会话差异走运行期 `SessionContext` |
| 状态 | ✅ 已实现（后端；前端未改；未 commit） |
| 设计 | `docs/design/2026-10-18-d2-graph-reuse.md` |
| 范围 | 新增 `easy_agent/agent_graph_cache.py`；改 `easy_agent/agent.py`（拆分/装配/调用段）、`easy_agent/services/agent_manager.py`（缓存失效钩子）、`easy_agent/services/streaming/orchestrator.py`（astream 传 context）；新增 3 个测试文件 |
| 明确不改 | `easy_agent/api/`、`app.py`、`checkpoints.py`、`mcp.py`、db、conftest、scripts、前端；HITL 策略；C1 API repository；未执行 Batch 0/1 |

## 1. 改动

| 文件 | 内容 |
| --- | --- |
| `easy_agent/agent_graph_cache.py`（新增，609 行） | `SessionContext`；`GraphKey` / `GraphCache`（进程内 LRU，默认 32，`EASY_AGENT_GRAPH_CACHE_SIZE` 可调）；指纹函数（model/tools/middleware/subagents/permissions/config/summarization）；`contextualize_tools()` + `resolve_tool_for_context()`（会话绑定工具的运行期注入）；命中/未命中/写入/驱逐/用户失效日志 |
| `easy_agent/agent.py` | 拆出 `_make_session_context()` / `_memory_sources()` / `_build_interrupt_config()` / `_graph_cache_block_reason()` / `_make_backend_factory()`；新增模块级 `_shared_graph_checkpointer()`、`_build_backend_for_context()`；`_create_agent()` 改为「算键 → 查缓存 → 未命中才编译」；`_build_backend()` 保留为兼容委托；`build_subagent_specs()` 不再写 session_id；`LoggingMiddleware()` 不再绑定 session_id；`run()` 的 astream 传 context |
| `easy_agent/services/agent_manager.py` | `init_agent_config()` 清空图缓存；`invalidate_user_agents()` 同步 `invalidate_user()`；per-session `_session_agents` 逻辑保留未动 |
| `easy_agent/services/streaming/orchestrator.py` | `pump_stream(..., context=None)`；`chat_stream_generator` / `resume_stream_generator` 传 `agent.session_context`；无 context 时保持旧调用形态 |
| `tests/agent/test_graph_cache.py`（新增） | 13 项：两 session 一次编译、不同 model/tools/skill/用户不命中、backend context 路由、无 context/跨用户 context 抛错、工具 context 注入、指纹忽略 session、LRU、会话头/legacy checkpointer/env 开关降级 |
| `tests/services/test_agent_manager_graph_cache.py`（新增） | 4 项：agent_manager 两 session 共享图、同 session 不重编译、专家提示词变化换图、`invalidate_user_agents` 清图 |
| `tests/services/test_stream_context_passing.py`（新增） | 2 项：`pump_stream` 有 context 透传、无 context 保持旧调用 |

## 2. 命中条件（真实实现）

缓存键 = `sha256` over：

`model`（provider/protocol/model/api_base/context_length/headers/retry）+
`tools`（name/description/class/args schema/config_model/tag_options/web_search 配置/knowledge 配置身份）+
`system_prompt`（终稿全文）+ `middleware`（类名/run_limit/exit_behavior）+
`hitl`（是否启用 + interrupt 工具名）+ `subagents`（name/description/prompt/skills/middleware）+
`skills` + `permissions` + `memory` 源 + `summarization` + `config`（workspace/memories 基目录、
sandbox、external_dirs、denied_dirs、skill_write、max_steps、result_log_truncate、file_context）+
`checkpointer`/`store` 的对象身份 + `GRAPH_CACHE_VERSION`。

**不进键**：session_id、workspace 真实路径、thread_id、message_id、workspace_name、
session 级连接器开关（改变工具列表，由 `tools` 体现）、`edit_targets`。

因此，**同一用户、同一机构、同一模型/工具集/提示词/专家/连接器/技能集合**的两个
session 只编译一次；`EasyAgent` 仍按 session 创建（会话编排/日志），底层
`agent.agent` 是同一个 `CompiledStateGraph` 对象。

## 3. 运行期 context 与工具绑定

- `EasyAgent.session_context`：`SessionContext(session_id, username, safe_username,
  workspace_dir, workspace_virtual_path, user_skills_dir, memory_file,
  long_term_memory_file, organization_id, shell_env, workspace_name)`。
- `create_deep_agent(..., backend=_factory, context_schema=SessionContext)`；
  `_factory(runtime)` 从 `runtime.context` 取当前会话，构建
  `/workspace/`、`/memories/`、`/user-skills/`、外部目录路由与
  `_PathTranslatingShell`。backend 实例按 session 做小型 LRU，避免每次工具调用重建。
- 会话绑定工具（`create_scheduled_task` / `save_expert` / `read_uploaded_file` /
  `access_knowledge_original` / `web_search`）由 `contextualize_tools()` 包成
  `StructuredTool`；调用时 `resolve_tool_for_context()` 用
  `model_copy(update={username, session_id, workspace_dir, workspace_name, user_id})`
  克隆出当前会话的工具实例。工具名/描述/schema 不变，HITL 与 SSE 不受影响。
  这层包装是**项目侧适配**（官方能力是 `ToolRuntime` 注入），不是 deepagents
  内置的“工具 context”功能；若未来 deepagents 提供官方 context-bound tool，
  应替换本适配层。
- `from __future__ import annotations` 会使 `ToolRuntime` 注解变成字符串，
  LangChain 注入检测不到；包装函数定义后显式写回真实注解（已用真实 deepagents
  集成探针验证）。

**验证**：真实 `create_deep_agent` + 脚本模型探针中，两个 session 得到同一张图，
`probe` 工具分别返回 `u1|s1|<w1>|hi` 与 `u1|s2|<w2>|hi`，证明工具与 backend 都按
运行期 context 解析。

## 4. 测试结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/agent tests/services/test_agent_manager_cache.py tests/services/test_agent_manager_graph_cache.py tests/services/test_agent_manager_tools.py tests/services/test_stream_*.py tests/services/test_streaming_context.py tests/api/test_chat.py -q` | **139 passed**（18.85s） |
| 追加 HITL / 记忆 / checkpoint / MCP / 技能 / 文件上下文等回归 | **300 passed**（34.11s） |
| `uvx ruff check .` | **All checks passed** |
| `python3 scripts/import_graph.py --check-scc` | **OK**：212 modules / 477 internal edges / 1 SCC(16)，与白名单完全一致 |

关键用例：

1. 两个 session 同 model/tools/prompt → spy `create_deep_agent` 只调用 1 次；
2. 不同 model / 不同 tools / 新增技能 / 不同专家提示词 → 2 次；
3. 同 session 第二次请求 → per-session `EasyAgent` 命中，0 次新编译；
4. `backend` 通过 `SessionContext` 指向 `/workspace/`、`/user-skills/`、`/memories/`
   对应目录；共享图无 context 时抛 `RuntimeError`，不会偷偷绑回首个 session；
5. 工具指纹对不同 session 的同一工具实例相同；包装后的工具按 context 返回正确
   username/session/workspace；
6. `EASY_AGENT_GRAPH_CACHE=0`、模型头 `{session_id}`、legacy checkpointer 三种情况
   均按会话建图且 `agent_graph_key is None`；
7. LRU 超限驱逐最旧项，`invalidate_user_agents()` 清空该用户图缓存。

## 5. 复用范围（诚实边界）

### 5.1 会复用的场景

- 同一用户/机构、同一模型、同一工具集（含相同的连接器开关）、同一系统提示词/
  专家提示词、同一技能集合、同一 HITL 配置、同一摘要配置；
- checkpointer 为官方全局 `AsyncSqliteSaver` / `AsyncPostgresSaver`（测试/dev 默认
  SQLite 属于此类）；
- 模型请求头不含 `{session_id}` 占位符；
- 进程内（单 worker/单副本）。

### 5.2 仍导致不命中的 session/用户差异

| 差异 | 为什么仍不命中 |
| --- | --- |
| 不同用户 / 不同机构 | `system_prompt` 含 username/organization_id（安全隔离优先） |
| 不同专家提示词 / 通用 ↔ 专家 | `system_prompt_extra` 改变终稿 |
| 联网开关、连接器开关、`save_expert` 注入条件 | 改变工具列表/系统提示词 |
| 技能新增/删除 | `skills_paths` 与 `permissions` 变化 |
| 不同模型 | 模型指纹不同 |
| `file_context.enabled` | memory 源列表不同 |
| 模型请求头含 `{session_id}` | 模型实例被 session 绑定，整图不缓存（见 §6.1） |
| legacy checkpointer | 按 session 创建 saver，整图不缓存（见 §6.2） |

## 6. 未解决阻塞 / 风险

### 6.1 生产 `opencode` 模型的 `{session_id}` 请求头（重要）

`easy_agent/config/config.yaml` 的 `opencode` provider 配置：

```yaml
headers:
  x-opencode-session: "{session_id}"
```

`create_model()` 在**建图时**把当前 session id 写进模型实例的 `default_headers`，
模型对象因此被 session 绑定。共享图会把首个 session 的 header 带给其他会话，
可能影响网关路由/prompt 缓存，甚至被网关视为错误会话。当前实现选择**检测到占位符
就禁用图缓存**（按会话建图），日志：

```text
[s1] D2 graph-cache 未启用 | 模型请求头绑定 session_id: ['x-opencode-session']
```

这意味着：使用该 provider 的部署**不会获得 D2 跨会话复用收益**。若要覆盖该
provider，需要把 header 注入改成运行期（官方 ChatOpenAI 当前没有按 run 覆盖
`default_headers` 的公开入口），或接受「同一用户所有会话共用一个稳定的 synthetic
session id」的语义变化；两者都需要单独评审，未在本任务中做。

### 6.2 MySQL 且无 Postgres 的 legacy checkpointer

`checkpoints.py` 在「业务库 MySQL + 无 `EASY_CHECKPOINT_POSTGRES_URI`」时回退到
`PersistentMemorySaver`，它是按 session 创建/持久化的对象，不能塞进共享图。
当前实现检测到官方 saver 未激活就**禁用图缓存**，HITL 恢复仍走原来的 per-session
路径。该配置下 D2 复用不生效；配置官方 Postgres/SQLite saver 后自动启用。

### 6.3 仅进程内

图缓存与 per-session `EasyAgent` 缓存一样是进程内 LRU，多 worker/多副本各自
编译一份。跨进程共享仍依赖 B1 的 checkpointer/store；D2 不改变多副本一致性语义。

### 6.4 deepagents 0.7 升级

`backend` callable 在 0.6.8 已标记 deprecated（`removal=0.7.0`）。升级前需要：

- 评估官方后继方案（例如单实例 backend + 运行期 ContextVar 路由），或
- 在 `log_framework_patch_status()` 增加 backend callable 体检项，升级时显式报错。

当前锁定 `deepagents==0.6.8`，运行期仅产生一次
`LangChainDeprecationWarning`（测试中可见），不影响行为。

### 6.5 MCP 工具的残留绑定风险

MCP 工具按「用户 + 连接器集合」加载，不带 chat session 字段，因此同用户多会话
共享是安全的；连接器开关变化会改变工具列表 → 键变化。但若未来 MCP 工具内部持有
per-session 的 `ClientSession` 或新会话字段，`CONTEXT_TOOL_FIELDS` 不会自动覆盖，
需要扩展字段表并加测试。当前 `mcp_sessions` 的持久会话按 server 复用，属于 D3 的
进程级连接池，不是 chat session 绑定。

另：包装器只转发模型参数；若未来某个会话绑定工具还依赖 `InjectedState` /
`InjectedToolCallId` 等额外注入，需要扩展 `_wrap_context_tool()`。当前五个
会话绑定工具都不依赖这些注入。

### 6.6 行为变化（有意）

1. **记忆加载时机**：`memory` 源改为固定列表（会话记忆、长期记忆、上传清单），
   文件存在即运行期加载。旧行为是「建图时存在才加载」，因此记忆写入后下一轮
   立即可见（不再依赖驱逐重建）；`MemoryMiddleware` 对不存在文件自动跳过。
2. **工具异常日志前缀**：共享图的 `LoggingMiddleware` 不再带 session_id（前缀
   `agent`）。工具名/调用 id/错误内容仍在日志与回给模型的 `ToolMessage` 中；
   StreamProcessor 的会话日志不变。
3. **用户技能目录**：`EasyAgent.__init__` 现在会创建 `workspace/{username}/skills/`
   （之前由 backend 构建时惰性创建），用于稳定 permissions 指纹，避免同一用户
   第二次建图键漂移。

### 6.7 已知未覆盖

- 未做跨进程图共享（不在 D2 范围）。
- 未改前端工具卡片/事件契约。
- 未新增 API 层字段；图缓存状态仅通过日志与测试访问。
- 未执行 Batch 0/1；未 commit。

## 7. 备注

- `tests/api/test_chat.py` 走真实 `create_deep_agent`（fake model），会打印一次
  backend callable deprecation warning；这是 deepagents 0.6.8 的已知提示，不是测试失败。
- 本增量之前工作树已有大量未提交改动（阶段 0/C2/B1/D1/D3 等），`git status` 不能
  直接作为本次改动清单；本次改动以上表文件为准。
