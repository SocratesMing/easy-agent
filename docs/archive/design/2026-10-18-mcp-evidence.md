# MCP 工具调用证据信封（查询证据链 / 减少幻觉）

| 项 | 内容 |
| --- | --- |
| 状态 | 已实现（本分支） |
| 日期 | 2026-10-18 |
| 范围 | `easy_agent/services/mcp.py`、新增 `easy_agent/services/mcp_evidence.py` |
| 依据 | 审计 FW-14：MCP server 可能把错误包装成文本成功；MCP 调用缺少统一的来源/结果证据 |
| 约束 | 不改工具名、description、`args_schema`、调用语义；不改 `agent.py` 装配、HITL 策略与前端 |

## 1. 问题

MCP 工具经 `langchain-mcp-adapters` 转成 LangChain `BaseTool`，再交给 DeepAgents。
模型看到的是工具返回的文本/内容块，但不知道：

- 这条数据来自哪个 MCP server、哪个工具；
- 是成功结果还是 server 返回的错误文本；
- 结果是否在进入上下文前被截断；
- 调用发生的时间。

当 server 把错误包装成 `isError=false` 的普通文本时（FW-14），模型更容易把
「服务不可用 / SQL 报错 / 上游 500」当成事实继续推理。本设计不改变工具契约，
只在每次调用的结果前附加一段机器可读、模型可读的**证据信封**。

## 2. 目标与非目标

**目标**

1. 每次 MCP 工具调用都带 `server / tool / ok / is_error / fetched_at / truncated / content`
   等证据字段。
2. 工具异常时返回明确的 `is_error=true` 结果并保留错误原文，而不是让异常静默变成
   普通文本或被框架统一改写。
3. 超长结果有上限并显式标记 `truncated=true`，避免单次 MCP 返回挤占上下文。
4. 包装对同一工具幂等；不改工具名、description、参数 schema 和异步调用方式。

**非目标**

- 不校验 MCP 返回内容的业务真伪，不替代人工/工具级校验。
- 不改 `get_mcp_tools()` 的筛选、缓存、连接器开关语义。
- 不改 HITL 审批匹配（工具名保持不变）和前端展示。
- 不做 MCP 结果的持久化证据库；信封只进入本轮 ToolMessage 上下文。

## 3. 证据信封字段

`easy_agent/services/mcp_evidence.py` 中的 `MCPEvidence`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `server` | `str` | MCP server 名（`mcp.json` 的 key） |
| `tool` | `str` | 工具名（与 `BaseTool.name` 一致） |
| `ok` | `bool` | 调用是否成功；当前定义为 `not is_error` |
| `is_error` | `bool` | 工具异常、`ToolMessage.status == "error"`，或文本启发式命中错误 |
| `fetched_at` | `str` | 调用完成时的 UTC 时间，ISO 8601，如 `2026-10-18T00:27:01Z` |
| `truncated` | `bool` | 文本内容是否被截断 |
| `content` | `Any` | 截断后的原始结果（字符串或多模态内容块列表），**不含**来源前缀 |
| `error` | `str` | 错误原文（成功时为空） |
| `error_from_text` | `bool` | 是否由文本启发式判定为错误（而非异常/status） |

对模型可见的前缀（`source_marker()`）示例：

```text
[MCP source: demo/echo; fetched_at=2026-10-18T00:27:01Z; ok=true; truncated=false; is_error=false]
```

文本启发式命中时追加 `; error_source=text`。`server` / `tool` 中的换行、`]`
等字符会被清洗，避免破坏前缀结构。

`content` / `error` 同时保留在 `MCPEvidence` 上，便于测试与后续若要做日志/审计时
直接读取；目前不新增数据库写入。

## 4. 包装方式

### 4.1 包装器

`wrap_mcp_tool(tool, *, server)` 返回 `_MCPEvidenceTool`（`BaseTool` 子类）：

- `name`、`description`、`args_schema` 原样复制；
- `response_format` 原样复制（`content` / `content_and_artifact`），因此 MCP 的
  `structuredContent` artifact 仍能随 `ToolMessage` 传给下游；
- `return_direct`、`metadata`、`tags`、`extras` 等可选字段尽量复制；
- `inner_tool` 以 `Field(exclude=True)` 持有原工具，不参与序列化。

`wrap_mcp_tools(tools_by_server)` 按 server 映射批量包装，返回 `list[BaseTool]`，
顺序与映射内工具顺序一致。

### 4.2 同步 / 异步

- `_run` 调原工具的 `invoke`；
- `_arun` `await` 原工具的 `ainvoke`；
- 包装器只负责加前缀、判错、截断，参数校验仍由包装器的 `args_schema`（即原
  schema）在 `BaseTool` 层完成；
- 对只有异步实现的 MCP 工具，DeepAgents 走 `ainvoke`，行为不变。

为拿到 `content_and_artifact` 的 artifact，调用原工具时传入一个带内部 id 的
标准 ToolCall（`{"type": "tool_call", "name": ..., "args": ..., "id": "mcp-evidence-..."}`），
从返回的 `ToolMessage` 中取 `content`、`artifact`、`status`。该 id 只用于内部
解包，不会替换外层真实 `tool_call_id`。

### 4.3 结果渲染

- 字符串结果：`marker + "\n" + 原文`；
- 内容块列表：在列表最前面插入一个 `{"type": "text", "text": marker}` 块，原有
  文本/图片/文件块保持不变；
- 纯字符串列表：转换为文本块列表后同样前置 marker；
- `None` / 其他对象：转为文本后前置 marker。

模型最终看到的是 LangGraph `ToolNode` 生成的 `ToolMessage.content`，即「来源标记
+ 结果」；工具名和参数 schema 未变。

### 4.4 幂等

- `wrap_mcp_tool` 检测到参数已经是 `_MCPEvidenceTool` 时直接返回，不重复加前缀；
- `get_mcp_tools()` 的缓存继续保存**原始工具**，每次返回前包装。即使缓存命中后
  多次调用 `get_mcp_tools()`，每次拿到的新包装器对同一次工具调用也只产生一个前缀；
- 同一工具被不同 server 重复包装时保留第一次的 server 归属并记 warning。

## 5. 错误处理

1. **异常**：捕获 `Exception`（不捕获 `CancelledError` / `KeyboardInterrupt`），
   返回：
   ```text
   [MCP source: srv/tool; fetched_at=...; is_error=true; ok=false; truncated=false]
   MCP tool call failed: <错误原文>
   ```
   `MCPEvidence.error` 保存原文，错误文本同样受截断上限约束。
2. **`ToolMessage.status == "error"`**：标记 `is_error=true`，内容保留。
3. **文本包装错误（FW-14）**：对结果首个非空行做保守启发式匹配：
   - `Error:` / `Exception:` / `Failed:` / `Failure:` / `Traceback (most recent call last)`；
   - `MCP error ...`、`Failed to ...`、`Error occurred/while/when ...`；
   - 中文 `错误：` / `失败：` / `异常：` / `报错：`；
   - JSON 首行包含 `isError: true`、`success: false`、`ok: false`、`status: "error"` 或非空 `error`。
   命中时 `is_error=true`、`error_from_text=true`，**不改动原文**。这是启发式，
   宁可多标也不让 server 错误伪装成成功；误报在文档和测试中明确。

`ok` 始终等于 `not is_error`，因此异常/文本错误都会出现 `ok=false`。

## 6. 截断处理

- 默认上限 `DEFAULT_MAX_CONTENT_CHARS = 20000` 字符，可用环境变量
  `EASY_MCP_MAX_CONTENT_CHARS` 覆盖（正整数）；
- 字符串直接截断；内容块列表只累计/截断文本块，图片、文件等二进制块不截断
  （截断 base64 会损坏数据）；
- 截断后追加：
  ```text
  ...[MCP output truncated: N chars omitted]
  ```
- `MCPEvidence.truncated=true`，marker 中同步显示 `truncated=true`；
- 截断只影响进入模型上下文的内容，不修改 MCP server 的原始返回，也不写入磁盘。

## 7. 对模型上下文 / 工具调用的影响

- **上下文增长**：每次调用增加约 100–130 字符前缀；默认上限 20k 字符，相比不限制
  的 MCP 长返回是净减少。
- **工具选择**：工具名、description、args_schema 完全不变，模型看到的工具列表和
  参数约束不变；HITL 按工具名匹配也不受影响。
- **结果语义**：原文保留在前缀之后；多模态块保留。模型会多看到一行来源信息，
  不影响原有内容解析。
- **多轮/缓存**：每次 `get_mcp_tools()` 返回新的轻量包装器，底层工具对象和 MCP
  client 缓存不变；包装器无状态，可安全用于并发调用。
- **回调**：包装器通过原工具的 `invoke` / `ainvoke` 委托，callback 型消费者可能
  看到外层与内层两次工具事件；本仓库流式链路基于 LangGraph `astream` 的
  `updates`，不使用工具 callback 生成 SSE，因此不影响现有 `tool_call` /
  `tool_result` 事件。

## 8. 集成点

`easy_agent/services/mcp.py:get_mcp_tools()` 在完成 server 筛选、缓存补差、
内建工具同名过滤、跨 server 重名去重之后，把每个 server 的工具收集为
`tools_by_server`，最后：

```python
return wrap_mcp_tools(tools_by_server)
```

`_mcp_tools_cache` 仍存原始工具，避免包装器跨请求复用导致 server 归属错乱；
`invalidate_mcp_cache()` 语义不变。

## 9. 测试策略

新增 `tests/services/test_mcp_evidence.py`，全部离线：

1. 工具契约：包装后 `name` / `description` / `args_schema` 不变；
2. 同步成功：前缀含 `server/tool`、`fetched_at`、`is_error=false`、`ok=true`，
   原文仍在；
3. 异步成功：`ainvoke` 与同步一致；
4. ToolCall 形式调用：返回 `ToolMessage`，`content` 含前缀，`tool_call_id` 保留；
5. 同步/异步异常：`is_error=true`、`ok=false`，错误原文保留；
6. 文本包装错误：`error_source=text`，原文保留；
7. 幂等：重复包装返回同一对象，内容中只出现一次 `[MCP source:`；
8. 多 server：每个工具带自己的 `server/tool`；
9. 截断：小上限下 `truncated=true`、出现截断提示、总长度受控；
10. 多模态：文本块前插 marker，image 块保留；
11. artifact：`content_and_artifact` 工具的 artifact 仍随 `ToolMessage` 返回；
12. `get_mcp_tools()` 集成：假 MCP client + 临时 `mcp.json`，验证每个 server 的
    工具都被包装，且缓存命中后不重复加前缀。

相关回归命令（仅相关文件，最终由 Lead 跑全量）：

```bash
pytest tests/services/test_mcp_evidence.py \
       tests/services/test_mcp_transport.py \
       tests/services/test_mcp_config.py -q
```

## 10. 风险与限制

- **文本启发式误报**：正文以 `Error:` / `失败：` 开头的正常数据会被标成
  `is_error=true`。原文不变，模型仍可自行判断；这是为 FW-14 做的保守取舍。
- **20k 上限可能截断大表格/长 JSON**：需要完整结果的场景可调高
  `EASY_MCP_MAX_CONTENT_CHARS`；上限只作用于文本块。
- **artifact 解包依赖原工具的标准 `invoke/ainvoke` 行为**：若某工具完全自定义
  `invoke` 且不返回 `ToolMessage`，artifact 可能取不到（内容仍正常）。
- **非 MCP 的特殊返回对象**（如 LangGraph `Command`）不做前缀改写，直接透传，
  避免破坏控制流；本仓库当前未给 MCP 配置 interceptor，不会产生该路径。
- **`BaseTool` 子类字段复制**：只复制已知可选字段；第三方工具若依赖未列出的
  自定义字段，包装器不会带上（工具名/schema/调用仍正常）。
- 本次未跑全量测试（按分工由 Lead 统一执行）；相关三个测试文件与
  `py_compile` / `ruff check` 结果见交付报告。