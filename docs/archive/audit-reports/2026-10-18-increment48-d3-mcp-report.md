# Increment 48 执行报告（D3 MCP 会话复用）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-18 |
| 依据 | 架构改进计划 D3：MCP 工具调用复用 session |
| 设计文档 | `docs/design/2026-10-18-d3-mcp-session-reuse.md` |
| 约束 | 仅后端；未改前端；未执行 Batch 0/1；未改 HITL 策略；未改 MCP 目录/前端契约；未 commit |
| 结论 | **已实现**基于官方 `client.session()` + `load_mcp_tools(session)` 的按 server 持久会话复用；同 server 连续调用只握手一次；故障重建一次；invalidate / mtime / 进程退出可回收；无 session API 时自动退回 stateless。目标测试 **91 passed**；全仓 `ruff check .` 通过 |

## 1. 改动清单

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `easy_agent/services/mcp_sessions.py` | 新增 | `MCPSessionManager`、`_SessionEntry`、`MCPManagedSession` 门面、环境变量/超时、故障重建、scope 关闭、统计 |
| `easy_agent/services/mcp.py` | 修改 | `_load_server_tools` 接入持久会话；mtime 变化关闭旧 scope；`invalidate_mcp_cache` 关闭 session；docstring |
| `tests/services/test_mcp_session_reuse.py` | 新增 | 11 个离线用例（复用/隔离/并发/重建/ToolException/idle/timeout/invalidate/fallback/单 server 故障） |
| `docs/design/2026-10-18-d3-mcp-session-reuse.md` | 新增 | 官方 API 调研、生命周期、并发/超时、降级、测试与风险 |
| `docs/audit/2026-10-18-increment48-d3-mcp-report.md` | 新增 | 本报告 |

未修改：`agent.py`、`agent_manager.py`、`app.py`、`checkpoints.py`、`streaming.py`、db、API、conftest、前端、HITL 策略、MCP 目录/连接器契约。

## 2. 官方 API 调研结论

调研对象：`.venv` 中 `langchain-mcp-adapters==0.2.2`、`mcp==1.27.2`。

1. **官方支持持久单 server 会话**：
   `MultiServerMCPClient.session(server_name, *, auto_initialize=True)` 是公开的
   `@asynccontextmanager`，yield 已 `initialize()` 的 `ClientSession`
   （`client.py:114-150`）。
2. **官方支持“按已有会话”加载工具**：
   `langchain_mcp_adapters.tools.load_mcp_tools(session, ...)` 在 `session` 非空时，
   生成的工具直接调用 `session.call_tool()`，不会每次建 session
   （`tools.py:436-497`、`tools.py:305-401`）。
3. **官方没有客户端级连接池 / close API**：
   `MultiServerMCPClient` 自 0.1.0 起不能 `async with`（`client.py:255-279` 抛
   `NotImplementedError`），也没有 `connect_all` / `aclose`。因此必须自建按 server
   的注册表。
4. **不能跨 task 进入/退出 context**：
   transport 内部使用 anyio cancel scope；同一个 `async with` 必须在同一个 asyncio
   task 中进入和退出。跨请求用 `AsyncExitStack` 持有可能触发
   “Attempted to exit cancel scope in a different task…”。
5. **会话并发**：`ClientSession` 按 JSON-RPC request id 路由响应，公开 API 未暴露
   同步锁；同一会话可并发调用。本实现不额外串行化普通工具调用。

**实现选择**：不碰 adapter/SDK 私有属性；只组合 `client.session()` 与
`load_mcp_tools(session)` 两个公开 API。

## 3. 实现方式

### 3.1 注册表与 key

```text
key = (scope, server, canonical_json(connection))
```

- `scope` = 解析后的 `mcp.json` 路径（全局或用户级），用于定向失效；
- `connection` 含 transport / command / url / headers / env；配置不同即不同 session；
- 不同 server 天然隔离；相同全局配置跨用户共享（与既有 `_mcp_clients` 按配置缓存
  一致，见风险）。

### 3.2 owner task（关键）

每个 entry 启动一个专属 task：

```python
async def _run(self):
    async with self.client.session(self.server) as session:
        self.session = session
        self.alive = True
        self.ready.set()
        await self.stop.wait()
```

- 建连、`initialize()`、关闭都发生在同一个 task 内，规避 anyio cancel scope 的
  task 归属问题；
- 工具调用在其他 task 通过 `MCPManagedSession.call_tool()` 直接调用
  `session.call_tool()`，不参与 context 管理；
- `close()` 设置 `stop` 并等待 owner task，超时 cancel。

### 3.3 工具绑定

`MCPSessionManager.load_server_tools()` 构造 `MCPManagedSession`（鸭子类型的
`ClientSession` 门面，只实现 `list_tools` / `call_tool`），然后调用官方
`load_mcp_tools(proxy, server_name=..., callbacks=..., tool_interceptors=...,
tool_name_prefix=...)`。

官方工具闭包持有 proxy；proxy 每次向 manager 要“当前健康 entry”。因此：

- session 正常时复用，连续调用不重复握手；
- session 因 idle / invalidate / 故障回收后，**已缓存的工具对象仍可用**，下一次
  调用自动重建；
- 工具名、description、`args_schema`、`response_format` 全部由官方转换生成，未改
  证据包装和 HITL 匹配。

### 3.4 生命周期

| 事件 | 行为 |
| --- | --- |
| 首次 `list_tools` / `call_tool` | 在 per-key `asyncio.Lock` 内建 session，并发只建一个 |
| 后续调用 | 复用 entry，`touch()` 重置 idle timer |
| idle 到期 | 按 transport TTL 回收（stdio 300s；HTTP/SSE/WS 600s；可环境变量覆盖） |
| `mcp.json` mtime 变化 | `get_mcp_tools()` 关闭该 scope 的旧 session，再加载新工具 |
| `invalidate_mcp_cache(username)` | 关闭用户/全局 scope；无 username 关闭全部 |
| 进程退出 | 事件循环取消 owner task，`async with` 在 owner task 内正常退出；另有 `atexit` best-effort |
| 测试/未来 lifespan | 新增 `await aclose_mcp_sessions()` 可确定性等待 |

环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EASY_MCP_SESSION_IDLE_SECONDS` | 按 transport | `0` = 禁用 idle 回收 |
| `EASY_MCP_SESSION_CONNECT_TIMEOUT_SECONDS` | 60 | 建连 + initialize 超时 |
| `EASY_MCP_SESSION_CLOSE_TIMEOUT_SECONDS` | 10 | 等待 owner task 退出 |
| `EASY_MCP_SESSION_CALL_TIMEOUT_SECONDS` | 0（禁用） | 单次工具调用超时；超时回收 session、不重试 |

### 3.5 故障重建与降级

- 传输层异常（`ClosedResourceError` / `BrokenResourceError` / `EndOfStream`、
  `ConnectionError` / `BrokenPipeError` / `EOFError`、非超时 `httpx.TransportError`、
  带 connection closed / broken pipe / session terminated 标记的 `McpError` /
  `RuntimeError`）：回收 entry，重建一次并重试；
- `ToolException`（MCP `isError=true` 或参数错误）：**不重试**，原样抛给证据层；
- `CancelledError` / `KeyboardInterrupt` / `SystemExit`：不重试；
- 超时：回收 session（状态不可信），不重试；
- 重建仍失败：异常交给 `get_mcp_tools()` 的 per-server 容错 / 证据信封
  （`is_error=true`），其他 server 不受影响；
- 客户端没有 `client.session`（旧适配器/测试替身）或 `load_mcp_tools` 不可导入：
  `get_mcp_tools()` 退回原有 `client.get_tools(server_name=...)` stateless 路径。

`ExceptionGroup` 会递归展开；只要包含 cancellation 就不重试。

## 4. 测试与门禁

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新增用例 | `pytest tests/services/test_mcp_session_reuse.py -q` | **11 passed, 1 warning** |
| 任务指定套件 | `pytest tests/services/test_mcp_evidence.py tests/services/test_mcp_transport.py tests/services/test_mcp_config.py tests/api/test_connectors.py -q` | **60 passed, 1 warning** |
| 补充回归（含新用例 + connector directive） | `pytest tests/services/test_mcp_evidence.py tests/services/test_mcp_transport.py tests/services/test_mcp_config.py tests/services/test_mcp_session_reuse.py tests/services/test_connector_directive.py tests/api/test_connectors.py -q` | **91 passed, 1 warning** |
| 改动文件 lint | `uvx ruff check easy_agent/services/mcp.py easy_agent/services/mcp_sessions.py tests/services/test_mcp_session_reuse.py` | **All checks passed!** |
| 编译 | `python -m py_compile` 三个文件 | 通过 |
| 全仓 lint | `uvx ruff check .` | **All checks passed!** |

说明：唯一 warning 是既有 `StarletteDeprecationWarning`（httpx/TestClient），与本次
改动无关。按任务约定**未跑全量测试**。

新增用例覆盖：

1. 同一 server 连续两次工具调用只打开一次 session；
2. 不同 server 各自 session、互不关闭；
3. 两个并发调用共享一个 session；
4. `anyio.ClosedResourceError` 后重建一次并重试成功；
5. `ToolException` 不重试；
6. idle 过期后下一次调用重建；
7. 调用超时回收 session、不重试；
8. scope 失效只关闭匹配的 scope；
9. `invalidate_mcp_cache()` 关闭并由下一次 `get_mcp_tools()` 重建；
10. 无 `client.session` 的客户端退回 stateless `get_tools`；
11. 某个 server 建连失败不影响其他 server。

## 5. 性能收益

> 未做真实网络/子进程基准（测试全部 mock，按约定不跑真实 MCP），以下为按握手次数
> 推导的收益，不虚构毫秒数字。

- 原路径：N 次工具调用 = N 次 transport 建立 + N 次 `initialize()` 握手 + N 次
  `list_tools`（工具列表缓存可省最后一次，但 session 仍 N 次）。
- 新路径：N 次调用 = 1 次建连/握手 + 1 次 `list_tools` + N 次 `call_tool`。
- **HTTP/SSE**：每次调用少一次 `initialize()` 往返（约 1 RTT）和一次连接建立。
- **stdio**：每次调用少一次子进程启动 + 握手；对 `npx` / Python server，通常节省
  几十毫秒到数秒（取决于运行时和依赖加载）。
- 额外开销：每次调用一次 key 查找、`asyncio.Lock` 短暂进入/exited、`touch()` 定时器
  重置，均为微秒级；idle 到期后多一次重建，属于预期取舍。

## 6. 并发与泄漏风险

| 风险 | 说明 / 缓解 |
| --- | --- |
| 跨用户共享 | key 不含 username；相同全局连接配置跨用户共享 session（与既有 client 缓存一致）。若 server 把状态挂在连接上，建议用户级 `mcp.json` 或配置含用户标识。 |
| 重复副作用 | 传输异常后重试一次；极端情况下 server 已执行但响应丢失。只对明确传输失败重试，`ToolException` / 超时不重试。 |
| 事件循环归属 | 每个 entry 绑定创建它的 loop；跨 loop 不复用，旧 loop 已关闭时只能 best-effort 清理。生产单 FastAPI loop 下无影响。 |
| idle/常驻连接 | 按 transport TTL 回收；`invalidate` / mtime / 进程退出均会关闭。默认 stdio 300s、HTTP 600s，避免无限常驻。 |
| 关闭是异步的 | `invalidate_mcp_cache()` 是同步 API，只调度关闭；需要确定性等待用 `await aclose_mcp_sessions()`。 |
| 配置热更新 | mtime 变化关闭该 scope 旧 session；已在运行的 agent 持有旧工具时按旧连接配置自动重建，直到下次 `get_mcp_tools()` 换新配置。 |
| atexit | 仅 best-effort；正常 uvicorn 退出由事件循环取消 owner task，`async with` 会在 owner task 内关闭。未改 `app.py` lifespan（超出允许范围）。 |

## 7. 明确未实现项

- 未实现跨进程/多副本共享会话池；
- 未实现健康检查、指数退避重连、连接数上限；
- 未改 `validate_mcp_servers()` 的探测方式（仍一次性、无缓存）；
- 未改 `app.py` lifespan；优雅关闭可用新增 `aclose_mcp_sessions()` 在后续批次接入；
- 未跑全量测试；按任务约定由 Lead 统一执行。

## 8. 验收对照

| 要求 | 结果 |
| --- | --- |
| 优先官方公开 API | 使用 `client.session()` + `load_mcp_tools(session)`；无 adapter/SDK 私有属性 |
| 同一 server 连续调用不重复握手 | 已实现；测试断言只打开一次 session |
| 不同 server 隔离 | 已实现；测试断言两个 session |
| 异常时重建/降级 | 传输异常重建一次；`ToolException` 不重试；无 session API 退回 stateless；单 server 故障隔离 |
| invalidate 后重建 | 已实现；测试断言旧 session 关闭、下一次加载新建 |
| stdio 与 HTTP 生命周期分开 | 按 transport 设置不同 idle TTL；统一 owner task 关闭；统计按 transport 分桶 |
| 工具名/描述/证据/前端语义不变 | 官方转换 + 原证据包装；`get_mcp_tools` 缓存/筛选/同名过滤逻辑未改 |
| 不改前端/API/HITL/流式 | 未改相关文件 |
| 不 commit | 未 commit |
