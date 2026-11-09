# Increment 5 执行报告（ARCH-03 收尾回边 + 文档治理 + 测试门禁）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 4） |
| 依据 | `docs/design/2026-10-17-module-refactor-plan.md` Phase 1；审计 ARCH-03/11/13/09 |
| 约束 | 不改外部 API/SSE 契约；文档与代码同步；测试全部通过 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| ARCH-03：`db.database ↔ knowledges.core.schema` 环 | 新增 `easy_agent/db/schema_registry.py`（可插拔 schema 初始化注册表）；`Database.init_tables()` 改为执行注册表；`knowledges/core/schema.py` 仅在 `TYPE_CHECKING` 下引用 `Database` 并在 import 时注册建表函数 | **SCC 从 2 个降为 1 个**；环内模块 18→16 |
| ARCH-03：`db → schema` 删除静态 import | `Database` 不再 import 领域模块，依赖方向变为 `db → schema_registry ← knowledges` | 设计文档 Phase 1 第 4 项完成 |
| ARCH-11：严格 pytest marker | `pyproject.toml` 增加 `addopts = "--strict-markers"` | 未知 marker（拼写错误）不再被静默忽略 |
| ARCH-11：DB 测试注册 schema | `tests/db/test_knowledge_schema_init.py`、`test_schema_version.py` 显式 import 领域 schema 模块 | 直接 `Database.init_tables()` 的测试与生产路径一致 |
| ARCH-13/09：架构文档 | 新增 `docs/architecture-current.md`（准确模块地图/数据流/命令/已知债）；`CLAUDE.md`、`AGENTS.md` 顶部加过时声明并指向新文档 | 修复主要文档漂移（17 skills、Vue CLI、X-Username、无 Docker/CI 等） |
| ARCH-05 文档对齐 | `docs/requirements-agent.md` FR-1.3 事件清单补齐 `tool_call_delta`、`knowledge_evidence` 并指向 `docs/sse-events.md` | 需求文档与事件注册表一致 |

## 2. 量化结果（AST import 图）

| 指标 | Round 3 末 | 本轮后 |
| --- | --- | --- |
| 循环依赖 SCC | 2 个（16 + 2 模块 = 18） | **1 个（16 模块）** |
| 环内模块数 | 18 | **16**；相对审计基线（39+2=41）累计 **-61%** |
| 已消除的环 | — | `db.database ↔ knowledges.core.schema` |
| 剩余大环 | `api.chat ↔ knowledges.* ↔ services`、`tools.scheduled_task → services.scheduler` | 需 Phase 2/3 或依赖注入（设计文档） |

## 3. 验证

| 检查 | 结果 |
| --- | --- |
| DB / schema 测试 | `tests/db` 50 passed（含 knowledge schema 初始化、版本快速路径） |
| 全量 `pytest tests/` | **936 passed, 1 skipped, 0 failed, 0 error（237.9s，`--strict-markers` 生效）** |
| `uvx ruff check .` | 通过（E9/F63/F7/F82） |
| 文档一致性 | `docs/sse-events.md` ←→ `services/sse.py`←→requirements 已同步；架构现状文档已建立 |

## 4. 未完成/后续

1. **ARCH-03 剩余**：`knowledges.api → api.chat`（知识面板流式下沉）、`tools.scheduled_task → services.scheduler`（依赖注入）；Phase 2（Database 拆 repository）与 Phase 3（streaming 拆分）未开始。
2. **ARCH-05 前端**：Vue2/Vue3 两套 SSE 消费端尚未收敛到统一 `api/sse.js`，后端生产端尚未全部引用注册表常量。
3. **ARCH-08/09/15/16**：API response_model、配置收口、多实例状态外置、版本化迁移均未开始。
4. **Batch 0/1 安全项**仍未执行（4 P0 + 8 P1）。
