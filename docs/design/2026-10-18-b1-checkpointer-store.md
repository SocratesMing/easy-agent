# B1 生产 checkpointer / Store 契约

- 日期：2026-10-18
- 状态：已实现（Increment 46 / B1）
- 范围：后端与部署，不改前端、API 层、streaming、agent_manager、mcp、db、conftest
- 相关：FW-09（checkpointer auto/postgres/sqlite/legacy）、FW-11（SqlStore）

## 1. 背景

FW-09 已把 HITL checkpointer 切到 LangGraph 官方 saver：`EASY_CHECKPOINT_BACKEND=auto|postgres|sqlite|legacy`，默认 `auto`。但生产形态仍有两个缺口：

1. 多副本（K8s `replicas: 2`）且未配置 `EASY_CHECKPOINT_POSTGRES_URI` 时，业务库 MySQL 没有官方 saver，只能回退自研 `PersistentMemorySaver`。该实现每轮把 MemorySaver 状态 pickle 进业务库，属于单机语义；多副本下 HITL 恢复不保证一致，而启动只打 warning。
2. `stores.SqlStore` 是业务库自研 Store，可用于 MySQL/单机；官方 `langgraph.store.postgres`（`AsyncPostgresStore`）已随依赖安装，却没有选择开关。

B1 的目标是把“生产多副本必须 Postgres checkpointer”变成启动期契约，并让 Store 在 Postgres URI 存在时优先使用官方实现，同时保持单机 SQLite/legacy 行为不变。

## 2. 目标与非目标

### 目标

- `AGENT_ENV=prod` 且 `detect_worker_count() > 1` 时：
  - checkpointer 选择不是 `postgres` → 启动期 fail-fast；
  - Postgres saver 打开失败而降级到 sqlite/legacy → 同样 fail-fast；
  - 唯一逃生门：`EASY_ALLOW_LEGACY_CHECKPOINTER=1`（默认关闭）。
- K8s manifest 通过 Secret 注入 `EASY_CHECKPOINT_POSTGRES_URI`，并说明应用库仍是 MySQL。
- 新增 `EASY_STORE_BACKEND=auto|postgres|sql`：auto 在 PG URI 存在且官方包可用时使用 `AsyncPostgresStore`，否则保持 `SqlStore`。
- 保持 dev/test、单 worker、SQLite、显式 legacy 的既有行为与测试。

### 非目标

- 不新增 MySQL 官方 saver/store（LangGraph 未提供）。
- 不改 API/streaming/agent_manager/MCP/db schema/前端。
- 不把业务数据（sessions/users/business tables）迁到 Postgres；业务库仍是 MySQL。
- 不做 Store 数据迁移：官方 PostgresStore 使用独立的 `store` 表，不复用 `agent_store` 业务表；切换后历史 store 数据不从 MySQL 自动搬迁（见风险）。
- 不执行 Batch 0/1，不动 Web Terminal。

## 3. 官方能力与现状

| 能力 | 官方实现 | 本项目接入点 |
| --- | --- | --- |
| HITL checkpoint | `AsyncPostgresSaver` / `AsyncSqliteSaver` | `easy_agent/checkpoints.py` |
| 跨线程 Store | `AsyncPostgresStore`（`langgraph.store.postgres`） | `easy_agent/stores.py` |
| 单机 MySQL 回退 | 无官方实现 | 自研 `PersistentMemorySaver` / `SqlStore` |

`langgraph.store.postgres` 提供：

- `AsyncPostgresStore.from_conn_string(uri)`：异步上下文管理器，内部创建连接/连接池；
- `await store.setup()`：建 `store` 表与迁移；
- 上下文退出时关闭连接。

实现只使用这些公开 API，不读取私有属性、不直接操作其内部连接。

## 4. 设计

### 4.1 Checkpointer 部署守卫

新增纯函数：

```python
validate_checkpoint_deployment(
    kind, *, agent_env=None, workers=None, allow_legacy=None
) -> None
```

规则矩阵：

| AGENT_ENV | worker/replica | kind | 结果 |
| --- | --- | --- | --- |
| prod | >1 | postgres | 通过 |
| prod | >1 | sqlite/legacy | `CheckpointDeploymentError` |
| prod | >1 | 任意非 postgres + `EASY_ALLOW_LEGACY_CHECKPOINTER=1` | 通过并 warning |
| prod | 1 | 任意 | 通过（既有单机行为） |
| dev/test | 任意 | 任意 | 通过（既有行为） |

`detect_worker_count()` 读取 `EASY_REPLICA_COUNT` / `WEB_CONCURRENCY` / `UVICORN_WORKERS`。K8s 无法从进程内看到 Deployment 副本数，因此 manifest 显式设置 `EASY_REPLICA_COUNT: "2"`，并要求与 `spec.replicas` 同步。

`start_checkpoint_backend()` 在两个时点校验：

1. 打开前：选择非 postgres 直接失败，避免无意义连接；
2. 打开后：若 Postgres 打不开而降级成 sqlite/legacy，关闭已打开的 saver 后失败。

app lifespan 对 `CheckpointDeploymentError` 直接 re-raise；其他异常仍保持原有“记录日志 + 自研快照降级”，避免把非配置类故障误判为致命错误。

### 4.2 Store 后端选择

新增：

```python
resolve_store_backend(*, backend=None, postgres_uri=None, official_available=None)
start_store_backend(selection=None, *, opener=None)
shutdown_store()
active_store_backend()
get_agent_store(db=None) -> BaseStore
```

选择规则：

| `EASY_STORE_BACKEND` | PG URI | 官方包可用 | 结果 |
| --- | --- | --- | --- |
| auto（默认） | 有 | 是 | postgres（`AsyncPostgresStore`） |
| auto | 有 | 否 | sql + warning |
| auto | 无 | 任意 | sql（`SqlStore`） |
| postgres | 有 | 是 | postgres |
| postgres | 无/不可用 | - | sql + warning（不阻断启动） |
| sql | 任意 | 任意 | sql（显式） |

URI 解析优先级：

1. `EASY_STORE_POSTGRES_URI`；
2. `EASY_CHECKPOINT_POSTGRES_URI`（一次 Secret 同时覆盖 HITL 与 Store）；
3. 空 → `SqlStore`。

生命周期：

- `start_store_backend()` 在 app lifespan 中调用，先 `shutdown_store()` 清理旧实例；
- 官方实现成功：`await store.setup()`，全局 `_store` 指向它；
- 失败：记录 error，回退 `SqlStore`，聊天等能力不受影响；
- app shutdown 调用 `shutdown_store()` 关闭连接池并清空缓存；
- `get_agent_store()` 仍是无参同步入口，返回当前生效的 Store；未初始化时按需构造 `SqlStore`，保持既有调用方不变。

`opener` 参数只用于测试注入 fake store，生产路径不传，避免测试依赖真实 Postgres。

### 4.3 K8s 注入

`deploy/k8s/agent-deployment.yaml`：

- ConfigMap 增加 `EASY_REPLICA_COUNT`、`EASY_CHECKPOINT_BACKEND=auto`、`EASY_STORE_BACKEND=auto`、`EASY_ALLOW_LEGACY_CHECKPOINTER=0`；
- 容器 env 增加 `EASY_CHECKPOINT_POSTGRES_URI`，来源为 Secret `easy-agent-secrets` 的 `checkpoint-postgres-uri`；
- 注释明确：应用库仍是 MySQL，Postgres 只承载 HITL checkpoint 与 LangGraph Store。

Secret 示例（部署侧维护，不提交密钥）：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: easy-agent-secrets
stringData:
  checkpoint-postgres-uri: postgresql://user:password@postgres:5432/easy_agent
```

## 5. 配置项

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EASY_CHECKPOINT_BACKEND` | auto | 既有：auto/postgres/sqlite/legacy |
| `EASY_CHECKPOINT_POSTGRES_URI` | 空 | 官方 `AsyncPostgresSaver` 连接串 |
| `EASY_CHECKPOINT_SQLITE_PATH` | data/checkpoints.db | 单机 SQLite checkpoint |
| `EASY_ALLOW_LEGACY_CHECKPOINTER` | 0（关闭） | prod 多副本逃生门 |
| `EASY_STORE_BACKEND` | auto | auto/postgres/sql |
| `EASY_STORE_POSTGRES_URI` | 空 | 官方 Store 连接串；留空复用 checkpoint URI |

## 6. 测试计划

- `tests/services/test_checkpoint_deployment.py`
  - prod 多副本 legacy/sqlite fail-fast；
  - prod 单 worker、dev 多 worker 放行；
  - 逃生门显式/环境变量开启，默认关闭；
  - 打开前拦截、Postgres 打开失败降级后仍 fail-fast；
  - K8s YAML 静态断言（Secret 注入、副本数、注释）。
- `tests/services/test_store_backend.py`
  - auto/postgres/sql 选择矩阵、官方包缺失回退；
  - URI 环境变量与 checkpoint URI 回退；
  - 注入 fake opener 的启动/关闭、失败回退、SqlStore 缓存。
- 现有 `tests/services/test_checkpoint*`、`tests/db/test_checkpoint*`、`tests/utils/test_deployment.py` 必须继续通过。

## 7. 风险与回滚

1. **MySQL 回退**：未配置 Postgres URI 的 MySQL 部署仍走自研 `PersistentMemorySaver`（prod 单副本）或 `SqlStore`（Store）。前者每轮全量 pickle 写业务库，长会话/高并发性能差；B1 只是把多副本风险前移为启动失败，没有改变单机回退实现。单副本生产建议尽快配置 Postgres URI。
2. **Store 数据不迁移**：从 `SqlStore` 切到官方 PostgresStore 后，历史 `agent_store` 数据不会自动复制，跨线程记忆会“从空开始”。回滚到 `EASY_STORE_BACKEND=sql` 仍可读旧数据。
3. **测试隔离**：Store/Checkpointer 都使用模块级全局连接。测试必须在用例内 `shutdown_store()` / `shutdown_checkpointers()`，否则连接会跨 event loop 泄漏；B1 新增用例均在 `finally` 中清理。app lifespan 已负责生产关闭。
4. **K8s 副本数漂移**：`EASY_REPLICA_COUNT` 是 manifest 的显式镜像值，手工扩缩容后需同步，否则守卫可能不触发。
5. **事件循环绑定**：`AsyncPostgresStore` 在 lifespan 的 event loop 中创建，必须由同一进程/同一 loop 使用；不要在多 worker 间共享对象（每 pod 各自建池）。
6. **回滚方式**：设置 `EASY_CHECKPOINT_BACKEND=legacy`/`sqlite`、`EASY_STORE_BACKEND=sql`（prod 多副本还需 `EASY_ALLOW_LEGACY_CHECKPOINTER=1`），或回退本次代码；不涉及 schema/数据迁移，可直接回滚。

## 8. 验收

- prod 多副本 + 非 Postgres checkpointer：启动失败并给出可操作错误；
- 逃生门开启：启动成功且日志 warning；
- PG URI 存在：Store 使用官方 `AsyncPostgresStore`，`setup()` 成功；
- 无 PG URI：Store 保持 `SqlStore`，现有测试全绿；
- `uvx ruff check .` 与 `scripts/import_graph.py --check-scc` 通过。
