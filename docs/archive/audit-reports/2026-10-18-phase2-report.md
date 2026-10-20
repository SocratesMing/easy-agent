# 多副本 Phase 2 执行报告（2A + 2B）

## 2A：stream_events 持久化 + 跨 Pod SSE

| 项 | 实现 |
| --- | --- |
| schema | `SCHEMA_VERSION=8`；`stream_events(event_id, session_id, payload, created_at)` + `(session_id,event_id)` 索引 |
| repo | `append/list/delete_before/clear`；`Database` 纯委托 |
| writer | `StreamEventWriter`：非阻塞队列、≤100 条/100ms 批量、DB 线程池、终态不丢、失败不打断 SSE |
| hub | 内存广播后持久化；新流持锁后清旧轮次；先关 hub 再释放流锁 |
| `/stream/live` | DB-first 分页回放，可选 `last_event_id`，150ms 轮询，按 DB `stream_owner` 判活跃，终态即停；不再依赖本 Pod hub |
| `/stream/status` | DB owner 未过期即 active，跨 Pod 一致 |
| shutdown | `flush_all_stream_events()` |

## 2B：run 租约与中断恢复

| 项 | 实现 |
| --- | --- |
| schema | `SCHEMA_VERSION=9`；`agent_runs(run_id, session_id, status, request_json, lease_owner, lease_until, error, ...)` |
| repo | `start_run`（幂等插入）/`renew_run`/`finish_run`（running 条件、原子）/`list_stale_running`/`mark_interrupted`（竞争裁决） |
| service | `run_registry`：聊天/恢复登记 run；流锁心跳同频续租；正常 completed、取消 cancelled、异常 failed |
| 恢复 | 启动时 `recover_stale_runs`：租约过期的 running → interrupted，并写 error 事件；新流已占用则跳过事件，避免污染 |
| 未做 | **自动重新执行 stale run 属 Phase 2C**；只保证前端不再停在“执行中” |

## 验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **1394 passed / 1 skipped / 0 failed（259.5s）** |
| C1/双写/架构 | 0 SCC；double-write OK；C1 边界计数更新并登记 |
| ruff | All checks passed |
| 定向 | 2A 114 passed；2B 133 passed；run/stream 新增 30+ |

## 遗留 / 风险

1. **Phase 2C**：stale run 自动执行器/队列 worker 未做；当前恢复后由用户重发或从审批继续。
2. **跨 Pod HITL**：依赖共享 Postgres checkpointer + resume 重建 agent；流回放已跨 Pod，但尚未做双进程真实 HITL 集成测试。
3. `stream_events` 无 TTL 清理调度（`delete_before` 已提供）；Redis/`EASY_STREAM_BACKEND=redis` 未做。
4. `agent_runs.request_json` 含消息正文（按任务要求），无 TTL/归档；MySQL 仅 DDL 单测。
5. 严格门禁仍会失败（run_queue=none、file/sandbox=local），K8s 保持 `EASY_MULTI_REPLICA_STRICT=0`。

## 文档

- `docs/design/2026-10-18-phase2-stream-events.md`
- `docs/design/2026-10-18-phase2-run-lease.md`
- `docs/audit/2026-10-18-increment62-phase2a-report.md`
- `docs/audit/2026-10-18-increment63-phase2b-report.md`
