# 手动压缩与 Token 统计审查意见复核及整改方案

日期：2026-11-12。复核基线：当前工作区 `0aabc8c3`；审查截图关联提交为 `f5e4dba1`（手动压缩）、`01ee257a`（Token 统计）。以下判断针对当前代码，不将截图中的建议直接视为既定需求。此次交付为分析文档，未修改业务实现。

## 1. 结论

截图指出的版本约束矛盾、身份重复查询、统计错误处理过宽和连接释放职责分散确实存在；但“快照事务没有必要”“单 worker 可以直接换成本地会话锁”“官方工具可以直接替换”均不能成立为当前整改依据。

| 编号 | 截图意见 | 当前判定 | 优先级与处理 |
| --- | --- | --- | --- |
| C1 | 单实例、单 worker、SQLite checkpoint，却使用幂等、去重、租约和提交围栏 | 机制存在，但不能据此认定过度设计；支持边界描述基本正确 | 保留现有行为；锁简化仅作为有条件的后续优化 |
| C2 | 使用 9 个私有接口，精确版本检查与依赖范围冲突 | 成立；升级后必定被版本检查拒绝，是否存在实际 API 不兼容另需验证 | P2：统一依赖契约及能力检查 |
| C3 | 官方已有手动压缩，设计未考虑 | 官方能力存在；现有设计未明确评估该工具，但不能推断开发者从未考虑 | P2 调研项：做行为对照后决定迁移 |
| T1 | 两条查询同一截止时间，因此无需快照事务 | 不成立；已用临时 SQLite 实验反证 | 保留快照；不按该意见删除事务 |
| T2 | `days` 校验重复、禁止缓存写四次、所有异常转 503 | 前两者事实成立，但有边界职责；异常转换过宽成立 | P2：收窄异常；校验和响应头为低优先级整理 |
| T3 | 身份 scope 重复，聊天请求两次查询用户 | scope 模式重复成立；当前共有 11 处；正常聊天两次额外用量身份查询成立 | P2：请求内复用已解析身份，同时保留执行期 scope |
| T4 | 两个 200 多行函数因 `with` 整体缩进，diff 约 500 行 | 整体包裹成立；当前两个函数分别为 154、156 行，截图行数不能当作现状 | P3：局部结构优化；历史 diff 大小不单独构成缺陷 |
| T5 | 仅新仓储显式关闭 MySQL 连接，应统一到数据库层 | 生命周期不统一成立；不能据此断言其他所有路径必然永久泄漏 | P2：统一池连接归还契约，单独实施并回归 |

P2 表示需要安排修复或兼容性治理；P3 表示可选维护性优化。本次未确认当前锁定版本下手动压缩已失效，也未确认现有用量统计返回错误。

## 2. 手动压缩逐项复核

### C1：支持范围真实，删除防护的建议依据不足

证据：

- [`compaction.py`](../../easy_agent/services/sessions/compaction.py) 的 `supported()`（47 行）要求 `agent.session_thread`、SQLite checkpointer，以及 `detect_worker_count() == 1`。这里要求的是 **checkpoint 使用 SQLite**，不代表业务数据库必须是 SQLite；业务库为 MySQL、checkpoint 为 SQLite 的单 worker 组合没有被该条件排除。
- `detect_worker_count()` 同时读取副本数和 worker 环境声明。因此“单实例”是配置约束，不是代码自动发现网络中所有实例的能力。
- `compact()`（64 行）先从 checkpoint 查已提交结果，以 `(session_id, request_id)` 合并正在执行的任务，再通过 `asyncio.shield()` 处理请求断开。
- `_execute()`（118 行）复用聊天的业务库会话租约，30 秒有效、每 8 秒续约；`_prepare_and_commit()` 在提交时调用 `SessionRuntimeRepository.guarded_commit()`。
- [`session_runtime.py`](../../easy_agent/db/repositories/session_runtime.py:171) 验证 owner、过期及取消状态，持有业务库写锁执行 checkpoint 提交。SQLite 是数据库写锁，不能把它理解为只影响一行。
- `independent_checkpoint()`（30 行）正是为该提交方式拒绝业务库与 checkpoint 同文件。否则业务事务持锁并等待另一个连接写入同一个 SQLite 文件，会产生锁等待/失败风险。

这些机制解决的问题并不相同：单 worker 仍可并发处理多个协程；代理超时、断线重试、后台生成、审批恢复也仍然存在。持久请求记录保证已提交操作不重复执行，任务表合并正在执行的重复请求，租约负责与其他会话操作互斥，提交围栏防止失去占用权后覆盖状态。写后回读确认结果，读取失败时按原请求 ID 恢复。这些都不因单 worker 自动消失。

**修改方案：** 当前先保留防护。若要简化，先证明所有 checkpoint 写入入口均通过同一个进程内会话互斥入口，包括聊天、resume、压缩及相关状态修改；然后在独立变更中替换互斥机制。不能仅在压缩函数内新增 `asyncio.Lock`，因为聊天仍使用业务库租约，二者不会自动互斥。即使换锁，也应保留请求 ID、持久结果、断线恢复和提交取消保护；只有移除跨库持锁提交方式后，才重新评估同库限制。新的锁需要明确实例生命周期、等待策略和释放时机。

**验证方案：** 对同会话并行发起聊天/压缩、resume/压缩及不同 ID 的压缩，只允许一个修改状态；同 ID 请求只触发一次摘要；取消首个等待者不取消实际提交；已有 owner 不被旧任务释放。验证租约续约失败、提交期间取消、超时与写后读失败。已有测试覆盖部分场景，但未充分覆盖真实编排层的心跳失败和提交中超时，后续应补行为测试，不能仅保留直接调用仓储方法的测试。

### C2：精确运行时版本检查与安装契约矛盾

[`manual_compaction.py`](../../easy_agent/agent/manual_compaction.py:57) 强制 `deepagents == 0.7.28` 且 `langgraph == 1.2.16`；同文件调用以下 9 个 DeepAgents 私有方法：

```text
_apply_event_to_messages   _determine_cutoff_index   _partition_messages
_aoffload_inline_media    _acreate_summary          _get_session_id
_aoffload_to_backend      _build_new_messages_with_path
_compute_state_cutoff
```

[`pyproject.toml`](../../pyproject.toml:14) 声明 `deepagents>=0.7.28,<0.8`，没有直接钉住 LangGraph；[`uv.lock`](../../uv.lock) 当前锁定上述两个精确版本。因此冻结锁文件的部署当前可正常使用；重新解锁依赖或通过包元数据安装时，可能安装被允许但手动压缩拒绝的版本。

本次用版本查询替身分别返回 `deepagents 0.7.29`、`langgraph 1.2.17`，均在进入模型逻辑前得到 `RuntimeError("手动压缩适配层尚未验证当前框架版本")`。这证明的是**版本守卫必然拒绝**，不证明新版私有 API 必然损坏。另一个问题是 `context_status()` 的 `supported()` 未包含版本兼容性，可能显示支持，执行时才通过 502 告知失败。

**修改方案：** 短期保留适配层集中封装，在项目依赖中明确锁定当前经过验证的 `deepagents==0.7.28`、`langgraph==1.2.16`，通过 `uv lock` 正规更新锁文件。增加统一兼容性判断，让状态查询与执行使用同一依据，拒绝时给出明确的不支持原因，而非等摘要执行阶段报通用失败。中期在契约测试通过后扩大支持集合；不能只删版本检查而继续无约束调用私有接口。项目安装与启动探测策略应同步写入设计和验收文档。

**验证方案：** 验证标准冻结安装、声明版本安装和不支持版本的能力查询；支持版本执行“摘要 → 持久化 → 重启 → 下一轮读取摘要”；不支持版本在摘要或归档前明确拒绝。升级时检查事件格式、cutoff 换算、归档、工具配对和图终止节点，验证未额外触发主模型回答。

### C3：官方工具存在，但与产品操作有行为差异

本机安装的 DeepAgents 0.7.28 源码确实包含 `SummarizationToolMiddleware`。其文档明确支持模型工具调用或用户显式操作触发，不应声称官方没有手动压缩能力。

但源码也表明：工具通过 `ToolRuntime` 输入并返回 `Command`/`ToolMessage`；有约为自动摘要阈值一半的准入门槛（token/fraction 条件依赖 reported usage）；其异步压缩路径没有复制本项目的媒体预处理、无效摘要检查和长工具链摘要降级。这些行为与本项目“主动执行、不等待自动阈值、自动压缩关闭时仍可操作、不生成额外主模型回答”并不天然等价。

现有[设计文档](../design/指令菜单于手动压缩设计.md)明确记录了私有适配层及锁定版本验证，但没有点名对比官方工具。因此应补一份采用/不采用依据，不能直接判定“已有工具，当前实现应全部删除”。

**修改方案：** 做最小集成验证，优先尝试官方暴露的工具入口，不调用其 `_arun_compact` 等私有入口来制造“已摆脱私有接口”的假象。评估工具执行是否能在不恢复完整生成流程的条件下满足状态更新、无工具痕迹污染 UI、归档隔离、主动压缩及失败恢复。满足全部验收条件时，再替换摘要实现；业务鉴权、互斥、幂等和结果投影仍由项目保留。若无法满足，则保留当前适配层并记录差异。

**验证方案：** 对照自动压缩关闭、低于官方门槛、无 reported usage、长工具链、媒体归档失败、无效摘要、已有摘要再次压缩等场景；检查调用次数、原历史、归档和下一轮实际模型输入。原型阶段使用假模型，不需要真实供应商消费。

## 3. Token 统计逐项复核

### T1：一致性快照有实际用途，应保留

[`token_usage.py`](../../easy_agent/db/repositories/token_usage.py:103) 在同一连接上开启 SQLite 读事务，或 MySQL `REPEATABLE READ` 只读一致性快照，再分别查询 summary 与 daily。

相同 `as_of_ms` 只限定业务完成时间，不能限定提交可见性。例如一条已完成的模型用量在查询截止时间之前产生，但由于回调/线程调度而稍后入库：第一条 SELECT 看不到，第二条却可能看到。当前[统计规则](../token-usage-api.md)及[设计](../design/2026-10-30-Token用量统计设计.md)要求同响应窗口合计一致，并非无需一致性的独立报表。

本次在临时 SQLite 账本进行对照：先写 12 tokens，在两次 SELECT 之间通过另一连接补写 23 tokens，补写记录的 `completed_at_ms` 同样小于固定的 `as_of_ms`：

| 模式 | `summary.today` | 趋势合计 |
| --- | ---: | ---: |
| 当前实现，保留读快照 | 12 | 12 |
| 仅绕过显式 `BEGIN` | 12 | 35 |

[`test_sqlite_read_snapshot_excludes_write_between_queries`](../../tests/db/test_token_usage.py:160) 已覆盖该竞争。MySQL 测试也覆盖连接默认为 READ COMMITTED 时的并发插入，但本轮未连接真实 MySQL 执行。

**修改方案：** 保持当前事务。若未来确有性能数据表明需要优化，可研究单条 SQL 生成 summary/daily 或聚合表；仍须保留当前时间边界、补零和一致性契约。不能单纯删除快照。

**验证方案：** 保留跨连接插入回归；覆盖上海零点、7/30 日起点、截止时间排除、全历史合计、用户隔离，以及查询异常回滚后下一次查询成功。MySQL 必须以真实 InnoDB、默认 READ COMMITTED 运行对应测试。

### T2：校验并非全部冗余，异常转换需要收窄

[`usage.py`](../../easy_agent/api/usage.py) 中存在三种不同职责：

1. 路由 72 行拒绝非法请求；仓储 88 行保护直接调用；响应模型 `Literal[7, 30]` 约束输出。它们不是三次同类输入校验。可以共享允许值或领域类型，但无需为删除两条简单判断引入复杂抽象。
2. `Cache-Control: no-store` 分别在通用故障、成功响应、非法参数和用户不存在四条分支设置。直接删除错误分支的 header 会改变行为；成功响应对象上的 header 不保证自动带到异常响应。
3. `_usage_errors()`（18 行）放行 HTTP/参数验证错误，捕获其他所有 `Exception` 并转换为 503。这确实会将编程错误、输出模型验证失败等也包装成“统计暂不可用”，且只记录异常类型，降低排错信息量。

**修改方案：** 将预期数据库故障转换为明确的服务不可用异常或只捕获明确的数据库异常类型，仍覆盖鉴权阶段的查库失败；其他异常保留服务器错误及完整服务端 traceback，响应不泄露内部信息。现有测试用 `RuntimeError` 模拟数据库故障，实施时需改为真实数据库异常或约定的领域异常，并补充编程错误不能误报 503 的用例。缓存头优先用局部响应策略统一，覆盖成功、401、422、503 和未预期的错误响应；不要因此影响全站其他接口。

**验证方案：** `days` 默认/7/30 成功，非法整数与非整数返回 422；无登录或用户消失返回 401；鉴权或统计查询数据库故障返回 503；业务内部 `TypeError`/输出校验错误保留 500 并有 traceback。各分支检查 no-store。不要把尚未测试过的所有异常分支 header 行为宣称为已保证。

### T3：重复身份查询成立，执行期 scope 仍然必要

当前普通聊天链路：

```text
prepare_turn()
  ├─ resolve_usage_identity(username, session_id)   # chat_turn.py:481
  ├─ usage_scope(...) → 专家相关性预检
  └─ 构造知识适配流 → 后台实际迭代宿主生成器
       └─ chat_stream_generator()
            ├─ resolve_usage_identity(...)        # orchestrator.py:1033
            └─ usage_scope(...) → 流式模型调用
```

非拒答的正常聊天确实有两次用量身份查询；专家预检拒答会提前结束，不进入后者。上面计数不包括现有登录态校验的数据库访问。当前共有 11 处 `with usage_scope(...)`，但知识问答直接使用可信 principal、记忆线程使用捕获的身份，专家写入工具也有复用逻辑，不能将 11 处理解为 11 次重复查用户。

scope 必须在实际消费异步生成器、后台任务或线程时建立并在退出时复位。仅在创建生成器时包一层 scope，作用域退出后再迭代，并不能保证身份正确。查询失败时显式清空身份，也不能改成继承外层上下文。

**修改方案：** 在聊天准备阶段解析一次不可变 `UsageIdentity`，作为内部参数通过知识适配包装层传入宿主生成器；预检和实际流仍各自建立 scope。用明确哨兵区分“调用者没有提供身份，应自行解析”与“调用者已经解析但失败，应禁用采集”，不能都用默认 `None` 后再次查询。resume、定时任务、压缩等独立入口保留自己的解析边界；不向共享模型或编译图缓存写入用户身份，不信任客户端传来的 ID。

对于重复的 scope 参数展开，可让 `usage_scope` 支持身份对象或新增一个薄包装；保留原有 reset/finally 语义。普通异步函数装饰器不能未经验证就用于异步生成器，否则 scope 可能只覆盖生成器创建，而没有覆盖迭代。

**验证方案：** 正常聊天只做一次用量身份解析；解析失败不在流阶段重查、不继承其他用户；专家预检和主模型记录同一真实用户 ID。覆盖知识包装层参数透传、两个用户交错消费生成器、请求断开后后台继续、resume、定时任务、压缩重复请求和记忆线程队列，保证无串用户且退出后复位。

### T4：缩进影响可读性，但不是独立功能缺陷

[`orchestrator.py`](../../easy_agent/services/streaming/orchestrator.py:1021) 当前 `chat_stream_generator` 为 154 行，`resume_stream_generator` 为 156 行，scope 分别覆盖 1034–1174 行、1197–1332 行。截图的“200 多行”和“diff 500 行”不能作为当前代码规模；本次不以历史 diff 行数认定缺陷。

**修改方案：** 若后续仍需整理，可将公共入口保持为“确定身份 → 建立 scope → 转发内部生成器”，原业务代码放入对应的私有实现函数。显式关闭内部生成器，并保留取消、异常、首事件和终态事件行为。只在确实降低阅读负担时实施，不为移动缩进新增一套通用执行框架，也不与账本逻辑修改混在同一大提交。

**验证方案：** 比较完整 SSE 序列、取消后部分消息保存、HITL 挂起和恢复、生成器 `aclose()`、身份复位及后台任务结束。若单独改缩进而不改变行为，不编写只断言函数结构的测试。

### T5：连接生命周期职责分散成立

[`Database.get_connection()`](../../easy_agent/db/database.py:226) 的 MySQL 分支仅负责 commit/rollback，没有 `finally: conn.close()`；[`TokenUsageRepository._connection()`](../../easy_agent/db/repositories/token_usage.py:17) 额外在 finally 归还 MySQL 池连接。`users.py`、`sessions.py` 等大量现有仓储只使用 `get_connection()`，没有对应的显式 close。说明生命周期约定确实不统一。

不过池对象的垃圾回收可能归还连接，不能从静态代码就断言每个路径都会永久泄漏或已经耗尽连接池。本次没有执行真实 MySQL 池压力测试，不能量化影响。统计仓储的局部 finally 是有效补偿，不应先删除。

**修改方案：** 先审计所有 `get_connection()` 调用是否借出连接或 cursor 给上下文外部，再由数据库层统一在 MySQL 分支的 finally 中执行 `close()`，保证 commit、rollback 或执行失败也尝试归还。保留 SQLite 的共享连接和锁语义，不能统一把 SQLite 连接也关闭。明确 close/rollback 异常的日志和原异常保留策略。该契约落实并通过池验证后，再删除统计仓储的补偿封装，避免重复归还。不要顺带迁移所有仓储或重写整个数据层。

**验证方案：** 使用可观测的池连接替身分别验证成功、查询失败、commit 失败、rollback 失败的归还次数与异常；SQLite 多次读写仍可用；真实 MySQL 用小连接池重复/并发调用用户、会话和统计仓储，成功与失败后借用数回落且无池耗尽。覆盖统计事务隔离级别不污染下一位借用者，以及两 SELECT 仍共享同一连接和快照。

## 4. 涉及文件与预计改动范围

下表区分必须调整与需要联查的文件，不表示全部都要改。

| 文件 | 关联意见 | 预计操作 |
| --- | --- | --- |
| `pyproject.toml`、`uv.lock` | C2 | 对齐已验证版本契约；锁文件由工具生成 |
| `easy_agent/agent/manual_compaction.py` | C2/C3 | 统一兼容性判断；保留私有调用隔离，或通过验证后迁移官方入口 |
| `easy_agent/services/sessions/compaction.py` | C1/C2 | 能力状态复用兼容性判断；锁机制先保留 |
| `easy_agent/db/repositories/session_runtime.py` | C1 | 联查租约和围栏；仅在独立锁改造中修改 |
| `easy_agent/services/sessions/runtime.py` | C1 | 联查聊天共用的占用、取消和释放规则 |
| `easy_agent/persistence/checkpoints.py`、`easy_agent/utils/deployment.py` | C1/C2 | 联查实际 saver 和部署限制，不默认扩大支持范围 |
| `easy_agent/agent/summarization.py`、`easy_agent/agent/turn_context.py` | C2/C3 | 联查摘要策略、状态字段及事件兼容性 |
| `easy_agent/api/sessions.py` | C1/C2 | 联查压缩接口、能力状态和历史投影 |
| `easy_agent/db/repositories/token_usage.py` | T1/T2/T5 | 保留快照；中央连接契约落实后删除局部补偿；酌情共享允许值 |
| `easy_agent/api/usage.py` | T2 | 收窄异常转换、统一局部缓存策略 |
| `easy_agent/services/usage.py`、`easy_agent/utils/usage_context.py` | T3 | 身份解析边界和 scope 的简化接口 |
| `easy_agent/services/agents/chat_turn.py` | T3 | 请求内复用已解析身份，向流传递 |
| `easy_agent/services/streaming/orchestrator.py` | T3/T4 | 接收显式身份；保留消费期 scope，按需要拆分函数 |
| `easy_agent/knowledges/chat/streaming.py`、`easy_agent/knowledges/chat/host_port.py` | T3 | 检查知识流包装及宿主端口的参数透传 |
| `easy_agent/middleware/auth.py`、`easy_agent/db/login_state.py` | T2/T3 | 联查鉴权错误及可信身份来源；第一阶段不要求重构鉴权 |
| `easy_agent/db/database.py` | T5 | 统一 MySQL 池连接归还 |
| `easy_agent/db/repositories/users.py`、`easy_agent/db/repositories/sessions.py` 及其他连接调用方 | T5 | 生命周期审计和回归，不预设逐文件重写 |

scope 简化还需检查其他使用方：`easy_agent/api/completion.py`、`easy_agent/api/prompt_enhancement.py`、`easy_agent/api/expert_helpers.py`、`easy_agent/knowledges/api.py`、`easy_agent/services/scheduler.py`、`easy_agent/services/streaming/side_effects.py`、`easy_agent/tools/expert_writer.py`。这些入口身份来源和生命周期不同，不应机械合并。

相关测试和文档：

| 文件 | 验证/同步内容 |
| --- | --- |
| `tests/services/test_manual_compaction.py` | 重启恢复、摘要失败、重复请求、独立库限制、提交结果未知；补编排层超时/心跳场景 |
| `tests/api/test_context_compaction.py` | 能力状态、归属、忙碌、部署限制、压缩用量计费 |
| `tests/services/test_session_runtime.py`、`tests/db/test_session_stream_lock.py` | 共用会话占用、释放和并发语义 |
| `tests/services/test_session_thread.py`、`tests/api/test_session_thread_api.py` | 摘要后历史对齐与后续会话输入 |
| `tests/db/test_token_usage.py`、`tests/api/test_usage.py` | 聚合快照、时间边界、状态码、故障分类、缓存头 |
| `tests/services/test_usage_scopes.py`、`tests/api/test_chat_turn_prep.py` | 身份解析次数、生成器生命周期及任务/线程隔离 |
| `tests/knowledges/test_chat_session.py` | 知识包装层参数与聊天链路兼容 |
| `tests/agent/test_token_usage.py`、`tests/agent/test_token_usage_graph.py` | 模型回调、每次调用入账和共享图身份隔离 |
| `tests/db/test_database_users.py`、`tests/db/test_user_repository.py`、`tests/db/test_session_repository.py` | 数据层生命周期调整的既有调用回归；建议按需新增连接管理行为测试 |
| `docs/design/指令菜单于手动压缩设计.md` | 官方工具选型对照、版本和锁简化取舍 |
| `docs/design/2026-10-30-Token用量统计设计.md`、`docs/token-usage-api.md`、`docs/token-usage-verification.md` | 依赖契约、错误语义、身份传递及实际验收结果 |
| `docs/architecture-current.md` | 仅在实际改变数据流/能力边界后同步事实 |

本方案不改变前端交互和接口字段，默认不需要修改两版前端；若官方工具迁移导致用户可见门槛、消息或结果变化，必须重新评估产品行为并验证两版前端，不能视为纯后端替换。

## 5. 建议实施顺序与验收命令

1. **先修版本契约和能力状态。** 改动集中于适配层、压缩服务、依赖及真实图契约测试，避免依赖升级后功能暗中失效。
2. **单独统一 MySQL 连接管理。** 影响面覆盖整个数据层，独立提交，先验证再删除统计仓储补偿。
3. **修统计故障分类。** 保留快照，分别处理数据库不可用与程序错误，并验证所有缓存头分支。
4. **复用聊天用量身份。** 先消除确定的二次解析，再按收益整理 scope 写法和生成器结构。
5. **官方工具替换与锁简化另立评估。** 两项均以语义等价和竞争测试为前提，当前不作为必须执行的删代码任务。

核心回归从仓库根运行：

```bash
uv run pytest tests/services/test_manual_compaction.py tests/api/test_context_compaction.py tests/services/test_session_runtime.py tests/db/test_session_stream_lock.py tests/services/test_session_thread.py tests/api/test_session_thread_api.py -q
uv run pytest tests/agent/test_token_usage.py tests/agent/test_token_usage_graph.py tests/db/test_token_usage.py tests/api/test_usage.py tests/services/test_usage_scopes.py tests/api/test_chat_turn_prep.py tests/knowledges/test_chat_session.py -q
uv run pytest tests/db/test_database_users.py tests/db/test_user_repository.py tests/db/test_session_repository.py -q
```

真实 MySQL 验证使用现有 `EASY_TEST_MYSQL_HOST/PORT/USER/PASSWORD` 开关，在有建删临时测试库权限的专用环境执行 `tests/db/test_token_usage.py`，并补跑连接池生命周期验证。未设置开关时会跳过，不能据 SQLite 结果宣称已验证 MySQL。无需为本次重构运行会产生消费的真实模型供应商测试。

实施后运行 Ruff 和仓库必要架构门禁、完整后端测试；依赖变化用冻结安装验证。准备 PR 时按仓库要求构建前端。只有修改前端交互或内部共享接口时，再补对应前端单测与浏览器验收。

## 6. 本次已执行的验证与限制

- 当前本机框架版本：DeepAgents 0.7.28、LangGraph 1.2.16，与 `uv.lock` 一致。
- 使用现有虚拟环境执行 `tests/services/test_manual_compaction.py`、`tests/api/test_context_compaction.py`、`tests/db/test_token_usage.py`、`tests/api/test_usage.py`、`tests/services/test_usage_scopes.py`：**56 passed、1 skipped，18.03 秒**。跳过项为未配置连接的真实 MySQL 测试。
- 补充执行 `tests/agent/test_token_usage.py`、`tests/agent/test_token_usage_graph.py`、`tests/services/test_session_runtime.py`、`tests/db/test_session_stream_lock.py`、`tests/api/test_chat_turn_prep.py`：**78 passed，14.35 秒**。两组合计 **134 passed、1 skipped**。
- 临时 SQLite 跨连接实验：保留快照时 12/12，绕过 BEGIN 时 12/35；未更改仓库实现。
- 版本守卫实验：分别模拟 DeepAgents 0.7.29、LangGraph 1.2.17，均被现有守卫拒绝；没有安装或声称测试这些新版框架。
- 静态 AST 核查：适配层确实使用 9 个中间件私有方法；全后端 11 处 `usage_scope` 上下文；两个流式入口当前为 154/156 行。
- 官方工具结论来自本机已安装 0.7.28 源码，不是已完成替换验证。未执行真实 MySQL 压测、全量测试或前端构建；上述未来验证方案不代表已经通过。
- 文档引用的本地文件已检查均存在；`git diff --check` 无错误。此次仅新增本 Markdown 文档。

## 7. Token 统计 P2/P3 本轮整改与验证（2026-11-12）

本轮仅实施 T2、T3、T4、T5。第 6 节保留原复核时的历史记录；本轮结果以下述定向验收为准。未修改或专项验收 C1/C2/C3；T1 的快照事务及聚合规则保持原样，快照相关用例仅验证 T5 连接生命周期调整的兼容性。

| 项目 | 实施结果 | 本轮验证 |
| --- | --- | --- |
| T2 / P2 | 统计路由局部 `APIRoute` 统一设置 `no-store`；明确的 SQLite/PyMySQL OperationalError、InterfaceError 及池耗尽转 503，其余内部错误返回通用 500 并记录 traceback；保留路由/仓储各自的输入边界 | 默认/显式 7/30 成功；整数/非整数非法值 422；匿名、JWT 用户消失及鉴权后用户消失 401；鉴权/用户查询/统计查询数据库故障 503；TypeError、输出校验及鉴权内部错误 500；检查缓存头与敏感错误信息隐藏，其他路由不受影响 |
| T3 / P2 | `prepare_turn` 解析的不可变身份经知识包装层传给宿主流；显式哨兵区分省略与已解析的 `None`；`usage_scope` 支持身份对象并保留 finally/reset | 成功/失败解析均只查询一次；预检与实际流使用相同身份，失败显式清空；两个用户延迟任务交错、共享模型/图、记忆线程排队及独立定时/知识入口归属回归 |
| T4 / P3 | chat/resume 公共生成器仅解析身份、建立执行期 scope、转发私有实现；用 `aclosing` 在退出 scope 前关闭私有生成器及其 `_run_stream`；以生命周期行为测试替换原函数源码结构断言 | 完整 SSE golden、HITL 挂起/恢复、准备/流中异常、取消及部分消息保存通过；两个公共生成器 `aclose()` 时内部清理仍持有正确身份；客户端断开后后台继续执行并复位身份 |
| T5 / P2 | MySQL commit/rollback/close 统一由 `Database.get_connection` 管理，清理失败保留原异常并记录 traceback；删除统计仓储局部归还封装；SQLite 共享连接与锁不变 | 替身验证成功、查询/commit/rollback/close/取消失败的归还与异常保留；SQLite 重复使用与回滚；真实 MySQL 两连接池重复/并发调用用户、会话、统计仓储及失败 SQL，36 轮后借用数归零；真实快照、失败恢复、两条查询同游标、两个池连接仍为 READ COMMITTED |

T5 实施前联查了 `get_connection()` 调用方，未发现要求将借用连接/游标交给上下文外继续使用的接口，未进行其他仓储迁移。连接池成功提交后的 close 故障向调用方报告；已有查询/commit 故障时，rollback/close 故障只记日志并保留原异常。

最终定向验收：**112 passed、0 failed、0 skipped，19.77 秒**。其中包含真实 MySQL **8.4.10** 的两项 T5 专项；使用独立临时实例和自动建删的临时测试库，连接池上限 2。以下命令复现同一组验收（MySQL 环境变量指向专用测试实例）：

```bash
EASY_TEST_MYSQL_HOST=127.0.0.1 EASY_TEST_MYSQL_PORT=23316 EASY_TEST_MYSQL_USER=root \
.venv/bin/python -m pytest -q \
  tests/db/test_database_connections.py \
  tests/api/test_usage.py tests/api/test_chat_turn_prep.py \
  tests/services/test_usage_scopes.py tests/services/test_stream_runner.py \
  tests/services/test_stream_split_golden.py \
  tests/knowledges/test_chat_session.py::test_knowledge_chat_stream_injects_evidence_and_persists_metadata \
  tests/db/test_token_usage.py::test_real_mysql_small_pool_returns_connections_across_repositories \
  tests/db/test_token_usage.py::test_real_mysql_return_preserves_snapshot_and_next_borrow_isolation \
  tests/db/test_token_usage.py::test_sqlite_read_snapshot_excludes_write_between_queries \
  tests/db/test_token_usage.py::test_failed_read_rolls_back_snapshot_and_allows_next_query \
  tests/db/test_user_repository.py::test_repository_delegates_registration_lookup_and_password_reset \
  tests/db/test_session_repository.py::test_repository_wraps_database_and_delegates_session_crud \
  tests/db/test_session_repository.py::test_repository_delegates_messages_and_keeps_a2_single_write \
  tests/agent/test_token_usage.py::test_shared_model_concurrent_users_and_successive_calls \
  tests/agent/test_token_usage.py::test_callback_freezes_identity_and_cleans_error_and_duplicate \
  tests/agent/test_token_usage_graph.py::test_shared_graph_concurrent_users_keep_separate_identity_snapshots \
  tests/agent/test_token_usage_graph.py::test_real_tool_loop_hitl_resume_and_checkpoint_read_are_counted_once
```

所有变更 Python 文件的 Ruff 检查及 `git diff --check` 通过。依赖产生 5 条已有 Swig 弃用警告。依照本轮范围约束，未运行全量后端、前端或真实供应商消费测试，也不据本机 MySQL 结果宣称生产库已验收。临时 MySQL 实例验收后关闭并清理。
