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
| `approval_required` | HITL 中断 | `thread_id`、`action_requests`（全部待审批中断的操作，见下文）；恢复流另带 `blocks` | chat |
| `done` | 正常结束 | `session_id`、`message_id`、`finish_reason` | 全部 |
| `error` | 失败/取消 | `content` | 全部 |

## HITL 审批：`approval_required` 与 `/agent/chat/resume`（DA-02，2026-10-19）

并行的 `task` 子代理各自触发审批时，图里会同时挂着多个中断。`action_requests` 下发**全部**
中断的操作，每项字段：

- `tool_call_id`：对应主消息里的工具调用；子代理内部的操作没有对应调用，为空串（前端在消息级审批面板展示）
- `tool_name` / `arguments` / `description` / `allowed_decisions` / `file_paths`
- `interrupt_id`：所属中断（2026-10-19 新增，追加字段）
- `action_index`：在所属中断里的下标（同上）

恢复请求的 `decisions` 每项可带 `interrupt_id`（与 `action_index`）：只回答这些中断，其余保持
挂起，恢复流结束后会再次下发 `approval_required`。不带 `interrupt_id`（旧前端 / 改造前的记录）
时只回答第一个中断。拒绝决策的 `message` 是回给模型的说明，可省略：省略时（或收到旧前端写死的「请勿重试此删除命令」时）服务端按工具生成，例如「用户拒绝了创建这个定时任务……」（DA-15）。决策与待审批中断对不上（未知 ID、数量或类型不符）时接口返回 400——批处理
HITL 会先保存恢复值再校验，非法决策会让线程卡死，所以必须在恢复前拒绝。

`thread_id` 是**审批句柄**，格式固定为 `{session_id}-{message_id}`（前端据此还原 message_id，
恢复请求原样回传）。DA-03 起开启 `agent.session_thread` 时，图实际跑在 `thread_id = session_id`
的会话 thread 上，由服务端把句柄映射过去；句柄对不上会话当前这一轮（会话已有新消息、审批已处理）
时 `/agent/chat/resume` 返回 409。会话里发新消息即放弃上一轮未处理的审批，DB 里对应的工具调用
标为 `rejected`。

## 约束

1. 新增事件：先改 `SSE_EVENT_TYPES` → 更新本文件 → 改生产端 → 改前端 handler → 契约测试。
2. 禁止在业务代码里手写 `"data: "` 拼接；统一用 `format_sse()`。
3. `full_content` 允许缺省（前端为增量 append 语义）；其余字段变更需保持向后兼容至少一个版本。
