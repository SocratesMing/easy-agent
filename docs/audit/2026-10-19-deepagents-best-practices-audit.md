# DeepAgents 官方规范与最佳实践对标审计（2026-10-19）

> 范围：`easy_agent/` 后端对 DeepAgents / LangChain / LangGraph 的使用方式（沙箱、Backend、工具、MCP、技能、HITL、子代理、记忆、上下文与摘要、流式、持久化、容错），以及代码结构的可扩展性。
> 对照基线：**deepagents 0.7.28（PyPI 最新，2026-10-16）**、langchain 1.4.6、langgraph 1.2.16、langchain-mcp-adapters 0.2.2（本仓库 `.venv` 实装版本）。
> 文档来源：LangChain 官方文档（经 Docs MCP `https://docs.langchain.com/mcp` 拉取全文，2026-10-19）+ `langchain-ai/langchain-skills` 中的 deep-agents-core / memory / orchestration 技能 + deepagents 0.7.28 源码。
> 与既有报告的关系：`docs/audit/2026-10-17-easy-agent-audit.md` 已覆盖的条目不重复展开，只复核状态；本报告新增条目编号为 **DA-xx**，严重度沿用 P0–P3。
> 测试基线：`pytest tests/` → **1607 passed, 1 skipped, 2 warnings**（266s）。
> **修复进度**：Batch B（DA-01 / DA-02 / DA-08 / DA-10 / DA-16）与修复中发现的 DA-18 已于 2026-10-19 完成，见 §9；Batch A 已修 SEC-01 / SEC-02 / SEC-04 / SEC-05，SEC-03（免密登录）按产品决定保留，见 §10。C1（DA-06 / DA-09 第一步 / DA-11 / DA-12 / DA-17、sqlite 连接器 `DB_PATH`）已完成，见 §11；C2（DA-04 / DA-05）已完成，见 §12；C3（DA-07）与一批清理项已完成，见 §13。DA-03 设计已评审，P0（铺底，行为中性）与 P1（会话 thread，开关默认关）已于 2026-10-20 完成，见 §14、§15；P2 的存量清理脚本与基准已完成，见 §16。其余后续项见 §17。

---

## 1. 结论速览

**一句话**：0.7 迁移基本到位（版本窗口锁定、`ContextBackend` 实例化、显式 `TodoListMiddleware`、`delete` 入 HITL、`HarnessProfile` 替代 monkeypatch、`context_schema` 运行期上下文、官方 checkpointer），框架装配层整体是"按官方公开 API 搭的"；但有 **3 个已复现的正确性/安全问题**（摘要 token 混入回复、HITL 多中断决策扇出、默认路由别名绕过 deny），以及若干**偏离官方推荐模式**的设计（每条消息一个 thread、自研 Composite 冒充、私有工厂 monkeypatch、MCP 仍用旧适配器 + 名字正则判 HITL）。另外，**上一轮审计的 4 个安全 P0 仍全部存在**，它们让沙箱设计形同虚设，必须先于本报告的任何条目处理。

| 维度 | 判定 | 要点 |
| --- | --- | --- |
| `create_deep_agent` 装配 / 版本 | ✅ 符合 | 只用公开参数；`deepagents>=0.7.28,<0.8` 窗口 + 契约测试 |
| Backend / 虚拟文件系统 | ⚠️ 部分符合 | `virtual_mode=True` ✔；默认路由与 `/workspace/` 同根（DA-04）；`ContextBackend` 冒充 `CompositeBackend`（DA-05）；官方不建议 Web 服务用 `LocalShellBackend` |
| 沙箱 / `execute` | ⚠️ 部分符合 | bwrap/Landlock/fail-closed 质量高；但网络全通、技能目录读写挂载可绕过 new-only、无资源限额（DA-07）；LangSmith 混合模式生命周期缺失（DA-14） |
| 工具 | ✅ 基本符合 | 异步 `BaseTool` + `args_schema` ✔；注册表 ✔；会话绑定靠"克隆工具"而非原生 `ToolRuntime.context`（结构建议） |
| MCP | ⚠️ 部分符合 | 默认 streamable_http ✔、逐 server 容错 ✔；漏 `delete` 冲突（DA-08）；仍用已被取代的 `langchain-mcp-adapters`、HITL 靠名字正则而非 MCP annotations（DA-09）；用户可提交 stdio = RCE（SEC-04，未修） |
| 技能 | ⚠️ 部分符合 | 7/7 SKILL.md frontmatter 合规 ✔；公共技能"复制式启用"、未用多源/只读/pinned/skill tools（DA-13） |
| HITL | ❌ 有缺陷 | `interrupt_on` + `when` 谓词 ✔（官方条件中断）；多中断时只展示第一个、决策扇出到所有中断（DA-02）；前端一键决策复制给全部动作（DA-15） |
| 子代理 | ⚠️ 部分符合 | 显式 `skills` ✔；工具未收窄（DA-11）；不受 call-limit 约束、主流程未设 `recursion_limit`（DA-10） |
| 记忆 | ✅ 基本符合 | `memory=` + `/memories/` 用户级作用域 ✔（官方推荐默认）；每轮 2 次额外 LLM 调用做整合（已知 PERF-16） |
| 上下文 / 摘要 | ❌ 有缺陷 | 摘要 token 被当作回复正文（DA-01）；阈值靠 monkeypatch 私有工厂，模型未设 `profile`（DA-06） |
| 流式 | ⚠️ 部分符合 | `["messages","updates"]` + HITL 检测是官方模式 ✔；不过滤 `lc_source`、不出子代理进度（`subgraphs`/v3） |
| 持久化 / thread 模型 | ⚠️ 偏离 | 每条消息一个 `thread_id`，历史每轮从 DB 重建（DA-03），与官方"thread = 会话"相反 |
| 容错 | ⚠️ 部分符合 | call-limit + SDK 重试 ✔；`LoggingMiddleware` 吞掉全部工具异常，等同自研 `ToolErrorMiddleware` 且无白名单（DA-12） |

---

## 2. 本次准备工作

| 项 | 内容 |
| --- | --- |
| 插件市场 | `claude plugin marketplace add langchain-ai/langchain-plugins`（LangChain 官方，用户级） |
| 技能 | `langchain-skills@langchain-plugins` v0.1.2：deep-agents-core / deep-agents-memory / deep-agents-orchestration / deepagents-python-quickstart / langgraph-human-in-the-loop / langgraph-persistence / langchain-middleware 等 22 个 |
| MCP | `langchain-mcp@langchain-plugins` v0.1.0：`langchain-docs`（`https://docs.langchain.com/mcp`，文档检索 + 虚拟文件系统读页）、`langchain-reference`（`https://reference.langchain.com/mcp`，API 签名查询） |
| 生效方式 | 新开 Claude Code 会话后自动加载；`claude plugin list` 可查看；可选再装 `langsmith-skills` / `langsmith-mcp`（后者需 LangSmith OAuth） |
| 已读文档 | deepagents：overview / customization / backends / sandboxes / permissions / tools / skills / human-in-the-loop / memory / subagents / context-engineering / going-to-production / fault-tolerance / profiles / streaming / event-streaming / changelog；langchain：mcp（index/tools/connections/auth/migrate）、human-in-the-loop（含 1.4.6 per_call）、prebuilt middleware |

---

## 3. 上一轮审计遗留：安全 P0/P1 仍未修复（已在当前代码复核）

这些问题与 DeepAgents 规范无关，但**直接绕过沙箱**：只要它们存在，bwrap/Landlock、permissions、HITL 的投入都不构成安全边界。

| ID | 状态 | 位置（当前代码） | 说明 |
| --- | --- | --- | --- |
| SEC-01 P0 | 已修（§10） | `easy_agent/api/terminal.py:231-239`；`easy_agent/app.py:78-81` | `/agent/terminal/ws` 无鉴权（docstring 写明"无需认证"），`os.environ.copy()` + fork bash，POSIX 下总是挂载 |
| SEC-02 P0 | 已修（§10） | `easy_agent/app.py:821-844` | SPA 兜底 `os.path.join(frontend_dist, full_path)` 无 resolve 前缀校验 |
| SEC-03 P0 | 保留（产品决定，受开关控制，§10） | `easy_agent/api/auth.py:226-250` | 免密登录仅凭用户名签发 token |
| SEC-04 P0 | 已修（§10） | `easy_agent/api/connectors.py:618-713`；`easy_agent/services/mcp.py:166` | 任意登录用户可提交 `stdio` 连接器 → 校验时立即在**宿主机、沙箱外**拉起进程；用户 `mcp.json` 还会做 `${VAR}` 展开（可把 `EASY_JWT_SECRET`/API Key 拼进 URL 外带） |
| SEC-05 P1 | 已修（§10） | `easy_agent/api/files.py:408-438` | `download_file` 直接 `upload_dir / file_path`，无 resolve/前缀校验 |

> 官方依据：Sandboxes 文档 "Never put secrets inside a sandbox"、"Keep secrets in tools outside the sandbox"；Going to production "FilesystemBackend and LocalShellBackend access the host directly. Don't use them in deployed agents"。SEC-04 等价于给每个用户一个沙箱外 shell。

---

## 4. 新发现（按严重度）

### DA-01 · P1 · 摘要模型的 token 被当作助手回复正文推给前端并落库【已复现】

- **现状**：主流程 `agent.astream(stream_mode=["messages","updates"])`（`easy_agent/services/streaming/orchestrator.py:169-175`）；`StreamProcessor._handle_messages` 取到 `(chunk, _metadata)` 后**完全不看 metadata**（`easy_agent/services/stream_processor.py:326-333`）。
- **官方规范**：Context engineering › Summarization："Streaming tokens from the agent will generally include tokens generated by the summarization step. You can filter out these tokens using their associated metadata"（`metadata.get("lc_source") == "summarization"`）。LangChain 的摘要调用固定带 `{"lc_source": "summarization"}`（`langchain/agents/middleware/summarization.py:135`）；v3 事件流的 `InternalCallTransformer` 会自动剔除，旧 `astream` 不会。
- **复现**：`docs/archive/audit-evidence-deepagents/exp_summarization_processor.py` 用真实 `create_deep_agent` + 项目 `StreamProcessor`：
  `accumulated_response = 'SUMMARY-TEXT: the user talked about applesFINAL-ANSWER'`
- **影响**：会话一旦越过摘要阈值，回复开头会出现整段"会话摘要"；它进入 `accumulated_response` → `session_messages` → 下一轮 `build_context_messages` 再次注入，污染后续上下文。叠加 DA-03（每轮新 thread），越过阈值后**每一轮**都会触发。
- **建议**：
  ```python
  def _handle_messages(self, data):
      chunk, metadata = data
      if (metadata or {}).get("lc_source") == "summarization":
          return []  # 可选：发一个「正在压缩上下文」的状态事件
  ```
  并用实验脚本的 fake model 套路加一条回归测试；中期迁移到 `astream_events(version="v3")`（同时拿到 `stream.subagents` 子代理进度）。

### DA-02 · P1 · HITL：多个待审批中断时只展示第一个，却把同一组决策扇出给所有中断【已复现】

- **现状**：
  - `easy_agent/services/hitl_state.py:25-37` `_extract_hitl_request` 只返回 `items[0]`，`approval_required` 只携带第一个中断的 `action_requests`；
  - `easy_agent/services/streaming/orchestrator.py:255-274` 多中断分支把**同一份** `decisions` 套给每个 `interrupt_id`；
  - `easy_agent/services/hitl_state.py:64-74` 前端只发 1 个决策时复制成 N 份；前端 `frontend-vue2/src/mixins/chatSession.js:1445-1447` 确实只发一个决策。
- **官方规范**：HITL › Handle interrupts with streaming："When multiple interrupts are pending at once (for example, parallel branches), map each interrupt ID to its resume value"；Multiple tool calls："You must provide decisions for each one in order"。
- **触发条件**：主代理同一轮并行调用多个 `task`（子代理默认继承 `interrupt_on`，`delete`/删除类 `execute`/`save_expert`/`create_scheduled_task` 都会在子代理里中断）。
- **复现**：`exp_hitl_fanout.py`：UI 只显示 `['delete']`，用户点"批准"后 resume 为 `{'A': approve, 'B': approve}`——**用户从未看到的 `create_scheduled_task` 也被批准**；若发两个决策，则每个中断都收到 2 个决策 → 批处理模式 `ValueError: Number of human decisions … does not match` → 线程卡死。
- **建议**：`approval_required` 下发**全部**中断 `[{interrupt_id, action_requests, review_configs}]`；前端逐动作决策并按 `interrupt_id` 回传；后端严格校验数量，不再"猜测复制"；拒绝文案按工具生成（当前固定为"请勿重试此删除命令"）。
- **备注**：langchain 1.4.6 新增 `interrupt_mode="per_call"`（每个工具调用一个中断 + `response_schema` 校验），但官方说明 "Deep Agents uses batched mode"，`interrupt_on` 仍是批处理；本项先在批处理模式下修正映射即可，per_call 作为下一主版本的跟进项。

### DA-03 · P2 · 每条消息一个 `thread_id`，偏离官方"thread = 会话"模型

- **现状**：`thread_id = f"{session_id}-{message_id}"`（`orchestrator.py:523`），每轮把 DB 历史重建成完整消息链再整体输入（`easy_agent/services/stream_messages.py:100-170`）。
- **官方规范**：Going to production › Invoking the agent："`thread_id`: a stable identifier for the conversation. The checkpointer uses it to persist and resume message history, so follow-up turns continue the same conversation."；deep-agents-core 技能 `fix-thread-id-for-conversations`。
- **影响**：
  1. 摘要状态 `_summarization_event` 存在线程状态里（`deepagents/middleware/summarization.py:199-205`），每轮丢失 → 越过阈值后**每轮重新摘要整段历史**（额外 LLM 调用、延迟、费用），并每轮新写一个 `/conversation_history/session_*.md`；放大 DA-01。
  2. `write_todos` 计划、`skills_metadata` 等状态不跨轮。
  3. checkpointer 每条消息新增线程，需额外清理；工具结果以 DB 中的（可能截断的）版本回灌。
- **优点（保留理由）**：DB 是消息权威、易于编辑/删除/重新生成；技能每轮自动重载。
- **建议**：中期改为 `thread_id = session_id`（分支/重试用 `session_id:branch`），只输入新 `HumanMessage`，checkpointer 为 agent 状态权威、DB 行表作为 UI 投影；若短期保留现状，至少修 DA-01，并把滚动摘要持久化到 DB 复用，避免每轮重摘要。
- **设计文档（2026-10-19 已评审，按推荐方案实施）**：`docs/archive/design/2026-10-19-da03-session-thread.md`。实测还发现：只换 thread 而不做适配，记忆（含上传清单）与技能会停留在会话第一轮；越过阈值后，模型可见的近期历史每轮都被压回 `keep`。

### DA-04 · P2 · 默认路由与 `/workspace/` 指向同一目录：deny 规则可被别名绕过，框架内部产物写进用户工作区【已复现】

- **现状**：`CompositeBackend.default` 是 `_PathTranslatingShell(root_dir=<会话工作区>)`（`easy_agent/agent/backend.py:134-145`），而 `/workspace/` 路由也指向同一目录（`:63-68`）；`ContextBackend.artifacts_root = "/"`（`easy_agent/backends/context.py:53`）。
- **官方规范**：Backends › Route to different backends："Deep Agents write internal data (offloaded tool results, conversation history) to the default backend. Use `StateBackend` as the default to keep these artifacts ephemeral"；FilesystemBackend Tip：避免 agent 产物与项目文件混在一起。
- **复现**：`exp_route_alias.py`：
  ```
  /workspace/secret/a.txt  permission=DENIED   read -> 'TOP-SECRET'
  /secret/a.txt            permission=allowed  read -> 'TOP-SECRET'
  artifacts on disk under session workspace: ['conversation_history/session_x.md', 'large_tool_results/call_abc']
  ```
- **影响**：`agent.denied_dirs` 中任何 `/workspace/...` 规则都可经根路径别名绕过；`/large_tool_results/`、`/conversation_history/` 出现在用户工作区面板与 LangSmith 同步范围内。
- **建议**：默认 backend 根目录改为会话专属的隐藏 scratch 目录（如 `<会话>/.agent/` 或工作区外的兄弟目录），execute 的 cwd 仍映射 `/workspace`（bwrap 已用 binds 实现；Landlock 分支需显式传 cwd）；或显式设置 `artifacts_root` 并路由到隐藏目录；工作区文件树隐藏该目录。

### DA-05 · P2 · `ContextBackend` 通过 ABC 注册"冒充" `CompositeBackend`

- **现状**：`CompositeBackend.register(ContextBackend)`，并返回占位 `default` / `routes` / 空 `sorted_routes`（`easy_agent/backends/context.py:96-113,216`）。
- **官方规范**：自定义后端实现 `BackendProtocol` / `SandboxBackendProtocol`；后端可在内部经 `get_runtime()` 解析上下文（这一点项目是对的）。
- **问题**：框架里依赖 composite 内省的逻辑拿到的都是占位对象——例如 0.7 的符号链接解析权限检查 `_resolve_real_path`（`deepagents/middleware/filesystem.py:421-431`）在本项目中变为 no-op、路由→宿主路径提示为空、权限前缀校验只对静态前缀生效。框架升级时容易静默失效，契约测试难以覆盖。
- **建议**：改为**真实的** `CompositeBackend`，让每个路由叶子各自按运行期 context 解析根目录（例如 `ContextRootedShell(kind="workspace" | "skills" | "memories")`，在 `ls/read/write/...` 内部取 `get_runtime().context`），composite 本身保持静态，从而完全走官方路由/权限/提示词逻辑。

### DA-06 · P2 · 摘要阈值仍靠 monkeypatch 私有工厂；0.7 已有官方覆盖方式，且模型未设置 `profile`

- **现状**：建图期间替换 `deepagents.graph.create_summarization_middleware`（`easy_agent/agent/summarization.py:45-82`）；注释仍写"重复添加 SummarizationMiddleware 会 AssertionError"（`easy_agent/agent/middleware.py:16-19,28-30`）。OpenAI 兼容模型 `profile=None`（实测 `ChatOpenAI(...).profile is None`），框架默认阈值退化为 170k tokens 触发——这正是当初要 monkeypatch 的原因。
- **官方规范**：0.7 changelog "Override a default middleware instance: a `middleware=` instance whose `.name` matches a built-in now replaces that default in place, rather than erroring"；`create_summarization_middleware` 按模型 profile 的 `max_input_tokens` 计算 85%/10% 默认值。
- **建议**（两步，均为官方 API）：
  1. 在 `_create_openai_compatible` / `_create_anthropic_compatible` 传 `profile={"max_input_tokens": llm_config.context_length}` → 主代理、通用子代理、声明式子代理的默认摘要阈值全部正确；
  2. 仍需自定义比例时，在 `middleware=` 与每个 subagent spec 的 `middleware` 中放 `deepagents.middleware.summarization.SummarizationMiddleware(model, backend=backend, trigger=..., keep=..., truncate_args_settings=...)`（`.name == "SummarizationMiddleware"`，原位替换），删除 `configured_summarization`（顺带消除模块全局替换的线程安全隐患）。

### DA-07 · P2 · 本地沙箱边界：网络全通、技能目录读写挂载、无资源限额

- **现状**：bwrap `--unshare-all --share-net`（`easy_agent/sandboxing/sandbox.py:193`）；所有 path_mappings（含 `/user-skills/`）以 `--bind` 读写挂载（`:234-235`），Landlock 同样对技能目录 RW；无 CPU/内存/进程数限制；目录删除拦截基于正则（`easy_agent/backends/shell.py:87-95`）。
- **官方规范**：Sandboxes › Security："Block sandbox network access when not needed"；Permissions："Permissions do not apply to sandbox backends, which support arbitrary command execution via the `execute` tool"；Going to production：生产使用真正的隔离容器（LangSmith / Daytona / Modal / E2B / Runloop…）。
- **影响**：`agent.skill_write=new-only` 只约束文件工具，`execute` 可直接改写已有技能（如 `/user-skills/docx/SKILL.md`）；被提示注入的 agent 可外带工作区数据或探测内网/云元数据；失控命令可耗尽宿主资源。删除类正则审批可被 `python -c`、`rm --recursive` 等绕过（已知 SEC-17），应视为"提示性"而非安全边界。
- **建议**：`skill_write != allow` 时技能目录 `--ro-bind`；网络默认 `--unshare-net`，需要联网时走白名单代理（配置项 `sandbox.network: none|proxy|host`）；用 `prlimit`/cgroup（如 `systemd-run --scope -p MemoryMax=... -p TasksMax=...`）限额并对模型传入的 `timeout` 设上限；多租户生产优先接入官方 sandbox provider（文件工具与 execute 都在沙箱内）。

### DA-08 · P2 · MCP 内建工具名冲突表漏了 `delete`，且与注册表是两套事实来源【已复现】

- **现状**：`easy_agent/services/mcp.py:292-295` 硬编码 `_BUILTIN_TOOL_NAMES`，不含 0.7 新增的 `delete`；注册表 `easy_agent/tools/registry.py:305-313` 已登记 `delete`。
- **复现**：`exp_delete_collision2.py`：同名工具会**静默覆盖** deepagents 内建 `delete`（模型只看到一个 `delete`，参数是 MCP 版本的 `record_id`），无任何报错。
- **建议**：`_BUILTIN_TOOL_NAMES = frozenset(builtin_tool_names())`，加单测；长期给 MCP 工具加 server 前缀（`MCPConfig` / `ClientGroup` 默认 `{server}_{tool}`）。

### DA-09 · P2 · MCP 集成落后于官方方向：旧适配器 + 名字正则判定写操作

- **现状**：`langchain_mcp_adapters.MultiServerMCPClient`（`easy_agent/services/mcp.py:16`）+ 自研持久会话层 `services/mcp_sessions.py`（882 行）；MCP 写操作 HITL 靠 `_MUTATING_TOOL_RE`（`easy_agent/agent/hitl.py:68-71`）。测试输出已出现上游弃用告警 `DeprecationWarning: Use streamable_http_client instead`。
- **官方规范**：langchain 1.4.0 "MCP support now ships inside LangChain in the `langchain.mcp` namespace … It replaces the standalone `langchain-mcp-adapters` package"（beta）；MCP › Tools › Human-in-the-loop："Reading annotations lets you gate a tool based on what the server declares about it, rather than hardcoding tool names"；另有 elicitation→interrupt、per-user auth、共享连接池与 `list_tools(cache_mode=...)`。
- **现状可立即利用**：当前适配器已把 MCP `ToolAnnotations` 平铺进 `tool.metadata`（`readOnlyHint` / `destructiveHint` …），`mcp_evidence` 包装也保留了 `metadata`，但项目未读取。
- **建议**：
  1. 现在：`build_interrupt_config` 先看 annotations（`readOnlyHint=True` → 不审批；`destructiveHint=True` → 审批），无 annotations 再回退正则，并打日志；
  2. 规划：在开关后迁移到 `langchain.mcp.MCPAdapter`（注意 beta、`websocket` 无 FastMCP transport、`headers/auth` 改到 `fastmcp.Client`），评估能否删掉大部分 `mcp_sessions.py`；
  3. 与 SEC-04 一起做：用户侧只允许 HTTP(S) + 域名白名单，stdio 仅管理员预置。

### DA-10 · P2 · 主对话流未设置 `recursion_limit`，子代理不受 call-limit 约束

- **现状**：resume 流设置了 `recursion_limit = max(2*max_steps, 50)`（`orchestrator.py:726-729`，注释写"与主生成器一致"），但主对话流 `stream_config` 只有 `thread_id`（`:524`），实际沿用 deepagents 默认 9999；`ModelCallLimitMiddleware`/`ToolCallLimitMiddleware` 只在主代理（`easy_agent/agent/middleware.py:52-63`），子代理 spec 只有 `LoggingMiddleware`（`easy_agent/agent/core.py:130-135`），通用子代理也只继承"同名替换默认槽位"的中间件（`deepagents/graph.py` `_gp_inheritable`）。`docs/framework-integration.md` 声称 FW-08 两条流都已落地，与代码不符。
- **官方规范**：Fault tolerance › Excessive calls："Set caps on both model calls and tool executions per run"。
- **建议**：主流程补 `recursion_limit`；给每个子代理 spec 加 call-limit；或在已注册的 `HarnessProfile` 上配置 `extra_middleware`（会作用于主代理、通用子代理与声明式子代理）。

### DA-11 · P3 · 子代理工具集未收窄

- **现状**：`office-writer` / `knowledge-researcher` 未声明 `tools`（`easy_agent/agent/subagents.py:13-40`），按官方语义继承主代理全部工具（MCP、web_search、save_expert、create_scheduled_task…）；知识库未启用时仍声明依赖 `access_knowledge_original` 的子代理。
- **官方规范**：Subagents › Minimize tool sets："Only give subagents the tools they need. This improves focus and security"。
- **建议**：`office-writer` 设 `tools=[]`（文件工具与 execute 由框架提供）；`knowledge-researcher` 设 `tools=[access_knowledge_original]`，且仅在该工具实际注入时加入 subagents。

### DA-12 · P3 · `LoggingMiddleware` 实为"吞掉所有工具异常"的自研 ToolErrorMiddleware

- **现状**：`easy_agent/services/logging_middleware.py:39-55` 捕获除 `GraphBubbleUp` 外的全部异常并把 `str(e)` 回灌模型；模块名与职责不符，且位于 `services/`。
- **官方规范**：Fault tolerance：`ToolErrorMiddleware(on_error)`（langchain≥1.3.14）只转换已知可恢复错误，"Unexpected errors … let them bubble up"；`ModelRetryMiddleware` / `ModelFallbackMiddleware` 处理瞬时错误与供应商故障。
- **建议**：换成 `ToolErrorMiddleware`，白名单 `ToolException`/校验错误/超时/HTTP 错误，并对消息脱敏（避免把内部路径、SQL、配置细节回灌给模型与用户）；多模型配置已具备，可加 `ModelFallbackMiddleware`。

### DA-13 · P3 · 技能：复制式启用，未用 0.7 的多源/只读/重载/pinned/skill tools

- **现状**：公共技能经 `shutil.copytree` 复制到用户目录（`easy_agent/api/skill_center.py:521`，即旧 FW-06 未处理）；`skills` 只有 `/user-skills/` 一个源（`easy_agent/agent/workspace.py:354-365`）；new-only 写保护靠逐个技能生成 deny 规则；`save_expert` 是否注入靠自研 `creator_requested` 逻辑；`skills/docx/SKILL.md` 正文 586 行（官方建议 <500 行 / <5000 tokens）。
- **官方规范**：Skills › Compose multiple sources（后者覆盖前者）、Read-only skills（路由 + deny write）、Reload skills（`skills_metadata=None`，≥0.7.16）、Pin skills（`pinned_skills`，≥0.7.23）、Add tools to skills（`metadata.include_tools` + `SkillsMiddleware(tools=...)`，≥0.7.22）。
- **设计文档（2026-10-20 已评审，按推荐方案实施）**：`docs/design/2026-10-20-da13-skills-sources.md`。
- **建议**：`skills=["/skills/builtin/", "/user-skills/"]`，`/skills/builtin/` 只读挂载 canonical 目录（需要"按用户启停"时用一个过滤 `ls` 的视图 backend），deny write；前端"使用技能 X"改为 `pinned_skills`；`expert-creator` 的 SKILL.md 声明 `include_tools: save_expert`，由框架在读取技能后才暴露该工具。

### DA-14 · P3 · LangSmith 混合沙箱：生命周期、同步范围与成本

- **现状**：`client.create_sandbox(...)` 无名称/TTL、无删除路径（`easy_agent/backends/langsmith_sandbox.py:205-210`），`ContextBackend` LRU 淘汰时也不关闭；只同步会话工作区，不同步 `/user-skills/` 与外部目录（`:305-362`），技能脚本在远端不可用；每次 execute 都全量上传 + 全量回写（`:534-563`）。
- **官方规范**：Going to production › Sandboxes › Lifecycle：thread-scoped 命名沙箱 + `idle_ttl_seconds`；"Sandboxes consume resources and cost money until they are shut down"；Skills › Sandbox scripts：用 `before_agent` 中间件把技能文件上传进沙箱。
- **建议**：`name=f"thread-{session_id}"` + TTL，会话删除/缓存淘汰时删除；用 `before_agent`/`after_agent` 中间件同步技能与产物；增量同步（mtime/hash 清单）；长期改为完整 sandbox backend（文件工具也在沙箱内）。

### DA-15 · P3 · 前端审批交互

- **现状**：一次点击只回传一个决策，后端复制给批次内全部动作；拒绝文案写死为删除场景；无 `edit`（`frontend-vue2/src/mixins/chatSession.js:1437-1447`，`App.vue:1724` 有一份重复实现）。
- **建议**：逐动作批准/拒绝；按工具定制拒绝说明；对 `create_scheduled_task`/`save_expert` 开放 `edit`（官方 decision 类型）；合并 `App.vue` 与 mixin 的重复逻辑。
- **进展（2026-10-20）**：按工具定制拒绝说明、合并重复逻辑已完成（§17）；逐动作决策与 `edit` 涉及交互设计，待定。

### DA-16 · P3 · `sandbox_enabled=false` 时 execute 继承完整宿主环境变量

- **现状**：默认 backend `inherit_env=True`（`easy_agent/agent/backend.py:136-143`），无沙箱分支直接 `super().execute`（`easy_agent/backends/shell.py:136-137`），`.env` 中的 API Key / JWT 密钥对命令可见（有沙箱时会经 `_sanitize_env` 过滤）。`core.py:222-224` 的注释称"不继承宿主环境"，与实现不符。
- **建议**：`inherit_env=False` + 显式最小 env，或所有分支统一走 `_sanitize_env`。
- **决定（2026-10-19）**：白名单只放行 shell 基本变量（`PATH`/`HOME`/`LANG`/`LC_*`/`TZ`/`TERM`/`SHELL`、临时目录、Windows 系统变量）。代理 / 镜像 / 证书类变量（`HTTP(S)_PROXY`、`NO_PROXY`、`PIP_INDEX_URL`、`NPM_CONFIG_REGISTRY`、`SSL_CERT_FILE` 等）关闭沙箱时也**不放行**——代理 URL 可能带凭据；依赖它们在 agent 命令里 `pip install` / `npm install` 的部署需另行显式配置。由 `tests/agent/test_backend_env.py::test_host_safe_env_does_not_pass_proxy_or_mirror_vars` 钉住。

### DA-17 · P3 · 文档 / 注释仍停留在 0.6.8 语义

`easy_agent/agent/core.py:1-10`（称 `write_todos` 内置）、`easy_agent/agent/middleware.py:16-19,28-30`（AssertionError）、`easy_agent/agent/graph_cache.py:24-31`（callable backend）、`easy_agent/agent/summarization.py:1-7`、`easy_agent/agent/hitl.py:97-102`、`easy_agent/services/agent_manager.py:146-160`（引用已不存在的 SCC；当前 import 图 0 SCC）、`docs/framework-integration.md`（FW-08 状态）。

### DA-18 · P1 · 定时任务在共享编译图上必然失败（修复 DA-10 时发现）【已复现，已修复】

- **现状**：`easy_agent/services/scheduler.py` 调 `agent.agent.ainvoke(..., config=...)` 时没传 `context=agent.session_context`。默认部署（SQLite/Postgres 官方 saver）会启用 D2 共享编译图，`ContextBackend` 没有兜底 context，`MemoryMiddleware.before_agent` 读记忆时即抛 `RuntimeError: ContextBackend 缺少运行期 SessionContext`，任务直接记为失败。
- **修复**：定时任务与聊天流共用 `agent/run_config.py`（`build_run_config` + `run_context_kwargs`）。回归测试 `tests/agent/test_run_limits.py::test_scheduled_task_runs_on_shared_graph` 用真实共享图驱动 `_execute_task`，改造前结果为 `failed`。

---

## 5. 做得好的地方（建议保留）

- **版本治理**：`deepagents>=0.7.28,<0.8`、`langchain>=1.4.6,<1.5` 窗口 + `tests/test_framework_contract.py`；0.7 破坏性变更（callable backend 移除、todo opt-in、`delete` 工具）均已适配。
- **运行期上下文**：`context_schema=SessionContext` + `astream(..., context=...)` 是官方推荐的"会话差异走 context"模式；在此基础上的 D2 编译图复用设计合理。
- **条件 HITL**：`interrupt_on` 按实际注入工具动态构造 + `when` 谓词（官方 Conditional interrupts）；`delete`、`save_expert`、`create_scheduled_task`、知识原文 materialize 都在审批范围；resume 前校验 `thread_id` 归属（FW-02）。
- **bwrap 实现质量**：env 白名单、`/etc` 敏感文件遮蔽、中性 `/etc/passwd`、venv 中性挂载、输出截断、超时、fail-closed——方向与官方"不把密钥放进沙箱"一致。
- **官方持久化**：默认 `AsyncPostgresSaver` / `AsyncSqliteSaver`；记忆按用户作用域（官方推荐默认）。
- **技能**：7 个运行时技能 frontmatter 全部符合 Agent Skills 规范（name 与目录一致、kebab-case、description ≤1024）；子代理显式声明 `skills`（官方：自定义子代理不继承技能）。
- **工具层**：异步 `BaseTool` + Pydantic `args_schema`；声明式工具注册表（名称/来源/HITL/顺序集中）。
- **工程门禁**：import 图扫描当前 **0 SCC**（`scripts/quality/import_graph.py --check-scc` 通过）、SSE 事件注册表 + 契约测试、配置 schema 门禁、1607 个测试全绿。

---

## 6. 代码结构评估

### 6.1 现状

| 包 | 文件 | 行数 | 评价 |
| --- | --- | --- | --- |
| `agent/` | 11 | 3,075 | 职责清晰（core / backend / hitl / middleware / subagents / skills / summarization / graph_cache / workspace） |
| `services/` | 39 | 14,569 | **平铺的"大杂烩"**：MCP（4）、记忆（4）、专家（4）、流式（7 + 子包）、技能（2）、上传（3）、调度、指标…混在一层 |
| `knowledges/` | 28 | 10,355 | 自成子系统；`service.py` 2,402 行、`ragflow/client.py` 1,470 行、`api.py` 1,349 行偏大 |
| `api/` | 16 | 6,819 | 路由层；`chat.py` 803、`skill_center.py` 782、`connectors.py` 745 |
| `db/` | 26 | 5,273 | 已拆 repository；`schema.py` 892 行 |
| `tools/` | 6 | 1,937 | 注册表 + 工具实现 |

分层方向总体正确：全仓 615 条内部 import 边中，"低层依赖高层"的仅 14 条：`agent → services`（4，`logging_middleware`、`mcp`、`session_uploads`）、`tools → services`（3）、`db.repositories → services.session_runtime`（2）、`backends.shell → agent.hitl`（1）、`tools.expert_writer → agent.model`（1）、`bootstrap → utils`（3，可接受）。

### 6.2 主要问题

1. **`services/` 缺少领域边界**：建议按领域拆子包——`services/mcp/`（mcp、mcp_sessions、mcp_evidence、connectors）、`services/memory/`、`services/experts/`、`services/skills/`、`services/uploads/`、`services/streaming/`（已有）；`agent_manager` 归入 `agent/runtime/`。
2. **Agent 关注点散落在其他层**：`LoggingMiddleware` 放在 `services/`；`backends/shell.py` 依赖 `agent/hitl.py` 的目录删除策略 → 建议 `agent/middlewares/` 收纳所有中间件，命令策略下沉到 `sandboxing/policy.py`。
3. **兼容垫片累积**：`services/streaming.py`（与同名包并存）、`agent/__init__.py` 的 `import *`、`_PathTranslatingShell` 别名、`install_execute_tool_description`、`EasyAgent._build_backend` 为 `__new__` 测试保留的分支、`_HITL_WHEN_PREDICATES` 别名等。建议给每个垫片标注移除版本，并把依赖私有名的测试改为测公开行为。
4. **工具的会话绑定方式绕了一层**：现有工具在构造时绑定 username/session_id，再由 `contextualize_tools` 包装、调用时克隆（`easy_agent/agent/graph_cache.py:526-608`），包装时会丢失 `metadata`/`tags` 等字段。官方模式是工具直接声明 `runtime: ToolRuntime[SessionContext]` 读取 `runtime.context`。新工具应直接采用官方模式，旧工具逐步迁移后可删除这层包装。
5. **工具工厂的间接层已无必要**：`_tool_factories()` 放在 `agent_manager` 的理由是避免 SCC（注释），但当前 import 图已 0 SCC，可把工厂直接登记进 `ToolSpec`，新增工具的改动点从 4 处降到 2 处。
6. **进程内状态与前端双基线**：agent 缓存 / SSE hub / 会话 side dict 均在进程内（已知 ARCH-15）；`frontend-vue2`（~38.6k 行）与 `frontend`（~29.2k 行）双基线、`App.vue` 3,292 行、`ChatInput.vue` 4,397 行、无 router/store（已知 ARCH-07）。

### 6.3 "新增一个 X 要改几处"

| 扩展点 | 现在的改动点 | 建议后 |
| --- | --- | --- |
| 新工具 | `tools/xxx.py` + `registry.ToolSpec` + `agent_manager._tool_factories` + 前端 `toolPresentation` | 工具用 `ToolRuntime[SessionContext]`；工厂进 `ToolSpec`；HITL/前端元数据都从注册表派生 → 2 处 |
| 新子代理 | 改 `agent/subagents.py` 静态列表 | 子代理注册表（name / description / prompt / tools / skills / interrupt_on / 启用条件），按会话实际工具裁剪 |
| 新技能 | `skills/` + `scripts/skills/sync_skills.py` + 用户在技能中心"添加"（复制） | 多源只读挂载，新增即全员可见；需要工具的技能用 `include_tools` |
| 新 MCP 连接器 | 全局目录 `config/mcp.json` | 保持；但需先完成 SEC-04 收口（仅 HTTP + 白名单 + 服务端密钥注入） |
| 新模型 | `config.yaml models:` | 补 `profile.max_input_tokens`；按模型的提示词/工具差异用 `HarnessProfile` |
| 新 SSE 事件 | `services/sse.py` 注册 + `docs/sse-events.md` + 契约测试 | 已规范，保持 |
| 新审批工具 | `ToolSpec(is_hitl=True, ...)` | 已规范；MCP 工具改为读 annotations |

---

## 7. 改进路线图

（2026-10-19 晚更新：按实际进度重排，C 批拆成 C1–C3。）

| 批次 | 内容 | 状态 / 规模 |
| --- | --- | --- |
| A · 安全止血 | SEC-01 / 02 / 04 / 05；SEC-03（免密登录）按产品决定保留 | ✅ 已完成（§10） |
| B · 框架对齐·正确性 | DA-01 / 02 / 08 / 10 / 16，及修复中发现的 DA-18 | ✅ 已完成（§9） |
| C1 · 快速项 | DA-06 模型 `profile` + 官方摘要中间件覆盖；DA-12 官方 `ToolErrorMiddleware`；DA-11 子代理工具收窄；DA-09 第一步 MCP annotations 决定审批；DA-17 过时注释与文档；sqlite 连接器 `DB_PATH` 限定个人工作区 | ✅ 已完成（§11） |
| C2 · Backend 重构 | DA-04 默认路由改隐藏目录（修已复现的 deny 别名绕过，框架产物不再出现在用户工作区）；DA-05 改用真实 `CompositeBackend` | ✅ 已完成（§12） |
| C3 · 沙箱加固 | DA-07 网络开关（默认联网，可配置断网）、技能目录只读挂载、execute 超时上限；CPU / 内存 / 进程数限额暂缓（原因见 §13） | ✅ 已完成（§13） |
| D · 演进 | DA-03 thread = 会话（超过摘要阈值后每轮都会重新摘要整段历史；设计文档已评审：`docs/archive/design/2026-10-19-da03-session-thread.md`，按 P0–P2 分阶段实施，P0 / P1 已完成见 §14、§15）；DA-13 技能多源 / 只读 / pinned（设计文档已评审：`docs/design/2026-10-20-da13-skills-sources.md`，按 S1–S3 实施，S1、S2 已完成见 §17、§18，剩 S3 拆分超长 `SKILL.md`）；子代理进度事件流；DA-09 迁移 `langchain.mcp`（beta）；DA-14 LangSmith 沙箱生命周期；`services/` 拆领域子包 | 1–2 周，需设计文档 |
| 穿插项 | CI 缺 `sqlite-saver` extra（暂不处理）；SEC-03 签名免密（需门户配合）；DA-15 前端审批交互（按工具的拒绝说明与去重已完成，逐动作决策与 `edit` 待定）；~~工具工厂并入 `ToolSpec`~~（2026-10-20 评估后保持现状，见 §17）；MySQL 连接器可达内网任意主机（2026-10-19 决定保持现状） | 待定 |

--- | --- | --- |
| A · 安全止血（先做） | SEC-01 下线/鉴权 Web 终端；SEC-02 SPA 回退路径校验；SEC-03 默认关闭免密；SEC-04 用户侧禁止 stdio、取消用户配置的 `${VAR}` 展开；SEC-05 下载接口复用 `resolve_workspace_file` | 1–2 天 |
| B · 框架对齐·正确性（小改动高收益） | DA-01 过滤 `lc_source`；DA-02 多中断按 ID 映射 + 前端逐项决策；DA-08 冲突表改用注册表；DA-10 主流程 `recursion_limit` + 子代理 call-limit；DA-16 关闭 `inherit_env` | 1–2 天 |
| C · 框架对齐·结构 | DA-06 模型 `profile` + 官方中间件覆盖，删除 monkeypatch；DA-04/DA-05 Backend 重构（真实 Composite、隐藏 artifacts 目录）；DA-07 沙箱只读技能挂载/断网/限额；DA-09 MCP annotations 驱动 HITL；DA-11/12 | 3–5 天 |
| D · 演进 | DA-03 thread 模型改为会话级；DA-09 迁移 `langchain.mcp`；DA-13 技能多源 + pinned + skill tools；v3 事件流与子代理进度；DA-14 沙箱生命周期；`services/` 拆领域子包、清理兼容垫片 | 1–2 周，需设计文档 |

---

## 8. 附录

### 8.1 复现脚本（`docs/archive/audit-evidence-deepagents/`）

在仓库根目录用项目虚拟环境执行（`source .venv/bin/activate`）；脚本只用 fake 模型与临时目录，不访问网络、不改仓库文件。

| 脚本 | 对应条目 | 关键输出 |
| --- | --- | --- |
| `exp_summarization_processor.py` | DA-01 | `accumulated_response = 'SUMMARY-TEXT: …FINAL-ANSWER'` |
| `exp_hitl_fanout.py` | DA-02 | UI 只显示 `['delete']`，resume 却是 `{'A': approve, 'B': approve}` |
| `exp_route_alias.py` | DA-04 | `/secret/a.txt permission=allowed read -> 'TOP-SECRET'` |
| `exp_delete_collision2.py` | DA-08 | `TOOL RESULT: delete | CUSTOM delete ran …` |
| `exp_session_thread.py` | DA-03 | 每消息 thread：越过阈值后每轮 1 次摘要；会话 thread 不做适配：`memory V2 seen False`；加刷新后两者兼得 |
| `exp_session_thread_benchmark.py` | DA-03 | 60 轮：摘要 37 次 → 2 次；首次摘要后平均输入 3,592 → 12,438 token；checkpoint 库 16,960 KB → 2,520 KB |

### 8.2 主要参考

- Deep Agents：<https://docs.langchain.com/oss/python/deepagents/overview>（backends / sandboxes / permissions / tools / skills / human-in-the-loop / memory / subagents / context-engineering / going-to-production / fault-tolerance / profiles / event-streaming）
- 版本变更：<https://docs.langchain.com/oss/python/releases/changelog>（deepagents 0.7.0、langchain 1.4.0 / 1.4.6）
- MCP：<https://docs.langchain.com/oss/python/langchain/mcp>、迁移指南 <https://docs.langchain.com/oss/python/migrate/langchain-mcp-adapters>
- HITL 模式对比：<https://docs.langchain.com/oss/python/concepts/human-in-the-loop>
- 官方技能：<https://github.com/langchain-ai/langchain-skills>；插件市场：<https://github.com/langchain-ai/langchain-plugins>

---

## 9. 修复记录（2026-10-19，Batch B）

| 条目 | 改动 | 回归测试（改造前失败） |
| --- | --- | --- |
| DA-01 | `agent/model.py:is_internal_model_call()` 识别 `lc_internal_call` / `lc_source`；`StreamProcessor` 与 CLI `EasyAgent.run()` 跳过中间件内部模型调用的 token | `tests/services/test_stream_internal_calls.py`（含真实 `create_deep_agent` + 摘要中间件） |
| DA-02 | 后端：`hitl_state.plan_resume()` 按 `interrupt_id` 分组作答，旧决策只答第一个中断；`approval_required` 下发全部中断（每项带 `interrupt_id` / `action_index`），tool_call_id 按「工具名 + 参数」对齐；`/agent/chat/resume` 前置校验，非法决策 400。前端（Vue2 + Vue3）：新增 `utils/hitlApproval.js`，卡片/面板只回答所属中断；子代理内部的审批（主消息里没有对应卡片）在消息级审批面板展示——改造前这类审批**没有任何可点的按钮** | `tests/services/test_hitl_multi_interrupt.py`（含真实并行子代理图）、`tests/api/test_chat_resume_validation.py`、两端 `src/utils/hitlApproval.test.js` |
| DA-08 | `services/mcp.py` 冲突名单改为 `tools.registry.builtin_tool_names()` | `tests/services/test_mcp_builtin_conflicts.py`（含「框架实际注入的内建工具 ⊆ 注册表」契约） |
| DA-10 | `agent/run_config.py` 统一构造调用参数，`recursion_limit` 按编译图钩子节点数计算（原 `2*max_steps` 约 6 轮即超限）；子代理（含显式覆盖的 general-purpose）各带 call-limit（`exit_behavior="end"`） | `tests/agent/test_run_limits.py`、`tests/agent/test_subagent_specs.py` |
| DA-16 | `sandboxing/sandbox.py:host_safe_env()` 白名单（含 Windows 必需变量）；默认 execute backend 与 LangSmith 混合 backend 不再 `inherit_env=True` | `tests/agent/test_backend_env.py` |
| DA-18 | 定时任务补传 `context`，与聊天流共用调用参数 | `tests/agent/test_run_limits.py::test_scheduled_task_runs_on_shared_graph` |

有意变更的契约（已同步 golden 测试与 `docs/sse-events.md`）：聊天流 config 新增 `recursion_limit`；`approval_required.action_requests[]` 追加 `interrupt_id` / `action_index`（追加字段，向后兼容）；`general-purpose` 子代理改为显式 spec（描述/提示词沿用上游 `GENERAL_PURPOSE_SUBAGENT`）。

修复后重跑 §8.1 复现脚本：`exp_summarization_processor.py` → `accumulated_response = 'FINAL-ANSWER'`；`exp_hitl_fanout.py` → 单个批准只回答中断 A（`{'A': {'decisions': [{'type': 'approve'}]}}`，B 保持挂起并再次下发），两个决策对 1 个操作则在恢复前被拒绝（接口 400）。

既有问题（非本批引入，已修复）：CI 的 `scripts/quality/check_env_usage.py --check` 在本批改动之前就失败——基线 `bare_getenv_whitelist.json` 仍记录模块迁移（c08206b）前的旧路径，8 处「新增」其实都是搬家。处理：`agent/graph_cache.py` 两处与 `bootstrap/config_validation.py` 一处改用 `env_str`（语义不变，由新增参数化用例钉住）；`bootstrap/env.py`（统一读取入口本身）与 `sandboxing/landlock.py`（沙箱子进程入口，按设计不 import `easy_agent`，按 `os.pathsep` 切分）保留在基线，并按新路径刷新（26 → 23 处）。另在 `tests/test_architecture_gate.py` 增加 `test_repository_env_usage_gate_is_green`：该门禁此前只在 CI 跑，本地 pytest 发现不了搬家导致的基线失效。

## 10. 修复记录（2026-10-19，Batch A）

| 条目 | 处理 | 回归测试（改造前失败） |
| --- | --- | --- |
| SEC-01 | Web 终端默认关闭，`EASY_WEB_TERMINAL_ENABLED=1` 才提供 `/terminal` 与 `/agent/terminal/ws`；连接后首帧必须是 `{"type": "auth", "token": "<JWT>"}`，按 `verify_token_sso` 校验（单点登录 / 停用 / 空闲过期）且只放行 admin，失败以 4401 / 4403 关闭，鉴权通过前不创建 pty。页面复用主界面登录态（同源 localStorage），开发环境可用 `#token=`。README 与 `docs/requirements-agent.md` 同步（原 README 所写的 exec/history 接口、路径翻译、命令拦截均不存在） | `tests/api/test_terminal.py` |
| SEC-02 | SPA 兜底先 `realpath` 再校验必须落在 `frontend_dist` 内，越界 404。实际影响比原记录更大：`os.path.join` 遇绝对路径会丢掉前缀，`GET //etc/passwd` 即可读宿主机任意文件 | `tests/api/test_spa_fallback.py` |
| SEC-05 | `download_file` 经 `resolve_workspace_file` 依次在本用户上传目录、工作区内解析，越界 400。同样存在绝对路径丢前缀：任意登录用户可下载宿主机文件 | `tests/api/test_files.py::test_download_rejects_paths_outside_user_dirs` |
| SEC-04 | 新增 `services/mcp_policy.py`：普通用户的 stdio 连接器必须与公共目录某个 stdio 条目一致（只允许改条目声明的 `fields`），自定义 stdio（任意 command / 多余 args / 未声明的 env / cwd）一律拒绝，admin 不受限。写入（安装 / 改字段 / 原文保存 / 粘贴添加）返回 403；状态校验、工具列表、Agent 加载对已存在的违规项只报错或跳过，不拉起进程。用户 `mcp.json` 不再做 `${VAR}` 展开（改造前连接器页会把展开后的值直接返回给用户），只有管理员维护的全局文件展开。注：内置目录的 `mysql` / `sqlite` 都是 stdio，若一刀切禁止 stdio 会让普通用户用不了内置连接器，故按目录一致性放行 | `tests/services/test_mcp_policy.py`、`tests/api/test_connectors.py`（SEC-04 段） |
| SEC-03 | **保留**：免密登录是要预留的产品特性（Vue2 嵌入 SDK 的登录方式，2026-10-19 决定），本批不改；默认已关闭（`personnel.passwordless_login_enabled=false`），风险说明见 §3 | — |

下载接口的合法用法不受影响：资产面板用列表接口返回的相对路径下载，上传接口返回的路径也落在本用户上传目录内（`test_download_by_listed_relative_path`）。

遗留观察（本批未处理）：

- **CI 后端测试仍红**：`langgraph-checkpoint-sqlite` 在可选 extra `sqlite-saver` 里，CI（`uv sync --group dev --extra docs`）与 Dockerfile（`--extra docs`）都不安装，导致 5 个官方 SQLite checkpointer 用例失败；Docker 部署也会静默回退自研 checkpointer。此前 CI 卡在 env-usage 门禁，没跑到测试这一步。2026-10-19 决定暂不处理。
- **内置 `sqlite` 连接器的 `env.DB_PATH`**：该字段允许普通用户填写任意路径，stdio 进程在宿主机上运行，可指向应用自身的 SQLite 业务库等文件（默认值就是业务库）。→ C1 已修，见 §11。

## 11. 修复记录（2026-10-19，C1）

| 条目 | 改动 | 回归测试（改造前失败） |
| --- | --- | --- |
| DA-06 | `agent/model.py:apply_context_profile()` 把配置的 `context_length` 写进 `profile["max_input_tokens"]`（只在显式配置时覆盖，其余能力字段保留）；`agent/summarization.py:build_summarization_middleware()` 构造官方 `SummarizationMiddleware`，主代理与每个子代理 spec 各放一份、由框架同名原位替换；删除 `configured_summarization`（建图期替换私有工厂）。摘要阈值与改造前一致 | `tests/agent/test_summarization_override.py`、`tests/agent/test_model_profile.py`、`tests/services/test_summarization_config.py`、`tests/test_framework_contract.py` |
| DA-12 | 新增 `agent/tool_errors.py`：官方 `ToolErrorMiddleware` + 可恢复白名单（`ToolException`、`ValueError`、`TimeoutError`、`OSError`、`httpx.HTTPError`、`McpError` 及只由它们组成的 `ExceptionGroup`），回灌文本隐去宿主机路径并截断；删除 `services/logging_middleware.py`。**行为变化**：`TypeError` / `KeyError` 等程序错误不再回灌模型，本轮中断并记录完整堆栈 | `tests/agent/test_tool_errors.py` |
| DA-11 | `agent/subagents.py`：`use_tools` / `requires_tools` + `resolve_subagent_tools()`，建图时解析成本图的工具实例；office-writer 只带 `read_uploaded_file`，knowledge-researcher 只带 `access_knowledge_original` 且仅在注入时声明；general-purpose 按官方语义继承 | `tests/agent/test_subagent_tools.py`、`tests/agent/test_subagent_specs.py` |
| DA-09（第一步） | `agent/hitl.py:mcp_annotation_decision()`：`destructiveHint=True` 或 `readOnlyHint=False` → 审批，`readOnlyHint=True` → 免审批，无显式标注回退名字启发式。annotations 是服务端自我声明，连接哪个 MCP 服务由用户 / 管理员决定 | `tests/agent/test_hitl.py`（DA-09 段） |
| DA-17 | `core.py` / `graph_cache.py` / `hitl.py` / `agent_manager.py` 注释更新到 0.7 现状；`docs/framework-integration.md` 适配点 3 → 2 处，模块路径更新到重组后的位置 | `tests/test_docs_facts.py::test_framework_integration_doc_points_to_existing_code` |
| sqlite `DB_PATH` | 公共目录字段声明 `"path": "workspace"`，默认值不再指向业务库；写入时相对路径换算到个人工作区；普通用户的路径 `resolve()` 后必须在个人工作区内（`..` 与符号链接都逃不出去），admin 不受限 | `tests/services/test_mcp_policy.py`（工作区路径段）、`tests/api/test_connectors.py`（sqlite 段） |

## 12. 修复记录（2026-10-19，C2）

| 条目 | 改动 | 回归测试（改造前失败） |
| --- | --- | --- |
| DA-04 | 会话 composite 的 default 改为 `backends/artifacts.py:AgentArtifactsBackend`：文件根是工作区之外的会话隐藏目录 `workspace/{user}/.agent/{session_id}/`（`agent/backend.py:agent_artifacts_dir()`），只允许写框架产物前缀 `artifacts_root="/.agent"`，写到别处返回「请写到 /workspace/ 下」；`execute` 原样转给会话 shell（工作目录、路径翻译、沙箱、LangSmith 同步都不变）。删除会话时一并删除隐藏目录，用户级工作区树隐藏 `.agent` | `tests/agent/test_backend_routing.py`（DA-04 段）、`tests/api/test_sessions.py::test_delete_session_removes_its_artifacts_dir`、`tests/test_framework_contract.py::test_framework_artifact_paths_follow_artifacts_root` |
| DA-05 | `backends/context.py` 改为两层真实 `CompositeBackend`：`SessionCompositeBackend` 的 default / routes / sorted_routes 按运行期 context 解析成本会话的 composite，路由沿用框架实现，框架的 symlink 感知权限校验能递归进来；顶层 `ContextBackend` 的 default 是会话 composite，`routes` 只登记前缀（满足「带 execute 时权限路径须落在 route 下」），`sorted_routes` 置空（完整路径交给会话层，也不生成 route→宿主机路径提示）。去掉 `CompositeBackend.register` 冒充子类与占位 default / routes | `tests/agent/test_backend_routing.py`（DA-05 段） |

为什么顶层仍要把 `sorted_routes` 置空、而不是直接把真实路由交给框架：框架会据此在系统提示词里写「执行命令时把虚拟路径换成宿主机路径」。本项目的沙箱按虚拟路径挂载目录（bwrap 里宿主机路径不存在），而且刻意不向模型暴露宿主机路径，这段提示既不可用也不该出现。会话层用的是真实路由，权限校验不受影响。

改造前后对照（同一脚本分别跑在 C1 提交与当前代码上）：`/secret/a.txt` 读到 `TOP-SECRET` → 文件不存在；框架产物 `large_tool_results/call_abc` 写进会话工作区 → 不在工作区；`/workspace/innocent/a.txt`（`innocent` → `secret` 的 symlink）权限放行 → `permission denied … (resolves to /workspace/secret/a.txt)`。§8.1 的 `exp_route_alias.py` 重跑结果同样已变为「not found / 工作区内无产物」。

行为变化：模型若把文件写到 `/report.md` 这类不在任何挂载目录下的路径，改造前会落进工作区（碰巧可见），现在会收到「请写到 /workspace/ 下」的错误后改写。

## 13. 修复记录（2026-10-19，C3 与清理项）

| 条目 | 改动 | 回归测试 |
| --- | --- | --- |
| DA-07 网络 | 新配置 `agent.sandbox_network`：`host`（默认，与改造前一致）/ `none`（bwrap 不再 `--share-net`，只剩回环）。Landlock 降级模式受内核限制（ABI v1 只管文件系统）无法断网，设为 `none` 时记一次告警。按 2026-10-19 决定默认联网 | `tests/integration/test_sandbox_hardening.py`（含真实 bwrap 下 `/proc/net/dev` 只剩 `lo`） |
| DA-07 技能目录 | `agent.skill_write` 落实到 execute 沙箱（改造前只约束文件工具）：`new-only` 时已有的每个顶层条目以只读覆盖在可写的技能根上，仍可新建技能；`deny` 整个技能根只读；Landlock 无法在可写目录里再收紧，`new-only` / `deny` 都把技能根整体改为只读 | 同上（真实 bwrap / Landlock 端到端：改已有技能失败、新建技能成功、deny / allow 各一例） |
| DA-07 超时 | 新配置 `agent.execute_max_timeout`（默认 600 秒）：模型给 execute 指定的超时超过上限时按上限执行，在会话 default（`AgentArtifactsBackend.execute`，所有 shell 的统一入口）里收口 | `tests/integration/test_sandbox_hardening.py::test_model_requested_timeout_is_capped` |
| 死代码 | 删除 `services/mcp.py:validate_mcp_servers`（无调用方，且会绕过 SEC-04 策略直接拉起进程）；其中的 stdio 预检（命令 / 工作目录不存在时给直白提示）移到连接器接口实际在用的 `_validate_one` | `tests/api/test_connectors.py::test_validate_reports_a_missing_stdio_command_without_spawning` |
| 下载路径 | 上传接口返回的落库路径相对服务进程目录（生产配置下上传目录是相对路径），下载接口改为也能解析这种路径，仍只认本用户目录内的真实文件 | `tests/api/test_files.py::test_download_accepts_the_stored_upload_path` |
| CI 维护 | `actions/checkout`、`actions/setup-python`、`actions/setup-node` 升到 v7，`astral-sh/setup-uv` 钉到 v10.2.0（不发布浮动主版本标签），都基于 Node 24 运行时；前端构建 Node 20（已 EOL）→ 24（本地构建已验证）；runner 固定 `ubuntu-24.04` | — |

暂缓：CPU / 内存 / 进程数限额。`RLIMIT_AS` 会让 Node（V8 预留大块虚拟内存）直接起不来；`RLIMIT_NPROC` 按宿主机 UID 计数，服务进程自身的线程也算在内；cgroup 限额要求 systemd / cgroup v2 委派，常见容器部署没有。需要时建议在部署层（容器 / Pod 资源限制）兜底。

更正：§7 早先列出的「`tests/api/test_files_store_backend.py` 依赖执行顺序」是误判——当时本地 `.venv` 被切换分支后的依赖覆盖成 deepagents 0.6.8（没有 `StoreBackend.delete`），在与 `uv.lock` 一致的环境里该文件单独运行全部通过。

决定：MySQL 连接器由服务器去连用户填写的主机，可达服务器能访问的内网数据库；2026-10-19 决定保持现状，风险在此记录。

## 14. 修复记录（2026-10-20，DA-03 P0）

thread 仍是每条消息一个，模型看到的内容不变；本批为切换到会话级 thread（P1）铺路。详细记录见设计文档 §13。

| 改动 | 回归测试 |
| --- | --- |
| 记忆每次运行都重新加载：`agent/memory.py` `SessionMemoryMiddleware` 同名替换框架默认项（框架每个 thread 只读一次记忆） | `tests/agent/test_session_memory.py` |
| 本轮注入（工作区前缀、知识库 / 页面上下文、上传预览、技能指令）不再拼进本轮 `HumanMessage`，改随 `turn_context` 传入；主代理的摘要中间件 `TurnAwareSummarizationMiddleware` 在计 token 之前渲染，并剥离历史推理 | `tests/agent/test_turn_context.py`、`tests/services/test_stream_split_golden.py::test_chat_stream_input_*` |
| 用户消息落库带 `turn_id`（= message_id） | `tests/api/test_chat.py::test_stream_records_turn_id_on_the_user_message`、`tests/api/test_expert_helpers.py::test_persist_user_turn_records_turn_id` |
| 删除会话时删除官方 saver 里该会话的 thread（含每条消息一个的旧 thread）；定时任务每次运行结束后删除自己的 thread。改造前官方 saver 的 thread 只增不减 | `tests/services/test_thread_cleanup.py`、`tests/api/test_sessions.py::test_delete_session_deletes_its_checkpoint_threads`、`tests/agent/test_run_limits.py::test_scheduled_task_deletes_its_thread_after_the_run` |

框架适配点新增两处（`SessionMemoryMiddleware`、`TurnAwareSummarizationMiddleware`），已登记在 `docs/framework-integration.md`。

## 15. 修复记录（2026-10-20，DA-03 P1）

新增开关 `agent.session_thread`，默认关闭。打开后，在官方 saver 上一个会话一个 thread，详细记录见设计文档 §14。

| 改动 | 回归测试 |
| --- | --- |
| `thread_id = session_id`，每轮只输入本轮消息；thread 为空时从 DB 水合；DB 里排在 thread 最后一轮之后的轮次补进输入；补回取消时用户已看到的半截回复 | `tests/services/test_session_thread.py` |
| 审批句柄仍是 `{session_id}-{message_id}`，恢复接口映射到会话 thread，失效返回 409；兼容期支持升级前挂起的审批；新消息把遗留的待审批标为 `rejected` | 同上，加 `tests/api/test_session_thread_api.py` |
| 本轮起点 checkpoint 记到本轮用户消息（`turn_checkpoint_id`），留给将来的编辑 / 重新生成 | `tests/api/test_session_thread_api.py` |
| **修复（P1 发现，影响现状）**：legacy `PersistentMemorySaver` 原先按 thread 只留 2 个 checkpoint，而 deepagents 0.7 的 `messages` 是 `DeltaChannel`（只存增量），运行刚结束 messages 就重建成空列表，MySQL 回退部署下 HITL 恢复时模型看不到对话。现在只按 thread 数裁剪；legacy saver 下也不启用会话 thread | `tests/db/test_checkpoint_pruning.py`（改造前的代码上会失败） |
| 定时任务用 agent 编译图自己的 checkpointer 删除本次运行的 thread（legacy 快照也适用） | `tests/agent/test_run_limits.py::test_scheduled_task_deletes_its_thread_after_the_run` |

更正：设计评审稿里两处说法不成立，已在设计文档 §6.8 改正。一是"`messages` 每个 super-step 都写一份完整列表"：`DeltaChannel` 只存增量，会话 thread 的 checkpoint 总量线性增长，每消息 thread 是平方。二是"legacy saver 逻辑不用改"。

## 16. 进展记录（2026-10-20，DA-03 P2）

- 存量清理脚本 `scripts/db/prune_checkpoint_threads.py`：清理官方 saver 里的每消息 thread 与定时任务 thread，默认只预览；会话 thread、近期仍有写入的、挂着待审批的 thread 保留。测试见 `tests/db/test_prune_checkpoint_threads.py`。
- 基准 `exp_session_thread_benchmark.py`（结果见设计文档 §15.1）：会话 thread 把摘要调用从每轮一次降到偶尔一次，模型可见的近期上下文约为原来的 3.5 倍，checkpoint 库约为原来的 1/7。
- 本地 `.env.dev` 的业务库是 MySQL 且没配 checkpoint 路径，跑的是 legacy 快照；要试运行会话 thread 需设置 `EASY_CHECKPOINT_SQLITE_PATH` 与 `SESSION_THREAD=true`。
- 剩余：真实模型试运行、生产执行清理脚本、观察一个版本后删除审批兼容路径并把默认值改为开启。

## 17. 修复记录（2026-10-20，遗留项推进）

| 条目 | 改动 | 回归测试 |
| --- | --- | --- |
| 记忆更新后驱逐 Agent | 删除 `streaming/side_effects.py` 里记忆写入后的 `remove_session_agent`：记忆每次运行都由 `SessionMemoryMiddleware` 重新加载，不需要重建。原来的驱逐有两个副作用：下一轮重新加载 MCP 工具、重建 Agent；顺带清掉会话的「待修改专家」绑定。后者是 bug：前端只在第一次发送时带上待修改专家，用户点「编辑」后模型先追问、下一轮再保存时，`save_expert` 会报找不到修改目标 | `tests/services/test_memory_update_keeps_agent.py`（改造前的代码上会失败） |
| DA-15 拒绝说明 | 后端按工具生成拒绝说明（`hitl_state.reject_message`）：拒绝决策没带说明，或带的是旧前端写死的「请勿重试此删除命令」时，按删除 / 命令 / 定时任务 / 专家 / 知识原文分别说明；两套前端不再发这句写死的文案 | `tests/services/test_hitl_reject_message.py`、两套前端的 `hitlApproval.test.js` |
| DA-15 去重 | Vue2 `App.vue` 里与 `chatSession` mixin 逐行相同的 `handleToolApproval`（112 行）删除，主面板与 AI 侧边栏共用 mixin 的实现 | Vue2 单测与构建 |
| 工具工厂并入 `ToolSpec` | 评估后保持现状：注册表是无包内依赖的叶子模块，工厂由装配根（`agent_manager._tool_factories`）注入，是有意的依赖注入；搬进 `ToolSpec` 只少一层间接，代价是注册表要依赖全部工具实现、测试改成替换 builder，收益不足 | — |
| DA-13 S1 | 选中的技能改用 `pinned_skills` 加载：第一次模型调用前技能全文已在对话里，不必先 `read_file`；不再拼 `/目录名` 指令（手打的照常生效）。`save_expert` 由技能带出（§6.3）改随 S2 实施，因为用户手里的 expert-creator 是没有 `include_tools` 的旧副本 | `tests/services/test_pinned_skills.py`、`tests/api/test_chat.py` |
| DA-13 | 写出设计文档（2026-10-20 已评审）：公共技能只读共享并按用户启停、选中技能改用 `pinned_skills`、`save_expert` 由 expert-creator 技能的 `include_tools` 带出、拆分超长 `SKILL.md` | — |
| 文档 | `CLAUDE.md` 删掉已不存在的 `shared_deps` / `.deps` 共享依赖描述（该机制已在 2026-06-09 `f8eafa3b` 删除，旧审计 T4-16 曾指出） | `tests/test_docs_facts.py` |

## 18. 修复记录（2026-10-20，DA-13 S2）

| 条目 | 改动 | 回归测试 |
| --- | --- | --- |
| 公共技能只读共享 | 技能中心「添加」不再复制，只在 `workspace/{user}/builtin-skills.json` 记一条；agent 经 `/skills/builtin/` 直接读 canonical 目录，只看得到已添加且启用的（视图 backend），写入一律拒绝（backend + deny write）；技能源固定为 `["/skills/builtin/", "/user-skills/"]`，同名时用户技能覆盖；execute 沙箱 bwrap 只挂已启用的，Landlock 整个目录只读 | `tests/agent/test_builtin_skills.py`、`tests/agent/test_builtin_skills_agent.py`、`tests/integration/test_sandbox_builtin_skills.py` |
| 旧副本迁移 | 与公共版本一致的副本在第一次访问时删除并记入清单，改过的保留为用户技能；`scripts/skills/migrate_builtin_skills.py` 一次性预览 / 执行 | `tests/agent/test_builtin_skills.py`、`tests/api/test_skill_center.py` |
| `save_expert` 由技能带出 | expert-creator 声明 `metadata.include_tools: save_expert`；`save_expert` 交给替换默认项的 `SkillsMiddleware(tools=...)`，读到或 pin 了技能才绑定；删除 `creator_requested` 及随之的 agent 重建。设计稿里的 `defer_loading` 在 OpenAI 兼容接口下不隐藏工具，没有采用 | `tests/agent/test_builtin_skills_agent.py`、`tests/test_expert_writer.py` |
| 技能中心路径穿越（改造前就有） | `remove-skill` 等接口直接拼「用户技能目录 / dir_name」，`../../别的用户/skills/x` 能删掉别的用户的技能；现在七个接口统一校验目录名（400） | `tests/api/test_skill_center.py::test_skill_endpoints_reject_path_traversal` |

细节与实施中的调整见设计文档 §14。
