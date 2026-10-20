# Increment 36 执行报告（ARCH-11 部分：SQLite 运行约束回归）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 34） |
| 依据 | 审计 ARCH-11（测试覆盖）/ PERF-07（WAL、busy_timeout、synchronous）；用户约束“单机 SQLite 启用 WAL/busy_timeout” |
| 约束 | 仅新增测试；不改运行代码 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 新增约束测试 | `tests/db/test_sqlite_pragmas.py`：`journal_mode=wal`、`busy_timeout=30000`、`synchronous=1` | 把用户约束固化为回归测试，防止后续改动误删 pragma |
| 验证 | 单独测试通过；全量回归通过 | 约束可验收 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 目标测试 | 1 passed |
| 全量 `pytest tests/` | **985 passed, 1 skipped, 0 failed, 0 error（241.0s）** |
| `uvx ruff check .` | 通过 |
| 覆盖对应 | PERF-07（SQLite WAL/busy_timeout）、ARCH-11（关键路径测试） |

## 3. 后续

1. ARCH-08 剩余：knowledge 路由批次、expert 6 个裸挂根路由；或 ARCH-19 异常/日志治理。
2. 多副本 Phase B、streaming 深度拆分、HITL 策略暂缓（用户指示）；Batch 0/1 安全项未执行。
