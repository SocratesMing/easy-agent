# SSE 事件契约设计（ARCH-05）

| 项 | 内容 |
| --- | --- |
| 状态 | 设计草案（待评审） |
| 依据 | `docs/audit/2026-10-17-easy-agent-audit.md` ARCH-05；`docs/requirements-agent.md` FR-1.3 |
| 目标 | 后端唯一事件注册表 + 前端唯一消费管线；新增事件只改一处 |

## 1. 现状问题

- 后端 **4 处**独立 SSE 生产：`services/streaming.py`（主/恢复）、`knowledges/chat/streaming.py`、`api/expert.py`（2 处）、`api/chat.py`（busy/error 兜底）。
- 前端 **3 处**独立消费：`frontend-vue2/src/api/chat.js`、`api/expert.js`、`features/knowledge/api.js`（`frontend/` Vue3 同构）。
- 实际事件 15 个，`docs/requirements-agent.md` 只列 13 个（缺 `tool_call_delta`、`knowledge_evidence`）；CLAUDE.md 还列了已废弃的 `user_input_required`。

## 2. 事件注册表（唯一事实来源）

建议位置：`easy_agent/services/sse/events.py`（或 `schemas/sse.py`）

| type | 触发 | 关键字段 | 消费端 |
| --- | --- | --- | --- |
| `start` | 流开始 | `session_id` | chat/expert/knowledge |
| `thinking_start` | 进入思考 | `step` | chat |
| `thinking` | 思考增量 | `content`（增量）、`full_content`（节流快照，可缺省）、`step` | chat |
| `thinking_end` | 思考结束 | `duration`、`step` | chat |
| `content` | 正文增量 | `content`、`step` | chat/expert/knowledge |
| `content_end` | 正文结束 | `content`（历史占位，通常空） | chat |
| `tool_call` | 完整工具调用 | `tool_name`、`tool_call_id`、`arguments`、`step` | chat/expert |
| `tool_call_delta` | 工具参数流 | `tool_call_id`、`delta`、`step` | chat |
| `tool_result` | 工具结果 | `tool_name`、`tool_call_id`、`result`、`success`、`duration`、`step` | chat/expert |
| `todo_list` | 待办更新 | `todos`、`step` | chat |
| `token_usage` | 用量更新 | `input_tokens`/`output_tokens`/`reasoning_tokens`/`context_tokens` | chat |
| `knowledge_evidence` | 知识库引用 | `items`/`session_id` | knowledge |
| `approval_required` | HITL 中断 | `thread_id`、`action_requests`、`review_configs` | chat |
| `done` | 正常结束 | `session_id`、`message_id`、`finish_reason` | 全部 |
| `error` | 失败/取消 | `content`、可选 `code` | 全部 |

> 若保留 `user_input_required`，必须由注册表标注 deprecated 并给出替换事件；当前代码已无生产者，建议删除。

## 3. 实施步骤

1. 新建 `sse/events.py`：`class SSEEvent(str, Enum)` + `EVENT_SPECS`（必填/可选字段）+ `format_sse(dict)` 唯一实现。
2. `stream_processor` 只返回事件对象（`SSEEvent` + payload），不再直接拼 `data:` 字符串；`streaming.py` 统一序列化。
3. `api/expert.py`、`knowledges/chat/streaming.py` 复用同一序列化器与事件常量。
4. 前端抽 `src/api/sse.js`：唯一 `readSSE(response, handlers)`；chat/expert/knowledge 只注册 handler。
5. golden test：用固定假 chunk 驱动 `StreamProcessor`，断言事件序列与字段（快照测试）；前端用 node --test 校验 handler 映射。
6. 文档：`docs/sse-events.md` 由注册表自动生成（避免再次漂移）。

## 4. 兼容策略

- 第一版不删任何字段；`full_content` 继续可缺省（前端已兼容）。
- 新增事件先加注册表 + 前端 handler 再发布后端。
- `/stream/live` 回放历史与新事件格式一致（复用序列化器）。

## 5. 验收

- 后端仅一处 `format_sse` 定义；grep `data: ` 字面量只在序列化器内。
- 前端仅一处 `ReadableStream` 解析。
- golden 事件契约测试通过；`docs/sse-events.md` 与注册表一致（CI diff 检查）。


## 实施进度（2026-10-17）

- 后端：`easy_agent/services/sse.py` 已建立 15 事件注册表 + 唯一 `format_sse`；
  契约测试扫描 6 个生产端并与 `docs/sse-events.md` 同步。
- 前端：Vue2/Vue3 两套基线已各自新增 `src/api/sse.js`，7 处重复解析器全部收敛；
  `npm run test:unit` 覆盖跨 chunk/错误传播/abort 场景。
- 后端生产端已全部改用 `SSEEvent.*` 常量（契约测试禁止事件字面量回归）。
- 待办：`docs/sse-events.md` 自动生成脚本。
