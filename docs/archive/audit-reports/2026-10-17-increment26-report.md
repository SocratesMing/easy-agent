# Increment 26 执行报告（FW-09 部分：官方 AsyncSqliteSaver 可选接入）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 25） |
| 依据 | 审计 FW-09（自研 pickle MemorySaver 私有属性依赖 + 写放大；单机建议官方 SqliteSaver） |
| 约束 | 默认行为不变；设置 `EASY_CHECKPOINT_SQLITE_PATH` 才启用官方 saver；Postgres 优先 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 可选依赖 | `pyproject.toml` 新增 `[project.optional-dependencies].sqlite-saver = ["langgraph-checkpoint-sqlite>=3.1.2"]`（uv 已安装 aiosqlite / sqlite-vec） | 单机部署可按需启用 |
| 官方 saver 接入 | `checkpoints.py` 新增 `open_sqlite_checkpointer/close_sqlite_checkpointer/sqlite_checkpointer_active()`；`get_session_checkpointer` 优先 Postgres，其次 SQLite 官方 saver，否则回退现有持久化 MemorySaver | 官方 saver 负责 checkpoint 持久化，不再每轮 pickle 全量写业务库 |
| 启停接线 | `app.lifespan`：`EASY_CHECKPOINT_POSTGRES_URI` 存在时用 Postgres；否则读 `EASY_CHECKPOINT_SQLITE_PATH` 打开 SQLite saver；两者都未设置且生产环境则告警；shutdown 关闭两种 saver | 运维通过环境变量选择后端 |
| 测试 | 新增 `tests/services/test_checkpoints_sqlite.py`：打开/激活/共享/关闭；与自研 checkpoint roundtrip 共 3 passed | 官方路径可回归 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 3 passed |
| 全量 `pytest tests/` | **976 passed, 1 skipped, 0 failed, 0 error（239.0s）** |
| `uv lock --check` | 通过（118 packages） |
| `uvx ruff check .` | 通过 |
| 兼容性 | 未设置新环境变量时完全走原路径；Postgres 配置优先 |

## 3. 未完成/后续（FW-09 完整项）

1. 自研 `PersistentMemorySaver` 仍在默认路径保留（自定义 pickle/裁剪逻辑）；后续可在 SQLite 成为默认后删除或仅作降级。
2. `release_session_checkpointer` 仍会删除业务库快照；官方 saver 路径下无需删除，可在后续清理。
3. 多副本 Phase B、ARCH-03 Phase 3 未开始；Batch 0/1 安全项未执行。
