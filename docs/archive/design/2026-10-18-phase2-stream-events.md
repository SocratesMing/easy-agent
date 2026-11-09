# 多副本 Phase 2A：stream_events 持久化与跨 Pod /stream/live 回放

| 项 | 内容 |
| --- | --- |
| 状态 | Implemented（Phase 2A） |
| 日期 | 2026-10-18 |
| 范围 | 仅后端；SSE 15 事件 wire 契约不变；前端零改动 |
| 上游 | `docs/design/2026-10-18-multi-replica-target.md`（SSE DB-first 决策） |
| 关联 | schema v8、`stream_events` 表、`StreamEventWriter`、`/stream/live`、`/stream/status` |

## 1. 问题

改造前 `easy_agent/api/chat.py` 的 `_StreamHub` / `_session_stream_hubs` 完全在进程内：

- `/stream/live` 只订阅本 Pod 的 hub；客户端重连落到另一 Pod 时，hub 不存在，
  立即返回 `done(already_completed=True)`；
- 即使落到原 Pod，进程重启 / Pod 漂移也会丢事件；
- `/stream/status` 依据本 Pod hub 判断，跨 Pod 会误报「未在生成」。

Phase 2A 把每个 SSE frame 原样持久化到共享业务库，任一 Pod 都能按 `event_id`
顺序回放，同时保留同 Pod 内存 hub 的低延迟广播。

## 2. 数据模型（schema v8）

`SCHEMA_VERSION` 7 → 8，新增 `stream_events`（只加表，不改既有表）：

| 列 | SQLite | MySQL | 说明 |
| --- | --- | --- | --- |
| `event_id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT PRIMARY KEY AUTO_INCREMENT` | 库级全局顺序，回放游标 |
| `session_id` | `VARCHAR(255) NOT NULL` | 同左 | 会话隔离 |
| `payload` | `TEXT NOT NULL` | `LONGTEXT NOT NULL` | 完整 SSE frame（`data: {...}\n\n`） |
| `created_at` | `VARCHAR(50) NOT NULL` | 同左 | ISO 时间，供 TTL 清理 |

索引：`idx_stream_events_session (session_id, event_id)`，覆盖
`WHERE session_id=? AND event_id>? ORDER BY event_id` 的回放查询。

`stream_events` 已加入 `EXPECTED_TABLES`；表缺失时 schema 快速路径会自动回退到
完整初始化并补建。

## 3. 写入链路

```
chat_stream_generator
  → _StreamHub.broadcast(frame)
      1. 追加内存 history / 推送给同 Pod 订阅者（保持原有低延迟）
      2. writer.append(frame)  ← 仅非阻塞入队
  → StreamEventWriter（每流一个，懒启动 asyncio 任务）
      批量：最多 100 条 或 100ms（先到先触发）
  → db/repositories/stream_events.append_stream_event()
  → stream_events 表
```

关键策略：

1. **非阻塞**：`append` 只做 `put_nowait`，满队列时普通事件丢弃并 warning，
   绝不 `await put` 阻塞事件循环；`done` / `error` / `approval_required`
   终态会挤掉最旧一条后强制入队，保证收尾帧最终落库。
2. **批量写**：后台任务单批 ≤100 条，或等待 100ms 后 flush；DB 写在线程池执行
   （`asyncio.to_thread`），SQLite/MySQL 同步驱动不会卡住 SSE。
3. **故障隔离**：DB 连接/写入失败只记 `error` 日志，SSE 主链路继续；同一 Pod
   的实时推送不依赖持久化成功。
4. **关闭**：`hub.close()` 先广播 EOF，再 `writer.request_close()` 入队 sentinel
   （不阻塞）；后台任务 flush 完剩余批次后退出。应用 shutdown 在
   `drain_stream_tasks()` 之后调用 `flush_all_stream_events()` 兜底。
5. **写库顺序**：`_drive` 在成功拿到跨副本流锁（`register_stream_task`）之后才
   清理该会话上一轮事件；抢锁失败（其它 Pod 正在生成）不会误删对方数据。

## 4. /stream/live：DB-first 回放

```
GET /agent/chat/stream/live?session_id=...&last_event_id=0
```

- 新增可选 query `last_event_id: int = 0`（严格 `after_id` 语义，默认 0 = 从头），
  当前前端不传，行为与旧版「重连回放完整历史」一致；wire 帧格式不变。
- 循环：按 `(session_id, event_id)` 分页（每页 500）逐条 `yield payload`；
  命中 `is_terminal_event`（`done` / `error` / `approval_required`）立即结束。
- DB 暂无新事件时 150ms 轮询；用 `session_runtime.stream_owner/stream_until`
  判断是否仍活跃。owner 失效后再等 2 轮（约 300ms），并做一次收尾复查，
  覆盖「writer 最后一批尚未落库」的窗口。
- 流已结束但事件表没有终态（崩溃/持久化失败）时补一帧
  `done(already_completed=True)`，避免前端永久停在「执行中」。
- 不再读取 `_session_stream_hubs`；本 Pod hub 只服务 `/stream` 主链路和
  同 Pod 订阅者，正确性以 DB 为准。

## 5. /stream/status：DB 视角

`active` 改为 `is_stream_active(db, session_id)`：`stream_owner` 非空且
`stream_until` 未过期。返回字段保持 `{session_id, active}`，跨 Pod 一致。
Pod 崩溃遗留的锁会在 TTL（30 分钟）到点后自动变为不活跃。

## 6. 流轮次语义

`stream_events` 是「当前/最近一次流」的回放缓冲，不是跨轮次审计日志：

- 新流在拿到流锁后清空该 `session_id` 的旧事件（`clear_stream_events`）；
- 否则默认 `last_event_id=0` 的重连会先回放到上一轮的 `done` 而立即结束，
  与旧实现「每个新 hub 历史为空」的语义不一致；
- 清理发生在持锁之后，多副本并发建流时不会删除另一个 Pod 正在写的事件。

## 7. 未做 / 限制（诚实清单）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| TTL 清理任务 | 未做 | `delete_stream_events_before()` 已提供但未调度；表会随会话增长，需要后续定时清理 |
| Redis 事件总线 | 未做 | 仍为纯 DB-first；`EASY_STREAM_BACKEND` 未接入本增量 |
| `stream_id` / run 维度 | 未做 | 仍以 `session_id` 为键，假设单会话同一时刻只有一条流（现有 409/流锁保证） |
| 跨轮次历史回放 | 不支持 | 新流会清理旧事件；审计级历史需独立表/开关 |
| 客户端续播 | 后端已支持 | 前端未发送 `last_event_id`（wire 无 event id），本次不改前端 |
| MySQL 实机验证 | 未跑 | 双栈 DDL/SQL 由单元测试覆盖，CI 环境无 MySQL 实例 |
| `/stream` 主链路 | 不变 | 除 hub 持久化与关闭顺序外，事件序列/字段/节奏保持原样 |
| Batch 0/1 安全项、Web Terminal | 未触碰 | 按任务约束 |

## 8. 测试

- `tests/db/test_stream_events_repository.py`：append 顺序、`after_id`、分页、
  session 隔离、清理（按时间 / 按会话）、MySQL DDL 口径。
- `tests/services/test_stream_events_writer.py`：关闭时全量 flush、批量上限、
  终态在满队列下不丢、DB 故障不上抛。
- `tests/api/test_stream_live_cross_pod.py`：无本地 hub 时 DB 回放、`last_event_id`
  跳过、无事件补 `already_completed`、status 依据 DB、新流替换旧轮次事件。
- 现有 `tests/api/test_chat.py` 与 `tests/services/test_stream_*.py` 回归通过。
