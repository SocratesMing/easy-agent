# EasyAgent 本地 MySQL

这份 Compose 仅用于行外开发联调：MySQL 只监听 `127.0.0.1:3307`，数据写入 Docker named volume `easyagent_mysql_data`，执行普通 `down` 不会删除数据。

## 1. 启动

```bash
docker-compose -f deploy/mysql-local/docker-compose.yml up -d
docker-compose -f deploy/mysql-local/docker-compose.yml ps
```

默认联调参数如下，均可通过同名环境变量覆盖：

| 参数 | 默认值 | 环境变量 |
|---|---|---|
| 地址 | `127.0.0.1` | 不可修改（强制本机绑定） |
| 宿主机端口 | `3307` | `EASYAGENT_MYSQL_PORT` |
| 库名 | `agent` | `EASYAGENT_MYSQL_DATABASE` |
| 用户 | `easyagent` | `EASYAGENT_MYSQL_USER` |
| 密码 | `easyagent_dev_only` | `EASYAGENT_MYSQL_PASSWORD` |
| root 密码 | `easyagent_root_dev_only` | `EASYAGENT_MYSQL_ROOT_PASSWORD` |

> 默认密码只为本机开发准备，不得用于行内或共享环境。行内密码应仅由环境变量/密钥管理系统注入。

EasyAgent 本地初始管理员为 `admin/admin`，人员管理新建账号的初始密码为
`123456`。这两组凭据同样只用于单机联调；共享测试或行内部署前必须接入正式
认证/密码策略并替换默认凭据。

MySQL 官方镜像只在 named volume 第一次初始化时使用上述库名、用户和密码；已有数据卷不会因后续更改环境变量而重置凭据。

等待 `STATUS` 变为 `healthy` 后再进行迁移。可执行以下命令确认服务可用：

```bash
docker-compose -f deploy/mysql-local/docker-compose.yml exec mysql \
  sh -lc 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -u"$MYSQL_USER" "$MYSQL_DATABASE" -e "SELECT VERSION();"'
```

## 2. 迁移现有 SQLite 数据

迁移前请先停止 EasyAgent，避免迁移期间 SQLite 继续产生新数据。迁移脚本默认只做 dry-run，不会写入 MySQL：

```bash
.venv/bin/python scripts/migrate_sqlite_to_mysql.py \
  --sqlite ./data/easy_agent.db \
  --host 127.0.0.1 --port 3307 \
  --user easyagent --database agent \
  --dry-run
```

若已通过 `EASYAGENT_MYSQL_PASSWORD` 覆盖默认密码，迁移脚本会自动读取它。确认 dry-run 中的源文件、目标库和行数后，显式执行：

```bash
.venv/bin/python scripts/migrate_sqlite_to_mysql.py \
  --sqlite ./data/easy_agent.db \
  --host 127.0.0.1 --port 3307 \
  --user easyagent --database agent \
  --apply --confirm-database agent
```

也可用 `--config easy_agent/config/config.dev.yaml` 读取该 YAML 中的 `database.mysql` 配置；命令行参数的优先级更高。不建议在共享终端中使用 `--password`，应使用环境变量或 YAML 占位符。

迁移的安全性约束：

- SQLite 以只读模式打开，原文件会保留，脚本不删除任何数据。
- 只有目标库完全没有业务表时，才调用 EasyAgent `Database.init_tables()` 初始化表结构。
- 目标库一旦已有任何业务表，迁移脚本绝不调用 `Database.init_tables()`，避免触发应用启动时的数据修复逻辑；此时要求全部源表已在目标库存在，否则立即停止。
- 已存在的主键行只跳过，不更新；若其他唯一键冲突但主键不同，整次数据事务会回滚并报错。
- 历史 SQLite 中未填写的 `users.employee_id` 空字符串会在写入 MySQL 时转为 `NULL`，以兼容工号唯一索引；非空工号保持原值。
- 按 MySQL 外键依赖顺序写入，事务提交前复核所有外键。
- 可重复执行；第二次及以后会跳过相同主键，不覆盖 MySQL 中的现有值。
- 数据库仅保存文件路径和元数据；`data/`、`workspace/` 与原文 NAS 文件本体不会被复制。

仓库内的 `easy_agent/config/config.dev.yaml` 已指向上述本地 MySQL，迁移成功后可直接重启 EasyAgent。若启动 Compose 时覆盖了端口、库名、账号或密码，请在启动 EasyAgent 前设置同名 `EASYAGENT_MYSQL_*` 环境变量；配置中不保存行内密码。

## 3. 停止与回退

```bash
docker-compose -f deploy/mysql-local/docker-compose.yml down
```

`down` 会保留 `easyagent_mysql_data`。需要回退时，将 `database.type` 改回 `sqlite` 并重启 EasyAgent 即可，原 SQLite 文件一直保留。请勿在未备份时执行 `docker-compose down -v`，该选项会删除 MySQL named volume。

> 当本机安装的是 Docker Compose v2 插件时，可将上述 `docker-compose` 等价替换为 `docker compose`。
