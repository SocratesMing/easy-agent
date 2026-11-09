# Increment 38 执行报告（FW-09 官方 checkpointer 默认化）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-18（Round 36） |
| 依据 | 审计 FW-09「自研 checkpointer」；用户决策：默认切官方 saver |
| 约束 | 仅改 `checkpoints.py`、`app.py` 启停段、checkpoint 测试、`conftest.py` 最小隔离、文档；不动 `streaming.py` / `db/` / API / 配置模型；不 commit |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 后端选择 | `checkpoints.resolve_checkpoint_backend()` + `CheckpointSelection` | 纯函数、全入参可注入；单测不依赖真实 PG/MySQL |
| 环境变量 | 新增 `EASY_CHECKPOINT_BACKEND=auto\|postgres\|sqlite\|legacy`（默认 `auto`） | 显式指定后端；非法值告警并按 `auto` 处理 |
| 默认 SQLite | `DEFAULT_SQLITE_PATH = "data/checkpoints.db"`（相对路径按项目根解析） | 单机业务库为 SQLite 时默认官方 `AsyncSqliteSaver` |
| 启动编排 | `start_checkpoint_backend()`：postgres → sqlite → legacy 逐级降级 | 官方 saver 打不开不再只告警摆烂，而是按可用后端降级，不阻断启动 |
| 关闭清理 | `shutdown_checkpointers()` 关闭 PG/SQLite saver 并清空 `_CHECKPOINTERS` | 全量关闭 + 清全局状态，测试隔离依赖此函数 |
| app 接线 | lifespan 仅调用 `start_checkpoint_backend()` / `shutdown_checkpointers()` | 启停逻辑收敛，app 不再直接判断 `EASY_CHECKPOINT_*` |
| 兼容 | `get_session_checkpointer()` 语义不变 | 官方 saver 激活时全局共享；否则按 session 回放自研 saver |
| 测试隔离 | `tests/conftest.py`：模块级临时 `EASY_CHECKPOINT_SQLITE_PATH` + 每个 `client` fixture 独立文件，并 `delenv` PG URI | 不写仓库 `data/checkpoints.db`，用例之间不共享 HITL 中断态 |
| 文档 | `docs/framework-integration.md` FW-09 段 + 本报告 | 默认行为、MySQL 回退、降级链有据可查 |

## 2. 选择规则（`EASY_CHECKPOINT_BACKEND`）

| 值 | 行为 |
| --- | --- |
| `auto`（默认） | ① `EASY_CHECKPOINT_POSTGRES_URI` → 官方 `AsyncPostgresSaver`；② 否则业务库为 SQLite（`get_database().db_type` / 已加载的 `_db_instance`）→ 官方 `AsyncSqliteSaver`，路径 `data/checkpoints.db`（可用 `EASY_CHECKPOINT_SQLITE_PATH` 覆盖）；③ 业务库为 MySQL 且无 PG URI → 官方无 MySQL saver，**回退自研 `PersistentMemorySaver` 并 warning**；④ 其它/未知（DB 未初始化）→ 自研 + warning |
| `postgres` | 要求官方 PG；缺 URI 时告警并回落 `auto` 规则（不会静默成功） |
| `sqlite` | 强制官方 SQLite（路径缺省 `data/checkpoints.db`）；业务库为 MySQL 时额外告警「本地文件、多副本各写各的」 |
| `legacy` | 强制自研快照；即使配置了 PG URI 也不使用 |
| 非法值 | 告警后按 `auto` 处理 |

补充规则：`auto` 下若显式配置了 `EASY_CHECKPOINT_SQLITE_PATH`，优先于「MySQL → legacy」回退
（保持 FW-09 部分落地时的既有行为；业务库为 MySQL 时会附带单机/多副本告警）。

降级链（`_open_with_fallback`）：`postgres` 打开失败 → 按 `auto`（去掉 PG）重算，通常落到
SQLite；`sqlite` 打开失败 → `legacy`。任何一步失败只 `logger.error`，服务继续启动。

## 3. 验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新增后端选择/启停测试 | `pytest tests/services/test_checkpoint_backend.py tests/services/test_checkpoints_sqlite.py -q -p no:cacheprovider` | **27 passed** |
| checkpoint 往返 + 裁剪回归 | `pytest tests/db/test_checkpoint_roundtrip.py tests/db/test_checkpoint_pruning.py -q` | 通过 |
| HITL 相关 API | `pytest tests/api/test_chat.py tests/api/test_expert.py -q` | **74 passed**（58.4s） |
| 全量 | `pytest tests/ -q -p no:cacheprovider` | **1023 passed, 1 skipped**（246.9s，exit 0） |
| Ruff | `uvx ruff check .` | **All checks passed** |
| 仓库无污染 | `ls -l --time-style=full-iso data/checkpoints.db` | 运行前后 mtime/size 不变（2026-10-17 13:00:24，118784B） |

测试计数口径：本轮新增 `test_checkpoint_backend.py` 23 个用例，`test_checkpoints_sqlite.py`
由 1 个扩到 4 个（净 +3），合计 **+26**；全量 1024 个用例（1023 passed + 1 skipped）
反推改动前为 **997 passed / 1 skipped**。文档记录的 986 passed 是 increment37 时点，
期间其它未提交增量新增了 11 个用例，与本任务无关。

覆盖点：auto 的 PG 优先 / SQLite 默认与覆盖 / MySQL legacy 回退 / 未知库回退、
强制 `legacy`/`sqlite`/`postgres` 与非法值、环境变量读取、`_db_instance` 类型探测、
官方 SQLite saver 打开-共享-关闭、PG 打开失败降级 SQLite、SQLite 打开失败降级 legacy、
`shutdown_checkpointers()` 幂等且清空自研缓存、app lifespan 真实启用官方 SQLite saver
且文件落在临时目录。

## 4. MySQL 回退行为（明确说明）

- 业务库为 MySQL 且未配置 `EASY_CHECKPOINT_POSTGRES_URI` 时，官方 LangGraph 没有
  MySQL checkpointer，`auto` 回退自研 `PersistentMemorySaver`：HITL 仍可用，跨副本
  恢复仍通过业务库 `agent_checkpoints` 快照完成。
- 代价：每轮 checkpoint 全量 pickle 写业务库（已做 thread/checkpoint 裁剪），非官方
  实现、大状态/高并发会话性能差，且绑定 `MemorySaver.storage/writes/blobs` 私有属性。
- 启动日志会打 `logger.warning`，包含「官方 LangGraph 没有 MySQL checkpointer」「生产建议
  配置 EASY_CHECKPOINT_POSTGRES_URI（多副本）或 EASY_CHECKPOINT_SQLITE_PATH（单机）」。
- 生产 MySQL 环境仍可正常启动（不会被 fail-fast 拒绝），但 `EASY_CHECKPOINT_BACKEND=sqlite`
  在 MySQL 下属于显式覆盖，会额外告警本地文件/多副本问题。

## 5. 风险点与遗留

1. **默认路径**：`data/checkpoints.db` 解析到项目根（不依赖 cwd）。测试已用临时路径隔离；
   生产单机需确认 `data/` 可写、随部署卷持久化，否则重启会丢未完成 HITL 中断态。
2. **SQLite saver 仅适合单机**：多副本/多 worker 共用同一文件不可行（各自本地文件），
   多副本请配置 PG URI。
3. **测试默认走官方 SQLite saver**：`client` fixture 的 lifespan 现在真实打开/关闭
   `AsyncSqliteSaver`，用例耗时略增（全量 246.9s）；若后续出现 aiosqlite 相关抖动，
   可只在 checkpoint 专项用例里启用，其余用例用 `EASY_CHECKPOINT_BACKEND=legacy`。
4. **自研 saver 未删除**：MySQL 场景仍依赖它；删除前需推动 MySQL 用户迁移到 PG。
   绑定 LangGraph 私有属性的升级风险仍在（见 `docs/framework-integration.md` 适配点 3）。
5. **`.env.*.example` 未同步**：本任务允许改动范围不含环境变量示例文件，
   `EASY_CHECKPOINT_BACKEND` 目前只在文档与代码中说明，示例文件待后续批次补。
6. **未执行**：Batch 0/1 安全项、前端、Web Terminal、`git commit`（按任务约束）。

## 6. 后续

1. 视 MySQL 部署占比，评估把 `EASY_CHECKPOINT_BACKEND` 写入 `.env.dev/.test/.prod.example`。
2. 观察生产 PG 打开失败降级到 SQLite 的场景，必要时改为「生产直接 fail-fast」。
3. 自研 `PersistentMemorySaver` 的删除/迁移单独立项，避免与框架升级叠加风险。
