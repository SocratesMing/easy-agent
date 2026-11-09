# C2 Streaming 拆分设计（`services/streaming`）

| 项 | 内容 |
| --- | --- |
| 状态 | 已实施（第一批，行为保持） |
| 依据 | C2：`easy_agent/services/streaming.py` 1482 行、`chat_stream_generator` / `resume_stream_generator` 各 400+ 行编排 |
| 目标 | 投影 / 持久化 / 副作用 / 编排分层；`from easy_agent.services.streaming import X` 全部可用；SSE 契约零变化 |
| 非目标 | 不改 API 层、`agent.py`、`agent_manager.py`、`mcp.py`、`app.py`、`checkpoints.py`、DB、conftest、前端；不改事件名/字段/顺序；不动 HITL 策略 |
| 约束 | 不 commit；不跑全量测试；分步实施，风险过高时宁可少抽但保持全绿 |

## 1. 现状与问题

拆分前 `streaming.py`（1482 行）同时承担五类职责：

1. **上下文/用量投影**：模型窗口解析、token usage 载荷、HITL action_requests 投影；
2. **流式编排**：`agent.astream(["messages","updates"])` 消费、SSE 序列化、事件顺序；
3. **持久化**：增量落库节流、thinking/tool/todo 记录、最终消息、审批决策；
4. **收尾副作用**：工作区重命名、技能入库、会话/长期记忆后台更新；
5. **HITL 恢复**：从 DB 载入部分消息、决策回写、blocks/records 去重、resume 载荷。

两条链路（chat / resume）各有一份逐行相同的流式循环与副作用分派，修改时必须双改，
是 C2 的主要维护成本。

## 2. 模块边界

新增包 `easy_agent/services/streaming/`（与同名 `streaming.py` 并存）：

| 模块 | 职责 | 依赖 | 纯度 |
| --- | --- | --- | --- |
| `projector.py` | chunk/状态 → 内部事件载荷与持久化结构：HITL 审批投影、resume 快照、决策映射、records/blocks 去重、usage 载荷、thinking 耗时 | 标准库、`langchain_core.messages.AIMessage` | 纯函数 |
| `persistence.py` | 增量落库节流（字符 + 时间双阈值）、thinking/tool/todo 写入、最终消息、审批决策、调试 context 快照 | `Database` 协议（duck typing）、`stream_messages` | 半纯（只写 DB） |
| `side_effects.py` | 工作区重命名、技能入库/归类、会话级 + 用户级记忆后台更新 | 文件系统、Agent 实例、`agent_manager`、`memory_manager` | 副作用 |
| `orchestrator.py` | 上下文解析、共享流泵、HITL 检测、resume 载荷构造、`chat_stream_generator` / `resume_stream_generator` | 上述三层 + `StreamProcessor` + `hitl_state` + `sse` | 编排 |
| `__init__.py` | 兼容门面：静态重导出 projector/persistence/stream_messages/sse/hitl_state；惰性重导出 orchestrator/side_effects；旧私有名别名 | — | 兼容层 |
| `../streaming.py` | 保留的按文件路径加载垫片（见 §4） | 包 | 兼容层 |

依赖方向是单向的：`orchestrator → {projector, persistence, side_effects} → stream_messages/sse`
（`side_effects` 只依赖 services 内既有模块），不存在子模块反向 import 包 `__init__`。

## 3. 数据流

### 3.1 chat

```
api.chat → chat_stream_generator
  ├─ resolve_chat_context(agent)                  # 窗口 / 自动压缩点 / 日志截断
  ├─ build_context_messages(history, user)        # 既有 stream_messages
  ├─ StreamProcessor + IncrementalPersister
  ├─ pump_stream(agent.astream(["messages","updates"]))
  │    ├─ proc.handle(mode, data) → 内部事件
  │    ├─ tool_result   → persist_tool_result_event
  │    ├─ thinking_end → persist_thinking_event
  │    ├─ todo_list    → persist_todos
  │    ├─ content      → IncrementalPersister.maybe_persist
  │    └─ yield 事件（format_sse 只在生成器边界做）
  ├─ proc._end_thinking() → thinking_end
  ├─ content_end
  ├─ detect_hitl_interruption(...)
  │    ├─ 命中：mark_hitl_pending → 保存 partial → approval_required → return
  │    └─ 未命中：继续
  ├─ build_assistant_message_dict → persist_final_message
  ├─ maybe_rename_workspace → register_session_skills → submit_memory_updates
  └─ done（usage 仅最近一次模型调用）
```

### 3.2 resume

```
api.chat → resume_stream_generator
  ├─ load_resume_snapshot(session.messages)       # 内容/思考/blocks/tool_calls/pending ids
  ├─ normalize_resume_decisions → decision_status_map
  ├─ apply_decision_status_to_{records,blocks}    # 先写内存，最终整条覆盖持久化
  ├─ build_resume_command(aget_state)             # 单/多 interrupt 映射
  ├─ pump_stream(Command(resume=...))             # 与 chat 同一个流泵
  ├─ content_end
  ├─ detect_hitl_interruption(with_approval=True) # 嵌套中断：额外下发 blocks
  │    └─ finally: persist_approval_decisions
  ├─ merge_tool_call_records → finalize_blocks → proc.blocks = blocks
  ├─ build_assistant_message_dict → persist_final_message
  ├─ 收尾副作用（同 chat）
  └─ done（usage 内额外带 elapsed_time，并下发权威 blocks）
```

## 4. 兼容垫片

### 4.1 为什么包优先

同目录同时存在 `streaming/`（含 `__init__.py`）与 `streaming.py` 时，Python
`FileFinder` **先检查同名包**，因此运行时 `easy_agent.services.streaming` 指向
`streaming/__init__.py`。`streaming.py` 已缩为 12 行按文件路径加载的垫片
（`spec_from_file_location` 等工具仍可使用），不含第二份实现。

### 4.2 符号兼容

- 静态重导出：`stream_messages` 全部 7 个符号、`sse.SSEEvent/format_sse/SSE_EVENT_TYPES`、
  `hitl_state` 全部 6 个符号、`projector` 全部公开函数、`persistence` 全部公开函数；
- 旧私有名别名：`_mark_hitl_pending`、`_extract_file_paths_from_command`、
  `_get_model_context_limit`、`_persist_incremental`、`_handle_tool_result_event`、
  `_persist_thinking_event`、`_maybe_rename_workspace`、`_register_session_skills`、
  `_submit_memory_updates`、`_resolve_recorded_path`、`_drop_generated_records`、
  `_auto_assign_skill_category`、`_MEMORY_UPDATE_EXECUTOR` 等；
- 对象身份不变：`streaming.format_sse is sse.format_sse`、
  `streaming.build_context_messages is stream_messages.build_context_messages` 等。

### 4.3 惰性重导出与 import-graph

`streaming/__init__.py` 只静态 import `projector` / `persistence`，对
`orchestrator` / `side_effects` 使用 PEP 562 `__getattr__` 惰性加载：

- 运行期 `services/__init__ → streaming(chat_stream_generator)` 首次访问即加载编排器，
  依赖关系与拆分前一致；
- 静态 import 图不新增 SCC 成员，现有 16 模块环白名单无需扩容（白名单只减不增）；
- `hasattr(streaming, "xxx")` 等探测会触发一次惰性 import，属预期行为。

## 5. 实施步骤

1. **projector**：抽出 HITL 投影 / resume 快照 / 去重 / usage 载荷，纯函数单测；
2. **persistence + side_effects**：抽出节流器、thinking/tool/todo/最终消息、工作区/技能/记忆；
3. **orchestrator**：抽共享 `pump_stream`、`detect_hitl_interruption`、
   `build_resume_command`，把两个生成器迁入包；`streaming.py` 缩为垫片。

本批三步均已完成； orchestrator 内仍保留 chat/resume 两段收尾代码（见 §8）。

## 6. 测试策略

- **golden（新增）**：假 Agent/StreamProcessor/DB 驱动两个公开生成器，断言
  SSE 事件名/顺序/关键字段、增量/最终/HITL 落库载荷、resume 决策合并与权威 blocks；
- **纯函数单测（新增）**：projector 16 个函数、persistence 节流与落库；
- **既有测试**：`test_stream_processor.py`、`test_stream_messages.py`、
  `test_stream_concurrency.py`、`test_hitl_resume.py`、`api/test_chat.py`、`api/test_expert.py`；
- **兼容测试**：`test_stream_compat.py`（旧符号全集 / 对象身份 / 包优先垫片）、
  `test_stream_messages.py::test_streaming_reexports_same_function_objects`、
  `test_memory_executor.py`（`_MEMORY_UPDATE_EXECUTOR`）、
  `test_session_skill_registration.py`（`_register_session_skills`）、
  `test_reasoning_safety.py`（`build_assistant_message_dict`）。

## 7. 回滚策略

- 纯代码移动，无 DB schema / 配置 / 前端改动；
- 回滚：删除 `easy_agent/services/streaming/`，`git checkout -- easy_agent/services/streaming.py`
  并删除新增测试/文档即可；不涉及数据迁移；
- 若只想回滚某一步：projector/persistence 为纯/半纯模块，可先恢复
  `streaming.py` 的对应调用，再逐个删除模块。

## 8. 剩余工作与风险

1. **orchestrator 仍 864 行**：chat/resume 的收尾段（最终消息 + 副作用 + done）仍有
   结构重复，可抽 `finalize_stream(...)`；因 chat 无 `blocks`、resume 的 usage 含
   `elapsed_time` 且 done 带权威 blocks，参数化会触碰事件字段/顺序，风险高于收益，
   本批未做；
2. **HITL 恢复的 blocks/records 去重**依赖 dict 相等与列表 index，属既有逻辑，
   本批保持逐字语义；
3. **惰性导出**对 IDE 静态补全不友好（运行期正常）；如未来 import-graph 白名单
   允许扩容，可改回静态 import；
4. **并发环境**：本批实施期间仓库存在其他改动的并发写入，最终目标测试与门禁均
   在稳定树上通过；后续集成前建议再跑一次目标套件。
