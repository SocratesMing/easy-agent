# MySQL 事务与锁排查手册

适用场景：

- 启动/运行时报 `1205 Lock wait timeout exceeded; try restarting transaction`
- 建表/改列（DDL）卡住，`CREATE DATABASE` / `ALTER TABLE` 长时间不返回
- 需要清理残留事务、确认是谁持有锁

> MySQL 没有「一键删除所有事务」的语句。事务与连接绑定，只能 `ROLLBACK/COMMIT`
> 结束自己的事务，或 `KILL <连接>` 让服务端回滚该连接上的事务。

---

## 1. 查看当前事务与锁

```sql
-- 活跃事务：状态、开始时间、持续秒数、线程、锁定/修改行数、当前 SQL
SELECT trx_id, trx_state, trx_started,
       TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS seconds,
       trx_mysql_thread_id, trx_rows_locked, trx_rows_modified, trx_query
FROM information_schema.INNODB_TRX
ORDER BY seconds DESC;

-- 元数据锁（DDL 等 MDL 时看这里；需要 performance_schema 开启）
SELECT * FROM performance_schema.metadata_locks
WHERE OBJECT_SCHEMA = 'your_db_name';

-- 连接与当前执行状态（看 Time / Command / Info）
SHOW FULL PROCESSLIST;

-- 等待中的锁请求（MySQL 8.0+）
SELECT * FROM performance_schema.data_lock_waits;
```

## 2. 终止事务

```sql
-- 只杀「非系统/非复制、且持续超过 300 秒」的连接，排除当前会话
SELECT GROUP_CONCAT(CONCAT('KILL ', id, ';') SEPARATOR ' ')
FROM information_schema.PROCESSLIST
WHERE Command NOT IN ('Binlog Dump', 'Binlog Dump GTID', 'Daemon')
  AND user NOT IN ('system user', 'event_scheduler')
  AND id <> CONNECTION_ID()
  AND time > 300;
```

把输出复制出来逐条执行（或直接粘贴到客户端批量执行）。

按事务表生成终止语句：

```sql
SELECT CONCAT('KILL ', trx_mysql_thread_id, ';')
FROM information_schema.INNODB_TRX
WHERE trx_mysql_thread_id <> CONNECTION_ID();
```

其他方式：

```sql
KILL QUERY <id>;   -- 只终止当前 SQL，保留连接（事务仍在）
KILL <id>;         -- 断开连接，服务端回滚该连接未提交的事务
```

## 3. 观察回滚与锁释放进度

```sql
SHOW ENGINE INNODB STATUS\G
-- 关注 TRANSACTIONS 段的 ROLLING BACK / history list length

SELECT COUNT(*) FROM information_schema.INNODB_TRX;
```

## 4. 注意事项

- 不要 KILL `system user`、`event_scheduler`、`Binlog Dump` / `Binlog Dump GTID` 线程。
- KILL 会触发事务回滚，大事务回滚可能持续数分钟，期间磁盘 IO 与 undo 压力上升。
- 需要 `PROCESS` + `CONNECTION_ADMIN`（旧版本 `SUPER`）权限。
- 杀掉业务连接会导致对应请求报错，建议低峰期或停应用后操作。
- 重启 mysqld 会回滚所有事务，但属最后手段，且不解决根因。

## 5. 参数与根因排查

```sql
-- 行锁等待（默认 50s）
SHOW VARIABLES LIKE 'innodb_lock_wait_timeout';
-- DDL 的元数据锁等待（默认极大，DDL 卡住的常见原因）
SHOW VARIABLES LIKE 'lock_wait_timeout';

-- 会话级临时调小，避免启动被长时间挂住（仅当前连接生效）
SET SESSION lock_wait_timeout = 10;
```

根因清单：

1. **应用连接池泄漏**：连接借出后未归还（异常路径漏 `close`），事务一直不提交。
   本项目使用 `dbutils.PooledDB`，`reset=True`（默认）会在归还时回滚，前提是连接确实归还。
2. **autocommit 关闭后遗忘**：`SET autocommit=0` 后没有 `commit/rollback`。
3. **手工表锁**：`LOCK TABLES ... WRITE` 之后没有 `UNLOCK TABLES`。
4. **多实例同时启动**：多个 pod 同时执行 `init_tables` 的建表/ALTER，互相等待 MDL。
   建议滚动启动，先让一个实例完成迁移。
5. **库已存在仍执行建库 DDL**：已在 `easy_agent/db/database.py` 修复（先查
   `information_schema.SCHEMATA`，存在则跳过 `CREATE DATABASE`），见提交
   `fix(db): MySQL 启动跳过已存在库的建库 DDL，避免 1205 锁等待超时`。

## 6. 命令行快速执行

```bash
# 列出长事务
mysql -h <host> -u <user> -p -e "
SELECT trx_id, trx_state, TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS seconds,
       trx_mysql_thread_id, trx_query
FROM information_schema.INNODB_TRX ORDER BY seconds DESC;"

# 生成批量 KILL（确认后执行）
mysql -h <host> -u <user> -p -N -e "
SELECT CONCAT('KILL ', id, ';')
FROM information_schema.PROCESSLIST
WHERE Command NOT IN ('Binlog Dump','Binlog Dump GTID','Daemon')
  AND user NOT IN ('system user','event_scheduler')
  AND id <> CONNECTION_ID() AND time > 300;" | mysql -h <host> -u <user> -p
```
