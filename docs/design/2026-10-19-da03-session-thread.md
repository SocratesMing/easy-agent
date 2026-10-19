# DA-03 会话级 thread 设计（一个会话一个 thread）

- 日期：2026-10-19
- 状态：已评审（2026-10-19，§11 均按推荐方案）；P0 实现中
- 范围：后端聊天 / 恢复链路、agent 装配（记忆、技能刷新与本轮上下文注入）、checkpointer 生命周期；前端与 SSE 契约不变
- 相关：审计 DA-03（`docs/audit/2026-10-19-deepagents-best-practices-audit.md` §4）、DA-01（已修）、B1 checkpointer 契约（`docs/design/2026-10-18-b1-checkpointer-store.md`）、D2 共享编译图
- 证据：`docs/audit/evidence-2026-10-19-deepagents/exp_session_thread.py`（真实 `create_deep_agent` + 假模型 + `InMemorySaver`，不联网）
- 版本：deepagents 0.7.28 / langchain 1.4.6 / langgraph 1.2.16（lock）

## 1. 结论先行

- **建议采用官方模型：`thread_id = session_id`，每轮只输入本轮的用户消息**，checkpointer 成为模型上下文的权威；`session_messages` 继续作为 UI 的权威投影，写入逻辑不变。
- 只改 thread_id 不够。现状有四处行为依赖"每轮一个新 thread"，必须同时改造，否则会出回归：
  1. 记忆（会话 `memory.md`、长期 `AGENTS.md`、上传清单 `uploads/index.md`）每轮重新加载；
  2. 技能列表每轮重新扫描；
  3. 知识库上下文、页面上下文、工作区前缀等**本轮临时注入**不进入后续轮次的历史；
  4. 发新消息后，旧的待审批不再影响新一轮。

  四项都有对应方案，并已在实验里验证（§6）。
- 前端零改动：给前端的审批句柄继续用 `{session_id}-{message_id}` 格式，服务端再映射到会话 thread。
- 老会话第一次发消息时，用现有的 `build_context_messages` 从 DB 重建历史，**惰性写入** thread；checkpoint 丢失时走同一条路径恢复。
- 用配置开关分阶段上线。回滚即关闭开关，DB 投影始终完整，回滚不丢消息。
- 评审决策见 §11。

## 2. 现状

### 2.1 一轮对话的调用链

1. `api/chat.py:chat_stream`
   - 把用户原话 `request.message` 写入 DB（`repo.add_message`）。
   - 取或建会话 agent。
   - 登记 run（`start_run`）。
   - 同一会话只允许一条流：进程内检查 `is_session_streaming`（`:473`）加 DB 租约 `acquire_stream`。
2. `services/streaming/orchestrator.py:chat_stream_generator`
   - 给本轮消息加前缀 `[workspace: … | shell: cd …]`（`:452`）。
   - 取 `history = session.messages[:-1]`，调用 `build_context_messages(history, message_content)` 重建完整消息链（`:505-512`）。
   - 设置 `thread_id = f"{session_id}-{message_id}"`（`:522`），然后整段输入 `astream`。
3. HITL 恢复：`api/chat.py:chat_resume` 接收前端回传的 `thread_id`，只校验前缀是 `{session_id}-`（`:722`），随后 `Command(resume=…)`。
4. 历史重建：`services/stream_messages.py:100` `build_context_messages`
   - 用户消息只取 DB 正文。
   - 助手消息拆成"每个 step 一条 `AIMessage(tool_calls)` + 对应 `ToolMessage`"，最后附一条合并后的正文。
   - 思考内容统一丢弃（`:81`）。

### 2.2 本轮临时注入（隐式契约）

下列内容只出现在**本轮**的模型输入里。DB 存的是用户原话，所以后续轮次从 DB 重建时它们自然消失：

| 注入 | 位置 | 内容 |
| --- | --- | --- |
| 工作区前缀 | `orchestrator.py:452` | `[workspace: /workspace/ \| shell: cd /workspace]` |
| 知识库上下文 | `knowledges/chat/streaming.py:27-28` | 检索片段 + `## 用户当前问题` 包装 |
| 页面上下文 | `api/chat.py:569` | `<page_context>…</page_context>` |
| 上传文件开头预览 | `api/chat.py:558` | 本轮上传文件的前若干字符 |
| 技能指令 | `api/chat.py:481` | `/技能目录名` 前缀 |
| 回退模式文件全文 | `api/chat.py`（`file_context.enabled=false`） | 非首轮把解析全文拼进消息 |

### 2.3 现状依赖"每轮新 thread"的地方

每轮都是新 thread，框架状态每轮从零开始，于是下面几项"免费"成立：

| 框架状态（thread 级） | 框架行为 | 现状为何没问题 |
| --- | --- | --- |
| `memory_contents`（MemoryMiddleware） | 每个 thread 只在第一次运行时加载（`deepagents/middleware/memory.py:294`） | 每轮新 thread，每轮重新加载。轮后后台任务会改写会话 `memory.md`（`services/streaming/side_effects.py:215-248`），上传清单也随上传变化，下一轮都能读到 |
| `skills_metadata`（SkillsMiddleware） | 每个 thread 只加载一次，官方说明见 skills 文档 "Reload skills" | 本会话里用 skill-creator 新建的技能，下一轮即可见 |
| 待审批中断 | 挂在对应 thread 上 | 新消息落在新 thread，与旧中断互不干扰 |
| 本轮临时注入 | 只存在于本轮输入 | DB 只存原话 |

反过来，下面这些状态**本该跨轮保留，现状每轮都丢**：

- `_summarization_event` / `_summarization_session_id`：摘要截断点与 history 文件名（`deepagents/middleware/summarization.py:199-208`）；
- `todos`：`write_todos` 的计划。

## 3. 问题（实测）

### 3.1 实验

`exp_session_thread.py` 跑 8 轮，摘要阈值调小为 `trigger=8 条`、`keep=4 条`；第 1 轮之后修改记忆文件、新增一个技能：

```text
== per-message ==（现状：每轮新 thread + 从"DB"重建全量历史）
turn | summary calls (prompt chars) | agent input msgs | memory V2 seen | beta skill seen
   3 | 0 [] |                8 |           True |            True
   4 | 1 [3534] |                6 |           True |            True
   5 | 1 [3609] |                6 |           True |            True
   6 | 1 [3684] |                6 |           True |            True
   7 | 1 [3759] |                6 |           True |            True
threads in checkpointer: 8 | conversation_history files: 4

== session ==（thread_id = 会话，只输入本轮消息，不做任何适配）
   4 | 1 [3534] |                6 |          False |           False
   5 | 0 [] |                8 |          False |           False
   6 | 1 [3835] |                6 |          False |           False
   7 | 0 [] |                8 |          False |           False
threads in checkpointer: 1 | conversation_history files: 1

== session+refresh ==（再加 §6.3 的记忆 / 技能刷新）
   4 | 1 [3534] |                6 |           True |            True
   5 | 0 [] |                8 |           True |            True
   ...
threads in checkpointer: 1 | conversation_history files: 1
```

现状一旦越过阈值就**每轮都摘要**，模型可见的消息数被钉在 `keep`。只换 thread、不做适配时，记忆和技能**在整个会话里都停在第一轮**。加上刷新后两者兼得。

### 3.2 换算到真实配置

项目配置：

- `trigger = 0.8 × 窗口`，`keep = 0.1 × 窗口`；
- 送去摘要的输入裁到 `trim = max(4000, 0.02 × 窗口)` token（`agent/summarization.py:19-43`）；
- 以 128k 窗口为例：trigger ≈ 102k，keep ≈ 12.8k，trim = 4000。

| 指标（越过阈值之后） | 现状（每消息一个 thread） | 会话 thread |
| --- | --- | --- |
| 摘要调用 | **每轮 1 次**，且在主模型调用之前同步执行，拖慢首 token | 每 `(trigger − keep) / 每轮 token` 轮 1 次；每轮 5k token 时约 18 轮一次 |
| 模型可见的近期历史 | 每轮被重新压回 ≈ keep（12.8k）+ 一段摘要 | 在 12.8k 到 102k 之间锯齿增长，平均约 57k |
| prompt cache | 摘要每轮重新生成、文本每轮不同，system prompt 之后的前缀每轮全部失效 | 两次摘要之间前缀稳定，可命中 |
| `/.agent/conversation_history/` | 每轮新写一个文件，内容是整段被裁掉的历史，磁盘占用 O(轮数²) | 一个文件，每次摘要追加，O(轮数) |
| checkpointer 线程数 | 每条消息一个，官方 saver 从不删除 | 每个会话一个，会话删除时删除 |

两种模式下，摘要模型都只看到被裁历史的最后 `trim` token。更早的内容靠 history 文件找回：摘要消息里写了文件路径，模型需要时可以读。所以**摘要质量本身不是本方案的收益**；收益在调用次数、可见上下文、缓存和存储四项。

### 3.3 其他问题

1. **checkpoint 只增不减**：官方 SQLite / Postgres saver 全局共享。删除会话只调用 `release_session_checkpointer`（`api/sessions.py:326`），它只清理 legacy 快照，`{session_id}-{message_id}` 线程永不删除。定时任务的 `sched-{task}-{run}`（`services/scheduler.py:378`）同理。
2. **重建失真**：
   - 工具步骤与正文的交错顺序被打乱：各 step 的正文被合并成一条，放在所有工具调用之后；
   - 缺 id 的工具调用被编号成 `historical-N`；
   - 取消时的半截回复被当作普通助手消息回灌，没有中断标记。
3. **todos 不跨轮**：上一轮的计划，下一轮就看不到了。
4. **审批遗留标记**：发新消息时，后端不改写上一轮的 `approval_status=pending` 标记；前端会话列表的"待审批"徽标按整段历史 `some()` 判定（`frontend-vue2/src/mixins/chatSession.js:210-214`）。

## 4. 目标与非目标

### 目标

- 普通聊天、专家会话、知识面板会话、AI 侧边栏会话：`thread_id = session_id`，每轮只输入本轮消息。
- 越过阈值后，摘要频率、可见上下文、prompt cache、history 文件都回到官方行为。
- 保持现有语义：
  - 记忆、技能每轮刷新；
  - 本轮临时注入不进入后续历史；
  - 切换专家 / 模型下一轮立即生效；
  - 发新消息即放弃上一轮未处理的审批（前端现在就是这个语义，见 §6.4）。
- 前端、SSE 契约零改动。
- 老会话无感迁移；checkpoint 丢失可自动恢复；配置开关可回滚。
- 删除会话时清理 thread；定时任务运行结束后清理 thread。

### 非目标

- 不做编辑消息 / 重新生成 / 分支 UI，只预留锚点（§11 第 6 条）。
- 不改 `session_messages` 的结构与写入逻辑。
- 不改定时任务"每次运行一个独立 thread"的语义，只补清理。
- 不改 durability 模式，不迁移 v3 事件流（另行评估）。

## 5. 方案比较

| 方案 | 做法 | 结论 |
| --- | --- | --- |
| **A：会话 thread（推荐）** | `thread_id = session_id`，只输入本轮消息；配合 §6 的适配 | 官方模型（Going to production："`thread_id`: a stable identifier for the conversation"）。摘要、todos、history 文件、checkpoint 清理一次性理顺 |
| B：保持每消息 thread，把滚动摘要存进 DB | 每轮建新 thread，先用 `aupdate_state` 预置 `_summarization_event` 再运行 | `_summarization_event` 是私有状态；`cutoff_index` 依赖重建后消息链的长度与顺序，而重建本身就失真。耦合框架内部，且不解决重建失真、todos 和 checkpoint 增长。否决 |
| C：会话 thread，但每轮仍输入全量重建历史 | 用 `RemoveMessage(REMOVE_ALL)` 加全量输入覆盖 state | 每轮改写 state，摘要截断点立即失效，等于回到现状。否决 |

## 6. 详细设计（方案 A）

### 6.1 thread 标识与每轮输入

- `thread_id = session_id`。会话 id 由服务端生成（uuid4），不可猜测；thread 的访问权限始终从"属于当前用户的会话"推导，不信任客户端传入的 thread_id。
- 新一轮的输入：

  ```python
  {
      "messages": [HumanMessage(content=raw, id=turn_id)],
      "skills_metadata": None,                               # §6.3，官方重载方式
      "turn_context": {"message_id": turn_id, "content": rendered},  # §6.2
  }
  ```

  - `turn_id`：本轮的 `message_id`；
  - `raw`：与 DB 一致的用户原话。原话为空（只传文件或只选技能）时，写一条简短占位，如 `[上传文件: a.pdf]`，避免在 thread 里留下空的用户消息；
  - `rendered`：现状下真正发给模型的完整文本，即 §2.2 的各项注入加原话。
- 子代理不受影响：每次 `task` 调用都从全新状态起步，其 checkpoint 落在父 thread 的子命名空间里。

### 6.2 本轮上下文：`turn_context` 通道 + `TurnContextMiddleware`

目标：模型在本轮的每一次调用里看到 `rendered`，thread 的 `messages` 里只存 `raw`。

- 新增状态通道：

  ```python
  turn_context: Annotated[NotRequired[TurnContext | None], OmitFromOutput]
  ```

  - 出现在 input schema 里，由 orchestrator 每轮传入；
  - 是 LastValue 通道，HITL 中断后仍留在 checkpoint 里，**恢复的那次运行也能拿到**；下一轮会被覆盖。
- `wrap_model_call` / `awrap_model_call` 只改本次请求（`request.override(messages=…)`），不写回 state：
  1. 把 id 等于 `turn_context.message_id` 的用户消息内容替换为 `content`；
  2. 对本轮用户消息之前的 `AIMessage`，剥离推理内容：列表 content 里的 `thinking` / `redacted_thinking` / `reasoning` 块，以及 `additional_kwargs["reasoning_content"]`。这与现状"历史思考不回灌"一致（`stream_messages.py:81`），也避免切换 provider 时把 Anthropic 的 thinking 签名块发给 OpenAI 兼容通道。本轮内的推理原样保留：Anthropic 在工具循环里需要它。
- 实验第 5、6 段用的就是这个状态通道写法：
  - 模型看到 `'[KNOWLEDGE CONTEXT]\nquestion with context'`，thread 里存的是 `'question with context'`；
  - 下一轮传 `turn_context: None`，模型看到的那条历史是原话；
  - 审批后恢复执行，恢复的那次模型调用仍看到渲染内容。
- 知识库包装器（`knowledges/chat/streaming.py`）改为产出 `rendered`，不再改写 `parsed_content`；orchestrator 的工作区前缀也并入 `rendered`。

### 6.3 记忆与技能每轮刷新

- **技能**：每轮输入 `skills_metadata: None`。这是官方做法（deepagents ≥ 0.7.16，skills 文档 "Reload skills"）；技能集合没变时 prompt 不变，不影响缓存。
- **记忆**：官方没有重载入口。`memory_contents` 是私有状态，不能经输入重置；用 `update_state` 写成 `None` 会让 `wrap_model_call` 读到 `None`。因此新增 `SessionMemoryMiddleware(MemoryMiddleware)`：
  - `name` 仍为 `"MemoryMiddleware"`，经 `middleware=` 原位替换默认实例（deepagents 按名字合并，与 C1 覆盖摘要中间件同一机制）；
  - 只重写 `before_agent` / `abefore_agent`，忽略 state 里已缓存的 `memory_contents`、每次运行都重新读取，其余逻辑原样继承；
  - 实验里的 `RefreshingMemoryMiddleware` 就是这个实现；
  - 主代理的 `middleware` 只会被 forked 子代理继承（`deepagents/graph.py:759`）。项目里的子代理是声明式的，不受影响；即使将来被继承，行为也相同。
- 刷新之后，`side_effects.py` 在记忆更新后调用 `remove_session_agent` 驱逐缓存就不再必要（保留无害，可后续清理）。
- 实测编译图的输入通道只有 `messages`、`pinned_skills`、`skills_metadata`，`memory_contents` 与 `todos` 都不接受输入。
- 备注（DA-13）：官方 pinned skills 的键在消费后自动清空，但被钉住的 `SKILL.md` 会作为一条用户消息留在 thread 历史里；将来若用它替代 `/技能目录名` 文本指令，要接受这一差异。

### 6.4 HITL

- **审批句柄**：`approval_required` 事件和 DB 的 `pending_approval.thread_id` 继续写 `f"{session_id}-{turn_id}"`。前端 `reconstructPendingApproval`（`frontend-vue2/src/mixins/chatSession.js:566`、`frontend/src/App.vue:693`）靠这个格式还原 message_id，因此**前端不用改**。对后端而言，它只是一个不透明的句柄。
- **恢复校验**（`chat_resume`）：
  - 句柄必须以 `{session_id}-` 开头；
  - 句柄里的 message_id 必须等于会话 thread 当前 `turn_context.message_id`；
  - 会话 thread 上必须真的有待审批中断。

  三条都满足，才在 `thread_id = session_id` 上执行 `Command(resume=…)`；任一不满足返回 409"审批已失效"。
- **兼容期**：升级前就挂起的审批，其句柄对应的旧 thread `{session_id}-{message_id}` 若仍有待审批中断，就按旧逻辑在旧 thread 上恢复。兼容期结束后删除这段代码（§9）。
- **新消息作废审批**：实验第 3 段证明，在有待审批中断的 thread 上直接输入新消息：
  - 中断被丢弃；
  - `PatchToolCallsMiddleware` 给悬空的工具调用补一条 error ToolMessage（"did not complete … cancelled or interrupted"）；
  - 工具不执行（`/x.txt written: False`），之后没有挂起的中断。

  后端同时把 DB 里上一轮 `pending` 的块改写为 `rejected`，并清除 `pending_approval`，会话列表徽标随之消失。
- **与现状的差异**：现状下旧审批在新消息之后仍可在旧 thread 上恢复。但前端从不提供这个入口：`reconstructPendingApproval` 遇到末尾的用户消息就返回，会话内审批挂起时输入框也是禁用的。所以用户可见行为不变。

### 6.5 取消、失败与重试

- **工具执行中取消**（实验第 4 段）：checkpoint 停在 `next=('tools',)`，最后一条是带 tool_calls 的 AIMessage。下一轮由 `PatchToolCallsMiddleware` 补齐，运行正常。
- **模型流式输出中取消，或模型报错**：未完成的模型节点不写入 state，但 DB 已经有半截正文。现状下一轮会把半截正文回灌给模型。为保持一致：
  - 取消或出错路径在 DB 助手消息的 `extra_data` 上标记 `thread_synced=false`；
  - 下一轮输入变成 `[AIMessage(半截正文 + "（已中断）"), HumanMessage(…)]`；
  - 注入后清除标记。

  错误文本本身不回灌。
- **重试按钮**：会把原文作为新消息重新发送（`frontend-vue2/src/App.vue:1810`），等同于在同一 thread 上开新一轮，与现状一致。

### 6.6 冷启动水合与偏离检测

- **水合**：每轮开始时 `aget_state`。thread 没有消息、而 DB 里已有历史时，本轮输入改为 `build_context_messages(history, raw)`，即现状的重建链路，最后一条用户消息带上 `id=turn_id`；之后 thread 即为权威。这一条路径同时覆盖三种情况：
  - 老会话首次发消息（迁移）；
  - checkpointer 数据丢失，例如删除了 `data/checkpoints.db`，或从 legacy 切到 Postgres；
  - 开关从关切到开。
- **偏离检测**：
  - DB 用户行的 `extra_data` 记录 `turn_id`，**不论开关状态都记录**（P0 起）。否则开关关闭期间跑过的轮次没有 `turn_id`，就无法发现 thread 漏了这些轮次。
  - 每轮开始时，比较 DB 上一条用户行的 `turn_id` 与 thread 里最后一条用户消息的 id。不一致说明 thread 漏了轮次，处理方式是 `adelete_thread(session_id)` 后重新水合。
  - P0 之前写入的旧行没有 `turn_id`，跳过比较；这类会话的 thread 必然为空，会先走水合。

### 6.7 thread 生命周期与清理

- **删除会话**：在 `persistence/checkpoints.py` 新增 `delete_session_thread(session_id)` 统一封装：
  - 先 `await saver.adelete_thread(session_id)`（官方 saver 的公开 API）；
  - 兼容期内顺带删除该会话旧的 `{session_id}-{message_id}` 线程，message_id 取自该会话的 `agent_runs`（尽力而为，遗漏的由清理脚本兜底）；
  - 再调用 legacy 的 `release_session_checkpointer`，并删除隐藏 artifacts 目录（C2 已实现）。
- **定时任务**：一次运行结束后 `adelete_thread(f"sched-{task_id}-{run_id}")`。定时任务关闭了 HITL（`enable_hitl=False`），没有需要恢复的状态。
- **存量旧线程**：一次性脚本 `scripts/maintenance/prune_message_threads.py`：
  - 用 saver 公开的 `alist(None)` 遍历全部 checkpoint 的 thread_id（一次性全量扫描可以接受，且不依赖 `agent_runs` 的保留期）；
  - 按格式筛出 `{uuid}-{message_id}` 与过期的 `sched-*`，逐个 `adelete_thread`；
  - 默认只预览，`--apply` 才删除；只删不再有待审批中断的线程。

### 6.8 多副本、并发与 checkpointer

- **并发**：同一会话同时只有一条流，靠进程内 `is_session_streaming` 加 DB 租约 `acquire_stream` 双重互斥，所以会话 thread 不会被两次运行同时写入。
- **Postgres saver**：thread 状态在副本之间共享，任何副本都能续跑。agent 缓存仍在进程内，但 thread 状态不依赖它。
- **legacy `PersistentMemorySaver`**（MySQL 且无 PG）：
  - 一个会话只有一个 thread，按 namespace 只保留最近 2 个 checkpoint（`checkpoints.py:37-38`），逻辑不用改；
  - 快照随会话变长而变大，与现状同阶：现状每个 thread 的输入也是全量历史。
- **checkpoint 体积**：`messages` 通道每个 super-step 都写一份完整列表，两种模式同阶。deepagents 的摘要只记截断点，不删 state 里的原始消息，所以 thread 保留完整原始历史。降低写放大可另行评估 `durability="exit"`（§11 第 5 条）。

### 6.9 UI 投影与模型视图的差异（接受的偏离）

DB 记录用户看到的内容，thread 记录模型看到的内容，二者按设计会有差异：

- 摘要：模型看到摘要，UI 显示全部历史；
- 本轮临时注入；
- 补齐的悬空工具调用；
- 推理内容剥离；
- 取消时的半截回复。

其中半截回复通过 §6.5 回灌对齐，其余差异予以接受。排查问题时可以加一个仅管理员可用的调试接口，用 `aget_state` 查看模型视图（可选）。

## 7. 改动清单（实现阶段）

| 文件 | 改动 |
| --- | --- |
| `services/streaming/orchestrator.py` | thread_id 改为会话；本轮输入（原话 / `turn_context` / `skills_metadata`）；水合与偏离检测；半截回复回灌；审批句柄生成 |
| `api/chat.py` | 拆分 `raw` 与 `rendered`；`chat_resume` 的句柄映射、校验与兼容期旧 thread；新消息作废上一轮 pending 标记 |
| `knowledges/chat/streaming.py` | 知识上下文改为产出 `rendered`，不再拼进 `parsed_content` |
| `agent/turn_context.py`（新） | `TurnContextState`、`TurnContextMiddleware`（本轮内容替换与历史推理剥离） |
| `agent/memory.py`（新）/ `agent/core.py` | `SessionMemoryMiddleware`；`_build` 的 `middleware` 加入上面两个中间件 |
| `persistence/checkpoints.py` | `delete_session_thread()`（官方 saver `adelete_thread` 与 legacy 清理） |
| `api/sessions.py`、`services/scheduler.py` | 删除会话 / 定时任务结束时清理 thread |
| `bootstrap/config_models.py` 等 | `agent.session_thread: bool`，同步 `config_validation` 已知键、`config-example.yaml`、README |
| `scripts/maintenance/prune_message_threads.py`（新） | 存量旧线程清理 |
| 文档 | `architecture-current.md`、`docs/sse-events.md`（审批句柄语义）、审计 §7 |

## 8. 测试计划

单元测试：

1. 句柄生成与解析：前缀、message_id 不匹配、没有挂起中断时返回 409。
2. 水合：thread 为空时输入 = 重建链；thread 已有消息时只输入本轮。
3. 偏离检测会触发删除 thread 再水合。

集成测试（真实 `create_deep_agent` + 假模型，沿用实验脚本的写法，用 `InMemorySaver`）：

1. 越过阈值后连续多轮：摘要调用次数 < 轮数，只有 1 个 history 文件。
2. 轮次之间修改 `memory.md` / `AGENTS.md` / 上传清单、新增技能：下一轮 system prompt 可见。
3. `turn_context`：模型看到 `rendered`，state 只存 `raw`；HITL 恢复那次运行仍使用 `rendered`。
4. 有待审批时发新消息：悬空调用被补齐、工具没有执行、DB 的 pending 标记被改写、旧句柄恢复返回 409。
5. 工具执行中取消、流式中取消：下一轮正常，半截正文被回灌一次。
6. 同一 thread 上切换专家（不同的 system prompt 和工具集）与切换模型：实验第 4 段已验证能看到历史；另需构造 Anthropic thinking 块验证推理剥离。
7. 删除会话后 `aget_state` 为空；定时任务结束后 thread 被删除。官方 SQLite saver 的用例需要 `sqlite-saver` extra，CI 目前缺这个 extra（已决定暂不处理），这类用例缺依赖时跳过。

回归：开关关闭时全部现有测试不变。

前端：不改代码；人工检查刷新页面后审批恢复、审批与重试两条路径。

## 9. 分阶段上线与回滚

| 阶段 | 内容 | 开关 |
| --- | --- | --- |
| P0 · 铺底 | `SessionMemoryMiddleware`、`TurnContextMiddleware`、注入改走 `turn_context`、DB 用户行记录 `turn_id`、删除会话与定时任务清理 thread。每消息 thread 下也成立：记忆和技能本来就每轮重载，注入效果不变 | 无（行为中性） |
| P1 · 切换 | 会话 thread、水合与偏离检测、审批句柄映射与兼容期、新消息作废审批、半截回复回灌 | `agent.session_thread`，dev / test 先开 |
| P2 · 默认开启 | 默认 `true`；运行存量旧线程清理脚本；观察一个版本后删除兼容期旧 thread 恢复代码 | 默认开 |

回滚：把 `agent.session_thread` 设为 `false`，立即回到每消息 thread。DB 投影始终完整，不丢消息；损失的只有 todos 与滚动摘要，这本来就是现状行为。

## 10. 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 记忆 / 技能停在第一轮 | 会话记忆、长期记忆、上传清单、新技能不可见 | §6.3，加集成测试 2；实验已复现问题，也已验证修复 |
| 临时注入写进历史 | 上下文膨胀；知识片段在后续轮次重复出现 | §6.2，加集成测试 3 |
| 切换 provider 时历史里有原生内容块 | 请求被拒，或推理内容重复 | §6.2 剥离历史推理；加 Anthropic 到 OpenAI 兼容通道的用例 |
| checkpoint 丢失或被手工清理 | 模型丢上下文 | §6.6 从 DB 水合 |
| 依赖框架行为：新输入丢弃待审批中断并由 `PatchToolCallsMiddleware` 补齐 | 框架升级后行为可能变化 | 集成测试 4 锁定；升级 deepagents / langgraph 时必跑 |
| `SessionMemoryMiddleware` 重写了公开类的 `before_agent` | 上游改动 MemoryMiddleware 的加载逻辑后可能失效 | 只重写一个钩子，其余继承；集成测试 2 锁定 |
| 升级时有挂起的审批 | 无法恢复 | 兼容期旧 thread 恢复路径 |
| thread 体积与写放大 | 长会话 checkpoint 变大 | 与现状同阶；`durability` 另行评估 |
| UI 与模型视图不一致 | 排障困难 | §6.9 列明差异，可选调试接口 |

## 11. 评审决策（2026-10-19，均按推荐方案）

1. thread_id 直接用 `session_id`，不加前缀；与旧 `{sid}-{mid}` 和 `sched-*` 按格式即可区分。
2. 半截回复回灌（§6.5），与现状一致。
3. 作废的审批在 DB 里标记为 `rejected`，前端零改动，显示"已拒绝"。
4. 工作区前缀本次保持为临时注入，移入 system prompt 另行清理。
5. `durability="exit"` 另起任务评估，不在本次范围内。
6. `turn_id` 在 P0 无条件记录。本轮起点的 `checkpoint_id` 从 P1 起记录：只有会话 thread 的 checkpoint 才能用来 fork，留给将来的编辑 / 重新生成。

## 12. 验收标准

- 长会话越过阈值后：摘要调用约每 `(trigger − keep) / 每轮 token` 轮一次；每个会话只有一个 history 文件；模型可见上下文大于 keep。
- 记忆、技能、上传清单在下一轮可见；知识库、页面上下文不出现在后续轮次的模型输入里。
- 审批、取消、重试、切换专家、切换模型的行为与现状一致（§6.4 / §6.5 列出的差异除外）。
- 删除会话后 checkpointer 中不再有该会话的 thread。
- 开关关闭时，现有测试全部通过、行为不变。
