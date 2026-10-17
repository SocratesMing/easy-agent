# SSE 事件契约（ARCH-05）

> 唯一事实来源：`easy_agent/services/sse.py` 的 `SSE_EVENT_TYPES`。
> 本文件由契约测试 `tests/services/test_sse_contract.py` 与代码同步校验。

| type | 触发 | 关键字段 | 消费端 |
| --- | --- | --- | --- |
| `start` | 流开始 | `session_id` | chat / expert / knowledge |
| `thinking_start` | 进入思考 | `step` | chat |
| `thinking` | 思考增量 | `content`（增量）、`full_content`（节流快照，可缺省）、`step` | chat |
| `thinking_end` | 思考结束 | `duration`、`step` | chat |
| `content` | 正文增量 | `content`、`step` | chat / expert / knowledge |
| `content_end` | 正文结束 | `content`（通常空） | chat |
| `tool_call` | 完整工具调用 | `tool_name`、`tool_call_id`、`arguments`、`step` | chat / expert |
| `tool_call_delta` | 工具参数流 | `tool_call_id`、`delta`、`step` | chat |
| `tool_result` | 工具结果 | `tool_name`、`tool_call_id`、`result`、`success`、`duration`、`step` | chat / expert |
| `todo_list` | 待办更新 | `todos`、`step` | chat |
| `token_usage` | 用量更新 | `input_tokens` / `output_tokens` / `reasoning_tokens` / `context_tokens` | chat |
| `knowledge_evidence` | 知识库引用 | `items` / `session_id` | knowledge |
| `approval_required` | HITL 中断 | `thread_id`、`action_requests`、`review_configs` | chat |
| `done` | 正常结束 | `session_id`、`message_id`、`finish_reason` | 全部 |
| `error` | 失败/取消 | `content` | 全部 |

## 约束

1. 新增事件：先改 `SSE_EVENT_TYPES` → 更新本文件 → 改生产端 → 改前端 handler → 契约测试。
2. 禁止在业务代码里手写 `"data: "` 拼接；统一用 `format_sse()`。
3. `full_content` 允许缺省（前端为增量 append 语义）；其余字段变更需保持向后兼容至少一个版本。
