# FW-12 动态 HITL 工具名单（仅后端）

| 项 | 内容 |
| --- | --- |
| 状态 | 已实现（后端；前端未改；未 commit） |
| 日期 | 2026-10-18 |
| 依据 | 架构整改计划 FW-12；审计 T1-14 / T3-16 / SEC-17；D1 工具注册表 |
| 范围 | `easy_agent/hitl.py`（新增纯函数与谓词绑定）、`easy_agent/agent.py`（仅 `_build_interrupt_config` 段与 import）、`easy_agent/tools/registry.py`（仅 `read_uploaded_file` 说明）、`tests/agent/test_hitl.py`、`tests/services/test_hitl_destructive.py` |
| 明确不改 | API、db、streaming、checkpoints、mcp、conftest、前端；不改审批决策/条件语义；不改目录删除拒绝逻辑 |
| 报告 | `docs/audit/2026-10-18-increment57-fw12-report.md` |

## 1. 问题与目标

`create_deep_agent(interrupt_on=...)` 交给 `HumanInTheLoopMiddleware` 的是一张
「工具名 → 审批配置」表。中间件只按名字匹配当前工具表；**名字不在本会话工具表里
时静默跳过**，既不建立中断也不报错（T1-14）。

改造前 `EasyAgent._build_interrupt_config()` 无条件预置：

- `execute`（条件 HITL，`_is_destructive_command`）；
- `DEFAULT_INTERRUPT_TOOLS`：`save_expert`、`create_scheduled_task`；
- `access_knowledge_original`（条件 HITL，`materialize`）；
- 再遍历 `self.mcp_tools`，把名字形如 write/delete/upload/... 的工具按启发式加入。

问题在于工具是否真的注入取决于运行条件：

| 工具 | 实际注入条件 |
| --- | --- |
| `access_knowledge_original` | `KnowledgeConfig.enabled` 为真且工厂成功 |
| `save_expert` | 本轮 `skill_dirs` 含 `expert-creator`（或跟随缓存时已注入） |
| `create_scheduled_task` | `enable_hitl=True` 且工厂成功 |
| MCP 写工具 | 连接器启用且 MCP 加载成功 |

因此「知识库未启用」「普通聊天未激活 expert-creator」「工具工厂失败」等会话的
`interrupt_on` 里会留下**永不生效**的条目；配置看起来覆盖了很多工具，实际只有
一部分生效。这既误导审计/运维，也会让 D2 编译图缓存键里的 HITL 名单与真实能力
不一致。

FW-12 的目标：**从本会话实际注入的工具集合生成 `interrupt_on`**，未注入/未知
工具不进入配置（debug 日志），同时保持已有审批语义（决策集合、`when` 条件、
工具顺序、MCP 写工具、`save_expert`/`create_scheduled_task` 现有行为）。

## 2. 设计

### 2.1 三个输入源

1. **注册表声明**（`easy_agent/tools/registry.py`）：哪些工具是 HITL、允许的决策、
   条件谓词名、`hitl_order`。这是元数据唯一来源，不在 `agent.py` 复制名单。
2. **实际注入工具**：`EasyAgent.mcp_tools` 实际承载的是
   `agent_manager.build_session_tools()` 返回的「MCP + 应用注入工具」全集
   （历史命名，参数名保持不变以缩小 diff）。框架内置工具（`execute` 等）
   不在该列表里，由注册表的 `builtin_tool_names()` 视为始终可用。
3. **动态 MCP 工具**：未登记名字一律按名字启发式
   （`looks_like_mutating_tool`）判定，与 `resolve_tool_source` 的「未登记 = mcp」
   口径一致。

### 2.2 纯函数

`easy_agent/hitl.py` 新增：

```python
def build_interrupt_config(
    tool_names: Iterable[object],
    *,
    enable_hitl: bool = True,
    specs: Iterable[ToolSpec] | None = None,
    predicates: Mapping[str, Callable[[ToolCallRequest], bool]] | None = None,
    sid: str = "",
) -> dict[str, bool | InterruptOnConfig] | None:
```

算法：

```text
if not enable_hitl: return None
names        = 实际注入工具名（工具对象或字符串，保持注入顺序）
builtins     = registry.builtin_tool_names()
available    = set(names) | builtins
config       = {}

for spec in registry.hitl_tool_specs():          # execute → save_expert →
    if spec.name not in available:               # create_scheduled_task →
        debug("未实际注入，跳过")                 # access_knowledge_original
        continue
    options = {"allowed_decisions": spec.interrupt_decisions}
    if spec.hitl_predicate:
        predicate = HITL_WHEN_PREDICATES.get(spec.hitl_predicate)
        predicate 缺失 → error 日志 + 退化为无条件审批（宁可多问）
        predicate 存在 → options["when"] = predicate
    config.setdefault(spec.name, InterruptOnConfig(**options))

for name in names:                               # 动态 MCP 写工具
    if name in builtins or name in config: continue
    if looks_like_mutating_tool(name):
        config.setdefault(name, InterruptOnConfig(["approve", "reject"]))
return config
```

要点：

- `execute` 是 DeepAgents 内置工具，注册表把它列为 builtin，因此总在配置里；
  若未来框架移除/改名 `execute`，必须同步注册表，否则仍会静默无效（见 §7 风险）；
- `save_expert` / `create_scheduled_task` / `access_knowledge_original` 只有实际
  出现在 `mcp_tools` 时才加入；
- MCP 写工具只在**实际加载**时加入；
- 注册表里的 `hitl_order` 决定声明工具顺序；MCP 工具保持注入顺序，追加在声明
  工具之后（与改造前 `setdefault` 的追加语义一致）；
- 决策集合仍来自注册表（`approve` / `reject`），不新增/删除决策；
- `HITL_WHEN_PREDICATES` 由 `hitl.py` 自己持有：谓词实现与绑定同处一模块，
  `agent.py` 只保留 `_HITL_WHEN_PREDICATES` 别名供既有自检/测试导入。

### 2.3 `agent.py` 接入

`EasyAgent._build_interrupt_config()` 缩为一次纯函数调用：

```python
return build_interrupt_config(
    getattr(self, "mcp_tools", None) or [],
    enable_hitl=bool(getattr(self, "enable_hitl", True)),
    sid=getattr(self, "sid", ""),
)
```

`agent.py` 不再 import `InterruptOnConfig`、`hitl_tool_specs`、`looks_like_mutating_tool`
或两个谓词函数；`_HITL_WHEN_PREDICATES` 保留为 `HITL_WHEN_PREDICATES` 的别名。

### 2.4 日志

- 未注入的注册表 HITL 工具：`logger.debug("[sid] HITL 工具 <name> 未实际注入，跳过 interrupt_on")`；
- 条件谓词在注册表/绑定之间漂移：沿用改造前的 `logger.error`，并退化为无条件审批；
- 正常路径不新增 INFO 日志，避免每会话建图刷屏。

## 3. 目录删除策略（文档化，行为不变）

目录删除**不进入 HITL 审批**，而是策略层直接拒绝，分两层：

1. `hitl._is_destructive_command()` 先匹配 `_DESTRUCTIVE_CMD_PATTERNS`，再调用
   `_is_directory_deletion()`；命中目录删除模式时返回 `False`，即 `execute` 的
   `when` 不成立，不产生审批中断；
2. `easy_agent/backends/shell.py:87-94` 在真正执行前用 `_is_directory_deletion()`
   再次判断并直接返回错误（`exit_code=1`），不进入沙箱执行。

这是**有意的策略拒绝**，不是 HITL 漏配：用户无法通过「批准」执行目录删除。
`execute` 工具描述与系统提示词已写明这一点（`hitl.EXECUTE_HITL_TOOL_DESCRIPTION`、
`services/prompt_loader.py`、`config/prompts/system.md`）。FW-12 只补充设计文档，
不改正则、不改 shell backend、不放宽/收紧拒绝条件。

已知边界：目录删除识别仍是正则，`rm -f -r`、`rm --recursive`、脚本内删除等
绕过属于 SEC-17 / T3-16 的独立问题，不在 FW-12 范围。

## 4. 行为等价矩阵

「旧配置」= 改造前 `_build_interrupt_config()`；「新配置」= FW-12。
标 `*` 的是旧配置里**实际未注入、因而静默无效**的条目。

| 场景 | 旧配置 | 新配置 | 说明 |
| --- | --- | --- | --- |
| 知识库关闭 + 未激活 expert-creator + file_context 开 + 无 MCP | `execute`、`save_expert*`、`create_scheduled_task`、`access_knowledge_original*`、`read_uploaded_file` | `execute`、`create_scheduled_task`、`read_uploaded_file` | 两个无效项移除；实际工具的决策/条件不变 |
| 知识库开 + expert-creator + file_context 开 | 上述 5 项全部 | 上述 5 项全部 | 完全一致 |
| 存在 MCP 写工具 `mysql_execute_sql` | 追加 `mysql_execute_sql` | 追加 `mysql_execute_sql` | 启发式与顺序不变 |
| `enable_hitl=False` | `None` | `None` | 不变 |
| 直接 `EasyAgent(mcp_tools=[])`（脚本/单测） | `execute`、`save_expert*`、`create_scheduled_task*`、`access_knowledge_original*` | `execute` | 仅保留框架真实提供的工具 |
| 某个应用工具工厂抛异常 | 配置里仍有该名字（静默无效） | 不出现 | 配置与真实能力一致 |

对**实际注入**的每个工具，`allowed_decisions`、`when` 谓词、声明顺序均与改造前
逐项相同；变化仅限「移除本来就不会生效的条目」以及因此带来的 D2 编译图缓存键
变化（键里 HITL 名单更短、更真实）。

## 5. 测试

| 文件 | 覆盖 |
| --- | --- |
| `tests/agent/test_hitl.py` | 纯函数：未注入的注册表 HITL 工具不出现；注入后出现；顺序；未知非写工具不加入；MCP 写工具加入；`enable_hitl=False` 返回 `None`；未知谓词退化；`EasyAgent` 接入随 `mcp_tools` 变化 |
| `tests/services/test_hitl_destructive.py` | 目录删除 `when=False`（策略层拒绝）；动态名单不包含未注入工具；原有破坏性命令/知识库 materialize/名字启发式用例保持 |
| `tests/services/test_hitl_state.py` / `test_hitl_resume.py` | 未改；回归确认 HITL 状态解析与恢复决策不受影响 |

命令与结果见报告。

## 6. 未覆盖 / 明确不做

1. **MCP 工具静态枚举**：数量/名字由用户 `mcp.json` 与连接器开关决定，只能在
   运行时按实际工具名判定；未登记名字仍走名字启发式。
2. **框架内置工具漂移**：`execute` 等「始终可用」来自注册表的 builtin 声明；
   若 DeepAgents 升级移除/改名 `execute`，需要同步注册表（测试会提示）。
3. **子代理工具**：`subagents.py` 的 `interrupt_on` 继承主图配置；子代理工具
   若不在主图工具集内，仍按框架「未知名字静默跳过」处理，不在本次枚举范围。
4. **`read_uploaded_file` 的历史启发式**：注册表声明 `is_hitl=False`，但名字含
   `upload`，改造前会被 `looks_like_mutating_tool` 加入审批。为满足「不改变审批
   行为语义」，FW-12 保持加入（测试锁定）；是否改为显式 `is_hitl` 是后续产品决策。
5. **`write_file` / `edit_file` 是否进 HITL**：仍是独立产品决策；它们是内置工具，
   改造前就不在 `EasyAgent.mcp_tools` 里，不会因本次改动被启发式加入。
6. **目录删除正则绕过**：SEC-17 / T3-16 的独立安全问题，本任务只文档化策略分层。
7. **前端审批 UI / SSE 事件**：未改；`approval_required` 契约不变。

## 7. 风险与回滚

| 风险 | 说明 / 缓解 |
| --- | --- |
| `mcp_tools` 命名误导 | 该参数实际是「会话工具全集」，文档与 `agent.py` docstring 已注明；不重命名以缩小 diff |
| 直接 `EasyAgent(...)` 的脚本/测试获得更少配置 | 这正是目标（未注入即不配置）；相关测试已覆盖空工具集 |
| D2 编译图缓存键变化 | HITL 名单参与 `build_graph_key`；更真实的名单会改变部分会话的键，属预期行为，缓存仍按工具/提示词等完整输入隔离 |
| 注册表声明与实际注入再次漂移 | 纯函数只信「实际集合」；新增 HITL 工具必须在注册表声明并在 `build_session_tools` 注入 |
| 内置工具集合与 `builtin_tool_names()` 不一致 | 注册表是框架 0.6.8 的显式快照；升级 deepagents 需同步并跑 `tests/agent/test_tool_registry.py` |
| `read_uploaded_file` 继续被启发式审批 | 行为等价优先；已在注册表说明与报告「未覆盖工具」中显式记录 |

回滚：恢复 `agent.py` 的 `_build_interrupt_config()` 旧实现与 `hitl.py` 旧 import
即可；无持久化、无迁移、无 API/DB 变更。
