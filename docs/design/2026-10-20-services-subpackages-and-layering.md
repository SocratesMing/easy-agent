# services 拆领域子包与分层规则

- 日期：2026-10-20
- 状态：已评审（2026-10-20，§13 的 6 个问题均按推荐答案定案）；2026-10-21 完成 §7 中 `services.streaming` 的瘦身并删除 `services/streaming.py` 垫片，其余尚未实施
- 更新（2026-10-29）：§9 违规修复清单除 #7（按 §13 留在白名单）外已全部按表中改法实施；`agent_manager` ↔ `tool_scope` ↔ `expert_writer` 的环也已拆掉（`resolve_tool_scope` 由调用方传入开关，`save_expert` 直接持有会话暂存表）。§6.1 方向门禁已落地：`scripts/quality/import_layers.json` 只登记 #7 一条，CI 跑 `--check-scc --check-layers`。§6.3 动态导入已落地：字面量的 `import_module("easy_agent...")` 算成普通的边，不是字面量的记告警（当前 0 条）。为此去掉了 `services/scheduler.py` 未注册时回退用的 `importlib.import_module("easy_agent.services.agent_manager")`，它会让 `agent_manager`、`scheduler`、`tools.scheduled_task` 成环；现在 agent 生命周期没注册就直接报错。2026-10-30：§7 瘦身完成，根包只留 `__version__`，`services`、`utils` 的 `__init__.py` 只留说明，调用方改为直接导入子模块；`import easy_agent.bootstrap.env` 现在只加载 62 个模块。§6.2 包入口门禁已落地（`init_allow` 14 条，CI 加 `--check-inits`），§6.4 的两条导入隔离用例已加上。§8 子包划分仍未实施
- 范围：`easy_agent/services/` 的子包划分与迁移；根包、`services`、`services.streaming`、`utils` 四个 `__init__.py` 的瘦身；import 图门禁增加分层方向检查。前端与 `knowledges/` 的内部结构不在范围内
- 相关：`docs/design/2026-10-18-architecture-improvement-plan.md`（阶段 0 的 SCC 门禁、阶段 C 的分层收口与 streaming 拆分）；审计路线图 D 项「`services/` 拆领域子包、清理兼容垫片」（`docs/audit/2026-10-19-deepagents-best-practices-audit.md` §7）
- 数据：2026-10-20，HEAD `72014e0`；工作区里其他会话未提交的改动不计

## 1. 结论先行

- **先加门禁，再动代码。** `scripts/quality/import_graph.py` 增加三项检查：分层方向、包 `__init__` 不得导入、动态导入识别。当前的 9 处方向违规冻结进白名单，只减不增。
- **先给四个 `__init__.py` 瘦身。** 根包、`services`、`services.streaming`、`utils` 都在包入口一次性导入整片代码：导入 30 行的 `services/health.py` 要 2.6～3.6 秒、加载 4,820 个模块；连导入读环境变量的 `bootstrap/env.py` 都会先把 agent 核心和 deepagents 拉起来（2.2 秒、4,516 个模块）。瘦身只需改写各包入口的少量导入语句（§7）。
- **再修 9 处下层依赖上层。** 每处都是挪一个常量、一个纯函数或一个模块（§9）。
- **最后把 `services/` 根下的 33 个业务模块收进 7 个领域子包**（agents / streaming / sessions / mcp / memory / experts / skills），根下只留 4 个横切小模块。一个子包一个提交、纯移动；有活跃分支在改的子包（mcp、streaming）等分支合并后再动。
- 全部预计 5 个工作日左右。

## 2. 现状

### 2.1 `services/` 平铺

40 个 `.py` 文件、15,311 行：根下 35 个（33 个业务模块，加上 `__init__.py` 和一个兼容垫片），`streaming/` 子包 5 个。按领域看是这样混在一起的：

| 领域 | 模块（行数） |
| --- | --- |
| Agent 生命周期 | `agent_manager` 694、`prompt_loader` 229 |
| 流式 | `stream_processor` 682、`stream_messages` 246、`stream_events` 294、`stream_hub` 170、`sse` 179、`sse_emitter` 127、`hitl_state` 252、`run_registry` 224，以及 `streaming/`（orchestrator 927、projector 512、persistence 316、side_effects 294） |
| 会话 | `session_runtime` 142、`session_thread` 278、`session_uploads` 732、`upload_storage` 511 |
| MCP | `mcp` 518、`mcp_sessions` 882、`mcp_policy` 202、`mcp_evidence` 472、`connectors` 303 |
| 记忆 | `memory_entries` 542、`memory_locks` 245、`memory_manager` 582、`memory_store` 578 |
| 专家 | `expert_service` 798、`expert_guardrail` 85、`expert_relevance` 448、`public_expert_service` 437 |
| 技能 | `skill_library` 489、`skill_categories` 471 |
| 横切 | `scheduler` 529、`health` 30、`metrics` 567、`document_convert` 102 |

### 2.2 包入口一次性导入整片代码

| 包 | `__init__.py` 导入了什么 | 经包入口取符号的地方 | 影响 |
| --- | --- | --- | --- |
| `easy_agent`（根） | `EasyAgent`、`Config`、`create_model`、`discover_skills` | 1 处（`tests/integration/test_basic.py`） | 导入任何 `easy_agent.*` 都先执行它：`import easy_agent.bootstrap.env` 要 2.2 秒、加载 46 个项目模块、共 4,516 个模块，agent 核心已在其中 |
| `easy_agent.services` | `agent_manager`、`connectors`、`streaming`、`stream_events`、`scheduler` 的 29 个名字 | 14 条导入，分布在 12 个文件（`app.py`、9 个 api 模块、`knowledges/personnel/api.py`、`tests/integration/test_basic.py`） | 导入 `services` 下任何模块都会加载 `agent_manager` 和 deepagents：`import easy_agent.services.health` 要 2.6～3.6 秒、加载 96 个项目模块、共 4,820 个模块 |
| `easy_agent.services.streaming` | 62 个导入名，外加用 PEP 562 `__getattr__` 惰性转出的 24 个名字 | 14 条导入，分布在 13 个文件（3 处在 api / services，其余是测试） | C2 拆分时为避免新增 SCC 而设的兼容门面；SCC 早已为 0，这层间接已不再需要 |
| `easy_agent.utils` | `auth`、`file_parser`、`session`（依赖 db）、`session_logger`、`task_logger` | 5 条导入，5 个文件（全在 api） | 导入任何 `utils` 子模块都会连带加载数据库层和文件解析器 |

### 2.3 依赖方向：9 处下层依赖上层

按 §5 的分层模型统计，函数内导入也计入：

| # | 依赖 | 导入的东西 |
| --- | --- | --- |
| 1 | `agent/core.py:831` → `services/mcp.py` | 私有函数 `_parse_mcp_content` |
| 2 | `agent/core.py:67` → `tools/web_search.py` | 常量 `WEB_SEARCH_SYSTEM_PROMPT` |
| 3 | `agent/workspace.py:107` → `services/session_uploads.py` | 常量 `MANIFEST_FILENAME`、`UPLOADS_DIR_NAME` |
| 4 | `backends/shell.py:13` → `agent/hitl.py` | 私有函数 `_is_directory_deletion` |
| 5 | `db/repositories/chat_repository.py:83` → `services/session_runtime.py` | `renew_stream` |
| 6 | `db/repositories/expert_repository.py:136` → `services/session_runtime.py` | `renew_stream` |
| 7 | `knowledges/personnel/api.py:16` → `middleware` | 鉴权依赖；知识库是自带路由的纵向切片 |
| 8 | `utils/distributed_lock.py:38` → `db` | 数据库分布式锁 |
| 9 | `utils/session.py:21` → `db` | 会话归属校验 |

此外，包之间还存在双向依赖：agent↔services、db↔services、tools↔services、utils↔db、utils↔bootstrap。函数内部的内部导入共 120 处。

### 2.4 门禁看不到的依赖

- **父包 `__init__` 的隐式执行。** import 图只记录「A 导入 B」，不记录「导入 B 之前要先执行 B 所在各级包的 `__init__`」。DA-13 S2 就碰到过这种运行期循环：agent 层导入 `services` 下的一个小模块，先执行 `services/__init__`，它导入 `agent_manager`，`agent_manager` 又导入 `easy_agent.agent`，而后者此时还没初始化完。图上没有环，运行时却报错。
- **动态导入。** `services/scheduler.py:48` 的 `importlib.import_module("easy_agent.services.agent_manager")`，以及 `services/streaming/__init__.py` 惰性转出用的 `import_module(f"{__name__}.{module_name}")`，AST 扫描都看不到。
- **只检查环，不检查方向。** SCC 为 0 是真的（`ast.walk` 会把函数内导入也算进去），但依赖方向现在没有任何检查。

### 2.5 已有约定（本方案沿用）

- 2026-10-18 方案 C1：路由经 repository 访问数据是既定做法，`api → db` 不算违规。
- C2 拆 streaming 时保留了 12 行的 `services/streaming.py` 垫片，约定「保留一个小版本」。它被同名包遮住，运行时从不加载；它注释里说的「按文件路径加载」也已无人使用（仓库里的 `spec_from_file_location` 只用来加载 `scripts/`），可以删除。

## 3. 问题

1. **导入成本和耦合。** 任何模块都要先拉起 agent 栈，脚本、测试收集和小工具都要付这个代价，也让「下层只依赖下层」无从谈起。
2. **运行期循环门禁看不到**（§2.4）。DA-13 S2 被迫把 `builtin_skills` 放进 agent 层，就是绕开这个问题的结果。
3. **边界不清。** `services/` 平铺，领域关系只能靠模块名前缀判断；下层借用上层的常量和私有函数。
4. **门禁只管环，不管方向。** 新的反向依赖可以随时加进来。
5. **兼容门面增加阅读和维护成本。** 光 `services.streaming` 的门面就有 62 个导入名，外加 24 个惰性转出；调用方很难看出一个名字到底定义在哪里。

## 4. 目标与非目标

### 目标

- 每个模块只加载它真正依赖的东西：`import easy_agent.bootstrap.env` 不加载 agent 和 deepagents；`services` 下的模块不会因为包入口而加载 `agent_manager`。
- 分层方向写进门禁，新的违规在 CI 失败。
- `services/` 按领域分包，每个子包的 `__init__.py` 不导入任何模块。
- 行为不变：只做移动和导入改写，不改逻辑。

### 非目标

- 不改 `knowledges/` 的内部结构（另一路会话正在重组，整包视为应用层）。
- 不改前端。
- 不改「路由经 repository 访问数据」的约定。
- 不拆大文件：`agent_manager`、`orchestrator` 等单个文件的内部结构不动。

## 5. 分层模型

层号按模块前缀指定，最长前缀优先：

| 层 | 模块 | 可以导入 |
| --- | --- | --- |
| L0 基础 | `utils`、`bootstrap`、`security`、`models` | L0 |
| L1 基础设施 | `db`、`persistence`、`sandboxing` | L0–L1 |
| L2 运行时适配 | `backends`、`tools.registry` | L0–L2 |
| L3 Agent | `agent` | L0–L3 |
| L4 应用 | `services`、`tools`（`registry` 以外）、`knowledges`（含 `facade`） | L0–L4 |
| L5 Web | `api`、`middleware` | L0–L5 |
| L6 装配 | `app`、根包 `easy_agent` | 全部 |

规则：

- 同层可以互相导入；下层不得导入上层。函数内导入和 `TYPE_CHECKING` 导入同样计入。
- 包的 `__init__.py` 不导入项目内的模块，只写说明；白名单内的包除外（§6.2）。
- `importlib.import_module` 的字面量参数视同静态导入。

`tools` 拆成两层：`tools/registry.py` 是纯元数据的叶子模块，`agent/hitl.py` 和 `agent/core.py` 要用它；工具实现会调用 `expert_service`、`scheduler`、`session_uploads` 等服务，并由 `agent_manager` 通过工厂装配，属于应用层代码。

## 6. 门禁设计

### 6.1 方向检查

`scripts/quality/import_graph_core.py` 增加 `check_layers(graph, config)`，配置放在 `scripts/quality/import_layers.json`：

```json
{
  "schema_version": 1,
  "layers": {
    "easy_agent.utils": 0,
    "easy_agent.bootstrap": 0,
    "easy_agent.db": 1,
    "easy_agent.tools.registry": 2,
    "easy_agent.agent": 3,
    "easy_agent.services": 4,
    "easy_agent.api": 5,
    "easy_agent.app": 6
  },
  "allow": [
    {
      "from": "easy_agent.knowledges.personnel.api",
      "to": "easy_agent.middleware.auth",
      "reason": "知识库纵向切片自带路由，内部分层另行设计"
    }
  ]
}
```

示例只列了部分层号，完整列表见 §5。

- 出现新违规就失败；白名单里已经不存在的条目也失败（与 SCC 白名单一样只减不增）；每个条目必须写 `reason`。
- 阶段 0 落地时，白名单就是 §2.3 的 9 条；阶段 2 每修一处删一条。
- CI：在现有的 architecture-gate 步骤里运行 `python scripts/quality/import_graph.py --check-scc --check-layers`。

### 6.2 包 `__init__` 检查

包的 `__init__.py` 只要导入了项目内的模块就失败。当前带导入的包入口都先进白名单，各写原因：

| 包 | 处理 |
| --- | --- |
| 根包、`services`、`services.streaming`、`utils` | 阶段 1 瘦身后从白名单删除 |
| `agent` | 保留：对外门面（`EasyAgent` 等），使用方都在 L3 及以上 |
| `api` | 保留：路由汇总，供 `app.py` 注册 |
| `knowledges` 及其子包 | 保留：不在本方案范围 |
| `models`、`db`、`db.repositories`、`backends`、`middleware`、`bootstrap.initialization` | 保留：都只导入本包内的模块，逐个评估 |

以后新建的包必须不带导入。

### 6.3 动态导入

在 `_import_edges` 里识别 `importlib.import_module("easy_agent...")` 和 `import_module("easy_agent...")` 的字符串字面量参数，当作普通的边参与 SCC 和方向检查。参数不是字面量的（例如 `f"{__name__}.{x}"`）记为告警；阶段 1 删掉 streaming 的惰性转出后，这类告警应当为 0。

### 6.4 测试

- **门禁用例：** `tests/test_architecture_gate.py` 增加用例，构造小包验证：方向违规会失败；进了白名单能通过；过期的白名单条目会失败；`__init__` 带导入会失败；动态导入会被识别成边。
- **导入隔离用例：** 新增，在子进程里断言：
  - `import easy_agent.bootstrap.env` 之后，`sys.modules` 里没有 `easy_agent.agent` 和 `deepagents`；
  - `import easy_agent.services.health` 之后，`sys.modules` 里没有 `agent_manager`（迁移后用新路径）。

## 7. `__init__` 瘦身

| 包 | 改法 | 要改的调用方 |
| --- | --- | --- |
| 根包 `easy_agent` | 只留 `__version__` 和说明 | 1 处测试 |
| `easy_agent.services` | 只留说明 | 12 个文件的 14 条导入改成直接导入子模块 |
| `easy_agent.services.streaming` | 只留说明；删掉 3 个兼容别名（`_extract_file_paths_from_command`、`_mark_hitl_pending`、`_get_model_context_limit`）和 24 个惰性转出 | 13 个文件的 14 条导入，以及通过 `streaming.X` 访问属性的测试，改成从 `orchestrator`、`projector`、`persistence` 等实际定义的模块导入 |
| `easy_agent.utils` | 只留说明 | api 层 5 个文件的 5 条导入 |

同时删除被遮住的 `services/streaming.py` 垫片。

验收：§6.4 的导入隔离用例通过；`python -c "import easy_agent.app"`、全量测试、所有门禁通过，`easy-web` 能正常启动。

## 8. 子包划分

「外部调用方」按调用方所在的包统计边数，不含 `services` 内部和测试：

| 新路径 | 原模块（行数） | 外部调用方 |
| --- | --- | --- |
| `agents/manager.py` | `agent_manager.py`（694） | api 3（另有 `scheduler.py` 的动态导入） |
| `agents/prompts.py` | `prompt_loader.py`（229） | api 1、app 1 |
| `streaming/processor.py` | `stream_processor.py`（682） | — |
| `streaming/messages.py` | `stream_messages.py`（246） | — |
| `streaming/events.py` | `stream_events.py`（294） | — |
| `streaming/hub.py` | `stream_hub.py`（170） | api 1 |
| `streaming/sse.py` | `sse.py`（179） | api 2、knowledges 2 |
| `streaming/emitter.py` | `sse_emitter.py`（127） | api 3、knowledges 1 |
| `streaming/approvals.py` | `hitl_state.py`（252） | api 1 |
| `streaming/runs.py` | `run_registry.py`（224） | api 1、app 1 |
| `sessions/runtime.py` | `session_runtime.py`（142） | api 2、db 2（§9 修复后为 0） |
| `sessions/thread.py` | `session_thread.py`（278） | api 2 |
| `sessions/uploads.py` | `session_uploads.py`（732） | api 4、tools 1、agent 1（§9 修复后为 0） |
| `sessions/upload_storage.py` | `upload_storage.py`（511） | api 2、app 1 |
| `mcp/tools.py` | `mcp.py`（518） | api 1、agent 1（§9 修复后为 0） |
| `mcp/sessions.py` | `mcp_sessions.py`（882） | app 1 |
| `mcp/policy.py` | `mcp_policy.py`（202） | api 1 |
| `mcp/evidence.py` | `mcp_evidence.py`（472） | — |
| `mcp/connectors.py` | `connectors.py`（303） | api 1 |
| `memory/entries.py`、`locks.py`、`manager.py`、`store.py` | `memory_*.py`（共 1,947） | — |
| `experts/service.py` | `expert_service.py`（798） | api 2、tools 1 |
| `experts/guardrail.py` | `expert_guardrail.py`（85） | api 1 |
| `experts/relevance.py` | `expert_relevance.py`（448） | api 1 |
| `experts/public.py` | `public_expert_service.py`（437） | api 2、app 1 |
| `skills/library.py` | `skill_library.py`（489） | api 3 |
| `skills/categories.py` | `skill_categories.py`（471） | api 1 |

留在根下的都是单模块、横切的：`scheduler.py`、`health.py`、`metrics.py`、`document_convert.py`。

几点说明：

- `hitl_state` 改名为 `streaming/approvals.py`，避免和 `agent/hitl.py` 同名；它只服务于审批恢复流程。
- `run_registry` 是流式运行的租约注册表，依赖 `sse_emitter`，归入 streaming。
- `services/mcp/` 和第三方库 `mcp` 同名，但 Python 3 默认使用绝对导入，`from mcp import ...` 仍然指向第三方库（现在的 `services/mcp.py` 也是同样的情况）。

## 9. 违规修复清单

| # | 违规 | 改法 |
| --- | --- | --- |
| 1 | `agent/core.py` → `services.mcp._parse_mcp_content` | 纯解析函数下沉到 `utils/mcp_content.py`（L0），`services` 一侧改为从那里导入 |
| 2 | `agent/core.py` → `tools/web_search.WEB_SEARCH_SYSTEM_PROMPT` | 常量移到 `tools/registry.py`（L2），作为 web_search 那条 `ToolSpec` 的新字段 `prompt_note`；`agent/core.py` 从注册表读取 |
| 3 | `agent/workspace.py` → `services.session_uploads` 的两个路径常量 | 常量属于工作区布局，移到 `agent/workspace.py`；`session_uploads` 改为从 agent 层导入（上层依赖下层，合规） |
| 4 | `backends/shell.py` → `agent/hitl._is_directory_deletion` | 目录删除、删除类命令的正则等命令策略移到 `sandboxing/command_policy.py`（L1），`agent/hitl.py` 和 `backends/shell.py` 都从那里导入 |
| 5、6 | `db/repositories/{chat,expert}_repository.py` → `services.session_runtime.renew_stream` | 流锁续约本身是数据库操作，移到 `db/repositories/`（例如新建 `session_runtime_repository.py`），由 `services` 层调用 |
| 7 | `knowledges/personnel/api.py` → `middleware` | 留在白名单（§6.1）：知识库纵向切片自带路由，不在本方案范围 |
| 8 | `utils/distributed_lock.py` → `db` | 整个模块移到 `persistence/distributed_lock.py`（L1） |
| 9 | `utils/session.py` → `db` | 会话归属校验移到 `db/session_ownership.py`；它的三个调用方本来就都是 db 层的 repository |

## 10. 迁移步骤

| 阶段 | 内容 | 前置 | 估时 |
| --- | --- | --- | --- |
| 0 门禁 | §6：方向检查、`__init__` 检查、动态导入识别和测试；当前状态全部进白名单，接入 CI | 无 | 0.5–1 天 |
| 1 瘦身 | §7 的四个 `__init__`，删除 `services/streaming.py` 垫片 | 阶段 0；要改的调用方文件上没有在途改动（2026-10-20 时 `app.py`、`api/files.py`、`knowledges/personnel/` 搬迁、`services/stream_messages.py` 都有其他会话未提交的改动） | 0.5–1 天 |
| 2 修违规 | §9 第 1–6、8、9 项，每项一个提交，同步从白名单删条目 | 阶段 0；第 4 项要等 `agent/hitl.py` 的在途改动提交 | 1 天 |
| 3 拆子包 | §8，一个子包一个提交，顺序：memory → skills → experts → sessions → agents → mcp → streaming | 阶段 1；mcp 等 `origin/feat/mcp` 合并，streaming 等改过它的分支合并（§12） | 2–3 天 |
| 4 收尾 | 更新现行文档里的模块路径和 `scripts/quality/bare_getenv_whitelist.json`（有 2 条按文件路径记录）；方向白名单只剩第 7 条 | 阶段 3 | 0.5 天 |

阶段 3 每个子包的迁移流程：

1. 确认要动的文件没有未提交的改动（`git status`），也没有活跃分支在改（`git log HEAD..origin/<分支> -- <文件>`）。
2. 用 `git mv` 移动文件。同一个提交里只做移动和导入改写、不改逻辑，这样其他分支合并时 git 能识别出改名。
3. 用脚本改写导入：
   - `from easy_agent.services.X import ...` 改成新路径；
   - `from easy_agent.services import X` 改成 `from easy_agent.services.<子包> import <新名> as X`；
   - `importlib.import_module` 的字面量参数一并改；
   - 测试里的导入同样处理。
4. `grep` 旧路径结果为 0；全量测试、所有门禁、`python -c "import easy_agent.app"` 都通过。
5. 同步更新现行文档里的路径：`architecture-current.md`、`CLAUDE.md`、`AGENTS.md`、`README.md`、`sse-events.md`、`distributed-lock.md`、`requirements-agent.md`、`framework-integration.md`。审计结论和设计文档里的路径记录的是当时的状态，保持原样；归档文档不改（docs 约定）。

## 11. 测试与验收

- 门禁用例和导入隔离用例（§6.4）。
- 每个阶段都跑：全量测试、ruff、所有质量门禁、`python -c "import easy_agent.app"` 冒烟。
- 最终验收：
  - 方向白名单只剩 1 条（知识库）；
  - `__init__` 白名单里不再有根包、`services`、`utils`；
  - 仓库里对旧路径的引用为 0；
  - `services/` 根下只剩 4 个模块和 7 个子包；
  - `import easy_agent.bootstrap.env` 不再加载 agent。

## 12. 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 活跃分支冲突：今天仍在更新的 5 个远端分支带着尚未合入的 `services/` 改动（`feat/frontend-guocl` 13 个提交，`vue26-1015` 13 个，`zhq/dt-1016` 13 个，`rag` 5 个，`feat/mcp` 4 个；`feat/deepagents-v3` 也有 2 个，但最后更新在 2026-09-15） | 合并时出现改名冲突；分支里新写的旧路径导入会失效 | 纯移动的提交便于 git 识别改名；分支改动多的子包排在后面，等分支合并后再迁；必要时启用 §13 第 1 条的别名垫片 |
| 同一工作区里的并行会话 | 移动文件时打断或覆盖对方的在途改动 | 每一步先看 `git status`，确认目标文件没有未提交的改动再动 |
| 瘦身暴露隐藏的导入顺序依赖 | 启动或部分测试出现 ImportError | 全量测试加应用导入冒烟；按报错补上显式导入 |
| 测试的 patch 打在门面上 | patch 不生效，测试假通过 | 目前没发现对门面对象打的 patch；改写时 patch 目标一律改成定义该名字的模块 |
| 持久化数据里记着模块路径 | 旧数据反序列化失败 | 已核对：调度器用内存 job store，每次启动从数据库重建任务；checkpoint 里只有 LangChain 对象的序列化结果，不含 `services` 的类。风险低 |
| 文档引用失效 | 现行文档里的路径过期 | 阶段 3、4 同步更新（§10 第 5 步） |

## 13. 评审决策（2026-10-20，均按推荐答案）

| # | 问题 | 结论 |
| --- | --- | --- |
| 1 | 旧路径要不要留兼容垫片 | 不留。有活跃分支在改的子包（mcp、streaming）等分支合并后再迁。如果这些分支短期内合不进来，就对这两个子包用 `sys.modules` 别名保留一个小版本（新旧路径指向同一个模块对象，patch 也会同时生效），同时用门禁禁止仓库内新增对旧路径的导入 |
| 2 | 子包命名 | 采用 agents / streaming / sessions / mcp / memory / experts / skills（`mcp` 与第三方库同名的问题见 §8 的说明） |
| 3 | 根包是否保留 `EasyAgent` 等转出 | 不保留，只留 `__version__`；仓库里只有 1 处测试在用 |
| 4 | `knowledges/` 是否纳入 | 不纳入：整包算应用层，唯一的违规（personnel 路由依赖 `middleware`）进白名单，内部结构另行设计 |
| 5 | 函数内导入和 `TYPE_CHECKING` 导入是否计入方向检查 | 计入。它们都是真实的设计依赖，函数内导入只是推迟了执行时机 |
| 6 | 什么时候开始 | 等工作区里其他会话的在途改动（hitl、streaming、files、personnel 搬迁）提交后，再开始阶段 0 和 1；阶段 3 按各分支的合并进度排期 |

## 附：数据采集方法

```bash
# 依赖图与分层统计（JSON 报告，再按 §5 的层号做前缀匹配统计违规）
python scripts/quality/import_graph.py --json-out /tmp/imports.json

# 包入口的导入成本（数值随机器变化，看相对量）
python -c "import time,sys; t=time.perf_counter(); import easy_agent.services.health; print(time.perf_counter()-t, len(sys.modules))"

# 改过 services/ 但尚未合入当前分支的远端分支
git log --oneline HEAD..origin/<branch> -- easy_agent/services
```
