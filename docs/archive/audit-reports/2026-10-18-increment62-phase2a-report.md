# Increment 62 / 多副本 Phase 2A：stream_events 持久化与跨 Pod 回放报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 分支 | `refactor/audit-improvements-2026-10-18`（基于 Phase0/1 提交） |
| 范围 | 仅后端；不改前端；SSE 15 事件 wire 契约不变 |
| 未执行 | commit / push / Batch 0/1 / Web Terminal / 全量测试 |
| 结论 | Phase 2A 目标达成；跨 Pod `/stream/live` 回放与 `/stream/status` 已改为 DB 事实源 |

## 1. 交付内容

### 1.1 schema v8

- `SCHEMA_VERSION` 7 → 8，`EXPECTED_TABLES` 增加 `stream_events`；
- `_init_tables_full` 调用新增的 `_create_stream_events_table`；
- SQLite：`INTEGER PRIMARY KEY AUTOINCREMENT` + `TEXT`；
- MySQL：`BIGINT PRIMARY KEY AUTO_INCREMENT` + `LONGTEXT`；
- 索引 `idx_stream_events_session (session_id, event_id)`；
- 未修改任何既有表结构。

### 1.2 repository / Database 委托

新增 `easy_agent/db/repositories/stream_events.py`：

- `append_stream_event(db, session_id, payload) -> int`（返回 `event_id`）；
- `list_stream_events(db, session_id, after_id=0, limit=500) -> list[dict]`；
- `delete_stream_events_before(db, cutoff_iso) -> int`（TTL 清理入口，本增量未调度）；
- 额外 `clear_stream_events(db, session_id) -> int`：新流开始后清理该会话上一轮
  事件（见 §3 轮次语义）。

`Database` 增加三个纯委托：`append_stream_event` / `list_stream_events` /
`delete_stream_events_before`，并同步更新 `scripts/quality/check_double_write.py` 的
`INFRA_DATABASE_METHODS` / `FROZEN_DATABASE_METHODS` 白名单。

### 1.3 写入器

新增 `easy_agent/services/stream_events.py`：

- `StreamEventWriter(session_id, db=None)`：asyncio.Queue（10000 条）+ 懒启动
  后台任务；
- 批量规则：最多 100 条或 100ms；
- `append()` 非阻塞；队列满丢弃普通事件（warning），终态
  （done/error/approval_required）挤掉最旧一条强制入队；
- DB 写入失败只记 error，不上抛；
- `aclose()` 幂等：sentinel + flush 剩余批次 + 超时取消；
- `flush_all_stream_events()` 注册表，供应用 shutdown 调用；
- 同步上下文（无运行中的事件循环）自动退化为 no-op，便于直接构造 hub 的测试。

### 1.4 hub 接入（`api/chat.py`）

- `_StreamHub(session_id, db=None)` 持有一个 writer；
- `broadcast(item)`：先更新内存 history + 通知同 Pod 订阅者，再
  `writer.append(item)`（包 try/except，持久化异常不影响 SSE）；
- `close()`：`broadcast(None)` 后 `request_close()` 入队 sentinel（不阻塞），
  终态最终落库；
- `_detached_event_stream(..., db=None)`：成功 `register_stream_task`（拿到跨副本
  流锁）后 `clear_stream_events` 清理上一轮；先 `hub.close()` 再释放流锁，
  缩小「owner 已失效但 done 尚未落库」的窗口；
- `chat_stream` / `chat_resume` 传入 `db=repo.database`。

### 1.5 /stream/live 与 /stream/status

- `/stream/live` 新增可选 query `last_event_id: int = 0`；
- 改为 DB-first：分页（`after_id`）逐条透传 `payload`，终态立即结束；
- 无新事件 150ms 轮询；`is_stream_active()`（`session_runtime`）判活跃，
  失效后再等 2 轮（约 300ms）+ 收尾复查；
- 无终态时补 `done(already_completed=True)`；
- 不再依赖本 Pod hub；hub 仅保留给 `/stream` 主链路；
- `/stream/status` 改为 DB `stream_owner` + `stream_until` 未过期即 active，
  返回字段不变。

### 1.6 shutdown

`app.py` 在 `await drain_stream_tasks()` 之后调用
`await flush_all_stream_events()`，避免尾部事件丢失。

## 2. 验证

执行命令与结果（WSL，仓库 `.venv`）：

```
pytest tests/api/test_chat.py tests/services/test_stream_*.py \
       tests/db/test_stream_events_repository.py \
       tests/services/test_stream_events_writer.py \
       tests/api/test_stream_live_cross_pod.py -q
# 114 passed

uvx ruff check .
# All checks passed!

python scripts/quality/import_graph.py --check-scc
# 221 modules, 506 internal edges, 0 SCC(s) / 0 modules
# [check-scc] OK — 实际 SCC 与白名单完全一致

python scripts/quality/check_double_write.py
# [OK] sessions.py / database.py
# Database: 145 methods, 110 pure delegations
```

未跑全量测试（按任务要求）。

## 3. 设计取舍与风险

1. **写库顺序**：内存广播优先。同 Pod 客户端零额外延迟；DB 故障只丢跨 Pod
   回放能力，不影响当前连接。
2. **轮次语义**：`stream_events` 按 `session_id` 存储，没有 `stream_id` 列。
   若不清理旧轮次，默认 `last_event_id=0` 的重连会先回放到上一轮 `done` 而
   立即结束。因此新流拿到流锁后清空该会话旧事件，等价于旧实现「每个新 hub
   历史为空」。代价：不保留跨轮次回放历史。
3. **清理时机**：放在 `register_stream_task` 之后，避免另一 Pod 正在生成时被
   误删；抢锁失败时不会清理。
4. **终态可靠性**：writer 的 sentinel + 批量 flush + shutdown `flush_all`
   三重兜底；队列满时终态有强制入队策略，但若进程被 SIGKILL，最后一批仍可能
   丢失，此时 `/stream/live` 会补 `done(already_completed=True)`。
5. **多副本边界**：现有 `is_session_streaming` 是进程内判断，跨 Pod 并发仍靠
   `session_runtime` 流锁（409/`SessionBusyError`）。Phase 2A 未新增跨 Pod
   取消/接管语义。
6. **读放大**：`/stream/live` 默认从头回放，长流分页读取；每页 500，轮询
   间隔 150ms。DB 索引覆盖 `(session_id, event_id)`。

## 4. 未做 / 后续

| 项 | 说明 |
| --- | --- |
| TTL 清理调度 | `delete_stream_events_before()` 已实现但未接入定时任务；表会随会话增长 |
| Redis 事件总线 | 未实现；仍是 DB-first 单通道 |
| `EASY_STREAM_BACKEND` | 本增量未接入该开关（始终持久化） |
| `stream_id` / run 表 | 未引入；当前依赖单会话单流假设 |
| 前端 `last_event_id` | 未改前端；wire 帧没有 event id，续播字段暂由外部调用方使用 |
| MySQL 实机测试 | 只有 DDL 单测（`BIGINT AUTO_INCREMENT` / `LONGTEXT`），CI 无 MySQL 实例 |
| 跨轮次审计回放 | 不支持（新流清理旧事件） |
| Batch 0/1、Web Terminal | 未触碰 |

## 5. 工作区状态说明

开始前 `git status` 已存在 52 个 **mode-only** 变更（`100755 → 100644`，
`git diff --stat` 为 0 行内容差异），以及未跟踪的 `.claude/`；这是
WSL/Windows 挂载下的既有环境噪声，本次未改动/未提交这些文件。本次新增/修改
文件见 §6。

## 6. 变更文件

新增：

- `easy_agent/db/repositories/stream_events.py`
- `easy_agent/services/stream_events.py`
- `tests/db/test_stream_events_repository.py`
- `tests/services/test_stream_events_writer.py`
- `tests/api/test_stream_live_cross_pod.py`
- `docs/design/2026-10-18-phase2-stream-events.md`
- `docs/audit/2026-10-18-increment62-phase2a-report.md`

修改：

- `easy_agent/db/schema.py`（schema v8 / 新表）
- `easy_agent/db/database.py`（3 个纯委托 + 建表委托）
- `easy_agent/db/repositories/stream_events.py`（见上）
- `easy_agent/services/stream_events.py`（见上）
- `easy_agent/services/session_runtime.py`（`is_stream_active`）
- `easy_agent/services/__init__.py`（导出 writer / flush）
- `easy_agent/api/chat.py`（hub 持久化、DB-first `/stream/live`、DB status）
- `easy_agent/api/expert.py`（仅传 `db=repo.database`，两处）
- `easy_agent/app.py`（shutdown flush）
- `scripts/quality/check_double_write.py`（白名单）
