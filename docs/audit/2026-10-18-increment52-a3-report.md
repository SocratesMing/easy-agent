# Increment 52 · A3 技能单一安装源报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 结论 | 完成：根 `skills/` 为唯一可编辑源，包内副本由脚本单向生成，`--check` 全量 sha256 + office 12 文件校验，CI 已接入一步 |
| 范围 | 后端/工程化；未改前端、未改运行时装配逻辑、未 commit |
| 依据 | `docs/design/2026-10-18-a3-skills-single-source.md`；审计 ARCH-06；整改计划 A3 |

## 1. 结论

- `scripts/sync_skills.py` 从“仅 3 格式对账”升级为“7 技能全量单向生成 + 确定性
  校验”：新增显式 `--write`、`--manifest`，默认动作改为只读 `--check`。
- 覆盖全部 7 个运行时技能、**215 个受管文件**（另有两份非受管 `CANONICAL.md`），
  office 三格式共享的 **12 个脚本全量 hash**，不再只查 `base.py`。
- 发现并修复了 1 处真实漂移：`easy_agent/skills/skill-creator/SKILL.md` 比
  canonical 多出 20 行（历史提交只改了副本）。按单向规则以 canonical 覆盖副本，
  根树未动，运行时读取的 7 个技能内容不变。
- 新增 `test_skills_single_source.py` 10 项；`test_office_canonical.py` 扩展 2 项
  （12 脚本集合冻结 + 跨格式全量 hash）；`tests/skills` 19 项 + 框架契约 9 项，
  共 28 项通过。

## 2. 交付物

| 文件 | 变更 |
| --- | --- |
| `scripts/sync_skills.py` | 重写：全量 manifest、`--check`/`--write`/`--manifest`、office 门、孤儿清理、安全保护、`--json` |
| `tests/skills/test_skills_single_source.py` | 新增 10 项：全量镜像、单文件漂移/修复、单向清理、空源保护、manifest 确定性、office 漂移不被掩盖 |
| `tests/skills/test_office_canonical.py` | 保留整树镜像；新增 12 脚本集合冻结 + 跨格式全量 hash |
| `easy_agent/skills/**` | 从 canonical 重新生成（1 个文件更新，16 个 `__pycache__` 清理） |
| `skills/CANONICAL.md`、`easy_agent/skills/CANONICAL.md` | 写明单向规则、命令、CI 校验、`.agents/skills` 边界 |
| `.github/workflows/ci.yml` | 在 `architecture-gate` 新增 1 步 `sync_skills.py --check` |
| `docs/design/2026-10-18-a3-skills-single-source.md` | 设计文档 |
| 本文件 | 审计报告 |

## 3. 同步方向与哈希覆盖

- **方向**：`skills/`（canonical）→ `easy_agent/skills/`（生成副本），单向。
  脚本没有反向入口；`--write` 只读 canonical、只写 packaged。
- **受管集合**：canonical 下含 `SKILL.md` 的顶层目录，共 7 个：
  `docx`、`expert-creator`、`mcp-builder`、`pdf`、`pptx`、`skill-creator`、`xlsx`。
- **哈希覆盖**：每个受管目录下全部文件，共 215 个；其中 office 三格式各 12 个
  Python 脚本（`helpers/*`、`pack.py`、`soffice.py`、`unpack.py`、`validate.py`、
  `validators/*`）全部纳入，同时校验三格式之间逐字节一致。
- **忽略项**：`__pycache__/`、`*.pyc`/`*.pyo`、`.DS_Store`、`Thumbs.db` 与常见
  缓存目录（与 `MANIFEST.in` 的 global-exclude 对齐）。
- **清单 digest**：`sha256(concat(sorted(path + "\0" + file_sha256 + "\n")))`，
  与绝对路径、mtime、枚举顺序无关。

## 4. 执行记录

1. 同步前 `python3 scripts/sync_skills.py --check` → exit 1：

   ```text
   drift（内容不同）: 1
     - skill-creator/SKILL.md
   ```

2. `python3 scripts/sync_skills.py --write`：

   ```text
   canonical: 7 skills / 215 files / digest 8f14dbef8394dd02…
   written: 1 updated, 0 stale files pruned, 0 stale skills removed, 16 __pycache__ dirs removed
   ```

3. 同步后 `--check` → exit 0：

   ```text
   技能副本一致：7 skills, 215 files, digest 8f14dbef8394dd02…
   ```

4. `--manifest`：`count=215`，`digest=8f14dbef8394dd02334259d1a8a8719bd9e5dc626b641d10469727f8085bfd15`。

5. 运行时发现不变：

   ```text
   root: /home/sututu/code/easy-agent/skills
   count: 7
   names: ['docx', 'expert-creator', 'mcp-builder', 'pdf', 'pptx', 'skill-creator', 'xlsx']
   ```

6. 根 `skills/` 的 7 个技能目录未改：`git diff --name-only -- skills` 仅
   `skills/CANONICAL.md`（策略说明文件）。

## 5. 测试结果

| 命令 | 结果 |
| --- | --- |
| `uv run pytest tests/skills tests/test_framework_contract.py -q` | **28 passed**, 1 warning（Starlette/httpx 弃用告警），2.96s |
| `uvx ruff check .` | **All checks passed!** |
| `python3 scripts/import_graph.py --check-scc` | **OK**：212 modules / 479 edges / 1 SCC（16 modules），与白名单完全一致 |
| `python3 scripts/sync_skills.py --check` | exit 0，7 skills / 215 files |

新增测试要点：

- 修改 canonical 单个文件（`pdf/SKILL.md`）→ `--check` exit 1 且指出路径；
  `--write` 后 exit 0，副本内容与 canonical 一致，canonical 保留改动（证明单向）。
- 修改 docx 的共享 office 脚本 → `--check` 报 office 不一致；`--write` 仍 exit 1，
  不会用“复制”掩盖三格式漂移。
- `--write` 会删除包内 `STALE.md` 与包内独有的 `orphan-skill/`，不会反向写入
  canonical。
- canonical 为空时 `--write` exit 2，包内哨兵文件保持不变。
- `--manifest` 两次输出逐字节相同，且每个 hash 可由文件内容复算；
  `__pycache__`/`.DS_Store` 不影响清单。

## 6. 未处理项与风险

1. **`skill-creator/SKILL.md` 内容归属**：包内原有的一段“本环境约定”只存在于
   副本（commit `3cde5f0` 未同步到根）。按 A3 单向规则，已由 canonical 覆盖，
   包内副本少 20 行；若需保留，应先把该段补进根 canonical（会改变运行时技能
   提示词，属产品决策），本次未做反向同步。
2. **wheel 打包未验证**：`pyproject.toml` 的 `package-data` 未显式包含
   `easy_agent/skills/**`，且 `easy_agent/skills` 无 `__init__.py`；包内副本是否
   真正随 wheel 发布未在本任务验证，也未修改打包配置（超出允许改动范围）。
3. `CANONICAL.md` 是策略文档，两棵树文案不同，故意不纳入 hash。
4. `.agents/skills/`（26 个开发技能目录 / 32 个文件）按约定保留原状，不删除、
   不同步、不 hash。
5. 本地 `easy_agent/config/config.yaml` 的 `skills_dir` 被部署环境覆盖为
   `/qts/skills`；仓库默认模板仍为 `./skills`，不属于本任务。
6. 工作区中 `easy_agent/config.py`、`easy_agent/config/config-example.yaml`、
   `easy_agent/services/streaming/*` 存在并行任务的改动，本任务未触碰；上述测试
   结果是在这些改动共存时取得的。

## 7. 变更与回滚

- 本任务未执行 `git commit`，未改前端、未改运行时逻辑、未改 `conftest` 或其他测试。
- CI 仅新增 1 个技能校验步骤（`architecture-gate` 内，标准库调用，无需额外依赖）。
- 回滚：`git checkout -- easy_agent/skills` 恢复生成副本；脚本/测试/文档/CI 按文件
  回退即可，无数据迁移或运行时状态变化。
