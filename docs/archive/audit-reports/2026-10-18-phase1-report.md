# Phase 1 执行报告：DeepAgents 0.7 兼容

## 交付

- `deepagents 0.7.28` / `langchain 1.4.6` / `langgraph 1.2.16`，`uv.lock` 已更新。
- `easy_agent/backends/context.py`：`ContextBackend` 实例，替换 0.7 已移除的 callable backend，保留 D2 跨会话图复用。
- `agent.py`：`context_schema=SessionContext` + `ContextBackend`；显式 `TodoListMiddleware`。
- `tools/registry.py`：新增 `delete` builtin，并纳入 HITL；`write_todos` 描述更新。
- `tests/test_framework_contract.py`：版本窗口改为 0.7.28/1.4.6。
- `tests/agent/test_store_backend_spike.py`：官方 StoreBackend 往返 spike。
- `docs/design/2026-10-18-phase1-backend-migration.md`。
- `docs/framework-integration.md` 同步版本与 0.7 适配说明。

## 验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **1351 passed, 1 skipped, 0 failed（252.96s）** |
| StoreBackend spike | 1 passed |
| ruff | All checks passed |
| import graph | 0 SCC（219 modules / 498 edges） |
| 前端 | 未改 |

## 未完成（Phase 1 明确不做）

- `/memories/` 默认切 StoreBackend：需先解决 `memory_manager` 本地文件双源/迁移。
- `/workspace/` 对象存储：仍 LocalShell + RWX。
- 外部 Sandbox：仍 bwrap/Landlock；Phase 3 评估。
- `EASY_FILE_BACKEND`/`EASY_SANDBOX` 仍只是门禁占位，未接业务。

## 风险

- 0.7 `delete` 默认 HITL 改变审批行为；已有 HITL 测试与前端 approval 事件契约覆盖。
- `ContextBackend` 依赖 LangGraph runtime context；直接调用需 `resolve(ctx)`/fallback。
- 0.7 升级为一次性窗口，后续需持续跑框架契约测试。
