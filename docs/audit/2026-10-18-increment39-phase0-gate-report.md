# Increment 39 执行报告（阶段 0 架构门禁：import SCC + 双写冻结）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-18（Asia/Shanghai） |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 0.1 / 0.2 |
| 约束 | 只新增 `scripts/`、CI、本测试与文档；未改 `easy_agent/` 业务源码、`tests/conftest.py` 或其他测试；未 commit |
| 结论 | import 图 SCC 门禁**通过**；双写冻结检查**通过**（执行期间 A2 的 `sessions.py` 改动已落进工作区） |

## 1. 改动清单

| 文件 | 类型 | 作用 |
| --- | --- | --- |
| `scripts/import_graph.py` | 新增 | 纯 stdlib（`ast`）扫描 `easy_agent` 内部 import，Tarjan 计算 SCC；`--check-scc` 强制白名单，`--json` / `--json-out` 输出模块、边、SCC |
| `scripts/import_graph_whitelist.json` | 新增 | 只允许当前 1 个 SCC（16 模块），逐项写明原因与目标阶段；空白名单等价于“SCC 必须为 0” |
| `scripts/check_double_write.py` | 新增 | `sessions.py` 的 `sessions.messages` 动态写入检查 + `Database` 方法冻结（显式名单 + 纯委托校验）；输出 `文件:行号`，违规 exit 1 |
| `.github/workflows/ci.yml` | 修改 | 新增 `architecture-gate` job，依次运行两个门禁脚本（Python 3.12，无第三方依赖） |
| `tests/test_architecture_gate.py` | 新增 | 10 个用例：import 解析/SCC/白名单/CLI，双写坏样本/好样本/真实仓库 |
| `docs/audit/2026-10-18-increment39-phase0-gate-report.md` | 新增 | 本报告 |

> 仓库当前把 `.github/` 整体置于未跟踪状态（`git status` 显示 `?? .github/`），提交时需 `git add .github/`，否则 CI 门禁不会进入版本库。

## 2. Import 图 SCC 门禁（0.1）

### 2.1 实现口径

- 只扫描 `--root/--package` 下的 `.py`（默认 `easy_agent/`），跳过 `__pycache__`、`.venv`、`node_modules` 等。
- 绝对 import、相对 import（`.` / `..` / `...`）、`from . import submodule`、`import a.b.c` 均按“最长已存在模块前缀”解析；`__init__.py` 正确映射到包名。
- SCC 使用迭代版 Tarjan，避免递归深度问题；只把 **模块数 > 1** 的分量视为环（单模块自环不影响架构边界）。
- 白名单比较是**集合精确匹配**：
  - 出现新环 → 失败；
  - 已有环的模块集合增减 → 失败；
  - 白名单里记录了但实际已断开的环 → 失败（提示删除，“只减不增”）；
  - 白名单为空且存在环 → 失败（要求 SCC=0）。
- 白名单每项必须带非空 `reason`，同一模块不允许出现在两个白名单项里。

### 2.2 当前结果

```text
$ python3 scripts/import_graph.py --check-scc
import graph: 199 modules, 412 internal edges, 1 SCC(s) / 16 modules
  SCC-1 [16]: api.chat, api.sessions, knowledge_impl, knowledges.api,
             knowledges.chat.agent_extension, knowledges.chat.chat_bridge,
             knowledges.chat.original_tool, knowledges.chat.streaming,
             knowledges.runtime.lifecycle, knowledges.runtime.worker,
             knowledges.service, services, services.agent_manager,
             services.scheduler, services.streaming, tools.scheduled_task
[check-scc] OK — 实际 SCC 与白名单完全一致
```

说明：`_analysis/t4_import_graph4.json` 的旧快照记录 409 条内部边；本次对当前代码重算为 412 条，多出的 3 条是 `app.py` / `middleware/rate_limit.py` 的函数内延迟 import（`app -> utils.auth`、`rate_limit -> utils.client_ip`、`rate_limit -> utils.deployment`）。模块集合与 SCC 数量与旧快照一致，环没有变化。

### 2.3 白名单内容

`scripts/import_graph_whitelist.json` 当前只有一项：

| 字段 | 内容 |
| --- | --- |
| `id` | `knowledge-chat-scheduler-core` |
| `reason` | 知识库流式/聊天桥接（`knowledges.chat.*`）、`api.chat`、`services.streaming`、`services.scheduler`、`tools.scheduled_task` 相互 import 形成 16 模块强连通分量；A1 将 `knowledges.api → api.chat` 反向依赖下沉到 `knowledges.chat`，并把 `scheduled_task` 改为端口依赖，断环后删除本项 |
| `target_phase` | `A1` |
| `modules` | `easy_agent.api.chat`、`easy_agent.api.sessions`、`easy_agent.knowledge_impl`、`easy_agent.knowledges.api`、`easy_agent.knowledges.chat.agent_extension`、`easy_agent.knowledges.chat.chat_bridge`、`easy_agent.knowledges.chat.original_tool`、`easy_agent.knowledges.chat.streaming`、`easy_agent.knowledges.runtime.lifecycle`、`easy_agent.knowledges.runtime.worker`、`easy_agent.knowledges.service`、`easy_agent.services`、`easy_agent.services.agent_manager`、`easy_agent.services.scheduler`、`easy_agent.services.streaming`、`easy_agent.tools.scheduled_task` |

## 3. 双写冻结（0.2）

### 3.1 `sessions.messages`（`easy_agent/db/repositories/sessions.py`）

禁止规则（`DW101` / `DW102`）：

- `INSERT/REPLACE INTO sessions` 的列清单含 `messages` 时，值必须是**固定空值**（`'[]'`、`''`、空 list literal、或 `json.dumps([])`）；动态值（`session_data.messages`、`json.dumps(session_data.messages)` 等）失败。
- `UPDATE sessions SET messages=...` 同样只允许固定空值。
- 任何把 `messages`（属性/变量/字典键/`get("messages")`）传给 `json.dumps` 的调用失败。
- 允许 legacy **只读**：`SELECT messages FROM sessions`、`json.loads`；允许写 `session_messages` 行表。
- 未显式列名、列/值数量不匹配、多行 INSERT 无法静态证明安全时，分别以 `DW103` / `DW104` / `DW105` 失败。

为什么允许“固定空值”：当前 `sessions` 表 `messages` 列为 `NOT NULL`（`db/schema.py`），A2 的终态是“空串或固定 `[]`”，不是删除列。A2 已按此落地：`create_session` 的 `INSERT` 仍带 `messages` 列，但参数是固定 `""`（第 41 行附近），并注明该空串可避免启动期 `_repair_missing_message_rows` 把新会话误判为 JSON/行表不一致；`update_session`（第 228 行）已不再触碰 `messages`。若后续把 schema 改为可空并彻底移除列，可把 `_is_fixed_empty_expr` 收紧为“任何 messages 列都失败”。

### 3.2 `Database` 业务方法（`easy_agent/db/database.py`）

- 显式冻结 `FROZEN_DATABASE_METHODS`（当前 141 个方法名）。新增未在名单中的方法 → `DW201`。
- `INFRA_DATABASE_METHODS`（35 个）允许直接 SQL：连接/事务、`_execute`、`ping`、DDL/迁移包装、`init_tables` 等。
- 其余冻结方法必须是**纯委托**（函数体只有一条 `return repo.func(self, ...)` 或 `repo.func(self, ...)`，repo 来自 `from .repositories import ...` / `from . import schema`）；否则 → `DW202`。这同时防止把已有委托改成内联 SQL。
- 当前统计：**141 methods / 106 pure delegations**，检查通过。

### 3.3 当前结果（A2 已落地）

```text
$ python3 scripts/check_double_write.py
[OK] easy_agent/db/repositories/sessions.py
[OK] easy_agent/db/database.py
[double-write] OK — legacy sessions.messages 写入与 Database 业务方法均已冻结
  Database: 141 methods, 106 pure delegations
```

任务开始时工作区的 `sessions.py` 仍含 `json.dumps(session_data.messages)`（00:49 读取）；按该版本本检查会报 `create_session` / `update_session` 的 `DW101`/`DW102`。A2 于 00:51 左右完成 `sessions.py` 修改后转绿；脚本首次实际运行（00:52）即通过，未使用任何豁免或基线绕过。

## 4. CI 门禁

`.github/workflows/ci.yml` 新增 `architecture-gate` job：

```yaml
  architecture-gate:
    name: Architecture gate (import SCC + double-write freeze)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Import graph SCC (whitelist = existing cycles only)
        run: python scripts/import_graph.py --check-scc
      - name: Double-write freeze (sessions.messages / Database facade)
        run: python scripts/check_double_write.py
```

两个脚本只依赖标准库，不需要 `uv sync`，因此该 job 不增加依赖安装时间；任一脚本非 0 退出即失败。

## 5. 测试与验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新增门禁用例 | `.venv/bin/python -m pytest tests/test_architecture_gate.py -q -p no:cacheprovider` | **10 passed**（1 个既有 Starlette 弃用 warning） |
| Ruff | `uvx ruff check .` | **All checks passed!** |
| SCC 门禁 | `python3 scripts/import_graph.py --check-scc` | exit 0，1 SCC / 16 模块与白名单一致 |
| 双写门禁 | `python3 scripts/check_double_write.py` | exit 0，两个文件 OK |
| JSON 排查 | `python3 scripts/import_graph.py --json` | 输出 199 模块 / 412 边 / 1 SCC（含每个模块邻接表与边行号） |

> 本机 WSL 只有 `python3`；CI 的 `architecture-gate` job 使用 `actions/setup-python` 提供的 `python`，两者跑的是同一份 stdlib 脚本。

用例覆盖：

- import 解析：相对 `from . import b`、`from ..util import x`、绝对 `from pkg.a import thing`、子包 `__init__`。
- SCC：2 模块环识别；空/匹配/模块集合变化/过期白名单；白名单缺 reason、模块重复的配置错误。
- CLI：失败 exit 1、通过 exit 0、`--json` 结构。
- 真实仓库：import SCC 与 `import_graph_whitelist.json` 完全一致。
- 双写：坏样本同时命中 `DW101`/`DW102`；好样本（固定空串、legacy SELECT/`json.loads`、`json.dumps(todos)`）零违规；`Database` 未知方法 `DW201`、委托改内联 SQL `DW202`；坏样本 CLI exit 1 且输出 `文件:行号`；真实仓库双写检查全绿。

测试全部使用 `tmp_path` 注入文件，不访问网络、不写业务数据库，也不依赖 `tests/conftest.py` 的 app/client fixture。

## 6. 风险与后续

1. **白名单精确匹配的维护成本**：A1 每断开一条环边、或移动一个模块，都必须同步更新白名单；CI 失败是预期信号，不是误报。断环完成后应把 `sccs` 置空（SCC=0）。
2. **固定空值豁免**：`INSERT ... messages=''` / `'[]'` 被允许，因为 schema 是 `NOT NULL` 且 A2 明确采用该终态。若组织要求“列出现即失败”，需同时改 schema/迁移，并收紧 `_is_fixed_empty_expr`。
3. **Database 冻结名单是“当前债”的显式快照**：名单内仍有 106 个业务委托（C1 要迁走）。新增方法会失败；删除方法不会失败（允许收敛）。C1 若加 `__getattr__` 兼容层，需要评审后显式加入名单。
4. **A2 并发**：本次执行期间 A2 正在改 `sessions.py`，门禁结果以最终工作区为准；若 A2 后续再改回 `json.dumps(messages)`，CI 会立即变红。
5. **旧分析产物**：`_analysis/t4_import_graph*.py` 保留未动；新脚本不读取它们。旧 JSON 的 409 边是时点快照，与当前 412 边不矛盾。
6. **未提交**：按约束未 commit；`.github/` 目前整体未被 git 跟踪，提交时需一并 add。

## 7. 回滚

- 删除 `.github/workflows/ci.yml` 中的 `architecture-gate` job，或
- 删除 `scripts/import_graph.py`、`scripts/import_graph_whitelist.json`、`scripts/check_double_write.py`、`tests/test_architecture_gate.py`。
- 脚本均为只读检查，不影响运行时；回滚不需要改业务源码。
