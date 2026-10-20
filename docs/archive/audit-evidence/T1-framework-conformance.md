# T1 · DeepAgents 官方框架规范一致性审计

- 审计对象：`easy-agent`（当前工作区实际代码，非 CLAUDE.md/AGENTS.md 描述）
- 权威基准：**已安装 deepagents 0.6.8 源码**（`.venv/lib/python3.11/site-packages/deepagents/`）+ LangChain 官方文档（docs.langchain.com/oss/python/deepagents/*，当前线上文档已到 0.7.x）
- 已安装版本：deepagents 0.6.8 / langchain 1.3.4 / langchain-core（1.3.x）/ langgraph 1.2.4 / langchain-mcp-adapters 0.2.2
- 只读约束遵守：未修改任何业务代码/配置；仅在 `_analysis/` 下新增验证脚本 `t1_probe*.sh / t1_verify_create.py`
- 证据方式：每条结论均有 `file:line` + 代码原文；框架侧行为全部经已安装源码/实际建图验证，未使用记忆推测

---

## 1. 结论摘要（10 条）

1. **当前装配在 0.6.8 上是可运行的**（已实测 `create_deep_agent` 用仓库同参建图成功），但**大量能力靠私有/内部 API 搭桥**：`FilesystemMiddleware.__init__` 全局 monkeypatch、`deepagents.graph.create_summarization_middleware` 私有工厂替换、`MemorySaver` 私有属性 pickle，任一上游小版本变化都会静默或硬失败。
2. **最大的系统性风险是依赖无上界**：`pyproject.toml` 写 `deepagents>=0.1.0`，而 PyPI 最新 **0.7.28** 要求 `langchain>=1.4.6`（现装 1.3.4），且 0.7 起 `write_todos` 改为 opt-in、`FilesystemMiddleware` 增加 `tools` 白名单、`delete` 工具出现——一次 `uv lock --upgrade` 即可让本项目内建工具面与适配代码同时失效。**建议立即加 `<0.7` 上界**。
3. **HITL 恢复接口把客户端传入的 `thread_id` 直接当 LangGraph thread 用**（`api/chat.py:674,707`），未校验其属于当前 session；一旦启用官方 `AsyncPostgresSaver`（全局 saver，`app.py:310-320`），这是跨会话 checkpoint 读取/续跑的通道（与 T3 交叉，建议 P0 复核）。
4. **execute 工具描述注入方式违反"用公开 API"原则**：`hitl.py:94-114` 在进程内改写 `FilesystemMiddleware.__init__`，而 0.6.8 已提供公开的 `custom_tool_descriptions` 参数 + **beta 的 `HarnessProfile.tool_description_overrides`**（`harness_profiles.py:279`，`graph.py:614` 会把它传给 `FilesystemMiddleware`）——官方已有能力，不必 monkeypatch。
5. **摘要阈值没有公开参数，仓库的绕行可以理解但很脆**：替换的是 `deepagents.graph.create_summarization_middleware` 这个未导出私有名，且丢掉了官方工厂的 `summary_prompt`/`token_counter` 参数；建议加断言/回归测试并锁版本。
6. **MCP 工具名未做命名空间/去重**：`MultiServerMCPClient(servers)` 默认 `tool_name_prefix=False`，LangChain `ToolNode` 同名工具 **last-wins 静默覆盖**；MCP server 若暴露 `execute`/`read_file`/`write_todos`/`task`，或两个 server 工具同名，会静默顶掉内置工具，且 HITL 是按名字匹配的（可能审批到被替换后的工具）。
7. **内置 7 个公共技能没有走官方 skills 多源机制**：`skills=` 只传 `/user-skills/`，公共 `skills/` 靠"复制到用户目录启用"；官方推荐 `skills=[builtin, user]`（后者覆盖前者），当前方式导致公共技能更新不同步、无法 last-wins 分层、frontmatter 未校验。
8. **自定义 subagent 未声明 `skills`**，官方文档明确"custom subagent 不继承主 agent 技能"，`office-writer` 却让子代理去读硬编码 `/user-skills/docx/SKILL.md`；且自定义 subagent 默认继承父代理**全部 tools**（含 MCP 写工具）。
9. **`max_steps` 与框架语义脱节**：deepagents 内部硬编码 `recursion_limit=9999`（`graph.py:855`），仓库只用 `ModelCallLimitMiddleware(run_limit=max_steps)` + `ToolCallLimitMiddleware(run_limit*4)`（`agent.py:662-674`）；两者默认 `exit_behavior` 为 `end`/`continue`（静默截断，无 SSE 提示），且**子代理 middleware 栈不含 call-limit，子代理步数不受 max_steps 约束**。
10. **官方推荐能力存在"重复造轮子"**：自研 `PersistentMemorySaver`（pickle MemorySaver 私有属性）对应官方 `AsyncPostgresSaver`/`SqliteSaver`；自研 `SqlStore` 对应官方 `BaseStore` 语义（且实现不完整）；MCP SSE 默认对应官方 Streamable HTTP。文档（CLAUDE.md/AGENTS.md）已与代码漂移（`/skills/`→`/user-skills/`、17→7 个技能、deepseek 直连→仅 openai/anthropic 协议）。

---

## 2. 问题清单

| ID | 严重度 | 类别 | 位置(file:line) | 现象与证据 | 风险/影响 | 建议修法 | 工作量 |
|---|---|---|---|---|---|---|---|
| T1-01 | **P1** | 版本/兼容 | `pyproject.toml:14`（`deepagents>=0.1.0`）；`agent.py:20,21`；`summarization.py:47`；`hitl.py:100` | 依赖无上界；代码绑定 0.6.8 内部实现。PyPI 最新 0.7.28 要求 `langchain>=1.4.6`；官方文档："Starting in v0.7 task planning is opt-in only"（`write_todos` 不再默认）、`delete` 工具需 `deepagents>=0.7` | `uv sync/upgrade` 后：`write_todos` 消失（提示词/前端 todos 依赖它）、`FilesystemMiddleware` 签名变化使 monkeypatch 失效、摘要工厂被移除/改名 → 启动期或首轮对话硬失败 | 锁定 `deepagents>=0.6.8,<0.7`、`langchain>=1.3,<1.4`；新增"框架适配冒烟测试"（见附录 B `t1_verify_create.py` 思路）纳入 CI；升级 0.7 单独立项 | S（锁版本）+ M（升级适配） |
| T1-02 | **P1** | HITL/隔离 | `api/chat.py:674,707`；`services/streaming.py:1225,1352`；`models/api.py:106`；`app.py:310-320`；`checkpoints.py:127-131` | `thread_id = request.thread_id` 直接来自前端；仅用 `get_owned_session(db, session_id, username)` 校验 session 归属，**未校验 thread_id 与 session_id 绑定**；Postgres 模式所有会话共享一个 saver | 已登录用户 A 传 `session_id=自己的` + `thread_id=<他人 session-msg>` → 读取/续跑他人 checkpoint（消息历史、待审批操作），跨用户隔离被绕过 | resume 前强校验 `thread_id.startswith(f"{session_id}-")`；更稳：从 DB `pending_approval` 取 thread_id，不信任前端；MemorySaver 模式按 session 取 checkpointer 也能兜底 | S |
| T1-03 | **P1** | 框架适配 | `hitl.py:94-114`；`agent.py:92-94,412-413` | 全局替换 `FilesystemMiddleware.__init__` 注入 execute 描述；`framework_patch_status()` 只返回"patch 是否执行过"（`_EXECUTE_DESC_INSTALLED`），不验证上游是否仍接受 `custom_tool_descriptions` | 上游改签名时：patch 包装仍"安装成功"→ 建图期 `TypeError: unexpected keyword`（所有会话创建失败）或静默丢失描述；健康检查给出假阳性 | 用公开机制：`register_harness_profile(model_key, HarnessProfile(tool_description_overrides={"execute": ...}))`（0.6.8 已导出 `HarnessProfile`，`graph.py:614,698` 会传入）；或至少把"实例化一个 FilesystemMiddleware 验证 kwarg 生效"写进自检 | S |
| T1-04 | **P1** | 框架适配 | `summarization.py:44-70`（尤其 47,53,66）；`agent.py:501-516` | 建图期间把 `deepagents.graph.create_summarization_middleware` 换成自定义 factory；该函数未在 `deepagents.__init__.__all__` 导出，是模块私有实现；替换后忽略官方 `summary_prompt`/`token_counter`/`trim_tokens_to_summarize` 入参 | 0.6.8→0.7 若函数改名/搬移/改为内部调用 → 阈值静默失效（回落到框架默认 0.85/0.10），或建图期 AttributeError；同时丢失官方的 token_counter 可配置性 | 保留绕行但：①锁版本；②`graph.create_summarization_middleware` 不存在时显式 warning/降级；③新增单测断言 `settings["trigger"]` 生效；④向 deepagents 提议公开参数（现无公开开关，见 §4.3） | S（护栏） |
| T1-05 | **P1** | MCP/安全 | `services/mcp.py:270-277,373`；`agent.py:487-493`；适配器 `client.py:__init__(tool_name_prefix=False)`；`tool_node.py:773-784` | MCP 工具名直通（无 server 前缀、无冲突校验）；`ToolNode._tools_by_name[tool_.name]=tool_` 同名 last-wins 静默覆盖；HITL 以工具名为 key | MCP server 暴露 `execute`/`read_file`/`task`/`write_todos` 时静默顶掉内建工具（或被内建顶掉），模型行为不可预期；HITL 审批匹配到错误实现，安全边界被绕过 | `MultiServerMCPClient(servers, tool_name_prefix=True)`；加载后校验与内建工具名（`ls/read_file/write_file/edit_file/glob/grep/execute/task/write_todos`）及跨 server 去重，冲突则拒绝/重命名并告警 | S |
| T1-06 | **P2** | 持久化 | `checkpoints.py:26-93`（`_hydrate` 35-54、`_flush` 63-73、`put/aput` 75-93） | 自研 `PersistentMemorySaver` 直接 pickle `MemorySaver.storage/writes/blobs`（langgraph 1.2.4 中确为这三个私有属性：`langgraph/checkpoint/memory/__init__.py:93-95`），每次 `put/put_writes` 全量 pickle 并同步写业务库；无 `delete_thread`/清理 | ①私有属性无版本契约，langgraph 升级即降级失效（`hasattr` 守卫导致**静默**不恢复）→ 重启后审批恢复失败；②全量 pickle 写放大 + 同步 DB 写在 async 路径上（`aput` 里调用 `_flush`）阻塞事件循环；③多副本下按 session 回放整份 MemorySaver，语义上是"快照覆盖"而非 checkpoint 增量 | 一律使用官方 saver：生产设 `EASY_CHECKPOINT_POSTGRES_URI` 用 `AsyncPostgresSaver`，本地/单机用 `langgraph-checkpoint-sqlite` 的 `SqliteSaver`；删除自研 saver 或仅作降级并显式告警 | M |
| T1-07 | **P2** | 步数/预算 | `agent.py:267,662-674`；`services/streaming.py:939-941`；框架 `graph.py:855` | `max_steps` 只折算成 `ModelCallLimitMiddleware(run_limit)`、`ToolCallLimitMiddleware(run_limit*4)`；deepagents 0.6.8 `.with_config({"recursion_limit": 9_999})` 硬编码；`astream` config 只有 `thread_id`（`streaming.py:941`） | ①`ModelCallLimitMiddleware` 默认 `exit_behavior="end"`、`ToolCallLimitMiddleware` 默认 `"continue"` → 超限**静默结束/跳过工具**，前端无明确错误；②`max_steps` 不约束子代理（子代理 middleware 栈 `graph.py:617-631` 不含 call-limit），一个 `task` 内可跑至 9999 步 → 成本失控窗口 | 显式传 `exit_behavior="error"` 或捕获后回传明确 SSE 错误；`stream_config["recursion_limit"] = max_steps`（或 2*max_steps）让子代理也受控；在配置文档中说明 max_steps 的真实语义 | S |
| T1-08 | **P2** | Skills 机制 | `agent.py:507,895-906`；`api/skill_center.py:490-560`；`services/skill_library.py:1-25`；`skills.py:45-91` | `skills=skills_paths or None`，`_resolve_skills_paths()` 只返回 `/user-skills/`（且有技能才返回）；公共 `skills/`（7 个：docx/expert-creator/mcp-builder/pdf/pptx/skill-creator/xlsx）不在 sources 中；"启用技能"=把公共目录复制进 `workspace/{user}/skills/`（`skill_center.py:496-560`），"停用"=改名移到 `skills-disabled/`（`skill_library.py`） | 未启用技能的会话看不到任何技能元数据（官方 progressive disclosure 失效）；公共技能更新不会同步到已复制的用户目录（漂移）；`discover_skills` 只判断 `SKILL.md` 存在、不校验 Agent Skills 规范 frontmatter（`name` 必须=目录名、`description`≤1024） | 改为官方多源：`skills=["/skills/", "/user-skills/"]`（后者 last-wins），为公共技能挂只读 backend 路由；"启用/停用"改为用户偏好过滤（在 `sources` 或自定义 middleware 里做），不再复制文件；启动期用 `agentskills` 规范校验 frontmatter | M |
| T1-09 | **P2** | Subagents | `subagents.py:3-28`；框架 `graph.py:665-675`、`subagents.py:SubAgent.skills` | 两个自定义 subagent 只有 `name/description/system_prompt`；官方文档：`skills` 字段"Does not inherit from main agent. Only the general-purpose subagent inherits the main agent's skills"；`office-writer` 描述依赖硬编码 `/user-skills/docx/SKILL.md`、`/user-skills/pptx/SKILL.md`、`/user-skills/xlsx/SKILL.md`；未声明 `tools` 时继承父代理全部工具（含 MCP 写工具、knowledge 工具） | 用户未启用 docx/pptx/xlsx 技能时，子代理被引导去读不存在的路径 → 无效重试/幻觉；子代理持有全部 MCP 写工具而自身 system_prompt 未提审批约束（HITL 会继承 interrupt_on，但提示词不一致） | 给 subagent 显式 `skills=["/user-skills/"]`；或改为 `CompiledSubAgent`；显式 `tools=[...]` 收窄（如 office-writer 仅需文件工具 + 对应 skill 脚本能力）；描述里改为"若技能存在" | S |
| T1-10 | **P2** | MCP/规范 | `services/mcp.py:226-256`（尤其 248-254）；`api/connectors.py:354-363`；适配器 `sessions.py:130-194` | URL-only 配置默认 `transport="sse"`（注释：只有 url 的按 sse 处理）；MCP 2025-03-26 起 Streamable HTTP 为推荐传输、SSE 属旧传输（适配器仍支持）；`update_connector_config` 白名单只放开 `url/headers.*/env.*`，未支持适配器已提供的 `auth`（OAuth）、`timeout`/`sse_read_timeout` | 新建远程连接器默认走旧传输（部分新 server 已不提供 SSE）；无 OAuth 能力 → 只能静态 key；未暴露超时 → 用适配器默认（SSE 连接默认 5s HTTP timeout，见 `sessions.py:141-146`），慢 server 易超时 | 默认改为 `streamable_http`（兼容 `"http"`/`"streamable-http"` 别名）；透传 `timeout`/`sse_read_timeout`；OAuth 作为后续项单独立项（MCP 指南确有支持） | S |
| T1-11 | **P2** | MCP/资源 | `services/mcp.py:266-277,394-418,359-370`；适配器 `client.py:161` | `_mcp_clients` 以"配置 JSON"为 key 缓存 client，无上限、`invalidate_mcp_cache()` 只清 tools 缓存不清 clients；适配器 stateless 客户端"each tool call creates a fresh MCP session"（`client.py:161` 注释），stdio 即每次调用重启子进程 | 配置反复修改 → client 对象泄漏；stdio MCP 工具每次调用拉起子进程（延迟+资源），多副本生产各起一份（仓库自己在 `mcp.py:359-370` 也 warning）；工具调用并发高时进程风暴 | `invalidate_mcp_cache` 同步清理 clients（按配置指纹）；stdio 服务改用 `client.session()` 长连接或部署为 streamable_http；对 stdio 工具调用加并发/超时保护（与 T2 交叉） | M |
| T1-12 | **P2** | Store/重复实现 | `stores.py:40-52,99-132`；`agent.py:495-499,514`；框架 `memory.py:320-328` | 传入 `store=get_agent_store()`（自研 `SqlStore`），但 backend 全为 LocalShell（MemoryMiddleware 走 `backend.download_files`）；`store` 实际只暴露给 tools/subagent 的 `runtime.store`；`SqlStore` 只实现 `batch/abatch`，`SearchOp.filter` 忽略 dict 值、`ListNamespacesOp` 只处理 prefix/suffix、排序/limit 语义简化 | 官方 store 能力的重复实现且不完整；后续若用 `StoreBackend`（跨线程记忆/技能共享的官方推荐）会踩语义差异；现在又几乎是死代码（维护成本+误导） | 二选一：①删除自研 store，传 `None`；②若确需跨线程记忆，用官方 `StoreBackend`/`InMemoryStore`/Postgres store 并补全 `SqlStore` 语义与一致性测试 | M |
| T1-13 | **P2** | 文档漂移 | `CLAUDE.md:44,48,50,100-102`；`AGENTS.md`（Skills/MCP 描述） | 文档称：`/skills/` 只读路由、17 个技能、`deepseek`→`langchain-deepseek`、README.md 也算技能；实际代码：仅 `/user-skills/` 路由（`agent.py:890-906` 无 `/skills/` route）、7 个技能目录、`model.py:1-9` 只支持 `openai|anthropic` 协议、`skills.py:45-91` 只认 `SKILL.md`（且 `langchain-deepseek` 未安装） | 新人/维护者按文档改代码会引入 bug；审计与验收基线失真（与 T4 交叉，框架维度单列） | 更新 CLAUDE.md/AGENTS.md 的 Agent 层/Skills/MCP/模型章节；把"框架适配点清单"（monkeypatch/私有 API/版本上界）单独写成 `docs/framework-integration.md` | S |
| T1-14 | **P3** | HITL 语义 | `agent.py:467-493`；`hitl.py:34,121-132`；`backends/shell.py:87-94` | `interrupt_on` 预置 `save_expert`/`create_scheduled_task`/`access_knowledge_original`（可能未注册，HITL 对未知名静默无效，见 `human_in_the_loop.py:after_model`）；目录删除不进 HITL 而是在 backend `execute` 里直接返回错误（`when` 返回 False = 自动放行语义 + 自定义拒绝策略） | 配置看似覆盖很多工具，实际部分无效（误导审计/运维）；目录删除无法走"用户批准"流程，与工具描述里"会被直接拒绝"一致但未在规范层面文档化 | 按实际注册工具动态构造 `interrupt_on`；目录删除要么纳入 HITL（`when=True`）要么在 README/提示词中明确"策略拒绝"；删除未注册项或加启动期告警 | S |
| T1-15 | **P3** | 异常/子代理 | `agent.py:654-661`；框架 `graph.py:617-631` | `LoggingMiddleware`（工具异常兜底，`services/logging_middleware.py`）只加在主 agent 的 `middleware=`，子代理 middleware 栈由框架按 `TodoList/Filesystem/Summarization/PatchToolCalls/profile` 构建，**不含用户 middleware** | 子代理内 MCP `ToolException`（适配器 `tools.py:180-189` 在 `isError` 时抛）或工具异常会直接冒泡，导致 `task` 调用中断；主代理有兜底、子代理没有 → 行为不一致 | 把异常兜底写成 subagent `middleware` 传入（`DEFAULT_SUBAGENTS` 每项加 `middleware=[LoggingMiddleware(...)]`），或改 `handle_tool_errors` | S |
| T1-16 | **P3** | 依赖/一致性 | `pyproject.toml:34`；`config/mcp.json:7`；`checkpoints.py:131-135`；`services/mcp.py:266-277` | `mysql-mcp-server>=0.4.0` 在运行时依赖里，但仓库代码无 import，实际用 `uvx` 拉起（`config/mcp.json:7` 描述）；`PersistentMemorySaver` 上限 200 用 `pop(next(iter(...)))` FIFO 驱逐；MCP client 缓存泄漏（同 T1-11） | 以 root 安装无关 server 包到后端 venv（攻击面/冲突）；正在审批的会话可能被驱逐 → 恢复失败；内存缓慢增长 | 移除 `mysql-mcp-server` 依赖（由 uvx 隔离运行）；驱逐改为 LRU + 不驱逐有 pending_approval 的会话；client 缓存随失效清理 | S |
| T1-17 | **P3** | MCP server 端 | `mcp/jmfx_mcp_server.py:691-707`；`mcp-server/easy_mcp_server/app.py:56-90`；`mcp-server/easy_mcp_server/auth.py:37-79` | jmfx server 的 `_safe` 把异常包装成 **JSON 文本成功返回**（`{"shape":"text","title":"查询失败",...}`），MCP `isError` 仍为 false；客户端适配器仅在 `isError=true` 时抛 `ToolException`（`tools.py:180-189`）→ "失败"被模型当成功；`mcp-server/` 方向正确（FastMCP + `streamable_http_app` 挂载 + Bearer APIKey 中间件 + 传输安全白名单） | 错误静默（模型基于"查询失败"文本继续推理，本可重试）；MCP 规范建议执行错误用 `isError` 表达；缺少 structuredContent | `_safe` 改为 `raise ToolError(...)`（FastMCP 会输出 `isError=true`）；`mcp-server` 工具统一错误码 + `structuredContent`；客户端侧对"业务失败但 isError=false"保持兜底 | S |

> 严重度口径：P1 = 版本升级即断裂/跨用户数据边界/安全边界；P2 = 规范偏离 + 明确技术债/成本；P3 = 一致性、可维护性、文档与资源问题。性能/并发主线由 T2、安全主线由 T3 深挖，本表只保留与框架规范强相关的部分并标注"与 Tx 交叉"。

---

## 3. 详细分析

### T1-01 依赖无上界 + 绑定 0.6.8 内部实现（P1）

**证据 1：依赖声明**
```toml
# pyproject.toml:14-16
    "deepagents>=0.1.0",
    "langchain>=1.3.0",
    "langchain-anthropic>=0.3.0",
```

**证据 2：实际安装与最新版**
- 已安装：`deepagents 0.6.8 / langchain 1.3.4 / langgraph 1.2.4 / langchain-mcp-adapters 0.2.2`
- PyPI `deepagents` 最新 `0.7.28`，`requires_dist`：`langchain<2.0.0,>=1.4.6`、`langchain-core<2.0.0,>=1.6.11`、`langchain-anthropic<2.0.0,>=1.7.6`（即 0.7 与当前 langchain 1.3.4 **不兼容**）
- 官方文档明示 0.7 的破坏性变化：*"Starting in v0.7 task planning is opt-in only. In earlier versions, task planning middleware was included by default."*（见 §4 文档对照）；`delete` 工具 *"requires deepagents>=0.7"*

**证据 3：仓库对 0.6.8 内部细节的依赖点**
| 依赖点 | 位置 | 0.7 下的可能变化 |
|---|---|---|
| `FilesystemMiddleware.__init__` 全局包装 | `hitl.py:94-114` | 0.7 增加 `tools` allowlist 等参数，签名变化即 TypeError |
| `FilesystemMiddleware(custom_tool_descriptions=...)` | 同上（0.6.8 实测签名存在） | 未承诺长期保留 |
| `deepagents.graph.create_summarization_middleware` | `summarization.py:47,66` | 未导出私有名，可能搬移/删除 |
| `TodoListMiddleware` 默认注入 → `write_todos` | 框架 `graph.py:750`；仓库提示词/前端 todos 依赖 | 0.7 变 opt-in |
| `MemorySaver.storage/writes/blobs` | `checkpoints.py:46` | 私有属性无契约 |
| `InterruptOnConfig`/`permissions`/`skills`/`memory` 参数 | `agent.py:502-516` | 0.6.8 全部存在，0.7 语义有调整（skills sources 文档更新、forked subagent 等） |

**推断链**：`uv sync` 默认遵循锁文件（安全），但 `uv lock --upgrade` / 新增依赖 / 手动 `pip install -U deepagents` 都会拉到 0.7.28 → 因 `requires_dist` 冲突，uv 会同时升级 langchain/core/anthropic → 适配代码失效。**风险不是"当前坏了"，而是"锁定被破坏时不可控"**。

**建议**：
1. `pyproject.toml` 改 `"deepagents>=0.6.8,<0.7"`、`"langchain>=1.3.0,<1.4"`、`"langchain-mcp-adapters>=0.2.2,<0.3"`；
2. 把附录 B 的建图冒烟脚本变成 `tests/test_framework_contract.py`（无网络：用 `ChatOpenAI(api_key="sk-dummy", base_url=...)` 建图即可）；
3. 0.7 升级单独立项，先跑契约测试，再逐项替换 monkeypatch。

### T1-02 HITL resume 的 thread_id 未与 session 绑定（P1）

**代码**：
```python
# easy_agent/api/chat.py:672-707
session_id = request.session_id
thread_id = request.thread_id
...
session = get_owned_session(db, session_id, username)   # 只校验 session 归属
...
resume_stream_generator(..., session_id=session_id, thread_id=thread_id, ...)
```
```python
# easy_agent/services/streaming.py:1352
stream_config = {"configurable": {"thread_id": thread_id}}
```

**checkpointer 选择**：
```python
# easy_agent/checkpoints.py:127-131
def get_session_checkpointer(session_id: str, db=None):
    if _postgres_saver is not None:
        return _postgres_saver          # 全局共享 saver，thread 名空间完全由 config 决定
```
```python
# easy_agent/app.py:310-320（生产若配置了 URI 就启用官方 AsyncPostgresSaver）
checkpoint_uri = os.getenv("EASY_CHECKPOINT_POSTGRES_URI", "").strip()
if checkpoint_uri:
    await open_postgres_checkpointer(checkpoint_uri)
```

**问题**：正常流程里 thread_id 由服务端生成（`streaming.py:939-941`：`f"{session_id}-{message_id}"`），但恢复接口完全信任前端回传值。Postgres 模式下 saver 全局共享，`thread_id` 是唯一隔离维度 → 伪造 `thread_id` 即可命中他人 thread（checkpoint 内含完整消息历史与 HITL pending 操作）。MemorySaver 模式下 `get_session_checkpointer(session_id)` 按 session 取实例，"伪造"只会命中空状态（fail-safe），所以**风险等级取决于是否启用 Postgres**，但接口本身应无条件防御。

**建议**：resume 前 `if not thread_id.startswith(f"{session_id}-"): raise HTTPException(400)`；更佳：DB 里已持久化 `pending_approval.thread_id`（`db/database.py:1233` 附近），以它为准，前端只传 decisions。

### T1-03 execute 描述靠 monkeypatch（P1）

**代码**：
```python
# easy_agent/hitl.py:94-114
def install_execute_tool_description() -> bool:
    global _EXECUTE_DESC_INSTALLED
    if _EXECUTE_DESC_INSTALLED:
        return True
    try:
        from deepagents.middleware.filesystem import FilesystemMiddleware
        original_init = FilesystemMiddleware.__init__

        def wrapped_init(self, *args, custom_tool_descriptions=None, **kwargs):
            merged = dict(custom_tool_descriptions or {})
            merged.setdefault("execute", EXECUTE_HITL_TOOL_DESCRIPTION)
            original_init(self, *args, custom_tool_descriptions=merged, **kwargs)

        FilesystemMiddleware.__init__ = wrapped_init
        FilesystemMiddleware._easy_execute_description_installed = True
        _EXECUTE_DESC_INSTALLED = True
        return True
    except Exception:
        return False
```
```python
# easy_agent/agent.py:92-94 + 412-413：健康检查只反映 "patch 执行过"
def framework_patch_status() -> dict[str, bool]:
    return {"execute_tool": execute_description_installed()}
...
install_execute_tool_description()
log_framework_patch_status(self.sid)
```

**已安装 0.6.8 事实**：
- `FilesystemMiddleware.__init__` 确有 `custom_tool_descriptions: Mapping[str, str] | None = None`（实测签名，见附录 B 输出 `FSMW_CUSTOM_DESC_OK True`），因此当前可用；
- 但 `create_deep_agent` 自己构造 `FilesystemMiddleware(backend=..., custom_tool_descriptions=_profile.tool_description_overrides, _permissions=...)`（`graph.py:614-619, 698-703`）；
- `HarnessProfile.tool_description_overrides: Mapping[str, str]` 是公开（beta）字段（`profiles/harness/harness_profiles.py:279`），通过 `register_harness_profile(model_key, ...)` 生效 → **官方已有能力，无需替换类方法**。

**风险**：包装函数的 `try` 只覆盖"导入+赋值"，不覆盖"上游是否接受该 kwarg"。上游若改名/删除，`install` 仍返回 True，真正报错发生在 `create_deep_agent` 内部 → 全部会话创建失败，而日志显示"适配体检: execute_tool=生效"（假阳性）。

**建议**：改用 `register_harness_profile`（需为每个可切换模型注册，或按 provider 注册）；若短期保留 patch，则在 `install_execute_tool_description()` 里用 `inspect.signature(original_init)` 验证 `custom_tool_descriptions` 存在，否则显式 ERROR。

### T1-04 摘要阈值替换私有工厂（P1）

**代码**：
```python
# easy_agent/summarization.py:44-70
@contextmanager
def configured_summarization(config):
    import deepagents.graph as graph
    from deepagents.middleware.summarization import SummarizationMiddleware
    original = graph.create_summarization_middleware
    def factory(model, backend, **_kwargs):
        settings = summarization_settings(config, model)
        ...
        return SummarizationMiddleware(model, backend=backend, **settings)
    graph.create_summarization_middleware = factory
    try:
        yield
    finally:
        graph.create_summarization_middleware = original
```
```python
# easy_agent/agent.py:501-516
with configured_summarization(self.config):
    return create_deep_agent(... )
```

**已安装 0.6.8 事实**：
- `create_deep_agent` 内部 3 处调用模块级 `create_summarization_middleware(...)`（`graph.py:625,701,778`），因此当前替换有效（实测建图 `CREATE_OK`）；
- 官方工厂签名：`create_summarization_middleware(model, backend, *, summary_prompt=..., trim_tokens_to_summarize=None, token_counter=...)` → 仓库 factory 的 `**_kwargs` 会**丢弃**这些官方入参；
- `create_summarization_middleware` 不在 `deepagents.__init__.__all__`（导出清单只有 `create_deep_agent` 等），属私有实现细节。

**风险**：0.7 若把工厂搬进 `deepagents/middleware/summarization.py` 直接内部构造，则 `configured_summarization` 静默失效 → 回落到 0.85/0.10 阈值（与配置不一致，且上下文占用率前端显示会失真）；模块全局写操作在**并发建图**时还有竞态窗口（两个会话同时 create，A 的 finally 可能在 B 建图期间还原 factory）。

**建议**：①锁版本；②`graph` 无该属性时打出 ERROR 并在 `framework_patch_status` 反映；③长期给 deepagents 提 PR 暴露 `summarization_settings=` 参数；④若保留，改用进程级一次性安装（而非每次建图 patch/还原）以避免竞态，并加并发建图测试。

### T1-05 MCP 工具名不隔离（P1）

**代码**：
```python
# easy_agent/services/mcp.py:270-277
def _client_for(servers: dict[str, dict[str, Any]]) -> MultiServerMCPClient:
    key = json.dumps(servers, sort_keys=True, ensure_ascii=False, default=str)
    client = _mcp_clients.get(key)
    if client is None:
        client = MultiServerMCPClient(servers)     # 未开 tool_name_prefix
        _mcp_clients[key] = client
    return client
```
```python
# easy_agent/agent.py:487-493（HITL 以工具名为 key）
for extra in self.mcp_tools:
    name = getattr(extra, "name", "") or ""
    if looks_like_mutating_tool(name):
        interrupt_on_config.setdefault(name, InterruptOnConfig(...))
```

**已安装框架事实**：
- `MultiServerMCPClient.__init__(connections, *, callbacks=None, tool_interceptors=None, tool_name_prefix: bool = False)` → 默认**不加** server 前缀；
- `load_mcp_tools(..., tool_name_prefix=True)` 时才生成 `f"{server_name}_{tool.name}"`（`langchain_mcp_adapters/tools.py:423-424`）；
- LangChain `ToolNode`：`self._tools_by_name[tool_.name] = tool_`（`langgraph/prebuilt/tool_node.py:773-784`）→ **同名后者覆盖前者，无告警**。

**影响链**：MCP server（尤其是第三方/用户自建，`config/mcp.json` 允许自定义）若定义 `execute`/`read_file`/`write_file`/`edit_file`/`task`/`write_todos`，就会与 `FilesystemMiddleware`/`SubAgentMiddleware`/`TodoListMiddleware` 注入的内建工具重名；模型调用该名字时实际执行哪一个由中间件 tool 收集顺序决定（用户 tools 与 middleware tools 合并顺序），两种结果都危险：MCP 顶掉沙箱 `execute`（绕过 bwrap/Landlock），或内建顶掉 MCP（用户以为在用连接器）。HITL 按名字匹配（`HumanInTheLoopMiddleware.after_model` 用 `interrupt_on[tool_call["name"]]`）同样无法区分。

**建议**：`MultiServerMCPClient(servers, tool_name_prefix=True)`；加载后做内建名/跨 server 冲突检测并拒绝；HITL 正则匹配改用完整工具名列表（当前 `looks_like_mutating_tool` 对 `create_*` 之类仍会误伤，可保留但日志标注明细）。

### T1-06 自研 PersistentMemorySaver（P2）

```python
# easy_agent/checkpoints.py:35-54, 63-73
def _hydrate(self):
    blob = self._db.load_agent_checkpoint(self._session_id)
    ...
    for attr in ("storage", "writes", "blobs"):
        value = payload.get(attr)
        if value is not None and hasattr(self, attr):
            ...
def _flush(self):
    payload = {}
    for attr in ("storage", "writes", "blobs"):
        if hasattr(self, attr):
            payload[attr] = self._plain_mapping(getattr(self, attr))
    self._db.save_agent_checkpoint(self._session_id, pickle.dumps(payload, protocol=5))
```

**已安装 langgraph 1.2.4 事实**：`langgraph/checkpoint/memory/__init__.py:93-95` 确实定义了 `self.storage / self.writes / self.blobs`，所以现在能工作；但这三个名字不是公开 API（类公开方法是 `get_tuple/list/put/put_writes/delete_thread/get_next_version`）。

**后果**：①升级 langgraph 若改内部容器 → `hasattr` 守卫让 hydrate **静默跳过**（不抛错），表现为"重启后 HITL 审批恢复失效/会话状态丢失"；②每个 step 的 `aput/put` 都全量 pickle（含所有历史 thread 的 checkpoint）→ DB 写放大 + `pickle.dumps` 阻塞 async 事件循环（`aput` 直接调用 `_flush`，非 `to_thread`）；③`pickle` 反序列化 DB blob 是反序列化不可信数据面（DB 被写即 RCE，虽然 DB 本身可信，但属纵深防御问题）。

**建议**：默认/生产一律官方 saver（`AsyncPostgresSaver` 已接入，缺 URI 时用 `langgraph-checkpoint-sqlite`），删掉自研 saver；如必须保留，改为 `put_writes` 后异步节流落盘 + 版本断言。

### T1-07 max_steps 语义（P2）

```python
# easy_agent/agent.py:662-674
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
run_limit = max(1, int(self.max_steps or 50))
middleware.extend([
    ModelCallLimitMiddleware(run_limit=run_limit),
    ToolCallLimitMiddleware(run_limit=run_limit * 4),
])
```
```python
# 框架 deepagents/graph.py:851-857（.with_config）
).with_config({
    "recursion_limit": 9_999,
    "metadata": {...},
})
```
已安装签名：`ModelCallLimitMiddleware(*, thread_limit=None, run_limit=None, exit_behavior: Literal['end','error'] = 'end')`、`ToolCallLimitMiddleware(..., exit_behavior: ExitBehavior = 'continue')`（实测 `inspect.signature`）。

**问题**：①`max_steps` 并不是 LangGraph 的 `recursion_limit`，两者语义不同（前者模型调用数，后者图步数）；②默认 `exit_behavior='end'` 到限即结束本轮，前端无"达到步数上限"提示，用户体验/排障差；③**子代理不受限**：`graph.py:617-631` 子代理 middleware 栈 = `TodoList + Filesystem + Summarization + PatchToolCalls + profile`，不含用户 middleware → 子代理在 9999 recursion_limit 内可无限模型调用；`task` 工具因此成为预算逃逸口。

**建议**：`stream_config["recursion_limit"] = max(2*max_steps, 10)`（覆盖子代理）；`ModelCallLimitMiddleware(..., exit_behavior="error")` 并在 stream 层捕获转为明确 SSE `error` 事件；如需硬预算，给每个 subagent 的 `middleware` 也加 limit。

### T1-08 公共技能未走官方 sources（P2）

```python
# easy_agent/agent.py:895-906
def _resolve_skills_paths(self) -> list[str]:
    if not self.user_skills_dir.exists():
        return []
    if not self._discover_user_skill_names():
        return []
    return ["/user-skills/"]
```
```python
# easy_agent/agent.py:502-508
return create_deep_agent(
    ...
    skills=skills_paths or None,
```
```python
# easy_agent/api/skill_center.py:496-517（"启用"=复制公共技能目录到用户目录）
source_dir = Path(skills_root) / request.dir_name
...
user_skills_dir = _get_user_skills_dir(username)
target_dir = user_skills_dir / request.dir_name
```

**官方对照**（skills.md）：
- `skills: list[str]` 为"skill source paths"，**later sources override earlier ones (last wins)**，可分层 user/project；
- 路径必须相对 backend root、正斜杠；
- 每个 source 是"包含技能目录的目录"，直接指到技能目录不会加载；
- `SKILL.md` frontmatter 必须符合 Agent Skills 规范（`name` 与目录名一致、`description` ≤1024），超 10MB 跳过。

**仓库现状**：公共 `skills/`（7 个）仅作为"技能中心"的商品目录，必须逐个复制进 `workspace/{user}/skills/` 才对 Agent 可见；`easy_agent/skills.py` 的 `find_skills_root/discover_skills` 是 UI 侧发现逻辑，与深代理 `SkillsMiddleware` 无直接关系（`skills_root` 参数 docstring 自述"当前版本仅用于参考，不自动加载"，`agent.py:166`）。

**风险**：公共技能升级（例如 mcp-builder 文档更新）不会同步到已"启用"的用户副本；用户目录与公共目录双份维护；无法用 last-wins 做"项目级覆盖"；`SKILL.md` 不合格时框架静默跳过（skills.py 不校验）。

**建议**：`skills=["/skills/", "/user-skills/"]`，为 `/skills/` 增加只读 backend 路由（可用 `LocalShellBackend(root_dir=公共 skills, virtual_mode=True)`，并在 permissions 里 deny write）；启用/停用改为"过滤 sources 或过滤发现的技能名"（可在自定义 SkillsMiddleware 或 subagent 层面做）。

### T1-09 自定义 subagent 与技能/工具的关系（P2）

```python
# easy_agent/subagents.py:3-28
DEFAULT_SUBAGENTS = [
    {
        "name": "office-writer",
        "description": (
            "Generate or edit Word / Excel / PowerPoint files. "
            "Read /user-skills/docx/SKILL.md, /user-skills/pptx/SKILL.md, "
            "or /user-skills/xlsx/SKILL.md before writing."
        ),
        "system_prompt": ("You produce office documents in the session workspace. ..."),
    },
    {... "name": "knowledge-researcher" ...},
]
```

**官方对照**（subagents.md / 0.6.8 `SubAgent` TypedDict）：
- 必填 `name/description/system_prompt`（当前 dict 合法，已实测建图成功）；
- `skills` 字段："Does not inherit from main agent. Only the general-purpose subagent inherits the main agent's skills."；
- `tools` 不填则"inherits the main agent via default_tools"（`graph.py:665-675`）；
- `permissions` 不填则继承父级；`interrupt_on` 不填则继承父级（`graph.py:698-712`）。

**问题**：①自定义 subagent 没有 SkillsMiddleware，`office-writer` 的 SKILL.md 指引只是"读文件"提示，无技能元数据；若用户未启用 docx/pptx/xlsx，路径不存在，子代理会反复尝试；②子代理继承全部父 tools（MCP 写工具 + knowledge + save_expert 等），与其 system_prompt 里"不要删目录"等约束不匹配，属提示词与工具面不一致。

**建议**：给每个 subagent 显式 `"skills": ["/user-skills/"]`（或未来 `/skills/`），并显式收窄 `"tools": [...]`；把 `/user-skills/...` 路径改为"由技能列表决定"的提示（避免硬编码）。

### T1-10 MCP 传输/认证/超时（P2）

```python
# easy_agent/services/mcp.py:248-254
elif "transport" not in normalized:
    if "command" in normalized:
        normalized["transport"] = "stdio"
    elif "url" in normalized:
        normalized["transport"] = "sse"      # ← 旧传输
```
```python
# easy_agent/api/connectors.py:354-363（可编辑字段白名单）
if entry ... : return [ ...entry fields... ]
return ["url", "headers.*", "env.*"]
```

**适配器 0.2.2 事实**（`langchain_mcp_adapters/sessions.py`）：
- 支持 `StdioConnection/SSEConnection/StreamableHttpConnection/WebsocketConnection`（`sessions.py:83-210`）；
- `SSEConnection.timeout` 默认 5s、`sse_read_timeout` 默认 300s（`:141-149`）；`StreamableHttpConnection.timeout` 是 `timedelta`；
- 通过 `auth` 支持 OAuth（`sse_client(..., auth=auth)` `:306`），而仓库未透传。
**官方文档**（tools.md）："For detailed configuration options—including stdio servers, **OAuth authentication**, tool filtering, and **stateful sessions**—see the full MCP guide"。

**建议**：默认 `streamable_http` 并兼容 `http`/`streamable-http` 别名；`normalize_servers_mapping` 透传 `timeout`（并对 streamable_http 的 timedelta 做转换）；`update_connector_config` 白名单增加 `auth`（后续）与超时字段。

### T1-11 MCP client 生命周期（P2）

```python
# easy_agent/services/mcp.py:263-277
_mcp_tools_cache: dict[str, tuple[float, dict[str, list]]] = {}
_mcp_clients: dict[str, MultiServerMCPClient] = {}
...
_mcp_clients[key] = client
```
```python
# easy_agent/services/mcp.py:394-418（invalidate 只清 tools，不清 clients）
def invalidate_mcp_cache(username=None):
    if username is None:
        _mcp_tools_cache.clear()
        _mcp_clients.clear()
        ...
```
适配器文档/源码：`MultiServerMCPClient` 默认 stateless，"A new session will be created for each tool call"（`client.py:161`），只有显式 `async with client.session(name)` 才复用长连接。

**影响**：stdio MCP 每次工具调用都重新拉起进程（冷启动延迟、可能每次重新鉴权/建连），大模型多步任务会放大；仓库在 `mcp.py:359-370` 已对生产 stdio 告警，但未提供替代路径。虽然属性能问题（T2 主线），但**框架使用方式**（stateless 客户端 + 无 session 复用）是根因，故 T1 单列。

**建议**：对 stdio/streamable_http 提供长连接模式（缓存 `client.session()` 的会话上下文或改用 `MCPAdapter` 的 stateful 能力）；至少把 `_mcp_clients` 纳入失效清理并按 LRU 上限。

### T1-12 自研 SqlStore 与官方 store 能力（P2）

```python
# easy_agent/stores.py:40-52
class SqlStore(BaseStore):
    def batch(self, ops): return [self._run_op(op) for op in ops]
    async def abatch(self, ops):
        return await asyncio.to_thread(self.batch, list(ops))
```
```python
# easy_agent/agent.py:495-499, 514
store = get_agent_store()
...
store=store,
```
框架侧：`MemoryMiddleware` 通过 `backend.download_files(list(self.sources))` 读 memory（`deepagents/middleware/memory.py:320-328`），不读 store；`store` 仅传给 `create_agent(store=...)`，供工具/`runtime.store` 使用。当前 backend 全是 `LocalShellBackend`（无 `StoreBackend`），所以 store 无内建消费者。

**风险**：①误导（看起来"有跨线程 store"，实际无内建消费方）；②`SqlStore` 语义不完整，将来切 `StoreBackend`（官方推荐跨线程记忆方案）会踩坑；③每次 `batch` 无事务/无批优化，逐个 op 查 DB。

**建议**：删除或明确标注为"仅工具侧 runtime.store"；若要跨线程记忆，用官方 `StoreBackend` + `InMemoryStore`/Postgres store。

### T1-13 文档漂移（P2，与 T4 交叉）

| 文档声明 | 实际代码 | 证据 |
|---|---|---|
| `CompositeBackend` 映射 `/skills/` 到只读技能文件 | **没有** `/skills/` 路由；只有 `/user-skills/`、`/memories/`、`/workspace/`、外部目录 | `agent.py:568-637` 全部 routes；`grep -rn '"/skills/"' easy_agent/*.py` 无结果 |
| 17 个技能 | `skills/` 与 `easy_agent/skills/` 各 7 个（docx/expert-creator/mcp-builder/pdf/pptx/skill-creator/xlsx） | `ls -d skills/*/` |
| `deepseek` provider = `langchain-deepseek` | 未安装该包；`model.py` 只有 openai/anthropic 协议 | `model.py:1-9,229-239`；`importlib.metadata` 查询 `langchain-deepseek MISSING` |
| `discover_skills` 认 `SKILL.md` 或 `README.md` | `skills.py:80-89` 只认 `SKILL.md` | `skills.py:86-89` |
| 上下文压缩通过 `SummarizationMiddleware` 配置 | 实际是私有工厂 monkeypatch（见 T1-04） | `summarization.py:44-70` |

### T1-14 ~ T1-17（P3）

- **T1-14**：`interrupt_on` 中 `save_expert/create_scheduled_task/access_knowledge_original` 是否注册取决于运行路径（`agent_manager.py` 按条件注入），HITL 对未注册名静默不生效；`execute` 目录删除走 `_PathTranslatingShell.execute` 直接拒绝（`backends/shell.py:87-94`）而不是 interrupt → 用户无法"批准"目录删除（设计如此，但需在文档中声明策略层 vs HITL 层）。
- **T1-15**：`LoggingMiddleware` 只在主代理 `middleware=`（`agent.py:654-661`）；子代理工具异常无兜底（框架 `graph.py:617-631` 不含用户 middleware）。MCP 适配器在 `isError=true` 时抛 `ToolException`（`langchain_mcp_adapters/tools.py:180-189`）→ 子代理内可能中断 `task`。
- **T1-16**：`mysql-mcp-server` 依赖（`pyproject.toml:34`）代码未用，实际 `uvx` 拉起（`config/mcp.json:7`）；`_CHECKPOINTER_MAX=200` 的 `pop(next(iter(...)))` 是 FIFO，可能驱逐"正在等待审批"的会话（`checkpoints.py:131-135`）。
- **T1-17**：`mcp/jmfx_mcp_server.py:691-707` 的 `_safe` 把异常包装成 JSON 文本成功返回（isError=false），客户端仅在 isError 时抛错 → 失败被当成功；`mcp-server/` 架构（FastMCP + streamable_http_app + Bearer APIKey + transport security）方向正确，建议补 `isError`/`structuredContent`。

---

## 4. 与官方规范/需求的对照（T1 必填）

### 4.1 官方文档与结论清单

| # | 文档 URL | 关键结论（用于对照） |
|---|---|---|
| D1 | https://docs.langchain.com/oss/python/deepagents/overview | Deep Agents = agent harness；内建工具表（0.7 含 `delete`）；"Starting in v0.7 task planning is opt-in only"；`interrupt_on` 是官方 HITL 入口；MCP 通过 `tools=` 传入；skills 是 context management 的第一层（progressive disclosure） |
| D2 | https://docs.langchain.com/oss/python/deepagents/skills | `skills: list[str]` 是 source 目录列表，**last wins**；路径相对 backend root、正斜杠；每 skill 一个 `SKILL.md` + Agent Skills 规范 frontmatter（`name` 必须=目录名，`description`≤1024）；`SkillsMiddleware` 负责 level1/2，`read_file` 读全文；custom subagent **不继承**主 agent skills（只有 general-purpose 继承） |
| D3 | https://docs.langchain.com/oss/python/deepagents/subagents | `SubAgent` dict 字段表（name/description/system_prompt 必填；tools/model/middleware/interrupt_on/skills/permissions 可选）；`tools` 默认继承主代理；`skills` 不继承；`permissions`/`interrupt_on` 默认继承；CompiledSubAgent 用于复杂编排；0.7.13+ 有 fork 模式 |
| D4 | https://docs.langchain.com/oss/python/deepagents/tools | 自定义工具直接 `tools=`；MCP 完全支持（`langchain.mcp.MCPAdapter` 或适配器）；stdio/OAuth/tool filtering/stateful sessions 见 MCP guide；内建工具清单 |
| D5 | https://docs.langchain.com/oss/python/langchain/mcp | LangChain MCP 集成（OAuth、stateful session、tool filtering） |
| D6 | https://docs.langchain.com/oss/python/deepagents/backends | Backend 可插拔（State/Filesystem/Store/Composite/LangSmith sandbox）；自定义 backend 需实现 `BackendProtocol`/`SandboxBackendProtocol` |
| D7 | https://docs.langchain.com/oss/python/deepagents/human-in-the-loop | `interrupt_on` + checkpointer；decision 语义（approve/edit/reject）；需 `thread_id` 恢复 |
| D8 | https://docs.langchain.com/oss/python/deepagents/memory | `memory=[AGENTS.md paths]`，MemoryMiddleware 从 backend 加载，始终注入 |
| D9 | https://docs.langchain.com/oss/python/deepagents/customization | middleware 顺序与 Deep Agents stack；prompt assembly（caller prompt 在 BASE_AGENT_PROMPT 之前） |
| D10 | https://pypi.org/pypi/deepagents/json | 最新 0.7.28；`requires_dist`：`langchain>=1.4.6`、`langchain-core>=1.6.11`、`langchain-anthropic>=1.7.6`（证明升级即连带升级 langchain） |
| D11 | 已安装源码 `.venv/.../deepagents/graph.py`（0.6.8，权威） | `create_deep_agent` 真实签名与 middleware 组装顺序；`.with_config({"recursion_limit": 9_999})`；`HumanInTheLoopMiddleware` 最后 append；`_apply_tool_description_overrides`；`HarnessProfile` 接入点 |
| D12 | 已安装源码 `.venv/.../langchain_mcp_adapters/{client,tools,sessions}.py`（0.2.2，权威） | `tool_name_prefix` 默认 False；`get_tools(*, server_name=)`；stateless session；`isError`→`ToolException`；连接 schema（含 `auth`/`timeout`） |

### 4.2 仓库装配 vs 官方推荐（逐项判定）

| 能力 | 官方推荐 | 仓库做法 | 判定 |
|---|---|---|---|
| 建图入口 | `create_deep_agent(model, tools, system_prompt, backend, middleware, subagents, skills, memory, permissions, interrupt_on, checkpointer, store)` | 全部使用，参数正确（实测建图成功） | ✅ 合规 |
| 内建工具 | 由框架注入，勿重复定义 | 未重复定义；`execute`/`task`/`write_todos` 均来自框架 | ✅ 合规 |
| system prompt | caller prompt 在 BASE_AGENT_PROMPT 之前 | 交给 `system_prompt=`，正确叠加框架 BASE prompt | ✅ 合规 |
| Backend | `CompositeBackend(default=SandboxBackend, routes={...})`；自定义需求继承 `LocalShellBackend`/实现协议 | `CompositeBackend` + `LocalShellBackend` + 自定义 `PathTranslatingShell(LocalShellBackend)` 重写 `execute`；`aexecute` 由协议 `asyncio.to_thread` 提供 | ✅ 合规（自定义实现基于公开基类/协议） |
| Permissions | `permissions=[FilesystemPermission(...)]`；sandbox backend + 未覆盖路径会 NotImplementedError | 用官方 `FilesystemPermission`，且限定在已挂载 route 前缀内（实测 `_all_paths_scoped_to_routes=True`） | ✅ 合规（规避了已知限制） |
| Memory | `memory=[paths]`，由 MemoryMiddleware 从 backend 读 | 传 `/workspace/memory.md`、`/memories/AGENTS.md`、上传清单虚拟路径 | ✅ 合规 |
| Skills | `skills=[sources]`，last wins | 仅 `/user-skills/`；公共技能靠复制 | ⚠️ 部分合规（T1-08） |
| Subagents | dict/CompiledSubAgent；自定义 subagent 不继承 skills；tools 默认继承 | dict 字段合法，但未设 skills、未收窄 tools | ⚠️ 部分合规（T1-09） |
| HITL | `interrupt_on` + checkpointer + thread_id | 使用官方 `InterruptOnConfig`；但恢复接口信任前端 thread_id | ⚠️ 用法合规、隔离不合规（T1-02） |
| execute 描述定制 | `FilesystemMiddleware(custom_tool_descriptions=...)` 公开参数；beta `HarnessProfile.tool_description_overrides` | 进程级 patch `FilesystemMiddleware.__init__` | ❌ 偏离（T1-03） |
| 摘要阈值 | 0.6.8 无公开参数（官方工厂私有） | 替换私有工厂 | ⚠️ 无官方替代，但需锁版本+护栏（T1-04） |
| Checkpointer | 官方 `AsyncPostgresSaver`/`SqliteSaver` + thread 命名空间 | Postgres 路径用官方；否则自研 pickle MemorySaver | ❌ 偏离（T1-06） |
| Store | 官方 `BaseStore` 实现（InMemory/Postgres）或不用 | 自研 `SqlStore`，当前无内建消费者 | ❌ 过度实现（T1-12） |
| MCP client | `MultiServerMCPClient`；建议 `tool_name_prefix` 防冲突；长连接/超时/OAuth 按需 | stateless client，无前缀，url→sse 默认，无 OAuth/超时透传 | ⚠️ 可用但不稳健（T1-05/10/11） |
| 递归/预算 | LangGraph `recursion_limit` + middleware 预算 | `max_steps` → call-limit middleware（默认静默结束），子代理不受控 | ⚠️ 语义偏离（T1-07） |
| 版本 | 官方语义化版本；API beta 有变更 | 无上界 + 依赖私有 API | ❌ 高风险（T1-01） |

### 4.3 关键"官方已有能力 vs 重复造轮子"清单

1. **execute 工具描述**：官方 `custom_tool_descriptions`（公开参数）+ `HarnessProfile.tool_description_overrides`（beta 公开字段，0.6.8 已导出 `HarnessProfile`）。仓库应弃用 monkeypatch。
2. **持久化 checkpointer**：官方 `AsyncPostgresSaver`（已接一半）与 `langgraph-checkpoint-sqlite`；不必自研 MemorySaver 序列化。
3. **跨线程存储**：官方 `StoreBackend` + `BaseStore` 实现；不必自研 `SqlStore`（除非明确需要业务库同库存储，也应补全语义）。
4. **技能多源分层**：官方 `skills=[builtin, user]` last-wins；不必"复制到用户目录"模拟启用。
5. **MCP 命名前缀/冲突避免**：官方适配器 `tool_name_prefix=True`；不必自建命名空间。
6. **MCP 会话/Tool 错误语义**：适配器 `isError → ToolException`；服务端应用 `isError=true` 而非 JSON 文本。

---

## 5. 不确定项 / 需 Lead 复核

| 编号 | 不确定点 | 目前证据 | 需谁确认 |
|---|---|---|---|
| U1 | **T1-02 是否已实际可利用**取决于是否设置 `EASY_CHECKPOINT_POSTGRES_URI`（生产/测试环境变量），以及 T3 的认证强度。若线上未启用 Postgres，风险降为 P2（MemorySaver 按 session 取实例，伪造 thread 命中空状态） | `app.py:310-320` 仅当 env 存在才启用；`checkpoints.py:129-131` 全局 saver 分支 | 需部署配置（Lead/T3）；建议按"接口应防御"仍修 |
| U2 | `InterruptOnConfig.when` 在 `"batch"` 模式下构造的 `ToolCallRequest` 的 `tool_call["args"]` 是否总是 dict/存在 `command`（仓库 `_is_destructive_command` 用 `.get("command","")`，对非 execute 工具自身已用 `when` 限定 execute，OK） | `langchain/agents/middleware/human_in_the_loop.py:354-386`；`langchain/agents/middleware/types.py` | 静态推断已足够；无需动作 |
| U3 | deepagents 0.7 对 `FilesystemMiddleware(custom_tool_descriptions=)` 是否保留、`HarnessProfile` 是否仍 beta 兼容，需在升级 spike 时实测 | 未安装 0.7；仅有 docs/PyPI 元数据 | Lead 决定是否立项升级 spike |
| U4 | `create_deep_agent` 同名 MCP 工具与内建工具的最终覆盖次序（LangChain `create_agent` 合并 user tools 与 middleware tools 的顺序）未逐行验证到最终 list 拼接点；但 `ToolNode.tools_by_name` last-wins 已确证，冲突本身即问题 | `langgraph/prebuilt/tool_node.py:773-784`；`langchain/agents/factory.py` 未定位到确切拼接行 | 无需精确定序：建议直接避免重名 |
| U5 | `mcp-server/`（FastMCP）与主项目 `mcp` SDK 版本兼容性：`mcp-server/pyproject.toml` 注 `mcp>=1.27,<2`，主项目 `uv.lock` 的 mcp 版本未本次核对 | `mcp-server/pyproject.toml:8-11` | 交 T5 运行时验证 |
| U6 | jmfx `_safe` 返回 JSON 文本是否"有意设计（模型可读失败原因）"：仓库注释称便于模型理解，但与 MCP `isError` 规范有偏差 | `mcp/jmfx_mcp_server.py:691-707` | 产品/维护者确认语义 |

---

## 附录 A：验证脚本与实测输出

所有脚本位于 `_analysis/`（临时文件，不属业务代码）：

- `_analysis/t1_probe.sh` / `t1_probe2.sh` / … / `t1_probe13.sh`：框架源码与服务端代码探针（版本、签名、middleware 顺序、权限、MCP、checkpointer、thread_id 等）
- `_analysis/t1_verify_create.py`：**用仓库同参实际建图**的契约验证

关键实测输出（0.6.8）：

```
deepagents 0.6.8 / langchain 1.3.4 / langgraph 1.2.4 / langchain-mcp-adapters 0.2.2
create_deep_agent signature:
(model, tools, *, system_prompt, middleware, subagents, skills, memory, permissions,
 backend, interrupt_on, response_format, state_schema, context_schema, checkpointer,
 store, debug, name, cache)

CREATE_OK CompiledStateGraph                     # CompositeBackend + skills + memory + permissions
                                                 # + interrupt_on + subagents + summarization patch 全部通过
FSMW_CUSTOM_DESC_OK True                         # FilesystemMiddleware 接受 custom_tool_descriptions
HITL_PATCH True ⚠️⚠️⚠️ 人工审批工具 ...            # hitl monkeypatch 生效
SCOPED_TO_ROUTES True                            # permissions 全部限定在已挂载 route 前缀（无 NotImplementedError）
SKILLS_MW_OK SkillsMiddleware
```

> 该实测同时证明：**当前 0.6.8 下装配是自洽的**（不存在"现在就崩"的装配错误）；问题集中在版本漂移、私有 API 依赖、MCP 命名/传输、HITL 隔离。

## 附录 B：建议的框架契约测试（伪代码）

```python
# tests/test_framework_contract.py（无网络）
def test_create_deep_agent_accepts_repo_params(tmp_path):
    """仓库装配参数面 + 私有适配点仍在时通过；升级 deepagents 先跑此测试。"""
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend, LocalShellBackend
    ...
    assert hasattr(importlib.import_module("deepagents.graph"), "create_summarization_middleware")
    assert "custom_tool_descriptions" in inspect.signature(FilesystemMiddleware.__init__).parameters
    agent = create_deep_agent(model=fake_model, backend=composite, skills=["/user-skills/"],
                              permissions=[...], interrupt_on={...}, subagents=[...],
                              memory=None, checkpointer=None)
    assert agent is not None
```

---

## 附录 C：审计边界与交叉

- 本报告只覆盖**框架规范一致性**；性能/并发见 T2（本报告 T1-06/07/11 与其交叉，已标注）；安全/多用户隔离见 T3（T1-02/05 与其交叉）；架构/巨型文件/文档漂移整体见 T4（T1-13 与其交叉）；运行验证见 T5。
- 未验证项：0.7.x 实际 API（未安装，不联网安装）、生产配置（`EASY_CHECKPOINT_POSTGRES_URI`、`AGENT_ENV`）、真实 MCP server 端到端联调（只做了源码级/静态级判定）。
