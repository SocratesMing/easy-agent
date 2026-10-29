# scripts/ 结构重整报告（2026-10-18）

## 目标

按业务/功能属性整合 `scripts/`，拆分超长脚本，保持 CLI 行为与 CI/测试可用。

## 新结构

```
scripts/
  README.md
  __init__.py
  quality/      # 质量/架构门禁
    check_config_schema.py
    check_env_usage.py
    check_multi_replica.py
    check_double_write.py        # CLI 129 行
    double_write_scanner.py      # 扫描/校验 700 行
    double_write_allowlists.py   # 冻结白名单 189 行
    import_graph.py              # CLI 143 行
    import_graph_core.py         # 图/SCC/白名单 372 行
    bare_getenv_whitelist.json
    import_graph_whitelist.json
  db/
    migrate_db.py
    migrate_sqlite_to_mysql.py   # 671 行
    mysql_target.py              # MySQLTarget/参数解析 172 行
    migrate_market_mcp_keys.py
  deps/         # 离线依赖下载/落盘
  release/      # 预检/变更导出
  docs/         # API 文档生成
  skills/       # 技能单一源同步
  dev/          # 本地开发 shell
```

## 拆分明细

| 原脚本 | 前 | 后 |
| --- | --- | --- |
| `check_double_write.py` | 974 行单文件 | CLI 129 + scanner 700 + allowlists 189 |
| `import_graph.py` | 486 行单文件 | CLI 143 + core 372 |
| `migrate_sqlite_to_mysql.py` | 811 行单文件 | 编排 671 + `mysql_target.py` 172 |

CLI 行为不变：`--check-scc`、`--json`、`--root`、`--apply` 等参数保持；扫描函数从
原模块 re-export，测试无需改调用方式。

## 路径更新

- `.github/workflows/ci.yml` 全部改为 `scripts/quality|skills|docs/...`。
- 活跃文档/README/AGENTS/CLAUDE/架构文档/env example 路径同步。
- 测试导入改为 `scripts.quality.*` / `scripts.db.*`；新增 `tests/test_scripts_layout.py`
  守卫“根目录不得再放散脚本”。
- 历史审计报告中的路径做了机械替换，保留当时结论。

## 验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **1474 passed / 1 skipped / 0 failed（273.6s）** |
| 质量门禁 | SCC=0；double-write OK；config/env/api docs/skills checks OK |
| ruff | All checks passed |
| 脚本 CLI | import_graph/check_double_write/check_config_schema/check_env_usage/check_multi_replica/generate_api_docs/sync_skills 均冒烟通过 |

## 未做 / 后续

1. `double_write_scanner.py`（700）与 `migrate_sqlite_to_mysql.py`（671）仍偏长，
   可继续按“SQL 解析 / 表复制 / 校验”拆分。
2. 依赖下载 4 个脚本可进一步合并为一个 `deps.py` 子命令 CLI，但会改变现有命令，
   本轮仅分组、不改 CLI。
3. 历史报告里的旧路径虽已替换，旧链接/外部脚本若仍引用旧路径需迁移。
