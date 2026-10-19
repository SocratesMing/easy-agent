# DeepAgents 框架适配点清单

> 本仓库把 deepagents / langchain 固定在 **已验证窗口** 内：
> `deepagents>=0.7.28,<0.8`、`langchain>=1.4.6,<1.5`。
>
> 0.7 适配：callable backend 已移除 → `ContextBackend` 实例；
> `write_todos` 需显式 `TodoListMiddleware`；新增 `delete` 工具并纳入 HITL。
> 升级前先让 `tests/test_framework_contract.py` 失败并逐项复核本清单。

## 适配点（3 处）

| # | 位置 | 依赖的上游实现 | 失效后果 | 升级时需要做什么 |
| --- | --- | --- | --- | --- |
| 1 | `easy_agent/hitl.py` `install_execute_tool_description` | `FilesystemMiddleware.__init__(custom_tool_descriptions=...)` | execute 工具描述不再注入 HITL 说明；若 kwarg 被移除，建图期 TypeError | 改用官方 `HarnessProfile(tool_description_overrides=...)` |
| 2 | `easy_agent/summarization.py` `configured_summarization` | `deepagents.graph.create_summarization_middleware` 模块级工厂 | 摘要阈值静默回落到框架默认（0.85/0.10）或建图期 AttributeError | 检查工厂是否改名/搬移；优先改用官方公开参数（若已提供） |
| 3 | `easy_agent/checkpoints.py` `PersistentMemorySaver`（仅 MySQL 回退使用） | `MemorySaver.storage/writes/blobs` 私有属性 + pickle | 重启后 HITL 审批恢复失败（静默）；数据格式变化 | FW-09 起默认已走官方 saver；升级时复核私有属性/pickle 格式，并推动 MySQL 用户迁移到 PG/SQLite |

## 升级流程

1. 修改 `pyproject.toml` 版本窗口与 `tests/test_framework_contract.py` 中的常量。
2. 跑 `pytest tests/test_framework_contract.py tests/services/test_stream_processor.py -q`。
3. 跑全量 `pytest`，并重点回归：HITL 审批恢复、摘要触发、skills 注入、MCP 工具、`write_todos`。
4. 对照官方 changelog 复核：`write_todos` 是否仍默认开启、filesystem 工具签名、`delete` 工具、`recursion_limit`。
5. 更新本文件与审计报告中的版本窗口。

## 相关审计项

- FW-01（版本无上界/契约测试）
- FW-03（MCP 工具名冲突）
- FW-04/FW-05（私有 API 适配）
- FW-09（checkpointer 后端选择，默认官方 saver）


## 2026-10-17 更新

- **FW-05 护栏已落地**：`summarization.configured_summarization` 在
  `deepagents.graph.create_summarization_middleware` 缺失时显式 `logger.error`
  并降级为框架默认阈值（不再 AttributeError）。
- **FW-07 已落地**：`agent.build_subagent_specs()` 为每个 subagent 显式声明
  `skills=["/user-skills/"]` 与异常兜底 `LoggingMiddleware`。
- **FW-08 已落地**：call-limit 中间件 `exit_behavior="error"`；主/恢复流
  `stream_config["recursion_limit"] = max(2*max_steps, 50)`。
  - **2026-10-19 修正（DA-10）**：实际只有恢复流设了上限，且 `2*max_steps` 太小——当前装配
    一次 model→tools 循环要走 8 个 super-step，约 6 轮就会 `GraphRecursionError`。现统一由
    `easy_agent/agent/run_config.py:build_run_config()` 构造（聊天流 / 恢复流 / 定时任务），
    上限按编译图里 `model`、`tools` 与 `before_model`/`after_model` 钩子节点数计算，保证
    大于跑满 `max_steps` 所需步数。定时任务同时补上了漏传的 `context=SessionContext`。
  - 子代理图各自绑定 `recursion_limit=9999`，主流程的上限管不到它们：每个子代理 spec
    （含显式覆盖的 `general-purpose`）都带 `ModelCallLimitMiddleware` /
    `ToolCallLimitMiddleware`（`exit_behavior="end"`，超限以消息交回主代理）。
- **FW-04 已迁移 HarnessProfile**：0.7 的 `HarnessProfile.tool_description_overrides` 为官方注入点；
  现有 monkeypatch + `tests/test_framework_contract.py` 已能在升级时显式失败，
  待 0.7 升级立项时再迁移，避免在 beta API 上引入新风险。

- **FW-10 已落地**：`services/mcp.py:normalize_servers_mapping()` 中 URL-only 配置
  默认 transport 由旧 `sse` 改为 `streamable_http`；`streamable-http` / `http` 别名
  统一归一化为 `streamable_http`，显式 `sse` 保持兼容（`tests/services/test_mcp_transport.py`）。

- **FW-09 已落地（默认官方 saver）**：新增 `EASY_CHECKPOINT_BACKEND=auto|postgres|sqlite|legacy`
  （默认 `auto`），选择规则集中在 `checkpoints.resolve_checkpoint_backend()`：
  1. `EASY_CHECKPOINT_POSTGRES_URI` 存在 → 官方 `AsyncPostgresSaver`；
  2. 否则业务库为 SQLite（或显式 `EASY_CHECKPOINT_SQLITE_PATH`）→ 官方
     `AsyncSqliteSaver`，默认文件 `data/checkpoints.db`；
  3. 业务库为 MySQL 且无 PG URI → 官方没有 MySQL saver，回退自研
     `PersistentMemorySaver` 业务库快照并打 warning（功能可用，性能/官方性较差）；
  4. `EASY_CHECKPOINT_BACKEND=legacy` → 强制自研快照。
  官方 saver 打开失败会按 postgres → sqlite → legacy 逐级降级，不阻断启动；
  `shutdown_checkpointers()` 负责关闭 saver 并清空自研缓存（测试隔离亦依赖）。
  见 `docs/audit/2026-10-17-increment38-fw09-report.md`。

- **FW-11 部分落地**：`stores.SqlStore._search` 支持 BaseStore 常用 filter 操作符
  （`$eq/$ne/$gt/$gte/$lt/$lte/$in/$nin`），并改为“先过滤、后分页”的有界扫描；
  语义检索 `query` 仍未实现（见审计 FW-11）。
