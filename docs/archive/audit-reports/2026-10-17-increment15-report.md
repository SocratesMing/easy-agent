# Increment 15 执行报告（ARCH-03 Phase 2：users / MCP API Key repository）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 14） |
| 依据 | `docs/design/2026-10-17-module-refactor-plan.md` Phase 2 |
| 约束 | 保持 `Database.*` 调用兼容（含 `_row_to_user` 静态包装）；SQL/行为不变 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 抽取 users repository | 新增 `easy_agent/db/repositories/users.py`（~380 行）：注册/查询/更新、`token_version`、活跃时间、默认用户、免密用户、`list/count`；personnel 字段扩展保持函数内惰性 import（相对路径提升为 `...personnel`） | Phase 2 最大一块领域 SQL 移出 Database |
| 抽取 MCP API Key repository | 新增 `easy_agent/db/repositories/mcp_api_keys.py`：issue/revoke/list | 与用户域解耦 |
| Database 委托 | users 21 个方法 + MCP 3 个方法改为委托；`_row_to_user` 保留静态包装供测试/调用方使用 | 兼容既有调用方 |
| 验证 | `tests/db`、auth/passwordless/multi-worker 共 **102 passed** | 行为不变 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 102 passed（含 personnel 字段回退、token_version、跨 worker 活跃时间） |
| 全量 `pytest tests/` | **955 passed, 1 skipped, 0 failed, 0 error（235.8s）** |
| `uvx ruff check .` | 通过 |
| 规模 | `database.py` 2342 → **2104 行**（本轮 -238，累计 **-600**） |

## 3. 未完成/后续（Phase 2 继续）

1. 下一批：`sessions/messages`（含 `session_messages.py` 重复收口）、`files`、`experts`。
2. 之后处理 `db → personnel` 的剩余反向依赖（`ensure_default_admin` 等），并最终把 `Database` 收敛到连接/事务/迁移。
3. ARCH-08/09/16、FW-06/09/10、多副本 Phase B、ARCH-03 Phase 3 未开始；Batch 0/1 安全项未执行。
