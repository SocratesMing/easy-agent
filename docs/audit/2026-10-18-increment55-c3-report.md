# Increment 55 · C3 后端 SSE 生产者并一报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 结论 | 部分完成（诚实口径）：新增统一入口与带字段契约的事件注册表；`api/chat.py`、`api/expert.py`、`knowledges/chat/streaming.py` 已迁移；主聊天编排器 `services/streaming/orchestrator.py`（16/24 个 frame 站点）**未迁移**，因其不在本批允许改动清单内 |
| 范围 | 仅后端 SSE 生产/格式段；未改前端、`agent.py`、`agent_manager`、DB、checkpoints、MCP、conftest；未回退 wave1 未提交改动；未 commit |
| 设计 | `docs/design/2026-10-18-c3-sse-producers.md` |

## 1. 结论 / 并一程度

| 指标 | 迁移前 | 迁移后 |
| --- | --- | --- |
| 直接 `format_sse(...)` frame 站点 | 24（编排器 16 + chat API 3 + 专家拒答 4 + 知识适配器 1） | 16（全部在编排器）；**已并一 8/24 ≈ 33%** |
| 拥有独立 frame 生产逻辑的模块 | 4 | 2 个完全走统一入口（chat API、专家拒答）+ 1 个部分（知识适配器）；编排器原样 |
| 手写 `data: ` 帧解析 | 1 处（知识适配器 `removeprefix`） | 0（下沉到 `sse.parse_sse_frame`） |
| terminal 事件字面量集合 | 1 处（知识适配器） | 0（改由注册表 `TERMINAL_EVENT_TYPES` 派生） |
| `start` / `content` / `content_end` / `error` / `done` dict 字面量 | chat 3 + expert 4 处 | 0（改走 `SSEEmitter` 快捷方法，键序不变） |
| 带 `required`/`optional`/`terminal` 的事件注册表 | 无（只有扁平事件名集合 + 7 项必填） | 15 个事件全量登记 |
| 契约测试 | 5 项 | 23 项（`test_sse_contract.py`），另 +1 项知识适配器测试 |

一句话：**序列化与事件名早已单一，本次把「帧组装、终态语义、帧解析」也收口到 `services/sse.py` + `services/sse_emitter.py`；主聊天编排器这一大块仍是旧写法。**

## 2. 交付物

| 文件 | 变更 |
| --- | --- |
| `easy_agent/services/sse.py` | 新增 `SSEEventSpec` / `EVENT_SPECS` / `TERMINAL_EVENT_TYPES` / `parse_sse_frame` / `is_terminal_event`；`SSE_EVENT_TYPES` 改为由注册表派生；`format_sse` 实现与签名未动 |
| `easy_agent/services/sse_emitter.py` | 新增统一生产入口 `SSEEmitter`（`frame`/`emit`/`start`/`content`/`content_end`/`error`/`done`/`approval_required`/`knowledge_evidence`）与 `sse_stream` |
| `easy_agent/api/chat.py` | 3 个孤立帧改 `SSEEmitter`：并发兜底 `error`、后台异常广播 `error`、重连 `done(already_completed=True)`；去掉对 `services.streaming.format_sse` 的依赖 |
| `easy_agent/api/expert.py` | `_refusal_event_stream` 4 帧改 `emitter.start/content/content_end/done`；`done.usage` 7 键清单原样保留 |
| `easy_agent/knowledges/chat/streaming.py` | 首帧注入改 `sse_stream(...)`；终态判定改 `is_terminal_event(frame)`；删除 `json`/`data:` 解析/字面量集合 |
| `tests/services/test_sse_contract.py` | 由 5 项扩到 23 项：注册表=常量=文档、必填快照、终态、生产者无手写 frame、AST 常量覆盖、`SSEEmitter`/`sse_stream` 契约、三个迁移点逐字节等价 |
| `tests/knowledges/test_chat_session.py` | 新增 1 项知识适配器测试：首帧注入、宿主帧透传、终态后元数据双写、`aclose` |
| `docs/design/2026-10-18-c3-sse-producers.md` | 设计文档 |
| 本文件 | 审计报告 |

未改 `tests/api/test_expert.py`：既有 `test_stream_run_irrelevant_message_short_circuits` 已端到端断言 `start → content → content_end → done`，更精确的逐字段断言放在 `test_sse_contract.py`，避免重复用例。

## 3. 未迁移生产者（诚实清单）

1. **`services/streaming/orchestrator.py`（P1，16 个 `format_sse` 站点）**：不在本批允许改动清单。仍是 `yield format_sse(ev)`；`error` / `approval_required` / `done` 的 dict 字面量、resume 的 `blocks` 先于 `action_requests` 的特殊顺序均未动。后续 C3b 可逐处替换为 `emitter.frame(ev)` / `emitter.emit(...)`。
2. **`services/stream_processor.py`**：只产事件 dict（17 处 `SSEEvent.*`），不产 frame；未改。若要彻底统一 payload 组装，应改用 `SSEEmitter.emit()` 或纯工厂函数。
3. **`knowledges/chat/chat_bridge.py`**：产 `knowledge_evidence` 事件 dict（非 frame）；文件不在允许清单，未改。`SSEEmitter.knowledge_evidence()` 已实现并测试，留待后续替换。
4. **`knowledges/api.py`**：**没有 SSE 生产者**。唯一 `StreamingResponse` 是文档二进制下载（`iter_bytes`），不属于 SSE；因此未改（“仅 SSE 生产/格式段”范围内无内容可改）。
5. `StreamingResponse(media_type="text/event-stream")` + 3 个响应头在 `api/chat.py`（3 处）与 `api/expert.py`（2 处）重复 5 次，本批未抽 `sse_response()`：不影响 JSON 契约，且不在任务列出的重复点清单。

## 4. 行为等价证据

### 4.1 字节级

- `SSEEmitter` 的 6 类快捷帧逐字节等于 `format_sse(旧字面量)`，且 `list(parse_sse_frame(frame)) == list(旧 payload)` 锁定键序；
- `chat._detached_event_stream` 并发兜底帧断言为旧字面量原文：

  ```text
  data: {"type": "error", "content": "本会话正在生成回复，请等待完成或先取消当前生成"}\n\n
  ```

- 专家拒答流断言：事件顺序 `start/content/content_end/done`、`done` 键序 `type/session_id/elapsed_time/usage`、`usage` 7 键顺序与旧实现一致、落库发生在推送之前；
- 知识适配器断言：`frames[1] == format_sse(旧 initial_event)`，事件序列 `start → knowledge_evidence → content → done` 不变，`done` 终态触发一次落库、`finally` 再落一次（共 4 次 blob+row 双写，与迁移前一致），`aclose()` 仍被调用。

### 4.2 注册表 / 事件名

- `SSEEvent` 常量集合 == `SSE_EVENT_TYPES` == `docs/sse-events.md` 表格（15 个）；
- AST 扫描 7 个生产者的 `SSEEvent.<NAME>` 引用：无未定义常量，且并集恰好覆盖全部 15 个事件（无“注册了但无人发”/“发了但没注册”）；
- 生产者源码不再出现事件名字面量（`"type": "done"` 等 12 种）与 `data: {` 手写帧；
- 必填字段快照锁定：`start/thinking/content/tool_call/tool_result/done/error` 与迁移前逐项一致；**新增** `approval_required` 的 `thread_id`/`action_requests` 必填校验——`validate_event` 只在测试/调试调用，wire 行为不变，已在设计与测试注释中说明；
- 诚实边界：`approval_required` 的现网唯一生产者仍是未迁移的编排器，本批只锁定注册表 + `SSEEmitter` 契约，**没有**该事件的运行时逐字节证据；迁移编排器时需补 golden。

### 4.3 测试与门禁

```text
$ .venv/bin/python -m pytest tests/services/test_sse_contract.py \
    tests/api/test_chat.py tests/api/test_expert.py \
    tests/knowledges/test_chat_session.py -q
101 passed, 2 warnings in 58.22s

$ uvx ruff check .
All checks passed!

$ python3 scripts/quality/import_graph.py --check-scc
import graph: 217 modules, 488 internal edges, 0 SCC(s) / 0 modules
[check-scc] OK — 实际 SCC 与白名单完全一致

$ python3 scripts/quality/check_double_write.py
[OK] easy_agent/db/repositories/sessions.py
[OK] easy_agent/db/database.py
[double-write] OK — legacy sessions.messages 写入与 Database 业务方法均已冻结
  Database: 141 methods, 106 pure delegations
```

额外的相邻回归（不在必跑清单，用于确认 `format_sse` 同一性与 golden 未受影响）：

```text
$ pytest tests/services/test_stream_compat.py tests/services/test_stream_split_golden.py \
    tests/api/test_stream_hub.py -q
12 passed
```

### 4.4 兼容性

- `services.streaming` 仍重导出**同一个** `format_sse` 对象（`test_stream_compat` 的 `is` 断言通过）；
- 前端零改动：未新增/删除/重命名事件，未新增非 `data:` 帧；
- 未回退 wave1 未提交改动：本批只新增/修改 §2 列出的文件；工作区同时存在并行批次的其它改动，未触碰、未 commit。

## 5. 风险

1. **编排器仍是主要重复源**：16/24 个 frame 站点未迁移，新增终态字段时仍需改 `orchestrator.py`；`SSEEmitter` 目前对主链路只是“可用”而非“在用”，并一收益有限。
2. **`done`/`start` 缺 `session_id` 会抛 `ValueError`**（迁移前会发出缺字段的帧）。当前 4 个调用点都显式或构造时传入；若未来有生产者省略，会在流开始处暴露而不是静默发出坏帧——这是有意的 fail-fast，但属于新增失败模式。
3. **`validate_event` 对 `approval_required` 变严**：仅测试/调试路径调用；若外部脚本依赖旧行为需注意。
4. **快捷方法的键序是硬编码契约**：`approval_required` 的 `blocks` 目前排在 `action_requests` 之后，而 resume 流要求 `blocks` 在前；迁移编排器时必须用 `emit()` 显式排序，不能直接套快捷方法（设计文档已注明）。
5. **wire 心跳仍缺位**：`_STREAM_HEARTBEAT_SECONDS=300` 只是流锁续约；超长空闲流的代理超时风险与迁移前相同，本批未引入 `: ping`（需前端同步改造）。
6. **`sse_stream` 对字符串入参显式 `TypeError`**，避免把 str 当事件可迭代对象逐字符序列化；已有测试覆盖。

## 6. 未完成项（后续）

1. C3b：迁移 `orchestrator.py` 的 16 个站点（注意 resume 的 `blocks` 顺序、`done` 的 chat/resume 差异）；
2. `stream_processor.py` / `chat_bridge.py` 的 payload dict 改用 `SSEEmitter`/工厂；
3. 抽 `sse_response(generator)` 统一 5 处响应头（可选）；
4. wire 心跳帧：需前端 `readSSE` 先支持注释行，再评估。
