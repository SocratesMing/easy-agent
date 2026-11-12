# 自研 `AsyncMySQLSaver` 技术方案

> 版本：v5（方案稿，不含代码）｜ 日期：2026-11-11 ｜ 基线分支：`feat/guocl-1021-0728`
> 环境约束（已确认）：MySQL **8.0.25**（与业务库同库）、Redis **4.0**、无 MongoDB、无 PostgreSQL
> 参照实现：官方 `langgraph-checkpoint-sqlite` 3.1.2、官方 `langgraph-checkpoint-postgres` 3.2.0
> 运行时（已核对实际安装）：`langgraph-checkpoint 4.3.0`、`langgraph 1.2.x`、`deepagents 0.7.29`
>
> **v2 变更**（按你的 6 个问题修订）：① 明确 `record_ttl_days` 的完整语义（§5.7.1，含"整 thread 删除"而非删行）；② 定稿同库共用（§8.2）；③ `auto_setup` 改为默认开启并给出 `GET_LOCK` 流程（§8.2.1）；④ 定稿"不做迁移脚本"及切换清单（§9.1.1）；⑤ 解释 `EASY_CHECKPOINT_BACKEND` 的语义变更（§13.2）；⑥ P1+P2 同版本交付，并说明 delta 优化到底在解决什么（§5.5、§10）；修正 `prune` 签名为批量 `thread_ids`（§5.7）；新增 R21~R24。
>
> **v3 变更**（按你的 4 个问题 + 一轮自查）：⑦ 新增 §14「跨 Pod 并发与一致性」——回答"刷新/重登换 Pod 会不会互相覆盖"，并列出**去掉粘性会话后暴露的 4 个真实缺口**（其中 3 个是 P0 必改，见 §14.4）；⑧ 新增 §15「删除旧快照后端」——`agent_checkpoints` 未上线，`PersistentMemorySaver` 整条链路直接删掉（§15）；⑨ 新增 §16「后端命名」——`mysql_saver` 定稿，`mysql` 作废弃别名（§16）；⑩ 新增 §17「回滚 sqlite」——回滚只需改一个环境变量，历史靠 `plan_turn` 水合恢复（§17）；⑪ 新增 §18「开发初期必须解决的坑」——22 条，逐条给"如果不解决会在什么时候、以什么现象爆炸"。
> 另修正 v2 中 5 处经复核不准确的地方：`asetup` 不是基类契约（§5）、"取最新"应改用 `checkpoint_id`（§5.3）、容量估算低估 5~10 倍（§4.5）、`agent_checkpoints` 的删除范围要绕开 `SqlStore`（§15.2）、"孤儿 blob 率为 0"这个验收判据不成立（§11）。
>
> **v4 变更**（本轮 4 个结构问题）：⑫ 定稿 `record_ttl_days=0`（本期不开启，先观察真实增量，§5.7.1/§13.1）；⑬ 迁移表**不取消，但改型**为 `cp_checkpoint_meta`（KV 表，同时承担"迁移版本号"和"`ns_hash` 算法标识"，理由见 §4.4）；⑭ **全部 4 张表补全表注释与列注释**，并给出注释的三条硬约束（连接字符集、`SET NAMES`、`MODIFY COLUMN` 会重写整个列定义，§4.3.2）；⑮ 新增 §4.6「能否再精简」——结论是**不能再减到 3 张**，给出两个"看起来能合并"的候选逐个否决，另附理论合并版的完整结构与其 6 条代价，供你判断。
>
> **v5 变更**（同事实测反馈 + 你的两条落地要求）：⑯ 新增 §2.1「会话线程为什么会被关掉」——把"改后端就把会话线程关掉"定位到 `session_thread_in_use` 一行判据（`sqlite_checkpointer_active()`），并列出**三处同类判据**（`thread.py:56` / `core.py:184` / `compaction.py:45,52`）与**三个会被唤醒的休眠分支**（§2.1.4）；⑰ **更正上一版一处认知**：手动压缩**不需要新增分布式锁**，它已经在 `try_acquire_session_stream` 的跨 Pod 租约里，要做的只是放开判据 + 加一次乐观校验（§2.1.3）；⑱ 新增 **§19 可观测性设计**（16 个必打日志点、errno→建议映射、审批内容禁记、噪声控制）；⑲ 新增 **§20 发版手册**（12 项前置检查 / 8 步发版操作 / 9 条日志告警阈值 / 回滚步骤）；⑳ 新增 **§21 场景测试方案**（14 个用例、三种"造两台"的方法、四个最容易测错的用例展开、判定硬标准）；㉑ 新增 **§22** 明确"跨进程配置缓存失效"不在本次范围及其理由。

---

## v5 导读：本轮 3 个问题的结论

| # | 你的输入 | 结论 | 详见 |
|---|---|---|---|
| 1 | 同事实测：一启用 `mysql` 会话线程就被关掉 | **属实，且是本方案的准入条件**。根因是 `session_thread_in_use` 用 `sqlite_checkpointer_active()`（即 `_sqlite_saver is not None`）当判据——问的是"有没有那个 `.db` 文件"，不是"saver 有没有官方协议语义"。同一个错误口径共 **3 处**，必须一起改，否则会话线程关闭后本次改造等于白做 | **§2.1**、§7.2 第 6/6b 项 |
| 2 | 可观测性要求（异常日志 + 关键节点 + `session_id`） | 现有日志格式 `%(asctime)s - %(name)s - %(levelname)s - %(message)s` **不带会话上下文**，必须在消息里手写；现存 87 处只打 `session_id[-5:]`（末 5 位），不足以定位。给出了 16 个必打点、固定 kv 顺序、errno→处理建议映射，以及"审批内容禁记"等两条红线 | **§19** |
| 3 | 发版前/中/后要做什么 | 给出可直接照做的三张表：**前置 12 项**（含 DDL 权限、`max_allowed_packet`、在途审批盘点、无活跃流）、**发版 8 步**（含"`hydrate` → `continue` 是唯一能自证共享生效的动作"）、**发版后 9 条日志告警阈值** + 4 个主动验证场景 + 回滚 5 步 | **§20** |
| 4 | 这个场景怎么测 | 14 个用例（T1~T14），其中 **T1/T2/T3/T5/T6 是必过项**；给了三种"造两台"的方法（两进程 / compose+nginx / k8s 摘粘性）与各自必设的环境变量，以及"审批恢复必须看日志里的 `instance=` 是不是两个不同名字"这类防假通过的判据 | **§21** |

---

## v4 导读：上一轮 4 个问题的结论

| # | 你的问题 | 结论 | 详见 |
|---|---|---|---|
| 1 | `record_ttl_days` 本期是否开启 | **不开启，默认 0**。本期只上"已删会话对账"（零风险），按 1 周 / 1 月 / 3 个月三个时点观察 `cp_*` 表真实增量，再决定是否启用 | §5.7.1、§13.1 |
| 2 | `checkpoint_migrations` 是不是可以不用建了 | **技术上可以省，但建议保留——改成 KV 形态的 `cp_checkpoint_meta`**。理由不是"以后要演进"，而是**它还要存 `ns_hash` 算法标识**，这个值一旦和代码不一致，读出来的 `channel_values` 会静默错位。详见 §4.4 | **§4.4** |
| 3 | 表加 table/column 注释 | **已全部补齐**（4 张表 + 每列），并写明三条容易踩的约束：① 建表连接必须是 `utf8mb4`，否则中文注释入库即乱码；② SQL 脚本开头必须 `SET NAMES utf8mb4;`（DBA 用命令行执行时默认字符集常不是 utf8mb4）；③ 改列注释只能用 `MODIFY COLUMN`，**必须重写完整列定义**，漏写 `NOT NULL` 就把约束悄悄改掉了 | §4.3、§4.3.2 |
| 4 | 还能不能再精简 | **不能减到 3 张以内**。`checkpoints` / `blobs` / `writes` 三者是三种不同的**写粒度**：checkpoint 是"快照头"、blobs 是"多版本共享的值"、writes 是"任务级的中间结果"。合并任意两张都会退化（写放大 / 表变宽 / 查询必须带类型过滤）。理论合并版的完整 DDL 与 6 条代价列在 §4.6.3，结论是不建议 | **§4.6** |

---

## v3 导读：你的 4 个问题 → 结论与位置

| # | 你的问题 | 一句话结论 | 详见 |
|---|---|---|---|
| 1 | 刷新 / 重登到不同 Pod，读写会不会互相覆盖 | **读没问题**（消息、流事件、流状态都在业务库，`/stream/live` 本来就是 DB-first 跨 Pod 可回放）；**只有一个写冲突是真问题**——"老回合还在跑、新客户端又发一条"。它今天靠进程内判断拦不住，去掉粘性后会变成"消息发出去了但没有回复，且可能把上一条的流渲染到这一条上"。必须在落库前改成查库 409 | **§14** |
| 2 | `agent_checkpoints` 不用管 | 已确认未上线 → **整条旧快照链路删除**（`PersistentMemorySaver` + 3 个 SQL + 建表 + 启动校验分支，约 310 行生产代码）。注意 `AgentStateRepository` 不能整个删——它还兼着 `SqlStore` 的 DB 句柄 | **§15** |
| 3 | 后端名能不能叫 `mysql_saver`，`auto` 也默认走它 | **可以，就这么定**。`mysql_saver` = 新实现（默认）、`sqlite`、`legacy`、`auto`；`mysql` 保留为**废弃别名**（映射到 `mysql_saver`，打 WARN），防止 ConfigMap 里还写着 `mysql` 的旧部署启动失败 | **§16** |
| 4 | 出问题改回 sqlite，要保证原来那条路子可用 | **能回滚，但有两个前提**：① 不要删 Ingress 的粘性 annotation（只解除"依赖"，删了 annotation 回滚时启动校验会直接失败）；② 回滚后历史不丢——`plan_turn` 会从业务库水合，只有**在途审批**失效 | **§17** |
| — | 还有没有别的坑 | 27 条（v5 补了"会话线程被判据关掉"、短 session 日志、压缩乐观校验三条），按"必须现在解决 / 上线前解决 / 观察项"分级 | **§18** |

---

## 0. 结论摘要

**方案：自研 `AsyncMySQLSaver`（新模块 `easy_agent/persistence/mysql_saver.py`），采用"3 表 + 内联标量"模型（照抄 PG，不照抄 SQLite），驱动用 `aiomysql`（纯 Python，aio-libs 维护，2025-10 刚发版），后端名 `mysql_saver` 成为默认；旧的业务库快照实现（`PersistentMemorySaver` + `agent_checkpoints`）**整条删除**——该代码 2026-11-06 才合入、从未上线。**

| 项目 | 结论 |
|---|---|
| 新增依赖 | **1 个**：`aiomysql>=0.3.2`（其依赖 `PyMySQL` 项目已有） |
| 新增配置 | `config.yaml` 新增 `checkpoint.mysql` 段（13 项，全部有默认值）+ `.env` 可选覆盖 3 项 |
| 新增代码 | 核心约 **600~700 行**（7 个接口方法）+ delta/TTL 约 **150 行**（P2）+ **改造点 13 处约 240 行**（含 P0-c 并发互斥、三处"会话线程判据"）+ 结构化日志约 **80 行**（§19） |
| 删除代码 | **约 310 行生产代码**（旧快照后端整条链路）+ 3 个测试文件整体删除——不是"保留为逃生门"，见 §15 |
| 数据库对象 | 4 张表（`cp_checkpoint_meta` / `cp_checkpoints` / `cp_checkpoint_blobs` / `cp_checkpoint_writes`），前缀可配，**全部带表注释与列注释**；**与业务库同库**（已确认）。**不能再精简到 3 张**，理由见 §4.6 |
| 建表方式 | `auto_setup: true` 启动时自动建表（`GET_LOCK` 串行化）；另有 SQL 脚本作审计基线（已确认） |
| 是否改业务表 | **删一张从未使用的 `agent_checkpoints`**（`SCHEMA_VERSION` 16→17），其余业务表不动 |
| 后端名 | `mysql_saver`（新，默认）/ `mysql`（废弃别名，映射到 `mysql_saver`）/ `sqlite` / `legacy` / `auto` |
| 会话线程 | **必须保持开启**（`thread_id = session_id`）。这是本次改造的**准入条件**：`session_thread_in_use` 现在拿 `sqlite_checkpointer_active()` 当判据，换后端就会把线程关掉（§2.1） |
| 达到的目标 | 会话线程继续开着 ✅、跨 Pod 恢复 HITL ✅、去掉粘性依赖 ✅（annotation 可保留可摘）、扩缩容/Pod 重建无感 ✅、默认 mysql ✅ |
| 可观测性 | 16 个必打日志点、统一 kv 顺序、errno→处理建议映射、审批内容禁记（§19） |
| 发布与测试 | 前置 12 项检查 + 发版 8 步 + 9 条告警阈值（§20）；14 个用例，T1/T2/T3/T5/T6 必过（§21） |
| 实施节奏 | **P1 + P2 同版本发布**（约 9~11 人日，v5 增加：三处判据 + 结构化日志 + 端到端脚本），不做"先慢后快"的中间态 |

**与"用社区包"的本质差别**：不是"装个包改个配置"，但也不是从零造轮子——官方 PG 实现已经把 90% 的语义定死了（3 表模型、标量内联、blob 去重、writes 的两种冲突策略），本方案是"把 PG 的 SQL 翻成 MySQL 方言 + 修掉社区包踩过的 5 个坑"。

---

## 1. 目标、约束与不做什么

### 1.1 必须达到（对应你的 1/2/3 条）

| # | 目标 | 本方案如何满足 |
|---|---|---|
| 1 | 任意 Pod 都能恢复 HITL 审批 | checkpoint 落在共享 MySQL，`aget_tuple` 与 Pod 身份无关 |
| 2 | 去掉粘性会话依赖 | 部署校验对 `mysql_saver` 直接放行，不再要求 `{pod}` 路径与 `EASY_STICKY_SESSIONS`（annotation 可保留，见 §17.2） |
| 3 | 扩缩容 / Pod 重建不影响恢复 | 状态不在本地磁盘，进程重启后从库读；`adelete_thread` 一次删除全局生效 |
| 4 | 同一会话不会有两条流互相覆盖 | **v3 新增**：并发互斥从"进程内"改成"查库 + 落库前"，见 §14.4 |
| 5 | **会话线程必须继续开着**（`thread_id = session_id`） | **v5 新增**：换后端不能改变这个行为。`session_thread_in_use` 等三处判据要改成"当前 saver 是共享的官方协议实现"，见 §2.1 / §7.2 第 6、6b 项。**这条不满足，1~4 全部无意义** |
| 6 | 出问题能在日志里直接定位到会话 | **v5 新增**：16 个必打点 + 全量 `session_id` + errno→建议映射，见 §19 |

### 1.2 明确不做

- **不做** PG / Redis / MongoDB 适配（Redis 4.0 不满足 Redis 方案要求的 8.0+，本次彻底排除）。
- **不删** 官方 SQLite 后端与 `legacy`（进程内 `MemorySaver`）——它们分别是"回滚逃生门"与"调试用"，见 §17。
- **要删** `PersistentMemorySaver`（旧业务库快照）与 `agent_checkpoints` 表——该代码从未上线，见 §15。这是 v3 相对 v2 的转向：v2 把它当"逃生门"保留，既然没上线就不用留。
- **不并入** 业务 `SCHEMA_VERSION`（checkpoint 的 4 张表由 saver 自己的元数据表 `cp_checkpoint_meta` 管理；但"删 `agent_checkpoints`"这一步走业务 `SCHEMA_VERSION`，见 §15.2）。
- **不做** checkpoint 归档到冷存储、不做跨库分片。
- **不做** 旧快照（`agent_checkpoints`）到新表的自动迁移，也不做"标记失效"脚本（已确认，见 §9.1.1）。
- **不做** `prune(strategy="keep_latest")`（会切断 `DeltaChannel` 快照链，见 §5.7）。
- **不做** 跨进程的配置缓存失效（技能 / 连接器 / 专家）——这与会话线程是两件事，理由见 §22。

### 1.3 环境与已确认的前提

MySQL **8.0.25** 是 2021 年的版本，本项目要用的特性都在支持范围内（JSON、`ON DUPLICATE KEY UPDATE` 行别名、`ROW_FORMAT=DYNAMIC`、`utf8mb4_bin`、递归 CTE 均 ≥8.0.19）。

**已确认的前提**（本方案按此设计，不再作为待确认项）：

| 前提 | 影响 |
|---|---|
| 生产应用账号**有 DDL 权限** | `auto_setup: true` 成为默认，启动时自动建表（§8.2.1） |
| checkpoint 表**与业务库同库** | 复用连接信息与备份；需关注连接数、buffer pool、binlog（§8.2） |
| **接受切换时放弃在途审批** | 不做迁移脚本（§9.1.1） |
| **P1 + P2 本期一次到位** | delta 优化与 TTL 能力同期交付（§10） |

> **本文的 DDL 已在真实 MySQL 上跑通验证**（本机 8.0.46，独立临时库 `cp_ddl_probe_20261111`，建表→写入→清理，业务库未动）。8.0.25 与 8.0.46 在本方案用到的语法上没有差异，但**上生产前仍需在 8.0.25 上执行一遍 `scripts/db/mysql-checkpoint-ddl.sql`**，把"两版本行为一致"变成事实而不是推断。

---

## 2. 现有实现的缺陷 → 新方案如何修正（对应你的第 4 条）

现有 `PersistentMemorySaver`（`checkpoints.py:343`）是"每轮把整个 `MemorySaver` 全量 pickle 进业务库"，多副本下有三个硬上限和一个结构性缺陷。

> **v3 说明**：这一节保留是作为"为什么不能沿用"的复盘依据。由于该实现（2026-11-06 合入）**从未上线**，本次不再"修"它，而是**整条删除**（§15）。下面第 5~8 行是"社区包踩过、我们也必须避开"的坑，仍然有效。

| # | 现有缺陷 | 后果 | 新方案 |
|---|---|---|---|
| 1 | `_PERSIST_MAX_THREADS = 3`，只保留最近 3 个 thread | 早一轮的审批状态被裁掉，恢复时"找不到审批" | 不裁剪；按 thread 维度无限保留，由 GC/prune 按策略删 |
| 2 | `_PERSIST_MAX_BYTES = 12MB`，超限**静默跳过** | 工具结果大的会话，HITL 快照直接不落库 | 无总量上限；单文档上限由 `max_allowed_packet` 决定，超限**报错而非静默** |
| 3 | 每轮全量序列化整个 saver 的 storage | 写放大 O(轮数²)，长会话越来越慢 | 只写**本次新增/变更的 channel**（PG 的 `new_versions` 语义） |
| 4 | 用 `pickle` 存业务库单列 | 跨版本反序列化脆弱、无法按 thread 查询/删除 | 用官方 `JsonPlusSerializer` 的 typed 序列化，`(type, bytes)` 分列存 |

另外四项"社区包踩过、我们必须避开"的坑：

| # | 坑 | 来源 | 本方案对策 |
|---|---|---|---|
| 5 | 表名硬编码、无法加前缀 | 社区 mysql 包（issue 2025-05 至今未合） | 表名前缀可配置，默认 `cp_` |
| 6 | 默认 collation 大小写不敏感，`ABC` 与 `abc` 共用一条历史 | 社区 mysql 包 open issue 2026-10-30 | 全部 checkpoint 表显式 `COLLATE utf8mb4_bin` |
| 7 | 索引键塞进 `checkpoint_ns` 原值，靠 `MD5()` 生成列 | 社区 mysql 包（MySQL 9.6 起 MD5 生成列废弃） | **应用层算 SHA-256 前 16 字节**存 `BINARY(16)`，不依赖 DB 函数与版本 |
| 8 | 无 `aprine`、无 TTL，checkpoint 只增不减 | 社区 mysql 包 / MongoDB 包都有此缺口 | P2 实现 `aprine`（仅 `delete`）+ 对账 GC 改为库内 JOIN + `record_ttl_days`（默认关闭），见 §5.7 / §5.7.1 |

### 2.1 会话线程为什么会被关掉（你同事踩到的这个问题，是本方案的准入条件）

> 这一段是 v5 补的。它把"能不能跨 Pod 恢复 HITL"从一个"性能问题"变成了"功能是否成立"的问题：**如果会话线程被关掉，后面所有努力都不产生任何效果**——每条消息一个 thread 时，跨 Pod 恢复的语义仍然存在（任一 Pod 都能读到那个 thread），但每轮都要从 DB 重建历史、上一轮的中断挂在上一轮的 thread 上，恢复路径完全不同，且 §5 的 TTL/GC/压缩全部按多 thread 假设运行。
>
> 结论先给：**判据本身写错了口径**。代码把"官方 `AsyncSqliteSaver` 已打开"当成了"当前 saver 具备 LangGraph 官方协议语义"，而这两件事在 MySQL 上没有必然联系。

#### 2.1.1 直接原因：一个 `is not None` 决定了会话线程开不开

```python
# services/sessions/thread.py:56
def session_thread_in_use(config) -> bool:
    from easy_agent.persistence.checkpoints import sqlite_checkpointer_active
    return session_thread_enabled(config) and sqlite_checkpointer_active()

# persistence/checkpoints.py:517
def sqlite_checkpointer_active() -> bool:
    return _sqlite_saver is not None
```

`_sqlite_saver` 只在 `open_sqlite_checkpointer()` 成功时被赋值。所以：

| 后端 | `_sqlite_saver` | `session_thread_in_use` | 实际行为 |
|---|---|---|---|
| `auto` / `sqlite`（**生产现状**） | 非 None | **True** | 一个会话一个 thread ✅ |
| `EASY_CHECKPOINT_BACKEND=mysql`（旧快照，你同事试的） | None | **False** | 每条消息一个 thread ❌ |
| `legacy` | None | False | 每条消息一个 thread ❌ |

**这就是"一启用 mysql 就把线程关掉"的全部原因**——不是因为 MySQL 不能做会话线程，而是因为判断"能不能"的那句话问的是"有没有那个 `.db` 文件"。

同一个错误口径在 agent 侧还有第二份（两道闸，任一道关都会退回）：

```python
# services/sessions/thread.py:69
def session_thread_active(agent) -> bool:
    if not session_thread_enabled(...): return False
    checkpointer = getattr(getattr(agent, "agent", None), "checkpointer", None)
    if checkpointer is None: return False
    if isinstance(checkpointer, PersistentMemorySaver):   # 旧快照 → 关
        ...警告...
        return False
    return True
```

旧 `mysql` 后端的 checkpointer 恰好就是 `PersistentMemorySaver`，于是两道闸同时关闭。**我们新建的 `AsyncMySQLSaver` 不是这个类，也不是 None，所以第二道闸天然放行**；真正要改的是第一道（§7.2 第 6b 项）。

#### 2.1.2 三处判据，都要改成"当前 saver 是不是共享的官方协议实现"

| # | 位置 | 现在的判据 | 应改成 | 不改的后果 |
|---|---|---|---|---|
| 1 | `thread.py:56` `session_thread_in_use` | `sqlite_checkpointer_active()` | `shared_checkpointer_active()`（`sqlite` 或 `mysql_saver`） | **会话线程关闭**，本方案失去意义 |
| 2 | `agent/core.py:184` `_shared_graph_checkpointer` | 同上 | 同上 | 退回"每会话编译一张图"，**进程内图缓存全废**（内存与冷启动双双变差，且不报错） |
| 3 | `compaction.py:45` `supported()` / `:52` `records()` | `sqlite_checkpointer_active() and detect_worker_count()==1` | `shared_checkpointer_active()`，**去掉 worker 数限制** | 手动压缩按钮点了报 409；历史压缩记录读不出来 |

`shared_checkpointer_active()` 的实现就是"确认存在一个进程级共享、按 `thread_id` 分会话、具备完整协议语义的 saver"：

```python
def shared_checkpointer_active() -> bool:
    return _sqlite_saver is not None or _shared_mysql_saver is not None
```

> 为什么不直接叫 `checkpointer_active()`：语义必须**排除** `PersistentMemorySaver`（按 session 建、进程内快照）与 `MemorySaver`（纯内存）。名字带 `shared` 是为了让后来的人不会顺手把 legacy 也塞进来。

#### 2.1.3 手动压缩的互斥：**锁其实已经在了**（对上一版认知的更正）

你说"压缩要包进已有的分布式锁"——核了代码，**它已经在锁里了**，只是被两个判据挡在门外，让人误以为"没锁"：

```python
# services/sessions/compaction.py:122  _execute() 里
acquired, _ = await run_blocking(
    runtime.try_acquire_session_stream, sid, owner, until, now.isoformat(timespec="seconds"))
if not acquired:
    raise HTTPException(409, "会话正在生成或压缩，请稍后重试")
```

`try_acquire_session_stream` 是对 `session_runtime` 表行的**原子条件更新**（`db/repositories/session_runtime.py:28`），跨 Pod 生效；压缩与普通对话**抢的是同一行、同一把锁**（owner 都按 `session_id` 记），而且压缩期间还有 8 秒一次的心跳续约。所以：

- 两台 Pod 同时点压缩 → 一台拿到租约、另一台 409，**不会同时压**；
- 压缩与对话并发 → 同样互斥；
- 唯一残留的问题是：**这台 Pod 拿到租约后才发现"另一个 Pod 已经往这条 thread 追加了内容"**——租约保证的是"同一时刻只有一个写者"，不保证"我读到的是最新"。

因此压缩改造是 3 件事，**不是**加一把新锁：

| # | 改动 | 原因 |
|---|---|---|
| ① | `supported()` / `records()` 的判据改成 `shared_checkpointer_active()`，去掉 `detect_worker_count()==1` | 否则共享后端下压缩直接 409，功能消失 |
| ② | 压缩提交前**重读一次最新 checkpoint_id 并做乐观校验**（与开始时读到的不一致就 409 让用户重试） | 租约只保证互斥，不保证"我基于最新状态压缩"；跨 Pod 后这个窗口比单机大 |
| ③ | 压缩结果的 `request_id` 去重（`records()` 里已有）保持 | 客户端 120s 超时重试会打到另一台 Pod，必须靠它保证幂等 |

> 注意 `detect_worker_count()` 读的是 `EASY_REPLICA_COUNT/WEB_CONCURRENCY/UVICORN_WORKERS`，**不是副本数**。它当初的含义是"同一进程里有几个 worker 共用同一个 sqlite 文件句柄"。共享 saver 之后这个限制没有理由了，所以是"去掉"而不是"改小"。

#### 2.1.4 开启会话线程后，三个**休眠分支**会被唤醒（这是最容易漏测的地方）

生产现在跑 `auto` → sqlite，所以会话线程本来是**开着**的；但只要你同事把后端改成 `mysql`（旧快照）测试，这三条路径就被静默关掉了。切到新 saver 后它们会**重新生效**，需要专门测：

| 分支 | 位置 | 什么时候执行 | 关着时的表现（容易被当成正常） |
|---|---|---|---|
| 新消息丢弃上一轮挂起的审批 | `chat_turn.py:728`、`experts/runtime.py:346` → `abandon_pending_approval` | 会话线程开启时，每条新用户消息 | 上一轮的"待审批"徽标**一直挂着不消失**，用户以为还能点 |
| 审批句柄兼容核对 | `chat_turn.py:400` `_last_session_message` | 恢复审批时核对旧格式句柄 | 升级前挂起的旧审批永远恢复不了（本来也恢复不了，但原因不同） |
| 手动压缩 | `compaction.py` 全链路 | 用户点"压缩上下文" | 直接 409，用户以为功能没做 |

#### 2.1.5 老 SQLite 文件不用迁移：靠的是"thread 为空就水合"，而且水合是**在锁里**做的

```python
# services/sessions/thread.py:189-192  plan_turn()
state = await agent.agent.aget_state(build_run_config(agent, session_id))
thread_messages = list((state.values or {}).get("messages") or [])
if not thread_messages:
    return TurnPlan(session_id, rebuilt(history), "hydrate")   # 从 session_messages 重建
```

三种模式（`mode` 字段会打进日志，见 §20）：

| `mode` | 触发条件 | 行为 |
|---|---|---|
| `hydrate` | thread 为空（新会话 / **刚切后端** / checkpoint 被删） | 用 `session_messages` 重建一次历史写入 thread |
| `rehydrate` | thread 非空，但它最后一轮在 DB 里找不到 | 先 `delete_session_thread` 再重建（防止两份历史分叉） |
| `continue` | 正常续用 | 只补 DB 里比 thread 新的那几轮 |

**关键时序**（这一点决定了"两台同时首问"不会各写一份历史）：

```
api/chat.py:_drive_stream
  └─ aregister_stream_task(session_id, bg)      ← 先拿 DB 流锁（跨 Pod 原子）
       └─ async for chunk in gen:               ← 之后才进 orchestrator
            └─ plan_turn() → hydrate（写 thread）← 已经在锁里
```

所以"两台 Pod 同时收到同一会话的第一条消息"时，第二台会在拿锁时被拒（`SessionBusyError`），不会出现两份水合历史。**但前提是那个拒绝要能被用户看见**——现在的实现是 `queue.put_nowait(None)`，客户端拿到空流（§14.4-B，P0-c 必修）。这是"水合安全"与"用户体验"两件事，别混为一谈。

#### 2.1.6 与本次改造无关、但必须写进发布说明的一件事：缓存不跨进程

技能目录、连接器、专家列表的缓存仍在各自进程里（`services/skills/library.py:233` 的 `_ref_index_cache` / `_user_ref_index_cache`，以及 agent 缓存驱逐）。后果是：在 A 台改了技能/专家配置，B 台要等它自己的缓存过期（或那次请求触发的 mtime 变化）才看得到。

这与"会话线程"是两件独立的事——**checkpoint 共享解决的是"状态能不能接着跑"，缓存解决的是"配置能不能立刻看到"**。本次不做跨进程失效通知（那需要一条 pub/sub 或一个版本号轮询，是另一个改造）。写进文档的理由是：上线后一定会有人报"我在另一台看不到刚装的技能"，需要一句话能答上来。详见 §23。

---

## 3. 官方两个实现的对照，以及为什么这么选

### 3.1 两者的结构性差异

| | SQLite（官方） | PostgreSQL（官方） | 本方案 |
|---|---|---|---|
| 表数 | 2（checkpoints / writes） | 3（+ checkpoint_blobs） | **3 + 1 元数据表（KV）**，见 §4.6 |
| channel 值存放 | **全部内联**在 checkpoint 的 BLOB 里 | 标量内联在 JSON，其余进 blobs | **同 PG** |
| 同一 channel 同一 version | 每个 checkpoint 各存一份 | blobs 表按 `(thread, ns, channel, version)` 去重，多 checkpoint 共享 | 同 PG |
| 读取往返 | 1 次（writes 再 1 次） | 1 次（子查询 + `jsonb_each_text` 关联） | **2~3 次**（MySQL 无等价 JSON 展开） |
| version 生成 | 32 位递增 + 16 位随机后缀 | 同 | 同（必须覆写基类的纯 int） |
| delta 优化 | `_delta.py` 两阶段 CTE | `base.py` 两阶段 + `JSONB` 展开 | P2 实现，第一阶段先用"应用层合并" |
| 裁剪 | 无 | `prune` 两种策略（keep_latest / delete） | P2 实现 `delete` 策略 |

### 3.2 三个关键取舍

**取舍一：为什么不用 SQLite 的"全内联"（更简单）？**
SQLite 单机单文件，重复存一份 channel 值成本可忽略。MySQL 多副本共享时，`messages` 这类 channel 每轮都在长，内联意味着**每个 checkpoint 都要重存整个消息数组**——这正是现有实现 O(轮数²) 的病根。PG 的 blobs 按 version 去重，让"没变的 channel 不重写"，是必须照抄的。

**取舍二：为什么不照抄 PG 的"一条 SQL 取全"？**
PG 靠 `jsonb_each_text(checkpoint -> 'channel_versions')` 与 blobs 做 join。MySQL 的等价物是 `JSON_TABLE`（8.0 支持），但可读性与可维护性差很多，且优化器对 JSON 函数的选择率估计很悲观，容易退化成全表扫。
**决定**：拆成 2~3 次参数化查询（`checkpoints` → `blobs`（`IN` 批量按 `channel+version`）→ `writes`），预估单轮往返开销本机 <3ms、跨网络 5~15ms，相对 LLM 秒级延迟可忽略。这是"用可维护性换一点延迟"的明确取舍。**这个数字要等 P1 集成测试实测确认，不是当前结论。**

**取舍三：`checkpoint` 列用 MySQL `JSON` 还是 `BLOB`？**
用 **`JSON`**。理由：P2 的 delta 实现需要在 SQL 层判断"某个祖先的 `channel_values` 里是否已有该 channel"（PG 靠这一点避免反序列化整条 blob）。JSON 列可用 `JSON_CONTAINS_PATH(checkpoint, 'one', '$.channel_values.messages')` 直接判断，省掉把祖先全量读出来反序列化的开销。

---

## 4. 数据模型

### 4.1 命名与前缀

- 表名统一 `{prefix}` 前缀，默认 `cp_`，可配 `checkpoint.mysql.table_prefix`。
- 所有表 `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC`。
- **`utf8mb4_bin` 是硬要求**：thread_id / checkpoint_ns 是字符串主键的一部分，业务库默认的 `utf8mb4_unicode_ci` 会让 `ABC` 与 `abc` 命中同一行（社区包真实 bug）。

### 4.2 为什么主键里放 `checkpoint_ns_hash` 而不是 `checkpoint_ns`

`checkpoint_ns` 是子图命名空间，形如 `tools:<uuid>|model:<uuid>|...`，**嵌套层数不确定，长度不可控**。MySQL 的索引键长度上限是 3072 字节（DYNAMIC 行格式、utf8mb4 每字符 4 字节），把 `thread_id + checkpoint_ns + checkpoint_id` 三个原值直接做主键，256 字符的 ns 就会顶到上限，而且**超长时是插入报错，不是截断**——这会变成一个只在深嵌套场景才复现的偶发故障。

方案：**应用层计算 `sha256(checkpoint_ns)[:16]`，存 `BINARY(16)` 参与主键；原值另存 `MEDIUMTEXT` 普通列。**

- 128 bit 哈希，碰撞概率可忽略；
- 所有查询**同时带 `checkpoint_ns = ?` 作为额外过滤条件**，即使真碰撞也不会读错数据（hash 只用于走索引）；
- 不用 `MD5()` 生成列，因此不受 MySQL 9.x 废弃影响，也不需要建表时的 `GENERATED` 权限；
- 原值用 `MEDIUMTEXT` 而非定长 `VARCHAR`：**已实测 `VARCHAR(768)` 在写入 769 字符时直接报 `Data too long`（不是截断）**，而 ns 的嵌套深度由运行时决定，属于不可控输入。`MEDIUMTEXT` 只在行内占 20 字节指针，彻底消除这个上限。

### 4.3 DDL（4 张表，全部带表/列注释）

> 4 张表由 `auto_setup` 在启动时**自动创建**（默认 `true`，生产账号已确认有 DDL 权限，见 §8.2.1）。`scripts/db/mysql-checkpoint-ddl.sql` 是**同一份 DDL 的审计基线**，内容必须与代码里的迁移常量逐字一致（单测里做字符串比对，防止两边漂移）。
>
> **注释是必填项，不是锦上添花**：这张库跟业务库共用，DBA 和运维在 `information_schema` 里看到 `cp_` 表时必须能自解释——否则"cp_checkpoint_writes 和 cp_checkpoint_blobs 有什么区别、能不能清"这种问题会反复问到开发。注释占位成本是 0（表注释上限 2048 字符、列注释 1024 字符），写错人就是纯损失。

**① 元数据表（KV）**——存 DDL 版本号与 `ns_hash` 算法标识

```sql
CREATE TABLE IF NOT EXISTS {p}checkpoint_meta (
  meta_key   VARCHAR(64)  NOT NULL COMMENT '元数据键：schema_version / ns_hash_algo / ddl_applied_at',
  meta_value VARCHAR(255) NOT NULL COMMENT '元数据值；schema_version 为递增整数串，ns_hash_algo 形如 sha256-16',
  updated_at DATETIME(6)  NOT NULL COMMENT '最后更新时间（UTC，应用显式写入；不用 DEFAULT CURRENT_TIMESTAMP）',
  PRIMARY KEY (meta_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='checkpoint 存储自身的元数据（KV）：DDL 版本号、checkpoint_ns 哈希算法标识。业务代码不读它，仅供 asetup 与运维排查';
```

- 为什么从 v3 的 `cp_checkpoint_migrations(v INT)` 改成 KV：见 §4.4。**这一改动不影响三张业务表**，是纯增量。
- 只有 3~5 行数据，全表 < 1 KB。`asetup` 读它是 `SELECT meta_value FROM ... WHERE meta_key='schema_version'`，命中主键。

**② checkpoint 主体**（`channel_values` 只保留标量，其余移到 blobs；三列均不可为空）

```sql
CREATE TABLE IF NOT EXISTS {p}checkpoints (
  thread_id            VARCHAR(128) NOT NULL COMMENT '线程 id：<session_id>（会话级）或 <session_id>-<message_id>（单条消息级），定时任务以 sched- 开头',
  session_id           VARCHAR(64)  NOT NULL COMMENT '会话 id 冗余列，写入时由 session_id_of_thread() 解析；供 GC/TTL 直接 JOIN，避免全表拉回应用层解析',
  checkpoint_ns_hash   BINARY(16)   NOT NULL COMMENT 'checkpoint_ns 的 sha256 前 16 字节，仅用于索引定位（ns 由运行时决定、长度不可控，原值无法进主键）',
  checkpoint_ns        MEDIUMTEXT   NOT NULL COMMENT '子图命名空间原值，形如 tools:<uuid>|model:<uuid>|...；与 hash 同时作为查询过滤条件，保证 hash 碰撞时也不读错',
  checkpoint_id        VARCHAR(64)  NOT NULL COMMENT 'checkpoint 版本号，UUIDv6（36 字符），字典序≈写入时序，可直接倒序取最新',
  parent_checkpoint_id VARCHAR(64)  NULL     COMMENT '父 checkpoint id；首条为 NULL。delta 回溯与 alist 链式读取沿它上溯，不要用 checkpoint_id 排序代替',
  step                 INT          NULL     COMMENT '该 checkpoint 对应的图执行步号，用于 prune 排序与问题排查',
  channel_versions     JSON         NOT NULL COMMENT '各 channel 的版本号快照（内联自 checkpoint JSON，避免每次读都解析大 JSON）',
  type                 VARCHAR(64)  NULL     COMMENT 'checkpoint 序列化类型标识，langgraph 内部使用',
  checkpoint           JSON         NOT NULL COMMENT 'checkpoint 主体（channel_values 仅保留标量，大值在 checkpoint_blobs 表）',
  metadata             JSON         NOT NULL COMMENT '来源、步号、写入方等元数据（含 langgraph 的 source/steps 等字段）',
  created_at           DATETIME(6)  NOT NULL COMMENT '写入时间（UTC，应用显式写入）。不设 DEFAULT CURRENT_TIMESTAMP：该函数取服务器时区，跨时区 Pod 会混入两个时区的时间',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, checkpoint_id),
  KEY idx_thread_ns_created (thread_id, checkpoint_ns_hash, created_at),
  KEY idx_thread_created (thread_id, created_at),
  KEY idx_session (session_id),
  KEY idx_parent (thread_id, checkpoint_ns_hash, parent_checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='LangGraph checkpoint 主体：一行 = 某 thread 在某命名空间下的一个版本（即"快照头"）。大对象在 checkpoint_blobs，任务级中间结果在 checkpoint_writes';
```

- **`created_at` 不能写 `DEFAULT CURRENT_TIMESTAMP(6)`**（v2 的写法，v3 已改）：`CURRENT_TIMESTAMP` 取的是 **MySQL 服务器会话时区**，容器时区不一致时同一批数据会混进两个时区的时间，`ORDER BY created_at` 直接错乱、TTL 判断也跟着错。做法：连接池 `init_command="SET time_zone='+00:00'"` + 应用层显式写 `datetime.now(UTC)`，列上不设默认值（漏写就是 `NOT NULL` 报错，比静默写错时区好）。
- `KEY idx_thread_ns_created` 是 v3 补的：`aget_tuple(checkpoint_id=None)` 要按 `(thread_id, checkpoint_ns)` 取最新，v2 只有 `(thread_id, created_at)`，`checkpoint_ns` 过滤只能回表。实际取最新走的是主键倒序扫描，这个索引是给 `alist` 的分页与"某 ns 下最近 N 条"兜底用的。
- `idx_thread_created` 保留：GC/TTL 的 `MAX(created_at)` 与运维排查都按它。
- 额外冗余 `channel_versions` 与 `step` 两列（PG 靠从 `checkpoint` JSON 里取，我们提出来）：`channel_versions` 用于 blobs 批量取数与 delta 走链，`step` 用于 `prune` 排序与排查。**这是相对 PG 的改进**——避免每次读都解析大 JSON。
- **额外冗余 `session_id` 列**（首次写入时由 `session_id_of_thread(thread_id)` 解析）：`thread_id` 有两种形式——`<session_id>`（36 字符 UUID）和 `<session_id>-<message_id>`，**没有可用的固定分隔符**（UUID 本身含 `-`），因此在 SQL 里无法可靠地"从 thread_id 取 session_id"。加这一列后，GC/TTL 的对账可以用一条 JOIN 完成，不必把几十万 thread 拉到应用层再逐个解析（R23）。定时任务 `sched-` 的 thread 该列为空串。
- 主键长度：128×4 + 16 + 64×4 = **784 字节**，远低于 3072（`session_id` 不在主键内，不占主键长度）。

**③ channel 值**（按 `(thread, ns, channel, version)` 去重，多个 checkpoint 共享同一份）

```sql
CREATE TABLE IF NOT EXISTS {p}checkpoint_blobs (
  thread_id          VARCHAR(128) NOT NULL COMMENT '线程 id，与 checkpoints.thread_id 同源',
  checkpoint_ns_hash BINARY(16)   NOT NULL COMMENT '命名空间哈希，与 checkpoints.checkpoint_ns_hash 一致',
  channel            VARCHAR(128) NOT NULL COMMENT 'channel 名，如 messages / context_compactions / 各节点输出',
  version            VARCHAR(64)  NOT NULL COMMENT '该 channel 的版本号，取自 checkpoints.channel_versions 对应值',
  type               VARCHAR(32)  NOT NULL COMMENT '值的序列化类型：msgpack（正常值）/ empty（该版本下无值）',
  payload            LONGBLOB     NULL     COMMENT '序列化后的 channel 值；type=empty 时为 NULL。列名不能叫 blob（MySQL 保留字）',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, channel, version),
  KEY idx_thread_ns (thread_id, checkpoint_ns_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='channel 值（大对象）：按 (thread, ns, channel, version) 去重，多个 checkpoint 共享同一份，避免"每步重存整个 messages 数组"的写放大';
```

- `payload` 允许 NULL：`type='empty'` 时表示"该 version 下该 channel 无值"（PG 在 migration 4 专门把 NOT NULL 去掉，原因相同）。
- **列名不能叫 `blob`**：`BLOB` 是 MySQL 保留字，直接用会报 `1064 语法错误`（已在真库踩到）。统一叫 `payload`，全程不要用 `` `blob` `` 反引号方案——一旦某条 SQL 忘了反引号就是运行时错误。
- 主键长度：128×4 + 16 + 128×4 + 64×4 = **1296 字节** ✅

**④ 中间写入**（interrupt / error / 普通写）

```sql
CREATE TABLE IF NOT EXISTS {p}checkpoint_writes (
  thread_id          VARCHAR(128) NOT NULL COMMENT '线程 id，与 checkpoints.thread_id 同源',
  checkpoint_ns_hash BINARY(16)   NOT NULL COMMENT '命名空间哈希，与 checkpoints.checkpoint_ns_hash 一致',
  checkpoint_id      VARCHAR(64)  NOT NULL COMMENT '产生该次写入的 checkpoint id；interrupt 时可能先于该 checkpoint 的 aput 落库',
  task_id            VARCHAR(64)  NOT NULL COMMENT '产生写入的任务（节点）id',
  idx                INT          NOT NULL COMMENT '同一 (checkpoint, task) 内多个 channel 写入的序号；特殊通道（__interrupt__ 等）用固定负值，见 WRITES_IDX_MAP',
  channel            VARCHAR(128) NOT NULL COMMENT 'channel 名；HITL 审批时值为 __interrupt__',
  task_path          TEXT         NOT NULL COMMENT '任务层级路径，与 checkpoint_ns 同源、长度不可控故用 TEXT；TEXT 无字面量默认值，INSERT 必须显式传（空串也要传）',
  type               VARCHAR(32)  NOT NULL COMMENT '值的序列化类型',
  payload            LONGBLOB     NOT NULL COMMENT '写入值。与 blobs 的区别：这是任务级中间结果，HITL 会长期挂在这里等人审批，不会被后续 aput 覆盖',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, checkpoint_id, task_id, idx),
  KEY idx_thread_ns_cp (thread_id, checkpoint_ns_hash, checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='任务级中间写入：interrupt / error / 节点输出。HITL 审批挂起与恢复强依赖本表，禁止单独清理（整 thread 一起删）';
```

- 主键长度：128×4 + 16 + 64×4 + 64×4 + 4 = **1044 字节** ✅
- `task_path` 用 `TEXT` 而非 `VARCHAR`：它与 `checkpoint_ns` 同源（嵌套 task 路径），长度同样不可控；`TEXT` 不支持字面量默认值，**所有 INSERT 必须显式给值**（代码里恒有值，空串也要传 `''`）。

### 4.3.1 验证记录（已在真实 MySQL 执行）

在临时库（库默认 collation 故意设成 `utf8mb4_unicode_ci`，模拟业务库现状）里建表并写入，结果：

| 检查项 | 结果 |
|---|---|
| 4 张表建立 | ✅ 全部成功 |
| 表级 collation 覆盖库默认值 | ✅ 4 张表均为 `utf8mb4_bin` |
| 主键列顺序 | ✅ 与设计一致（`thread_id, ns_hash, checkpoint_id` 等） |
| 主键字节数（784 / 1296 / 1044） | ✅ 远低于 InnoDB 3072 上限 |
| `checkpoint_ns` 768 字符 | ✅ 插入成功 |
| `checkpoint_ns` 769 字符（`VARCHAR(768)` 时） | ❌ 报 `1406 Data too long` —— **这是把原值改为 `MEDIUMTEXT` 的直接依据** |
| `checkpoint_ns` 2000 字符（`MEDIUMTEXT`） | ✅ 插入成功，无上限 |
| `thread_id` 大小写不同是否算两行 | ✅ 算两行（`utf8mb4_bin` 生效，社区包在此处有 bug） |
| 列名 `blob` | ❌ 报 `1064 语法错误`（保留字）→ 改为 `payload` |
| **v2 新增 `session_id` 列 + `idx_session`**（2026-11-11 复验，临时库 `cp_ddl_probe_v2`） | ✅ 建表成功；`session_id varchar(64) NOT NULL`；两种 thread 形式（`<uuid>` / `<uuid>-<message_id>`）都写入成功 |
| **`WHERE session_id=?` 是否真的走索引** | ✅ `EXPLAIN` 结果为 `type=ref, key=idx_session, rows=2`——这是 GC/TTL 用 JOIN 代替全表扫描的前提，已实测而非推断 |
| 主键字节数（784） | ✅ 128×4 + 16 + 64×4 = 784，`session_id` 不在主键内不占额度 |
| **v4 DDL（含中文注释）能否建成**（2026-11-11，临时库 `cp_ddl_v4`，库默认 collation 故意设 `utf8mb4_unicode_ci`） | ✅ 4 张表全部建成；`TABLE_COMMENT` 4/4 非空且中文无乱码；列注释 `cp_checkpoints 12/12`、`cp_checkpoint_blobs 6/6`、`cp_checkpoint_writes 9/9`、`cp_checkpoint_meta 3/3` 全非空；10 个索引名与列顺序与设计一致 |

### 4.3.2 注释的三条硬约束（补注释时必须一起处理）

`COMMENT` 在 MySQL 里跟 Oracle 的 `COMMENT ON` 不一样：**没有独立的注释语句**，只能写进 `CREATE TABLE`（表用表尾 `COMMENT='...'`，列用列定义末尾 `COMMENT '...'`），后续修改要靠 `ALTER TABLE`。这带来三个具体问题：

| # | 约束 | 为什么 | 怎么处理 |
|---|---|---|---|
| 1 | **建表连接必须是 `utf8mb4`** | 注释以表字符集存储。若连接是 `latin1`/`gbk`，中文注释入库即乱码，而且不报错 | `aiomysql.create_pool(..., charset='utf8mb4')`（§5.8 已有）；`SET NAMES utf8mb4` 由驱动发送 |
| 2 | **SQL 脚本开头必须 `SET NAMES utf8mb4;`** | DBA 用 `mysql -u... < xxx.sql` 执行时，客户端默认字符集不一定和库一致（尤其 Windows 上的 mysql.exe 默认 gbk）→ 脚本里的中文注释全变问号 | `scripts/db/mysql-checkpoint-ddl.sql` 第一行写 `SET NAMES utf8mb4;`，第二行 `SET time_zone='+00:00';` |
| 3 | **改列注释只能用 `MODIFY COLUMN`，且必须重写完整列定义** | 没有 `COMMENT ON COLUMN`。`ALTER TABLE t MODIFY COLUMN c ... COMMENT '...'` 会**用你写的定义整体替换**——漏写 `NOT NULL` 就等于把约束去掉了，漏写 `COMMENT` 就等于清空注释 | 规则：**上线后不再单独改注释**。要改就走一次正式迁移（`ALTER ... MODIFY COLUMN` 全量列定义 + 单测比对 `SHOW CREATE TABLE`），并且迁移只允许"加"，不允许借机改约束 |
| 4 | 审计基线要连注释一起比 | `scripts/db/mysql-checkpoint-ddl.sql` 与代码常量的字符串比对单测，如果只比到主键/索引就漏掉注释漂移 | 单测用 `SHOW CREATE TABLE` 反查，逐表断言 `TABLE_COMMENT` 非空 + 列注释数量与代码常量一致（不要求字面完全相等，否则改文案就得改测试） |

> 顺带说明：`ROW_FORMAT=DYNAMIC` 是 MySQL 8.0.20+ 的默认值，写显式是因为 8.0.25 上 `TEXT/BLOB` 是否"行内 20 字节指针 + 行外存储"取决于它；一旦某环境把默认值改回 `COMPACT`，`MEDIUMTEXT` 的 `checkpoint_ns` 就会大量溢出到行外，读性能下降。显式写死，不依赖默认值。


### 4.4 元数据表：为什么要保留（`checkpoint_migrations` 是否可以不要）

**结论：可以省，但不建议省——保留，并改成 KV 形态的 `cp_checkpoint_meta`。**

先把两件事拆开，它们经常被混为一谈：

| 问题 | 答案 |
|---|---|
| (a) checkpoint 的 DDL 版本号要不要并入业务 `SCHEMA_VERSION`？ | **不要**。checkpoint 表结构跟 langgraph 大版本走，业务 schema 跟功能走。混在一起意味着"升级 langgraph 要动业务版本号"，回滚困难，还会让业务升级脚本被迫携带 checkpoint 的 DDL |
| (b) 要不要一张**独立的**版本/元数据表？ | **要**，理由见下（不是"以后可能要演进"这种空话） |

#### 你的疑问是合理的：初版一次性建表，确实"看起来"不需要版本表

如果这套 DDL 永远只有 v1，那 `CREATE TABLE IF NOT EXISTS` 本身就是幂等的，版本号完全用不上——**这是可以省的**。所以真正的问题不是"现在有没有用"，而是"省掉之后，第一次要改表结构时会发生什么"。

省掉的话，只剩一条路：**结构探测**（查 `information_schema` 判断某列在不在，不在就 `ALTER`）。它在多 Pod 环境下的代价是具体的：

| 问题 | 具体表现 |
|---|---|
| 每个迁移都要手写探测 SQL | 一条 `SELECT COUNT(*) FROM information_schema.COLUMNS WHERE ...` 配一条 `ALTER`。10 个迁移就是 20 条语句 |
| 探测条件写错 = 每次启动都 ALTER | 比如库名大小写不匹配、`TABLE_SCHEMA` 用了 `database()` 但连接的是另一套库 → 探测恒为"不存在" → **每次 Pod 重启、每次滚动发布都对同一张表做一次 `ALTER`**。10 个 Pod × N 次发布 = N×10 次 DDL，每次都拿 metadata lock |
| `ALTER` 不幂等，比 `CREATE` 危险得多 | `CREATE ... IF NOT EXISTS` 重复执行是无害的；`ALTER TABLE ADD COLUMN` 重复执行直接报 `1060 Duplicate column`。而启动期异常在现有代码里**会被 `except Exception` 吞掉**（§7.2 第 7 项要求改成 fail-fast，但那是新写的分支，老的兜底还在） |
| 无法回答"这个库是哪一版" | 运维排查时只能肉眼 diff 结构；DBA 问"要不要跑迁移"，开发只能翻代码 |

**所以：一张 3 列、不到 1 KB 的元数据表，换的是"迁移能不能用版本号决策"这个能力。这个交易是划算的。**

#### 更重要的第二个理由：它还要存 `ns_hash` 算法标识

§4.2 说 `checkpoint_ns_hash = sha256(checkpoint_ns)[:16]`，并且**这个算法上线后不能改**（改了所有历史 ns 都对不上）。但"不能改"如果只写在代码注释和这份文档里，**下一个接手的人不会知道**。

一旦有人真的改了算法（比如从 `sha256[:16]` 改成 `md5[:16]`，或者改成 `blake2b`），故障现象是**静默的**：主键里多了一批"新 hash"，旧数据查不到，`aget_tuple` 返回空历史，`alist` 少了一半记录，**但全程不报错**。用户看到的是"我昨天的对话怎么没了"。

所以 `cp_checkpoint_meta` 里存一条：

```sql
INSERT INTO cp_checkpoint_meta (meta_key, meta_value, updated_at)
VALUES ('ns_hash_algo', 'sha256-16', UTC_TIMESTAMP(6));
```

`asetup` 启动时做一次比对：**代码里算出来的算法标识 ≠ 库里存的值 → 启动失败**，并打印"检测到 ns 哈希算法变更，历史 checkpoint 将无法命中，必须走数据迁移"。这是唯一能把"静默错"变成"启动就炸"的地方。

同一个表顺带还能放两三个运维需要、但没地方存的键（都不是必须，占了也不占地方）：

| `meta_key` | 用途 |
|---|---|
| `schema_version` | DDL 版本号（`1` 起步，递增） |
| `ns_hash_algo` | `sha256-16`，算法变更的哨兵 |
| `ddl_applied_at` | 最近一次应用到哪一版、什么时候，滚动发布时排查"新旧 Pod 混跑"的利器 |
| `last_gc_at` | 最近一次 GC 完成时间。GC 是靠分布式锁随机落在某个 Pod 上的，没有这个值就不知道"到底有没有在扫" |

#### 成本对比

| 方案 | 表数 | 代码 | 优点 | 缺点 |
|---|---|---|---|---|
| **A. KV 元数据表（采用）** | 4 | `asetup` 约 15 行 | 版本号 + 算法哨兵 + 运维字段，一张表全覆盖；迁移用版本号决策，永远不会"重复 ALTER" | 多一张表（<1 KB）；多一个 `INSERT` |
| B. 只存版本号的 `v INT` 表（v3 写法） | 4 | 约 10 行 | 最简 | 省下的 5 行代码换来"算法标识无处可存"；将来要加 KV 还得再改一次表 |
| C. 不建表，用结构探测 | 3 | 每个迁移约 8 行 | 少一张表 | 上面表格列的 4 个问题全中；**静默降级风险**（算法错位不报错） |

**选 A**。B→A 的增量就是多加两列 + 一条 `INSERT`，现在做是零成本；等上线后再从 B 改成 A，那本身就是一次迁移。

> 注意：v3 那份 DDL 里写的是 `v INT NOT NULL PRIMARY KEY`，而 §8.2.1 的流程写的是 `SELECT version FROM ...`——**两处字段名本来就不一致**（v3 的笔误）。v4 统一成 `meta_key/meta_value` 后，这个不一致自然消失。

**不并入业务 `SCHEMA_VERSION` 的约束（保留 v3 结论）**：checkpoint 的 4 张表由 saver 自己的 `schema_version` 管理；删 `agent_checkpoints` 那一步仍走业务 `SCHEMA_VERSION` 16→17（§15.2），因为那是业务表的事。

**算法不可变的约束**：`checkpoint_ns_hash` 的计算方式一旦上线不得修改。若真需要改，走"加一列 + 后台回填 + 双写过渡"，不作为本次范围。

### 4.5 存储容量估算

| 项 | 估算 |
|---|---|
| 单 checkpoint 行 | ~1~3 KB（JSON，含内联标量、`channel_versions`、元数据） |
| 单 blobs 行 | 普通 channel 5~50 KB；`messages` 的完整快照（每 50 步一次）20 KB~2 MB；工具返回大结果（base64/大文件）可到数 MB |
| **一个回合的写入量** | `session_thread` 开着时**每一步**（一次模型调用 / 一次工具调用）都落一个 checkpoint，一个回合 10~30 步 → **10~30 个 checkpoint 行 + 20~60 个 blobs 行**，约 **1~5 MB** |
| 20 轮会话 | checkpoints 约 **200~600 行**；blobs 约 400~1200 行；总约 **20~100 MB** |
| 1000 个活跃会话 | **20~100 GB**（不含历史归档） |

> **v3 修正**：v2 按"每轮 2~4 个 checkpoint"估，把量级低估了 5~10 倍。真因是 `agent.session_thread` 默认开启（`config.yaml:58`），一个会话只有一个 thread，thread 内的 checkpoint 数随**步数**增长而不是轮数。这不是设计缺陷（PG/SQLite 也一样），但意味着：
> ① 容量规划要按"步"算，且**首个版本就必须有 GC**，不能"先上线观察"；
> ② `alist` 分页、`aget_tuple` 取最新这两个操作必须走索引（§4.3、§5.3 已按此设计）；
> ③ 一个长会话的 thread 会积累几千个 checkpoint，`adelete_thread` 必须分批删（§18-B7），否则删一次会话就是一个几千行 + 数百 MB 的长事务。
>
> **但清理的优先级要分清**：先上"已删会话对账"（零风险，语义干净），`record_ttl_days` 的按时间淘汰默认关闭——本项目没有会话过期策略，贸然清理会出现"列表里还能看到的会话点进去是空的"（R21、§5.7.1）。

### 4.6 还能不能再精简？（**你问的第 4 个问题**）

**结论：三张业务表是必需最小集，减不到 3 张；第四张（元数据表）按 §4.4 权衡后保留。** 下面不是"我觉得不行"，而是逐个给出合并后的具体故障。

#### 4.6.1 先看三张表的本质差异：三种写粒度，不是"一份数据的三种拆法"

| 表 | 写粒度 | 一行代表什么 | 什么时候写 | 生命周期 | 单行大小 |
|---|---|---|---|---|---|
| `checkpoints` | 每个**步骤边界** | 图状态的一个版本（"快照头"）：`channel_versions` + 标量值 | `aput`（每步结束 / 中断 / 回合结束） | 长（`alist` 历史回放依赖） | 1~3 KB |
| `blobs` | 每个 **channel 的每次变化** | 某 channel 在某 version 下的值 | `aput` 内（只写变了的 channel） | 长（同 version 被多个 checkpoint 共享） | 5 KB~2 MB |
| `writes` | 每个 **(checkpoint, task, channel)** | 任务级中间结果（`__interrupt__` / 节点输出 / 错误） | `aput_writes`（中断时**立即**写，不等下一次 `aput`） | 中（下次 `aput` 后"转正"进 blobs，但我们不主动清） | 几十 B~MB |

**判断能不能合并的标准只有一条：合并后，三者的主键（key）能不能用同一组列表达。** 答案是不能——`checkpoints` 的 key 是版本号，`blobs` 的 key 是 channel 版本号，`writes` 的 key 是任务执行序号。这是三种正交的标识，不是同一标识的不同精度。

#### 4.6.2 三个"看起来能合并"的候选，逐个否决

**候选 1：`checkpoints` + `blobs` 合并（退化成 SQLite 的全内联）**

- 后果：`aput` 只能把 `channel_values` **整体内联**进 checkpoint 行，没有"按 channel 版本去重"这一层。
- 代价分两部分，要分开算（否则容易得出夸大的结论）：
  - **① 不变的 channel 被重复存 N 份**：`system_prompt`、工具定义、`context_compactions` 这类几十步都不变的 channel，内联后每个 checkpoint 都带一份。分离式里它们**一行都不会多写**。这部分是纯浪费，与是否支持 delta 无关，**是合并的确定损失**。
  - **② 每步都变的 channel（`messages`）**：两边量级取决于是否启用 delta 快照。不做 delta 时，两边都是"每步一份全量"，都是 O(N²)；做了 delta（P2 的 `aget_delta_channel_history` + `_DeltaSnapshot`）后，分离式能降到 O(N) 增量，内联式**做不到**——因为内联的行是自包含的，没有"相对上一版"的位置。
- 结论不是"慢几倍"，而是：**合并后 O(N²) 就是上限，再优化也没空间**；分离式保留了降到 O(N) 的可能（P2 已经在做）。
- 这正是 §2 要修的现有实现的病根（全量快照 → 写放大），合并等于把病重新装回来。
- **否决。**

**候选 2：`checkpoints` + `writes` 合并**

这是唯一"技术上建得起来"的合并（下面 §4.6.3 给了完整 DDL）。但代价是：

- **时序倒挂**：`aput_writes` 可以在 `aput` **之前**发生（interrupt 时先写 `__interrupt__`，checkpoint 头还没落）。合并后同一张表里两者混排，主键只能靠 `kind` 区分，而 `kind` 又必须在主键里 → 主键变成 6 列。
- **读放大**：`aget_tuple` 只需要 checkpoint 头，合并后必须 `WHERE kind=0`。**只要漏写一处**，就会把同一 thread 下所有 task 的 payload（可能几十 MB）读进内存。
- **列浪费**：合并后 16 列，checkpoint 行实际用到 14 列、write 行 11 列（合并前分别是 12 列和 9 列），每行平均一半是 NULL。InnoDB 的 NULL 位图不占多少存储，但**行宽变大 → 每页行数下降 30~40% → buffer pool 命中率下降**，而这张表恰恰是热点。
- **统计查询全要改**："这个 thread 有几个 checkpoint"从 `COUNT(*)` 变成 `COUNT(*) WHERE kind=0`，运维手写 SQL 时极容易漏。
- **否决。**

**候选 3：`blobs` + `writes` 合并**

两者都是 `(..., channel) → payload`，看起来最像。但：

- key 不同源：`blobs.version` 是 channel 的**单调版本号**，`writes.idx` 是同一 (checkpoint, task) 内的**写入序号**（特殊通道还用固定负值 `WRITES_IDX_MAP`）。
- 更根本的：`writes` 在下一次 `aput` 时会被 `_apply_writes` **转正**成 blobs 里的 channel 值。合并后就无法表达"这条写入还没转正"——而 HITL 的全部意义就是"它可能永远不转正（用户不点继续）"。
- **否决。**

#### 4.6.3 如果你一定要 3 张表：理论合并版（供判断，**不建议采用**）

把 `checkpoints` + `writes` 合成一张 `checkpoint_records`，用 `kind` 区分：

```sql
CREATE TABLE IF NOT EXISTS {p}checkpoint_records (
  thread_id            VARCHAR(128) NOT NULL COMMENT '线程 id',
  checkpoint_ns_hash   BINARY(16)   NOT NULL COMMENT '命名空间哈希',
  checkpoint_id        VARCHAR(64)  NOT NULL COMMENT 'checkpoint id',
  kind                 TINYINT      NOT NULL COMMENT '0=checkpoint 头，1=任务级写入；所有查询必须带此过滤',
  task_id              VARCHAR(64)  NOT NULL DEFAULT '' COMMENT 'kind=1 时的任务 id；kind=0 固定空串',
  idx                  INT          NOT NULL DEFAULT 0 COMMENT 'kind=1 时的写入序号；kind=0 固定 0',
  -- ── kind=0 专用（kind=1 时恒 NULL）──
  session_id           VARCHAR(64)  NULL COMMENT '会话 id，仅 kind=0 有值',
  parent_checkpoint_id VARCHAR(64)  NULL COMMENT '父 checkpoint id，仅 kind=0 有值',
  step                 INT          NULL COMMENT '图执行步号，仅 kind=0 有值',
  channel_versions     JSON         NULL COMMENT '各 channel 版本号，仅 kind=0 有值',
  metadata             JSON         NULL COMMENT '元数据，仅 kind=0 有值',
  -- ── 两者共用 ──
  channel              VARCHAR(128) NULL COMMENT 'kind=1 时必填；kind=0 时 NULL',
  task_path            TEXT         NULL COMMENT 'kind=1 时的任务路径',
  type                 VARCHAR(32)  NULL COMMENT '序列化类型',
  payload              LONGBLOB     NULL COMMENT 'kind=1 时是写入值；kind=0 时是 checkpoint 主体 JSON',
  created_at           DATETIME(6)  NOT NULL COMMENT '写入时间（UTC）',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, checkpoint_id, kind, task_id, idx),
  KEY idx_thread_ns_kind (thread_id, checkpoint_ns_hash, kind, created_at),
  KEY idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='合并版（不建议）：checkpoint 头与任务级写入混排，用 kind 区分';
```

> 这段 DDL **已实测能建成**（2026-11-11，临时库 `cp_ddl_v4b`：建表成功、16 列、`utf8mb4_bin`、中文注释正常）——所以这里是"能建但不该建"，不是"建不起来"。

**合并前 vs 合并后的完整对比**：

| 维度 | 合并前（4 表，本方案） | 合并后（3 表） |
|---|---|---|
| 表数 | 4 | **3**（少 1 张） |
| DDL 行数 | 4 段约 60 行 | 3 段约 50 行（**少 10 行**） |
| `aput` 的 SQL | 1 条 INSERT + 1~N 条 blob INSERT（同事务） | 1 条 INSERT（kind=0）+ 1~N 条（kind=1 混合），语句数不变 |
| `adelete_thread` | 3 条 DELETE | **2 条 DELETE**（省 1 条，约 3 行代码） |
| **主键列数** | 3 / 4 / 5 | **6**（多了 kind/task_id/idx） |
| **主键字节数** | 784 / 1296 / 1044 | **1048 + kind(1) + 空串 task(4)** ≈ 1053 ✅ 仍安全 |
| **行宽** | checkpoints 12 列 / blobs 6 列 / writes 9 列 | 合并后 **16 列**，平均一半 NULL |
| `aget_tuple` 正确性 | 天然正确（只查本表） | **漏写 `kind=0` 就把 MB 级 payload 读进内存**，且"取最新"会取到 writes 行 |
| 统计查询 | `SELECT COUNT(*)` | `SELECT COUNT(*) WHERE kind=0`（容易漏） |
| `created_at` 语义 | 单一（checkpoint 写入时间） | **分裂**（kind=0/1 两种含义），TTL 与 prune 判据要分叉 |
| 未来加字段（PG 已加 4 次） | 只动对应的窄表 | 动宽表，影响面更大 |
| 新人理解成本 | 表名自解释 | 必须先读 `kind` 的含义才能读懂任何一条 SQL |

**取舍结论**：合并只省下 **1 张表 + 约 10 行代码 + 1 条 DELETE**，代价是**每一处读都可能读错、每一处统计都可能漏过滤、每一次加字段都动宽表**。在"这张表是热点且跨 Pod 并发写"的前提下，**不值得**。

#### 4.6.4 已经做掉的"真精简"（v2→v4 一路砍下来的）

这四项才是真正降低了复杂度，且不牺牲正确性：

| 精简项 | 省掉了什么 |
|---|---|
| 不建 `checkpoint_ns` 字典表 | 省一张表 + 一次 JOIN；用 `sha256[:16]` 内联解决"ns 长度不可控" |
| `blobs` 只留 1 个二级索引（`idx_thread_ns`） | 不按 channel 建索引（channel 基数高、选择性差，建了也用不上） |
| `writes` 只留 1 个二级索引（`idx_thread_ns_cp`） | 同上 |
| 不建"每 thread 统计行"（count / size） | 需要时现算；避免每次写都要 UPDATE 同一行（热点行锁） |
| 元数据表压到 3 列 | 只留 `meta_key/meta_value/updated_at`，不建 `created_by`、`note` 这类"看起来有用"的列 |

**下一次真要精简，应该砍的是"功能"而不是"表"**——比如不做 `alist`（历史回放），那 `checkpoints.parent_checkpoint_id` 与 `idx_parent` 都能去掉。但那等于放弃时间旅行，不是本次的取舍范围。

---

## 5. 接口实现规格（8 个必实现方法）

`langgraph-checkpoint 4.3.0` 的 `BaseCheckpointSaver` 契约（已核对基类签名）：

| 方法 | 是否必须 | 不实现的后果 |
|---|---|---|
| `aput` | ✅ 必须 | 无法写 checkpoint |
| `aput_writes` | ✅ 必须 | HITL 的 interrupt 写不进去，审批永远等不到 |
| `aget_tuple` | ✅ 必须 | 无法恢复 |
| `alist` | ✅ 必须 | 历史/时间旅行不可用 |
| `adelete_thread` | ✅ 必须 | 删会话后 checkpoint 残留（孤儿数据无限增长） |
| `setup` / `asetup` | ✅ 自研的初始化入口（**注意：不是基类契约**） | 见下方说明 |
| `aget_delta_channel_history` | ⚠️ P2 覆写（**本期必做**） | 不覆写则长会话每次打开多 180 次 SQL 往返（见 §5.5 量化） |
| `aprine` | ⭕ P2（只做 `delete`） | 基类 `NotImplementedError`；**已核实 langgraph/deepagents 内部无任何调用点**，不实现不影响运行 |
| `adelete_for_runs` / `acopy_thread` | ❌ 不实现 | 基类抛 `NotImplementedError`。**v3 更正**：这两者不在"必实现"清单里（v2 没提），P1 开工时用 `grep -rn "copy_thread\|delete_for_runs" venv/deepagents venv/langgraph` 复核一次调用点即可 |

> **v3 更正（`asetup`）**：`langgraph-checkpoint 4.3.0` 的 `BaseCheckpointSaver` **没有 `asetup` 或 `setup` 方法**（已核对 `base/__init__.py`：`prune` 398、`adelete_for_runs` 547、`acopy_thread` 565、`aprine` 585、`aget_delta_channel_history` 680，没有 setup）。`setup()` 是**各官方包自己定义**的初始化入口（`langgraph_checkpoint_sqlite/aio.py:310`）。我们沿用 `async def asetup()` 这个名字，由 `start_checkpoint_backend()` 显式调用，**不要指望框架会自动调用它**——v2 写"必须实现的基类契约"是错的。

### 5.1 `aput`：写一个 checkpoint

对齐 PG 的五步，语义逐条固定：

1. 从 `config.configurable` 取 `thread_id` / `checkpoint_ns` / `checkpoint_id`（父 id）。
2. 复制 `checkpoint`，遍历 `channel_values` 分三类：
   - `None / str / int / float / bool` → **内联**保留在 JSON 里；
   - `_DeltaSnapshot` → 移入 blobs，JSON 里该 key 置 `True`（**必须照做**，否则 delta 通道每次都被当全量重存）；
   - 其他（dict / list / 自定义对象）→ 移入 blobs，JSON 里删掉该 key。
3. 只对 `new_versions` 中**且当前存在**的 channel 写 blobs（`type` + `bytes` 来自 `serde.dumps_typed`）。用 `INSERT ... ON DUPLICATE KEY UPDATE` **不更新任何业务列**（等价 PG 的 `DO NOTHING`）——同 version 已存在就跳过，避免重复写大对象。
4. 写 checkpoints 主体，冲突时**更新** `checkpoint` / `metadata` / `channel_versions`（等价 PG 的 `DO UPDATE`）。写入时**顺带算好 `session_id = session_id_of_thread(thread_id)`**（`sched-` 前缀的写空串）——这一列只在 INSERT 时算，`DO UPDATE` 不覆盖，避免历史行被改成新值。
5. 以上 3 与 4 必须在**同一事务**里提交，否则会留下"数据行在、channel 值缺失"的半截 checkpoint。

### 5.2 `aput_writes`：写中间写入（HITL 的关键路径）

冲突策略取决于 **write 的 channel 是否全是特殊通道**（这不是随便定的）：

- `WRITES_IDX_MAP = {ERROR: -1, SCHEDULED: -2, INTERRUPT: -3, RESUME: -4}`，这四类用**固定负 idx**；
- 若本次 writes 的 channel **全部**在 `WRITES_IDX_MAP` 中 → 用 `ON DUPLICATE KEY UPDATE` 覆盖（**同一 interrupt 重复写必须覆盖，不能丢弃**，否则审批状态停留在第一次的旧值）；
- 否则 → 用"冲突即忽略"（普通 writes 重复投递应被丢弃，保证幂等）。
- `idx` 的取值规则：`WRITES_IDX_MAP.get(channel, 枚举序号)`。

> 这是最容易写错的一处。写反了的表现是：**同一轮重复触发审批时，界面显示的还是上一次的审批内容**，或者相反——普通 writes 被覆盖导致消息重复。

### 5.3 `aget_tuple`：读一个 checkpoint

三次参数化查询（同一连接、同一快照内）：

1. `checkpoints` 按主键取行：`thread_id + ns_hash + checkpoint_id`；`checkpoint_id` 为空时取该 ns 下最新一条，**`ORDER BY checkpoint_id DESC LIMIT 1`**（v3 修正 v2 的"按 `created_at` 倒序"）。
   - `checkpoint_id` 是 `langgraph.checkpoint.base.id.uuid6()` 生成的 **UUIDv6（36 字符）**，高位就是时间戳，**字典序 ≈ 写入时序**，且正好命中主键倒序扫描（无需额外索引、无 filesort）。
   - 为什么不用 `created_at`：它取的是**应用写入时刻**，比 `checkpoint_id` 多一层"应用生成 id → 攒出 INSERT → 提交"的延迟；多 Pod 并发时这两个时间可能反向。官方 PG 实现也是只按 `checkpoint_id` 排。
   - ⚠️ 依赖**跨 Pod 时钟大致同步**（NTP，误差 < 数秒）。跨 Pod 分叉本身由 §14.4 的 409 前置挡掉，所以这个依赖只影响"极端情况下取到哪一支"。**绝不能用 `checkpoint_id` 的范围扫描来找祖先**（SQLite `_delta.py:34` 的注释就是在说这件事：id 只在单进程内单调），祖先只能沿 `parent_checkpoint_id` 走。
2. 从行内 `channel_versions`（JSON）拿到 `{channel: version}`，用它做 `IN` 批量查 `checkpoint_blobs`（**一次查询取全部 channel**，禁止循环 N 次）。
3. 查 `checkpoint_writes`（`thread_id + ns_hash + checkpoint_id`），按 `writes_sort_key(task_path, task_id, idx)` 排序后反序列化。

返回前把 blobs 里取到的值**合并回** `channel_values`（内联的标量已在 JSON 里，不覆盖）。

**一致性要求**：三次查询若不在同一事务/同一快照，可能读到"checkpoint 行已提交、blobs 尚未提交"的中间态（另一次 aput 正在提交）。做法：**显式开一个只读事务**（`START TRANSACTION READ ONLY` + `COMMIT`）把三次查询包住，靠 InnoDB 默认的 REPEATABLE READ 拿一致快照。

⚠️ **`autocommit=True` 时这条不能省**：连接池统一 `autocommit=True` 是为了让单条写不用手动提交，但代价是**每条 SELECT 自己就是一个事务**，三条 SELECT 落在三个不同快照上。所以读路径必须显式 `START TRANSACTION READ ONLY` … `COMMIT`，写路径（`aput` 的 blobs+checkpoints）必须显式 `begin()` … `commit()`。这一点必须在集成测试里覆盖（并发 aput + aget_tuple，断言读到的 `channel_values` 要么全有要么是旧版本，不能"新 checkpoint 行 + 缺 channel"）。

### 5.4 `alist`：列历史

- 支持 `before`（按 `checkpoint_id` 倒序游标分页）、`limit`、`filter`（`metadata` 的等值匹配）。
- MySQL 的 `JSON` 过滤用 `JSON_EXTRACT(metadata, '$.k') = CAST(? AS JSON)`。
- 排序键用 **`checkpoint_id DESC`**（主键倒序扫描，天然分页；`before` 游标就是上一页最后一条的 `checkpoint_id`）。**不要再叠加 `created_at`**：UUIDv6 已经是时间序，多一个排序键只会让索引失效。
- ⚠️ 别把两件事混起来：`checkpoint_id` **可以用来排序/分页**（时间序），但**不能用来"按 id 范围扫祖先"**——祖先只能沿 `parent_checkpoint_id` 走。SQLite `_delta.py:34` 那句注释说的是后者（"ids are only monotonic within one process, so a range scan by id can miss a parent whose id sorts above its child's"），v2 误把它当成了"不能按 id 排序"。

### 5.5 `aget_delta_channel_history`：P2，本期必须做（**这是你问的第 6 个问题**）

#### 先说它是什么

`deepagents` 把 `messages` 这个 key 定义成了 `DeltaChannel`：

```python
# deepagents/graph.py:78
messages: Required[Annotated[list[AnyMessage], DeltaChannel(_messages_delta_reducer, snapshot_frequency=50)]]
```

`DeltaChannel` 的作用是**解决 checkpoint 的 O(N²) 增长**：普通 channel 每个 checkpoint 都存完整值，第 100 轮就要把 100 轮消息全存一遍；`DeltaChannel` 平时只存"这一步新增了什么"（增量），每 50 次写才存一次完整快照（`_DeltaSnapshot`）。

代价是**读的时候要重建**：如果最新 checkpoint 上的 `messages` 只是一个"增量哨兵"，要还原完整消息列表，就得沿父链往回走，把途中每一步的增量都收集起来，直到撞上最近的那个完整快照。

`get_delta_channel_history` 就是这个"往回走"的接口。

#### 基类默认实现做了什么（P1 阶段我们继承到的行为）

`checkpoint/base/__init__.py:699`：

```python
while cursor_config is not None and remaining:
    tup = await self.aget_tuple(cursor_config)   # ← 每层祖先一次完整读
    ...
    cursor_config = tup.parent_config
```

**它每一层祖先都调一次完整的 `aget_tuple`**。而我们的 `aget_tuple` 是 3 次 SQL（checkpoints + blobs + writes）。所以：

| 项 | 数值 |
|---|---|
| 回溯深度 | 最坏 = `snapshot_frequency` = **50 层**（实测 120 轮会话回溯 **60 次**） |
| 每层 SQL | 3 次（`aget_tuple` 的三段查询） |
| 总 SQL | **≈180 次** |
| 本机耗时 | 111ms（实测，MySQL 包同结构同算法） |
| **跨网络耗时**（2ms/次往返） | **≈360ms 起**，网络抖动时到 500ms |

用户能感知到的是什么？**点开一个长会话（或任何一次刷新续接）时，先白屏半秒**。因为 `aget_state` 必须先把这个会话的完整状态重建出来，才能渲染。

而且这不是偶发：`deepagents` 每轮恢复都会走这条路径，所以**会话越长、越慢，是线性恶化的**。

#### P2 改成什么（照抄官方 PG 的两阶段）

| 阶段 | 做什么 | SQL 次数 |
|---|---|---|
| **一** | 一条递归 CTE 沿 `parent_checkpoint_id` 把祖先链一次性取出来，只取 `checkpoint_id / channel_versions / JSON_CONTAINS_PATH(channel_values, '$.messages')`，**不取 blob** | 1 |
| **二** | 用阶段一拿到的祖先 id 集合，一条 `IN (...)` 批量取这些祖先在该 channel 上的 `writes`；再一条取最近的 `_DeltaSnapshot` blob | 2 |
| **应用层** | 把 `{channel: {writes, seed}}` 组装成基类要的 `DeltaChannelHistory` | 0 |

**结果：180 次往返 → 3 次。** 跨网络从 300~500ms 降到 **<20ms**（大 blob 传输时间另算）。

#### 必须遵守的两条约束（PG 源码注释明确写明）

1. **必须沿 `parent_checkpoint_id` 遍历，不能按 `checkpoint_id` 范围扫。** `checkpoint_id` 是"时间戳+随机后缀"，跨 Pod 并发下字典序不等于链序，按范围扫会漏节点、把历史拼错。
2. **遇到重复 id 必须停。** `ON DUPLICATE KEY UPDATE` 理论上不会成环，但一旦数据被人工修过就可能出现环，遍历要有 visited 集合，否则递归 CTE 会打满 `cte_max_recursion_depth`（MySQL 8.0 默认 1000）后报错。

#### 本期一次到位（P1 + P2）的代价

多写约 **120~150 行**（递归 CTE 拼装 + 三阶段合并 + 单测里构造 60 层祖先链的 fixture），多测 1 天。换来的是**不经历"先慢后快"的中间态**：否则 P1 上线后，长会话用户会先遇到半秒白屏，等 P2 才恢复。

> P2 里 `aprine` 只做 `delete`（见 §5.7），`keep_latest` 显式抛 `NotImplementedError` 并写明原因——不要留一个"看起来能用、实际会静默丢历史"的实现。

### 5.6 `adelete_thread`

`DELETE FROM blobs/writes/checkpoints WHERE thread_id = ?`，**三表必须在一个事务里**，顺序先子后主。返回删除行数用于日志。

`delete_session_threads` 的现有逻辑可原样复用——它调的就是 `adelete_thread`，只是从"删本 pod 文件"变成"删共享表"。

### 5.7 `prune` / `aprine`：P2，只作为"缩容单个会话"的手段

**签名以基类为准（已核对 `checkpoint/base/__init__.py:398`）**，注意是**批量 thread_ids**，不是单个：

```python
async def aprine(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None
```

- `strategy="delete"`：删掉这些 thread 的全部 checkpoint / blobs / writes（等价逐个 `adelete_thread`）。
- `strategy="keep_latest"`：每个 `checkpoint_ns` 只留最新一条。
- **P2 只实现 `delete`**；`keep_latest` 推迟，理由是基类 docstring 的 DeltaChannel 警告：
  > `_DeltaSnapshot` 每 `snapshot_frequency`（本项目 50）次更新才写一次。保留下来的"最新一条"几乎不可能是快照点，删掉中间 checkpoint 会让 delta 通道**静默重建为空数组**——表现是"长会话突然丢历史"，不报错。
  
  若要安全实现 `keep_latest`，基类给了三条路，我们选第三条：**对使用 `DeltaChannel` 的 thread 直接跳过 prune**。本项目 `messages` 就是 `DeltaChannel`（`deepagents/graph.py:78`），等于 `keep_latest` 在本项目里不该用。
- 已核实 `langgraph` / `deepagents` 内部**没有任何 `prune` 调用点**，不实现不影响运行，只是运维少一个手动缩容手段。

> **`aprine` 与 `record_ttl_days` 是两件不同的事**：`aprine` 是"给单个/一批 thread 缩容"，由代码或运维显式调用；`record_ttl_days` 是"定时按时间淘汰整条会话"。后者不依赖前者（见 §5.7.1），所以 P2 里 `aprine` 可以只做 `delete` 分支，风险很低。

### 5.7.1 `record_ttl_days` 启用后到底做什么（**这是你问的第 1 个问题**）

**结论先说**：它是一个**定时任务**，把"很久没人用的会话"对应的 checkpoint **整条 thread 从三张表里删掉**。不是"删某几行 checkpoint"，也不删业务 `sessions` 表的会话。

> **v4 已定稿：本期不启用（`record_ttl_days=0`）。** 本节保留完整的语义设计，是因为代码要按此实现（默认关闭），且将来启用时不需要重新设计一遍。观察期的具体做法见本节末尾。

#### 触发方式

不新建定时器，**并入现有的 `_checkpoint_gc_loop`**（`checkpoints.py:734`，每 `gc_interval_seconds` 跑一轮）。每轮顺序：

```
① 对账：sessions 表里已不存在的会话 → 删它的 thread（现有逻辑，保留）
② TTL ：sessions 表里还在、但 N 天没动过 → 删它的 thread（本次新增）
```

第 ② 步只在 `record_ttl_days > 0` 时执行，默认 0 = 完全跳过，行为与现在一模一样。

#### 选主：只让一个 Pod 干活

用现成的 `DistributedLock`（MySQL 锁表，`distributed_lock.py:102`）：

```python
handle = lock.acquire("checkpoint-ttl", ttl_seconds=300, wait_seconds=0)  # wait=0：抢不到立即跳过
if not handle.acquired:
    return 0            # 别的 Pod 正在跑，本轮跳过
```

锁未启用（`distributed_lock.enabled=false`）时退化为"每个 Pod 都跑"。这是可以接受的：`DELETE` 幂等，最坏情况是几路 Pod 同时删同一批 thread，产生短暂行锁等待，不会删错。

#### 删除粒度：**整 thread，绝不删中间行**

这是最关键的一条，也是"吸取原有实现教训"的落点：

| 做法 | 后果 |
|---|---|
| 按 `created_at < now-N天` 删 `cp_checkpoints` 的行 | ❌ **会产生"父 id 悬空"的碎片链**，`alist` 与 delta 回溯都会读到断裂的历史；且 `messages` 的 `_DeltaSnapshot` 被删后，恢复出来的历史是空的，**不报错** |
| 删整 thread（三表一起） | ✅ 语义干净：要么这个会话的 checkpoint 全在，要么全不在，恢复时得到"没有历史"（前端表现为新会话） |

所以 TTL 的实现是：**先选出 thread_id 列表，再对每个 thread 调 `adelete_thread`（或等价的三条 `DELETE ... WHERE thread_id=?`，同一事务）**。

#### 具体 SQL（选定期的 thread）

**口径 A：`ttl_basis = session_updated_at`（默认，推荐）**——先选过期会话，再取它的 thread，两步都能走索引：

```sql
-- ① 选"已归档 + N 天没动 + 当前无活跃流"的会话
SELECT s.session_id
FROM sessions s
LEFT JOIN session_runtime r ON r.session_id = s.session_id
WHERE s.archived = 1
  AND s.updated_at < :cutoff
  AND (r.stream_until IS NULL OR r.stream_until = '' OR r.stream_until < :now)
LIMIT :ttl_batch_size;

-- ② 用上一步的 session_id 列表取 thread（走 idx_session）
SELECT DISTINCT thread_id
FROM cp_checkpoints
WHERE session_id IN (:sids);
```

**口径 B：`ttl_basis = checkpoint_created_at`**——只依赖 checkpoint 表，不与业务表耦合：

```sql
SELECT thread_id
FROM cp_checkpoints
GROUP BY thread_id
HAVING MAX(created_at) < :cutoff
LIMIT :ttl_batch_size;
```

> ⚠️ **不要试图在 SQL 里从 `thread_id` 解析会话 id**。`thread_id` 有两种形式（`<uuid>` 与 `<uuid>-<message_id>`），UUID 本身含 `-`，没有可靠分隔符；`LEFT(thread_id, 36)` 虽然能work但属于"假设 UUID 永远 36 字符"的隐式约定。正确做法就是 §4.3 新增的 `session_id` 冗余列——写入时用现成的 `session_id_of_thread()` 算好，SQL 里直接 JOIN。

#### 三重保护（缺一不可）

1. **活跃流跳过**：`session_runtime.stream_until >= now` 的一律不删（用户正在跑回合）。
2. **在途审批跳过**：`cp_checkpoint_writes` 里最新一条 `channel='__interrupt__'` 且 `cp_checkpoints` 最新 checkpoint 之后没有 `__resume__` 的 thread 不删。判定较绕，**兜底策略是阈值取得足够大**（≥ 业务允许的最长审批挂起时长 × 3）。
3. **只删"用户已经看不到"的会话**：优先只清理 `sessions.archived=1` 的会话；`archived` 的会话在 UI 上本来就折叠了，删掉 checkpoint 不会让用户"点进去打不开"。**这是推荐的第一版口径**。

#### 为什么默认 0（不启用）

因为本项目**目前没有任何"会话过期/归档"策略**，也没有 UI 提示。一旦启用 TTL，会出现这种体验：

> 用户 90 天前的一个会话还在左侧列表里、点得开、标题还在（`sessions` 行没删），但点进去是空的（checkpoint 已被 TTL 删）。

这不是 bug，是"两套数据生命周期不一致"。要么把 TTL 限定在 `archived=1`，要么同步提供 `sessions` 的归档/清理策略。

**已定稿（v4）：本期 `record_ttl_days=0`，不启用 TTL。** 只上线"已删会话对账"（清理 `sessions` 里已经不存在的会话残留），这条是零风险的——用户已经删掉的会话，checkpoint 留着没有任何价值。

**观察期怎么做（写进运维手册，否则"观察一段时间"会变成永远不观察）**：

| 时点 | 动作 | 判据 |
|---|---|---|
| 上线后第 1 周 | 每天记一次 `SELECT COUNT(*), ROUND(SUM(data_length+index_length)/1024/1024) FROM information_schema.TABLES WHERE TABLE_NAME LIKE 'cp_%'` | 拿到"每个活跃会话产出多少 MB"的实测值，用来校核 §4.5 的估算 |
| 上线后第 1 个月 | 记录增长曲线 vs 活跃会话数 | 若"每会话 MB"显著高于 §4.5 的 20~100 MB，说明单步快照比预期大，要查 `messages` 是否在重复存 |
| 上线后第 3 个月 | 若 `data_length` 超过库总容量的 30%，再评估启用 TTL；否则继续观察 | 届时必须同时回答"是否接受 `archived=1` 的会话被清理"，否则 TTL 的语义无解（R21） |

> 期间如果出现"库快满了"的紧急情况，先做的是 **`aprine(thread_ids, strategy='delete')` 手动缩容个别超大 thread**（§5.7 已实现），而不是打开 TTL——前者可控、后者是全局策略。

（这也是 R13 的对策。）

### 5.8 `asetup` 与启动期行为

1. 建连接池（`aiomysql.create_pool`，`autocommit=True`，`charset='utf8mb4'`，`init_command="SET time_zone='+00:00'"`）。**`charset='utf8mb4'` 同时决定中文注释写入是否正确**（§4.3.2 约束 1），不能省。
   - **这个池与业务库池（`DBUtils`）完全独立**：两套池、两条连接、互不借用。原因有三：① 业务库用的是同步驱动（`PyMySQL` + `DBUtils`），checkpointer 是全异步路径，复用会把事件循环堵住；② 两边的超时/重试策略不同（checkpoint 要"短超时 + 快速失败"，业务库是"长事务"）；③ 隔离故障：checkpoint 把池打满时不应该拖住业务查询。
   - 代价是连接数翻倍：现网 `replicas: 2` → 4 个池。建议 `pool_max_size=4~6`（不是 10），按"扩到 10 副本"算总账（§18-C5）。
2. `SELECT VERSION()` → 校验 ≥ 8.0.19，否则启动失败并给出明确原因。
3. **建表/迁移**：`auto_setup=true`（默认）时按 §8.2.1 的流程执行——`GET_LOCK` 串行化 → 读 `cp_checkpoint_meta.schema_version` → 逐条应用未执行的迁移 → 写回版本号 → `RELEASE_LOCK`；`false` 时只校验 4 张表存在，缺失即**抛部署错误**（不静默降级、不回退 legacy）。
4. **校验 `ns_hash_algo`**：`SELECT meta_value FROM {p}checkpoint_meta WHERE meta_key='ns_hash_algo'`，与代码常量（`sha256-16`）比对，不一致 → **启动失败**并提示"ns 哈希算法已变更，历史 checkpoint 将全部无法命中，必须先做数据迁移"。这是把"静默读空历史"变成"启动即炸"的唯一位置（§4.4）。
5. 校验 collation 是 `utf8mb4_bin`（防止建表时用了库默认值），不一致直接失败并给出 `ALTER TABLE ... CONVERT TO` 建议。
6. 记录一次 `SHOW VARIABLES LIKE 'max_allowed_packet'`，小于 16MB 时告警。
7. **打印实际生效的后端**（`AsyncMySQLSaver（共享 MySQL，cp_ 前缀 4 张表）` / `AsyncSqliteSaver（本 Pod 文件 …）` / `MemorySaver（进程内，重启即丢）`）——这是运维判断"跑的是哪个实现"的唯一可靠依据（旧快照后端已删除，见 §15）。

---

## 6. 依赖与配置变更（对应你的第 6 条）

### 6.1 `pyproject.toml`（唯一新增依赖）

```toml
dependencies = [
    # ...现有项不动...
    "aiomysql>=0.3.2",          # 新增：MySQL 异步驱动（纯 Python，依赖 PyMySQL，项目已有）
]
```

| 包 | 版本 | 为什么选它 | 备选与不选的理由 |
|---|---|---|---|
| `aiomysql` | `>=0.3.2` | aio-libs 组织维护，**2025-10-22 刚发版**；纯 Python（`py3-none-any`），内网镜像易获取；复用项目已有的 PyMySQL | `asyncmy`：C 扩展，需平台 wheel，且 0.2.x 更新停在 2023；`pymysql + to_thread`：零新依赖但占默认线程池，与业务 `run_blocking` 抢线程（MongoDB 包的实测问题） |

**不需要**新增：`langgraph-checkpoint`（已是 `deepagents`/`langgraph` 的传递依赖）、`langgraph-checkpoint-mysql`（本方案自研，**明确不引入**）。

### 6.2 `config.yaml`（新增一段，全部有默认值，不加也能启动）

```yaml
checkpoint:
  # 原有项保留：sqlite_path（不改）
  sqlite_path: ${EASY_CHECKPOINT_SQLITE_PATH:-}

  mysql:
    enabled: ${CHECKPOINT_MYSQL_ENABLED:-true}       # 业务库是 MySQL 时自动启用
    host: ${CHECKPOINT_MYSQL_HOST:-}                 # 留空=复用 database.mysql.host
    port: ${CHECKPOINT_MYSQL_PORT:-0}                # 0=复用业务库端口
    user: ${CHECKPOINT_MYSQL_USER:-}                 # 留空=复用业务库账号
    password: ${CHECKPOINT_MYSQL_PASSWORD:-}
    database: ${CHECKPOINT_MYSQL_DATABASE:-}         # 留空=复用业务库库名（同库不同表）
    charset: utf8mb4
    table_prefix: cp_                                # 表名前缀，多环境共库时靠它隔离
    pool_min_size: 2                                 # 连接池下限
    pool_max_size: 10                                # 连接池上限（≈ 单 pod 并发请求数 × 1.5）
    connect_timeout: 5                               # 建连超时（秒）
    read_timeout: 10                                 # 单次查询超时（秒）

    # ── 建表 ──────────────────────────────────────────────
    auto_setup: ${CHECKPOINT_MYSQL_AUTO_SETUP:-true}  # true=启动时自动建表/升级（已确认生产账号有 DDL 权限）
    auto_setup_lock_wait: 60                          # 多 Pod 同时启动时，抢建表串行锁的等待秒数

    # ── 清理 ──────────────────────────────────────────────
    gc_interval_seconds: ${EASY_CHECKPOINT_GC_INTERVAL:-600}  # 巡检间隔；0=完全关闭（对账 + TTL 一起关）
    record_ttl_days: ${CHECKPOINT_TTL_DAYS:-0}        # 0=按时间不过期（推荐先用 0）；>0 时按"整 thread"删除
    ttl_basis: session_updated_at                     # 过期判定依据：session_updated_at | checkpoint_created_at
    ttl_only_archived: true                           # 只清理 archived=1 的会话（避免"列表里还在、点进去空了"）
    ttl_batch_size: 200                               # 单轮最多删多少个 thread（分批，避免长事务）
    ttl_protect_active_stream: true                   # 有流锁的会话一律跳过（防误删在跑的回合）
```

**逐项说明**：

| 配置 | 默认 | 说明 |
|---|---|---|
| `host/port/user/password/database` | 全空 | **空即复用 `database.mysql.*`**，即"与业务库同库同实例"（已确认）。只改后端名就能跑 |
| `table_prefix` | `cp_` | 与业务表同库时靠前缀隔离（社区包缺这个能力） |
| `pool_max_size` | `10` | 每 pod 的连接数；多副本时总连接数 = pod 数 × 该值，需 ≤ MySQL `max_connections` 的 30%。**注意这是新增的第二套池**，见 R7 |
| `auto_setup` | `true` | **已确认生产账号有 DDL 权限**，故默认 `true`：启动时幂等建表 + 执行未应用的迁移。仍然保留 `scripts/db/mysql-checkpoint-ddl.sql` 作为审计基线 |
| `auto_setup_lock_wait` | `60` | `auto_setup` 用 `GET_LOCK()` 串行化；抢不到锁就等，超时则**启动失败**（不静默跳过建表） |
| `record_ttl_days` | `0` | **默认关闭**。>0 时按"整 thread"删除，语义见 §5.7.1。不要设成"看起来很短"的值（如 7），原因见 R21 |
| `ttl_basis` | `session_updated_at` | 判定"多久没动"的依据。默认取业务 `sessions.updated_at`（语义最准：用户真的没再用过这个会话）；`checkpoint_created_at` 只按 checkpoint 表自己判断，不与业务表耦合 |
| `ttl_only_archived` | `true` | 只清理**已归档**会话的 checkpoint。这是避免"左侧列表里还能看到、点进去是空的"的关键开关；设为 `false` 时必须同时上线会话保留策略，否则不要开 |
| `ttl_batch_size` | `200` | 单轮删除上限，分批提交。`LONGBLOB` 的 DELETE 会产生大事务，一次删几千个 thread 会撑爆 redo |
| `ttl_protect_active_stream` | `true` | 删除前查 `session_runtime.stream_until`，有活跃流锁的 thread 跳过 |
| `gc_interval_seconds` | `600` | 复用现有 `start_checkpoint_gc` 的间隔语义；同一轮里先做"已删会话对账"再做 TTL |

### 6.3 `.env` 变更清单

**新增（全部可选，不配就复用业务库）**

```dotenv
EASY_CHECKPOINT_BACKEND=mysql_saver    # 显式指定；不配时 auto 也会选中 mysql_saver（业务库是 MySQL 时）
CHECKPOINT_MYSQL_DATABASE=             # 留空=同业务库
CHECKPOINT_MYSQL_TABLE_PREFIX=cp_
CHECKPOINT_MYSQL_AUTO_SETUP=true       # 生产账号有 DDL 权限，故为 true；若某环境禁用权限则设 false
CHECKPOINT_TTL_DAYS=0                  # 0=按时间不过期（本期建议 0，见 §5.7.1）
EASY_CHECKPOINT_GC_INTERVAL=600        # 已有变量，语义不变（现在同时对账 + TTL 生效）
```
**保留但语义变化**
```dotenv
EASY_CHECKPOINT_SQLITE_PATH=data/checkpoints.db   # 仍用于 sqlite 后端（dev/test 零依赖 + 回滚，见 §17）
```
**保留、不再必需（v3 改口）**
```dotenv
EASY_STICKY_SESSIONS=1                  # mysql_saver 下不再校验，保留只会打一条 info 日志
                                        # ⚠️ 建议**保留 Ingress 的粘性 annotation**，原因见 §17.2（回滚要用）
```
**后端名字符串（v3 定稿，见 §16）**
| 值 | 含义 | 用途 |
|---|---|---|
| `mysql_saver` | **新自研 AsyncMySQLSaver（默认）** | 生产 |
| `mysql` | **废弃别名 → `mysql_saver`**（打一条 WARN） | 兼容 ConfigMap 里还写着 `mysql` 的部署 |
| `sqlite` | 官方 AsyncSqliteSaver（原样，一行未改） | dev/test、单机、**以及生产回滚** |
| `legacy` | 进程内 `MemorySaver`（原样） | 调试 |
| `auto` | 业务库是 mysql → `mysql_saver`；否则 → `sqlite` | 默认值 |

> ⚠️ **兼容性提醒（v3 已弱化）**：`mysql` 现在只存在于**未发布的代码**里（`deploy/k8s/agent-deployment.yaml:19` 写的是 `mysql`，但那份 yaml 属于 2026-11-06 之后未上线的提交）。为了不让"ConfigMap 里还写着 `mysql`"的部署升级后启动失败，`mysql` 保留为别名并打 WARN，映射到 `mysql_saver`。**不需要在发布说明里警告"行为突变"**——线上从来没有跑过旧快照后端（见 §15.1 的核实依据）。

---

## 7. 代码改造清单（最小侵入）

### 7.1 新增文件

| 文件 | 内容 | 约行数 |
|---|---|---|
| `easy_agent/persistence/mysql_saver.py` | `AsyncMySQLSaver`（8 个方法）+ DDL 常量 + 连接池管理 + ns hash 工具 | 600~700 |
| `scripts/db/mysql-checkpoint-ddl.sql` | 4 张表 DDL（**审计基线**：变更评审 diff、新环境手工预建、故障时对齐"库里该长什么样"）。与 `auto_setup` 并存，不是二选一 | 60 |
| `tests/persistence/test_mysql_saver_unit.py` | 无 DB 的单元测试（fake cursor） | 250 |
| `tests/persistence/test_mysql_saver_it.py` | 真库集成测试（`@pytest.mark.integration`） | 200 |

### 7.2 修改点（13 处，均"加分支不改原逻辑"）

| # | 文件:位置 | 改动 | 必改？ | 不改的后果 |
|---|---|---|---|---|
| 1 | `persistence/checkpoints.py:73-77` | 加 `BACKEND_MYSQL_SAVER="mysql_saver"`、`mysql` 作废弃别名、扩 `BACKEND_CHOICES`；**同时改 `active_checkpoint_backend()`（:234）**——它现在把 `BACKEND_MYSQL` 判成旧快照，改名后不改会一直返回错误的后端名 | ✅ | 启动日志与实际后端不符，排查全歪 |
| 2 | `checkpoints.py:170` `resolve_checkpoint_backend` | 加 mysql_saver 分支：读 `checkpoint.mysql.*`、`per_pod=False`、`kind` 返回 `"mysql_saver"` | ✅ | — |
| 3 | `checkpoints.py:264` `validate_checkpoint_deployment` | mysql_saver 直接 `return False`（共享后端不需要 `{pod}` / 粘性）；**sqlite 分支原样保留**（回滚要用，见 §17） | ✅ | 生产多副本启动即失败 |
| 4 | `checkpoints.py:612` `start_checkpoint_backend` + `:671` `_list_thread_ids` + `:680` `prune_deleted_session_threads` + `:738` `start_checkpoint_gc` + `:802` `adelete_threads` | 把 `_sqlite_saver is not None` 的判据换成"共享 saver 存在"（`_shared_saver`）；**mysql_saver 不再走 `_list_thread_ids`（那是全表 distinct + N+1，见 R23），改为一条库内 JOIN**；GC 循环追加 TTL 阶段 | ✅ | GC 不启动 / 抛 AttributeError / 全表扫描拖垮库 |
| 5 | **`agent/core.py:188` `_shared_graph_checkpointer`** | `sqlite_checkpointer_active()` → `shared_checkpointer_active()` | ✅ | **静默退化**：每会话重建编译图，图缓存全废 |
| 6 | **`services/sessions/compaction.py:45` `supported()` + `:52` `records()`** | 两处都把 `sqlite_checkpointer_active() and detect_worker_count()==1` 当判据 → 改成 `shared_checkpointer_active()`，并**去掉 `worker_count==1`**（共享 saver 支持同进程多 worker）。**互斥不需要新增**：`_execute` 里已经在用 `try_acquire_session_stream(sid, owner, …)`（`session_runtime` 表实现的跨进程租约），压缩与普通对话抢的是同一把锁，见 §2.1.3 | ✅ | **静默退化**：上下文压缩整体失效（而且不报错，只有摘要延迟）；放开判据后若不加锁则两 Pod 同时压同一 thread |
| 6b | **`services/sessions/thread.py:56` `session_thread_in_use`** | `sqlite_checkpointer_active()` → `shared_checkpointer_active()`。**这是"会话线程被关掉"的直接原因**（§2.1.1），影响 3 个调用点：`chat_turn.py:400` 的审批兼容核对、`chat_turn.py:728` / `experts/runtime.py:346` 的"新消息丢弃挂起审批" | ✅ | 会话线程关闭 → 退回每条消息一个 thread，跨 Pod 恢复 HITL 的整个前提消失（**本次改造等于白做**） |
| 7 | **`app.py:373` `_start_checkpointing`** | 现在 `except Exception` 一律"回退业务库 MemorySaver 快照"，新后端必须**只对 sqlite 保留降级，mysql_saver 一律 fail-fast** | ✅ | 后端起不来时静默退回进程内存储 → 一半 Pod 写库、一半写内存，**比启动失败严重得多** |
| 8 | **`services/agents/chat_turn.py:228` `reject_concurrent_stream`** | 进程内 `is_session_streaming()` → **先查库 `is_stream_active()`**（跨 Pod），且在 `open_turn_session` / `add_message` **之前** | ✅ | **去掉粘性后这条并发互斥完全失效**，两个 Pod 同时跑同一会话（§14.4-A） |
| 9 | **`api/chat.py:201` `_drive_stream` 的 `SessionBusyError` 分支 + `:130` `_stream_heartbeat`** | ① 不再是"静默 `put_nowait(None)`"，改成推一条明确 error 帧；② 续约失败（`renewed=False`）时**主动取消本后台任务**，不要"停止续约但继续跑" | ✅ | 用户看到"消息发出去了没回复"；更糟：失锁后两个 Pod 同时写同一会话（§14.4-B/C） |
| 10 | **`bootstrap/runtime_features.py:82`** | `multi_replica_errors()` 里写死的 "HITL checkpointer…不能跨副本共享" 要按后端判断；否则将来把 `EASY_MULTI_REPLICA_STRICT=1` 打开时，明明已经共享了却仍报错 | ⚠️ | 严格模式无法启用 |
| 11 | **`tests/conftest.py:44`** | `EASY_CHECKPOINT_BACKEND=auto` → **`sqlite`**（显式），避免"业务库是 MySQL 的测试环境"被 auto 带去连真库建表 | ✅ | 单测/CI 变成"必须有 MySQL 才能跑" |
| 12 | **`db/schema.py`（删表）** | 见 §15.2：`SCHEMA_VERSION` 16→17、`_create_agent_checkpoints_table` 移除、`EXPECTED_TABLES` 移除、`_RETIRED_TABLES` 加回 | ✅ | 快速启动路径永远命中不了，每次启动跑全量建表 |

**明确不用改**：
- `services/sessions/thread.py:283` `ensure_approval_on_this_instance` —— 它用 `per_pod_checkpoints_active()` 守卫，共享后端该函数返回 `False`，自动 `return`，审批归属校验自然失效（这正是我们要的）。
- `services/sessions/thread.py:82` 的 `isinstance(checkpointer, PersistentMemorySaver)` —— **这个要改**（类被删了），改成 `isinstance(checkpointer, MemorySaver)`（进程内 saver 没有跨会话持久化能力，仍退回每消息一个 thread）。列入 §15.3 的删除清单。
- `persistence/thread_ids.py` —— thread 命名规则与存储无关。
- 前端 —— **除了 §14.4-D 那一处"打开会话时探一次流状态"之外，零改动**。
- sqlite 路径本身 —— 一行不动（回滚要保证它可用，§17）。

### 7.3 P0 前置重构（建议先做，约 40 行，行为零变化）

把模块级 `_sqlite_saver` 升级为语义中性的 `_shared_saver`，`sqlite_checkpointer_active()` 增加同义函数 `shared_checkpointer_active()`。

**为什么必须先做**：现在有 6 处判据是"是不是 sqlite"，而这个判据真正想表达的是"有没有一个进程级共享、可跨会话复用的官方 saver"。不先抽象，每加一个后端都要重复改一遍 `_list_thread_ids`、GC 判据、图缓存、压缩——这次修完，下次换存储还会再踩一遍。

> **v5 补充**：这 6 处里，`session_thread_in_use`（`thread.py:56`）是**功能开关**而不是性能开关——它一旦返回 False，会话线程就关了（§2.1）。所以 P0-d（三处判据）不是"顺手抽象"，它是**在真库就绪之前就能验证"换后端不会关线程"的唯一手段**：先用一个假的共享 saver 桩把判据跑绿，再接 MySQL。

---

## 8. 部署与运维变化（对应你的第 2、3 条）

### 8.1 可以拆除的东西

| 现状 | 变化 |
|---|---|
| Ingress 基于 cookie 的会话保持（`EASY_STICKY_SESSIONS=1`） | **不再被依赖**（mysql_saver 分支不校验），但 **v3 建议先保留 annotation**——它是回滚到 sqlite 的前提，摘掉会让回滚变成"改 Ingress + 改配置"两步（§17.2） |
| `WEB_CONCURRENCY=1` / `UVICORN_WORKERS=1` 的强制约束 | **可以放开**（共享 saver 支持同 Pod 多 worker；但建议仍保持 1，因为进程内图缓存与 `_operations`（压缩去重）都会重复，`compaction.supported()` 还硬性要求 `detect_worker_count()==1`） |
| `checkpoint.sqlite_path` 里的 `{pod}` 占位 | mysql_saver 下不再必需；**但 sqlite 分支（含回滚）仍然要求它**，不要删 |
| 每次 Pod 重建导致"在途审批失效" | 消失：状态在库里，新 Pod 直接接手 |
| HPA 扩缩容触发审批丢失 | 消失 |

### 8.2 与业务库共库（**已确认**）意味着什么

checkpoint 的 4 张表就建在业务库里，用 `cp_` 前缀隔离。好处是不新增实例、不新增备份策略、不新增账号；代价是**资源与故障域完全共享**，逐项说清：

| 共享的东西 | 具体影响 | 应对 |
|---|---|---|
| **连接数** | 现有 `DBUtils` 同步池 + 新增 aiomysql 池，两套都连同一个 MySQL。10 副本 ×(20 业务 + 10 checkpoint) = 300 连接 | 确认 `max_connections` 余量 ≥ 30%；`pool_max_size` 从 10 起，不够再调；把 `read_timeout=10` 配上，避免慢查询长期占连接 |
| **buffer pool** | `LONGBLOB` 走 off-page，但读写仍消耗 buffer pool。业务热表（`sessions`、`session_messages`）可能被换出 | 确认 `innodb_buffer_pool_size ≥ 4GB`；观察 `Innodb_buffer_pool_read_requests` 增长；必要时把 `cp_` 表单独放一个 `TABLESPACE` |
| **binlog / 主从** | MB 级 blob 的写入会进 binlog；若有只读备库，主从延迟会被 checkpoint 写入拉高 | 监控 `Seconds_Behind_Master`；若延迟明显，评估把 `cp_` 表排除在同步链路外（备库不需要 checkpoint） |
| **`max_allowed_packet`** | 默认 64MB（8.0）。工具返回大文件时单条 blob 可能接近上限 | 调到 128MB；应用侧对超限 blob 记录明确错误（不要把 `Packet too large` 直接抛给上层） |
| **DDL 锁** | `auto_setup` 建表时 `CREATE TABLE` 需要短暂 metadata lock | 用 `GET_LOCK()` 串行化；只在启动期做，且是 `IF NOT EXISTS`（已存在时秒过） |
| **权限边界** | 应用账号现在对业务库有 DDL 权限（已确认），意味着一次错误的迁移脚本可能影响业务表 | **DDL 语句硬编码为模块常量，不接受外部输入**；迁移只做 `CREATE TABLE IF NOT EXISTS` 与加列，**永不做 `DROP`/`TRUNCATE`**；表名一律拼接 `table_prefix` 并校验前缀非空 |
| **故障域** | MySQL 挂了 = 业务和 checkpoint 一起挂。对 HITL 来说这没问题（业务也停了），但要写进可用性说明 | 无额外动作 |
| **审计** | 审批内容现在在业务库里，任何有库权限的人都能读到（R17） | 确认是否需要行级审计 |

### 8.2.1 `auto_setup` 自动建表（**已确认开启**，同时保留 SQL 脚本）

两条腿都走，各司其职：

| 方式 | 用途 | 谁执行 |
|---|---|---|
| `auto_setup: true`（默认） | 启动时幂等建表 + 应用未执行的迁移；**多开发/测试环境零手工步骤** | 应用自己（`asetup()`） |
| `scripts/db/mysql-checkpoint-ddl.sql` | **审计基线**：变更评审时 DBA 可以 diff；新建环境可以一次性预建；出问题时能快速确认"库里到底该长什么样" | 人 / DBA |

`asetup()` 的建表流程（`auto_setup=true`）：

```
1. SELECT GET_LOCK('<db>.cp_ddl', 60)          -- 连接级命名锁，串行化多 Pod 并发启动
2. SELECT meta_value FROM cp_checkpoint_meta WHERE meta_key='schema_version'
   -- 表不存在 / 无此行 → 视为已应用 0 条
3. 对未应用的迁移逐条执行（每条一个事务），全部走 CREATE TABLE IF NOT EXISTS（幂等）
4. INSERT ... ON DUPLICATE KEY UPDATE 写回 schema_version + ddl_applied_at
5. SELECT RELEASE_LOCK(...)
6. 校验 4 张表存在 + collation 是 utf8mb4_bin + ns_hash_algo 与代码一致，任一不满足 → 启动失败
```

> 注意第 2 步读的是 `cp_checkpoint_meta`，**不是** v3 里那张单列的 `cp_checkpoint_migrations`（那是笔误/旧稿，v4 已统一，见 §4.4）。`cp_checkpoint_meta` 本身也是幂等 `CREATE TABLE IF NOT EXISTS` 建的，所以"第一次启动时它还不存在"这件事不需要特殊处理——第 3 步会先建它。

四个必须遵守的细节：

1. **`GET_LOCK` 必须在同一个连接上 `GET` 和 `RELEASE`**（MySQL 命名锁是连接级的）。`aiomysql` 池里要显式 `async with pool.acquire() as conn` 拿同一条。
2. **抢锁超时 = 启动失败**，不能静默跳过建表——否则该 Pod 会在第一次写 checkpoint 时报 `Table doesn't exist`，错误点远离根因。
3. **迁移只允许"加"**：加表、加列、加索引。**禁止** `DROP COLUMN` / `MODIFY` 缩小列宽。`auto_setup` 在 10 个 Pod 上跑 10 次，任何非幂等语句都会放大成事故。
4. **迁移 SQL 里含中文注释时，执行连接必须是 `utf8mb4`**（§4.3.2 约束 1）。`aiomysql` 池已设 `charset='utf8mb4'`，所以 `auto_setup` 这条路是安全的；**但 `scripts/db/mysql-checkpoint-ddl.sql` 由 DBA 用命令行执行，必须自带 `SET NAMES utf8mb4;`**，否则注释进库就是乱码，而且不报错。另外：**后续迁移不要用 `MODIFY COLUMN` 改注释**——它会用新定义整体替换列定义，漏写 `NOT NULL` 就等于悄悄去掉约束（§4.3.2 约束 3）。

> 即使有 DDL 权限，`auto_setup=true` 也只做**幂等的最小集**。需要停机变更的结构（如主键调整）仍然必须走 SQL 脚本 + 人工窗口。

### 8.3 存储与备份

- **与业务库同库（已确认）**：直接沿用现有备份/恢复策略，这是相对 Redis/MongoDB 方案最大的运维优势——不需要新增备份清单，也不需要单独做恢复演练脚本。
- 恢复演练时注意：`cp_*` 表与业务表在同一个备份集里，**恢复会把 checkpoint 一起回退到备份点**。如果备份点早于最后一次审批，用户点"继续"会失败。这与"数据库整体回滚"的业务影响一致，不需要额外处理，但发布/演练说明里要写一句。
- `binlog` 保持现状；`record_ttl_days>0` 时大事务删除会放大 binlog 体积，因此设置了 `ttl_batch_size=200` 分批提交。

---

## 9. 潜在问题与风险（对应你的第 5 条，这部分请重点看）

### 9.1 高危（上线前必须解决）

| # | 风险 | 触发条件 | 影响 | 对策 |
|---|---|---|---|---|
| R1 | **切换瞬间的在途审批读不到**（v3 改口） | 生产当前跑 `sqlite`（每 Pod 一个 `.db`），在途审批落在 Pod 的本地文件里；切到 `mysql_saver` 后新 Pod 读的是 `cp_*` 表 | 用户点"继续"报"找不到审批状态"，需重新发消息 | **已决策：接受，不做迁移**（见 §9.1.1）。动作只有两步：① 发布窗口选在无在途审批时（发布前查一次 `pending_approval` 计数）；② 发布后首小时只观察，不自动处理。**注意：这与"`agent_checkpoints` 旧快照"无关——那张表从未上线**（§15.1） |
| R2 | **同一会话被两个 Pod 同时跑** | 用户连点、重试、或粘性去掉后负载均衡把同一会话的两个请求分到不同 Pod | 两路流同时写同一 `thread_id`，checkpoint 分叉 → 上下文错乱、审批徽标错乱 | **升级为 v3 的 P0-c**（§14.4）：① `reject_concurrent_stream` 改成查库并在落库前 409；② `SessionBusyError` 推明确 error 帧；③ 失锁自杀；④ TTL 30min→180s。**只靠现有 DB 流锁不够**——它只保证"不会两路都写成功"，但"抢锁失败"这一侧现在会静默吞掉 |
| R3 | **半截 checkpoint**（行在、blob 缺） | `aput` 的三次写入未包在同一事务 | 恢复时 `channel_values` 缺字段，模型看到空历史 | 强制单事务提交；集成测试加"并发 aput + aget_tuple"用例 |
| R4 | **`checkpoint_ns` 超长插入失败** | 子图嵌套很深（`tools:<uuid>｜model:<uuid>｜...`） | 写入报 `Data too long`，该轮无法保存 | **已规避**：原值列用 `MEDIUMTEXT`（实测 2000 字符可写），主键只用 16 字节 hash；但要在集成测试里固定一条深嵌套用例，防止有人把列改回 `VARCHAR` |
| R5 | **collation 不匹配导致主键冲突** | DBA 建表未显式指定 `utf8mb4_bin` | `thread_id` 大小写不同的两条会话互相覆盖（社区包真实 bug） | §5.8 的启动期校验 + DDL 显式声明 |
| R6 | **delta 通道丢历史**（P2 引入） | `keep_latest` 删掉了快照链中间的 checkpoint | 长会话突然"失忆"，且不报错 | P2 只实现 `delete` 策略；`keep_latest` 直接抛 `NotImplementedError`（§5.7） |

### 9.1.1 切换决策：放弃在途审批，不写迁移脚本（**已确认**）

**决策**：不做 checkpoint 数据的任何迁移（既没有 pickle→3 表的迁移，也没有 sqlite→MySQL 的搬运），也不做"旧记录标记失效"的脚本。

**代价与边界**（写清楚，避免误判为"有 bug"）：

| 场景 | 切换后的表现 | 是否可接受 |
|---|---|---|
| 用户在切换前已触发审批、还没点确认 | 点"继续"会 409（找不到 checkpoint），提示"审批已失效" | ✅ 接受：让用户重新发一次消息即可，审批本身有业务时效 |
| 切换前已完成的历史会话 | **完全正常**：历史消息来自 `session_messages`（业务表），与 checkpoint 无关；点开会话时 `plan_turn` 发现 thread 为空 → **从业务库水合**（`mode="hydrate"`） | ✅ 无影响（这也是 §17 回滚可行的同一机制） |
| 切换前正在跑回合（有流锁） | 该回合的 checkpoint 落在旧后端，新 Pod 接不回来；但 `stream_events` 仍在库，前端能看到已产生的增量 | ✅ 接受：发布前确认无在跑回合 |
| 想回滚到 `sqlite` | 历史照常可读（水合），只有切换后新产生的在途审批读不到 | ✅ 回滚成本很低（§17），不是"丢新数据" |

**执行清单（发布时照着做）**：

1. 发布前 30 分钟：`SELECT COUNT(*) FROM session_messages WHERE ...` 里 `pending_approval` 非空的行数（口径见 `last_message.pending_approval`），结果记入发布单；>0 就推迟窗口。
2. 发布前确认 `session_runtime` 无活跃流锁：`SELECT COUNT(*) FROM session_runtime WHERE stream_until > ?`（UTC 当前时间；注意 `stream_until` 是**带时区的 ISO 字符串**，不是 `DATETIME`，别用 `NOW()` 直接比）。
3. 发布：全部 Pod 一次性切换后端（**不做灰度**：灰度期跨 Pod 恢复不成立，见 §10）。
4. 发布后首小时：观察日志里"审批已失效"的条数；预期 0~少量，超过 5 条回滚（§17）。
5. `agent_checkpoints` 表由 `SCHEMA_VERSION` 升级自动 `DROP`（§15.2）——里面本来就没有线上数据。

### 9.2 中危（要监控/要有预案）

| # | 风险 | 说明 | 对策 |
|---|---|---|---|
| R7 | **连接数翻倍** | 业务库一套 `DBUtils` 同步池，checkpoint 一套 aiomysql 池，都连同一个 MySQL | 总连接 = pod 数 ×(业务池 + checkpoint 池)。按 10 副本 ×(20+10) 估算，需确认 `max_connections` 余量 |
| R8 | **写放大与 SQL 量** | 一个 agent 回合约 10~30 步，每步 1 次 `aput` + 1 次 `aput_writes`，每次 2~4 个 SQL → 单轮 60~120 个 SQL。按 10 个会话同时进行、每轮约 30 秒估算，约 **20~40 QPS**，MySQL 轻松承受；真实瓶颈更可能在连接数（R7）而非 QPS | 把 `pool_max_size` 与 `read_timeout` 配对设置，避免慢查询占满连接 |
| R9 | **大 blob 拖慢同步** | `binlog` 写入 + 主从同步（若有备库）被 MB 级 blob 拖慢 | 若存在只读备库，考虑把 checkpoint 库排除在同步链路外（业务可接受"备库无 checkpoint"） |
| R10 | **读一致性** | `REPEATABLE READ` 下若复用连接未 `commit`，读到旧快照 | 池统一 `autocommit=True`，读操作显式开只读事务 |
| R11 | **全局 GC 多 Pod 同时跑** | 现在是 per-pod 文件，各删各的；改成共享表后 N 个 Pod 会同时 `DELETE` 同一批数据 | 用现有 `distributed_lock` 选主，只让一个 Pod 跑 GC；或改成"每个 Pod 只处理自己 last_seen 的 thread"（复杂度更高，不推荐） |
| R12 | **GC 删正在写的 thread** | 会话删除与会话写入并发 | `adelete_thread` 前先查 `session_runtime` 流锁；活跃会话跳过本轮 |
| R13 | **checkpoint 只增不减** | 没有 TTL，`aprine` 只做 `delete` | 上线即开启 `gc_interval_seconds` 做"会话已删除"对账；`record_ttl_days` 默认 **0**（不启用），原因见 §5.7.1 与 R21 |
| R14 | **MySQL 8.0.25 已知限制** | 该版本不支持 `utf8mb4_0900_as_cs` 的部分排序行为差异；`innodb_redo_log_capacity`（8.0.30+）不可用，大事务写入受 `innodb_log_file_size` 限制 | DDL 用 `utf8mb4_bin` 规避第一个；单事务只写一个 checkpoint（不批量），规避第二个 |
| R21 | **TTL 让"会话还在列表里、点进去是空的"** | 启用 `record_ttl_days>0` 且按月清理，但 `sessions` 行没有同步清理 → 标题还在、内容没了，容易被当成数据丢失 | ① 本期默认 0；② 未来启用时**只清 `archived=1`**；③ 若清全部，必须同时上线会话保留策略（`sessions` 行一起归档） |
| R22 | **`sessions.updated_at` 是 `VARCHAR(50)` 不是 `DATETIME`** | 按它判过期只能字符串比较；ISO 串字典序 = 时间序**只在格式完全统一时成立**（`Z` 后缀 / 毫秒位数不一致 / 时区偏移都会让比较出错，导致误删或永不删） | 启动期用一条真实行做探针 `SELECT STR_TO_DATE(updated_at,'%Y-%m-%dT%H:%i:%s') FROM sessions LIMIT 1`，失败则降级 `ttl_basis=checkpoint_created_at` 并打 WARN |
| R23 | **GC 变成全表扫描** | 照搬 `_list_thread_ids()` 就是 `SELECT DISTINCT thread_id FROM cp_checkpoints`（全库所有 thread）+ 应用层逐个查 `sessions`（N+1）；几十万 thread 时每 600 秒烧一次 CPU 与 IO | 用 §4.3 的 `session_id` 冗余列改成库内一条 JOIN（`idx_session` 可用），并对账与 TTL 都加单轮上限 |
| R24 | **共库 + DDL 权限 = 误操作波及业务表** | 迁移代码有 bug，或 `table_prefix` 被配成空串时，最坏会把 `DROP`/`ALTER` 打到 `sessions` 上 | ① 迁移只允许"加"（§8.2.1）；② `table_prefix` 为空**拒绝启动**；③ 单测断言生成的表名全部以 `cp_` 开头 |

### 9.3 低危 / 需要注意

| # | 项 | 说明 |
|---|---|---|
| R15 | 时区 | 全部时间列存 UTC（`DATETIME(6)` + 应用层 `datetime.now(UTC)`），不做 `TIMESTAMP` 自动转换，避免容器时区不一致导致的 `alist` 排序错乱 |
| R16 | 回滚路径（**v3 修正结论**） | 切回 `sqlite` **不会**导致"最近的会话打不开"：`plan_turn` 在 thread 为空时会从 `session_messages` 水合（`services/sessions/thread.py:189-192`）。真正丢的只有**在途审批**与**上下文压缩记录**（`context_compactions` 存在 checkpoint 里）。前提是回滚时 Ingress 粘性还在（§17.2）。**建议不做双写**——双写带来的"两边都可能读到旧数据"比回滚本身更危险 |
| R17 | 审计合规 | 审批状态跨 Pod 可见后，**任何有库权限的人都能看到全部审批内容**；如果原来靠"卷隔离"形成的事实隔离，现在消失了。需确认是否要加行级审计 |
| R18 | 旧快照后端已删除 | `mysql_snapshot` 这个值不存在了（旧实现整条删除，§15）。**不做双写**；回滚目标是 `sqlite`（§17），不是旧快照 |
| R19 | `deepagents` 的 `DeltaChannel` 参数 | `snapshot_frequency` 若被上游调大（>50），delta 回溯次数会上升；P1 阶段（走基类）延迟会先恶化再被 P2 修复 |
| R20 | DDL 锁竞争 | `auto_setup=true` 时，多 Pod 同时启动会并发执行 DDL → 用 `GET_LOCK()` 串行化（MySQL 命名锁），避免重复建表报错 |

---

## 10. 分阶段实施（**P1 + P2 同版本发布，一次到位**）

`P1` 与 `P2` 在开发上仍分两步走（先保证正确，再优化性能），但**同一次发布上线**，不拆成两个版本——否则 P1 上线后长会话用户会先经历半秒白屏，等 P2 才恢复。

| 阶段 | 内容 | 交付判据 | 估时 |
|---|---|---|---|
| **P0-a** | `_shared_saver` 抽象（§7.3） | 现有 checkpoint 用例全绿，行为无变化 | 0.5 人日 |
| **P0-b** | **删旧快照后端**（§15）：`PersistentMemorySaver`、`agent_checkpoints`、`mysql` 旧分支 | 全仓 `grep PersistentMemorySaver` 为空；`SCHEMA_VERSION=17` 能升级库；启动日志只打印三种后端 | 0.5 人日 |
| **P0-c** | **跨 Pod 并发互斥**（§14.4 A/B/C）：`reject_concurrent_stream` 查库、`SessionBusyError` 推 error 帧、失锁自杀 + 租约收紧 | 双 Pod 手测：并发第二条消息返回 **409**、库中无孤儿用户消息；kill -9 持锁 Pod 后 ≤3 分钟可接管 | 1 人日 |
| **P0-d** | **三处会话线程判据**（§2.1.2）：`shared_checkpointer_active()` + `thread.py:56` + `core.py:184` + `compaction.py:45,52`（含去掉 `worker_count==1`、压缩的乐观校验） | 用 `sqlite` 后端跑：会话线程照常开；把 `EASY_CHECKPOINT_BACKEND` 临时指向一个"假共享 saver"（单测桩）时**不再是 `per-message`**；压缩在 `WEB_CONCURRENCY=2` 下可用 | 1 人日 |
| **P1** | DDL（4 张表，含表/列注释与 `auto_setup`）+ `AsyncMySQLSaver` 六方法（`asetup/aput/aput_writes/aget_tuple/alist/adelete_thread`）+ 改造点 + 配置 + **统一日志（§19）** + 单测 + 真库集成测试 | 单机跑通"发消息→中断审批→重启进程→恢复审批"；3 个 Pod 手测跨副本恢复通过；`information_schema` 里 4 表注释与 30 列注释全非空；日志里能按 `session=` 串出完整时间线 | 5~6 人日 |
| **P2** | `aget_delta_channel_history`（两阶段递归 CTE）+ `aprine`（仅 `delete`）+ GC 库内 JOIN 改造 + `record_ttl_days`（默认 0） | 120 轮会话打开：跨网络 <100ms（P1 走基类基线 <1s）；GC 单轮不产生全表扫描 | 1.5~2 人日 |
| **P3** | **端到端脚本与 T1~T14（§21）** + 默认值切换 + 摘粘性 annotation（可选）+ 发布说明 + 切换清单（§9.1.1、§20）+ **回滚演练（§17）** | 生产 2 副本跑 7 天，HITL 恢复成功率 100%，无孤儿 thread 增长；`resume_approval hit=false` 为 0；回滚演练一次成功 | 1.5 人日 |

**合计约 10~12 人日**（不含评审与联调排队；比 v4 多 1.5 人日 = P0-d + 可观测性与端到端脚本）。

> **P0-c 不是"顺手做"**：它跟 checkpointer 是两件独立的事，但**只有一起做才有意义**。共享 checkpoint 解决了"能不能在别的 Pod 恢复"，而 P0-c 解决"会不会有两个 Pod 同时在跑"。前者上线、后者不做，等于把"审批恢复不了"换成了"上下文被另一条流覆盖"——后者更难查。所以 P0-c 必须在 P1 之前完成并被单测覆盖（它的测试不需要 MySQL，可以用 fake db 断言"落库之前就抛 409"）。

**灰度与回滚**：`EASY_CHECKPOINT_BACKEND` 是环境变量，技术上可以按 Pod 灰度（先 1 个 pod 用 `mysql_saver`，其余 `sqlite`）。但注意：**灰度期跨 Pod 恢复是不成立的**（走 sqlite 的 pod 仍看不到），所以灰度只能验证"新后端自身可用"，不能验证"跨 Pod 恢复"。**真正的验收必须一次性全量切换**（现网 `replicas: 2`，同一 Deployment 里的 Pod 各自读同一个 ConfigMap，做不到按 Pod 分环境变量，所以实际只有"全量切"与"全量回"两个动作）。

**同库共用 + TTL 的启用节奏**：本期 `record_ttl_days=0`，即 TTL 代码交付但**不开启**。观察 3 个月真实增长后，再按 §5.7.1 的三重保护逐条打开。

---

## 11. 验收标准（测试矩阵）

> **本章是"技术验收"，§21 是"场景验收 + 怎么测"**——本章回答"每个方法对不对"，§21 回答"两台机器上这个场景成不成立"。发版前必须两边的判据都满足；§21.6 是最终判定标准。

| 层级 | 用例 | 判据 |
|---|---|---|
| **会话线程（v5 新增，P0-d）** | ① `shared_checkpointer_active()` 在 sqlite / 假共享 saver 下为 True、在 legacy 与 `PersistentMemorySaver` 下为 False；② 用一个"假共享 saver"桩时 `session_thread_in_use()` 返回 True；③ 压缩 `supported()` 在 `WEB_CONCURRENCY=2` 且共享 saver 下为 True | 三条都过。**这是"换后端不会把会话线程关掉"的唯一自动化证据**（§2.1） |
| 单元（无 DB） | `aput` 的 channel 拆分（标量内联 / 对象进 blob / `_DeltaSnapshot` 置 True）；writes 的两种冲突策略选择；ns hash 稳定性；`alist` 的 `before` 游标；**delta 祖先链的 visited 防环**；**TTL 选 thread 的 WHERE 条件拼装（含 `archived` 与流锁过滤）** | fake cursor 断言生成的 SQL 与参数 |
| 集成（真库） | ① 完整往返 ② 同 version 重复写不重复存 payload ③ 同 interrupt 重复写要覆盖 ④ 并发 aput + aget_tuple 无半截 ⑤ 深嵌套 ns（2000 字符）可写 ⑥ collation 大小写 ⑦ `adelete_thread` 三表清空 | 全部通过；①~⑦ 各一条断言 |
| 集成（delta，P2 必测） | 构造 **60 层祖先链**（真实 `DeltaChannel`，跨 2 个 `_DeltaSnapshot` 边界），对比"`aget_delta_channel_history` 覆写版"与"基类逐条回溯版"的返回值 | 两者**逐条相等**；覆写版 SQL 次数 ≤3；消息顺序与写入时一致 |
| 集成（TTL，默认关闭，开启前必测） | 造 3 个 thread：① 过期且 archived ② 过期但 `archived=0` ③ 过期但有活跃流锁。跑一轮 TTL | 只删①；②③ 保留；删除后三表无残留、`cp_checkpoints` 无孤儿父 id |
| 多副本（手测） | 3 个 Pod × 1 worker：A 发消息触发审批 → 直接在 B 完成审批 → C 上查历史 | 三向都对；`logs` 中无"找不到审批状态" |
| **跨 Pod 并发（v3 新增，P0-c）** | ① A 还在跑时，B 上发第二条消息 → 断言 **409**，且 `session_messages` 里**没有**第二条用户消息；② B 上点同一审批两次（双端）→ 只有一次生效，另一次明确报错；③ 持锁 Pod `kill -9`，另一 Pod 在 **≤3 分钟**内可接管（租约收紧后）；④ 旧 Pod 失锁后不再写 `stream_events` | 四条都要过。这是去掉粘性会话后**唯一新增**的并发面，必须有自动化用例（可用 fake db 断言"抛 409 发生在 `add_message` 之前"） |
| **回滚演练（v3 新增，§17）** | `mysql_saver` 跑出 3 个会话 → 改 `EASY_CHECKPOINT_BACKEND=sqlite` 重新发布 → 打开老会话继续问 → 再切回 `mysql_saver` | ① 老会话历史完整（水合生效）；② 在途审批报明确的 409 而不是 500；③ 切回后新会话可继续（旧 thread 仍在库里） |
| 故障注入 | ① 审批中途 kill 掉 A 的 Pod → 新 Pod 起来后继续审批；② MySQL 短暂不可达 → 启动即失败而非降级 legacy | ① 恢复成功；② 日志明确报"checkpoint 后端不可用"，**不出现静默降级** |
| 并发（建表） | 5 个进程同时启动、`auto_setup=true` | 全部成功，无 `Table already exists`；日志中能看到 4 个进程等锁 |
| 性能 | 120 轮长会话 `aget_state` 耗时；单轮回合 checkpoint SQL 条数 | P1（走基类回溯）：本机 <150ms、跨网络 <1s，作为基线记录；P2（覆写 delta）：**跨网络 <100ms**；单轮 SQL ≤120 条 |
| 数据完整性 | 跑 24h 后核对：`checkpoints` 有孤儿行（父 id 不存在）？`writes` 有孤儿（所属 checkpoint 已删）？ | **前者必须为 0**（父 id 只会指向同一 thread 内的 checkpoint，thread 删除是整体的）；**`blobs` 允许有孤儿**——并发分叉时某一支的 channel 值会没人引用，这是协议本身的产物，**不要按引用计数做 GC**（v3 更正 v2 的"孤儿率 0"） |

---

## 12. 对现有整体流程的影响预估（对应你的第 8 条）

| 环节 | 影响 | 评估 |
|---|---|---|
| **启动** | 新增"建池 + 版本校验 + 表校验 + collation 校验"，约 +0.2~1s；MySQL 不可达时**必须 fail-fast**（不能静默回退 legacy，否则多副本各写各的） | ⚠️ 需改 `app.py:375` 的 except 分支：现在所有异常都降级为快照，新后端要区分"配置错误/不可用 → 拒绝启动" |
| **对话回合** | 每步 +2~4 次数据库往返（本机 <3ms，跨网络 5~15ms）；相比 LLM 秒级延迟可忽略 | 低风险 |
| **流式输出** | 无影响（checkpoint 写入在图的节点边界，不在 token 流路径上） | 无 |
| **HITL 审批** | 最大收益点：任意 Pod 可恢复；`StaleApprovalError` 不再触发；`pending_approval.instance` 字段变为无意义（**建议保留字段、不再校验**，避免影响历史数据） | ✅ 目标达成 |
| **会话删除** | 从"删本 Pod 文件"变成"删共享表"，一次删除全局生效；GC 从"补删其他 Pod 残留"变成"全局对账" | 需重写判据（§7.2 第 4 项） |
| **上下文压缩** | 判据必须是"共享 saver 存在"；否则整体失效（静默） | ⚠️ 必须改 |
| **图缓存** | 同上；否则每会话重建图，首字延迟上升 | ⚠️ 必须改 |
| **定时任务** | `sched-` thread 每轮自删，逻辑不变；但删除现在是全局的，多 Pod 重复删不报错 | 无 |
| **checkpoint 清理（GC / TTL）** | GC 从"扫本 Pod 文件"变成"扫全库"，需改成库内一条 JOIN（否则全表 distinct + N+1，见 R23）；TTL 是**新增能力**，默认关闭 | ⚠️ 需改 + 需决策 |
| **历史回放/时间旅行** | `alist` 走新实现，分页语义要保证与前端一致 | 需回归 |
| **前端** | **只有一处**：打开会话时探一次 `/stream/status`，活跃就挂 `/stream/live`（不再依赖 `sessionStorage` 的在飞记录），见 §14.4-D | ⚠️ 小改 |
| **并发控制（v3）** | `reject_concurrent_stream` 从"进程内"变"查库"，慢一个 RTT（<3ms）；**收益是去掉粘性后不再有双流** | ⚠️ 必须改（P0-c） |
| **旧快照后端（v3）** | **整条删除**（`PersistentMemorySaver` / `agent_checkpoints` / `mysql` 旧值）；回滚目标是 `sqlite`，不是它 | 见 §15 / §17 |
| **备份/容灾** | 已确认同库，直接沿用业务库策略；注意"恢复数据库 = checkpoint 一起回退"（§8.3） | 低风险 |

---

## 13. 决策点（**v4 起全部确认，无待定项**）

### 13.1 已确认（本方案按此执行，**无待定项**）

| # | 决策 | 落地影响 |
|---|---|---|
| 2 | **checkpoint 表与业务库同库共用**（`cp_` 前缀隔离） | 配置里 `host/database` 留空即复用；DDL 需自带 collation；关注连接数/buffer pool/binlog（§8.2） |
| 3 | **`auto_setup: true`，自动建表**（生产账号有 DDL 权限） | §8.2.1 的 `GET_LOCK` 串行化流程；同时保留 `scripts/db/mysql-checkpoint-ddl.sql` 作审计基线 |
| 4 | **接受"切换时放弃在途审批"，不做迁移脚本** | §9.1.1 的执行清单；不迁移任何 checkpoint 数据 |
| 6 | **P1 + P2 本期一次到位** | §10 合计 8.5~10 人日 |
| 7（v3） | **后端名 `mysql_saver`，`mysql` 作废弃别名** | §16 |
| 8（v3） | **删除旧快照后端，不留逃生门** | §15 |
| 9（v3） | **回滚目标是 `sqlite`，且必须保留粘性 annotation** | §17 |
| **10（v4）** | **`record_ttl_days=0`，本期不启用 TTL** | 代码按 §5.7.1 实现但默认跳过；只跑"已删会话对账"；观察期动作见 §5.7.1 表 |
| **11（v4）** | **保留元数据表，形态改为 KV（`cp_checkpoint_meta`）** | §4.4；`asetup` 用它读版本号 + 校验 `ns_hash_algo` |
| **12（v4）** | **4 张表全部补表/列注释** | §4.3 已给出完整 DDL；三条约束见 §4.3.2 |
| **13（v4）** | **表数定为 4，不再合并** | §4.6；理论合并版仅作参考，不采用 |

### 13.2 `EASY_CHECKPOINT_BACKEND` 到底是什么（**你问的第 5 个问题**）

它选的**不是"连哪个数据库"**，而是"用哪一种 checkpointer 实现"；数据库连接是另一套配置（`database.mysql.*`），两者完全独立。

当前代码里的 4 个取值（`checkpoints.py:73-77`）：

| 值 | 含义 | 状态存哪 |
|---|---|---|
| `auto` | 业务库是 MySQL 就用 MySQL 快照，否则 SQLite | 跟随后两者 |
| `sqlite` | 官方 `AsyncSqliteSaver` | 本 Pod 的 `{pod}.db` 文件 |
| `mysql` | 自研 `PersistentMemorySaver` 全量快照 | 业务库 `agent_checkpoints` 表（pickle） |
| `legacy` | 进程内 `MemorySaver` | 内存，重启即丢 |

v2 纠结的"语义变更风险"（升级后 `mysql` 从旧快照突变到新实现）**在 v3 里不存在了**：旧实现在生产上从未运行过（§15.1），`mysql` 这个值只出现在未发布的 k8s yaml 里。因此 v3 的处理是：**新实现叫 `mysql_saver`，`mysql` 保留为别名并打 WARN**，具体见 §16。

### 13.3 全部已确认（v4 起无待定项）

`record_ttl_days` 的启用口径在 v4 定稿：**保持 `0`，本期不开启**。理由与观察期做法见 §5.7.1，摘录三点：

1. 本项目**没有会话过期/归档策略**，贸然启用会让"列表里还在的会话点进去是空的"，这在用户看来就是 bug；
2. 先跑"已删会话对账"（零风险）就能解决 90% 的"只增不减"；
3. 数据增量必须**实测**而不是估算——`record_ttl_days` 是个一旦打开就自动删数据的开关，先拿到 3 个月的 `cp_*` 表 `data_length` 曲线再定阈值，代价最低。

将来要启用时只需改一个值 + 重启（代码已实现），不需要再改数据结构。届时要先回答"是否接受 `archived=1` 的会话被清理"，因为 TTL 的语义建立在它之上。

---

## 14. 跨 Pod 并发与一致性（**你问的第 1 个问题**）

> 先把结论说清：**刷新 / 重新登录换到别的 Pod，读没问题、写只有一个真问题。**
> 读——消息、流事件、流状态三样全在业务库，任何 Pod 看到的都一样，`/stream/live` 本来就是"DB-first 回放"，不依赖粘性。
> 写——"老回合还在跑 + 新客户端又发一条"这个场景，今天靠**进程内变量**判断，去掉粘性后必然失效。这是 P0，必须跟着本次一起改（§14.4）。

### 14.1 先看数据分层：什么存在哪里

| 数据 | 存在哪 | 跨 Pod 一致 | 刷新后能接上 | 备注 |
|---|---|---|---|---|
| 会话消息正文（UI 权威） | `session_messages`（业务库，行表） | ✅ | ✅ | 前端渲染只认它 |
| 流式增量事件（thinking / tool_call / content / done） | `stream_events`（业务库，`event_id` 自增全局序） | ✅ | ✅ `/stream/live` 按 `event_id` 回放 | 模块注释原文："请求落到任一 Pod 都能得到一致的流状态与回放结果"（`api/chat_live.py:1-6`） |
| 流状态（谁在跑、租约到什么时候） | `session_runtime.stream_owner / stream_until`（业务库，`VARCHAR(50)` ISO 串） | ✅ | ✅ `/stream/status` | 判断口径是 DB，不是本 Pod 的 hub |
| **HITL checkpoint** | 现在：本 Pod 的 `{pod}.db`（sqlite）；旧快照：`agent_checkpoints` | ❌ → **本次改成 ✅** | ❌ → **本次改成 ✅** | 这是本次改造唯一改变的可观测行为 |
| 进程内 hub / agent 实例缓存 / `_sessions` 任务表 | 进程内存 | ❌ | — | 只影响"推送通道"，已被 `stream_events` 兜住 |
| 前端"在飞记录"（该去续接哪个会话） | `sessionStorage`（`streamResume.js` 的 `FLIGHT_KEY`） | ❌ | ⚠️ **换标签页/换浏览器/重登就丢了** | §14.4-D |

### 14.2 直接回答三个子问题

**(a) 刷新后落到不同 Pod，读会不会乱？**
不会。前端流程是：`/stream/status`（查库）→ 若 active 则挂 `/stream/live`（按 `event_id` 从库里回放）。两者都只依赖业务库。**续接能力跟粘性会话无关**，粘性只影响"有没有可能落到别的 Pod"。

**(b) 写会不会互相覆盖？** 逐张表看：

| 写入点 | 并发两路时会发生什么 | 现状 |
|---|---|---|
| `session_messages` 追加行 | 各自 insert，不覆盖 | ✅ 没问题 |
| 最后一条 assistant 的 `pending_approval` / `blocks` / `tool_calls` | `update_last_assistant_message` 是**整条 JSON 覆盖**（`chat_repository.py:92`），后写盖掉前写 | ⚠️ 靠 409 前置解决 |
| `abandon_pending_approval`（新消息到来把上轮审批标拒绝） | 同上，整条覆盖 | ⚠️ 同上 |
| `session_runtime.stream_owner/until` | DB 原子条件更新（`try_acquire_session_stream`），只有一个能拿到 | ✅ 已有 |
| **checkpoint（同一 thread）** | `session_thread` 默认开 → `thread_id = session_id` → 两路各写不同 `checkpoint_id`，形成**分叉链**；下一次"取最新"只能看到其中一支 | ❌ **本次新增的风险，必须靠 409 挡** |
| `stream_events` | 新一轮开始会 `DELETE FROM stream_events WHERE session_id=?`（整表清空） | ⚠️ 靠锁 + 失锁自杀（§14.4-C） |

**(c) 老回合还在跑、新客户端又发一条** —— 见 14.3 的时序，这是唯一必须动代码的地方。

### 14.3 现状时序：去掉粘性后必然踩到

```
① 用户 A 在 Pod1 发起「分析这份财报」→ 流还在跑，持锁 stream_owner=pod1, until=now+30min
② 用户刷新页面 / 换设备登录 → 请求落到 Pod2，看到历史（✅ 正常）
③ 用户等不及，在 Pod2 又发一条「顺便算下毛利率」
   Pod2 的处理链：
     open_turn_session()                ✅ 通过
     reject_concurrent_stream()         ← 读 is_session_streaming()：Pod2 的 _sessions 里没有这个会话 → False
     add_message(用户消息)               ⚠️ 消息已经落库
     返回 200 SSE，前端开始转圈
     后台 _drive_stream → aregister_stream_task → acquire_stream(DB)
        ❌ SessionBusyError（pod1 持有）
        → except 分支只做 queue.put_nowait(None)
        → SSE 静默结束，**没有任何 error 帧**
④ 前端 streamResume 探测：/stream/status = active（pod1 的锁）
   → 挂 /stream/live → 回放的是 **pod1 那条流的增量**
   → 用户看到「第二条消息下面出现的是第一个问题的答案」
```

**最后落在库里的状态**：`session_messages` 里多了一条**没有回复的用户消息**；thread 上 1 条流正常结束（另一条根本没跑）。用户观感是"串话 + 丢失"。

这不是"数据库同步没做好"，而是**并发互斥只做在了进程内**。粘性会话过去是"隐式的并发控制"——同一用户的请求都被钉在同一个 Pod，进程内判断就够用。**去掉粘性 = 把这道隐式保护拆掉了，必须显式补上。**

### 14.4 必须改的 4 处（P0-c，跟 checkpointer 一起上线）

> 本节只讲**并发互斥**。另外三处"会话线程判据"是另一件事（不是并发，是功能开关），见 §2.1.2 与 §7.2 第 6/6b 项——两者都在 P0 阶段落，但**判据不修，本节修得再对也没用**（线程关着，两个请求根本不在同一个 thread 上打架）。

#### A. 并发互斥改成"查库 + 落库前"（`services/agents/chat_turn.py:228`）

```python
# 现在（进程内，跨 Pod 无效）
def reject_concurrent_stream(session_id: str) -> None:
    if agent_manager.is_session_streaming(session_id):
        raise ConflictError("本会话正在生成回复，请等待完成或先取消当前生成")

# 改成（先查库；本进程判断保留，省一次 DB 往返）
async def reject_concurrent_stream(session_id: str, runtime) -> None:
    if agent_manager.is_session_streaming(session_id):
        raise ConflictError(...)
    if await run_blocking(is_stream_active, runtime, session_id):
        raise ConflictError("本会话正在其它实例上生成回复，请稍后再试")
```

两条调用点都要改：`prepare_turn`（`:492`）与 `prepare_resume`（`:829`）。**位置必须仍然在 `add_message`（`:732`）之前**——否则会留下一条没有回复的用户消息。也不能提到 `open_turn_session` 前面去：新会话的 `session_id` 是它产出的，提前调用拿不到 id（`request.session_id` 为 None 时无从检查，本来也不会撞流）。`reject_concurrent_stream` 的 docstring 已经把这个"必须早于落库"的理由写清楚了，只是它当初只考虑了"同 Pod"。

**局限要说清**：这是"早失败"，不是权威判定。`prepare_turn` 里还有技能解析、意图闸、建 Agent/MCP 等耗时动作（实测有卡 59 秒的案例），期间锁可能被别人抢走。**权威判定仍然是后台任务里的 `acquire_stream`**，所以 B 必须一起改。

> 想把窗口压到 0 也可以（方案 B）：把 `acquire_stream` 提前到 `prepare_turn` 开头、失败即 409，成功后由后台任务"接管"。代价：要给 `try_acquire_session_stream` 的 SQL 加"同一 owner 重复抢 = 续期"的语义（现在是同 owner 也判忙），还要在 prepare 抛异常的路径上补释放。**建议先用方案 A**（改动小、可回退），观察日志里 `SessionBusyError` 的次数再决定是否上 B。

#### B. `SessionBusyError` 不能静默结束（`api/chat.py:201`）

```python
except SessionBusyError as e:
    # 现在：只结束本请求队列，客户端拿到空流
    queue.put_nowait(None)
    # 改成：推一条明确的终态帧，前端按"本会话正在别处生成"提示 + 引导重新挂 /stream/live
    hub.broadcast(emitter.error("本会话正在其它实例上生成，请稍候；已生成的内容会自动同步"))
```

注意顺序：`aregister_stream_task`（抢锁）在 `_clear_previous_events`（清事件表）**之前**，这是对的——抢不到锁时绝不能清别人的事件表。**重构时别把这两句顺序调过来**，否则 Pod2 会把 Pod1 正在回放的事件全删掉。

#### C. 失锁后必须自杀 + 租约收紧（`api/chat.py:94/130`）

现在 `_stream_heartbeat` 续约失败时只是 `return`（停止续约），**流继续跑**。于是：Pod1 因网络抖动丢锁 → Pod2 在 TTL 到点后接管、清空事件表 → Pod1 还在往同一会话写事件、写 checkpoint。**双写**。

改法：续约失败 → `asyncio.current_task().cancel()`（或置一个 `lost_lease` 事件让 `_drive_stream` 退出），落 `run_status="failed"`，日志明确"租约丢失，已终止本轮"。**租约丢了就不该继续写**，这是租约的字面语义。

租约参数也要收紧（`db/repositories/session_runtime.py:13`）：

| 参数 | 现在 | 建议 | 理由 |
|---|---|---|---|
| `STREAM_TTL_SECONDS` | 30 分钟 | **180 秒** | Pod 崩溃后锁要挂 30 分钟才失效。这期间任何客户端 `/stream/status` 都是 active → 刷新后挂 `/stream/live` 会一直"生成中"，最长 30 分钟 |
| `_STREAM_HEARTBEAT_SECONDS` | 300 秒 | **60 秒** | 必须 ≤ TTL/3。心跳本身是一次 `UPDATE session_runtime`，每会话每分钟一次，可忽略 |

> 这个改动**独立于 checkpointer**，但**因为去掉粘性而变得必须**：以前崩溃只影响"钉在那个 Pod 的用户"，现在是"任何 Pod 都看到一个假活跃会话"。

#### D. 前端：打开会话就探一次流状态（不依赖 `sessionStorage`）

`streamResume.js` 的"在飞记录"存在 `sessionStorage`——**换浏览器、换标签页、重登后是空的**，于是不会自动续接（`waitForActive` 的短轮询也不会启动）。用户看到的是"停在半截的静态页面"，而后台其实还在跑。

改法很小：会话打开/切换时调一次 `/agent/chat/stream/status`，`active=true` 就挂 `/stream/live`（不管有没有在飞记录）。这条接口本来就是为这个场景设计的（`chat_live.py:58` 的 summary 写的正是"刷新页面后用于判断是否需要重新挂载到进行中的流式任务"）。两套前端（`frontend/` 与 `frontend-vue2/`）要同步改。

### 14.5 改完之后还剩什么（写进文档，避免被当成 bug）

| 残留 | 说明 | 要不要处理 |
|---|---|---|
| 分叉窗口 | 方案 A 下仍有"探测通过 → 抢锁失败"的极小窗口，结果是"消息落库 + 明确错误提示" | 可接受；日志计数，超过预期再上方案 B |
| 孤儿 blob | 并发分叉时被丢弃那一支的 channel 值没人引用，永久留着 | 不处理（thread 级删除兜底）；**不要按引用计数做 GC** |
| 工具副作用不回滚 | 被丢弃那一支如果已经调过工具（写文件、发通知），副作用留在外部系统 | 只能靠 409 减少概率；写操作类工具本身有 HITL 审批兜底 |
| 时钟偏差 | `checkpoint_id` 是 UUIDv6，跨 Pod 排序依赖 NTP | 集群内 NTP 是默认配置；只在分叉时才有观感差异 |
| 不做排队 | 被 409 挡住时用户只能等或手动重试，**不做"服务端排队"** | 排队需要跨 Pod 的任务队列（`runtime_features.py` 的 `run_queue` 还是 `none`，Phase 3 未做），本期不做 |

---

## 15. 删除旧快照后端（**你问的第 2 个问题**）

### 15.1 核实：它确实没上线

| 证据 | 内容 |
|---|---|
| 引入时间 | `602499e1`（2026-11-06 09:32，lulongfei）"fix(checkpoint): 修复 Pod 重建后审批恢复返回 409 —— 增加 MySQL 共享 checkpoint 快照后端及持久化表" |
| `SCHEMA_VERSION` 注释 | `db/schema.py:39`："15：恢复 agent_checkpoints，用于共享 MySQL HITL 状态快照" |
| 你的确认 | 2026-11-05 之后 lulongfei 的提交都**没有发布到线上**；线上没有在跑这张表 |

**结论**：`agent_checkpoints` 在线上**不存在**（连表都没有），因此
- 不需要"旧数据迁移"，也不需要"保留旧后端作逃生门"；
- v2 里 R1（旧快照不可读）、R18（双写）、§9.1.1 里"旧表保留 30 天"全部作废；
- 回滚目标不是它，是 `sqlite`（§17）。

### 15.2 删除清单

| # | 位置 | 内容 | 注意 |
|---|---|---|---|
| 1 | `persistence/checkpoints.py:343-~500` | `class PersistentMemorySaver(MemorySaver)` 整个类（全量 pickle、3 thread 上限、12MB 静默跳过、`_hydrate`/`_flush`） | 顺带删 `_CHECKPOINTERS` / `_CHECKPOINTER_MAX` / `_PERSIST_MAX_THREADS` / `_PERSIST_MAX_BYTES` 及其注释 |
| 2 | 同上 `:73-77` | `BACKEND_MYSQL = "mysql"`（旧语义）、`_mysql_snapshots_active` 全局 | `mysql` 这个**字符串**要保留为别名（§16），只是不再指向旧实现 |
| 3 | 同上 `:192-200` `:640-660` `:585` | `resolve_checkpoint_backend` / `start_checkpoint_backend` / `_describe_target` 里的旧快照分支 | 改成 mysql_saver 分支 |
| 4 | 同上 `:765-795` | `get_session_checkpointer` 的 `_mysql_snapshots_active` 分支、`refresh_session_checkpointer` | 后者**直接删**（共享 saver 不需要"刷新快照"），3 个调用点一并删：`chat_turn.py:750-752`（新消息）、`chat_turn.py:859-861`（恢复审批）、`tests/db/test_checkpoint_roundtrip.py:65` |
| 5 | 同上 `:592` `:608` `:653` | 三条日志文案 | 改成 `AsyncMySQLSaver（共享 MySQL，cp_* 4 表）` |
| 6 | `db/repositories/agent_state.py` | **只删** `load_checkpoint` / `save_checkpoint` / `delete_checkpoint` 三个模块函数 + 类上对应的 3 个方法 | ⚠️ **不要整个文件删**：`AgentStateRepository` 还兼着 `SqlStore` 的 DB 句柄（`persistence/stores.py:209` `db = get_agent_state_repository()`）。文件头注释也要改（现在写着"与共享 HITL checkpoint 的持久化 repository"） |
| 7 | `db/schema.py` | ① 删 `_create_agent_checkpoints_table` 函数与 `:628` 的调用；② `EXPECTED_TABLES`（:75）移除 `agent_checkpoints`；③ `_RETIRED_TABLES`（:456）**加入** `agent_checkpoints`；④ `SCHEMA_VERSION` **16 → 17** | ② 若不删，快速启动路径永远命中不了（每次都跑全量建表）；③ 让已建过表的环境在升级时自动 `DROP TABLE IF EXISTS`（线上本就没有，是 no-op） |
| 8 | `services/sessions/thread.py:31,82` | `isinstance(checkpointer, PersistentMemorySaver)` | 改成 `isinstance(checkpointer, MemorySaver)`（`langgraph.checkpoint.memory.MemorySaver`）语义完全一致：进程内 saver 不能跨会话共享 → 仍退回每消息一个 thread |
| 9 | `tests/db/test_checkpoint_legacy_flush.py`、`test_checkpoint_pruning.py`、`test_checkpoint_roundtrip.py` | 整个文件删（考的全是旧快照） | `tests/services/test_checkpoints_sqlite.py:54` 里那条 `isinstance(PersistentMemorySaver)` 断言要改成"legacy 返回 MemorySaver" |
| 10 | `deploy/k8s/agent-deployment.yaml:1-6,19` | 注释"schema migration creates agent_checkpoints"、`EASY_CHECKPOINT_BACKEND: "mysql"` | 改 `mysql_saver`；注意 `tests/services/test_checkpoint_deployment.py:179` **断言了这份 yaml 的值**，要同步改 |

### 15.3 删除后的收益与代价

| | 内容 |
|---|---|
| 收益 | ① 少 300+ 行"必然被淘汰"的代码；② 少一条 `pickle` 反序列化路径（跨版本脆弱 + 安全问题）；③ 少一个业务表；④ 启动日志不可能再"报错后端"；⑤ `thread.py` 里那个 `isinstance` 分支不再是"隐形开关" |
| 代价 | ① 没有"回滚到旧快照"这个选项（但线上从未跑过它，等于没有）；② **`SCHEMA_VERSION` 要 16→17**——注意这次 +1 不是为了 checkpoint 4 张表（它们走自己的元数据表 `cp_checkpoint_meta`），**纯粹是为了删掉 `agent_checkpoints`**；③ 3 个测试文件消失，checkpoint 相关用例少了约 3 组，需要新的 `mysql_saver` 用例补上 |

### 15.4 不做的事

- 不写"从 pickle 迁移到 3 表"的脚本（无数据可迁）。
- 不在业务库里 `DROP COLUMN` 或改任何业务表。
- 不保留 `mysql_snapshot` 这个名字（v2 的"逃生门"方案作废，见 §16）。

---

## 16. 后端命名：`mysql_saver`（**你问的第 3 个问题**）

### 16.1 定稿

| 值 | 指向 | 状态存哪 | 用途 |
|---|---|---|---|
| `mysql_saver` | **新 `AsyncMySQLSaver`** | 业务库 `cp_*` 4 张表 | **生产默认** |
| `mysql` | **`mysql_saver` 的废弃别名**（启动打 WARN） | 同上 | 兼容还写着 `mysql` 的 ConfigMap |
| `sqlite` | 官方 `AsyncSqliteSaver`（一行未改） | 本 Pod `{pod}.db` | dev/test、单机、**生产回滚** |
| `legacy` | 进程内 `MemorySaver` | 内存 | 调试 |
| `auto` | 业务库是 MySQL → `mysql_saver`；否则 `sqlite` | 跟随后两者 | 默认值 |

**同意你的判断**：`mysql_saver` 与"数据库是 MySQL"解耦得更干净，排查时不会再有"设了 `mysql` 但跑的不是我以为的那个"。

### 16.2 `auto` 的语义（这条要写进代码注释，容易错）

**后端选择只看配置，不看连通性。**

| 场景 | 结果 |
|---|---|
| 业务库 MySQL + `checkpoint.mysql.enabled=true`（默认） | `mysql_saver` |
| 业务库 MySQL，但连不上 | **启动失败**（`CheckpointDeploymentError`），**绝不降级到 sqlite** |
| 业务库 sqlite（单机/dev） | `sqlite` |
| `checkpoint.mysql.enabled=false` | `sqlite`（显式退出共享后端，给 CI/临时环境用） |

**为什么连不上不能降级**：`auto` 一旦按"能不能连上"决定，就会出现 Pod1 连上了（写 MySQL）、Pod2 没连上（写本地 sqlite）——**两套状态并存，比启动失败难查一百倍**。这也是 `app.py:373` 那个 `except Exception` 必须改的原因（§7.2 第 7 项）。

### 16.3 同步要改的地方（漏一个就前后不一致）

1. `checkpoints.py`：`BACKEND_MYSQL_SAVER = "mysql_saver"`、`BACKEND_CHOICES`、`_LEGACY_ALIASES = {"mysql": "mysql_saver"}`、`active_checkpoint_backend()`。
2. `resolve_checkpoint_backend()`：先做别名归一化再判分支；别名命中时 `warnings.append("EASY_CHECKPOINT_BACKEND=mysql 已废弃，请改为 mysql_saver")`。
3. `validate_checkpoint_deployment()`：`mysql_saver` 直接 `return False`。
4. `deploy/k8s/agent-deployment.yaml` + `tests/services/test_checkpoint_deployment.py:179`（断言了 yaml 里的值）。
5. `docs/architecture-current.md`、`README`（`eeedb0f6`/`2f2b7ea6` 那两次文档提交里写的是 MySQL 快照）。
6. 启动日志：`HITL checkpointer: AsyncMySQLSaver（共享 MySQL，cp_checkpoints/cp_checkpoint_blobs/cp_checkpoint_writes/cp_checkpoint_meta）`——**这是运维判断"跑的是哪个实现"的唯一可靠依据**。

### 16.4 `mysql` 别名什么时候删

P3 之后观察一个发布周期：日志里没有别名 WARN → 删掉别名与那条 WARN。**不要现在就删**——ConfigMap 不在这份仓库里维护（`kubectl apply` 的历史版本可能还写着 `mysql`），留一个别名比让 Pod 起不来便宜。

---

## 17. 回滚到 sqlite（**你问的第 4 个问题**）

### 17.1 为什么回滚是安全的（关键机制，很多人不知道）

`plan_turn`（`services/sessions/thread.py:169`）在**thread 为空时会从业务库水合**：

```python
state = await agent.agent.aget_state(build_run_config(agent, session_id))
thread_messages = list((state.values or {}).get("messages") or [])
if not thread_messages:
    return TurnPlan(session_id, rebuilt(history), "hydrate")   # ← 从 session_messages 重建
```

所以换到 sqlite 后：
- 每个会话的 thread 都是空的 → 第一次打开就 hydrate → **历史一条不少**（来自 `session_messages`）；
- 之后的回合在 sqlite 里正常累积；
- 再切回 `mysql_saver` 时，MySQL 里的老 thread 还在，`plan_turn` 会走 `continue`（带 catch-up 补齐"DB 里有、thread 里没有"的轮次）。**两个方向都可逆。**

**这就是为什么"回滚不等于丢数据"**（v2 的 R16 结论是错的，已改）。

### 17.2 回滚的两个前提

| 前提 | 为什么 | 怎么保证 |
|---|---|---|
| **Ingress 的粘性 annotation 不要删** | `validate_checkpoint_deployment()` 对 sqlite 后端要求 `replicas>1` 时 **必须** `sticky=true` 且路径含 `{pod}`，否则**启动直接失败**。如果 P3 时把 annotation 摘了，回滚就不只是"改个环境变量"，还得先改 Ingress | P3 阶段**只解除"mysql_saver 对粘性的依赖"**（`EASY_STICKY_SESSIONS` 不再校验），**先不摘 annotation**。等 `mysql_saver` 稳定两周后再单独做一次"摘粘性"变更（可独立回滚） |
| `EASY_CHECKPOINT_SQLITE_PATH` 里有 `{pod}` | 同上，否则每个 Pod 抢同一个文件（PVC 是 RWX） | 默认值 `DEFAULT_SQLITE_PATH = /qts/checkpoint/{pod}.db` 已经满足；**不要为了"整洁"改掉它** |

> 换句话说：**粘性会话不是"这次要拆的东西"，而是"回滚预案的组成部分"**。把它留着，成本是 0（cookie 亲和，负载略不均），收益是"出事时一条命令回滚"。这跟你的目标"去掉粘性会话、无状态"不冲突——**目标是"不再依赖"，不是"立刻删掉"**。

### 17.3 回滚操作步骤（照着做）

```
1. 改 ConfigMap：EASY_CHECKPOINT_BACKEND=sqlite
   （其余不变；EASY_STICKY_SESSIONS=1 本来就在，sqlite 分支需要它）
2. 滚动重启（replicas=2，滚动期间新旧 Pod 并存：
   新 Pod 读 sqlite、旧 Pod 仍读 MySQL → 这一小段时间跨 Pod 恢复不成立，
   但用户看到的正文来自 session_messages，不会丢内容）
3. 验证（3 条 SQL / 接口）：
   - 启动日志：`HITL checkpointer 后端: sqlite` 且 `_describe_target` 打印 `/qts/checkpoint/<pod>.db`
   - 无 `CheckpointDeploymentError`
   - 打开一个"切换前就已存在"的会话继续提问 → 模型能正确引用历史（证明 hydrate 生效）
4. 记录：本次回滚区间内产生的在途审批全部失效（用户重新发消息即可）
```

### 17.4 回滚会丢什么（写清楚，别让人以为是 bug）

| 丢的东西 | 影响 | 是否可接受 |
|---|---|---|
| 在途审批（`__interrupt__` 写） | 点"继续"→ 409 "审批已失效" | ✅ 有明确提示，不是 500 |
| `context_compactions`（手动压缩记录） | 压缩历史丢失 → 上下文用量回到未压缩状态（可能出现一次"突然变长"） | ⚠️ 可接受，但要提前告知 |
| 时间旅行/回放的历史 checkpoint | `alist` 只能看到 sqlite 里新写的 | ✅ 该功能线上没入口 |
| 定时任务 `sched-` thread | 每轮自删，无影响 | ✅ |

### 17.5 回滚后要不要清 `cp_*` 表

**不要**。理由：① 回滚可能是临时的，清理后就再也切不回来；② `cp_*` 表在业务库里，占空间但 GC 会按"会话已不存在"对账清孤儿；③ 真要清理，走 §5.7 的 `aprine(strategy="delete")` 或 `DELETE ... WHERE thread_id=?`，**不要 `TRUNCATE`**（同库共表，误操作代价太高）。

---

## 18. 开发初期必须解决的坑（v5 自查，27 条）

> 分级标准：**A = 不解决会在开发中期返工/上线即故障**；**B = 上线前必须解决，否则表现为"偶发/难查"**；**C = 观察项，先记录**。

### A 级（P0/P1 阶段就要解决，9 条）

| # | 坑 | 现象 | 根因 | 对策 |
|---|---|---|---|---|
| A1 | 并发互斥只在进程内 | 两 Pod 同时跑同一会话，上下文互相覆盖 | `is_session_streaming` 读 `_sessions` 字典 | §14.4-A/B，P0-c |
| A2 | 失锁后继续写 | 两个 Pod 同时写 `stream_events` 与 checkpoint | 心跳续约失败只 `return` | §14.4-C，失锁自杀 |
| A3 | 租约 TTL 30 分钟 | Pod 崩溃后 30 分钟内"永远生成中" | TTL/heartbeat 比例过松（300s/1800s） | §14.4-C，改成 60s/180s |
| A4 | `autocommit=True` 下三次读不在一个快照 | 偶发"checkpoint 行在、channel 值缺" | 每 SELECT 一个事务 | §5.3，显式只读事务 |
| A5 | `created_at DEFAULT CURRENT_TIMESTAMP` | 跨时区 Pod 数据混时区，排序/TTL 全错 | 服务器时区 ≠ UTC | §4.3，列不设默认 + `init_command` 设 `time_zone='+00:00'` |
| A6 | `agent_checkpoints` 删不干净 | 启动每次都跑全量建表（快速路径永久失效） | `EXPECTED_TABLES` 里还留着它 | §15.2 第 7 项，四处一起改 |
| A7 | `AgentStateRepository` 被整个删掉 | `SqlStore` 起不来（`get_agent_store` 拿不到 db） | 它同时是 store 的 DB 句柄 | §15.2 第 6 项，只删 3 个 checkpoint 函数 |
| A8 | 启动异常静默降级 | 一半 Pod 写 MySQL、一半写内存/sqlite | `app.py` 的 `except Exception` 回退旧快照 | §7.2 第 7 项，`mysql_saver` fail-fast |
| **A9** | **会话线程被判据关掉（v5）** | 一切照跑，但每轮从 DB 重建历史、审批挂在每消息 thread 上——共享 checkpoint 等于没用 | `session_thread_in_use` 用 `sqlite_checkpointer_active()` 作判据（§2.1.1） | §7.2 第 6/6b 项；P0-d 先落，用一个"假共享 saver"桩验证 |

### B 级（上线前必须解决，12 条）

| # | 坑 | 现象 | 对策 |
|---|---|---|---|
| B1 | `blob` 是 MySQL 保留字 | `CREATE TABLE` 报 1064 | 列名用 `payload`，**不要靠反引号**（已在真库踩到并改） |
| B2 | `checkpoint_ns` 长度不可控 | 769 字符时 `VARCHAR` 报 `1406 Data too long`（不是截断） | 原值 `MEDIUMTEXT` + 主键只放 16 字节 hash（已在真库验证 2000 字符可写） |
| B3 | 默认 collation 大小写不敏感 | `ABC` 与 `abc` 共用一条历史 | 4 张表显式 `COLLATE=utf8mb4_bin` + 启动校验 |
| B4 | `task_path` 用 `VARCHAR` | 深嵌套 task 路径超长 | 用 `TEXT`；注意 `TEXT` 不能有字面量默认值，INSERT 必须显式传（空串也要传） |
| B5 | `writes` 的 idx 排序错 | 审批"看起来挂了但读不到"；或同一中断显示上一次的内容 | `WRITES_IDX_MAP` 四类用固定负 idx；全特殊通道 → upsert 覆盖，否则 → 忽略冲突；读时按 `writes_sort_key(task_path, task_id, idx)` 排 |
| B6 | `channel_values` 里 dict 型 channel 丢值 | 手动压缩（`context_compactions`）静默失效，每次都重新摘要 | `aget_tuple` 必须把 blobs 里的值**合并回** `channel_values`；单测要覆盖"dict/list 进出 blobs 后等值"（`compaction.py:53` 直接读这个字段） |
| B7 | `adelete_thread` 大事务 | 删一个长会话卡几十秒，主从延迟飙升 | 三表分批删（每批 500 行 + 提交），先子表后主表；`innodb_lock_wait_timeout` 调到 5s |
| B8 | `innodb_lock_wait_timeout` 默认 50s | 一个卡住的 `aput` 占住连接近一分钟 | 会话级设 5s，对 1213/1205 做 3 次指数退避重试（我们的写都是 upsert，天然幂等） |
| **B9** | **中文注释入库变乱码（v4 新增）** | `SHOW CREATE TABLE` 里注释是 `???`；`information_schema` 查出来也是问号，且**不报错**——属于"改完以为没事"的坑 | ① `aiomysql` 池 `charset='utf8mb4'`；② `scripts/db/mysql-checkpoint-ddl.sql` 首行 `SET NAMES utf8mb4;`（Windows 上 `mysql.exe` 默认 gbk，DBA 手工执行必踩）；③ P1 首日按 §4.3.1 最后一行做一次 `information_schema` 断言 |
| **B10** | **用 `MODIFY COLUMN` 改注释顺手改掉约束（v4 新增）** | 某次"只是改个注释"的迁移，把 `NOT NULL` 漏掉了 → 之后写入 NULL 不报错，读到空值时行为不可预期 | 规则：**上线后不再单独改注释**；必须改就走正式迁移，列定义全量重写 + 单测比对 `SHOW CREATE TABLE`（§4.3.2 约束 3） |
| **B11** | **日志只打 `session_id[-5:]`（v5 新增）** | 跨 Pod 排查时只有 5 位后缀，可能需要 `LIKE '%xxxxx'` 反查，且多个会话可能撞尾（16⁵ 空间） | 新增代码打**全量** `session_id`，需要兼容旧 grep 习惯时再追加 `sid=<末5位>`（§19.1）；不要求改造现有 87 处 |
| **B12** | **压缩放开判据后没做乐观校验（v5 新增）** | 租约只保证"同一时刻一个写者"，不保证"我基于最新状态压缩"；A 拿到租约、B 刚追加过内容时，压缩会把新内容压掉 | 压缩提交前重读最新 `checkpoint_id`，与开始时读到的不一致就 409（§2.1.3 第 ② 项） |

### C 级（观察项，6 条）

| # | 项 | 说明 |
|---|---|---|
| C1 | 孤儿 blob | 并发分叉/`prune` 后会有无人引用的 blob，**不要按引用计数 GC**（§11 已更正） |
| C2 | `sessions.updated_at` 是 `VARCHAR(50)` | TTL 默认按它判过期，只能做字符串比较；启用 TTL 前必须用真实行探针验证格式一致（R22） |
| C3 | `stream_until` 也是 `VARCHAR(50)` | 排查时别用 `NOW()` 直接比，要传 ISO 串 |
| C4 | `max_allowed_packet` | 默认 64MB；工具返回大文件时单条 blob 可能顶到上限。超限要抛**带 channel 名和字节数**的错误，不要把 `Packet too large` 直接甩给上层 |
| C5 | 连接数 | 每 Pod 两套池（`DBUtils` + aiomysql）。现网 `replicas: 2`，要按"扩到 10 副本"留余量；建议 `pool_max_size` 默认 4~6 而不是 10 |
| C6 | `EASY_MULTI_REPLICA_STRICT` | 严格模式里写死"checkpointer 不能跨副本共享"，改完后可以让它通过（§7.2 第 10 项）；`run_queue`/`file_backend`/`sandbox` 仍未外置，严格模式整体还是开不了，**本期不碰** |

---

## 19. 可观测性设计（**你要求的第 1 条**）

> 这一章不是"顺手加日志"，而是**把"能不能在 5 分钟内定位一次跨 Pod 故障"做成设计约束**。原因很实际：这套改造把状态从"某台 Pod 的本地文件"搬到了"共享库"，出问题时你手上只有日志和几张表，**日志里没有 session_id 就等于没有线索**。

### 19.1 先看现有日志机制的三条硬约束（决定了日志怎么写）

| 事实 | 位置 | 影响 |
|---|---|---|
| 日志格式是 `%(asctime)s - %(name)s - %(levelname)s - %(message)s`，**没有会话上下文变量** | `bootstrap/initialization/log.py:30` | 无法靠 formatter 自动带 `session_id`；**必须在 message 里手写** |
| 现存 87 处用 `session_id[-5:]` 作短前缀（`SidMixin.sid`，`agent/core.py:400`、`chat_live.py:75` 等） | 全仓 | 末 5 位来自 UUID 随机段，**只能当"后缀 grep 线索"，不能唯一确定会话**，也不能直接与 DB 关联 |
| `SessionLogger` 写的是 JSONL 会话转录，**不是应用日志** | `utils/session_logger.py` | 排查"后端为什么没恢复"要看应用日志，不要去看 JSONL |

**于是新代码的日志规范（三条，写进 PR 检查项）**：

1. **一律打全量 `session_id`**（36 字符）。需要兼容现有短码 grep 时，additionally 追加 `sid=<末5位>`。
2. **固定 kv 顺序**，便于 `grep` / `awk` 切字段：
   ```
   op=<操作> session=<session_id> thread=<thread_id> ns=<ns_hash前8位> cp=<checkpoint_id前12位> step=<n> rows=<n> bytes=<n> elapsed_ms=<n>
   ```
3. 新代码用 `logger.info("... %s ...", value)` 的惰性格式化，不写 f-string（全仓已有大量 f-string，不在本次统一改造范围内，但新增的 600 行要按规范写）。

### 19.2 关键节点日志清单（必须打的）

| # | 位置 | 级别 | 必须包含 | 为什么必须 |
|---|---|---|---|---|
| 1 | `asetup` 进入 / 退出 | INFO | `op=setup backend=mysql_saver version=<v> tables=<4> elapsed_ms=` | 判断"这个 Pod 到底跑到哪一步了" |
| 2 | 建表/迁移每条 | INFO | `op=migrate version=<v> ddl=<前60字符> elapsed_ms=` | 上线后追"谁在什么时候建的表" |
| 3 | `ns_hash_algo` 校验通过/失败 | INFO / **ERROR** | `op=ns_hash_check expect=sha256-16 actual=<库里的值>` | 失败即启动失败，必须一眼看出原因 |
| 4 | 连接池建立 | INFO | `op=pool session=?` 不需要；只打 `host/port/db/pool_size`（**不含密码**） | 连错库是最高频的低级故障 |
| 5 | `aput` | INFO（回合级）/ DEBUG（步级） | `op=aput session= thread= ns= cp= step= blobs=<新增数> bytes=<总字节> elapsed_ms=` | 写放大、慢写、大 payload 都从这里看 |
| 6 | `aput_writes` | INFO | `op=aput_writes session= thread= cp= task= idx= channels=<名> bytes= elapsed_ms=` | HITL 的关键路径，中断写不进去就永远等不到审批 |
| 7 | `aget_tuple` | DEBUG（命中）/ WARN（未命中但调用方期望有） | `op=aget_tuple session= thread= ns= cp=? rows=<blobs数> elapsed_ms=` | "恢复不了"的第一现场 |
| 8 | `alist` | DEBUG | `op=alist session= thread= limit= returned= elapsed_ms=` | 历史页慢的时候看 |
| 9 | `adelete_thread` | INFO | `op=delete_thread session= thread= batches=<批数> checkpoints=<行数> blobs=<行数> writes=<行数> elapsed_ms=` | 删会话卡库的唯一证据 |
| 10 | GC 每轮 | INFO | `op=gc round=<n> scanned=<n> deleted_threads=<n> ttl_enabled=<bool> elapsed_ms=` | 确认 GC 真的在跑（而不是被锁挡住） |
| 11 | TTL 每轮（启用后） | INFO | `op=ttl candidates=<n> deleted=<n> skipped_active=<n> skipped_approval=<n>` | 将来开启 TTL 时唯一的审计来源 |
| 12 | `plan_turn` 结果 | INFO（**已有**，保留并加全量 session） | `thread: hydrate/rehydrate/continue` | 判断"这条会话是不是第一次走新后端" |
| 13 | 压缩开始/结束 | INFO（**已有**） | `op=compact session= request= owner= result=` | 跨 Pod 压缩互斥是否生效 |
| 14 | 跨 Pod 恢复审批 | INFO | `op=resume_approval session= thread= handle= instance=<本Pod> hit=<true/false>` | **本次改造的核心能力，必须能看到"在哪台成功恢复"** |
| 15 | 流锁获取失败（409） | WARN（**已有**，需补 session 全量） | `op=stream_busy session= owner=<对方Pod> instance=<本Pod>` | 区分"真并发"和"崩溃残留锁" |
| 16 | 失锁自杀 | **ERROR** | `op=lost_lease session= owner=<新owner> elapsed_s=<持有时长>` | 双写风险发生的唯一信号，必须告警 |

### 19.3 异常路径：必须记、必须带 errno、不许吞

**三类必须用 `logger.exception`（带堆栈）**：

| 场景 | 额外要求 |
|---|---|
| 连接 / 建表 / 迁移失败 | 打出 `host`、`database`、`table_prefix`、当前 `schema_version`；**不要打密码** |
| 任一 SQL 执行失败 | 打出 `op`、表名、`errno`、`sqlstate`，以及**可执行的处理建议**（见下表） |
| 序列化 / 反序列化失败 | 打出 `channel`、`type`、字节数、`checkpoint_id`；**绝不打 payload 内容** |

**errno → 提示映射（写进代码常量，不要临时拼字符串）**：

| errno | 含义 | 日志里应给出的建议 |
|---|---|---|
| 1146 | 表不存在 | `auto_setup=false` 时没预建表 → 开 `auto_setup` 或跑 SQL 脚本 |
| 1406 | 数据过长 | 哪个列、实际字节数；`checkpoint_ns` 应为 `MEDIUMTEXT`，说明表被旧 DDL 建过 |
| 1366 | 字符集不合法 | 连接 charset 不是 `utf8mb4` |
| 1213 | 死锁 | 已自动重试 N 次仍失败；给出涉及的 `thread_id` |
| 1205 | 锁等待超时 | `innodb_lock_wait_timeout` 当前值 + 长事务线索（多半是 `adelete_thread` 没分批） |
| 1062 | 主键冲突 | 哪个唯一键；`aput_writes` 的 upsert 分支是否走错 |
| 1153 | `max_allowed_packet` 超限 | 实际字节数 + 当前配置值 + 建议调到 128MB |
| 2006/2013 | 连接断开/丢失 | 池大小、是否触发了 MySQL 侧 `wait_timeout` |

**两条"不许"**：

1. **不许把审批参数、工具返回内容打进日志**——HITL 的 `payload` 里可能有业务数据、SQL、文件内容。只记 `channel` 名、字节数、`tool 名`。
2. **不许在 `except Exception` 里只写一句 `logger.warning("失败")` 然后继续跑**。现有 `services/agents/manager.py:964` 的 `_acquire_stream_lock` 就是"非 SessionBusyError 一律 warning 后继续"——这在共享后端下意味着"锁没拿到也照样跑"，需要在本轮里改成"记 ERROR + 抛出"（或者明确写注释说明为什么可以继续）。

### 19.4 一次跨 Pod 故障的日志链（验收时照着 grep）

给定一个 `session_id=1f2e...`，应该能按时间拼出这条线：

```
T+0.0s  [pod-a] op=plan_turn session=1f2e... thread: hydrate | 输入消息: 12        ← A 首次水合
T+0.3s  [pod-a] op=stream_busy? 否；op=aput session=1f2e... cp=019a... step=1
T+2.1s  [pod-a] op=aput_writes session=1f2e... cp=019a... task=execute_script channels=__interrupt__
T+2.2s  [pod-a] 前端收到审批句柄 1f2e...-msg-7f3c
T+30s   [pod-b] op=resume_approval session=1f2e... handle=1f2e...-msg-7f3c instance=pod-b hit=true   ← B 恢复成功
T+30.1s [pod-b] op=aget_tuple session=1f2e... cp=019a... rows=3 elapsed_ms=11
```

> 如果 `hit=false`，下一条日志必须是"为什么 hit=false"（`aget_tuple` 未命中 / `ns` 不匹配 / thread 为空）。**不允许出现"恢复失败但日志里什么都没有"**。

### 19.5 噪声控制：每步一个 `aput` 会把日志刷爆

`session_thread` 开着时一个回合 10~30 步 = 10~30 次 `aput`。因此：

- **单步 `aput` 用 DEBUG**，只在"回合结束"（`on_chain_end` 或流结束时）打一条 INFO 汇总：`op=turn_summary session= steps=<n> checkpoints=<n> bytes=<总字节> elapsed_ms=`；
- 出问题时把 `LOG_LEVEL=DEBUG` 临时打开，或用 `EASY_CHECKPOINT_DEBUG=1` 只放开 `easy_agent.persistence.mysql_saver` 这个 logger（推荐，避免整库 DEBUG）；
- **ERROR/WARN 一条都不压缩**。

### 19.6 指标：不引入新组件，靠日志行统计

不为了这个改造引入 Prometheus。GC / TTL / aput 的 INFO 行已经是结构化的 kv，日志平台按 `op=` 聚合就能得到：`aput` P99 耗时、`aget_tuple` 命中率、GC 删除量、409 比例、失锁次数。**要求是每行都能被 `awk -F'op=' '{print $2}' | cut -d' ' -f1` 切开**。

---

## 20. 发版手册（**你要求的第 2 条**）

> 适用对象：执行发布的人（不一定是写代码的人）。原则是**每一步都能照做、每个判据都能验证**。

### 20.1 发版前（T-3 天 ~ T-0）

| # | 检查项 | 怎么查 | 不通过怎么办 |
|---|---|---|---|
| 1 | 业务库账号有 DDL 权限 | `SHOW GRANTS FOR CURRENT_USER()` 里含 `CREATE`、`ALTER`、`INDEX` | 让 DBA 预建表并把 `auto_setup=false` |
| 2 | 有 `GET_LOCK` 权限（内置函数，一般都有） | `SELECT GET_LOCK('probe',1), RELEASE_LOCK('probe')` | 无则 `auto_setup` 的串行化失效，改用 DBA 预建表 |
| 3 | `max_allowed_packet ≥ 16MB` | `SHOW VARIABLES LIKE 'max_allowed_packet'` | 调到 128MB（需重启实例，提前排期） |
| 4 | 表不存在或已预建 | `SHOW TABLES LIKE 'cp\\_%'` | 走 SQL 脚本预建（**脚本首行 `SET NAMES utf8mb4;`**） |
| 5 | 配置就位 | ConfigMap/`.env` 里 `EASY_CHECKPOINT_BACKEND=mysql_saver`（或 `auto`） | 漏配也能启动（默认 auto → 业务库是 MySQL 就走 `mysql_saver`），但要确认日志打的是 `mysql_saver` |
| 6 | 旧值处理 | 若原来写的是 `mysql`，会映射成 `mysql_saver` 并打 WARN | 知会运维："这个 WARN 是预期内的，下个版本可删" |
| 7 | **Ingress 粘性 annotation 保留** | `kubectl get ingress -o yaml` 里 `sessionAffinity` 还在 | 先别摘（§17.2），回滚要靠它 |
| 8 | `WEB_CONCURRENCY` / `UVICORN_WORKERS` | 确认为 1 | 保持 1（共享 saver 支持多 worker，但进程内图缓存会翻倍占内存） |
| 9 | **在途审批盘点** | 生产是 sqlite 每 Pod 一个文件：只能逐 Pod 查 `session_messages` 里 `pending_approval` 非空的条数（或看 UI 上的审批徽标数量） | >0 就推迟窗口（R1：切换后在途审批会失效） |
| 10 | 无活跃流 | `SELECT COUNT(*) FROM session_runtime WHERE stream_until > :now_iso`（注意是 ISO 字符串，**不能用 `NOW()`**） | >0 等它跑完或让它超时 |
| 11 | 备份 | `mysqldump` 业务库（含 `cp_*` 若有） | 无备份不发 |
| 12 | 回滚预案演练过 | 见 §20.5，确认回滚只需改一个环境变量 + 保留粘性 | 演练一次再发 |

### 20.2 发版时的手动操作（按此顺序）

| 步 | 动作 | 完成判据 |
|---|---|---|
| 1 | 发公告 / 暂停入口流量（可选，2 分钟窗口） | — |
| 2 | 再次确认第 9、10 项（在途审批 = 0、无活跃流） | 两个计数都是 0 |
| 3 | **全量发布**（不要按 Pod 灰度：灰度期跨 Pod 恢复不成立，§10） | 所有 Pod 同一镜像 |
| 4 | **逐 Pod 检查启动日志 4 行**：`op=setup ... version=1`、`ns_hash_check expect=... actual=...`（一致）、`HITL checkpointer: AsyncMySQLSaver（共享 MySQL...）`、无 `Table doesn't exist` | 每个 Pod 都有这 4 行且都对 |
| 5 | 建表核对：`SHOW TABLES LIKE 'cp\\_%'` 得 4 张；`SELECT * FROM cp_checkpoint_meta` 有 `schema_version` 与 `ns_hash_algo` | 4 张表 + 2 个键 |
| 6 | 冒烟（**在 Pod A 上做**）：新建会话发一条普通消息 | 日志出现 `thread: hydrate`；`cp_checkpoints` 多出该 `thread_id` 的行 |
| 7 | 冒烟（**换到 Pod B**）：同一会话再发一条 | 日志出现 `thread: continue`（**不是 hydrate**）；`cp_checkpoints` 的 `parent_checkpoint_id` 接上一条 |
| 8 | 恢复流量，进入 §20.3 观察 | — |

> 第 6、7 步是**整个发布唯一能自证"共享真的生效了"的动作**。`hydrate → continue` 这个变化如果没出现（两边都是 hydrate），说明两个 Pod 没看到同一张表——立刻停流量排查，不要往下走。

### 20.3 发版后监控（前 24 小时）

**日志关键字告警表**（把这张表配到日志平台的告警规则里）：

| 关键字 | 含义 | 阈值 / 处置 |
|---|---|---|
| `ns_hash_check` 且 `expect != actual` | 算法标识不一致 | **任何一条都直接回滚**（服务会启动失败，但可能只有部分 Pod） |
| `Table doesn't exist` / errno `1146` | 表没建 | 任何一条 → 查该 Pod 的 `auto_setup` 与 DDL 权限 |
| `SessionBusyError` / `op=stream_busy` | 并发流被拒 | 正常应 < 5 次/小时；突增说明用户连点或锁泄漏，看 `owner=` 是不是某个已下线的 Pod |
| `op=lost_lease` | 失锁自杀（双写风险） | **任何一条都要立刻查**：两个 Pod 是否都在跑同一会话 |
| `op=resume_approval ... hit=false` | 恢复审批失败 | > 5 条/小时 → 停流量排查（预期为 0） |
| errno `1213` / `1205` | 死锁 / 锁等待超时 | > 10 条/小时 → 看是否有长事务（多半是 `adelete_thread` 未分批） |
| errno `1153` / `Packet too large` | 单条过大 | 记录 channel 与字节数；工具返回大文件触发，考虑压缩或分片 |
| errno `2006/2013` | 连接断开 | 看池大小与 `wait_timeout` |
| `op=gc` 缺失超过 `2 × gc_interval` | GC 卡住 | 查分布式锁是否被某个已死 Pod 持有 |

**数据侧监控（每天一次）**：

```sql
-- 表增长（用于 §5.7.1 的观察期：这就是"再看是否开启 TTL"的依据）
SELECT TABLE_NAME,
       TABLE_ROWS,
       ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS mb
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'cp\_%';

-- 孤儿检查（要能解释，不要求为 0）
SELECT COUNT(*) FROM cp_checkpoints c
LEFT JOIN cp_checkpoints p
       ON p.thread_id = c.thread_id
      AND p.checkpoint_ns_hash = c.checkpoint_ns_hash
      AND p.checkpoint_id = c.parent_checkpoint_id
WHERE c.parent_checkpoint_id IS NOT NULL AND p.checkpoint_id IS NULL;
```

**首日必盯的 4 个场景**（不是"看看有没有报错"，是主动去验）：

1. **跨 Pod 审批**：找一个用审批类工具的会话，A 触发、切到 B 点继续 → 必须成功，日志 `hit=true`；
2. **hydrate 比例**：新后端上线当天会有大量 `thread: hydrate`（老会话第一次），**第二天应该基本消失**；如果持续高，说明水合结果没落库或被反复删（`rehydrate` 会带 `delete_session_thread` 日志）；
3. **手动压缩**：点一次压缩，确认 200 且 `cp_checkpoints` 里 `context_compactions` 有记录；
4. **删会话**：删一个测试会话，确认三张表里该 `thread_id` 的行清零（`op=delete_thread` 的 `batches` 合理，不是一次几万行）。

### 20.4 回滚（30 分钟内可完成）

| 步 | 动作 |
|---|---|
| 1 | 把 `EASY_CHECKPOINT_BACKEND` 改成 `sqlite`（**保留** `sqlite_path` 里的 `{pod}` 与 Ingress 粘性） |
| 2 | 确认 `EASY_STICKY_SESSIONS=1`（若之前删了要加回） |
| 3 | 滚动重启 |
| 4 | 验证：老会话发一条消息 → 日志 `thread: hydrate`（一定会有，因为 sqlite 文件里是空的）；历史完整（来自 `session_messages`） |
| 5 | `cp_*` 表**不删不动**（留作下次切换的对照与排查依据） |

**回滚会丢什么**：只有"回滚时刻的在途审批"和"回滚期间新写入的 checkpoint"（都在 MySQL 里，sqlite 读不到）。历史聊天记录一条不丢（§17.1）。

### 20.5 发布说明要写的两句话（对内）

> ① 本次升级后 `EASY_CHECKPOINT_BACKEND=mysql` 指向新的协议级实现（`mysql_saver`），如需保持旧快照请改为 `mysql_snapshot`（旧实现已删除，实际不可用）；
> ② 技能 / 连接器 / 专家的**配置缓存仍按进程隔离**（§22），改完配置在另一台 Pod 上可能需要等缓存过期才生效——这与会话状态无关。

---

## 21. 场景测试方案（**你要的"这个场景怎么测"**）

> 目标：证明"**请求落到哪一台都一样**"。不是"跑通了"，而是"能在任意 Pod 上接着跑、能证明它在接着跑"。

### 21.1 怎么造出"两台"（三种方案，按可信度排序）

| 方案 | 做法 | 适合 | 坑 |
|---|---|---|---|
| **A. 两个本地进程**（最快，日常开发用） | 两个 terminal，同一 MySQL，不同端口，**必须给不同的 `EASY_POD_NAME`**：<br>`EASY_POD_NAME=pod-a python main.py --port 8080`<br>`EASY_POD_NAME=pod-b python main.py --port 8081` | P1 阶段的 90% 用例 | ① 不设 `EASY_POD_NAME` 时两个进程 `instance_name()` 相同 → sqlite 路径相同（回滚用例会串）；② `instance_owner()` 是 `hostname:pid`，天然不同，所以流锁能正常互斥 ✅ |
| **B. docker compose 两实例 + nginx 轮询** | 同一网络里两个 app 容器（`EASY_POD_NAME=pod-a/pod-b`），nginx `proxy_next_upstream` 或 ip_hash 关闭 | 验证"同一前端请求被分到不同后端" | 需要让 nginx 每次请求换上游（`least_conn` + 关 keepalive 到上游），否则同一连接永远命中一台 |
| **C. k8s `replicas=2` + 临时摘掉粘性 annotation** | 真实拓扑 | **最终验收**（P3） | 摘粘性前先把 §14.4 的 P0-c 上了，否则会踩幻影 409 / 空流 |

> 方案 A 的关键细节：两个进程连**同一个库**、用**同一个 `MYSQL_DATABASE`**；`WORKSPACE_DIR` 建议也分开（`./workspace-a` / `./workspace-b`），否则同一用户的 workspace 文件会被两个进程同时写。

### 21.2 断言用哪四种证据（每个用例至少取两种）

| 证据 | 怎么取 | 证明什么 |
|---|---|---|
| **HTTP 响应 / SSE 帧** | `curl -N` 抓流，看 `type=content` 与最终 `done` | 用户看到的东西对不对 |
| **应用日志**（§19 的 kv） | `grep "session=<id>" app.log` | 请求走了哪条路径（`hydrate`/`continue`/`hit=true`） |
| **DB 行** | `cp_checkpoints` / `cp_checkpoint_blobs` / `cp_checkpoint_writes` / `session_messages` | 状态真的落到共享库、没重复、没孤儿 |
| **前端表现** | 浏览器 | 只有"用户可见"的问题才需要它；不要用前端当唯一证据 |

**通用查询（把 `<SID>` 换成真实 session_id）**：

```sql
-- 1. 这个会话在 MySQL 里有几个 thread？（会话线程开启时应为 1~2 个：会话 thread + 可能的每消息 thread 历史残留）
SELECT thread_id, COUNT(*) cp_cnt, MIN(created_at), MAX(created_at)
FROM cp_checkpoints WHERE session_id = '<SID>' GROUP BY thread_id;

-- 2. 是不是一条链（父子是否接得上）
SELECT checkpoint_id, parent_checkpoint_id, step, created_at
FROM cp_checkpoints WHERE session_id = '<SID>' ORDER BY created_at;

-- 3. 最后一条 assistant 消息在 thread 里存不存在（判断"半截回复"）
SELECT checkpoint_id, JSON_EXTRACT(metadata, '$.source') AS src
FROM cp_checkpoints WHERE thread_id = '<SID>' ORDER BY created_at DESC LIMIT 3;
```

### 21.3 用例矩阵（T1~T14）

| # | 用例 | 前置 | 步骤（A=pod-a，B=pod-b） | 断言（必须全中） | 失败长什么样 |
|---|---|---|---|---|---|
| **T1** | **会话线程没被关掉**（最基础，先测这个） | 新后端已起 | A 建会话发一条消息 | ① 日志 `thread: hydrate`；② `cp_checkpoints` 出现 `thread_id = session_id`；③ **不存在** `thread_id = "<session_id>-<msg_id>"` 的行 | 出现每消息 thread / `thread: per-message` → `session_thread_in_use` 没改（§2.1.1） |
| **T2** | **跨 Pod 审批恢复**（核心） | T1 通过 | A：发一条会触发审批（如执行脚本）的消息 → 记下审批句柄；B：直接点"继续/拒绝" | ① B 的日志 `op=resume_approval ... hit=true`；② B 返回 200 且流里有后续内容；③ `cp_checkpoint_writes` 里该 `__interrupt__` 行之后出现恢复记录；④ A 上没有"实例不匹配"报错 | B 返回"找不到审批状态" → `per_pod_checkpoints_active()` 仍为 True，或 `ensure_approval_on_this_instance` 没被短路（§7.2 明确不用改那一处） |
| **T3** | **跨 Pod 连续对话** | T1 | A 发第 1 条 → B 发第 2 条 → A 发第 3 条 | ① 日志依次 `hydrate` → `continue` → `continue`；② `cp_checkpoints` 只有 1 个 `thread_id`，`parent_checkpoint_id` 首尾相接成一条链；③ 第 3 条回答里能引用第 1、2 条的内容 | 第二次是 `hydrate`（B 看不到 A 写的）→ 两个进程连的不是同一个库/同一个 `table_prefix` |
| **T4** | **刷新续接（流没结束就刷新）** | — | A 发起长回答 → 中途刷新页面（或换端口访问同一会话） | ① `/stream/status` 返回 active；② `/stream/live` 从 `event_id` 续上，内容连续无重复；③ 最终 `session_messages` 里只有一条 assistant 消息 | 一直"生成中"（`stream_until` 悬空，§14.4-C）或正文重复两段 |
| **T5** | **并发双发（去掉粘性后最危险）** | T1 | 同一个会话，A 和 B **同时**发两条（间隔 <200ms） | ① 一条 200、一条 **409**（不是 200 空流）；② `session_messages` 里**没有**"只有用户消息、没有回复"的孤儿；③ 日志 `op=stream_busy owner=` 指向对方 | 两条都 200 但一条无内容（现在 `queue.put_nowait(None)` 的行为）→ P0-c 没修 |
| **T6** | **老会话首问水合** | 造一条"sqlite 时代的会话"（或用 `session_messages` 直接插历史） | 新后端下打开该会话，发一条新消息 | ① 日志 `thread: hydrate` 且 `输入消息数 = 历史条数 + 1`；② 模型回答能引用历史内容；③ 再发一条是 `continue` 且**不重复**历史 | 模型"失忆"（历史没进 thread）或历史重复两遍（水合了两次） |
| **T7** | **删除会话** | T1 | 删一个有若干轮对话的会话 | ① 三张表 `WHERE thread_id = <SID>` 全为 0；② 日志 `op=delete_thread batches>=1`；③ 另一端再访问该会话 → 新建，不报错 | 表里还有残留（`adelete_thread` 只删了主表） |
| **T8** | **手动压缩（跨 Pod 互斥）** | T1 + 有可压缩的长会话 | ① A 点压缩；② 立刻在 B 也点压缩 | ① 一个 200、一个 409（"正在压缩"）；② 成功的那个 `cp_checkpoints` 里出现 `context_compactions` 记录；③ B 再点一次能读到已提交结果（`records()` 不再 409） | 两个都 200（互斥失效）或两个都 409（判据没放开） |
| **T9** | **Pod 崩溃后接管** | T1 | A 开始长回答 → `kill -9` A 的进程 | ① B 上 `/stream/status` 在 ≤ 新 TTL 后变为 inactive；② 用户重发能正常进行；③ 日志无 `lost_lease` 以外的异常 | 一直"生成中"（TTL 仍是 30 分钟，§14.4-C） |
| **T10** | **回滚到 sqlite** | T1 | 改 `EASY_CHECKPOINT_BACKEND=sqlite`（保留粘性）重启 | ① 启动成功，日志打 `AsyncSqliteSaver`；② 老会话发消息 `hydrate` 成功、历史完整 | 启动即失败（`{pod}` 或粘性校验没保留，§17.2） |
| **T11** | **大 payload** | — | 让工具返回 > 16MB 内容（可用大文件） | ① 报明确错误（含 channel 与字节数）；② 不静默丢弃；③ 若调大 `max_allowed_packet` 后能成功 | `Packet too large` 直接抛给前端 / 静默不落库 |
| **T12** | **长会话性能** | 造 120 轮 | 打开该会话 | `aget_tuple` 的 `elapsed_ms` < 100（跨网络）；`alist` 首页 < 200ms | 秒级白屏（delta 没做，§5.5） |
| **T13** | **压缩后跨 Pod 可见** | T8 | A 压缩成功 → B 打开该会话继续聊 | B 的上下文是压缩后的（tokens 明显下降），且历史没丢 | B 又压一遍（摘要重复） |
| **T14** | **缓存差异（已知限制，不修）** | — | A 上给用户加一个技能 → B 上立刻发消息 | B **看不到**新技能（符合 §22 的说明）；等 B 的缓存过期后再看得到 | B 立刻看到 → 说明 B 根本没缓存（与文档不符，要更新文档） |

**优先级**：T1 → T3 → T2 → T5 → T6 是**必过项**（这五个不过，方案不成立）；T4/T8/T9 是**上线前必过**；T10 在回滚演练时做；其余是回归项。

### 21.4 三个最容易测错的用例，展开说

**T2 跨 Pod 审批**——最容易"假通过"：因为前端可能把审批请求发回了原 Pod（粘性还在）。判定必须看日志里的 `instance=`：**发起审批的 Pod 与恢复审批的 Pod 必须是不同的名字**。如果两边都是 `pod-a`，这个用例等于没测。

**T5 并发双发**——必须用"两条独立连接同时打"，不能串行 `curl`（串行时第二条会等第一条结束，永远测不出 409）。做法：

```bash
# 同一个 session_id，两个端口同时发
( curl -s -N -X POST http://127.0.0.1:8080/agent/chat/stream -H 'Content-Type: application/json' \
        -d '{"session_id":"'$SID'","message":"第一条"}' > /tmp/a.txt ) &
( curl -s -N -X POST http://127.0.0.1:8081/agent/chat/stream -H 'Content-Type: application/json' \
        -d '{"session_id":"'$SID'","message":"第二条"}' > /tmp/b.txt ) &
wait
grep -c '"type": *"error"' /tmp/a.txt /tmp/b.txt   # 期望恰好一个文件里有明确 error 帧
```

**T6 老会话首问**——"历史完整"不能靠肉眼看回答，要数消息：日志里 `输入消息: N` 必须等于 `session_messages` 里该会话的条数（含本轮）。**最容易漏的是"水合两次"**：写完 thread 后如果 `record_turn_checkpoint` 或后续逻辑又触发一次 `rehydrate`，thread 里会有两份历史，模型会自己跟自己重复对话——用用例 2 的父子链查询能立刻看出来（同一个 `parent_checkpoint_id` 出现两次就意味着分叉）。

### 21.5 自动化分层（别把所有东西都塞进 pytest）

| 层 | 覆盖 | 做法 | 备注 |
|---|---|---|---|
| 单测 | `AsyncMySQLSaver` 的 8 个方法、`shared_checkpointer_active()`、`session_thread_in_use()` | fake 连接 / monkeypatch，**不需要真库** | 这是 P1 的主要工作量，要求覆盖率 ≥ 80% |
| 集成 | 真库上的 DDL、往返、upsert 冲突、`adelete_thread` 分批、事务快照 | `pytest -m mysql`（`EASY_TEST_MYSQL_DSN` 存在才跑，CI 默认 skip） | 每次跑用独立库 `cp_test_<uuid>`，跑完 DROP |
| 端到端 | T1~T14 | **脚本**（不是 pytest）：起两个 uvicorn 子进程 → 按用例表打 HTTP → 断言日志与 DB | 放在 `scripts/dev/e2e_two_pods.py`，**只用于联调与发版前回归**，不进 CI |

端到端脚本的最小骨架（伪代码，实施时照此写）：

```python
# 1) 起两个进程：不同 port、不同 EASY_POD_NAME、同一个 MYSQL_DATABASE
procs = [spawn(port=8080, pod="pod-a", log="a.log"), spawn(port=8081, pod="pod-b", log="b.log")]
# 2) 等两个进程都打出 "HITL checkpointer: AsyncMySQLSaver"
wait_for(logs, pattern="AsyncMySQLSaver", count=2)
# 3) 建会话 → 在 A 发消息 → 断言 A 日志有 "thread: hydrate"
# 4) 同一 session 在 B 发消息 → 断言 B 日志有 "thread: continue"（不是 hydrate）
# 5) 触发审批 → 在 B 恢复 → 断言 "hit=true" 且 instance 是 pod-b
# 6) 断言 DB：单 thread、父子链连续、无孤儿用户消息
# 7) 并发双发 → 断言一个 409 + 库里无孤儿
```

### 21.6 判定"通过"的硬标准（写进验收单）

1. T1/T2/T3/T5/T6 全过，且**证据里同时有日志 + DB 行**；
2. 跨 Pod 恢复的日志里 `instance=` 确实是两个不同名字；
3. `cp_checkpoints` 里同一会话只有 1 个 `thread_id`（老历史残留的每消息 thread 允许存在，但新写入不能再产生）；
4. 全天 `op=resume_approval hit=false` 为 0；
5. 回滚演练一次成功（T10）。

---

## 22. 本次不做：跨进程的配置缓存失效

你提到的第三件事（技能、连接器、专家缓存各在自己的进程里）**不在本方案范围**，但必须写清边界，否则上线后会被当成 bug 报到这个改造头上：

| 现象 | 是不是缺陷 | 说明 |
|---|---|---|
| A 台装了技能，B 台列表里暂时没有 | 已知限制 | `services/skills/library.py:233` 的 `_ref_index_cache` / `_user_ref_index_cache` 按"目录 mtime"缓存；B 台要等自己的下一次扫描（或缓存过期） |
| A 台改了专家提示词，B 台对话还是旧版本 | 已知限制 | 专家在每次建 Agent 时按 `active` 读盘，但 **Agent 对象本身有缓存**（`agent/graph_cache.py`） |
| A 台启用连接器，B 台对话看不到工具 | 已知限制 | 连接器开关记在会话级状态里（`services/agents/manager.py` 的 `_sessions`），跨 Pod 不共享 |

**要解决它需要另做一层**（三种做法，择一，都不在本次）：
1. 配置版本号 + 每请求比对（成本最低，一次 SELECT）；
2. 库表 + 轮询（`config_revision` 表，30s 一次）；
3. pub/sub（Redis 4.0 可用 pub/sub，但本仓无 Redis 依赖，不建议为这件事引入）。

**本次只做一件事**：在发布说明里写一句"配置变更可能需要等另一台 Pod 的缓存过期"（§20.5 第 ② 条）。

---

## 23. 结论与下一步

1. **方案骨架不变**：自研 `AsyncMySQLSaver`、3 张业务表 + 内联标量、`aiomysql`、同库 `cp_` 前缀、`auto_setup` 自动建表。
2. **v5 最重要的发现**：会话线程被关掉不是"MySQL 不支持"，而是**判据把"官方 SQLite 文件已打开"当成了"具备官方协议语义"**（§2.1）。`session_thread_in_use` 这一行不改，后面全部白做。同一个错误口径在 `_shared_graph_checkpointer` 与 `compaction.supported()/records()` 各有一份，三处必须一起改。
3. **对上一版的一处更正**：手动压缩**不需要新增分布式锁**——它已经在 `try_acquire_session_stream` 的跨进程租约里（§2.1.3）。要做的只是放开两个判据、加一次乐观校验（重读最新 `checkpoint_id`）。
4. **v4 的 4 处收敛仍然有效**：`record_ttl_days=0`、元数据表改 KV（`ns_hash` 哨兵）、表/列注释补齐、表数锁定 4（§4.6）。
5. **新增两份可直接执行的手册**：可观测性规范（§19，含 errno 映射与 16 个必打日志点）、发版手册（§20，含 12 项前置检查、8 步发版操作、9 条日志告警阈值、回滚步骤）。
6. **测试方案**（§21）：14 个用例 + 三种"造两台"的方法 + 四种断言证据；硬标准是 T1/T2/T3/T5/T6 必须过，且日志里 `instance=` 确实是两个不同的 Pod。

**下一步（等你点头后执行）**：P0-a → P0-b → P0-c → P1 → P2 → P3（§10）。其中 **P0-c 与 §2.1 的三处判据**建议合为一个 PR 先落（都不依赖 MySQL，可以先用 `sqlite` 后端跑回归），这样在真库就绪前就能证明"会话线程不会因为换后端而关掉"。

