# 多 pod 部署与分布式锁

服务部署在多个 pod（或 uvicorn `--workers > 1`）时，每个实例都有独立进程内存与
独立的调度循环，实例之间**唯一共享的是数据库和共享存储**。本文梳理已知的「多实例
会出问题」场景，并说明基于数据库的分布式锁（`easy_agent/utils/distributed_lock.py`）
的开关与用法。

## 一、场景梳理

### 1. 重复执行类（同一件事被多个实例各做一遍）

| 场景 | 关键代码 | 多实例下的现象 | 处理情况 |
| --- | --- | --- | --- |
| 定时任务 cron 触发 | `easy_agent/services/scheduler.py`（`reload_all_tasks` / `_execute_task`）、`easy_agent/app.py`（`_start_scheduler`） | 每个 pod 启动时都会把 DB 里所有 enabled 任务注册进自己的 APScheduler，同一 cron 到点后所有 pod 各触发一次：任务被重复执行、重复建 session/run 记录、重复调 LLM | ✅ 已用分布式锁互斥，开关 `distributed_lock.enabled` |
| 手动触发任务 `POST /{task_id}/run` | `easy_agent/api/scheduled_tasks.py` | 请求只落在某一个 pod 上，只有该 pod 的调度器会执行；但可能与其它 pod 的 cron 触发同时进行 | ✅ 与 cron 共用同一把锁，并发触发只有一个真正执行 |
| 知识库任务队列 | `easy_agent/knowledge/worker.py`（`claim_next_task` / `heartbeat_task` / `recover_stale_tasks`） | 已经是「DB 条件 UPDATE 抢占 + 心跳续期 + 超时回收」的多 worker 安全模型 | ✅ 已有 DB 抢占机制，无需再加锁 |
| 知识库对账与告警维护 | `KnowledgeTaskWorker.maintain()` → `KnowledgeReconciler.run()` | 每个 worker 都会跑对账扫描与告警 upsert；写入是幂等的，但会重复扫描远端、重复执行修复动作（浪费配额） | ⚠️ 可选优化：用同一把锁让单个 worker 执行对账 |

### 2. 进程内状态类（不是重复执行，但多实例下行为不一致）

| 场景 | 关键代码 | 多实例下的现象 |
| --- | --- | --- |
| 暂停/删除定时任务时的在途中断 | `services/scheduler.py` 的 `running_tasks` / `stopping_tasks` | 只有接到请求的 pod 能取消本进程的在途执行；其它 pod 上的在途运行会继续跑完（收尾时会重新读 DB，已禁用则标记 cancelled，不会记为成功，但 agent 调用过程不会被中断） |
| 会话流式事件中心 | `easy_agent/api/chat.py` 的 `_session_stream_hubs`（TTL 120s） | 事件流只存在单进程内存：断线重连、多标签页、刷新后 SSE 落到别的 pod 就订阅不到，表现为输出「丢帧」 |
| 登录态相关缓存 | `easy_agent/api/auth.py` 的 `_active_login_ip` / `_login_time_cache` | 异地登录提示、上次登录时间只反映单个实例；鉴权与空闲超时已改为 DB 权威（`users.last_activity_at`），不受影响 |
| HITL 中断态与 Agent 缓存 | `easy_agent/agent.py` 的 `_CHECKPOINTERS`、`services/agent_manager.py` 的 `_session_agents` | 中断审批后的恢复请求若落到别的 pod，会找不到 checkpointer / 缓存 Agent，恢复失败 |
| 共享存储上的文件写 | `easy_agent/utils/task_logger.py`（append 同一个 `{task_id}.log`）、workspace 下的 `memory.md` 等 | 多 pod 挂同一份 NAS 时并发追加/覆盖同一文件可能交错或丢内容 |

### 3. 已确认安全（仅作排查参考）

- `Database.init_tables()`：建表与补列/建索引都是幂等的（`CREATE TABLE IF NOT EXISTS`、
  重复索引错误已忽略），并发启动安全。
- `easy_agent/services/mcp.py` 的 `_mcp_tools_cache`：只是进程内缓存，最多多读几次
  MCP 配置，无正确性影响。

## 二、分布式锁

实现：`easy_agent/utils/distributed_lock.py`，锁表 `distributed_locks`
（`lock_key` 主键 + `owner` + `expires_at`，随 `Database.init_tables()` 自动创建）。

抢锁 = 「插入锁行（已存在则忽略）」，失败再尝试「抢占过期锁」（`expires_at < now`
的原子 UPDATE）。两步都是单条 SQL，SQLite 与 MySQL(InnoDB) 行为一致，不依赖
`INSERT ... ON DUPLICATE KEY UPDATE` 的影响行数语义。

### 配置开关

```yaml
# config.yaml
distributed_lock:
  enabled: ${DISTRIBUTED_LOCK_ENABLED:-false}   # 多 pod / 多 worker 部署置 true
  key_prefix: "${DISTRIBUTED_LOCK_KEY_PREFIX:-easy_agent}"
  ttl_seconds: ${DISTRIBUTED_LOCK_TTL:-300}     # 持有者崩溃后其它实例最多等这么久
  renew_interval_seconds: ${DISTRIBUTED_LOCK_RENEW_INTERVAL:-100}  # 长任务续约间隔
  retry_interval_seconds: ${DISTRIBUTED_LOCK_RETRY_INTERVAL:-1}    # 等待抢锁的轮询间隔
```

实际取值来自 `.env.{AGENT_ENV}`（`.env.prod.example` 已默认开启）。**默认关闭**：
单实例部署保持引入分布式锁之前的行为，关闭时不产生任何额外数据库访问。

### 用法

```python
from easy_agent.utils.distributed_lock import get_distributed_lock

handle = get_distributed_lock().acquire("scheduled_task:t-1")   # 非阻塞，抢不到立即返回
if not handle.acquired:
    return                                                       # 其它实例正在处理，跳过
try:
    ...临界区：只能由一个实例执行的处理...
finally:
    handle.release()                                             # 只删除自己持有的锁
```

长任务（执行时间可能超过 TTL）要周期性续约，续约失败说明锁已被其它实例接管，
调用方应中止处理：

```python
while True:
    await asyncio.sleep(handle.renew_interval_seconds)   # 实际取 min(配置值, ttl/3)
    if not handle.renew():
        break                                            # 锁已丢失
```

同步代码可用上下文管理器（`wait_seconds > 0` 时轮询等待）：

```python
with get_distributed_lock().locked("reconcile", wait_seconds=5) as handle:
    if not handle.acquired:
        return
    ...
```

### 语义与限制

- **未开启开关时** `acquire` 恒返回 `acquired=True`（`enabled=False` 的空操作句柄），
  调用方不需要为开关写分支。
- **不可重入**：同一实例在释放前重复 `acquire` 同一 key 会返回未获锁；互斥是按
  owner（`主机名-进程号-随机串`）判定的，释放与续约都会校验 owner，不会误删他人的锁。
- **TTL 与崩溃恢复**：持有者崩溃后，其它实例最多等待 `ttl_seconds` 即可接管；TTL
  需要大于「单次临界区最长执行时间 / 续约间隔」，同时不能太大，否则故障转移变慢。
- **时钟**：过期判断使用各实例本地 UTC 时间，要求各实例时钟同步（NTP）。偏移远小于
  TTL 时无影响。
- **只保护数据库可见的临界区**：跨实例的进程内状态（见上文第 2 类）不会被这把锁解决。
- 锁表由 `Database.init_tables()` 创建，MySQL 账号需要有建表/改表权限。

## 三、定时任务接入方式

`easy_agent/services/scheduler.py` 的 `_execute_task` 已接入：

1. 读到任务后先抢锁 `scheduled_task:{task_id}`，抢不到直接返回，并记一条
   `operation=skip_locked` 的任务审计日志（`workspace/{用户}/cron/{task_id}.log`）；
2. 抢到后照常建 session/run、调用 agent，同时启动续约心跳；
3. 续约失败（锁过期后被其它实例接管）会取消本次在途执行，避免重复写库；
4. 收尾（含 run 记录、`last_run_at`/`next_run_at` 更新）完成后释放锁。

多 pod 部署时的行为：每个 pod 仍会注册全部 cron（注册动作本身无副作用），但同一时刻
只有一个实例真正执行任务。启动时还会顺带清理已过期的锁行。

剩余待改进项：暂停/删除任务目前只能中断「收到请求的那个 pod」的在途执行（见第 2 类
场景表）。后续可以把停止标记落到数据库，让所有实例都能感知并中断。
