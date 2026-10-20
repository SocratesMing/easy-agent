# DA-03 会话级 thread 设计（一个会话一个 thread）

- 日期：2026-10-19
- 状态：已评审（2026-10-19，§11 均按推荐方案）；P0、P1 已实现（2026-10-20，见 §13、§14），开关默认关闭；P2 进行中（清理脚本与基准已完成，见 §15）
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
| checkpoint 存储 | 每轮把全量历史作为输入写一次，总量随轮数平方增长 | `messages` 是 `DeltaChannel`，每轮只写新消息，总量线性增长 |

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

### 6.2 本轮上下文：`turn_context` 通道 + 主代理摘要中间件渲染

目标：模型在本轮的每一次调用里看到 `rendered`，thread 的 `messages` 里只存 `raw`。

- 新增状态通道：

  ```python
  turn_context: Annotated[NotRequired[TurnContext | None], OmitFromOutput]
  ```

  - 出现在 input schema 里，由 orchestrator 每轮传入；
  - 是 LastValue 通道，HITL 中断后仍留在 checkpoint 里，**恢复的那次运行也能拿到**；下一轮会被覆盖。
- 渲染只改本次请求（`request.override(messages=…)`），不写回 state：
  1. 把 id 等于 `turn_context.message_id` 的用户消息内容替换为 `content`；
  2. 对本轮用户消息之前的 `AIMessage`，剥离推理内容：列表 content 里的 `thinking` / `redacted_thinking` / `reasoning` 块，以及 `additional_kwargs["reasoning_content"]`。这与现状"历史思考不回灌"一致（`stream_messages.py:81`），也避免切换 provider 时把 Anthropic 的 thinking 签名块发给 OpenAI 兼容通道。本轮内的推理原样保留：Anthropic 在工具循环里需要它。
- 实验第 5、6 段用的就是这个状态通道写法：
  - 模型看到 `'[KNOWLEDGE CONTEXT]\nquestion with context'`，thread 里存的是 `'question with context'`；
  - 下一轮传 `turn_context: None`，模型看到的那条历史是原话；
  - 审批后恢复执行，恢复的那次模型调用仍看到渲染内容。
- **渲染放在主代理的摘要中间件里**（P0 实现时的细化）：`TurnAwareSummarizationMiddleware` 继承官方 `SummarizationMiddleware`，`name` 仍为 `"SummarizationMiddleware"`（原位替换默认项），在 `wrap_model_call` / `awrap_model_call` 里先渲染、再交给父类计 token 和摘要；`turn_context` 字段也声明在它的状态 schema 上。不做成单独的中间件，是因为 deepagents 把新增的自定义中间件排在摘要之后（更内层）：单独中间件改写请求时摘要已经按原话计完 token，回退模式的文件全文、知识片段等大段注入会让请求超过阈值却不触发摘要。`tests/agent/test_turn_context.py::test_summarization_counts_the_rendered_turn` 里的对照组复现了这一点。子代理用普通的官方摘要中间件。
- 知识库包装器（`knowledges/chat/streaming.py`）不用改：它写入的 `parsed_content` 就是渲染内容。orchestrator 把工作区前缀加上 `parsed_content` 作为 `rendered`，原话取 `request.message`。

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

  后两条都满足，才在 `thread_id = session_id` 上执行 `Command(resume=…)`；对不上时再看兼容期，仍对不上返回 409"审批已失效"。
- **兼容期**：升级前就挂起的审批，其句柄对应的旧 thread `{session_id}-{message_id}` 若仍有待审批中断，且 DB 最后一条仍是这条待审批消息（`pending_approval.thread_id` 等于句柄，即之后没有新消息），就按旧逻辑在旧 thread 上恢复。兼容期结束后删除这段代码（§9）。句柄格式不对（不以 `{session_id}-` 开头）仍返回 400。
- **新消息作废审批**：实验第 3 段证明，在有待审批中断的 thread 上直接输入新消息：
  - 中断被丢弃；
  - `PatchToolCallsMiddleware` 给悬空的工具调用补一条 error ToolMessage（"did not complete … cancelled or interrupted"）；
  - 工具不执行（`/x.txt written: False`），之后没有挂起的中断。

  后端同时把 DB 里上一轮 `pending` 的块改写为 `rejected`，并清除 `pending_approval`，会话列表徽标随之消失。
- **与现状的差异**：现状下旧审批在新消息之后仍可在旧 thread 上恢复。但前端从不提供这个入口：`reconstructPendingApproval` 遇到末尾的用户消息就返回，会话内审批挂起时输入框也是禁用的。所以用户可见行为不变。

### 6.5 取消、失败与重试

- **工具执行中取消**（实验第 4 段）：checkpoint 停在 `next=('tools',)`，最后一条是带 tool_calls 的 AIMessage。下一轮由 `PatchToolCallsMiddleware` 补齐，运行正常。
- **模型流式输出中取消，或模型报错**：未完成的模型节点不写入 state，但 DB 已经有半截正文。现状下一轮会把半截正文回灌给模型。为保持一致（P1 实现，不需要额外标记）：
  - 下一轮开始时，比较 DB 里 thread 最后一轮的助手正文与 thread 里该轮 AIMessage 的正文；
  - DB 正文以 thread 正文开头且更长时，多出的部分作为 `AIMessage(多出的部分 + "（回复被中断）")` 放在本轮输入最前面；
  - 对不上（例如正文里带 `<think>`）就不补，宁可少补也不重复。补过之后 thread 的最后一轮变成本轮，不会重复补。

  错误文本只写进 SSE，不在 DB 正文里，所以不会回灌。
- **重试按钮**：会把原文作为新消息重新发送（`frontend-vue2/src/App.vue:1810`），等同于在同一 thread 上开新一轮，与现状一致。

### 6.6 冷启动水合与偏离检测

- **水合**：每轮开始时 `aget_state`。thread 没有消息、而 DB 里已有历史时，本轮输入改为 `build_context_messages(history, raw)`，即现状的重建链路，最后一条用户消息带上 `id=turn_id`；之后 thread 即为权威。这一条路径同时覆盖三种情况：
  - 老会话首次发消息（迁移）；
  - checkpointer 数据丢失，例如删除了 `data/checkpoints.db`，或从 legacy 切到 Postgres；
  - 开关从关切到开。
- **偏离检测**：
  - DB 用户行的 `extra_data` 记录 `turn_id`，**不论开关状态都记录**（P0 起）。否则开关关闭期间跑过的轮次没有 `turn_id`，就无法发现 thread 漏了这些轮次。
  - 每轮开始时，在 DB 里找 thread 最后一轮（thread 里最后一条能对上 `turn_id` 的用户消息）。DB 里排在它之后的轮次（开关关闭期间的对话、定时任务写入、专家预检拒答）thread 里没有：P1 实现时改为**重建后补进本轮输入**，而不是删 thread 重来，这样摘要状态和 todos 不会丢。
  - 只有 thread 的最后一轮在 DB 里找不到时（DB 被改过），才 `adelete_thread(session_id)` 后重新水合。
  - P0 之前写入的旧行没有 `turn_id`，跳过比较；这类会话的 thread 必然为空，会先走水合。

### 6.7 thread 生命周期与清理

- **删除会话**：在 `persistence/checkpoints.py` 新增 `delete_session_thread(session_id)` 统一封装：
  - 先 `await saver.adelete_thread(session_id)`（官方 saver 的公开 API）；
  - 兼容期内顺带删除该会话旧的 `{session_id}-{message_id}` 线程，message_id 取自该会话的 `agent_runs`（尽力而为，遗漏的由清理脚本兜底）；
  - 再调用 legacy 的 `release_session_checkpointer`，并删除隐藏 artifacts 目录（C2 已实现）。
- **定时任务**：一次运行结束后，用 agent 编译图自己的 checkpointer 删除 `sched-{task_id}-{run_id}`（官方 saver 与 legacy 快照都适用）。定时任务关闭了 HITL（`enable_hitl=False`），没有需要恢复的状态。
- **存量旧线程**：一次性脚本 `scripts/db/prune_checkpoint_threads.py`（P2 已实现；仓库约定脚本按功能分组，放在 `scripts/db/`）：
  - 用 saver 公开的 `alist(None)` 遍历全部 checkpoint，取每个 thread 根命名空间的最新一个（一次性全量扫描可以接受，且不依赖 `agent_runs` 的保留期）；
  - 按格式分类：会话 thread（`thread_id` 就是会话 uuid）与无法识别的 thread 保留；`{uuid}-{message_id}` 与 `sched-*` 是清理对象；
  - 最新 checkpoint 早于 `--min-age-hours`（默认 24）才删，避免删到正在运行的 thread；挂着待审批中断的每消息 thread 保留，审批兼容期结束后用 `--include-pending` 再删；
  - 默认只预览，`--apply` 才删除；legacy 快照下直接提示不需要清理。

### 6.8 多副本、并发与 checkpointer

- **并发**：同一会话同时只有一条流，靠进程内 `is_session_streaming` 加 DB 租约 `acquire_stream` 双重互斥，所以会话 thread 不会被两次运行同时写入。
- **Postgres saver**：thread 状态在副本之间共享，任何副本都能续跑。agent 缓存仍在进程内，但 thread 状态不依赖它。
- **legacy `PersistentMemorySaver`**（业务库为 MySQL 且没配 PG / SQLite checkpoint）（P1 实现时更正）：
  - 评审稿说"按 namespace 只保留最近 2 个 checkpoint，逻辑不用改"——不成立。deepagents 0.7 的 `messages` 是 `DeltaChannel`，checkpoint 只存增量，读状态要沿父链回溯到最近的快照再重放；按 thread 内条数裁剪后，运行刚结束 messages 就重建成空列表。这在每消息 thread 下同样存在：legacy 模式下 HITL 恢复时模型看不到对话。P1 已修：只按 thread 数裁剪，thread 内的 checkpoint 全部保留（`tests/db/test_checkpoint_pruning.py`）；
  - 这个 saver 每次写入都把整个快照 pickle 进业务库，会话 thread 越长越慢，所以**即使开关打开，legacy saver 下仍按每条消息一个 thread**（启动后首次遇到时记一次告警）。要用会话 thread，请配置 `EASY_CHECKPOINT_POSTGRES_URI` 或 `EASY_CHECKPOINT_SQLITE_PATH`（业务库为 MySQL 时也可以用本地 SQLite checkpoint，仅限单机）。
- **checkpoint 体积**（P1 实现时更正）：评审稿说"`messages` 每个 super-step 都写一份完整列表，两种模式同阶"——不成立。`DeltaChannel` 只存每步新增的消息，每 50 次更新存一次快照。每消息 thread 每轮把全量历史作为输入写一次，总量随轮数平方增长；会话 thread 每轮只写新消息，总量线性增长。这也是会话 thread 的收益之一。deepagents 的摘要只记截断点，不删 state 里的原始消息，所以 thread 保留完整原始历史。降低写放大可另行评估 `durability="exit"`（§11 第 5 条）。

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
| `agent/turn_context.py`（新） | `turn_input`、`render_turn_messages`、`TurnAwareSummarizationMiddleware`（本轮内容替换与历史推理剥离，主代理摘要前执行） |
| `agent/memory.py`（新）/ `agent/core.py` / `agent/summarization.py` | `SessionMemoryMiddleware`；`_build` 给主代理用 `render_turn=True` 的摘要中间件并加入会话记忆中间件 |
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
| P0 · 铺底（✅ 2026-10-20） | `SessionMemoryMiddleware`、`TurnAwareSummarizationMiddleware`、注入改走 `turn_context`、DB 用户行记录 `turn_id`、删除会话与定时任务清理 thread。每消息 thread 下也成立：记忆和技能本来就每轮重载，注入效果不变 | 无（行为中性） |
| P1 · 切换（✅ 2026-10-20） | 会话 thread、水合与补齐、审批句柄映射与兼容期、新消息作废审批、半截回复回灌、本轮起点 checkpoint 记录；修复 legacy saver 的裁剪 | `agent.session_thread`（默认关），dev / test 先开 |
| P2 · 默认开启（进行中） | ✅ 存量旧线程清理脚本、✅ 基准（§15）；⬜ dev / test 用真实模型试运行；⬜ 观察一个版本后删除兼容期旧 thread 恢复代码；⬜ 默认改为 `true` | 默认开 |

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
| thread 体积与写放大 | 长会话 checkpoint 变大 | `DeltaChannel` 只存增量，会话 thread 的总量随轮数线性增长（每消息 thread 是平方）；legacy 快照 saver 下不启用会话 thread；`durability` 另行评估 |
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

## 13. P0 实施记录（2026-10-20）

thread 仍是每条消息一个，模型看到的内容与改造前一致；以下改动为 P1 铺路：

| 改动 | 位置 | 回归测试 |
| --- | --- | --- |
| 记忆每次运行都重新加载（覆盖框架"每个 thread 只读一次"） | `easy_agent/agent/memory.py` `SessionMemoryMiddleware` | `tests/agent/test_session_memory.py`（含框架默认行为的对照组：上游改成每次重读时它会失败，届时可去掉覆盖） |
| 主代理摘要前渲染本轮上下文、剥离历史推理 | `easy_agent/agent/turn_context.py`、`agent/summarization.py`（`render_turn=True`）、`agent/core.py` | `tests/agent/test_turn_context.py`：模型看到渲染内容、thread 只存原话；审批恢复后仍渲染；摘要按渲染后的内容计 token（含单独中间件的对照组） |
| 图输入改为 原话 `HumanMessage(id=message_id)` + `turn_context` + `skills_metadata: None`；只传文件 / 只选技能时用占位文本 | `services/streaming/orchestrator.py`、`services/stream_messages.py`（`turn_message_text`、`current_message_id`） | `tests/services/test_stream_split_golden.py::test_chat_stream_input_*` |
| 用户消息落库带 `turn_id`（普通聊天与专家链路） | `api/chat.py`、`api/expert_helpers.py`、`api/expert.py` | `tests/api/test_chat.py::test_stream_records_turn_id_on_the_user_message`、`tests/api/test_expert_helpers.py::test_persist_user_turn_records_turn_id` |
| 删除会话时删除官方 saver 里的会话 thread 与 `{session_id}-{message_id}` 旧 thread（message_id 取自 `agent_runs`），再清 legacy 快照 | `persistence/checkpoints.py`（`adelete_threads`、`delete_session_threads`）、`db/repositories/agent_runs.py`、`db/session_repository.py`、`api/sessions.py` | `tests/services/test_thread_cleanup.py`（含真实 `AsyncSqliteSaver`，缺 extra 时跳过）、`tests/api/test_sessions.py::test_delete_session_*` |
| 定时任务运行结束后删除 `sched-{task}-{run}` thread | `services/scheduler.py` | `tests/agent/test_run_limits.py::test_scheduled_task_deletes_its_thread_after_the_run` |

验证：

- 行为测试在改造前的代码上会失败：编排器输入、占位文本、`turn_id` 三项已在 HEAD 的 worktree 上确认；其余新增测试依赖新增的模块或接口，在改造前的代码上无法运行。
- 锁定版本环境全量：1762 passed，1 skipped（5 个官方 SQLite saver 用例因缺 `sqlite-saver` extra 未计入，已决定暂不处理）；装有该 extra 的项目 `.venv`（关键依赖版本与锁文件一致）里全量 1769 passed，1 skipped。
- ruff、import graph（0 SCC）、技能副本、double-write、multi-replica、API 文档门禁均通过。

为 P1 留下的事项：

- 定时任务会往它的会话里写用户行，这些行没有 `turn_id`。P1 的偏离检测要把"thread 之后出现的、没有 `turn_id` 的用户行"也算作偏离。
- 只有 P1 切到会话 thread 之后，`SessionMemoryMiddleware` 才会产生行为差异；`remove_session_agent` 在记忆更新后的驱逐届时可以移除。

## 14. P1 实施记录（2026-10-20）

开关 `agent.session_thread`（默认 `false`）。打开后，在官方 saver（Postgres / SQLite）上一个会话一个 thread；关闭时与 P0 完全一致。

| 改动 | 位置 | 回归测试 |
| --- | --- | --- |
| 配置开关 | `bootstrap/config_models.py`、`bootstrap/config.py`、`config_validation.py`、`config-example.yaml`、README | `scripts/quality/check_config_schema.py --check` |
| 轮次规划 `plan_turn`：会话 thread、为空时水合、补齐 DB 多出的轮次、补回半截回复、thread 与 DB 对不上时删除重水合；legacy 快照 saver 下回退每消息 thread | `services/session_thread.py`、`services/streaming/orchestrator.py` | `tests/services/test_session_thread.py`（真实 `create_deep_agent` + `InMemorySaver`） |
| 审批句柄：事件与 DB 仍写 `{session_id}-{message_id}`；恢复接口经 `resolve_resume_thread` 映射到会话 thread，兼容期支持升级前挂在旧 thread 上的审批，失效返回 409 | `services/session_thread.py`、`api/chat.py`、`resume_stream_generator(handle=...)` | `tests/services/test_session_thread.py::test_approval_*`、`test_pending_approval_from_before_the_upgrade_still_resumes`、`tests/api/test_session_thread_api.py::test_resume_with_a_handle_for_an_older_turn_returns_409` |
| 新消息作废上一轮遗留的待审批（DB 标为 `rejected`），普通聊天与专家链路；只在真正走会话 thread 时（开关打开且在用官方 saver）才作废 | `api/chat.py`、`api/expert_helpers.py`、`db/repositories/chat_repository.py`、`session_thread_in_use` | `tests/api/test_session_thread_api.py::test_new_message_marks_the_left_over_approval_rejected`、`test_legacy_snapshot_saver_does_not_abandon_approvals`、`tests/services/test_session_thread.py::test_new_message_makes_the_old_approval_stale` |
| 本轮起点 checkpoint 记到本轮用户消息（`turn_checkpoint_id`） | `db/repositories/sessions.py::annotate_user_turn`、`services/session_thread.py::record_turn_checkpoint` | `tests/api/test_session_thread_api.py::test_annotate_user_turn_*`、端到端用例 |
| legacy `PersistentMemorySaver` 只按 thread 数裁剪（修 DeltaChannel 下消息丢失），删除 thread 后立即落盘 | `persistence/checkpoints.py` | `tests/db/test_checkpoint_pruning.py`（改造前的代码上会失败：运行刚结束 messages 就为空） |
| 定时任务改用 agent 编译图自己的 checkpointer 删除本次运行的 thread | `persistence/checkpoints.py::adelete_agent_thread`、`services/scheduler.py` | `tests/agent/test_run_limits.py::test_scheduled_task_deletes_its_thread_after_the_run`（断言删除前 thread 存在、删除后不存在） |

端到端：`tests/api/test_session_thread_api.py::test_two_turns_share_one_checkpoint_thread`。它用真实 EasyAgent 加官方 SQLite saver（缺 `sqlite-saver` extra 时跳过）跑两轮，验证 checkpoint 库里只有会话 thread，用户消息不重复，第二轮记录了起点 checkpoint。

验证：项目 `.venv`（关键依赖版本与锁文件一致）全量 1789 passed，1 skipped；ruff、import graph、技能副本、double-write、multi-replica、API 文档、配置 schema 门禁均通过。

P2 之前要做的事：

- 在 dev / test 打开开关试运行，观察摘要频率、首 token 延迟、checkpoint 库大小；
- 存量旧线程清理脚本（§6.7）；
- 观察一个版本后删除兼容期旧 thread 的恢复路径，再把默认值改成 `true`。

## 15. P2 进展（2026-10-20）

### 15.1 基准

`docs/audit/evidence-2026-10-19-deepagents/exp_session_thread_benchmark.py` 用项目自己的编排器、主代理的 `TurnAwareSummarizationMiddleware` 和官方 `AsyncSqliteSaver` 跑 60 轮。参数：按项目的摘要比例缩到 32k 窗口（trigger 25.6k、keep 3.2k、trim 4000），每轮约 1.1k token。

| 指标 | 每消息 thread（现状） | 会话 thread |
| --- | --- | --- |
| 摘要调用（两种模式都在第 23 轮首次触发） | 37 次（之后每轮一次） | 2 次 |
| 首次摘要后主模型平均输入 | 3,592 token（被压在 keep 附近） | 12,438 token |
| checkpoint 库 | 16,960 KB | 2,520 KB |
| thread 数 | 60 | 1 |
| `/conversation_history/` | 37 个文件，5,588 KB | 1 个文件，158 KB |

与 §3.2 的估算一致：摘要调用从每轮一次降到偶尔一次（真实环境里每次都在主模型调用之前同步执行）。模型看得到的近期上下文约是现在的 3.5 倍。checkpoint 与历史文件从随轮数平方增长变为线性增长。

### 15.2 存量清理脚本

`scripts/db/prune_checkpoint_threads.py`（规则见 §6.7），测试 `tests/db/test_prune_checkpoint_threads.py`：待审批的判断用真实 LangGraph 中断产生的 pending writes。在一个临时 SQLite checkpoint 库上按预览、`--apply`、复查走了一遍，结果符合预期。

### 15.3 本地开发环境

本地 `.env.dev`（不在版本库里）配置的业务库是 MySQL，又没有配置 checkpoint 路径，所以本地跑的是 legacy 快照 saver：P1 修复的消息丢失问题正好影响本地环境，而且打开开关也会回退到每消息 thread（会记一次告警）。要在本地试运行会话 thread，需要同时设置：

- `EASY_CHECKPOINT_SQLITE_PATH=data/checkpoints.db`（单机；多副本用 `EASY_CHECKPOINT_POSTGRES_URI`）
- `SESSION_THREAD=true`（`config-example.yaml` 里 `session_thread: ${SESSION_THREAD:-false}`）

### 15.4 剩余事项

1. dev / test 用真实模型试运行，重点看：越过摘要阈值后首 token 延迟、摘要频率、审批与取消路径、切换专家 / 模型。
2. 生产环境运行 `prune_checkpoint_threads.py`：先预览，确认后加 `--apply`。
3. 观察一个版本后删除兼容期旧 thread 的恢复路径，用 `--include-pending` 清掉剩下的旧 thread，再把 `session_thread` 默认值改为 `true`。
