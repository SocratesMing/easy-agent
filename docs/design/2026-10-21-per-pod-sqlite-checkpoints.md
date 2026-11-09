# 多副本：每个 pod 独立的 SQLite checkpoint + 粘性会话

- 日期：2026-10-21
- 状态：已实施（2026-10-21，尚未提交）。§11 的待确认项按推荐处理：清单示例用 ingress-nginx、前端与 API 同源、接受 pod 重建时等待中的审批作废、多副本时上传改为 `EASY_UPLOAD_BACKEND=store`。实施时另修了一处：`langgraph-checkpoint-sqlite` 原在可选依赖 `sqlite-saver` 里，镜像（`uv export --extra docs`）和 CI 都没装，官方 SQLite saver 导入失败、静默回退到业务库快照；已挪进主依赖
- 更新（2026-10-29）：路径改到 config.yaml 的 `checkpoint.sqlite_path`（`config-example.yaml` 示例 `${CHECKPOINT_DIR:-/qts/checkpoint}/{pod}.db`）。YAML 没配时用旧环境变量 `EASY_CHECKPOINT_SQLITE_PATH`，再没有默认 `/qts/checkpoint/{pod}.db`；`auto` 不再因为业务库是 MySQL、没配路径而回退业务库快照。k8s 清单改为把 pod 本地的 emptyDir 挂到 `/qts/checkpoint`。下文的 `EASY_CHECKPOINT_SQLITE_PATH` / `data/checkpoints/{pod}.db` 是当时的写法
- 范围：`EASY_CHECKPOINT_SQLITE_PATH` 支持按 pod 名区分（`{pod}` 占位符）；生产多副本的启动校验；审批恢复请求落到别的 pod 时的处理；删除会话时清理所有 pod 上的 checkpoint；k8s 清单与 Ingress 粘性配置。不在范围：把审批请求转发到所在的 pod（§10）、前端改动
- 相关：`docs/design/2026-10-18-multi-replica-target.md`（多副本目标态，其中以 Postgres 为前提的部分已暂缓）；`easy_agent/persistence/checkpoints.py`；`deploy/k8s/agent-deployment.yaml`
- 数据：2026-10-21，HEAD `eddd9be`

## 1. 结论先行

- **每个 pod 用自己的 SQLite 文件，绝不共用。** 路径里写 `{pod}`，启动时替换成本 pod 的名字（依次取 `EASY_POD_NAME`、`POD_NAME`、`HOSTNAME`、主机名），例如 `data/checkpoints/{pod}.db`。
- **同一个用户的请求由 Ingress 用 cookie 固定到同一个 pod。** 审批挂在哪个 pod 的文件里，恢复请求就必须回到那个 pod。ingress-nginx 的写法见 §4.3。
- **启动校验只放开一种组合：** SQLite、路径含 `{pod}`、`EASY_STICKY_SESSIONS=1`、每个 pod 只跑一个 worker。其余多副本组合照旧 fail-fast（§4.4）。
- **审批记下所在的 pod。** 恢复请求落到别的 pod，或者本 pod 找不到这条审批的 checkpoint 时，返回 409 并说明原因。现在的代码在这种情况下会把审批决策直接传下去，在一个空 thread 上执行一次恢复（§2、§4.5）。
- **第一阶段 checkpoint 文件仍放在 pod 本地的 `data` 卷（emptyDir），不放 NAS。** pod 重建后用户会被分到别的 pod，文件保留下来也用不上，NAS 的限制却全都要承担（§4.2）。
- **checkpointer 固定用官方的异步 saver（`AsyncSqliteSaver`）。** 多副本时不允许回退到同步写库的自研快照：SQLite 打不开就直接启动失败（§4.4、§4.8）。
- **删除会话时，所有 pod 上的 checkpoint 都要删。** 处理删除请求的 pod 当场删掉自己文件里这个会话的全部 thread；其余 pod 各自有一个异步的后台对账任务，在 10 分钟内删掉（§4.9）。
- **代价：** pod 被重建（滚动发布、驱逐、缩容、节点故障）时，还在等审批的操作作废，用户要重新发送；同一用户在两台设备上同时使用时，可能落到不同的 pod。
- **工作量：** 代码约 2 天（含测试），清单与文档半天，预发验证半天。

## 2. 现状

- **一个进程只有一个 SQLite saver**（`_sqlite_saver`），路径来自 `EASY_CHECKPOINT_SQLITE_PATH`：绝对路径照用，相对路径按仓库根解析。路径能配，但不能按 pod 区分，多个 pod 配同一个路径就是共用一个文件。
- **生产多副本一律 fail-fast：** `validate_checkpoint_deployment` 在 `AGENT_ENV=prod` 且副本或 worker 数大于 1 时直接拒绝启动，除非设置 `EASY_ALLOW_LEGACY_CHECKPOINTER=1`。
- **副本数和 worker 数混在一起：** `detect_worker_count()`（`easy_agent/utils/deployment.py`）依次看 `EASY_REPLICA_COUNT`、`WEB_CONCURRENCY`、`UVICORN_WORKERS`，取第一个大于 1 的值，分不清「3 个 pod 各 1 个 worker」和「1 个 pod 里 3 个 worker」。
- **待审批不记录所在的 pod：** 待审批记在消息的 `pending_approval` 上，只有 `thread_id` 和 `action_requests` 两个字段（`easy_agent/services/streaming/projector.py`）。
- **只有官方 saver 是异步的：** 官方 `AsyncSqliteSaver` 基于 aiosqlite，SQLite 在单独的线程里执行，读写不占事件循环。自研的 `PersistentMemorySaver` 每次写 checkpoint 都在事件循环上同步写 MySQL。SQLite 打不开时，`_open_with_fallback` 会回退到后者。
- **删除会话只删当前进程里的 checkpoint：** `easy_agent/api/sessions.py` 删除会话时调用 `delete_session_threads`，用 `adelete_thread` 删掉会话 thread 和每条消息一个的 thread（消息 id 取自 `agent_runs`）。删的是处理这个请求的进程里的 saver：单 pod 时就是全部，多 pod 下会话在别的 pod 文件里留下的 thread 删不到。
- **找不到待审批时会直传：** 恢复接口（`easy_agent/api/chat.py` 的 `chat_resume`）在本进程读 thread 状态。读不到待审批中断时，`plan_resume` 按「改造前的直传」处理（`easy_agent/services/hitl_state.py`），对一个没有任何 checkpoint 的 thread 执行一次 `Command(resume=...)`。单 pod 时很少走到这里，多 pod 下请求一落错 pod 就会走到。
- **已经能跨副本工作的部分：**
  - 会话流锁（`session_runtime`，存 DB）；
  - SSE 断线重放（`stream_events`，存 DB，任意 pod 都能接上）；
  - run 租约（`agent_runs`，存 DB）；
  - 定时任务只在一个实例执行（`distributed_lock`，存 DB）；
  - 多 worker 时的限流计数（存 DB）；
  - 业务数据和 LangGraph Store（MySQL）。

## 3. 目标与非目标

目标：

1. 生产可以跑多个 pod，checkpoint 仍用官方 SQLite saver，不引入新的数据库。
2. 粘性会话正常时，审批和恢复的行为与单 pod 完全一致。
3. 粘性失效、pod 被重建时，用户收到明确的 409 和原因，不出现错误的执行。
4. 配错时启动就失败（共用文件、没配粘性、一个 pod 跑多个 worker），而不是运行一段时间后才出问题。
5. checkpoint 读写都走异步接口，不占事件循环。
6. 删除会话后，所有 pod 上这个会话的 checkpoint 都会被删掉。

非目标：

- 跨 pod 恢复审批，即 pod 重建后审批不丢（§10）。
- 让 pod 完全无状态，`EASY_MULTI_REPLICA_STRICT=1` 照旧不满足。
- 前端改动。

## 4. 设计

### 4.1 实例名与路径模板

- **实例名：** 新增 `instance_name()`（放在 `easy_agent/utils/deployment.py`），依次取 `EASY_POD_NAME`、`POD_NAME`、`HOSTNAME` 环境变量，都没有时用 `socket.gethostname()`。
  - k8s 里通过 Downward API 注入 `EASY_POD_NAME`（`metadata.name`）。不注入的话，k8s 默认把容器的 hostname 设成 pod 名，也能取到。
  - 只允许 `[A-Za-z0-9._-]`，不能为空，不能以 `.` 开头，最长 253 个字符；不符合时启动失败，防止拼出越界路径。
- **路径模板：** `EASY_CHECKPOINT_SQLITE_PATH` 里的 `{pod}` 在 `resolve_checkpoint_backend()` 时替换成实例名。`CheckpointSelection` 增加两个字段：`per_pod`（模板里有没有 `{pod}`）和 `instance`。启动日志同时打印模板和实际路径。
- **推荐写法：** `data/checkpoints/{pod}.db`，每个 pod 一个文件，SQLite 的 `-wal`、`-shm` 文件跟着放在同一目录。
- **为什么不用 k8s 的变量展开：** k8s 本身支持在 `env[].value` 里写 `$(EASY_POD_NAME)`，但通过 `envFrom` 引入的 ConfigMap 值不会展开，在 k8s 之外（docker-compose、本机）也用不了。应用自己处理 `{pod}`，在哪里都一样。
- **和已有标识的关系：** `session_runtime.instance_owner()` 返回 `hostname:pid`。在 k8s 里它冒号前的部分就是 `instance_name()`，日志里两者能对上。

### 4.2 文件放在哪

| 位置 | 容器崩溃重启后 | pod 重建后 | 注意 |
| --- | --- | --- | --- |
| pod 本地的 `data` 卷（emptyDir，**推荐**） | 保留 | 丢失 | 现有清单就是这样配的，不受 NAS 的限制 |
| 每个 pod 一块 RWO 块存储（StatefulSet 的 `volumeClaimTemplates`） | 保留 | 保留（同名 pod） | 第一阶段 pod 重建后用户已被分到别的 pod，保留的文件用不上；要配合 §10 的转发才有意义 |
| NAS 上按 pod 分开的文件 | 保留 | 保留 | 必须在开 WAL 之前设 `PRAGMA locking_mode=EXCLUSIVE`，避开 `-shm` 共享内存文件；同一文件只能有一个进程打开，`scripts/db/prune_checkpoint_threads.py` 只能停机跑；每次落盘都过网络。同样要配合 §10 才有意义 |

第一阶段用 emptyDir。

- **emptyDir 的生命周期：** 它跟着 pod 走，容器崩溃重启时数据还在，pod 也还是原来的 IP，cookie 仍然指向它。
- **pod 重建时：** 滚动发布、驱逐等会让 pod 重建，换一个新 IP。ingress-nginx 的 cookie 对应的是具体的后端端点，用户会被分到别的 pod，那么旧文件放在哪里都一样用不上。
- **NAS 上的单个文件：** SQLite 官方说明 WAL 不能用在网络文件系统上，EXCLUSIVE 模式下才不会创建共享内存索引（[wal.html](https://www.sqlite.org/wal.html)）。如果以后选 NAS 方案，需要新增 `EASY_CHECKPOINT_SQLITE_LOCKING=exclusive`，第一阶段不做。

### 4.3 粘性会话

ingress-nginx 在 Ingress 上加这几个注解：

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/affinity: "cookie"
    nginx.ingress.kubernetes.io/affinity-mode: "persistent"   # 扩容时不把已有用户挪走
    nginx.ingress.kubernetes.io/session-cookie-name: "EASY_AGENT_ROUTE"
    nginx.ingress.kubernetes.io/session-cookie-path: "/"
    nginx.ingress.kubernetes.io/session-cookie-max-age: "604800"   # 7 天
    nginx.ingress.kubernetes.io/session-cookie-samesite: "Lax"
    nginx.ingress.kubernetes.io/session-cookie-change-on-failure: "true"
```

- **前端和 API 必须同源。** 默认 `API_BASE_URL` 为空，请求走相对路径 `/agent/...`，浏览器会自动带上 cookie。如果 `API_BASE_URL` 指向另一个域名，fetch 不带 cookie，粘性就失效了。这时要么改成同源，要么前端加 `credentials: 'include'`，同时把 cookie 设成 `SameSite=None; Secure`，这不在本方案范围内。
- **不要用 Service 的 `sessionAffinity: ClientIP`：** 经过 Ingress 之后，来源 IP 都是 Ingress 控制器的 pod。
- **粘性要配在选后端 pod 的那一层：** 如果 Ingress 和后端之间还有一层前端 nginx（像 `docker/nginx-frontend.conf` 那样 `proxy_pass` 到后端 Service），粘性要配在那一层。k8s 清单里前端页面由后端自己托管，没有这一层。
- **其他 Ingress 控制器：** Traefik、云厂商负载均衡器都有基于 cookie 的会话保持，原理相同，按各自的写法配。

### 4.4 启动校验

以下是 `AGENT_ENV=prod` 且副本数大于 1 时的规则：

| checkpointer | 路径 | `EASY_STICKY_SESSIONS` | 每个 pod 的 worker 数 | 结果 |
| --- | --- | --- | --- | --- |
| sqlite | 含 `{pod}` | 1 | 1 | 放行；日志写明 checkpoint 按 pod 隔离、依赖粘性会话 |
| sqlite | 含 `{pod}` | 未设置 | 1 | fail-fast：先在 Ingress 配好粘性，再设 `EASY_STICKY_SESSIONS=1` |
| sqlite | 不含 `{pod}` | 任意 | 任意 | fail-fast：多个 pod 不能共用一个 SQLite 文件，路径里加 `{pod}` |
| sqlite | 含 `{pod}` | 1 | 大于 1 | fail-fast：每个 pod 只能跑一个 worker |
| sqlite，但文件打不开 | 含 `{pod}` | 1 | 1 | 启动失败，不回退自研快照（§4.8） |
| legacy（MySQL 快照） | — | 任意 | 任意 | fail-fast（不变） |
| 任意 | 任意 | 任意 | 任意，且设了 `EASY_ALLOW_LEGACY_CHECKPOINTER=1` | 放行并告警（这个逃生门不变） |

- **为什么要显式声明粘性：** 应用看不到 Ingress 的配置，`EASY_STICKY_SESSIONS=1` 是一个显式的确认。
- **副本数和 worker 数分开算：** 新增 `detect_replica_count()`（读 `EASY_REPLICA_COUNT`）和 `detect_workers_per_pod()`（读 `WEB_CONCURRENCY` / `UVICORN_WORKERS`）。`detect_worker_count()` 保持原来的含义，限流、调度器、`runtime_features` 照旧用它。
- **为什么每个 pod 只能一个 worker：** 同一个 pod 里的多个 worker 会解析出同一个 `{pod}` 路径，而审批又可能落到另一个 worker 上。现在的入口 `docker/entrypoint.sh` 本来就只起一个 uvicorn 进程。
- **不变的规则：** 多实例时业务库必须是 MySQL（`deployment_errors`）；多副本必须开 `distributed_lock`，否则调度器不注册定时任务；`EASY_MULTI_REPLICA_STRICT=1` 仍然不满足，因为 checkpoint 还在 pod 里。

### 4.5 审批落到别的 pod 时

**写入：** `pending_approval` 增加一个 `instance` 字段，记下 `instance_name()`。

**恢复时的实例检查**（在 `chat_resume` 里，取 Agent 之前）：

- 最后一条消息的 `pending_approval.instance` 与本 pod 不同时，返回 409，detail 为：「这条审批在实例 A 上，当前请求到了实例 B（粘性会话失效，或 A 已重启、下线），请重新发送消息」。
- 同时记一条 warning，方便排查粘性配置。
- 升级前的旧消息没有 `instance` 字段，跳过这项检查。

**兜底：** 本 pod 的 checkpointer 里，这条 thread 一个 checkpoint 都没有（`aget_state` 返回空快照，`created_at` 为 `None`）时，抛 `StaleApprovalError`，返回 409：「找不到这条审批的状态（可能在另一个实例上，或已过期），请重新发送消息」，不再直传。这一条对单 pod 同样有用，比如 pod 重建后 emptyDir 被清空了。

**409 时不清掉待审批标记：** 如果粘性只是暂时失效（比如用户清了 cookie），回到原 pod 还能继续。

- **会话 thread 模式**（`session_thread: true`）下，用户发新消息时 `abandon_pending_approval` 会照常把旧审批标成已拒绝。
- **默认的每条消息一个 thread 模式**下，旧审批会一直显示，点了返回 409。要自动作废，需要知道哪些 pod 已经下线（§10 的实例心跳）。

### 4.6 其他留在 pod 里的状态

| 状态 | 存在哪 | 有粘性时 | 需要做什么 |
| --- | --- | --- | --- |
| Agent 缓存、MCP 会话 | 进程内存 | 换 pod 时重建一次，慢一点，结果不变 | 不用做 |
| 本进程的流任务（`is_session_streaming`） | 进程内存 | 跨 pod 的互斥靠 DB 流锁 | 不用做 |
| SSE 断线重放 | DB `stream_events` | 任意 pod 都能接上 | 不用做 |
| 上传的原始文件（`EASY_UPLOAD_BACKEND=local`） | pod 本地 `data/uploads` | 换 pod 后看不到，pod 重建后丢失 | 多副本时改成 `EASY_UPLOAD_BACKEND=store`（存进 MySQL） |
| 工作区、记忆文件 | NAS | 共享 | 记忆文件锁用的是 fcntl，在 NAS 上跨 pod 不一定可靠；同一用户同时在两个 pod 上写记忆时可能互相覆盖（有粘性时少见） |
| 定时任务 | DB 分布式锁 | 只在一个实例执行 | 必须 `DISTRIBUTED_LOCK_ENABLED=true` |
| 删除会话时清理 thread | 每个 pod 自己的文件 | 只有处理删除请求的 pod 能当场删 | 其余 pod 由后台对账任务删，见 §4.9 |

### 4.7 各种场景下，等待中的审批会怎样

| 场景 | 等待中的审批 | 说明 |
| --- | --- | --- |
| 容器崩溃重启（pod 不变） | 保留 | emptyDir 和 IP 都不变，cookie 仍指向这个 pod |
| 滚动发布、驱逐、节点故障（pod 重建） | 作废 | 新 pod 的文件是空的，用户被分到别的 pod；恢复返回 409，重新发送即可 |
| 扩容 | 保留 | persistent 模式下，已有用户不会被挪走 |
| 缩容 | 被删掉的 pod 上的作废 | 同上 |
| 用户清了 cookie、换了设备 | 可能 409 | 回到原 pod 才能继续，否则重新发送 |
| 删除会话 | 一起删除 | 处理请求的 pod 当场删；其余 pod 在一个对账周期内删（§4.9） |

### 4.8 异步 checkpointer

- **只用官方异步 saver：** 多副本时 checkpointer 固定为官方 `AsyncSqliteSaver`。它基于 aiosqlite，SQLite 在单独的线程里执行，接口全是异步的。之前几轮压测（100～1000 个会话并发）里，事件循环延迟 p95 都在 1～16ms 之间。
- **调用方式不变：** 现有调用已经都走异步接口：图的 `astream` / `aget_state`、删除用的 `adelete_thread`。§4.9 的后台任务也用这些异步接口。
- **不回退自研快照：** 自研 `PersistentMemorySaver` 每次写 checkpoint 都在事件循环上同步写 MySQL，多副本时也不能保证一致。所以在 per-pod 模式下，SQLite 文件打不开（目录不可写、磁盘满）就直接启动失败，不像单 pod 那样回退到自研快照。
- **单 pod 不变：** 单 pod 的行为保持现状。

### 4.9 删除会话时清理所有 pod 上的 checkpoint

分两步：

1. **当场删（处理删除请求的 pod）：** 保持现在的做法，`delete_session_threads` 用 `adelete_thread` 删掉本 pod 文件里这个会话的全部 thread。
2. **其余 pod 上的（后台对账）：** 每个 pod 在 app lifespan 里起一个异步后台任务，启动时跑一次，之后每 `EASY_CHECKPOINT_GC_INTERVAL` 秒（默认 600）跑一次：
   1. 列出本 pod 文件里的所有 thread_id。直接查 `SELECT DISTINCT thread_id FROM checkpoints`，在 saver 的锁内执行，比用 `alist` 遍历全部 checkpoint 轻得多。
   2. 按 thread_id 解析出会话 id。规则与 `scripts/db/prune_checkpoint_threads.py` 一致：会话 thread 就是会话 id（UUID），每条消息一个的 thread 是「会话 id-消息 id」。`sched-` 开头的定时任务 thread 跳过（每次运行结束会自己删）；认不出的 thread 保留。
   3. 分批查 `sessions` 表，找出已经不存在的会话（`delete_session` 是硬删除）。数据库是同步接口，查询放进线程执行。
   4. 用 `adelete_thread` 删掉这些会话的 thread。每删一个就让出一次事件循环，避免长时间占着 saver 的锁、挡住正在进行的对话写 checkpoint。

为什么用对账，不用「删除广播」：

- **自愈：** 对账不需要新表；删除时某个 pod 正好不在线、当场删失败，下一轮都能补上。
- **代价可以接受：** 别的 pod 上的残留最多晚一个周期才删。这段时间里，这些 thread 已经无法访问：会话不存在，接口会直接拒绝。
- **如果有更严格的要求**（比如要求删除后立即从所有 pod 消失），再加一张删除事件表，让每个 pod 短间隔轮询。这次不做。

不会误删：

- 只删能认出会话 id、且会话确实已不存在的 thread。
- 新会话先写 `sessions` 再开始对话、产生 checkpoint，不会出现「thread 已有、会话还没落库」被误删的情况。
- 某一轮查数据库失败时，整轮跳过，不删任何东西。

## 5. 配置与清单

环境变量：

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `EASY_CHECKPOINT_SQLITE_PATH` | `data/checkpoints/{pod}.db` | 按 pod 分文件 |
| `EASY_STICKY_SESSIONS` | `1` | 确认 Ingress 已配粘性；不设则多副本启动失败 |
| `EASY_REPLICA_COUNT` | 与 `replicas` 一致 | 启动校验靠它判断是不是多副本 |
| `EASY_POD_NAME` | Downward API `metadata.name` | 实例名 |
| `DISTRIBUTED_LOCK_ENABLED` | `true` | 定时任务只在一个实例执行 |
| `EASY_UPLOAD_BACKEND` | `store` | 上传文件不留在 pod 本地 |
| `EASY_ALLOW_LEGACY_CHECKPOINTER` | `0` | 保持关闭 |
| `EASY_CHECKPOINT_GC_INTERVAL` | `600` | 后台对账间隔（秒），见 §4.9 |

`deploy/k8s/agent-deployment.yaml` 的改动（仍用 Deployment，第一阶段不需要 StatefulSet）：

```yaml
# ConfigMap
data:
  EASY_REPLICA_COUNT: "2"                                  # 与下面的 replicas 一致
  EASY_CHECKPOINT_SQLITE_PATH: "data/checkpoints/{pod}.db"
  EASY_STICKY_SESSIONS: "1"
  DISTRIBUTED_LOCK_ENABLED: "true"
  EASY_UPLOAD_BACKEND: "store"
---
# Deployment
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: easy-agent
          env:
            - name: EASY_POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
```

另外新增一份示例 `deploy/k8s/agent-ingress.yaml`：带 §4.3 的注解，host 和 TLS 留占位，路径 `/` 指向 `easy-agent` Service。

## 6. 改动清单

| 文件 | 改动 |
| --- | --- |
| `easy_agent/utils/deployment.py` | `instance_name()`、`detect_replica_count()`、`detect_workers_per_pod()` |
| `easy_agent/persistence/checkpoints.py` | `{pod}` 替换；`CheckpointSelection.per_pod` / `instance`；`validate_checkpoint_deployment` 按 §4.4 改，入参从 `kind` 改为整个 `selection`；per-pod 模式下 SQLite 打不开时不回退（§4.8）；对账函数 `prune_deleted_session_threads()`（§4.9） |
| 新增 `easy_agent/persistence/thread_ids.py` | thread_id 的分类与会话 id 解析，从 `scripts/db/prune_checkpoint_threads.py` 挪过来，脚本和后台任务共用 |
| `easy_agent/app.py` | lifespan 里启动、关闭对账任务 |
| `scripts/db/prune_checkpoint_threads.py` | 改用共用的分类函数 |
| `easy_agent/services/streaming/projector.py` | `pending_approval.instance` |
| `easy_agent/api/chat.py` | `chat_resume` 的实例检查 |
| `easy_agent/services/streaming/orchestrator.py` | `plan_resume_for_agent` 读到空快照时抛 `StaleApprovalError`，不再直传 |
| `deploy/k8s/agent-deployment.yaml`、新增 `deploy/k8s/agent-ingress.yaml` | §5 |
| `.env.prod.example`、`README.md`、`docs/architecture-current.md` | 多副本的配置方式 |
| `tests/` | §7 |

## 7. 测试计划

- **单元测试：**
  - 实例名的取值优先级，以及非法值时启动失败；
  - `{pod}` 替换；
  - §4.4 表里的每一行；
  - `pending_approval` 带上 `instance`；
  - 实例不一致时返回 409；
  - 空 thread 返回 409、不再直传；
  - 没有 `instance` 字段的旧消息不受影响。
- **对账单元测试：**
  - 只删已不存在的会话的 thread；还存在的会话、`sched-` thread、认不出的 thread 都保留；
  - 查数据库失败时整轮不删；
  - 删除过程中会让出事件循环，进行中的对话还能写 checkpoint；
  - per-pod 模式下 SQLite 打不开时启动失败，不回退。
- **双实例集成测试：** 在同一个测试进程里，用两个实例名和两个 SQLite 文件模拟两个 pod。
  - 在 A 上触发审批，到 B 上恢复返回 409，回到 A 上恢复成功；
  - 两个 pod 都留有同一会话的 thread 时，在 A 上删除会话，A 当场删掉，B 跑一轮对账后也删掉。
- **清单测试：**
  - 副本数与 `EASY_REPLICA_COUNT` 一致；
  - 注入了 `EASY_POD_NAME`；
  - 路径含 `{pod}`；
  - `EASY_STICKY_SESSIONS=1`；
  - 按清单的配置调用启动校验能通过；
  - Ingress 示例带 cookie 粘性注解。
- **预发手工验证：** 见 §8。

## 8. 上线步骤

1. **合入代码。** 默认行为不变，单 pod 的配置照常工作。
2. **预发环境：** 2 个副本，Ingress 配好粘性，逐项验证：
   - 同一浏览器的对话和审批都落在同一个 pod 上（看日志里的实例名）；
   - 删掉粘性 cookie 再点审批，返回 409，提示里有实例名；
   - `kubectl delete pod` 之后点旧审批，返回 409，重新发送正常；
   - 定时任务只执行一次；
   - 上传的文件在两个 pod 上都能读到（store 模式）；
   - 同一个会话先后在两个 pod 上对话过（清一次 cookie 即可做到），删除会话后，两个 pod 的文件里都查不到它的 thread（另一个 pod 最多等一个对账周期）。
3. **生产环境：** ConfigMap 和 Deployment 一起改，在低峰期把副本数从 1 调到 2。

## 9. 风险

| 风险 | 后果 | 缓解 |
| --- | --- | --- |
| 粘性没配好，或前端跨域 | 大部分审批返回 409 | 409 和 warning 日志里带实例名；上线前按 §8 验证 |
| 滚动发布 | 发布时一部分用户的审批要重新发送 | 低峰期发布；调小 `maxSurge` / `maxUnavailable`，减少每次替换的 pod 数 |
| persistent 模式下负载不均 | 扩容后，新 pod 只接新用户 | 接受；需要重新均衡时改成 `balanced`（扩容时会挪走一部分已有用户） |
| 记忆文件跨 pod 并发写 | 同一用户同时在两个 pod 上写时，可能互相覆盖 | 见 §4.6；有粘性时少见 |
| 对账删得太多 | 误删进行中会话的 checkpoint | 只删能认出会话 id、且会话已不在 `sessions` 表的 thread；查库失败时整轮跳过；有单测覆盖 |
| 对账占用 saver 的锁 | 文件很大时拖慢正在进行的对话 | 列 thread_id 用一条 SQL；逐个删、每次让出事件循环 |

## 10. 后续（不在本次范围）

- **把审批转发到所在的 pod：** 409 改成后端把恢复请求转给记录里的那个 pod（StatefulSet 加 headless Service 的 DNS，或者记录 pod IP）。再配上每个 pod 持久化的存储，pod 重建之后审批也不会丢。
- **实例心跳：** 记录哪些 pod 还活着，已下线 pod 上的审批直接标成作废，界面不再显示审批按钮。
- **按时间清理旧 thread：** 把 `scripts/db/prune_checkpoint_threads.py` 的按时间清理（每条消息一个的旧 thread）并进 §4.9 的后台任务，控制文件大小。

## 11. 待确认

1. Ingress 是不是 ingress-nginx？不是的话，换成对应控制器的 cookie 会话保持写法。
2. 前端和 API 是否同源，也就是 `API_BASE_URL` 是否为空？
3. 第一阶段能不能接受「pod 重建时，等待中的审批作废」？不能接受的话，需要先做 §10 的转发。
4. 多副本时把上传文件改成 `EASY_UPLOAD_BACKEND=store`，是否可以？
