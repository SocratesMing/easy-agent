# Increment 19 执行报告（解除 db → personnel 反向依赖）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 18） |
| 依据 | 审计 ARCH-03（反向依赖）、Phase 1/2；设计文档模块重构 |
| 约束 | 行为不变；personnel 领域仍在应用启动路径注册 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 新增 user-fields 注册表 | `easy_agent/db/user_fields_registry.py`：用户扩展字段 save/read/ensure + 默认管理员策略 + 测试用 reset | db 层不再静态 import personnel |
| personnel 注册 | `personnel/user_fields.py`、`personnel/bootstrap.py` 在 import 时注册到 registry；`personnel/__init__.py` 确保加载 | 应用启动路径自动生效 |
| db 层切换 | `db/schema.py`（`ensure_user_fields`）、`db/repositories/users.py`（save/read/default admin）改走 registry | 消除 `db → personnel` 静态边 |
| 测试 | 新增 `tests/db/test_user_fields_registry.py` 3 项：hook 分发、缺失 hook 行为、**db 层无 personnel 静态 import**；既有 personnel/auth/db 108 项通过 | 契约与解耦均有回归保护 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 108 passed（personnel/auth/db）+ 新增 registry 3 项 |
| 全量 `pytest tests/` | **958 passed, 1 skipped, 0 failed, 0 error（244.8s）** |
| import 图 | `db → personnel` 静态边已移除；主 SCC 仍为 16 模块（该依赖不在此环内） |
| `uvx ruff check .` | 通过 |

## 3. 未完成/后续

1. 默认管理员/扩展字段策略现在依赖 personnel 注册；非应用启动路径（如独立脚本）需显式 import personnel 或调用 registry。
2. ARCH-16 版本化迁移、ARCH-08/09、FW-06/09/10、多副本 Phase B、ARCH-03 Phase 3 未开始。
3. Batch 0/1 安全项（4 P0 + 8 P1）仍未执行。
