# Increment 59 · C3b 主聊天编排器 SSE 站点迁移报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 结论 | **完成**：`services/streaming/orchestrator.py` 的 16 个 frame 站点全部迁移到 `SSEEmitter`；源码中 `format_sse` 的 17 处文本（16 个 `yield` 站点 + 1 行 import）清零，`data:` 手写帧为 0 |
| 范围 | 仅后端；未改前端、事件名/字段/顺序；未改 `services/sse.py`、`services/sse_emitter.py`、`streaming/__init__.py`；未改 API/agent/db/checkpoints/mcp/conftest；未 commit；未执行 Batch 0/1 |
| 设计 | 沿用 `docs/design/2026-10-18-c3-sse-producers.md`（C3 已落地） |
| 上游 | C3 已迁移 `api/chat.py`（3）、`api/expert.py`（4）、`knowledges/chat/streaming.py`（1），共 8/24；本批补齐编排器 16/24 |

## 1. 结论

- **站点数口径（诚实说明）**：迁移前 `orchestrator.py` 中 `format_sse` 文本共出现 **17 次** = 16 个 `yield format_sse(...)` 帧站点 + 1 行 `from ..sse import SSEEvent, format_sse`。迁移后两者均为 **0**；实际迁移的帧站点为 **16**（与 C3 报告口径一致，父任务所述 17 应是把 import 行计入了文本出现次数）。
- 迁移后 16 个站点全部为 `yield emitter.<method>(...)` / `yield emitter.frame(...)` / `yield emitter.emit(...)`；编排器不再 import `format_sse`，也不再出现 `"data:"`。
- **wire 不变**：事件名集合、字段名、字段顺序、JSON 序列化字节均与迁移前一致。一次性差分验证覆盖 8 个场景、34 帧，**0 字节差异**。
- 全仓 SSE frame 组装点收敛：C3 的 8 个 + 本批 16 个 = **24/24** 都经 `SSEEmitter`；`services/sse.py:format_sse` 仍是唯一序列化实现。
- 前端零改动；未新增/删除/重命名任何事件；未 commit。

## 2. 交付物

| 文件 | 变更 |
| --- | --- |
| `easy_agent/services/streaming/orchestrator.py` | 16 个 frame 站点迁移到 `SSEEmitter`；每个流创建一个绑定 `session_id` 的 emitter；`finalize_stream` 增加可选 `emitter` 参数（缺省按 `session_id` 自建）；删除 `format_sse` import |
| `tests/services/test_stream_split_golden.py` | 增强 chat / resume golden：done 帧与 approval_required 帧的**逐字节**断言 + 键序断言；新增 resume 嵌套中断 golden（blocks 在 action_requests 之前） |
| `tests/services/test_sse_contract.py` | 新增 2 项：编排器无 `data:`/`format_sse`、事件名集合固定为 6 个且 ⊆ 注册表 |
| `docs/audit/2026-10-18-increment59-c3b-report.md` | 本报告 |
| `easy_agent/services/sse_emitter.py` | **未改**：现有快捷方法已覆盖全部站点，无需补方法（任务允许但非必需） |
| `easy_agent/services/streaming/__init__.py` | **未改**：惰性重导出面不变 |

## 3. 站点映射（16/16）

| # | 行号 | 场景 | 迁移后 | 键序（type 固定在前） |
| --- | --- | --- | --- | --- |
| 1 | 430 | `finalize_stream` done（chat/resume 共用） | `emitter.done(session_id=session_id, **done_fields)` | `type → session_id → elapsed_time → usage`（resume 追加 `blocks` 于末尾） |
| 2 | 479 | chat start | `emitter.start()` | `type → session_id` |
| 3 | 539 | chat 处理器事件透传 | `emitter.frame(ev)` | 原样（payload dict 插入序） |
| 4 | 546 | chat `_end_thinking` | `emitter.frame(_ev)` | 原样 |
| 5 | 553 | chat content_end | `emitter.content_end()` | `type → content` |
| 6 | 573 | chat HITL 中断 | `emitter.approval_required(...)` | `type → thread_id → action_requests` |
| 7 | 628 | chat 取消 | `emitter.error("请求被取消")` | `type → content` |
| 8 | 640 | chat 异常中的思考收尾 | `emitter.emit(SSEEvent.THINKING_END, duration=..., step=...)` | `type → duration → step` |
| 9 | 649 | chat 异常 | `emitter.error(f"处理失败: {str(e)}")` | `type → content` |
| 10 | 670 | resume start | `emitter.start()` | `type → session_id` |
| 11 | 773 | resume 处理器事件透传 | `emitter.frame(ev)` | 原样 |
| 12 | 778 | resume `_end_thinking` | `emitter.frame(_ev)` | 原样 |
| 13 | 790 | resume content_end | `emitter.content_end()` | `type → content` |
| 14 | 809 | resume 嵌套 HITL 中断 | `emitter.emit(SSEEvent.APPROVAL_REQUIRED, thread_id=..., blocks=..., action_requests=...)` | `type → thread_id → blocks → action_requests`（**显式排序**） |
| 15 | 873 | resume 取消 | `emitter.error("请求被取消")` | `type → content` |
| 16 | 886 | resume 异常 | `emitter.error(f"处理失败: {str(e)}")` | `type → content` |

`done` 的 `done_fields` 按 `elapsed_time → usage` 构造，resume 时再追加 `"blocks"`；`SSEEmitter.done` 内部按 `session_id → **fields` 的顺序合并，因此 chat 为 4 键、resume 为 5 键且 `blocks` 在最后。

## 4. 键序契约与实现选择

1. **chat done（无 blocks、usage 无 elapsed_time）**：`done_fields` 只有 `elapsed_time`/`usage`；`usage` 的 7 键由 `build_usage_payload` 决定，未改。
2. **resume done（带权威 blocks、usage 含 elapsed_time）**：`usage` 的 `elapsed_time` 由 `build_usage_payload` 插在 `context_tokens` 之后、`step_count` 之前；`blocks` 追加在 `usage` 之后。golden 锁死为 `["type","session_id","elapsed_time","usage","blocks"]`。
   `done` 调用显式传 `session_id=session_id`（而非只依赖 emitter 绑定），避免传入 emitter 绑定了其它会话时发出错误标识；键序与字节不变。
3. **chat approval_required**：走快捷方法，键序 `type → thread_id → action_requests`，与原字面量一致。
4. **resume approval_required**：快捷方法会把 `action_requests` 排在 `blocks` 之前，而既有前端契约要求 **blocks 在前**。因此该站点**不使用快捷方法**，改用 `emitter.emit()` 显式传参；golden 锁死 `["type","thread_id","blocks","action_requests"]`，源码注释也写明「不得改用 `approval_required()`」。
5. **thinking_end（chat 异常路径）**：字段为 `duration`/`step`，没有匹配的快捷方法，使用 `emit()` 保持 `type → duration → step`。
6. **frame 透传**：`emitter.frame(ev)` 只做 `dict(payload)` + `format_sse`，不重排字段；`stream_processor` 产出的事件 dict 未改。
7. **为何不用 `sse_stream`**：`sse_stream` 只负责把「纯事件 dict 的可迭代对象」包成 frame 流；编排器在帧之间还要落库、检测 HITL、执行收尾副作用，事件不是纯 iterable，强行套壳会改变时序与异常语义。因此按站点使用 `SSEEmitter` 的 `frame`/快捷方法/`emit`；`sse_stream` 仍用于知识适配器等纯包装场景（C3 已落地）。

## 5. 字节等价证据

### 5.1 一次性差分验证（迁移前 vs 迁移后）

方法：把本批 17 条字面量替换**反向**套用到迁移后的文件，精确重建 C3b 之前的 `orchestrator.py`（含 `finalize_stream`），以 `easy_agent.services.streaming._old_orchestrator` 载入内存（不写入仓库），对同一套 fake Agent/DB/Processor 分别跑两条流；把 `time.time` 冻结为常量，使 `elapsed_time` 可比较；逐帧比较**原始字符串**。

| 场景 | 覆盖的站点 | 帧数 | 结果 |
| --- | --- | --- | --- |
| `chat_normal`（思考/正文/工具/todo/两步） | start、frame(ev)、content_end、done | 12 | 字节一致 |
| `chat_hitl` | approval_required(chat)、done 前 return | 5 | 字节一致 |
| `chat_exception_thinking` | thinking_end(emit)、error | 3 | 字节一致 |
| `chat_cancel` | error（取消） | 2 | 字节一致 |
| `resume_normal` | start、frame(ev)、content_end、done(blocks) | 4 | 字节一致 |
| `resume_nested_hitl` | approval_required(resume，blocks 在前) | 4 | 字节一致 |
| `resume_exception` | error（异常） | 2 | 字节一致 |
| `resume_cancel` | error（取消） | 2 | 字节一致 |

**合计：8 场景 / 34 帧 / 0 差异。** 该差分脚本为一次性验证（位于仓库外临时目录），不随代码提交；常驻证据是 §5.2 的 golden。

### 5.2 常驻 golden（逐字节）

- chat done：`frames[-1] == format_sse({"type":"done","session_id":"s1","elapsed_time":...,"usage":{7 键}})`，并断言 `list(done) == ["type","session_id","elapsed_time","usage"]`、`usage` 7 键顺序、无 `blocks`。
- resume done：`frames[-1] == format_sse({... "usage":{8 键含 elapsed_time}, "blocks": ...})`，并断言 `list(done) == ["type","session_id","elapsed_time","usage","blocks"]`。
- chat approval_required：`frames[-1] == format_sse({"type","thread_id","action_requests"})` + 键序。
- resume 嵌套 approval_required：新增 `test_resume_nested_hitl_golden_blocks_before_action_requests`，断言事件序列 `start → content → content_end → approval_required`、键序 `type → thread_id → blocks → action_requests`、`frames[-1] == format_sse(同序 payload)`。
- start/content_end/error 等快捷帧的逐字节等价由既有 `test_emitter_matches_format_sse_byte_for_byte` 与 `test_emitter_error_done_approval_field_contract` 锁定。

### 5.3 事件名集合不变

- 编排器源码中直接引用的 `SSEEvent` 常量只剩 2 个：`THINKING_END`（chat 异常收尾）与 `APPROVAL_REQUIRED`（resume 显式排序）；
- 其余 4 个事件名由快捷方法产生：`start`/`content_end`/`done`/`error`；快捷方法映射后的事件名集合固定为 6 个，全部 ⊆ `SSE_EVENT_TYPES`（新增断言）；
- 15 个事件的注册表、文档、跨生产者 AST 并集均未变（`test_producer_event_constants_registered_and_all_used` 仍通过）；
- 处理器事件（`thinking*`/`content`/`tool_*`/`todo_list`/`token_usage`）仍经 `frame()` 原样透传，未新增/减少。

### 5.4 测试与门禁（本批实跑）

```text
$ .venv/bin/python -m pytest tests/services/test_stream_*.py \
    tests/api/test_chat.py tests/api/test_expert.py \
    tests/services/test_sse_contract.py -q
180 passed, 2 warnings in 56.82s

$ .venv/bin/python -m pytest tests/services/test_stream_split_golden.py \
    tests/services/test_sse_contract.py -q
30 passed, 1 warning in 0.19s

$ uvx ruff check .
All checks passed!

$ python3 scripts/quality/import_graph.py --check-scc
import graph: 217 modules, 489 internal edges, 0 SCC(s) / 0 modules
[check-scc] OK — 实际 SCC 与白名单完全一致

$ python3 scripts/quality/check_double_write.py
[OK] easy_agent/db/repositories/sessions.py
[OK] easy_agent/db/database.py
[double-write] OK — legacy sessions.messages 写入与 Database 业务方法均已冻结
  Database: 141 methods, 106 pure delegations
```

内部依赖边从 488 → 489（新增 `orchestrator → sse_emitter` 一条），SCC 仍为 0；`orchestrator` 不再依赖 `format_sse`，但 `services.streaming` 包的 `format_sse` 重导出与 `test_stream_compat` 的同一性断言不受影响。

## 6. 未迁移站点 / 风险（诚实清单）

### 6.1 未迁移（不在本批允许清单或无 frame 可迁）

1. `easy_agent/services/stream_processor.py`：只产事件 dict（11 个 `SSEEvent` 常量），不产 frame；编排器用 `frame()` 包一层，未改。若要把 payload 组装也统一，可后续改用 `SSEEmitter.emit()` 或纯工厂函数。
2. `easy_agent/knowledges/chat/chat_bridge.py`：只产 `knowledge_evidence` dict，不产 frame；不在允许清单。
3. `StreamingResponse(media_type="text/event-stream")` + 响应头在 `api/chat.py`（3 处）与 `api/expert.py`（2 处）仍重复 5 次；不影响 JSON 契约，未抽 `sse_response()`。
4. wire 心跳（`: ping`）仍缺位；需前端 `readSSE` 先支持注释行，本批未做。

### 6.2 风险

1. **resume `blocks` 顺序是硬契约**：`SSEEmitter.approval_required()` 的键序是 `action_requests` 在 `blocks` 之前，与 resume 现网顺序相反。本批用 `emit()` 显式排序并在源码注释 + golden 双重锁定；后续若有人「顺手」改成快捷方法，测试会失败。
2. **`done(session_id=session_id, **done_fields)` 依赖 Python 关键字参数顺序**：`done_fields` 的插入序 + `emit(type, session_id=..., **fields)` 的合并序共同决定输出序；golden 已锁死 chat 4 键 / resume 5 键。若未来给 `done` 增加固定字段，必须放在 `session_id` 之后、`**fields` 之前并同步更新 golden。
3. **`finalize_stream` 新增可选参数 `emitter`**：缺省按 `session_id` 自建，直接调用方（既有测试）行为不变；`done` 显式传 `session_id=session_id`，即使传入未绑定 emitter 也不会发错会话标识。`start` 仍依赖 emitter 绑定（两条生成器均以必填 `session_id` 自建，当前不会触发 `ValueError`）。
4. **`emitter.frame()` 会 `dict(payload)` 拷贝**：对现有 dict 事件逐字节等价；若未来传入带自定义序列化的 Mapping，仍需保证插入序。
5. **每个流一个 emitter**：`SSEEmitter` 仅保存 `session_id`/`validate`，无跨帧状态；复用不会改变顺序。`validate` 在生产路径保持默认 `False`，不新增运行时校验成本/失败模式。
6. **未跑全量**：按要求只跑定向子集（180 passed）。全量基线 1335 passed 来自任务说明，本批未重跑全量，故不声称全量仍绿。

## 7. 未完成项（后续）

1. `stream_processor.py` / `chat_bridge.py` 的事件 dict 组装统一（C3c）；
2. 抽 `sse_response(generator)` 统一 5 处响应头（可选）；
3. wire 心跳帧（需前端配合）；
4. 全量 `pytest tests/` 回归（本批按指令未跑）。
