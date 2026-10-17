# D1 工具注册表（仅后端）

| 项 | 内容 |
| --- | --- |
| 状态 | 已实现（后端注册表 + agent_manager/hitl 接入；前端未改） |
| 日期 | 2026-10-18 |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 D1；审计 ARCH-18 / T4-22 |
| 范围 | 新增 `easy_agent/tools/registry.py`；改 `easy_agent/services/agent_manager.py`（仅工具装配段）、`easy_agent/hitl.py`（仅默认 interrupt 名单与工具名判定段）；新增 `tests/agent/test_tool_registry.py`、`tests/services/test_agent_manager_tools.py` |
| 明确不改 | `agent.py`、`app.py`、`checkpoints.py`、`streaming.py`、`mcp.py`、db、conftest、前端；不改 HITL 策略行为（条件、决策、审批时机） |
| 报告 | `docs/audit/2026-10-18-increment47-d1-registry-report.md` |

## 1. 问题与目标

改造前新增一个 Agent 工具需要同步修改至少 3 处后端代码：

1. `easy_agent/services/agent_manager.py:353-412`：在 `_get_or_create_agent_unlocked`
   里按工具逐个 `if <条件>: tools.append(...)`，并各自写日志与 try/except；
2. `easy_agent/hitl.py:34`：`DEFAULT_INTERRUPT_TOOLS` 手写工具名元组；
3. `easy_agent/hitl.py:71-85`：`looks_like_mutating_tool` 手写「已知工具」豁免名单，
   名单与真实工具清单可能漂移。

D1 的目标是**后端工具元数据的单一来源**：工具的名字、来源、是否内置、是否写操作、
是否进入 HITL、注入条件与顺序，只在注册表声明一次；`agent_manager` 只遍历注册表装配，
`hitl` 从注册表派生默认 `interrupt_on` 名单。行为（注入条件/顺序、HITL 名单内容与
条件审批）必须与改造前等价。

本任务**不做**：前端工具卡片映射（`frontend-vue2/src/utils/toolPresentation.js`）、
`execute` 描述注入方式改造（FW-04）、HITL 策略调整（如 `write_file` 是否进 HITL）。

## 2. 注册表 Schema

`easy_agent/tools/registry.py` 定义不可变 `ToolSpec`：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `name` | `str` | 是 | 工具名，注册表内唯一（模块导入时校验） |
| `source` | `str` | 是 | 来源：`builtin` / `knowledge` / `expert` / `scheduling` / `file_context` / `web_search`；未登记名字一律视为 `mcp` |
| `description` | `str` | 否 | 一句话说明（只做文档/排查，不注入模型，避免产生第二份工具描述） |
| `mutating` | `bool` | 是 | 是否可能写工作区/外部状态（前端徽标与人工审查用，本任务不消费） |
| `is_hitl` | `bool` | 是 | 是否配置人工审批（`interrupt_on`） |
| `interrupt_decisions` | `tuple[str, ...]` | 条件必填 | `is_hitl=True` 时必须非空，取值 `approve` / `reject` |
| `hitl_conditional` | `bool` | 否 | HITL 是否带 `when` 谓词。带谓词的由 `agent.py` 直接构造（本任务不改），**不进入** `DEFAULT_INTERRUPT_TOOLS` |
| `hitl_order` | `int` | 否 | 派生 `DEFAULT_INTERRUPT_TOOLS` 时的排序键（保证元组顺序与改造前逐字一致） |
| `inject_order` | `int \| None` | 否 | `None` = 由框架自动提供、无需装配；非 `None` = `build_session_tools` 的追加顺序 |
| `condition` | `Callable[[ToolBuildContext], bool] \| None` | 否 | 注入条件；`None` 表示总是尝试 |
| `condition_note` | `str` | 否 | 条件的可读说明（设计/报告/排查用） |
| `builder` | `Callable[[ToolBuildContext], Any] \| None` | 条件必填 | 工具工厂；返回单个工具、工具列表或 `None`。可注入条目的 `builder` 必须存在 |

派生属性：

- `is_builtin`：`source == "builtin"`（字段与来源不重复存储，避免两处漂移）；
- `is_injectable`：`inject_order is not None`。

来源常量：`SOURCE_BUILTIN/SOURCE_KNOWLEDGE/SOURCE_EXPERT/SOURCE_SCHEDULING/SOURCE_FILE_CONTEXT/SOURCE_WEB_SEARCH/SOURCE_MCP`。
`resolve_tool_source(name)`：已登记返回 `spec.source`，未登记返回 `mcp`（MCP 工具由
`services/mcp.py` 运行时加载，注册表无法静态枚举）。

### 2.1 校验规则（`ToolSpec.__post_init__` + 模块级 `_validate_registry`）

- `name` 去空白后非空；全表 `name` 唯一，重复即 `ValueError`（导入期失败，不是运行期才炸）；
- `source` 必须在已知枚举内；
- `is_hitl=True` 时 `interrupt_decisions` 非空且每个决策都在 `approve/reject` 内；非 HITL 不得携带决策；
- `hitl_conditional=True` 必须同时 `is_hitl=True`；
- 有 `inject_order` 必须有 `builder`；有 `builder` 必须有 `inject_order`（内置工具不装配）。

### 2.2 公开查询/迭代函数

| 函数 | 用途 |
| --- | --- |
| `iter_tool_specs()` | 按声明顺序迭代全部条目 |
| `tool_names()` / `get_tool_spec(name)` / `require_tool_spec(name)` | 名单查询、按名查询（后者未命中 `KeyError`） |
| `builtin_tool_names()` | DeepAgents 内置工具名单 |
| `hitl_tool_specs()` / `default_interrupt_tool_names()` / `is_hitl_tool(name)` | HITL 元数据与默认名单 |
| `is_mutating_tool(name)` / `name_heuristic_exempt_tools()` | 写操作标记；名字启发式豁免集合 |
| `resolve_tool_source(name)` | 来源判定（未知 = MCP） |
| `ordered_injectable_specs()` | 按 `inject_order` 排序的可注入条目 |
| `build_session_tools(base_tools, ctx, *, specs=None)` | 装配会话工具列表（`specs` 仅供测试注入） |

### 2.3 Import 图约束：注册表是叶子模块

`scripts/import_graph.py --check-scc` 使用 AST 统计**函数内 import**，
且白名单要求 SCC 模块集合**精确匹配**。`knowledge_impl` / `tools.scheduled_task`
已位于既有的 knowledge→scheduler→agent_manager SCC（白名单项
`knowledge-chat-scheduler-core`）：只要 `tools/registry.py` 出现包内 import 边，
`registry` / `hitl` / `agent` 就会被并进该 SCC；反过来，若删掉 `agent_manager`
原有的 `knowledge_impl` / `scheduled_task` import 边，SCC 又会缩小——两种方向都会
让门禁失败。

因此本实现采用：

- `tools/registry.py` **不 import 任何包内模块**（顶层只有标准库），是叶子模块；
- 工具工厂由装配根 `agent_manager._tool_factories()` 提供（函数内 import，
  与改造前加载时机一致），经 `ToolBuildContext.factories` 注入；
- 注册表只按名字取工厂（`_get_factory`），工厂缺失时该条目告警并跳过。

这样 SCC 仍是白名单那 16 个模块（实测 `211 modules / 1 SCC(16)`；边数随其他
并行批次的文件变化而浮动，门禁只比对 SCC 模块集合），新增工具时也不需要改
`agent_manager` 的装配主流程，只需在 `_tool_factories()` 里补一个 import + 一个映射项。

## 3. 覆盖清单

### 3.1 DeepAgents 内置工具（框架提供，不装配）

| 工具 | source | mutating | HITL | 说明 |
| --- | --- | --- | --- | --- |
| `ls` | builtin | 否 | 否 | 列目录 |
| `read_file` | builtin | 否 | 否 | 读文件 |
| `write_file` | builtin | 是 | 否 | 写入；是否进 HITL 为独立产品决策，本任务不动 |
| `edit_file` | builtin | 是 | 否 | 编辑；同上 |
| `glob` | builtin | 否 | 否 | 路径匹配 |
| `grep` | builtin | 否 | 否 | 内容检索 |
| `execute` | builtin | 是 | 是（条件） | `agent.py` 以 `_is_destructive_command` 为 `when`；目录删除直接拒绝 |
| `write_todos` | builtin | 否 | 否 | 任务规划 |
| `task` | builtin | 否 | 否 | 子代理派生 |

### 3.2 应用注入工具（`build_session_tools` 按序装配）

| 顺序 | 工具 | source | mutating | HITL | 注入条件（与改造前一致） |
| --- | --- | --- | --- | --- | --- |
| 10 | `access_knowledge_original` | knowledge | 是 | 是（条件） | 总是尝试；由 `extend_agent_tools` 内部按 `KnowledgeConfig.enabled` 决定是否返回工具 |
| 20 | `create_scheduled_task` | scheduling | 是 | 是 | `enable_hitl=True` |
| 30 | `save_expert` | expert | 是 | 是 | `creator_requested is None or creator_requested`（`skill_dirs` 含 `expert-creator`） |
| 40 | `read_uploaded_file` | file_context | 是 | 否 | `config.file_context.enabled`（缺省视为开启） |
| 50 | `web_search` | web_search | 否 | 否 | 本轮请求开启且 `web_search` 已配置（`create_web_search_tool` 返回非 `None`） |

装配结果 = `list(mcp_tools)` + 上述条目按顺序追加。`access_knowledge_original` 与
`execute` 的 HITL 带 `when` 谓词，仍由 `agent.py:468-494` 直接构造，不在默认名单内。

## 4. 与 `agent_manager` 的接入点

改造前（`easy_agent/services/agent_manager.py:353-412`）：`tools = list(mcp_tools)` 之后是
5 段 `if/append` + 各自日志 + try/except。

改造后：同一位置只保留一次调用——

```python
tools = build_session_tools(
    mcp_tools,
    ToolBuildContext(
        config=config,
        username=username,
        session_id=session_id,
        workspace_name=workspace_name,
        enable_hitl=enable_hitl,
        model_name=model_name or "",
        creator_requested=creator_requested,
        web_search_tool=web_search_tool,
        edit_targets=_session_edit_targets,
        factories=_tool_factories(),
    ),
)
```

`_tool_factories()` 是装配根提供的工厂表（`access_knowledge_original` /
`create_scheduled_task` / `save_expert` / `read_uploaded_file` 的构造入口），
内部 `import` 与改造前逐项相同（`knowledge_impl`、`tools.expert_writer`、
`tools.scheduled_task`、`tools.session_file_reader`），因此 import 图 SCC 不变
（见 §2.3）。

保持不变的部分：

- `create_web_search_tool` 仍在缓存判定**之前**构造（`web_search_enabled` 参与缓存驱逐），
  注册表只消费已构造好的 `ctx.web_search_tool`，不改变缓存语义；
- MCP 工具仍由 `load_mcp_tools_for_user(..., server_names=only_connectors)` 异步加载，
  作为 `base_tools` 原样保留在最前；
- 每个条目的构造仍逐个 try/except（注册表统一 `logger.warning("注入 <name> 工具失败: ...")`
  并跳过），单点失败不影响其余工具；
- 成功注入的日志文案保留（`save_expert` / `read_uploaded_file` / `web_search`）。

`agent_manager` 的其他段落（缓存、连接器、专家、流任务）未改。

## 5. 与 `hitl` 的接入点

- `DEFAULT_INTERRUPT_TOOLS = default_interrupt_tool_names()`：由 `is_hitl and not
  hitl_conditional` 的条目按 `hitl_order` 排序派生，结果仍是
  `("save_expert", "create_scheduled_task")`。`agent.py` 对该元组的消费方式（`setdefault` +
  `InterruptOnConfig(allowed_decisions=["approve","reject"])`）未改。
- `looks_like_mutating_tool` 的豁免集合改为 `name_heuristic_exempt_tools()`：
  `is_hitl or (is_builtin and not mutating)`。对现有 MCP 工具名判定结果与改造前一致
  （原名单 9 项全部覆盖；新增的 `task` 不匹配写操作正则，结果不变）。
- `execute` / `access_knowledge_original` 的条件审批仍由 `agent.py` 的 `when` 谓词实现，
  注册表只记录 `is_hitl=True`、`hitl_conditional=True` 与决策集合，不改变审批行为。

## 6. DeepAgents 公开能力评估（为何本任务不用 `HarnessProfile`）

DeepAgents 0.6.8 提供公开的 `deepagents.profiles.HarnessProfile` 与
`register_harness_profile(key, profile)`，字段包括 `base_system_prompt`、
`system_prompt_suffix`、`tool_description_overrides`、`excluded_tools`、
`excluded_middleware`、`extra_middleware`、`general_purpose_subagent`。

评估结论：它解决的是「按 key 全局改写某个 harness 的提示词/工具描述/中间件」，
**不表达**工具来源、写操作标记、HITL 决策、按会话/按技能/按连接器的注入条件；
且 `register_harness_profile` 是进程级全局注册，本任务若使用会引入「注册顺序影响所有
会话」的新耦合。因此 D1 只用它做能力核对，不引入；`execute` 描述注入仍走现状
（`FilesystemMiddleware.__init__` 包装），改造列入 FW-04。

## 7. 未覆盖项（明确不在 D1）

1. **MCP 动态工具**：名字与数量取决于用户 `mcp.json` 与连接器开关，注册表不静态枚举；
   `resolve_tool_source` 对未登记名字返回 `mcp`，HITL 仍靠名字启发式（`looks_like_mutating_tool`）。
2. **条件 HITL 谓词**：`execute` 的删除检测、`access_knowledge_original` 的 `materialize`
   判定仍在 `agent.py`（本任务禁改），注册表只存元数据。
3. **前端呈现元数据**：`frontend-vue2/src/utils/toolPresentation.js` 的卡片映射未改，
   注册表暂不提供 `card/kind/title` 字段，避免出现「后端声明了但无人消费」的第二份数据。
4. **子代理工具文案**：`easy_agent/subagents.py` 的 `knowledge-researcher` 描述里提到
   `access_knowledge_original`，那是给模型看的提示词而非工具清单，本任务不改。
5. **HITL 系统提示词文案**：`HITL_SYSTEM_NOTE` 里的工具名是自然语言说明，不参与名单派生。
6. **`write_file`/`edit_file` 是否进 HITL**：产品决策，本任务只保证「改注册表一处即可生效」。
7. **`delete` 工具**：DeepAgents 0.7 才有，当前 0.6.8 无此工具，不登记。

## 8. 行为等价性与测试

新增测试（不依赖 LLM/网络/DB）：

- `tests/agent/test_tool_registry.py`：条目唯一性与必填字段、来源枚举、HITL 决策合法性、
  内置工具清单、可注入顺序、各注入条件、`build_session_tools` 的顺序/异常/列表返回、
  真实工厂产出的工具名、`DEFAULT_INTERRUPT_TOOLS` 与注册表一致、名字启发式豁免；
- `tests/services/test_agent_manager_tools.py`：spy `EasyAgent` + mock MCP/联网工具，
  断言两种场景下注入的工具名与顺序与改造前一致（无 LLM/DB/网络）。

回归：`tests/agent tests/services/test_agent_manager* tests/services/test_hitl_destructive.py -q`。

## 9. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 装配顺序变化导致工具提示/缓存哈希变化 | 注册表 `inject_order` 固定 10/20/30/40/50，测试断言精确顺序 |
| HITL 名单顺序或内容变化 | `hitl_order` + 测试断言元组逐字相等 |
| 注册表 import 变重引入循环依赖 | 注册表保持无包内 import 的叶子模块；工具/知识库工厂由 `agent_manager._tool_factories()` 注入（§2.3）；`import_graph --check-scc` 门禁 |
| `extend_agent_tools` 未来改为「变换列表」而非「追加」 | 当前以空列表调用并只取返回工具；设计文档记录该假设，若语义变化需同步注册表 builder |
| 单点工具构造失败影响面扩大 | 保持逐条目 try/except，失败仅告警并跳过 |

回滚：删除 `tools/registry.py`，恢复 `agent_manager.py` 的 5 段 `if/append` 与
`hitl.py` 的手写常量即可；注册表无持久化、无迁移、无 API 变更。
