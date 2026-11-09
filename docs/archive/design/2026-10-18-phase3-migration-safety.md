# Phase 3 后端部分：迁移串行化与启动只校验

> K8s 清单/探针/HPA/PDB 按用户要求跳过；文件对象存储与外部 Sandbox 仍属后续。

## 1. 目标

多副本同时启动时，`schema.init_tables()` 会并发执行建表/补列/数据自愈，存在：

- MySQL DDL 并发锁等待、半迁移；
- 两个 Pod 同时改列/补索引，重复报错或版本账本不一致；
- 每次 Pod 启动都跑全量 DDL，启动慢且放大 DB 压力。

本阶段实现：

1. **跨进程/跨 Pod 迁移锁**，同一库同时只有一个 Pod 执行迁移；
2. **一次性迁移入口** `scripts/db/migrate_db.py`（K8s Job/initContainer 等价物）；
3. **启动只校验模式** `EASY_AUTO_MIGRATE=0`，结构不匹配 fail-fast 并提示跑迁移脚本；
4. 保留单机/开发默认 `EASY_AUTO_MIGRATE=1`，行为不变。

## 2. 实现

### 2.1 迁移锁 `easy_agent/db/migration_lock.py`

| DB | 机制 |
| --- | --- |
| MySQL | `GET_LOCK('easy_agent_schema_migration', timeout)`，连接关闭自动释放 |
| SQLite | 数据库旁 `.migrate.lock` + `fcntl.flock(LOCK_EX)`，非阻塞轮询到超时 |
| Windows/无 fcntl | 降级进程内 `threading.Lock` + warning（生产多副本用 MySQL） |

- 超时 `EASY_MIGRATION_LOCK_TIMEOUT` 默认 120s；超时 RuntimeError，启动失败。
- `schema.init_tables()` 改为：`with schema_migration_lock(db): _init_tables_locked(db)`。

### 2.2 一次性迁移脚本

```bash
python scripts/db/migrate_db.py          # 持锁迁移
python scripts/db/migrate_db.py --check  # 只校验 schema_version/关键表
```

- 复用 `initialize_runtime()` + `init_database()`；
- `--check` 强制 `EASY_AUTO_MIGRATE=0`；
- K8s 中可配置为 Job/initContainer（清单本次跳过）。

### 2.3 启动只校验

`Database.init_database()`：

```python
if env_bool("EASY_AUTO_MIGRATE", True):
    db.init_tables()
else:
    db.verify_schema_current()
```

`verify_schema_current()` 检查 `schema_version == SCHEMA_VERSION` 且
`EXPECTED_TABLES` 齐全；不匹配抛：

```
RuntimeError: 数据库结构不是最新（...）。请先运行：python scripts/db/migrate_db.py
```

## 3. 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EASY_AUTO_MIGRATE` | `1` | 生产多副本建议迁移 Job 完成后设 `0` |
| `EASY_MIGRATION_LOCK_TIMEOUT` | `120` | 迁移锁等待秒数 |

`.env.*.example` 已补充说明。

## 4. 验证

- `tests/db/test_migration_lock.py`：
  - SQLite 锁 sidecar；
  - 子进程持锁 → 父进程超时 RuntimeError；
  - `verify_schema_current` 通过/旧版本拒绝；
  - `EASY_AUTO_MIGRATE=0` 旧库 `init_database` fail-fast；
  - 默认自动迁移仍可用。
- 全量测试：见 `docs/audit/2026-10-18-phase3-backend-report.md`。
- double-write 门禁新增 `verify_schema_current` pure delegation。

## 5. 未做 / 后续

| 项 | 状态 |
| --- | --- |
| K8s 探针/HPA/PDB/滚动/安全上下文 | 用户要求跳过 |
| Alembic 版本化迁移 | 未引入；当前 schema_meta + 锁 + 迁移脚本 |
| 文件对象存储 / 外部 Sandbox | 未做；Phase 3 后续 |
| MySQL 迁移锁实机 | 仅代码路径/单测；SQLite 多进程已测 |
| migrations 账本与 DDL 并发 | 锁保护整段 `init_tables`；DDL 内容未拆分 |
