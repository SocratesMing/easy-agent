# 多副本容器目标架构与 Phase 0 决策（ADR）

| 项 | 内容 |
| --- | --- |
| 状态 | Accepted（Phase 0） |
| 日期 | 2026-10-18 |
| 目标 | 自托管 Kubernetes 多副本 Web；Pod 无状态；状态外置 |
| 约束 | 不改前端；HTTP/SSE 契约保持；Batch 0/1 安全项另行批准 |

## 1. 目标拓扑

```
Ingress
  ├─ easy-agent Web Pod ×N          # 无状态请求/SSE/API
  ├─ run worker / scheduler leader  # 独立单 leader（Phase 2/4）
  ├─ MySQL                          # 业务数据（sessions/users/tasks/...）
  ├─ PostgreSQL                     # LangGraph checkpointer + Store
  ├─ 对象存储 (S3/MinIO)            # workspace/上传/生成文件/技能（Phase 1/3）
  ├─ stream_events / run 表         # DB-first 事件与运行（Phase 2）
  └─ 外部 Sandbox（可选）            # 代码执行（Phase 1 评估）
```

## 2. 决策

| 决策 | 选择 | 理由 | 回退 |
| --- | --- | --- | --- |
| 业务库 | MySQL（不变） | 现有数据/事务；多副本共享 | — |
| LangGraph checkpointer | **PostgreSQL**（多副本强制） | 官方 `AsyncPostgresSaver`；B1 已 fail-fast | 单机 SQLite |
| LangGraph Store | **PostgreSQL** 优先；MySQL `SqlStore` 过渡 | 官方 `AsyncPostgresStore`；B1 已支持 `EASY_STORE_BACKEND` | `sql` 回退 |
| 文件存储 | **S3/MinIO** 目标；Phase 1 先做抽象，保留 RWX 实现 | 官方 `StoreBackend`/Sandbox；避免 NFS flock/路径语义 | `EASY_FILE_BACKEND=local` |
| SSE/流事件 | **DB-first**（`stream_events` 表）+ 可选 Redis | 少一个硬依赖；跨 Pod 回放 | Redis |
| run 队列 | **DB outbox + worker** 优先；Redis/Celery 可选 | Pod 崩溃可续跑；避免仅内存 task | Redis |
| Sandbox | 外部 Sandbox 目标；Phase 1 评估，暂保留 bwrap/Landlock | 官方推荐生产 Sandbox；本地 shell 不适合多租户 | `EASY_SANDBOX=local` |
| 前端 | 不改 | 用户要求双基线保持 | — |
| 多租户 | 后置（Phase 5），除非确认 | 当前 username 隔离 | — |

## 3. 特性开关（Phase 0 预留）

| 变量 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- |
| `EASY_STREAM_BACKEND` | `local` | `local`/`db`/`redis` | Phase 2 才支持 db/redis |
| `EASY_RUN_QUEUE` | `none` | `none`/`db`/`redis` | Phase 2 |
| `EASY_FILE_BACKEND` | `local` | `local`/`s3`/`store` | Phase 1/3 |
| `EASY_SANDBOX` | `local` | `local`/`external` | Phase 1 评估 |
| `EASY_MULTI_REPLICA_STRICT` | `0` | `0`/`1` | 1 时启动强制校验当前多副本能力；Phase 0–1 预期会失败，直到 Phase 2/3 完成 |

`EASY_MULTI_REPLICA_STRICT=1` 的启动校验（`easy_agent/runtime_features.py`）：
- 生产 + 多 worker/副本时，checkpointer 必须是 postgres；
- Store 必须是 postgres；
- stream/run/file/sandbox 仍为 local/none 时，明确失败（Phase 2/3 前不允许宣称多副本安全）；
- 默认 0，K8s 样例暂设 0。

## 4. Phase 0 出口条件

- 当前基线提交、工作区干净；
- 本 ADR 合并；
- 特性开关 + 严格门禁 + 脚本 `scripts/check_multi_replica.py` + 测试；
- K8s 样例显式 `EASY_MULTI_REPLICA_STRICT=0` 并注明“Phase 2/3 后改 1”；
- 全量测试通过。

## 5. Phase 1 范围（下一步）

1. 升级 `deepagents` 到 0.7.x，`langchain` 到 1.4.x；补 `TodoListMiddleware`（0.7 `write_todos` opt-in）、`delete` 工具 HITL。
2. 移除 callable backend；实现 `BackendProtocol` 实例（运行期 `get_runtime()` 解析 `SessionContext`），保持 D2 图复用。
3. `/memories/` 用官方 `StoreBackend`；`/workspace/` 抽象为 `WorkspaceBackend`（local 实现先保留，S3/Sandbox 目标）。
4. 文件存储抽象与 Sandbox spike；不做破坏性切换。
5. 全量回归 + 框架契约测试 + K8s 2 副本基线启动。

## 6. 非目标 / 风险

- Phase 0–1 完成后仍不宣称多副本安全；SSE/run/文件外置未完成。
- `EASY_MULTI_REPLICA_STRICT=1` 在 Phase 2/3 完成前会阻止 prod 多副本启动，这是有意的。
- 官方 Docs：Backends（StoreBackend/StateBackend）、Going to production、LangGraph persistence。
