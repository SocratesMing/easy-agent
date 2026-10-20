# Increment 57：FW-12 动态 HITL 工具名单（2026-10-18）

| 项 | 内容 |
| --- | --- |
| 目标 | 按本会话实际注入的工具动态构造 `interrupt_on`，消除未注入工具的静默无效项；文档化目录删除策略 |
| 状态 | ✅ 完成（仅后端；前端未改；未 commit） |
| 设计 | `docs/design/2026-10-18-fw12-dynamic-hitl.md` |
| 范围 | `easy_agent/hitl.py`、`easy_agent/agent.py`（仅 interrupt_on 构造段）、`easy_agent/tools/registry.py`（仅说明）、两个测试文件、本报告 |
| 明确未动 | API、db、streaming、checkpoints、mcp、conftest、前端；审批决策/条件语义；目录删除拒绝逻辑；Batch 0/1；Web Terminal |

## 1. 改动

| 文件 | 内容 |
| --- | --- |
| `easy_agent/hitl.py` | 新增 `HITL_WHEN_PREDICATES`（注册表谓词名 → 实际函数）、`_tool_name()`、`build_interrupt_config()`；模块 docstring 记录 FW-12；原破坏性命令检测/execute 描述/HarnessProfile 逻辑未动 |
| `easy_agent/agent.py` | `_build_interrupt_config()` 改为调用纯函数；`_HITL_WHEN_PREDICATES` 保留为 `hitl.HITL_WHEN_PREDICATES` 别名；移除不再使用的 `InterruptOnConfig` / `hitl_tool_specs` / 谓词函数直接 import；其余段落未动 |
| `easy_agent/tools/registry.py` | 仅补充 `read_uploaded_file` 的 description，说明「名字含 upload，历史启发式会纳入审批，FW-12 保持等价」；注册表字段/顺序/条件未动 |
| `tests/agent/test_hitl.py` | 38 → 184 行：新增 12 项纯函数与 `EasyAgent` 接入测试 |
| `tests/services/test_hitl_destructive.py` | 新增 2 项：目录删除 `when=False`（策略拒绝）；未注入工具不出现 |

## 2. 行为等价证据

### 2.1 命令与结果

| 检查 | 结果 |
| --- | --- |
| `pytest tests/agent/test_hitl.py tests/services/test_hitl_destructive.py tests/services/test_hitl_state.py tests/services/test_hitl_resume.py -q` | **31 passed** |
| `pytest tests/agent/test_tool_registry.py tests/services/test_agent_manager_tools.py -q` | **20 passed**（注册表/注入顺序未回归） |
| `pytest tests/agent/test_graph_cache.py tests/test_framework_contract.py tests/services/test_web_search_injection.py tests/test_file_context.py -q` | **50 passed**（D2 缓存键、`create_deep_agent` 参数、工具注入无回归） |
| `uvx ruff check .` | **All checks passed** |
| `python3 scripts/quality/import_graph.py --check-scc` | **OK**：`217 modules, 488 internal edges, 0 SCC(s) / 0 modules`，与白名单完全一致 |

### 2.2 语义对照

| 场景 | 旧 `interrupt_on` | 新 `interrupt_on` | 判定 |
| --- | --- | --- | --- |
| 知识库关 + 无 expert-creator + file_context 开 + 无 MCP | `execute`、`save_expert`*、`create_scheduled_task`、`access_knowledge_original`*、`read_uploaded_file` | `execute`、`create_scheduled_task`、`read_uploaded_file` | 仅移除两个静默无效项；实际工具配置不变 |
| 知识库开 + expert-creator + file_context 开 | 同上 5 项 | 同上 5 项 | 逐项一致 |
| 含 MCP 写工具 `mysql_execute_sql` | 追加 `mysql_execute_sql` | 追加 `mysql_execute_sql` | 名字启发式/顺序不变 |
| `enable_hitl=False` | `None` | `None` | 一致 |
| 直接 `EasyAgent(mcp_tools=[])` | 4 项（其中 3 项未注入） | `execute` | 与真实工具集一致 |
| 应用工具工厂抛异常 | 仍写该名字（无效） | 不写 | 配置反映真实能力 |

「实际注入的每个工具」的 `allowed_decisions`、`when` 谓词、声明顺序均与改造前
逐项相同：

- `execute`：`["approve","reject"]` + `_is_destructive_command`；
- `access_knowledge_original`：`["approve","reject"]` + `_is_knowledge_materialize`；
- `save_expert` / `create_scheduled_task`：`["approve","reject"]`，无条件；
- MCP 写工具：`["approve","reject"]`，追加在声明工具之后；
- 顺序：`execute → save_expert → create_scheduled_task → access_knowledge_original → MCP`。

### 2.3 等价性由测试锁定

- `test_build_interrupt_config_only_adds_injected_registry_tools`：空/仅 MCP 时不出现
  `save_expert`/`create_scheduled_task`/`access_knowledge_original`；
- `test_build_interrupt_config_includes_every_injected_hitl_tool`：注入后出现且决策/
  谓词正确；
- `test_build_interrupt_config_preserves_registry_hitl_order`：声明顺序逐字锁定；
- `test_agent_interrupt_config_changes_with_tool_set`：`mcp_tools` 变化 → 名单变化；
- `test_build_interrupt_config_keeps_legacy_read_uploaded_file_heuristic`：历史
  `upload` 启发式仍生效（行为等价）；
- `test_build_interrupt_config_keeps_execute_conditional_for_directory_deletion`：
  目录删除 `when=False`，由 shell backend 直接拒绝。

## 3. 目录删除策略（已文档化）

- `hitl._is_destructive_command()` 对目录删除返回 `False` → 不进入 HITL；
- `backends/shell.py:87-94` 在沙箱执行前直接拒绝（`exit_code=1`，返回明确提示）；
- 工具描述/系统提示词已声明「目录删除会被直接拒绝」；
- 设计文档新增 §3「目录删除策略」，明确这是**策略层拒绝**而非漏配；用户无法
  「批准」目录删除；
- 正则绕过（`rm -f -r`、脚本删除等）属 SEC-17 / T3-16，本任务不动。

## 4. 未覆盖工具

1. **MCP 动态工具**：无法静态枚举；只能在本会话实际加载后按名字启发式判定。
   未登记且名字不含写操作关键字的 MCP 工具不会进入审批（与改造前一致）。
2. **子代理工具**：`subagents.py` 的 `interrupt_on` 继承主图；子代理独有工具若不在
   主图工具集内，仍由框架静默跳过；本次未枚举子代理工具表。
3. **框架内置工具**：`execute` 等「始终可用」来自注册表 `builtin_tool_names()` 的
   0.6.8 快照；若升级 DeepAgents 后内置集合变化，需要同步注册表。
4. **`read_uploaded_file`**：注册表 `is_hitl=False`，但名字含 `upload`，历史启发式
   会将其纳入审批。为「不改变审批行为语义」保持现状；是否改为显式 HITL 是后续
   产品决策。
5. **`write_file` / `edit_file`**：内置写工具，改造前不在 `mcp_tools`，本次也不会被
   启发式加入；是否进 HITL 仍是独立产品决策。
6. **目录删除正则绕过**：SEC-17 / T3-16 独立问题，未在本次修复。
7. **前端审批 UI / SSE 事件**：未改；`approval_required` / resume 契约不变。

## 5. 风险

| 风险 | 说明 / 缓解 |
| --- | --- |
| `EasyAgent.mcp_tools` 命名误导（实为会话工具全集） | docstring 与设计文档注明；不改参数名以缩小 diff |
| D2 编译图缓存键变化 | HITL 名单参与 `build_graph_key`；部分会话键会变化，属预期；缓存仍按完整编译输入隔离 |
| 直接 `EasyAgent(...)` 的脚本/测试配置项变少 | 目标即「未注入不配置」；空工具集测试已覆盖 |
| 注册表与真实注入再次漂移 | 纯函数只信实际集合；新增 HITL 工具必须同时改注册表与注入路径 |
| 内置工具集合与框架版本漂移 | 注册表是显式快照；升级时跑 `tests/agent/test_tool_registry.py` |
| `read_uploaded_file` 继续被启发式审批 | 行为等价优先；已显式记录，后续可单独决策 |
| 条件谓词绑定漂移 | `HITL_WHEN_PREDICATES` 与注册表 `hitl_predicate_names()` 有测试断言；缺失时 error + 退化为无条件审批 |

## 6. 验收 / 备注

- 仅后端；未改前端、API、db、streaming、checkpoints、mcp、conftest；
- 未 commit；未回退 wave1 未提交改动；未执行 Batch 0/1；未动 Web Terminal；
- 工作区另有其他批次未提交改动，本次 SCC 数字包含它们；SCC 模块集合仍与白名单一致。
