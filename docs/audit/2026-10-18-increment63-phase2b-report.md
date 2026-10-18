# Increment 63 / 多副本 Phase 2B：agent_runs 租约与启动恢复报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 分支 | 工作区既有分支（Phase2A 未提交改动之上） |
| 范围 | 仅后端；不改前端；API/SSE 15 事件 wire 契约不变 |
| 未执行 | commit / push / Batch 0/1 / Web Terminal / 全量测试 |
| 结论 | Phase 2B 目标达成：run 可持久化、可续约、可落终态；启动时 stale running 标记 interrupted 并补 error 事件；**自动重跑未做（Phase 2C）** |

## 1. 交付内容

### 1.1 schema v9

- `SCHEMA_VERSION` 8 → 9；`EXPECTED_TABLES` 增加 `agent_runs`；
- `_init_tables_full` 新增 `_create_agent_runs_table`；
- SQLite `request_json TEXT` / MySQL `LONGTEXT`；时间列 `VARCHAR(50)`；
- 索引 `idx_agent_runs_session (session_id, created_at)`、
  `idx_agent_runs_status (status, lease_until)`；
- 未修改任何既有表结构，未触碰 `stream_events` 语义。

### 1.2 repository

新增 `easy_agent/db/repositories/agent_runs.py`：

- `start_run`：幂等插入 `running`（SQLite `ON CONFLICT DO NOTHING` /
  MySQL `ON DUPLICATE KEY UPDATE run_id=VALUES(run_id)`），返回是否新插入；
- `renew_run`：`status='running' AND lease_owner=owner` 才续约；
- `finish_run`：`status='running'` 条件更新终态、清空 `lease_until`；
- `get_run`、`list_stale_running`、`mark_interrupted`（竞争裁决点）；
- 时间统一 UTC ISO（`+00:00`，`timespec="seconds"`），保证跨 Pod 可比。

`Database` 增加 `_create_agent_runs_table` 与 6 个纯委托，并同步
`scripts/check_double_write.py` 的 `INFRA_DATABASE_METHODS` /
`FROZEN_DATABASE_METHODS` 白名单。

### 1.3 service

新增 `easy_agent/services/run_registry.py`：

- `RUN_LEASE_SECONDS`（默认 600，`EASY_RUN_LEASE_SECONDS` 覆盖）；
- `new_run_id(session_id, message_id)`：确定性拼接，空 message_id 用 uuid，
  截断 255；
- `start_run` / `renew_run` / `finish_run`：薄封装，异常只记 warning；
- `recover_stale_runs(db, now=None) -> int`：分页扫描 stale running，
  `mark_interrupted` 成功才 `SSEEmitter(session_id).error(...)` append 到
  `stream_events`；同一 stale run 多 Pod 竞争只有一个写事件；单条失败不影响其它。

### 1.4 聊天接入

`easy_agent/api/chat.py`：

- `chat_stream` 在返回 `StreamingResponse` 前 `start_run`，`run_id =
  new_run_id(session_id, message_id)`，`request_json=request.model_dump_json()`
  （仅请求元数据，files 本身已是元数据，不含二进制）；
- `chat_resume` 同样 `start_run`；message_id 为空用 uuid；`request_json` 存
  `session_id` / `thread_id` / `decisions`；
- `_detached_event_stream(..., run_id=None)`：
  - `_drive` 心跳中 `renew_run` 与 `renew_stream` 同频（300s）；
  - 正常耗尽 → `completed`；`CancelledError` → `cancelled`；其它异常 →
    `failed`（`Type: msg`，service 层截断 500 字符）；
  - finally 中先落 run 终态，再 `hub.close()` / 释放流锁；
  - 端点外竞态（生成器内 `is_session_streaming` 为真）也会把已登记 run 落
    `failed`，避免只能等租约过期；
  - 不改变任何 SSE 输出；expert.py 两处调用不传 `run_id`（非 run 生命周期）。

### 1.5 启动恢复

`easy_agent/app.py` lifespan：`_init_database_and_state` 之后、部署检查/接流量
之前 `await asyncio.to_thread(recover_stale_runs, db_instance)`；有数量日志，
异常只 warning，不阻断启动。

## 2. 验证

执行命令与结果（WSL，仓库 `.venv`）：

```
pytest tests/db/test_agent_runs_repository.py tests/services/test_run_registry.py \
       tests/api/test_run_lifecycle.py tests/api/test_chat.py \
       tests/api/test_stream_live_cross_pod.py tests/services/test_stream_*.py -q
# 133 passed
```

额外跑了与 schema 版本相关的快速路径用例（不在任务要求的子集内）：

```
pytest tests/db/test_schema_version.py -q
# 7 passed（与上一个子集合计 140 passed）
```

相邻回归（C1 边界计数需随 Phase 2B 新增的两处 `repo.database` 同步）：

```
pytest tests/test_architecture_gate.py tests/api/test_c1_remaining_domains.py \
       tests/services/test_session_runtime.py tests/db/test_migrations.py \
       tests/db/test_schema_version.py -q
# 26 passed
```

```
uvx ruff check .
# All checks passed!

python scripts/import_graph.py --check-scc
# import graph: 223 modules, 514 internal edges, 0 SCC(s) / 0 modules
# [check-scc] OK — 实际 SCC 与白名单完全一致

python scripts/check_double_write.py
# [OK] sessions.py / database.py
# Database: 152 methods, 115 pure delegations
```

未跑全量测试（按任务要求）。任务给出的 Phase2A 全量基线为 1368 passed
（修复 C1 后预期 1369），本批未重新验证。

## 3. 设计取舍与风险

1. **租约是恢复提示，不是锁**：run 的并发互斥仍由 `session_runtime` 会话流锁
   保证。`agent_runs` 只回答「哪个 run 在哪个 Pod、是否已落终态」。
2. **租约时长**：默认 600s，心跳 300s；即使一次心跳丢失也不会误回收。
3. **幂等语义**：重复 `start_run` 不把 completed 改回 running；重复
   `finish_run` / `mark_interrupted` 不覆盖既有终态。`finish_run` 第二次返回
   False，调用方无需特殊处理。
4. **恢复事件可能落到新流**：极端情况下，stale 会话已被新 run 重新占用并清空
   `stream_events`，恢复写入的 error 帧会出现在新流回放里。当前按任务要求保留
   「恢复成功即写 error」；后续可在 append 前用 `is_stream_active` 收紧。
5. **恢复扫描成本**：每个 Pod 启动都扫描一次 `(status, lease_until)` 索引，
   分页 500；正常无 stale 行时只有 1 次查询。
6. **SQLite/MySQL 双栈**：SQL 全部参数化并经 `_execute` 转换；MySQL DDL 只有
   单测（`LONGTEXT` / 索引名），CI 无 MySQL 实机。
7. **`request_json` 体积**：`ChatRequest.model_dump_json()` 含用户消息正文与
   文件元数据；如后续担心体积/隐私，可改为只存字段白名单（本批按任务要求）。
8. **start_run 在事件循环同步调用**：与 `api/chat.py` 既有同步 DB 调用一致；
   单次 INSERT 很轻。若后续要求严格非阻塞，可改为 `asyncio.to_thread`。

## 4. 未做 / 后续

| 项 | 说明 |
| --- | --- |
| **stale run 自动重新执行** | **本批未做**。只标记 `interrupted` + 一条 error 事件；执行器/重放是 Phase 2C |
| 跨 Pod run 接管 / 取消 | 未做；取消仍是进程内 `task.cancel()` + `session_runtime` 标记 |
| `agent_runs` TTL / 归档 | 未做；表随聊天轮次增长，需要后续清理任务 |
| `stream_events` 关联 run_id | 未做（不改 Phase 2A 契约） |
| 前端 run 状态展示 | 未做；只通过既有 error 事件结束「执行中」 |
| MySQL 实机验证 | 未跑（CI 无 MySQL） |
| `/stream/live` 恢复语义 | 未改；仍按 `stream_events` 回放 + 无终态补 done |
| Batch 0/1、Web Terminal | 未触碰 |

## 5. 工作区状态说明

- 开始前工作区已有 Phase2A 未提交改动，以及 52 个 **mode-only**
  （`100755 → 100644`，内容零差异）变更和未跟踪的 `.claude/`；本批未改动、
  未提交这些内容。
- 本批所有新增/修改文件均为 LF、UTF-8 无 BOM；已用脚本核对，无 CRLF 混入。
- 未执行 commit / push。

## 6. 变更文件

新增：

- `easy_agent/db/repositories/agent_runs.py`
- `easy_agent/services/run_registry.py`
- `tests/db/test_agent_runs_repository.py`
- `tests/services/test_run_registry.py`
- `tests/api/test_run_lifecycle.py`
- `docs/design/2026-10-18-phase2-run-lease.md`
- `docs/audit/2026-10-18-increment63-phase2b-report.md`

修改：

- `easy_agent/db/schema.py`（schema v9 / `agent_runs` 建表与索引）
- `easy_agent/db/database.py`（建表委托 + 6 个业务纯委托）
- `scripts/check_double_write.py`（白名单）
- `easy_agent/api/chat.py`（start_run / resume / `_detached_event_stream` 生命周期）
- `easy_agent/app.py`（启动 stale 恢复）
- `tests/api/test_c1_remaining_domains.py`（`repo.database` 保留边界 9 → 11，
  登记 chat_stream / chat_resume 的 run 登记两处）
