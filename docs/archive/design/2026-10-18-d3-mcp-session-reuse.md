# D3：MCP 持久会话复用（后端）

| 项 | 内容 |
| --- | --- |
| 状态 | 已实现（本分支，未 commit） |
| 日期 | 2026-10-18 |
| 范围 | `easy_agent/services/mcp.py`、新增 `easy_agent/services/mcp_sessions.py`、新增 `tests/services/test_mcp_session_reuse.py` |
| 依据 | 架构改进计划 D3：MCP 工具每次调用重新建 session，握手/stdio 进程启动成本高 |
| 约束 | 不改前端、不改 MCP 目录/连接器契约、不改工具名/description/`args_schema`/证据信封、不改 HITL、不动 `agent.py` / `agent_manager.py` / `app.py` |

## 1. 问题

`services/mcp.py:get_mcp_tools()` 已经按 `mcp.json` 路径 + mtime 缓存

- `MultiServerMCPClient`（按连接配置子集缓存）；
- 每个 server 的工具列表（按 server 分组缓存）。

但 `MultiServerMCPClient.get_tools()` 是 **stateless** 的：官方文档明确
“A new session will be created for each tool call”。因此缓存只省掉了
“列工具”，真正执行工具时仍然每次：

1. `create_session()` 建立 transport（stdio 启动子进程 / HTTP 建连）；
2. `session.initialize()` 握手；
3. `session.call_tool()`；
4. 关闭 session（stdio 杀子进程 / HTTP 终止会话）。

同一轮里连续调用同一 server 的多个工具，会重复 N 次握手；stdio server
（`npx` / `python server.py`）每次还要重新启动进程。

## 2. 官方 API 调研结论

调研对象：仓库 `.venv` 中的 `langchain-mcp-adapters==0.2.2` + `mcp==1.27.2`。

| 能力 | 官方公开 API | 结论 |
| --- | --- | --- |
| 建立单 server 会话 | `MultiServerMCPClient.session(server_name, *, auto_initialize=True)`，`@asynccontextmanager`，yield 已 `initialize()` 的 `ClientSession`（`client.py:114-150`） | **可用**，是官方推荐的显式会话方式 |
| 按已有会话加载工具 | `langchain_mcp_adapters.tools.load_mcp_tools(session, *, connection=None, ...)`（`tools.py:436-497`） | **可用**；传入 `session` 时工具闭包直接 `session.call_tool()`，不会每次建 session（`tools.py:305-401`） |
| 列工具 | `load_mcp_tools` 内部 `_list_all_tools(session)` → `session.list_tools(cursor=...)` | 通过传入的会话执行 |
| 客户端级连接池 / `close()` | 无。`MultiServerMCPClient` 0.1.0 起**不能** `async with`（`client.py:255-279` 直接抛 `NotImplementedError`）；也没有公开的 `connect_all` / `aclose` | **不存在**；需要自建按 server 的注册表 |
| 低层建连 | `langchain_mcp_adapters.sessions.create_session(connection, ...)`（`sessions.py:398-470`） | 存在，但 `client.session()` 已封装 callbacks 与 `initialize()`，优先用后者 |
| 会话并发 | `mcp.ClientSession` 通过 JSON-RPC request id 路由响应；`BaseSession.send_request()` 直接写 anyio memory stream（`mcp/shared/session.py`），未暴露同步锁 | 同一会话可并发请求；本实现不额外串行化普通调用 |
| 会话关闭 | `async with` 退出即关闭；stdio 会终止子进程，streamable HTTP 按 `terminate_on_close` 终止 | 复用 context manager 的关闭语义 |

**关键约束（anyio cancel scope）**：stdio / HTTP 的 context manager 内部使用
anyio task group。同一个 async context 必须在**同一个 asyncio task** 中进入和退出，
否则会出现 “Attempted to exit cancel scope in a different task than it was entered in”。
HTTP 请求由不同 task 处理，不能简单用 `AsyncExitStack` 跨请求持有会话。

**结论**：官方没有现成的“持久会话池”，但提供了稳定的两块公开积木：
`client.session(server)` + `load_mcp_tools(session)`。本实现只组合这两者，
不读取 `MultiServerMCPClient` / `ClientSession` 的私有属性，也不调用私有方法。

## 3. 目标与非目标

**目标**

1. 同一 server 的连续工具调用复用同一个 MCP session：只握手一次、stdio 只启动一次。
2. session 按 `(配置文件 scope, server, 连接配置)` 隔离；不同 server 不共享。
3. `invalidate_mcp_cache()` 能关闭对应 scope 的 session；配置 mtime 变化时关闭旧
   scope；进程退出时由事件循环取消 owner task，从而走正常 `async with` 关闭。
4. 会话故障（连接关闭、子进程退出、HTTP 断连）时重建一次并重试；再次失败则交给
   现有证据信封返回 `is_error=true`，不拖垮其他 server。
5. 不改变工具名、description、`args_schema`、`response_format`、证据前缀与连接器
   语义；不依赖网络测试。

**非目标**

- 不做跨进程 / 多副本共享会话（每个后端进程各自持有）。
- 不做 MCP 请求级负载均衡、健康检查、自动重连风暴控制（仅一次重建）。
- 不把 `validate_mcp_servers()` 的探测改成持久会话（探测仍是一次性、无缓存）。
- 不引入后台常驻线程或独立事件循环；所有会话归属当前 FastAPI 事件循环。

## 4. 实现结构

新增 `easy_agent/services/mcp_sessions.py`：

```text
MCPSessionManager
├── _entries: dict[(scope, server, connection_key), _SessionEntry]
├── _locks:   dict[key, asyncio.Lock]          # 防止并发重复建 session
├── _close_tasks: set[asyncio.Task]            # 便于 aclose 等待
└── _stats: dict[transport, counters]          # created/reused/closed/failed/retried

_SessionEntry
├── owner task: 在同一个 task 内 async with client.session(server) as session
├── ready / stop: asyncio.Event
├── session: 已 initialize 的 ClientSession
├── idle timer: loop.call_later(transport TTL)
└── close task: 幂等关闭

MCPManagedSession  (duck-typed ClientSession facade)
├── async list_tools(cursor=..., **kwargs)  -> manager.list_tools(...)
└── async call_tool(name, args, progress_callback=...) -> manager.call_tool(...)
```

### 4.1 为什么用 owner task

`client.session(server)` 是 async context manager。若在请求 task A 中
`await cm.__aenter__()`、在请求 task B 中 `__aexit__()`，anyio cancel scope 会因
task 不一致而报错。因此每个 entry 启动一个**专属 owner task**：

```python
async def _run(self):
    try:
        async with self.client.session(self.server) as session:
            self.session = session
            self.alive = True
            self.ready.set()
            await self.stop.wait()      # 生命周期由 stop 事件控制
    finally:
        self.alive = False
        self.ready.set()
```

- 建连 / 关闭都在 owner task 内完成；
- 工具调用在其他 task 中直接 `session.call_tool()`，不碰 context 管理；
- `close()` 只 `stop.set()` 并等待 owner task 退出，超时则 cancel。

### 4.2 工具如何绑定会话

`load_server_tools()` 构造一个 `MCPManagedSession`，再调用官方：

```python
await load_mcp_tools(
    managed_session,              # session 参数不为 None
    server_name=server,
    callbacks=client.callbacks,
    tool_interceptors=client.tool_interceptors,
    tool_name_prefix=client.tool_name_prefix,
)
```

官方工具闭包持有的是 `managed_session`，不是某个具体 `ClientSession`。
`managed_session.call_tool()` 每次向 manager 要“当前可用的 entry”，因此：

- session 存活时直接复用；
- session 因 idle / invalidate / 故障被回收后，下一次调用自动重建，
  **工具对象不需要重新 `get_mcp_tools()`**（缓存的工具仍可用）；
- 工具列表 / schema 仍来自首次 `list_tools`，与当前缓存语义一致。

`MCPManagedSession` 只实现 `list_tools` 与 `call_tool` 两个公开方法，
是鸭子类型的 `ClientSession` 门面；不访问 adapter / SDK 私有字段。

## 5. 生命周期

### 5.1 创建

首次 `list_tools` / `call_tool` 时按 key 取 entry：

1. 命中且 `entry.loop is current_loop`、`alive`、未过期 → 复用；
2. 否则在 per-key `asyncio.Lock` 内重建（并发调用只建一个）；
3. 重建失败 → 不写入 `_entries`，异常交给 `get_mcp_tools()` 的 per-server 容错。

key = `(scope, server, canonical_json(connection))`：

- `scope` 是解析后的 `mcp.json` 路径（全局或用户级），用于配置变更时定向关闭；
- `connection` 含 transport / command / url / headers / env；配置不同即不同 session；
- 相同全局配置被多个用户使用时会共享 session（与现有 `_mcp_clients` 按配置缓存
  的语义一致；需要用户级隔离时使用用户自己的 `mcp.json`）。

### 5.2 空闲过期

按 transport 使用不同默认 TTL（可用环境变量覆盖）：

| transport | 默认 idle TTL | 说明 |
| --- | --- | --- |
| `stdio` | 300s | 子进程占用内存/句柄，过期更积极 |
| `streamable_http` / `sse` / `websocket` | 600s | HTTP 会话，保留更久以减少重连 |
| 其他 / 未标注 | 300s | 保守回退 |

环境变量：

- `EASY_MCP_SESSION_IDLE_SECONDS`：覆盖所有 transport，`0` 表示禁用空闲回收；
- `EASY_MCP_SESSION_CONNECT_TIMEOUT_SECONDS`：建连 + `initialize()` 超时，默认 60s；
- `EASY_MCP_SESSION_CLOSE_TIMEOUT_SECONDS`：等待 owner task 退出，默认 10s；
- `EASY_MCP_SESSION_CALL_TIMEOUT_SECONDS`：单次工具调用超时，默认 `0`（禁用，
  保持原有“由 transport 自己控制超时”的语义）。

每次成功调用重置 idle timer。timer 到期只回收 entry，不抛异常给调用方；
下一次调用自动重建。

### 5.3 主动失效与关闭

- `get_mcp_tools()` 发现同一 `mcp.json` mtime 变化时，调用
  `close_mcp_scope_sync(path_key)` 关闭该文件的旧 session，再重新加载工具；
- `invalidate_mcp_cache(username)`：
  - 无 username：`close_mcp_sessions_sync()` 关闭所有 session；
  - 有 username：关闭该用户 `mcp.json` 与全局 `mcp.json` 两个 scope 的 session；
- `invalidate` 是同步函数，运行在事件循环线程时直接 `loop.create_task(close)`；
  跨线程时用 `loop.call_soon_threadsafe()` 调度；
- 新增 `async aclose_mcp_sessions()` 供测试 / 未来 lifespan 显式等待关闭；
- 模块级 `atexit` 做 best-effort 关闭；正常进程退出由事件循环取消 owner task，
  `async with` 会在 owner task 内执行 `__aexit__`。

### 5.4 失败重建

`call_tool` / `list_tools` 捕获到异常后：

1. `ToolException`（MCP 返回 `isError=true` 或工具参数错误）**不重试**，原样抛出；
2. `CancelledError` / `KeyboardInterrupt` / `SystemExit` **不重试**，继续传播；
3. 传输层异常（`anyio.ClosedResourceError` / `BrokenResourceError` / `EndOfStream`、
   `ConnectionError` / `BrokenPipeError` / `EOFError`、非超时的 `httpx.TransportError`、
   带 connection closed/broken pipe/session closed 等标记的 `McpError` / `RuntimeError`）
   → 标记 entry 失效、关闭、重建一次并重试；
4. 第二次仍失败 → 回收 entry，异常交给上层；`get_mcp_tools()` 逐 server 容错，
   工具调用异常由 `mcp_evidence` 转为 `is_error=true` 证据结果。

`ExceptionGroup` 会递归展开判断；只要组内包含 cancellation 就不重试。

## 6. 并发与超时

- **同一 server 并发调用**：共享同一 `ClientSession`，依赖 MCP SDK 的 JSON-RPC
  request id 路由；本实现不额外加全局调用锁（避免把 agent 的并行工具调用串行化）。
- **并发首次调用**：per-key `asyncio.Lock` 保证只建一个 session；等待者复用结果。
- **并发失败重建**：先失败者 retired entry；后失败者 `_ensure_entry` 若发现已有
  新 entry 则直接复用，可能只重建一次；极端竞态下最多多建一个 session，旧 session
  会被关闭。
- **跨事件循环**：entry 记录创建时的 loop；在另一个 loop 被请求时旧 entry 不会被
  复用（关闭旧 loop 的 session 只能 best-effort），新 loop 重建。生产只有 FastAPI
  一个 loop；测试按 loop 隔离 manager。
- **调用超时**：默认禁用。启用后超时会回收 session（响应可能仍在路上，会话状态
  不可信），并把 `TimeoutError` 交给证据信封；**不自动重试**，避免重复副作用。

## 7. 降级策略

| 场景 | 行为 |
| --- | --- |
| 适配器 / 测试替身没有 `client.session` | `get_mcp_tools()` 退回原有 `client.get_tools(server_name=...)` stateless 路径 |
| `langchain_mcp_adapters.tools.load_mcp_tools` 导入失败 | `supports_persistent_sessions()` 返回 False，同上退回 stateless |
| 某个 server 建连 / `initialize()` / `list_tools` 失败 | 该 server 工具为空，记 warning；其他 server 正常 |
| 工具调用传输层失败 | 关闭旧 session，重建一次重试；再失败返回错误证据 |
| 工具业务错误（`ToolException`） | 不重试，直接交给证据信封，避免重复执行副作用 |
| `invalidate` 时仍有在途调用 | 旧 session 关闭；在途调用失败后由工具门面自动重建，或返回错误证据 |

## 8. 与现有缓存 / 证据层的关系

- `_mcp_tools_cache` 仍按 `mcp.json` 路径 + mtime 缓存**原始工具**（现在是
  绑定 `MCPManagedSession` 的官方工具）；缓存命中不重新 `list_tools`；
- `wrap_mcp_tools()` 每次返回新的证据包装器，底层仍是同一批 session-bound 工具；
- 工具名 / description / `args_schema` / `response_format` 完全由官方
  `load_mcp_tools` 生成，本实现不重写转换逻辑；
- 工具同名过滤、内建工具冲突过滤、跨 server 去重、连接器筛选均未改。

## 9. 测试方式（离线）

新增 `tests/services/test_mcp_session_reuse.py`，不访问网络、不启动真实 MCP：

- `FakeMCPClient.session()` 是 `@asynccontextmanager`，记录打开/关闭次数；
- `FakeSession.list_tools()` 返回 `mcp.types.Tool` 定义；
- `FakeSession.call_tool()` 返回 `mcp.types.CallToolResult`；
- 使用**真实** `load_mcp_tools` 完成 MCP Tool → LangChain Tool 转换，测试覆盖的是
  实际集成路径，而不是 mock 掉转换层。

用例（11 个）：

1. 同一 server 连续两次工具调用只打开一次 session；
2. 不同 server 各自一个 session，互不关闭；
3. 并发两次调用共享一个 session；
4. 传输层异常（`anyio.ClosedResourceError`）后重建并重试成功；
5. `ToolException` 不重试；
6. idle 过期后下一次调用重建；
7. 调用超时后回收 session 且不重试；
8. scope 失效只关闭匹配的 scope，不影响其他 scope；
9. `invalidate_mcp_cache()` 关闭 session，下一次 `get_mcp_tools()` 重建；
   同时校验证据前缀 / 工具名 / description 不变；
10. 无 `client.session` 的客户端退回 stateless `get_tools`；
11. 某个 server 建连失败不影响其他 server。

相关回归命令：

```bash
pytest tests/services/test_mcp_evidence.py \
       tests/services/test_mcp_transport.py \
       tests/services/test_mcp_config.py \
       tests/api/test_connectors.py -q
uvx ruff check .
```

## 10. 性能预期

- **HTTP/SSE**：每次工具调用少一次 `initialize()` 往返（约 1 RTT）与一次连接建立；
  连续 N 次调用由 N 次握手降为 1 次。
- **stdio**：每次调用少一次子进程启动 + MCP 握手；对 `npx` / Python server，
  通常是几十毫秒到数秒的节省（取决于运行时与依赖加载）。
- **列工具**：原本已缓存，本次不改变；额外开销是每次调用一次 entry 查找与
  `touch()`（微秒级）。
- 空闲 TTL 到期后会多一次重建，属于预期取舍。

## 11. 风险与限制

1. **跨用户共享**：session key 不含 username。相同全局连接配置会被多个用户共享，
   与现有 client 缓存一致；若某个 MCP server 把状态挂在连接上，建议使用用户级
   `mcp.json` 或让配置包含用户标识。
2. **至多一次语义**：传输层异常后重试一次，极端情况下 server 可能已执行但响应丢失，
   存在重复副作用风险。仅对“连接已关闭 / 断管 / EOF”等明确传输失败重试；
   工具业务错误、超时不重试。
3. **事件循环归属**：session 只能在创建它的 loop 使用。跨 loop 请求会放弃旧 entry；
   若旧 loop 已关闭，子进程/HTTP 连接只能依赖 OS/进程退出清理。生产单 loop 下无此问题。
4. **会话状态**：复用连接意味着 server 端 per-session 状态会跨调用保留；这是复用
   的收益也是语义变化。需要无状态隔离的 server 应使用不同连接配置或禁用复用。
5. **配置热更新**：mtime 变化只关闭该 `mcp.json` scope 的 session；正在运行的 agent
   若仍持有旧工具对象，会按旧连接配置自动重建，直到下次 `get_mcp_tools()` 才换新配置。
6. **关闭是异步的**：`invalidate_mcp_cache()` 是同步 API，只调度关闭；需要确定性
   等待时使用 `await aclose_mcp_sessions()`。
7. **工具列表仍是首次快照**：只缓存首次 `list_tools` 的结果；server 运行期动态
   增删工具时，需要 `invalidate_mcp_cache()` 或等配置 mtime 变化后重新加载。
8. **未跑全量测试**：按任务约定只跑 MCP 相关测试与 ruff，全量由 Lead 执行。

## 12. 明确未实现项

- 没有实现跨进程 / 多副本共享连接池；
- 没有实现 MCP server 健康检查、指数退避重连、连接数上限；
- 没有改 `validate_mcp_servers()` 的探测方式；
- 没有改 `app.py` lifespan；进程退出依赖 owner task 取消 + `atexit` best-effort，
  如需优雅等待可在后续批次把 `await aclose_mcp_sessions()` 接入 lifespan。
