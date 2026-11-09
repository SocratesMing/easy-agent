# C3 后端 SSE 生产者并一设计

| 项 | 内容 |
| --- | --- |
| 状态 | 已实施（主聊天编排器保留原状，见 §7） |
| 依据 | `services/sse.py` 已有注册表/序列化，但 chat / expert / knowledge 仍各自手写事件 dict 与 terminal 判定 |
| 目标 | 建立唯一生产入口（`SSEEmitter` / `sse_stream`）；事件注册表带字段契约；不改变任何事件名、JSON 字段或字段顺序 |
| 非目标 | 不改前端、`agent.py`、`agent_manager`、DB、checkpoints、MCP、conftest；不改主聊天编排器（不在本批允许改动清单内）；不新增事件、不新增 wire 心跳帧 |
| 约束 | 不 commit；不回退 wave1 未提交改动；仅跑指定测试；每个生产者单独迁移、迁移前后字节等价 |

## 1. 现状：四套生产者 + 一个序列化器

| 编号 | 生产者 | 入口 | 当前产出 | 主要重复/漂移 |
| --- | --- | --- | --- | --- |
| P0 | `services/sse.py` | `format_sse` | `data: {json}\n\n` | 唯一序列化实现，但被 API 层经由 `services.streaming` 门面间接引用 |
| P1 | `services/streaming/orchestrator.py` + `stream_processor.py` | `chat_stream_generator` / `resume_stream_generator` | 15 类事件中的绝大多数 | 在生成器边界逐帧 `format_sse(ev)`；`error` / `approval_required` / `done` 的 dict 字面量各写一遍；`done` 在 chat/resume 两处组合 |
| P2 | `api/chat.py` | `_detached_event_stream` / `chat_stream_live` | 3 个孤立帧：并发 `error`、后台异常广播 `error`、重连 `done(already_completed)` | 直接 `format_sse({...})`，与 P1 的终态字段口径无共享定义 |
| P3 | `api/expert.py` | `_refusal_event_stream` | `start → content → content_end → done` 4 帧 | 与 P1 完全同形，但 dict 字面量再写一遍；`done.usage` 字段清单手工维护 |
| P4 | `knowledges/chat/streaming.py` | 知识适配器 | 透传宿主帧 + 插入 `knowledge_evidence` 首帧 | 用 `event.removeprefix("data: ")` 手工解析帧；terminal 集合 `{"done","error","approval_required"}` 为字面量；`format_sse(initial)` 与宿主帧格式无共享入口 |

重复点归并：

1. **`format_sse` 调用**：P2/P3 从 `services.streaming` 门面导入，P4 从 `services.sse` 导入。运行时是同一对象，但 API 层因此静态依赖整个 streaming 门面；
2. **`data: ` 行**：只有 P4 需要反向解析，且解析逻辑与 `format_sse` 的格式知识各存一份；
3. **事件 payload**：`start` / `content` / `content_end` / `error` / `done` 的键序在 P1/P2/P3 重复手写，新增字段时容易只改一处；
4. **异常帧**：`{"type": "error", "content": ...}` 在 `api/chat.py`、`orchestrator.py` 共 6 处字面量；
5. **terminal 判定**：P4 手写 `done/error/approval_required` 集合，注册表没有该语义；
6. **心跳**：`_STREAM_HEARTBEAT_SECONDS = 300` 是**流锁续约**，不是 SSE 帧；wire 层当前没有任何 `: ping` / heartbeat 帧（见 §4）。

另有一处未纳入本批的重复：`media_type="text/event-stream"` + 三个响应头在 `api/chat.py`（3 处）与 `api/expert.py`（2 处）各写一遍。它不影响 JSON 契约，且不属于任务列出的重复点，留待 C3 后续批次（§7）。

## 2. 统一入口

### 2.1 `services/sse.py`：注册表 + 格式知识

新增（保留现有全部导出）：

| 符号 | 语义 |
| --- | --- |
| `SSEEventSpec(required, optional, terminal)` | 单个事件的字段契约（frozen dataclass） |
| `EVENT_SPECS: dict[str, SSEEventSpec]` | 事件注册表：事件名 → 契约；`SSE_EVENT_TYPES = frozenset(EVENT_SPECS)` |
| `TERMINAL_EVENT_TYPES: frozenset[str]` | `{done, error, approval_required}`：一条流合法结束的三种终态 |
| `parse_sse_frame(frame) -> dict` | `format_sse` 的逆操作：剥掉 `data:` 前缀后 `json.loads`，供重连/适配器消费 |
| `is_terminal_event(event) -> bool` | 接受事件 dict 或原始 frame，判断是否终态 |

`format_sse` **不改一行实现**，仍是全仓唯一序列化入口；`validate_event` 仍只校验必填字段（不因新增 optional 字段而变严）。

### 2.2 新增 `services/sse_emitter.py`：唯一生产入口

| API | 说明 |
| --- | --- |
| `SSEEmitter(session_id=None, *, validate=False)` | 绑定默认 `session_id`；`validate=True` 时逐帧走 `validate_event`（测试/调试用，默认关闭以保持生产路径零新增异常） |
| `emitter.frame(payload)` | 序列化任意事件 dict（P1/processor 产出的 payload 直接走这里） |
| `emitter.emit(type, /, **fields)` | 统一入口：`type` 在前，`**fields` 保持调用方顺序 |
| `emitter.start()` / `.content(content, step=?)` / `.content_end(content="")` | 高频事件快捷方式，键序与迁移前字面量逐键一致 |
| `emitter.error(content)` | 终态错误帧；`content` 必填 |
| `emitter.done(session_id=?, **fields)` | 终态完成帧；默认带上构造时的 `session_id`，缺失即 `ValueError`（注册表要求必填） |
| `emitter.approval_required(thread_id, action_requests, **fields)` | HITL 中断帧；两个必填字段在 extra 之前，extra 顺序由调用方决定 |
| `emitter.knowledge_evidence(evidence, warnings)` | 知识证据帧；空证据+空告警时返回 `None`（由调用方决定是否发送） |
| `sse_stream(events, *, emitter=None)` | 把「事件 dict 同步/异步可迭代对象」转成 frame 流；不自动补终态帧 |

字段顺序契约：`dict` 插入序即 wire 序。所有快捷方式都按迁移前字面量的键序构造，调用方需要特殊顺序（如 resume 的 `blocks` 在 `action_requests` 前）时用 `emit()` 显式传参。

## 3. 事件注册表

| type | 必填 | 常用可选 | terminal |
| --- | --- | --- | --- |
| `start` | `session_id` | `message_id` | |
| `thinking_start` | — | `step` | |
| `thinking` | `content` | `full_content`、`step` | |
| `thinking_end` | — | `duration`、`step` | |
| `content` | `content` | `step` | |
| `content_end` | — | `content`、`step` | |
| `tool_call` | `tool_name`、`tool_call_id` | `arguments`、`step` | |
| `tool_call_delta` | — | `tool_call_id`、`delta`、`step` | |
| `tool_result` | `tool_name`、`tool_call_id` | `result`、`success`、`duration`、`step` | |
| `todo_list` | — | `todos`、`step` | |
| `token_usage` | — | `input_tokens`/`output_tokens`/`reasoning_tokens`/`context_tokens`/`step` | |
| `knowledge_evidence` | — | `evidence`、`warnings`、`items`、`session_id` | |
| `approval_required` | `thread_id`、`action_requests` | `blocks`、`review_configs` | ✓ |
| `done` | `session_id` | `message_id`、`finish_reason`、`elapsed_time`、`usage`、`blocks`、`already_completed` | ✓ |
| `error` | `content` | `code` | ✓ |

必填集合在迁移前 `_REQUIRED_FIELDS` 的 7 项基础上补登 `approval_required`（`thread_id` / `action_requests`），逐项由测试锁定。`validate_event` 只在测试/调试路径调用（生产端不调用），因此补登不改变任何 wire 行为；新增的 optional 列只作文档与契约测试依据，不参与运行时强校验。

## 4. 错误 / 心跳 / 关闭语义

### 4.1 终态不变量

- 每条流**至多一个**终态帧，且终态只能是 `done` / `error` / `approval_required`；
- 终态由生产者决定，`SSEEmitter` **不自动补** `done`：知识适配器要透传宿主帧，自动补帧会改变事件序列；
- `approval_required` 是终态但非失败：流到此结束，客户端走 `/agent/chat/resume` 续跑。

### 4.2 错误帧

- 必填 `content`，沿用既有文案：`请求被取消` / `处理失败: {exc}` / 并发提示；
- 生产端异常仍按现状「记日志 + 发 error 帧 + 结束生成器」，不把异常抛回 ASGI（避免 500 覆盖已发出的 SSE 响应头）；
- 不改异常文本、不新增 `code` 字段。

### 4.3 心跳

- **wire 层无心跳**：`_STREAM_HEARTBEAT_SECONDS`（300s）只用于流锁续约；`_StreamHub` 不做周期 ping；
- 本批不引入 `: ping` 注释帧：前端 `readSSE` 目前只处理 `data:` 行，新增帧类型属于 wire 契约变更，必须前端同步改造后再做；
- 长连接的存活依赖反向代理 `X-Accel-Buffering: no` + 业务事件本身；断线由客户端重连 `/stream/live` 回放 hub 历史。

### 4.4 关闭

- `_StreamHub.close()` 广播 `None` 作为 EOF 哨兵，订阅者队列据此结束 async generator；TTL 120s 内迟到重连仍可回放终态；
- `sse_stream` 不吞异常、不 `aclose` 上游；知识适配器维持 `finally: await stream.aclose()` 的既有语义；
- 客户端断开只停止推送，后台任务继续跑完并落库（既有行为）。

## 5. 兼容策略

1. **字节兼容**：`format_sse` 实现不变；`SSEEmitter.frame` 只做 `dict(payload)` + 可选校验后委托它。迁移后的 frame 与迁移前逐字节相等（测试用固定字符串断言）；
2. **符号兼容**：`SSEEvent` / `SSE_EVENT_TYPES` / `format_sse` / `validate_event` 全部保留原导出；`services.streaming` 继续重导出同一 `format_sse` 对象（`test_stream_compat` 锁定 `is` 同一性）；
3. **语义兼容**：事件名、字段名、字段顺序、事件顺序都不变；`validate_event` 仅对 `approval_required` 增补了注册表必填校验（该函数只在测试/调试中调用，wire 行为不变）；
4. **回滚粒度**：`sse.py` / `sse_emitter.py` 是纯增量；任一生产者迁移可单独回退，不影响其它生产者；
5. **前端零改动**：不新增/删除/重命名事件，不新增非 `data:` 帧。

## 6. 逐生产者迁移

| 文件 | 迁移内容 | 等价性证据 |
| --- | --- | --- |
| `api/chat.py` | 3 个孤立帧改用 `SSEEmitter`（`error` / `error` / `done(already_completed=True)`）；去掉对 `services.streaming` 的 `format_sse` 依赖 | 并发分支 frame 与迁移前逐字节相等（契约测试）；`test_chat.py` 全绿 |
| `api/expert.py` | `_refusal_event_stream` 4 帧改用 `emitter.start()/content()/content_end()/done()` | 事件序列与 `done.usage` 键集固定断言；`test_expert.py` 全绿 |
| `knowledges/chat/streaming.py` | 首帧改 `sse_stream(initial_events, emitter=...)`；终态判定改 `is_terminal_event(frame)`；删除手工 `data:` 解析与字面量集合 | 注入/透传/元数据落库时序测试；`test_chat_session.py` 全绿 |
| `services/streaming/orchestrator.py` | **不迁移**（不在允许清单）。保留 `format_sse(ev)` 与终态字面量；由注册表测试保证事件名不漂移 | 源代码扫描 + 既有 golden/兼容测试 |
| `knowledges/api.py` | 无 SSE 生产者（唯一 `StreamingResponse` 是文档二进制下载），不改 | 源代码扫描：无 `data:` / `SSEEvent` |

## 7. 未完成 / 后续

1. P1 编排器与 `stream_processor.py` 的 payload dict 未改用 `emit()`：受本批允许改动清单限制。后续 C3b 可把 `yield format_sse(ev)` 换成 `yield emitter.frame(ev)`，风险低；
2. `StreamingResponse` 头 5 处重复未抽 `sse_response()`：非 JSON 契约且不在任务重复点清单；
3. wire 心跳帧未引入：需前端先行支持；
4. `knowledge_evidence` 的字段名历史上存在 `items`（文档）与 `evidence/warnings`（实现）两种口径，本批保持实现不变，仅在注册表 optional 中同时登记，避免契约测试误报。

## 8. 验收

```bash
python -m pytest tests/services/test_sse_contract.py tests/api/test_chat.py \
  tests/api/test_expert.py tests/knowledges/test_chat_session.py -q
uvx ruff check .
python3 scripts/quality/import_graph.py --check-scc
python3 scripts/quality/check_double_write.py
```
