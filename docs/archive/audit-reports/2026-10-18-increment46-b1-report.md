# Increment 46 / B1 报告：生产 checkpointer / Store 契约

- 日期：2026-10-18
- 范围：后端 + 部署（未改前端、API 层、streaming、agent_manager、mcp、db、conftest）
- 设计：`docs/design/2026-10-18-b1-checkpointer-store.md`
- 未 commit（工作区保留改动）

## 1. 结论

B1 已完成：

- prod 多副本 + 非 Postgres checkpointer 启动期 fail-fast；`EASY_ALLOW_LEGACY_CHECKPOINTER=1` 为唯一显式逃生门（默认关闭）。
- Postgres saver 打开失败而降级到 sqlite/legacy 时，同样 fail-fast（prod 多副本）。
- K8s manifest 通过 Secret 注入 `EASY_CHECKPOINT_POSTGRES_URI`，并声明副本数供 `detect_worker_count()` 读取。
- Store 新增 `EASY_STORE_BACKEND=auto|postgres|sql`；auto 在 PG URI 存在且官方 `langgraph.store.postgres` 可用时使用官方 `AsyncPostgresStore`，否则保持 `SqlStore`。
- 单机 SQLite / 显式 legacy / dev/test 行为不变，现有测试全通过。

## 2. 改动文件

| 文件 | 改动 |
| --- | --- |
| `easy_agent/checkpoints.py` | 新增 `CheckpointDeploymentError`、`validate_checkpoint_deployment()`；`start_checkpoint_backend()` 打开前 + 降级后双重校验 |
| `easy_agent/app.py` | lifespan 中 `CheckpointDeploymentError` 直接 re-raise；启动/关闭新增 `start_store_backend()` / `shutdown_store()`，仅动 checkpointer/store 段 |
| `easy_agent/stores.py` | 新增 `StoreSelection`、`resolve_store_backend()`、`open_postgres_store()`、`start_store_backend()`、`shutdown_store()`、`active_store_backend()`；`get_agent_store()` 兼容官方 Store |
| `deploy/k8s/agent-deployment.yaml` | ConfigMap 增加 `EASY_REPLICA_COUNT=2`、checkpoint/store 开关、逃生门默认 0；容器注入 `EASY_CHECKPOINT_POSTGRES_URI`（Secret key `checkpoint-postgres-uri`）；注释说明应用库仍是 MySQL |
| `.env.prod.example` / `.env.dev.example` / `.env.test.example` | 文档化 `EASY_ALLOW_LEGACY_CHECKPOINTER`、`EASY_STORE_BACKEND`、`EASY_STORE_POSTGRES_URI` |
| `tests/services/test_checkpoint_deployment.py` | 新增守卫矩阵、打开前/降级后 fail-fast、逃生门、K8s 静态断言 |
| `tests/services/test_store_backend.py` | 新增 Store 选择矩阵、URI 回退、fake opener 生命周期/失败回退 |
| `docs/design/2026-10-18-b1-checkpointer-store.md` | 设计文档 |
| `docs/audit/2026-10-18-increment46-b1-report.md` | 本报告 |

未修改：`easy_agent/api/**`、`services/streaming.py`、`services/agent_manager.py`、`mcp/**`、`db/**`、`tests/conftest.py`、任何前端文件。

## 3. fail-fast 行为

### 3.1 规则

`validate_checkpoint_deployment(kind, agent_env, workers, allow_legacy)`：

| 条件 | 结果 |
| --- | --- |
| `AGENT_ENV=prod` 且 worker/replica > 1 且 `kind=postgres` | 通过 |
| 同上，`kind=sqlite/legacy` | 抛 `CheckpointDeploymentError` |
| 同上 + `EASY_ALLOW_LEGACY_CHECKPOINTER=1` | 通过并打 warning（明确“跨副本恢复不保证一致”） |
| prod 单 worker / dev / test | 通过，保持原行为 |
| `kind=postgres` 但 saver 打开失败并降级 | 关闭已打开的降级 saver 后抛 `CheckpointDeploymentError` |

错误信息包含 worker 数、当前后端、修复方式（配置 `EASY_CHECKPOINT_POSTGRES_URI`）和逃生门名称，便于直接定位。

### 3.2 启动链路

- `app.lifespan` 调用 `start_checkpoint_backend(agent_env=agent_env)`。
- 非 Postgres 选择在**打开连接前**被拦截，避免无效连接。
- Postgres 连接失败时，原有的降级链仍执行（日志保留）；若实际生效后端不是 Postgres，在 prod 多副本下立即关闭并抛错，避免“启动成功但多副本各写各的”。
- `CheckpointDeploymentError` 不被 lifespan 的通用 `except Exception` 吞掉，启动直接失败。
- `EASY_ALLOW_LEGACY_CHECKPOINTER` 使用统一 `env_bool` 语义：`1/true/yes/on` 为真，默认 false。

### 3.3 K8s 触发条件

`detect_worker_count()` 读取 `EASY_REPLICA_COUNT` / `WEB_CONCURRENCY` / `UVICORN_WORKERS`。K8s 进程内看不到 Deployment 副本数，因此 manifest 增加：

```yaml
EASY_REPLICA_COUNT: "2"
```

必须与 `spec.replicas` 保持同步；扩缩容后未同步会导致守卫不触发（已写入设计风险）。

## 4. K8s 注入

`deploy/k8s/agent-deployment.yaml`：

- ConfigMap：`AGENT_ENV=prod`、`EASY_REPLICA_COUNT=2`、`EASY_CHECKPOINT_BACKEND=auto`、`EASY_STORE_BACKEND=auto`、`EASY_ALLOW_LEGACY_CHECKPOINTER=0`。
- 容器 env：

```yaml
- name: EASY_CHECKPOINT_POSTGRES_URI
  valueFrom:
    secretKeyRef:
      name: easy-agent-secrets
      key: checkpoint-postgres-uri
```

- 注释明确：业务库仍是 MySQL（sessions/users/business tables），Postgres 只承载 HITL checkpoint 与 LangGraph Store。
- `EASY_STORE_POSTGRES_URI` 未单独注入；Store auto 在未设置时复用 `EASY_CHECKPOINT_POSTGRES_URI`，一个 Secret key 即可。
- 部署侧需保证 Secret 存在该 key，否则 Pod 会 `CreateContainerConfigError`；这是生产多副本的预期强约束。
- Store 表由官方 `await store.setup()` 在启动时创建，PG 账号需要建表/建索引权限。

## 5. Store 选择与未做项

### 5.1 选择逻辑

- `EASY_STORE_BACKEND=sql`：始终 `SqlStore`（业务 MySQL/单机 SQLite）。
- `EASY_STORE_BACKEND=postgres`：需要 PG URI + 官方包；缺失/导入失败则 warning + 回退 `SqlStore`（不阻断启动）。
- `auto`（默认）：PG URI 存在且官方 `langgraph.store.postgres` 可导入 → `AsyncPostgresStore`；否则 `SqlStore`。
- URI 优先级：`EASY_STORE_POSTGRES_URI` → `EASY_CHECKPOINT_POSTGRES_URI` → 空。
- 生命周期：lifespan 启动时 `shutdown_store()` 清理旧实例 → `AsyncPostgresStore.from_conn_string()` → `await setup()`；shutdown 时 `__aexit__` 关闭连接。
- `get_agent_store()` 调用方保持不变；未初始化时仍按需构造 `SqlStore`。
- 测试通过 `opener` 注入 fake，不依赖真实 Postgres。

### 5.2 未做项

1. **Store 数据迁移**：从 `SqlStore` 切到官方 `PostgresStore` 后，历史 `agent_store` 数据不会自动复制；官方使用独立 `store` 表。回滚 `EASY_STORE_BACKEND=sql` 仍可读旧数据。
2. **向量检索**：未给官方 Store 配置 `index` / embeddings；当前只用键值/namespace 能力，避免引入 pgvector 扩展与 embedding 依赖。
3. **Store 不做 prod fail-fast**：官方 Store 失败时仍回退业务库 `SqlStore`。`SqlStore` 使用共享 MySQL，多副本一致性由 MySQL 保证；B1 只要求 checkpointer fail-fast，避免把可选记忆能力变成启动硬依赖。
4. **MySQL 官方 saver/store**：LangGraph 不提供，未自研替代；MySQL 场景仍走既有回退。

## 6. 测试

### 6.1 命令与结果

```text
.venv/bin/python -m pytest \
  tests/services/test_checkpoint_backend.py \
  tests/services/test_checkpoints_sqlite.py \
  tests/services/test_checkpoint_deployment.py \
  tests/services/test_store_backend.py \
  tests/services/test_sql_store.py \
  tests/db/test_checkpoint_pruning.py \
  tests/db/test_checkpoint_roundtrip.py \
  tests/utils/test_deployment.py -q

70 passed, 1 warning in 1.31s
```

warning 为既有的 Starlette/httpx `TestClient` 弃用提示，与 B1 无关。

新增用例覆盖：

- prod 多副本 legacy/sqlite fail-fast；prod 单 worker、dev 多 worker 放行；
- 逃生门显式 true、环境变量 true、默认关闭、环境变量 false；
- 打开前拦截（断言不会调用 `_open_with_fallback`）；
- Postgres 打开失败 → sqlite 降级 → 关闭并 fail-fast；
- Store auto/postgres/sql 矩阵、官方包缺失回退、URI 环境变量与 checkpoint URI 回退、URI 优先级；
- 注入 fake opener 的启动/关闭、失败回退、缺 URI 回退、SqlStore 缓存；
- K8s YAML 静态断言：Secret 注入、`EASY_REPLICA_COUNT=2`、逃生门默认 0、注释含 MySQL/Postgres 说明。

### 6.2 静态检查

```text
uvx ruff check .
All checks passed!

python3 scripts/quality/import_graph.py --check-scc
import graph: 209 modules, 458 internal edges, 1 SCC(s) / 16 modules
[check-scc] OK — 实际 SCC 与白名单完全一致
```

说明：B1 新增的 `easy_agent.persistence.checkpoints` / `easy_agent.persistence.stores` 不在 SCC 中；`app.py` 对二者的 import 位于 lifespan 函数内，`checkpoints.py` 对 `utils.deployment` 的 import 为函数内延迟导入，不会新增环。复核期间工作区其他并行改动一度让 `knowledge-chat-scheduler-core` 环变化，最终已收敛并通过白名单校验。

## 7. 风险

1. **MySQL 回退仍是默认**：未配置 PG URI 时，prod 单副本 MySQL 继续使用自研 `PersistentMemorySaver`（每轮全量 pickle 写业务库）；Store 继续 `SqlStore`。功能可用但性能/官方兼容性弱，建议生产尽快配置 PG URI。
2. **Store 数据不迁移**：切换后端后跨线程记忆从空开始；回滚可恢复旧数据。需要在切换窗口接受记忆丢失或另做迁移脚本（B1 未做）。
3. **全局单例测试隔离**：checkpointer/store 均为模块级全局；新测试在 `finally` 中 `shutdown_*`。后续新增用例若不清理，可能造成 event loop/连接泄漏或跨用例串状态。
4. **K8s 副本数漂移**：`EASY_REPLICA_COUNT` 是静态值，需与 `spec.replicas` 同步；HPA/手工扩缩容时应改为由部署模板注入。
5. **event loop 绑定**：`AsyncPostgresStore` / `AsyncPostgresSaver` 在 lifespan 的 loop 中创建，同进程复用；不可跨进程共享对象。每 pod 各自建连接池。
6. **PG 权限/DDL**：`setup()` 需要建表、建索引（含 `CREATE INDEX CONCURRENTLY`）权限；权限不足会回退（Store）或 fail-fast（prod 多副本 checkpointer）。多 pod 首次同时启动会并发执行官方 `setup()`，建议先在预发/单 pod 完成建表，或由 DBA 预创建 schema；上线前应在预发验证 Secret 与 DDL 权限。
7. **degradation 日志**：prod 多副本 PG 故障时会先尝试降级再关闭；日志中可能出现一条降级 error，最终以 `CheckpointDeploymentError` 启动失败，属于预期。

## 8. 回滚

- 配置级：`EASY_CHECKPOINT_BACKEND=legacy|sqlite`、`EASY_STORE_BACKEND=sql`；prod 多副本还需 `EASY_ALLOW_LEGACY_CHECKPOINTER=1`。
- 代码级：回退本报告第 2 节文件即可；没有 DB schema 迁移（官方 Store 仅新增自己的 `store` 表），可直接回滚。
