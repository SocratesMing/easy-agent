# SSE 事件契约（ARCH-05）

> 唯一事实来源：`easy_agent/services/streaming/sse.py` 的 `SSE_EVENT_TYPES`。
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
| `tool_call_delta` | 工具参数流 | `tool_name`、`tool_call_id`、`arguments`（可解析的分片快照，非 `delta` 字符串）、`step` | chat / SDK 侧边栏 |
| `tool_result` | 工具结果 | `tool_name`、`tool_call_id`、`result`、`success`、`duration`、`step` | chat / expert |
| `subagent_progress` | 子智能体实时进度 | `tool_call_id`、`subagent_type`、`stage`、`tool_name`、`summary`、`success`、`step` | chat |
| `todo_list` | 待办更新 | `todos`、`step` | chat |
| `token_usage` | 用量更新 | `input_tokens` / `output_tokens` / `reasoning_tokens` / `context_tokens` | chat |
| `knowledge_evidence` | 知识库引用 | `evidence`、`warnings`（旧文档写的 `items` 仅为向后兼容的兼容字段） | knowledge / chat |
| `approval_required` | HITL 中断 | `thread_id`、`action_requests`（全部待审批中断的操作，见下文）；恢复流另带 `blocks` | chat |
| `done` | 正常结束 | `session_id`、`elapsed_time`、`usage`；`blocks` **仅审批续跑（resume）** 下发，是这条消息全部 blocks 的权威快照（前端整体替换内存 blocks，所以思考 / 正文 / 工具结果都不截断，2026-11-12）；`EVENT_SPECS` 另登记可选 `message_id` / `finish_reason` / `already_completed` | 全部 |
| `error` | 失败/取消 | `content` | 全部 |

`done` **不携带** `title` / `content`：普通对话的正文由前端用流内 `content` 增量累积的块拼装，审批续跑时用 `done.blocks` 替换内存块；会话标题由服务端生成后在流结束时通过 `GET /agent/sessions` 回填；前端不应依赖 `done.title` 做本地即时更新。

`token_usage` 与 `done.usage` 里的 `elapsed_time` / `step_count` 是**这条 assistant 消息**的累计值：
审批续跑接着中断前的值往下算（等待审批的时间不计），与落库的 `usage` 一致。前端的会话合计 =
流开始前其余消息的合计 + 本条累计值；续跑时先从会话合计里扣掉这条消息中断前已计入的部分。

`input_tokens` / `output_tokens` 是当前用户、本会话所有已记录模型调用的输入 / 输出累计，
服务端在写入回放前查询逐调用账本，前端按绝对值覆盖；跨轮、重连和审批续跑均不在客户端再次相加。
`reasoning_tokens` / `context_tokens` 仍为最近一次调用的快照。消息落库的 `usage` 保留原始快照，
历史接口顶层 `usage` 再替换输入 / 输出为会话累计。统计规则见 [Token 用量统计](token-usage-api.md)。
错误展示也分层：全局临时提示 5 秒后清空，`message.error` 与 `fallback_note` 随消息保留；
超时使用常驻中性 `fallback_note`。完整约定见 [聊天行为契约](chat-behavior-contract.md)。

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

### 规划 / 头脑风暴确认（2026-11-14）

`ask_user` 是产品级交互工具，只允许 `respond` 决策；它不受 `hitl.enabled=false` 或自定义
`hitl.tools` 名单影响。开启 `hitl.enabled` 时它与安全审批共用同一个
`HumanInTheLoopMiddleware`，因此刷新页面、切换会话和跨副本恢复仍沿用同一套 checkpoint /
`approval_required` / `/agent/chat/resume` 链路。

定时任务等无人值守运行由 `enable_user_interaction=False` 建 Agent，不注入 `ask_user`，
避免自动化任务停在无法人工完成的确认中断上。

`approval_required.action_requests[]` 中的 `ask_user` 项形如：

```json
{
  "tool_call_id": "call-ask-1",
  "tool_name": "ask_user",
  "allowed_decisions": ["respond"],
  "interrupt_id": "int-1",
  "action_index": 0,
  "arguments": {
    "title": "确认三个项目的方案",
    "description": "这些选择会影响后续实现顺序。",
    "questions": [
      {
        "id": "project-a",
        "title": "项目 A 采用哪种方案？",
        "multiple": false,
        "required": true,
        "options": [
          {"id": "fast", "label": "快速版本", "recommended": true},
          {"id": "full", "label": "完整版本", "description": "覆盖更多边界"}
        ]
      }
    ],
    "submit_label": "确认并继续",
    "allow_note": true
  }
}
```

前端通过输入框的 `/plan` 指令切换本轮规划模式。头脑风暴不设专用模式指令，继续使用现有
`brainstorm` 技能。收到 `ask_user` 后，每个问题
渲染为堆叠轮播中的一张独立卡片；当前卡片参与操作，确认后自动滑向下一张，已完成步骤保留在
顶部进度轨道中，点击已完成步骤或按 Esc 可反向滑回。
单选 / 多选在每张卡片标题旁明确标注；候选项按纵向编号列表展示，支持数字键选择、上下键切换、
Enter 确认、Esc 返回上一项。全部必选项完成后，前端调用 `/agent/chat/resume`，
决策为：

```json
{
  "type": "respond",
  "message": "{\"answers\":{\"project-a\":\"fast\"},\"note\":\"先做核心流程\"}",
  "interrupt_id": "int-1",
  "action_index": 0
}
```

`message` 是 JSON 字符串，包含 `answers`（问题 ID → 选项 ID；多选为数组）和可选 `note`。
`respond` 和 `approve` 一样被视为该交互已完成；同一中断若意外混入其他写工具，前端必须把
这些工具转为 `reject`，不能在用户确认规划时顺带执行副作用。

规划确认的恢复请求额外携带：

```json
{
  "confirmation_message": "1. 项目 A：快速版本\n2. 项目 B：Web、移动端",
  "confirmation_message_id": "plan-confirm-...",
  "split_assistant_message": true
}
```

服务端会：

1. 将中断前的 assistant 消息标记为已完成；
2. 根据持久化的 `ask_user` 问题和本次 `respond` 答案重新生成确认文本，作为普通用户消息落库；
   旧客户端传入的 `confirmation_message` 仅作兼容占位，不能决定实际展示内容；
3. 将 `respond` 后的续写保存为新的 assistant 消息，因此刷新后的顺序仍是
   “用户需求 → 规划卡片 → 用户确认 → 后续执行”。

该确认用户消息带 `display_only=true`，只用于界面投影；会话 thread / 模型历史水合会跳过它，
避免把同一组选择同时作为 HumanMessage 和 ToolMessage 再喂给模型。

前端只发送 `interaction_mode=normal|plan`；服务端保留 `brainstorm` 值仅用于旧客户端兼容，
新界面统一使用 `brainstorm` 技能。模式说明只加入本轮模型上下文，不写入用户原话或历史消息。
规划模式要求模型在多个关键分支会显著影响后续工作时，先完成 `ask_user` 逐项确认，再继续执行。

`thread_id` 是**审批句柄**，格式固定为 `{session_id}-{message_id}`（前端据此还原 message_id，
恢复请求原样回传）。DA-03 起开启 `agent.session_thread` 时，图实际跑在 `thread_id = session_id`
的会话 thread 上，由服务端把句柄映射过去；句柄对不上会话当前这一轮（会话已有新消息、审批已处理）
时 `/agent/chat/resume` 返回 409。会话里发新消息即放弃上一轮未处理的审批，DB 里对应的工具调用
标为 `rejected`。

## 传输层心跳（非事件）

`/agent/chat/stream` 与 `/agent/chat/stream/live` 在**空闲超过 `streaming.sse_heartbeat_seconds`**
（config.yaml，默认 15s；环境变量 `EASY_SSE_HEARTBEAT_SECONDS` 优先；`<=0` 关闭）时下发
SSE 注释帧 `: keep-alive\n\n`。它只用于穿过反向代理的空闲
超时（nginx 默认 `proxy_read_timeout 60s`，否则浏览器报 `ERR_INCOMPLETE_CHUNKED_ENCODING
200`），**不是事件**：不进 `hub` 历史、不落 `stream_events` 表；前端 `readSSE` 只处理
`data: ` 行，会自动忽略。

## 约束

1. 新增事件：先改 `SSE_EVENT_TYPES` → 更新本文件 → 改生产端 → 改前端 handler → 契约测试。
2. 禁止在业务代码里手写 `"data: "` 拼接；统一用 `format_sse()`。
3. `full_content` 允许缺省（前端为增量 append 语义）；其余字段变更需保持向后兼容至少一个版本。
