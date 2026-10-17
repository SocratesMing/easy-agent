# 下一批改造执行报告（2026-10-18）

范围：架构计划 **阶段 0 门禁、A2 会话消息单写、B3 限流/JWT 契约第一批、C1 sessions 域去 Database 门面**。全部未 git commit。

## 1. 阶段 0：架构门禁

| 交付 | 内容 |
| --- | --- |
| `scripts/import_graph.py` | stdlib AST + Tarjan；`--check-scc` 与 `scripts/import_graph_whitelist.json` 精确比对；新环/白名单过期/空白名单有环 exit 1 |
| `scripts/check_double_write.py` | 冻结 `sessions.messages` 动态写入（仅允许固定空值）与 `Database` 业务方法（141 methods / 106 pure delegations） |
| `.github/workflows/ci.yml` | 新增 `architecture-gate` job |
| `tests/test_architecture_gate.py` | 10 项自测 |

**验证**：`python3 scripts/import_graph.py --check-scc` → 200 modules / 417 edges / 1 SCC(16) 与白名单一致；`python3 scripts/check_double_write.py` → OK。

## 2. A2：会话消息单一写路径

| 改动 | 说明 |
| --- | --- |
| `create_session` | 数据库行不再 `json.dumps(messages)`；`sessions.messages` 写固定空串 `""`，正文只进 `session_messages` |
| `_get_legacy_session_messages` | 标记 `[DEPRECATED]`；空值/`[]` 直接返回空，仅非空旧 JSON 回退并计数 |
| `update_session` | 不再回写 `messages` 列，只更新 title/updated_at/username |
| 测试 | 新增 `tests/db/test_session_single_write.py` 5 项；相关 96 passed |

**关键发现**：不能写 `'[]'`——启动期 `_repair_missing_message_rows()` 会按「JSON 条数 vs 行表行数」判不一致，重启时删除新会话消息行；空串在 SQLite/MySQL 均合法且被自愈跳过。若要改回 `'[]'`，必须先改 `db/schema.py` 自愈条件。

## 3. B3：限流 429 / 生产 JWT 契约（第一批）

| 改动 | 说明 |
| --- | --- |
| `RateLimitMiddleware` | 使用 `get_client_ip()`；超限返回 `JSONResponse(429, {"detail": ..., "code": "RATE_LIMITED"})`，不再 `raise HTTPException`；新增 `RateLimitCounter` 可插拔接口 + 单机实现 |
| 多副本 | 默认 auto：多 worker/副本用 DB 共享计数；单机用内存；DB 故障降级并告警 |
| JWT | `validate_production_jwt_secret()`：prod 未设 `EASY_JWT_SECRET` fail-fast；dev/test 仍可用 `data/.jwt_secret` |
| 测试 | `tests/middleware/test_rate_limit.py` 8 项、`tests/test_jwt_prod_contract.py` 8 项 |

**第二批已补**：DB 共享计数（`rate_limit_counters`，SCHEMA_VERSION=7）、窗口清理、auto/db/memory 选择、故障降级；Redis 实现/严格滑动窗口/Ingress 限流未做。

## 4. C1 第一批：sessions 域去 Database 门面

| 改动 | 说明 |
| --- | --- |
| 新增 `db/session_repository.py` | `SessionRepository(db)` 14 个薄委托方法，不写 SQL |
| `api/sessions.py` | 删除 `Database` import；10 路由改为 `Depends(get_session_repository)`；无 `db.*` 业务调用；`get_database` 只在 provider 内 |
| 兼容 | `_as_session_repository` 兼容直接调用路由函数的旧测试；`session_uploads` 暂通过 `repo.database` 边界传递 |
| 测试 | 新增 repository 5 项；sessions/chat/db 103 passed；架构门禁/OpenAPI/错误体 14 passed |

**未覆盖域**：`api/chat.py`、`api/files.py`、`api/auth.py`（users）、`api/scheduled_tasks.py`、`api/expert.py`、`api/settings.py`；`services/session_uploads.py` 仍接收 `Database`。

## 5. 集成验证

| 检查 | 结果 |
| --- | --- |
| 全量 `pytest tests/` | **1119 passed, 1 skipped, 0 failed, 0 error（243.7s）** |
| `scripts/import_graph.py --check-scc` | OK（200 modules / 417 edges / 1 SCC(16)） |
| `scripts/check_double_write.py` | OK |
| `uvx ruff check .` | All checks passed |
| `uv lock --check` | 通过 |

## 6. 风险 / 注意

1. `sessions.messages` 用空串而非 `'[]'`；若后续把 JSON 列改为可空/删列，需同步改门禁与自愈。
2. 限流仍是进程内计数，多副本阈值放大；B3 第二批需共享计数。
3. `SessionRepository` 仅 sessions 域，API 仍广泛使用 `Database` 门面；C1 整体未完成。
4. `.github/` 整体未跟踪，提交时需 `git add .github/`，否则架构门禁不会进入 CI。
5. 未执行 Batch 0/1 安全项；未改前端/Web Terminal/HITL 策略。
