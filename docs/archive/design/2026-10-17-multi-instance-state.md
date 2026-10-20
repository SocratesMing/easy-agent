# 多实例/多租户状态外置设计（ARCH-15 / CONC-02）

| 项 | 内容 |
| --- | --- |
| 状态 | 设计草案（待评审） |
| 依据 | 审计 ARCH-15、T2 并发结论、`deploy/k8s/agent-deployment.yaml` |
| 约束 | 单机开发仍可零依赖运行；多副本为可选部署形态；不引入必须的 Redis（可先用 DB 实现） |

## 1. 现状（进程内状态清单）

| 状态 | 位置 | 多副本后果 |
| --- | --- | --- |
| `_session_agents` / `_session_stream_tasks` / `_session_expert` / `_session_connectors` / `_session_create_locks` | `services/agent_manager.py` | 换实例后 Agent 重建、HITL resume 失效、取消落空 |
| `_session_stream_hubs`（SSE 广播/回放） | `api/chat.py` | 断线重连可能落到无状态副本，拿不到历史/收尾事件 |
| `_llm_instance` | `services/agent_manager.py` | 每副本各一份（可接受） |
| APScheduler 全量 job | `services/scheduler.py` | 未开分布式锁时每个副本各执行一次（已加 fail-closed 保护） |
| 限流计数 | `middleware/rate_limit.py` | 每副本独立计数，可被多副本放大（Batch 1 安全项） |
| 知识库 worker 轮询 | `knowledges/runtime/worker.py` | N 副本 N 倍 Ragflow 调用（需 leader 选举） |

## 2. 目标架构（分阶段，DB-first）

### Phase A（低风险，1–2 天）：把“会话是否在跑/谁在跑”统一到 DB

- 复用 `session_runtime` 表：`stream_owner`、`stream_until`、`cancel_requested`、`connectors`。
- `acquire_stream` 改为**原子条件更新**（`UPDATE ... WHERE stream_owner='' OR stream_until<now`，按 rowcount 判断），去掉先读后写的 TOCTOU。
- 增加 `stream_id`（每次流唯一）与 heartbeat（每 15s 更新 `stream_until`）。
- `/cancel` 只写 `cancel_requested=1` + 通知 owner；owner 轮询该标记（或 LISTEN/NOTIFY 可选）。

### Phase B（1 周）：SSE 事件外部化（可选 Redis，或 DB outbox）

- 方案 1（推荐先做）：`stream_events` 表（`stream_id, seq, payload, created_at`）+ 短 TTL；`/stream/live` 从 DB 回放 + 轮询增量，兼容进程内 hub 作为快路径。
- 方案 2（规模化后）：Redis Streams/PubSub；前端经统一网关订阅。
- 保留进程内 hub 仅作为“本副本快路径”，共享存储为权威。

### Phase C（设计级）：Agent 运行时与租户

- Agent 缓存能否共享：LangGraph 编译图可跨请求复用，但 EasyAgent 持有 workspace/model/prompt，按 `(tenant,user,session,model,expert)` 分片；建议每副本本地缓存 + DB 记录“缓存版本号”，版本变更时各副本惰性淘汰（替代跨副本广播）。
- 多租户：`sessions/users/files/experts/tasks` 增加 `tenant_id`；归属查询统一带 tenant；`workspace/{tenant}/{user}/...`；审计带 tenant。
- 迁移：新增列 + 双写 + 回填 + 切读，灰度发布。

### Phase D：调度与后台任务

- 调度器：`distributed_lock.enabled=true` 为多副本前置；启动校验 `EASY_REPLICA_COUNT>1` 时强制（已 fail-closed，下一步改为 config 校验 fail-fast）。
- 知识库 worker：leader 选举（DB 租约表或 k8s Lease），非 leader 只做健康检查。
- 限流：共享计数（DB/Redis）。

## 3. 部署约束（与用户决策一致）

- 生产强制 MySQL（代码已禁止静默降级）；SQLite 仅单机开发，WAL + busy_timeout 已启用。
- k8s：`data` 不能是 `emptyDir`（当前配置）如果依赖本地 SQLite/JWT secret；多副本必须配 MySQL + 共享 workspace PVC + 分布式锁 + Postgres checkpointer（推荐）。
- 健康检查：`/ready` 应等调度器/worker 初始化完成（当前 lifespan 同步阻塞，需拆分后台初始化）。

## 4. 验收

- 两台副本 + 负载均衡下：同一会话并发只允许一条流（409）；刷新/重连可回放最近事件；`/cancel` 在非 owner 副本也能生效；定时任务只执行一次。
- 断开任一副本，进行中的流按配置策略继续或明确失败（不能静默卡死）。


## Phase A 进展（2026-10-17）

已完成（详见 `docs/audit/2026-10-17-increment7-report.md`）：

- `Database.try_acquire_session_stream()` 用条件 UPDATE + INSERT 唯一键回退实现
  **原子流锁**，替换旧「先读后写」的 TOCTOU；跨副本并发只有一个赢家。
- `session_runtime.acquire_stream()` 改用原子方法，与进程内 `is_session_streaming`
  形成双层互斥。
- 新增 `utils/deployment.py`：`AGENT_ENV=prod` + workers/replicas>1 + SQLite 时
  启动期 fail-fast（生产强制 MySQL）；dev/test 多 worker 仅告警。
- 流锁 TTL heartbeat 已完成：`renew_session_stream` + 流任务每 5 分钟续约。
- 剩余：Phase B（SSE 事件外置）、Phase C/D（Agent 缓存/租户/调度 leader 选举）。
