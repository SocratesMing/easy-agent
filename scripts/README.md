# scripts/ 目录

按**业务/功能属性**分组的运维与质量脚本。所有路径以仓库根目录为基准，默认在
仓库根执行：

```bash
python scripts/quality/import_graph.py --check-scc
python scripts/quality/check_double_write.py
python scripts/skills/sync_skills.py --check
```

## 目录结构

| 目录 | 用途 | 入口 |
| --- | --- | --- |
| `quality/` | CI 质量门禁 / 架构检查 / 配置校验 | `check_config_schema.py`、`check_env_usage.py`、`check_multi_replica.py`、`check_double_write.py`、`import_graph.py` |
| `db/` | 数据库迁移与一次性数据修复 | `migrate_db.py`、`migrate_sqlite_to_mysql.py`、`prune_checkpoint_threads.py`（清理官方 checkpoint saver 里存量的每消息 / 定时任务 thread，默认只预览） |
| `deps/` | 离线依赖下载/落盘 | `download_backend_deps.py`、`download_backend_whls.py`、`download_frontend_deps.py`、`place_whls_to_repo.py` |
| `release/` | 发布前检查 / 变更导出 | `release_preflight.py`、`export_changes.py` |
| `docs/` | 文档生成 | `generate_api_docs.py` |
| `skills/` | 技能单一源同步 / 公共技能迁移 | `sync_skills.py`、`migrate_builtin_skills.py`（把 S2 之前复制进用户目录、与公共版本一致的技能副本迁到公共技能清单，默认只预览） |
| `dev/` | 本地开发辅助 | `set-agent-env.sh`、`start.dev.keychain.sh`、`memory_guard.sh` |

> `quality/` 内的白名单 JSON 与检查脚本同目录，脚本用 `Path(__file__).with_name(...)`
> 定位，移动目录时无需改配置路径。

## 约定

1. **按功能归类**：新脚本先判断归属目录，不再堆在 `scripts/` 根。
2. **控制体量**：单个脚本建议 ≤300 行；纯常量/纯算法抽到同目录模块
   （如 `double_write_allowlists.py`、`import_graph_core.py`），CLI 只保留参数与输出。
3. **CLI 入口保持可执行**：从仓库根 `python scripts/<group>/<script>.py ...`；
   内部跨模块引用用 `scripts.<group>.<module>`，脚本顶部把仓库根加入 `sys.path`。
4. **测试与 CI 跟着路径走**：移动脚本时同步更新 `.github/workflows/ci.yml`、
   `tests/` 与活跃文档；历史审计报告保留其当时路径。
5. **不在脚本里放业务逻辑**：可复用业务代码放 `easy_agent/`；脚本只做编排/检查。

## CI 对应命令

```bash
python scripts/quality/import_graph.py --check-scc
python scripts/skills/sync_skills.py --check
python scripts/quality/check_double_write.py
python scripts/quality/check_config_schema.py --check
python scripts/quality/check_env_usage.py --check
python scripts/quality/check_multi_replica.py
python scripts/docs/generate_api_docs.py --check
```
