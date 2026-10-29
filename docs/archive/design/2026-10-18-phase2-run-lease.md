# 多副本 Phase 2B：agent_runs 租约、聊天生命周期标记与启动 stale 恢复

| 项 | 内容 |
| --- | --- |
| 状态 | Implemented（Phase 2B） |
| 日期 | 2026-10-18 |
| 范围 | 仅后端；不改前端；API/SSE 15 事件 wire 契约不变 |
| 上游 | `docs/design/2026-10-18-multi-replica-target.md`、`docs/design/2026-10-18-phase2-stream-events.md` |
| 关联 | schema v9、`agent_runs` 表、`services/run_registry.py`、`_detached_event_stream`、启动恢复 |
| 明确不做 | **自动重新执行 stale run**（执行器属于 Phase 2C） |

## 1. 问题

Phase 2A 把 SSE frame 持久化到 `stream_events`，跨 Pod 回放已经可用，但「一次
run 本身」仍然只存在于进程内：

- 无法回答「哪个 run 正在哪个 Pod 上跑」——`session_runtime` 只有会话级流锁，
  没有 run 维度记录，也没有最终状态；
- Pod 崩溃/驱逐后，`session_runtime.stream_until` 到点只会让 `/stream/status`
  变成不活跃，数据库里没有任何「这次运行被中断」的痕迹；
- 前端在流断开后可能一直显示「执行中」；`/stream/live` 只能在无终态事件时补
  `done(already_completed=True)`，属于兜底而非事实源。

Phase 2B 增加 `agent_runs` 表与 `run_registry` 服务：登记 run、租约心跳、落终态，
并在应用启动时把租约过期的 `running` 标记为 `interrupted`。

## 2. 数据模型（schema v9）

`SCHEMA_VERSION` 8 → 9，新增 `agent_runs`（只加表，不改既有表）：

| 列 | 类型（SQLite / MySQL） | 说明 |
| --- | --- | --- |
| `run_id` | `VARCHAR(255) PRIMARY KEY` | `session_id:message_id`（message_id 缺失时 uuid） |
| `session_id` | `VARCHAR(255) NOT NULL` | 会话隔离/恢复定位 |
| `username` | `VARCHAR(255) NOT NULL DEFAULT ''` | 归属审计 |
| `message_id` | `VARCHAR(255) DEFAULT ''` | 本轮消息 id（resume 可空时用 uuid） |
| `status` | `VARCHAR(32) NOT NULL` | `running` / `completed` / `failed` / `cancelled` / `interrupted` |
| `request_json` | `TEXT` / `LONGTEXT` | 请求元数据（`ChatRequest.model_dump_json()` / resume decisions） |
| `lease_owner` | `VARCHAR(255) DEFAULT ''` | `hostname:pid`，记录执行 Pod |
| `lease_until` | `VARCHAR(50) DEFAULT ''` | ISO UTC 租约到期时间 |
| `error` | `TEXT` | 失败/中断原因（最长截断 500 字符） |
| `created_at` | `VARCHAR(50) NOT NULL` | ISO UTC |
| `updated_at` | `VARCHAR(50) NOT NULL` | ISO UTC |

索引：

- `idx_agent_runs_session (session_id, created_at)`：按会话查 run 历史；
- `idx_agent_runs_status (status, lease_until)`：启动恢复扫描 stale running。

`agent_runs` 已加入 `EXPECTED_TABLES`；表缺失时 schema 快速路径回退到完整初始化
并补建。时间统一 ISO 字符串、UTC（`+00:00`），`lease_until` 的跨 Pod 大小比较
必须是同一时区口径。

## 3. repository：`db/repositories/agent_runs.py`

| 函数 | 语义 |
| --- | --- |
| `start_run(db, run_id, session_id, username, message_id, request_json, owner, lease_until) -> bool` | 幂等插入 `running`；同 `run_id` 重复调用不覆盖既有状态 |
| `renew_run(db, run_id, owner, lease_until) -> bool` | 仅 `status='running' AND lease_owner=owner` 时续约 |
| `finish_run(db, run_id, status, error="") -> bool` | 仅 `status='running'` 时落终态并清空 `lease_until`；重复调用返回 False 但不覆盖 |
| `get_run(db, run_id)` | 返回 dict / None |
| `list_stale_running(db, now_iso, limit=500)` | `status='running' AND lease_until < now`，按租约升序 |
| `mark_interrupted(db, run_id, error) -> bool` | 仅 `status='running'` 时改为 `interrupted`；多 Pod 竞争的唯一裁决点 |

设计要点：

1. **幂等插入而不是 UPSERT**：客户端重试/重复投递不应把已完成的 run 改回
   `running`（否则会留下永远等不到终态的假运行）。
2. **条件更新**：`finish_run` / `mark_interrupted` 都以 `status='running'` 为
   条件，天然幂等且不会覆盖先到的终态。
3. **owner 条件**：`renew_run` 校验 `lease_owner`，A Pod 崩溃后 B Pod 已恢复时，
   A 的残留心跳不能把租约写回。
4. 双栈：SQLite `ON CONFLICT(run_id) DO NOTHING`，MySQL
   `ON DUPLICATE KEY UPDATE run_id=VALUES(run_id)`；所有 SQL 走 `db._execute`
   参数化。`Database` 只保留 6 个纯委托，`check_double_write.py` 白名单同步。

## 4. service：`services/run_registry.py`

- `RUN_LEASE_SECONDS`：默认 600 秒，`EASY_RUN_LEASE_SECONDS` 可覆盖；
- `new_run_id(session_id, message_id)`：确定性拼接（空 message_id 用 uuid），
  截断到 255；
- `start_run(...)` / `renew_run(...)` / `finish_run(...)`：薄封装，**异常只记
  warning 并返回 False**，绝不打断聊天；
- `recover_stale_runs(db, now=None) -> int`：分页扫描 stale running，逐个
  `mark_interrupted`；只有 `mark_interrupted` 返回 True 的 Pod 才向
  `stream_events` append 一条：

  ```json
  {"type": "error", "content": "上一次运行所在实例已中断，请重新发送或从审批处继续"}
  ```

  帧由 `SSEEmitter(session_id).error(...)` 生成，与既有 error 事件同格式；
  循环按 `RECOVER_BATCH_LIMIT=500` 分页直到没有 stale 行；单条失败只告警，
  若整页都写不了库（DB 故障）立即停止，避免启动恢复死循环。

## 5. 聊天生命周期接入（`api/chat.py`）

```
POST /agent/chat/stream
  → 校验/建会话/取 Agent
  → run_id = new_run_id(session_id, message_id)
  → start_run(db, run_id, ..., request_json=request.model_dump_json())
  → StreamingResponse(_detached_event_stream(..., run_id=run_id))

_detached_event_stream._drive
  → register_stream_task（跨 Pod 流锁）
  → 心跳（每 300s）：renew_run 与 renew_stream 同频
  → async for chunk in gen: hub.broadcast(chunk)
     - 正常耗尽      → finish_run("completed")
     - CancelledError → finish_run("cancelled") 后继续抛出
     - 其它 Exception → finish_run("failed", "Type: msg")（截断 500）
  → finally：先落 run 终态，再 hub.close() + 释放流锁
```

- `chat_resume` 同样 `start_run`：`message_id` 为空时生成 uuid；`request_json`
  只保存 `session_id` / `thread_id` / `decisions`。
- 专家拒答流等 `expert.py` 的两处 `_detached_event_stream` **不传 run_id**：
  专家预检不是 run 生命周期，行为与改造前一致。
- 端点已返回 409 的路径不会登记 run；`_detached_event_stream` 内部的并发兜底
  （`is_session_streaming` 为真）会把已登记的 run 落为 `failed`，避免只能等租约
  过期。
- 不改任何 SSE 事件名/字段，不改变事件序列与节奏。

## 6. 启动恢复（`app.py`）

DB 初始化完成、部署检查/接流量之前：

```python
recovered = await asyncio.to_thread(recover_stale_runs, db_instance)
```

有恢复时 warning 日志数量；异常只 warning，不阻断启动。每个 Pod 启动都会执行，
竞争由 `mark_interrupted` 的条件更新裁决。

## 7. 语义与边界

1. **租约是恢复提示，不是分布式锁**：run 的正常并发互斥仍由
   `session_runtime` 会话流锁保证；`agent_runs` 只负责可观测性与崩溃恢复。
2. **续约节奏**：流锁/run 心跳均为 300 秒，租约 600 秒，留出一倍余量；
   长任务不会被误回收。
3. **终态幂等**：`completed` / `failed` / `cancelled` / `interrupted` 一旦写入，
   后续 `finish_run` / `mark_interrupted` 都不会覆盖。
4. **多 Pod 恢复竞争**：两个 Pod 同时看到同一 stale run，只有一个
   `mark_interrupted` 返回 True，也只有它写 error 事件。
5. **已知风险（见审计报告）**：极端情况下（stale 会话已被新 run 重新占用并清空
   `stream_events`），恢复写入的 error 帧可能出现在新流回放中。当前按任务要求
   保留「恢复成功即写 error」的语义，后续可在写事件前检查
   `is_stream_active` 收紧。
6. `request_json` 只存请求元数据（files 是前端传入的元数据清单），不落文件
   二进制内容。

## 8. 未做（诚实清单）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| stale run 自动重新执行 | **未做** | 本批只标记 `interrupted` + error 事件；执行器/重放属于 Phase 2C |
| 跨 Pod run 接管/取消 | 未做 | 仍以会话流锁 + 进程内 cancel 为准 |
| `agent_runs` 保留/清理 | 未做 | 表会随聊天轮次增长，后续需要 TTL 清理任务 |
| run 与 stream 表的统一 id | 未做 | `run_id` 尚未写入 `stream_events`（不改 Phase 2A 契约） |
| 前端展示 run 状态 | 未做 | 仅通过既有 error 事件让前端结束「执行中」 |
| MySQL 实机验证 | 未跑 | DDL/SQL 由 SQLite + MySQL DDL 单测覆盖，CI 无 MySQL 实例 |
| Batch 0/1、Web Terminal | 未触碰 | 按任务约束 |

## 9. 测试

- `tests/db/test_agent_runs_repository.py`：schema 声明、start 幂等、renew owner、
  finish 幂等、stale 过滤/排序/分页、mark_interrupted 两次只有一次 True、
  Database 委托、MySQL DDL 口径。
- `tests/services/test_run_registry.py`：租约时长/覆盖、renew、finish 幂等、
  过期后 recover 写 error 事件、只恢复过期行、竞争只写一次事件、异常隔离。
- `tests/api/test_run_lifecycle.py`：chat stream 创建并完成 run、异常落 failed、
  取消落 cancelled、resume 创建 run（含空 message_id）、登记失败不影响聊天。
- 现有 `tests/api/test_chat.py`、`tests/api/test_stream_live_cross_pod.py`、
  `tests/services/test_stream_*.py` 回归通过。
