# 子项目 C2：流式流程原生重构设计（Phase 2，混合流式）

- 日期：2026-07-28
- 状态：已批准，待 spec 复核
- 范围：easy-agent 流式流程（`streaming.py` 后端 + `App.vue` 前端事件处理）
- 阶段/方案：C2-Phase2，方案 1（增量混合：`['messages','updates']`，借 DeepAgents/LangGraph 原生 stream_mode）
- 取代：子项目 A（`2026-07-28-streaming-frontend-state-design.md`）--其两个 bug 折叠进本 spec
- 关联：C1（模型流程重构）已完成；本 spec 为「基于 DeepAgents 重构」的流式子系统

## 背景

`streaming.py` 共 2409 行，含两个高度重复的生成器：
- `chat_stream_generator`（442-1595，~1150 行，正常流）
- `resume_stream_generator`（1596-2409，~810 行，HITL 恢复流）

两者共享 5 个几乎相同的内部 helper（`_persist_todos`/`_end_thinking`/`_end_thinking_if_needed`/`_seal_content_block`/`_log_step_start`）与重复的主循环。当前用 `agent.astream(stream_mode='messages')`，手动重组 token chunk 成 blocks、手动追踪 step、手动匹配 tool_call_id。

DeepAgents 0.6.8 构建于 LangGraph，自身不发自定义流式事件；原生能力即 LangGraph `stream_mode`。图节点探查确认：`model`（产出 `AIMessage`，含 `tool_calls` + `usage_metadata`）、`tools`（产出 `ToolMessage`，含 `tool_call_id` + 完整 result）、`TodoListMiddleware.after_model`、`PatchToolCallsMiddleware.before_agent`。

## 问题

1. **复杂度高、重复多**：两生成器 ~2000 行重复逻辑（chunk 重组、step 追踪、工具处理）。
2. **工具处理脆弱**：chunk 级参数累积 + `tool_call_id` 匹配 hack；LangGraph 恢复时 id 重生成导致 `resolved_tid` ≠ `tc_id`，匹配失败 -> 工具块卡"执行中"（子项目 A 问题 3）。
3. **token 用量不实时**：`usage_metadata` 仅在每个模型回合最后 chunk 返回，当前中途 token_usage 事件带 0 值，仅 done/重开才更新（子项目 A 问题 1）。

## 目标与验收

- 用 `updates` 的完整消息取代 chunk 重组与 id 匹配 hack，简化后端并去重两生成器。
- 保留 thinking/content 的逐 token 打字机效果（`messages` 模式）。
- 修复子项目 A 两个 bug：token 用量逐回合实时更新；HITL 工具块结果到达即清"执行中"。
- 前端简化工具匹配、删 `n` 死代码；契约最小改动（事件类型基本不变，tool/usage 语义更清晰）。
- 验收：多步流式中 token/迭代次数逐回合实时更新（无需重开）；工具块中途清除"执行中"、无重复块；HITL 恢复正常；thinking/content 仍逐 token；`streaming.py` 显著瘦身。

## 非目标

- 不改 thinking/content 的 token 流式方式（保留打字机）。
- 不重写 `ChatMessage.vue` 渲染（block 渲染基本不动）。
- 不引入自定义 stream 事件（DeepAgents 不自发，无需）。
- 不改 HITL 审批判定（`_is_destructive_command`/路径提取保留）；不改记忆生成逻辑（仅迁移调用点）。
- 不改模型创建流程（C1 已覆盖）。

## 设计

### 第 1 段：混合流式架构
- 两个生成器的 `agent.astream(stream_mode='messages')` 改为 `stream_mode=['messages', 'updates']`。
- 多模式产出 `(mode, data)` 元组，由分发器分流：
  - `('messages', (chunk, metadata))`：`AIMessageChunk`（reasoning_content/text/tool_call_chunks）。**仅用于 token 级 thinking/content 流式**（保留 `thinking_start/thinking/thinking_end/content/content_end` 现有逻辑）。
  - `('updates', {node: {messages: [...]}})`：完整节点输出，作为权威源：`model` -> `AIMessage`（完整 `tool_calls` + `usage_metadata`）；`tools` -> `ToolMessage`（完整 result + `tool_call_id`）。
- tool_call/tool_result/token_usage/step 边界改由 `updates` 驱动；thinking/content 仍由 `messages` 驱动。
- 两流共用分发器与 updates 处理逻辑（去重）。
- 契约：事件类型基本不变，但 `tool_call`/`tool_result`/`token_usage` 数据来源与语义更清晰（来自完整消息而非拼装）。

### 第 2 段：修复子项目 A 两个 bug + 去重
- **token 实时（A 问题 1）**：`model` 节点 update 的 `AIMessage.usage_metadata` 到达时立即下发 `token_usage`（累计 input/output/total、`context_tokens`=input_tokens、`step_count`=当前步）。逐回合实时更新，替代当前 `_finalize_step`/`_token_event` 回合内部带 0 值的下发。
- **HITL 工具块（A 问题 3）**：`model` 节点 -> `AIMessage.tool_calls`（含 name、完整 args、`id`）下发权威 `tool_call`；`tools` 节点 -> `ToolMessage`（含 `tool_call_id`、完整 result）下发权威 `tool_result`。同一 run 内 `tool_calls[].id` 与 `ToolMessage.tool_call_id` 保证一致（恢复流亦然）-> 前端按 id 精确匹配、即时写 `duration` -> "执行中"中途清除。删除 `tool_call_accumulated_args_str`/`tool_call_index_to_id`/`pending_tool_args_by_index`/chunk 级 `parse_tool_args`/前端 `findToolBlock` 兜底与 auto-create。
- **UX 取舍（已确认）**：工具参数一次性完整出现（不逐 token 成型）；思考/正文仍逐 token。
- **去重**：抽出共享 `StreamProcessor`（统一 updates 分发、messages 的 thinking/content 流式、共享 helper、token_usage 下发）。两生成器变薄封装：正常流（初始空状态 + 末尾记忆生成）、恢复流（DB 载入状态 + 应用审批决策 + resume Command），共用 `StreamProcessor`。

### 第 3 段：前端适配 + 范围/验证
- **前端 `App.vue`**：`tool_call` 处理器简化（完整 args 一次性到达，移除合并分片逻辑）；`tool_result` 匹配收敛为精确 id（移除 name/pending 兜底与 auto-create）；`token_usage` 处理器已就绪、现每回合带真实值；删 `n` 死代码；`thinking/content` 不变；两套 `onChunk`（正常:783/恢复:1263）契约统一后可合并（分阶段：先各自简化，再合并）。
- **`ChatMessage.vue`**：基本不动；`isToolRunning = duration==null && loading` 保留，因 tool_result 现可靠写 duration。
- **范围**：后端 `streaming.py`（共享 `StreamProcessor` + 两薄封装）；前端 `App.vue`（简化/合并 onChunk、删 `n`、简化工具匹配）；两流全覆盖。子项目 A 独立 spec 被取代。

## 涉及文件

- `easy_agent/services/streaming.py`：重写为 `StreamProcessor` + 两薄封装；删除 chunk 重组与 id 匹配 hack；token_usage 改由 updates 驱动。
- `easy_agent/agent.py`：`astream` 调用改 `stream_mode=['messages','updates']`（正常流 :868 附近；恢复流 `agent.agent.astream`）。
- `frontend/src/App.vue`：简化/合并 `onChunk`、删 `n` 死代码、简化工具匹配。
- `frontend/src/components/ChatMessage.vue`：极小或无改动。
- `tests/`：新增 `StreamProcessor` 的 fake-model 单测。

## 验证与测试

- **单元测试（免网络）**：用 `FakeMessagesListChatModel` 驱动新 `StreamProcessor`，断言事件产出--tool_call 完整 args、tool_result 按 id 匹配、token_usage 每回合带真实 usage_metadata、thinking/content token 流式。核心可自动化验证。
- **手动**：多步流式 -> token/迭代次数逐回合实时更新（无需重开）；工具块结果到达即清"执行中"；HITL 审批 -> 恢复 -> 工具块正常、无重复块。
- **回归**：现有流式测试（`test_chat`/`test_v3_streaming` 等需 config）在具备环境时通过。

## 风险与缓解

- 高风险：重写正在工作的流式核心 + 前端。
- 缓解：**分阶段实施**--(1) 后端 `StreamProcessor` 先产出与现有契约基本一致的事件，用 fake-model 单测 + 对现有前端冒烟验证；(2) 再简化/合并前端 onChunk。每阶段独立可验证、可回退。
- `updates` 节点结构以探查为准（`model`/`tools`）；实现期用日志确认 middleware 节点不产生干扰消息。
