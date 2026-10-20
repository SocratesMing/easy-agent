# Increment 45 执行报告（C2：streaming 拆分第一批）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-18（C2 子任务） |
| 依据 | `docs/design/2026-10-18-c2-streaming-split.md`；C2：`streaming.py` 1482 行、chat/resume 各 400+ 行编排 |
| 约束 | 仅改 `easy_agent/services/streaming.py`、新增 `easy_agent/services/streaming/` 包、新增 `tests/services/test_stream_*`、新增设计/本报告；**未改** API 层、`agent.py`、`agent_manager.py`、`mcp.py`、`app.py`、`checkpoints.py`、DB、conftest、前端；未 commit；未跑全量 |
| 结论 | **projector / persistence / side_effects 三层已抽出，chat/resume 迁入 orchestrator 并共享流泵/HITL/resume 载荷**；SSE 事件名/字段/顺序未变；`from easy_agent.services.streaming import X` 全部可用；目标测试 **122 passed（与拆分前基线一致）**；新增 C2 用例 **23 passed**；关联兼容用例 **45 passed**；`ruff` 与 `import_graph --check-scc` 通过 |

## 1. 改动概览与行数

| 文件 | 行数 | 说明 |
| --- | ---: | --- |
| `easy_agent/services/streaming.py`（前） | 1482 | 单体编排 |
| `easy_agent/services/streaming.py`（后） | 12 | 兼容垫片：包优先，文件保留供按路径加载 |
| `easy_agent/services/streaming/__init__.py` | 142 | 兼容门面：静态 + 惰性重导出、旧私有名别名 |
| `easy_agent/services/streaming/orchestrator.py` | 864 | 公开生成器 + 共享流泵/HITL/resume 载荷/上下文解析 |
| `easy_agent/services/streaming/projector.py` | 409 | 纯投影：HITL、resume 快照、去重、usage |
| `easy_agent/services/streaming/persistence.py` | 316 | 增量节流、thinking/tool/todo、最终消息、审批决策 |
| `easy_agent/services/streaming/side_effects.py` | 302 | 工作区重命名、技能入库、记忆更新 |
| `tests/services/test_stream_split_golden.py` | 562 | 事件序列 + 落库行为 golden |
| `tests/services/test_stream_projector.py` | 285 | projector 纯函数单测 |
| `tests/services/test_stream_persistence.py` | 264 | persistence 节流/落库单测 |
| `tests/services/test_stream_compat.py` | 115 | 旧导入面/对象身份/包优先兼容测试 |

包内合计 2033 行（5 文件），加垫片共 2045 行；比拆分前 +563 行（+38%），主要来自模块
docstring、显式 import、类型标注与兼容别名。编排器本体 864 行（较 1482 行 -618）。

## 2. 抽取清单

### 2.1 `projector.py`（纯函数）

| 函数/类 | 来源 |
| --- | --- |
| `get_model_context_limit` | `_get_model_context_limit` + `_MODEL_CONTEXT_LIMITS` |
| `extract_file_paths_from_command` | 同名私有函数 |
| `build_action_requests_payload` / `mark_hitl_pending` | `_mark_hitl_pending` 拆成载荷构造 + 标记两步 |
| `pending_tool_call_ids_for_approval` | chat/resume 两段重复的 AIMessage 扫描 |
| `ResumeSnapshot` / `load_resume_snapshot` | resume 开头的 DB 消息加载 |
| `last_user_message` | resume 的原始用户消息捕获 |
| `decision_status_map` / `apply_decision_status_to_records` / `apply_decision_status_to_blocks` | resume 决策映射与回写 |
| `merge_tool_call_records` | resume 收尾的工具记录去重 |
| `finalize_blocks` | resume 收尾的 blocks 决策重放 + 去重 |
| `thinking_duration` | chat 的 thinking 耗时汇总 |
| `build_usage_payload` | chat/resume 的 done usage 载荷（resume 多 `elapsed_time`） |

### 2.2 `persistence.py`（半纯）

| 函数/类 | 来源 |
| --- | --- |
| `IncrementalPersister` | chat/resume 各自的字符+时间双阈值节流变量 |
| `persist_incremental`（`_persist_incremental` 别名） | 同名私有函数 |
| `persist_partial` | 取消/异常/HITL 的部分消息保存 |
| `persist_thinking_event`（`_persist_thinking_event` 别名） | 同名私有函数 |
| `persist_tool_result_event`（`_handle_tool_result_event` 别名） | 同名私有函数 |
| `persist_todos` | 两段重复的 todo_list 落库 |
| `persist_final_message` | 两段重复的最终消息 UPDATE |
| `persist_approval_decisions` | resume 的 `_persist_approval_decisions` 闭包 |
| `write_session_context_snapshot` | chat 的 `EASY_WRITE_SESSION_CONTEXT` 调试快照 |

### 2.3 `side_effects.py`

`maybe_rename_workspace`、`resolve_recorded_path`、`drop_generated_records`、
`register_session_skills`、`auto_assign_skill_category`、`submit_memory_updates`、
`_MEMORY_UPDATE_EXECUTOR` 原样迁移，逻辑未改。

### 2.4 `orchestrator.py`

| 函数 | 说明 |
| --- | --- |
| `resolve_chat_context` / `resolve_resume_context` | 原两段上下文窗口解析（口径保持差异：chat 兜底 170000，resume 为 0） |
| `pump_stream` | **统一 chat/resume 的 astream 循环**：tool_result/thinking_end/todo_list/content 副作用分派与增量节流 |
| `detect_hitl_interruption` | **统一两段 HITL 检测**：action_requests、pending ids、partial 消息、pending 标记 |
| `build_resume_command` | 单/多 pending interrupt 的 resume 载荷构造 |
| `chat_stream_generator` / `resume_stream_generator` | 原公开生成器迁入，事件顺序与字段逐字保持 |

## 3. 兼容性

- **包优先**：同目录 `streaming/` 包优先于 `streaming.py`，运行时
  `easy_agent.services.streaming` 指向包 `__init__`；`streaming.py` 仅保留 12 行
  按文件路径加载的垫片，不含第二份实现。
- **符号**：`stream_messages` 7 个符号、`sse` 3 个符号、`hitl_state` 6 个符号、
  projector/persistence 全部公开函数、全部旧私有名（`_mark_hitl_pending`、
  `_persist_incremental`、`_handle_tool_result_event`、`_register_session_skills`、
  `_MEMORY_UPDATE_EXECUTOR` 等）均可从旧路径导入；对象身份保持
  （`format_sse is sse.format_sse`、`build_context_messages is stream_messages.*`）。
- **惰性导出**：`orchestrator` / `side_effects` 通过 PEP 562 `__getattr__` 加载，
  避免新增 import-graph SCC 成员，现有 16 模块白名单无需扩容。
- **SSE 契约**：事件名/字段/顺序未改；chat 的 `done` 仍不带 `blocks`，resume 的
  `done` 仍带权威 `blocks` 且 usage 内含 `elapsed_time`；`approval_required` 的
  `thread_id` / `action_requests` / 嵌套中断 `blocks` 均保持。

## 4. 测试与门禁

运行环境：`.venv/bin/python`（Python 3.11.15）。

| 命令 | 结果 |
| --- | --- |
| `pytest tests/services/test_stream_processor.py tests/services/test_stream_messages.py tests/api/test_chat.py tests/api/test_expert.py -q` | **122 passed**（拆分前基线同为 122 passed） |
| `pytest tests/services/test_stream_projector.py tests/services/test_stream_persistence.py tests/services/test_stream_split_golden.py tests/services/test_hitl_resume.py tests/services/test_memory_executor.py tests/services/test_session_skill_registration.py tests/services/test_streaming_context.py tests/api/test_stream_hub.py tests/test_reasoning_safety.py -q` | **45 passed**（其中 C2 golden/单测 20 个） |
| `pytest tests/services/test_stream_projector.py tests/services/test_stream_persistence.py tests/services/test_stream_split_golden.py tests/services/test_stream_compat.py -q` | **23 passed**（新增 C2 用例全集） |
| `pytest tests/services/test_stream_concurrency.py -q` | **2 passed**（补充回归） |
| `uvx ruff check .` | **All checks passed!** |
| `python3 scripts/quality/import_graph.py --check-scc` | **OK**：211 modules / 474 edges / 1 SCC（16 modules，与白名单完全一致） |

### golden 覆盖

- chat 正常流：`start → thinking_start → thinking → thinking_end → content → tool_call →
  tool_result → todo_list → content → content → content_end → done`，并断言 done usage、
  thinking/tool/todo 落库、最终消息 content/thinking/tool_calls/blocks；
- 流在思考中结束：`_end_thinking` 产出的 `thinking_end` 原样下发；
- chat HITL：`approval_required` 载荷（含 `file_paths`）、partial 消息的
  `approval_status/pending_approval`、不产生 `done`；
- resume：决策 applied 到 blocks/records、`Command(resume=...)`、工具记录/块去重、
  done 下发权威 blocks 且 usage 含 `elapsed_time`。

golden 用例先于拆分落盘并在旧实现上校准事件序列；旧实现下事件名/顺序断言通过，
失败项来自测试替身未同步 state（已在拆分后修正），拆分后 4/4 通过。

## 5. 剩余 orchestrator 工作与风险

1. **chat/resume 收尾段仍有结构重复**（最终消息 + 副作用 + done，约 40 行差异参数）：
   可抽 `finalize_stream(...)`；因 chat done 无 `blocks`、resume usage 含
   `elapsed_time` 且 done 带权威 blocks，参数化会触碰 SSE 字段，本批未做。
2. **HITL 嵌套中断**的 `finally` 审批写回、blocks/records 去重依赖 dict 相等与
   列表 index，属既有语义，保持逐字迁移，未"顺手优化"。
3. **惰性导出的静态可见性**：IDE 补全可能看不到 orchestrator 符号；运行期与
   `from ... import X` 正常。若后续允许扩容 SCC 白名单，可改回静态 import。
4. **并发写入环境**：实施期间仓库存在其他改动的并发写入（`api/expert.py` 一度
   指向不存在的 `easy_agent.repo`，随后恢复）；最终目标测试/门禁在稳定树上通过，
   集成前建议再跑一次目标套件。
