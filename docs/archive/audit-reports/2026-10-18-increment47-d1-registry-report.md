# Increment 47：D1 工具注册表（2026-10-18）

| 项 | 内容 |
| --- | --- |
| 目标 | 后端工具元数据单一来源：新增工具不再改 `agent_manager` 主流程，HITL 名单由注册表派生 |
| 状态 | ✅ 完成（仅后端；前端未改；未 commit） |
| 设计 | `docs/design/2026-10-18-d1-tool-registry.md` |
| 范围 | 新增 `easy_agent/tools/registry.py`；改 `easy_agent/services/agent_manager.py`（工具装配段）、`easy_agent/hitl.py`（默认名单/名字启发式段）；新增两个测试文件 |

## 1. 改动

| 文件 | 内容 |
| --- | --- |
| `easy_agent/tools/registry.py`（新增，539 行） | `ToolSpec` / `ToolBuildContext` / `TOOL_REGISTRY`（14 条）+ 查询函数 + `build_session_tools`；导入期校验名字唯一、`inject_order` 不冲突、HITL 决策合法 |
| `easy_agent/services/agent_manager.py` | 新增 `_tool_factories()`（4 个工具工厂，懒 import 与改造前一致）；`_get_or_create_agent_unlocked` 的 5 段 `if/append`（原 353–412 行）替换为一次 `build_session_tools(mcp_tools, ToolBuildContext(...))`；其余段落未动 |
| `easy_agent/hitl.py` | `DEFAULT_INTERRUPT_TOOLS = default_interrupt_tool_names()`；`looks_like_mutating_tool` 的豁免集合改为 `name_heuristic_exempt_tools()`；其余（删除检测、execute 描述、HITL 提示词）未动 |
| `tests/agent/test_tool_registry.py`（新增，277 行） | 13 项：唯一性/必填字段、来源枚举、HITL 决策、内置清单、注入顺序、注入条件、装配顺序/异常/列表返回、真实工厂产出、`DEFAULT_INTERRUPT_TOOLS` 一致、名字启发式 |
| `tests/services/test_agent_manager_tools.py`（新增，196 行） | 4 项：spy `EasyAgent` + mock MCP/联网，断言两种开关组合下工具名与顺序；注册表新增条目自动注入；工厂表与可注入条目对齐 |
| `docs/design/2026-10-18-d1-tool-registry.md`（新增） | Schema、覆盖清单、接入点、HarnessProfile 评估、未覆盖项、风险 |

未改：`agent.py`、`app.py`、`checkpoints.py`、`streaming.py`、`mcp.py`、db、conftest、前端、`subagents.py`。

## 2. 注册表覆盖（14 条）

内置 9 条（`source=builtin`，不装配）：
`ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`execute`、`write_todos`、`task`。

应用注入 5 条（按 `inject_order`）：

| 顺序 | 工具 | source | mutating | HITL | 注入条件 |
| --- | --- | --- | --- | --- | --- |
| 10 | `access_knowledge_original` | knowledge | 是 | 条件（materialize） | 总是尝试，`extend_agent_tools` 内部按 `KnowledgeConfig.enabled` 决定 |
| 20 | `create_scheduled_task` | scheduling | 是 | 是 | `enable_hitl=True` |
| 30 | `save_expert` | expert | 是 | 是 | `skill_dirs` 含 `expert-creator`（`None`=跟随缓存，保持注入） |
| 40 | `read_uploaded_file` | file_context | 是 | 否 | `config.file_context.enabled`（缺省开启） |
| 50 | `web_search` | web_search | 否 | 否 | `create_web_search_tool` 返回非 `None` |

`execute` 与 `access_knowledge_original` 标记 `hitl_conditional=True`（带 `when` 谓词），
由 `agent.py` 直接构造，不进默认名单。

## 3. 接入点

### 3.1 `agent_manager`

- `_tool_factories()`（`agent_manager.py:142`）：按工具名返回 4 个工厂
  （`extend_agent_tools` / `CreateScheduledTaskTool` / `create_save_expert_tool` /
  `ReadUploadedFileTool`），import 时机与改造前一致；
- `tools = build_session_tools(mcp_tools, ToolBuildContext(..., factories=_tool_factories()))`
  （`agent_manager.py:397`）：MCP 工具原样作为基底，其余按注册表顺序/条件追加；
- 每个条目独立 try/except：构造失败只告警跳过，不影响其余工具。

### 3.2 `hitl`

- `DEFAULT_INTERRUPT_TOOLS = default_interrupt_tool_names()` → `("save_expert", "create_scheduled_task")`
  （`hitl.py:38`），`agent.py` 的消费方式未改；
- 名字启发式豁免集合 `name_heuristic_exempt_tools()`（`hitl.py:42`）= 已单独 HITL 的 4 个工具
  + 只读内置工具；对现有 MCP 工具名判定结果与改造前一致。

### 3.3 Import 图约束（实现取舍）

`scripts/quality/import_graph.py` 的 AST 扫描把函数内 import 也计入 SCC，且白名单要求模块集合精确匹配：
`registry` 一旦 import `knowledge_impl`/`scheduled_task`，就会被并入既有
knowledge→scheduler→agent_manager SCC（16→20）；反过来删掉 `agent_manager` 原有 import 边，
SCC 会缩到 9。两种方向都失败。

最终方案：**`tools/registry.py` 不 import 任何包内模块**（叶子模块），工具工厂由装配根
`_tool_factories()` 注入。既保持 SCC 与白名单完全一致，也让注册表不反向依赖装配方。

## 4. 行为等价证据

| 检查 | 结果 |
| --- | --- |
| `pytest tests/agent tests/services/test_agent_manager_cache.py tests/services/test_agent_manager_tools.py tests/services/test_hitl_destructive.py -q` | **28 passed**（0.10s） |
| 追加 `tests/services/test_hitl_state.py tests/services/test_hitl_resume.py -q` | **8 passed** |
| `python3 scripts/quality/import_graph.py --check-scc` | OK —— `211 modules / 1 SCC(16)`，SCC 模块集合与白名单完全一致（边数随其他批次并行改动浮动） |
| `uvx ruff check .` | All checks passed |

等价性由测试逐项锁定：

1. `DEFAULT_INTERRUPT_TOOLS == registry.default_interrupt_tool_names() == ("save_expert", "create_scheduled_task")`；
2. `ordered_injectable_specs()` 顺序 = `access_knowledge_original → create_scheduled_task →
   save_expert → read_uploaded_file → web_search`；
3. agent_manager spy 断言两种场景的最终工具名与顺序：
   - HITL 开 + expert-creator + file_context 开：`mcp_a, access_knowledge_original,
     create_scheduled_task, save_expert, read_uploaded_file`；
   - HITL 关 + 无技能 + file_context 关 + 联网开：`mcp_a, access_knowledge_original, web_search`；
4. 单条目构造失败只跳过自身（注册表测试用抛错 builder 验证）；
5. `looks_like_mutating_tool` 对原 9 项豁免名单仍返回 `False`，`mysql_execute_sql` 仍为 `True`。

## 5. 未迁移硬编码（明确保留）

1. `agent.py:468-494`：`execute` / `access_knowledge_original` 的 `when` 谓词与
   `InterruptOnConfig` 构造（本任务禁改 `agent.py`，且属 HITL 策略行为）；
2. MCP 动态工具：名字/数量由 `mcp.json` 与连接器开关决定，注册表不枚举，
   `resolve_tool_source` 对未知名字返回 `mcp`；
3. `agent_manager._tool_factories()` 的 4 个 import + 映射项：新增需构造的工具时仍要加一行
   工厂（装配根职责，主流程不变）；
4. 前端 `frontend-vue2/src/utils/toolPresentation.js` 卡片映射（本任务不改前端）；
5. `subagents.py` 与 `HITL_SYSTEM_NOTE` 中的工具名是提示词文案，不参与名单派生；
6. `write_file`/`edit_file` 是否进 HITL 仍是独立产品决策（注册表已留 `is_hitl` 开关）。

## 6. 风险

| 风险 | 说明 / 缓解 |
| --- | --- |
| 注入顺序漂移 | `inject_order` 固定 10/20/30/40/50，测试断言精确顺序 |
| HITL 名单顺序漂移 | `hitl_order`（save_expert=10、create_scheduled_task=20）+ 测试逐字断言 |
| 工厂表与注册表漂移 | 新增测试断言工厂表 = 4 个需构造工具，且都存在于可注入条目；缺工厂时注册表告警并跳过 |
| 注册表被并入 SCC | 叶子模块 + 装配根提供工厂；SCC 门禁实测通过 |
| 工具构造失败影响面 | 逐条目 try/except，失败仅告警 |
| `extend_agent_tools` 语义变化 | 当前以空列表调用、只取追加条目；若未来改为变换列表需同步 builder（设计文档已记录假设） |

## 7. 备注

- 未 commit；工作区另有其他批次（阶段 0/C2 等）未提交改动与未跟踪文件
  （如 `easy_agent/services/streaming/orchestrator.py`），本次 SCC 数字包含它们，
  但 SCC 模块集合仍与白名单一致。
- `easy_agent/hitl.py` 在当前工作树为未跟踪文件（此前增量新增、未提交），
  因此 `git diff` 不显示其改动；内容以文件现状为准。
