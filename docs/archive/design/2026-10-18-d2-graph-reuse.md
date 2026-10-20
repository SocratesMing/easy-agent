# D2：编译图按「模型 + 工具集」复用（仅后端）

| 项 | 内容 |
| --- | --- |
| 状态 | 已实现（后端；前端未改；未 commit） |
| 日期 | 2026-10-18 |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 D2；deepagents 0.6.8 官方 `backend` callable + `context_schema` + `astream(context=...)` |
| 前置 | D1 工具注册表、C2 streaming 拆分、B1 checkpointer/store 已完成，本任务不重做 |
| 新增 | `easy_agent/agent_graph_cache.py` |
| 修改 | `easy_agent/agent.py`（拆分/装配/调用段）、`easy_agent/services/agent_manager.py`（缓存失效钩子）、`easy_agent/services/streaming/orchestrator.py`（astream 传 context） |
| 测试 | `tests/agent/test_graph_cache.py`、`tests/services/test_agent_manager_graph_cache.py`、`tests/services/test_stream_context_passing.py` |
| 报告 | `docs/audit/2026-10-18-increment49-d2-report.md` |

## 1. 问题与目标

改造前 `EasyAgent._create_agent()` 把下列**会话/用户差异**全部编译进图：

- session 工作区（`backend` 的 `LocalShellBackend(root_dir=...)`、`_PathTranslatingShell` 的
  `path_mappings`）；
- 会话记忆/长期记忆/上传清单（`memory=[...]` 按文件当前是否存在变化）；
- 用户技能（`skills=["/user-skills/"]` 与技能写权限按用户已有技能变化）；
- 工具实例（`create_scheduled_task` / `save_expert` / `read_uploaded_file` /
  `access_knowledge_original` / `web_search` 的构造参数含 username/session_id/workspace）；
- subagent 的 `LoggingMiddleware(session_id=...)`；
- checkpointer（legacy 模式下按 session 创建 `PersistentMemorySaver`）。

`agent_manager` 因此按 session 缓存 `EasyAgent`；同一用户开第二个会话必须重新
`create_deep_agent()`，模型/工具/提示词完全相同也无法复用。

D2 的目标：

1. **同一张编译图服务多个 session**：键只看编译期输入（模型、工具集、系统提示词、
   middleware、HITL、subagent、skills/permissions、memory 源、摘要配置）；
2. **会话差异走运行期 `SessionContext`**：workspace / user skills / 记忆路径 /
   session_id 通过 `astream(..., context=...)` 传入，由 backend callable 与
   context-bound 工具解析；
3. **不安全的差异显式降级**：不能把按 session 的 checkpointer、按 session 替换的
   模型请求头塞进共享图；检测到就按会话建图并记录原因。

## 2. 官方能力（deepagents 0.6.8）

本任务只使用框架公开 API：

```python
create_deep_agent(
    ...,
    backend: BackendProtocol | Callable[[ToolRuntime], BackendProtocol],
    context_schema: type[SessionContext],
    checkpointer: BaseCheckpointSaver | None,
    store: BaseStore | None,
)

await graph.astream(input, config={"configurable": {"thread_id": ...}}, context=ctx)
```

框架内部解析路径：

- `FilesystemMiddleware._get_backend(runtime)`：`callable(self.backend)` 时调用
  `self.backend(runtime)`；`runtime.context` 即 `astream(context=...)` 传入的对象。
- `MemoryMiddleware._get_backend(state, runtime, config)`：同样把 `runtime.context`
  包成 `ToolRuntime` 后调用 factory；`download_files()` 对不存在的 memory 源返回
  `file_not_found` 并跳过。
- `SkillsMiddleware._get_backend`：同上。
- `SummarizationMiddleware` 通过 `create_summarization_middleware(model, backend)`
  持有同一个 backend factory；历史溢出落盘也走当前会话的 workspace。
- `ToolRuntime` 的字段（0.6.8）：
  `state / context / config / stream_writer / tool_call_id / store / tools`。

因此 backend callable 是官方支持的「同一张图按请求解析文件系统」入口。

**升级注意**：`FilesystemMiddleware._get_backend` 在 0.6.8 对 callable backend 发出
`LangChainDeprecationWarning`（`since=0.5.0, removal=0.7.0`）。本仓库当前锁定
`deepagents==0.6.8`；升级到 0.7 前需要改用官方后继方案（或在本仓库内用
ContextVar + 单实例 backend 适配），见 §8。

## 3. 缓存键

键 = `sha256(stable_json(parts))`，`parts` 由 `agent_graph_cache.build_graph_key()`
生成：

| 字段 | 来源 | 是否含 session |
| --- | --- | --- |
| `version` | `GRAPH_CACHE_VERSION`（当前 1） | 否 |
| `model` | provider / protocol / model / api_base / context_length / headers / retry | 否（headers 若含 `{session_id}` 则整图降级，不缓存） |
| `tools` | 每个工具的 name / description / class / args schema / config_model / tag_options / web_search 配置 / knowledge 配置身份（不含 api_key） | 否（username/session_id/workspace_dir/user_id/edit_targets 被剔除） |
| `system_prompt` | 最终系统提示词全文（含 username、organization_id、OS、联网/HITL 附注、专家 extra） | 否（按用户/机构/专家区分，跨用户不复用） |
| `middleware` | 类名 + `run_limit` / `exit_behavior` | 否 |
| `hitl` | 是否启用 + `interrupt_on` 工具名集合 | 否 |
| `subagents` | name / description / system_prompt / skills / middleware 类名 | 否 |
| `skills` | `skills_paths`（当前为 `[]` 或 `["/user-skills/"]`） | 否（用户级） |
| `permissions` | `FilesystemPermission(operations, paths, mode)` 列表 | 否（用户级，随已有技能变化） |
| `memory` | 固定记忆源列表 | 否 |
| `summarization` | enabled / threshold / target / truncate 长度 | 否 |
| `config` | workspace_dir / memories_dir / sandbox_enabled / external_dirs / denied_dirs / skill_write / max_steps / result_log_truncate / file_context.enabled | 否 |
| `checkpointer` | `类型:id(共享 saver)` | 否（必须是进程级共享对象） |
| `store` | `类型:id(store)` | 否 |
| `context_schema` | 固定 `"SessionContext"` | 否 |

**不进键**：`session_id`、workspace 的真实绝对路径、`thread_id`、`message_id`、
`workspace_name`、session 级连接器开关（它改变工具列表，由 `tools` 字段体现）、
`edit_targets` 映射。

`id(shared_checkpointer)` / `id(store)` 只用于「对象被替换后自动换新图」；
进程内对象稳定，不会造成每会话新键。

## 4. 运行期 `SessionContext`

`easy_agent/agent_graph_cache.py`：

```python
@dataclass(frozen=True)
class SessionContext:
    session_id: str
    username: str
    safe_username: str
    workspace_dir: str
    workspace_virtual_path: str
    user_skills_dir: str
    memory_file: str
    long_term_memory_file: str
    organization_id: str = ""
    shell_env: Mapping[str, str] = field(default_factory=dict)
    workspace_name: str = ""
```

`EasyAgent` 在 `__init__` 构造 `self.session_context`；`rename_workspace()` 后重建。
`chat_stream_generator` / `resume_stream_generator` 的 `pump_stream()` 在
`agent.session_context` 非空时调用：

```python
agent.agent.astream(stream_input, stream_mode=[...], config=..., context=context)
```

`EasyAgent.run()`（CLI 模式）同样显式传 `context=self.session_context`。

`backend` callable：

```python
def _factory(runtime):
    ctx = runtime.context            # SessionContext
    return _build_backend_for_context(config, ctx, skills_paths)
```

`_build_backend_for_context()` 与旧 `_build_backend()` 的路由/权限语义逐项一致：

| 虚拟路径 | 实际目录 |
| --- | --- |
| `/workspace/` | `ctx.workspace_dir` |
| `/memories/` | `Path(ctx.long_term_memory_file).parent`（总是挂载；目录按需创建） |
| `/user-skills/` | `ctx.user_skills_dir`（总是挂载；目录按需创建） |
| 外部目录 | 配置 `external_dirs`，每次按 `ctx.safe_username` 做越权校验 |

`default` 为 `_PathTranslatingShell(path_mappings=..., root_dir=ctx.workspace_dir, ...)`，
因此 `execute` 的虚拟路径翻译仍指向当前 session。backend 实例在 factory 内按
`(session_id, workspace_dir, user_skills_dir, long_term_memory_file, safe_username)`
做小型 LRU 缓存，避免每次文件工具调用都重建 CompositeBackend / 沙箱检测；不同
session 仍是不同实例。

## 5. 工具会话绑定：调用时注入

`create_scheduled_task` / `save_expert` / `read_uploaded_file` /
`access_knowledge_original` / `web_search` 的实例带有会话/用户字段。直接放进共享图
会让 A 会话的工具读写 B 会话工作区，属于「假复用」。

`contextualize_tools(tools)` 在**建图时**把这些工具包成 `StructuredTool`。
这是项目侧适配层（不是 deepagents 内置的“工具 context 注入”功能），
依据是官方公开的 `ToolRuntime` 注入机制：函数签名带 `runtime: ToolRuntime`
时，ToolNode 会把当前运行的 context 传进来。

```python
async def _arun(runtime: ToolRuntime, **kwargs):
    target = resolve_tool_for_context(original_tool, runtime.context)
    return await target.ainvoke(kwargs)
```

`resolve_tool_for_context()` 用 `model_copy(update=...)` 克隆原工具，并按字段名覆盖：

| 工具字段 | 运行期值 |
| --- | --- |
| `username` | `ctx.username` |
| `session_id` | `ctx.session_id` |
| `workspace_dir` | `ctx.workspace_dir` |
| `workspace_name` | `ctx.workspace_name` |
| `user_id` | `ctx.safe_username` |

`StructuredTool` 保留原 `name` / `description` / `args_schema`，所以：

- 模型看到的工具 schema 与描述不变；
- HITL `interrupt_on` 按工具名匹配，行为不变；
- 工具异常仍由 `LoggingMiddleware` 兜底；
- 工具调用日志、SSE 工具卡片仍用原工具名。

包装器只转发模型参数；若未来某个会话绑定工具还依赖 `InjectedState` /
`InjectedToolCallId` 等额外注入，需要扩展 `_wrap_context_tool()`（当前五个
工具都不依赖）。

实现细节：`agent_graph_cache.py` 使用了 `from __future__ import annotations`，
LangChain 的 `_injected_args_keys` 只认「真实类型注解」，因此
`_wrap_context_tool()` 在定义函数后显式写回
`_arun.__annotations__["runtime"] = ToolRuntime`。缺少这一步时工具会因
`missing 1 required positional argument: 'runtime'` 失败（已由集成探针复现并修复）。

**未包装的工具**：无上述字段的工具（内置文件工具、MCP 工具、普通 StructuredTool）
原样进图。MCP 工具本身按用户/连接器加载，不含 session 字段；同用户多会话共享是
安全的（连接器开关变化会改变工具列表 → 键变化）。

## 6. 生命周期

| 事件 | 行为 |
| --- | --- |
| 首次请求 | `get_agent_graph_cache().get(key)` 未命中 → 编译 → `put`，日志 `未命中` + `写入` |
| 第二个会话同键 | `get` 命中 → 直接复用 `entry.graph`，日志 `命中`；`EasyAgent` 仍是新对象 |
| 键变化（模型/工具/提示词/技能/HITL/摘要/配置） | 新键 → 新图；旧图留在 LRU 中，切回旧配置可再次命中 |
| LRU 超限（默认 32，`EASY_AGENT_GRAPH_CACHE_SIZE` 可调） | 淘汰最久未使用项，日志 `LRU 驱逐` |
| `init_agent_config()`（应用启动/测试/热更新） | `clear_agent_graph_cache()` 清空全部图 |
| `invalidate_user_agents(username)`（MCP/技能/专家变更） | 同时 `invalidate_user` 清除该用户全部图 |
| 会话删除 / `remove_session_agent()` | 只清 per-session `EasyAgent`，不驱逐共享图（其他会话可能还在用） |
| `rename_workspace()` | 更新 `SessionContext` 后重新取图；键不变时复用同一张图 |

缓存是**进程内**的：多 worker/多副本各自一份，不共享编译图；B1 的
checkpointer/store 才是跨进程共享层。这是本任务接受的范围（与 per-session
`EasyAgent` 缓存同性质）。

## 7. 降级 / 阻断条件（不缓存，按会话建图）

`EasyAgent._graph_cache_block_reason()` 在以下情况返回原因并跳过缓存：

1. **`EASY_AGENT_GRAPH_CACHE=0`**：总开关，用于排障/回滚。
2. **模型请求头含 `{session_id}`**：`create_model()` 在创建模型时替换 header，
   模型实例本身被 session 绑定；共享图会把首个 session 的 header 带给其他会话。
   当前生产 `config.yaml` 的 `opencode` provider 使用
   `x-opencode-session: "{session_id}"`，因此该模型不参与复用。
3. **checkpointer 不是官方全局 saver**：`postgres_checkpointer_active()` /
   `sqlite_checkpointer_active()` 都为 false 时（MySQL 且无 Postgres URI →
   legacy `PersistentMemorySaver` 按 session 创建），checkpointer 无法跨会话共享。
   `get_session_checkpointer()` 仍按旧逻辑返回 per-session saver，图也按会话编译。

降级路径的日志示例：

```text
[s1] D2 graph-cache 未启用 | 模型请求头绑定 session_id: ['x-opencode-session']
[s1] D2 graph-cache 未启用 | checkpointer 为按 session 创建的 legacy saver ...
```

降级时 backend 仍用 callable，但带 `fallback_context=self.session_context`，
兼容绕过 orchestrator 直接 `agent.agent.astream(...)` 的旧调用；共享图路径
**不**带 fallback，缺少 context 时抛出明确 `RuntimeError`，避免把图偷偷绑回某个 session。

## 8. 升级风险

| 风险 | 缓解 |
| --- | --- |
| deepagents 0.7 移除 callable backend | 当前锁定 0.6.8；升级前评估官方替代（单实例 backend + ContextVar 路由）并在 `log_framework_patch_status()` 增加体检项 |
| MCP 工具内部若持有 per-session session 对象 | 当前 `get_mcp_tools()` 按 user/connectors 加载，不含 chat session；若未来引入 per-session MCP session，需要把该工具纳入 `CONTEXT_TOOL_FIELDS` 或禁用缓存 |
| 自定义工具若新增会话字段 | 新字段名不在 `CONTEXT_TOOL_FIELDS` 时不会注入；测试用 `tool_fingerprint` 会因字段未参与而误判可复用。新增工具时应同步扩展字段表并加测试 |
| 工具实例 `model_copy` 不深拷贝可变字段 | `edit_targets` 是全局 `{session_id: expert_id}` 映射，工具读取时用当前 session_id，语义不变；其他可变字段需评审 |
| 日志前缀 | 共享图的 `LoggingMiddleware` 不再带 session_id（日志前缀 `agent`）；工具异常仍含工具名/调用 id，且 stream 侧 `StreamProcessor` 仍按 session 记录 |
| 记忆加载时机 | memory 源改为固定列表，文件存在即运行期加载；比旧行为「建图时存在才加载」更及时，属有意的行为改进 |

## 9. 测试策略

1. **spy `create_deep_agent` 计数**：
   - 同 model/tools/prompt 两个 session → 1 次编译；
   - 不同 model / 不同 tools / 新增技能 → 2 次；
   - 同 session 第二次请求 → per-session `EasyAgent` 命中，0 次新编译。
2. **backend context**：`_make_backend_factory` + `SessionContext` → `/workspace/`、
   `/user-skills/`、`/memories/` 路由分别指向该 context 的目录；共享图无 context 时
   抛错。
3. **工具 context**：会话绑定工具包装后按 `runtime.context` 克隆；
   `tool_fingerprint` 对不同 session 的同一工具实例返回相同值。
4. **生命周期**：LRU 驱逐、`EASY_AGENT_GRAPH_CACHE=0`、模型头绑定、
   legacy checkpointer、`invalidate_user_agents` 清图。
5. **流式**：`pump_stream` 有 context 时透传、无 context 时保持旧调用形态。
6. **回归**：`tests/agent`、`tests/services/test_agent_manager*`、
   `tests/services/test_stream*`、`tests/api/test_chat.py`、HITL/记忆/checkpoint/
   MCP/技能相关测试。

## 10. 明确不改

- 前端（`frontend/`、`frontend-vue2/`）；
- HITL 策略（`interrupt_on` 条件、决策集合、审批时机、`HITL_SYSTEM_NOTE`）；
- `easy_agent/api/`、`app.py`、`checkpoints.py`、`mcp.py`、db、conftest、scripts；
- C1 新建的 API repository 文件；
- 不执行 Batch 0/1；
- 不 commit。
