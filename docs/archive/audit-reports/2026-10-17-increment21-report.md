# Increment 21 执行报告（ARCH-16：版本化迁移框架）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 20） |
| 依据 | 审计 ARCH-16（启动期 ALTER、无版本化 migration、不可审计/回滚） |
| 约束 | 兼容现有升级路径；首次启用只记 baseline，不重放历史 DDL |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 新增迁移框架 | `easy_agent/db/migrations.py`：`schema_migrations` 账本（version/description/applied_at）、`Migration` 描述、`record_baseline`、`apply_pending_migrations`；`BASELINE_VERSION=5` | 后续非幂等/数据型变更可按版本追加、只执行一次、可审计 |
| 集成 schema 初始化 | `SCHEMA_VERSION` 5→6；`schema_migrations` 加入 `EXPECTED_TABLES`；`_init_tables_full` 建账本表；快速路径/完整路径均 ensure 账本 → 记录 baseline → 应用 pending → 记录 schema 版本 | 首次启动 baseline=5；升级后 schema_version=6；后续迁移在启动时自动应用 |
| 测试 | 新增 `tests/db/test_migrations.py` 2 项（baseline、pending 只执行一次）；更新快速路径 SQL 上限（含账本探测） | 64 项 DB 测试通过 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| `tests/db` | 64 passed（含 schema 版本、快路径、自愈、迁移框架） |
| 全量 `pytest tests/` | **963 passed, 1 skipped, 0 failed, 0 error（241.4s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | 老库（version=5）→ 完整初始化建账本并升到 6；新库同样 baseline=5 + schema=6 |

## 3. 未完成/后续

1. 迁移失败目前会向上抛出并阻止启动（有测试覆盖成功路径）；后续可增加迁移前备份/回滚策略文档。
2. 现有幂等 DDL 仍保留在 `_init_tables_full`（建表/补列），只有非幂等变更走 migrations；后续可逐步把历史 ALTER 收编为版本化迁移。
3. ARCH-08、FW-06/09/10、多副本 Phase B、ARCH-03 Phase 3 未开始；Batch 0/1 安全项未执行。
