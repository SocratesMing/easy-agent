# 子项目 A：流式事件与前端状态修复设计

- 日期：2026-07-28
- 状态：已批准，待 spec 复核
- 范围：easy-agent
  - 问题 1：输入框 token 用量与迭代次数不实时更新
  - 问题 3：HITL 审批后工具块卡在「执行中」
- 选定方案：方案 1（最小契约修复）
- 后续：子项目 B（长期记忆增强，问题 2）单独走一轮 spec

## 背景

后端 `easy_agent/services/streaming.py` 以 SSE 下发事件；前端 `frontend/src/App.vue` 的 `onChunk` 分发事件、`frontend/src/components/ChatMessage.vue` 渲染消息块、`frontend/src/components/ChatInput.vue` 显示输入框 token 用量与迭代次数。

存在两套 `onChunk`：
- 正常流 `onChunk`（App.vue:783，由 `sendMessage` 使用）
- HITL 恢复流 `onChunk`（App.vue:1263，由 `resumeStream` 使用）

两者事件处理逻辑不一致，是问题 1 与问题 3 的共因之一。

## 问题陈述

### 问题 1
流式过程中，输入框的 token 用量与迭代次数不随后端 step 迭代实时更新；仅在流结束（done 事件）或重新点开会话（从 DB 重载 `history.usage`）后才显示。

### 问题 3
HITL 审批通过后的恢复流中，工具块卡在「执行中」；后续的思考/正文已正常出现，但该工具块仍显示处理中。

## 根因

### 问题 1（已定位）
- LLM 的 `usage_metadata`（token 计费）仅在每个模型回合的**最后一个 chunk** 返回（streaming.py:888 注释）。当前代码在该处只累加 `total_usage`（streaming.py:890-900），**不立即下发 token_usage 事件**。
- `_finalize_step` / `_token_event` 在回合**内部**转折点触发（streaming.py:610/745/598），此时 `total_usage=0`、`step_count` 取推进前旧值 -> 中途下发的 token_usage 事件带 0 token 且迭代不变化 -> 前端无可见更新。
- 仅 `done` 携带累计 usage，故只有流结束才更新；重开会话走 DB 重载（App.vue:350-355/546-549）也能看到。
- 前端残留一套监听 `n` 事件的死代码（后端从不发 `n`），增加混乱。
- 重要约束：流式 API 不支持逐 token 的 token 用量，token 计费只在每个模型回合结束时才有。因此「实时更新」的现实目标是**逐 step（每个模型回合）更新**，而非逐 token。

### 问题 3（已定位 + 待实现期确认）
- 「执行中」判定 = `block.duration == null && message.loading === true`（ChatMessage.vue:392）。
- 恢复流 `done` 处理器**已**用权威 `data.blocks` 全量替换内存 blocks（App.vue:1508-1509），故流结束状态正确 -> 失败定位在**中途**。
- 恢复生成器 yield 的 `tool_result` 事件用 `resolved_tid`（ToolMessage 的 id，streaming.py:2137），而前端建块用的是 `tc_id`（AIMessage 的 id，streaming.py:1975 附近）。streaming.py 注释已指出 LangGraph 恢复时会重新生成 id，二者可能不一致 -> 前端精确 id 匹配失败，兜底策略（name/pending/auto-create）可能错配或触发自动建重复块 -> 原块 `duration` 恒为 null -> loading 仍为 true 时持续显示「执行中」。

## 目标与验收

### 问题 1
- 每完成一个模型回合（step），输入框迭代次数 +1、token 用量随之增长，无需重开会话。
- token 用量按「逐回合」更新（API 约束，无法逐 token）。
- 切会话/刷新后历史值正确恢复。
- `n` 死代码清除后无回归。

### 问题 3
- HITL 恢复流中，每个工具一拿到结果就立即清除「执行中」（中途即可见），不卡住不放。
- 无重复工具块。
- 流结束与刷新后状态一致。

## 非目标

- 不新增独立 `step` 事件 / 工具块状态机（属方案 2）。
- 不追求逐 token token 用量（API 不可行）。
- 不改用量累计语义（仍会话级 `session_estimate`）。
- 不改正常流（非 HITL）的 done 行为（其不卡「执行中」因 loading 结束）。
- 不改 HITL 审批/拒绝语义。
- 子项目 B（问题 2，长期记忆）不在本 spec 范围。

## 设计

### 第 1 段：token 用量与迭代次数逐 step 实时更新（问题 1）

**后端 `streaming.py`**
- 在收到 `usage_metadata`（回合结束、token 数据就绪）的当下，立即 `yield` 一个 `token_usage` 事件，携带已填充的 `total_usage` / `context_tokens` 与**推进后**的真实 `step_count`。
- 保留 `_finalize_step` 转折点逻辑作为辅助；权威更新改由 `usage_metadata` 到达驱动。
- 0.5s 节流（streaming.py:598）不约束回合边界事件。
- HITL 恢复流同步遵循该契约（恢复生成器的 `usage_metadata` 处理处同样下发）。

**前端 `App.vue`**
- 删除所有 `n` 事件相关死代码分支。
- 正常流 `token_usage` 处理器（App.vue:803）保持 `preStreamIterationCount + step_count` 累加与 `sessionUsage` 同步逻辑，校准 `step_count` 语义。
- 统一两套 `onChunk` 的 token_usage 处理口径（恢复流当前直接 `iterationCount = data.step_count`，需与基线累加口径对齐或明确说明）。

**文档**
- streaming.py 顶部事件注释注明 `token_usage` 为用量/迭代唯一来源，按「逐回合」更新；移除 `n` 说明。

### 第 2 段：HITL 恢复后工具块卡「执行中」（问题 3）

**后端 `streaming.py`（恢复生成器）**
- `ToolMessage` 处理中（streaming.py:2120 附近），当按 `resolved_tid` 精确匹配失败、走 name/pending 兜底命中某 block 时，`yield` 的 `tool_result` 事件改用**该 block 实际持有的 `tool_call_id`**（前端认识的 `tc_id`），而非 `resolved_tid`。精确匹配成功时行为不变。

**前端 `App.vue`（恢复流 onChunk）**
- `tool_result` 兜底匹配命中后，确保清的是**来源工具块**（设 `duration`），避免在已有同 id 块时自动创建重复块；auto-create 仅在确无任何候选时触发。
- 保留现有 `done` 权威 blocks 替换（App.vue:1508）作为结束兜底。

**不动**
- `isToolRunning` 判定逻辑（ChatMessage.vue:392）。
- 正常流（非 HITL）的 done。

## 涉及文件

- `easy_agent/services/streaming.py`：usage_metadata 下发 token_usage；恢复流 tool_result 的 tool_call_id 归一；事件注释。
- `frontend/src/App.vue`：删 `n` 死代码；校准 token_usage 处理；恢复流 tool_result 匹配/去重。
- `frontend/src/components/ChatMessage.vue`：无逻辑改动（仅验证渲染）。
- `frontend/src/components/ChatInput.vue`：无改动（仅验证显示）。

## 验证与测试

- 问题 1：多步流式中，每个模型回合结束后输入框迭代次数递增、token 用量增长，无需重开；切会话/刷新后历史值正确；`n` 死代码清除无回归。
- 问题 3：多工具 HITL 恢复场景，每个工具块在结果到达时（中途）即从「执行中」变为结果展示，后续思考/正文正常渲染；无重复工具块；流结束与刷新后状态一致。
- 实现期排查：用日志确认问题 3 的 `resolved_tid` 与 `tc_id` 是否确有分歧；若实际总一致，则转向排查 tool_result 是否真的下发或字段缺失。
- 回归：现有 `tests/test_chat.py`、`tests/test_v3_streaming.py`、`tests/test_dev_streaming.py` 等流式相关测试通过。

## 风险与待确认

- 问题 3 的 id 分歧为「主嫌」，需实现期日志确认；若非此因，需另行排查 tool_result 下发路径。
- 两套 `onChunk` 口径不统一，改动需保证正常流与恢复流一致，避免引入新差异。
- usage_metadata 到达时机因 provider 而异（部分 provider 可能不返回），需对缺失情况做兜底（仍由 done 兜底）。
