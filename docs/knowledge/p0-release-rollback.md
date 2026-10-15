# P0 发布、Migration 与回滚

1. 从同一 Git tag 构建 Web 和 Worker；禁止在目标机修改代码。
2. 发布包只含运行代码、前端 `dist`、编号 SQL Migration 和生产配置模板；排除 `data/`、`tests/`、`test-results/`、dev YAML 和本地研报。
3. 密码、Token 和证书由环境变量/行内 Secret 注入。运行 `python scripts/release_preflight.py <staging-package>`。
4. 先执行并验证 MySQL+NAS 备份，再用显式 Migration 命令升级（确认值必须与配置中的数据库名完全一致）：

   ```bash
   python scripts/migrate_knowledge_schema.py \
     --config easy_agent/config/config.prod.yaml \
     --apply --confirm-target "$MYSQL_DATABASE"
   python scripts/migrate_knowledge_schema.py \
     --config easy_agent/config/config.prod.yaml
   ```

   Migration 文件发布后不可修改，checksum 不一致必须停止启动。命令只读取现有配置中的 `database` 段，不要求在数据库升级阶段注入模型或 RAGFlow 密钥。

   只有全新且完全空的数据库可在上述 `--apply` 命令中增加 `--bootstrap-empty-database`；工具发现任何已有表时都会拒绝此选项。生产 Web 和 Worker 只验证已应用的版本与 checksum，不在启动期隐式改表。
5. 先启 Worker，确认心跳；再启 Web，执行健康、权限、上传、下载、检索、问答烟雾测试。

回滚时先停止新流量和 Worker，保留当前现场快照。如新版本未执行不可逆 DDL，直接将 Web/Worker 一起回退到前一 tag 并恢复前一配置；如 DDL 已与旧版不兼容，必须从发布前备份恢复到新库/NAS，验证后切换，不在原库上尝试手工逆向改表。

`0002_p0_production.sql`、`0003_team_space_managers.sql` 与
`0004_session_scope_order.sql` 只新增表/列/索引，不更新或删除历史业务行；
失败后保留旧表数据，排除环境问题后可幂等重试。版本 4 为知识库问答会话范围增加
跨 SQLite/MySQL 一致的显式顺序列，禁止再依赖 SQLite 专属 `rowid`。
