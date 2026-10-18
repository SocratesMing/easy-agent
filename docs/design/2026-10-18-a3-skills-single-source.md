# A3 · 技能单一安装源（canonical → packaged）

| 项 | 内容 |
| --- | --- |
| 状态 | 已落地（2026-10-18，Increment 52） |
| 范围 | 后端/工程化：同步脚本、包内副本、技能测试、CI 一步、文档 |
| 关联 | 审计 ARCH-06；整改计划 A3；`docs/architecture-current.md` §3 技能 |
| 非目标 | 不改前端、不改运行时装配逻辑、不删 `.agents/skills`、不 commit |

## 1. 问题与目标

改造前 `scripts/skills/sync_skills.py`：

- 只覆盖 `docx` / `pptx` / `xlsx` 三个格式，`pdf`、`mcp-builder`、`skill-creator`、
  `expert-creator` 四个技能完全不在同步范围；
- 没有显式 `--write`（无参数即写），也没有删除包内多余文件的能力，改名/删除
  会在副本里留下孤儿文件；
- office 12 个共享脚本没有跨格式一致性校验，只保证“每棵树内部”一致；
- 实测已出现漂移：`easy_agent/skills/skill-creator/SKILL.md` 比根 canonical 多
  一段项目约定（commit `3cde5f0` 只改了副本），说明“两棵树靠人记”不可持续。

目标：**根 `skills/` 是唯一可编辑源，包内树完全由脚本单向生成，CI 用全量
sha256 拦住任何漂移**；不改变运行时当前读取的技能数量与内容。

## 2. 职责边界

| 路径 | 角色 | 可写方 | 是否受管 |
| --- | --- | --- | --- |
| `skills/` | canonical（唯一可编辑源） | 人 | 是（7 个含 `SKILL.md` 的目录） |
| `easy_agent/skills/` | 包内生成副本（pip 安装回退） | 仅 `sync_skills.py --write` | 是 |
| `.agents/skills/` | 开发期 agent 技能（26 个目录 / 32 个文件） | 各开发工具 | 否 |
| `skills/CANONICAL.md`、`easy_agent/skills/CANONICAL.md` | 策略说明（两棵树文案不同） | 人 | 否 |

运行时装配只认 `config.tools.skills_dir`（仓库默认 `./skills`），包内树仅在
“没有仓库根树”的安装场景回退。`.agents/skills` 是给编码 agent 用的开发技能，
不参与运行时装配，A3 不删除、不同步、不 hash。

## 3. 同步契约

- **方向**：canonical → packaged，单向。脚本从不读取包内内容回写 canonical，
  也不存在 `--reverse` 之类的入口。
- **受管集合**：canonical 下每个含 `SKILL.md` 的顶层目录（当前 7 个：
  `docx`、`expert-creator`、`mcp-builder`、`pdf`、`pptx`、`skill-creator`、`xlsx`）。
- **覆盖**：受管目录内全部文件（`SKILL.md`、`LICENSE.txt`、脚本、XSD schema、
  XML 模板、eval assets），当前 **215 个文件**；office 三格式共享的 12 个 Python
  脚本全量在列，不做抽样。
- **忽略**：`__pycache__/`、`*.pyc`、`*.pyo`、`.DS_Store`、`Thumbs.db` 以及
  `.pytest_cache` / `.mypy_cache` / `.ruff_cache` / `.cache`（与 `MANIFEST.in`
  的 `global-exclude __pycache__ *.py[cod]` 对齐）。
- **`--write` 行为**：
  1. 复制缺失或内容不同的文件（内容相同不重写，保留 mtime）；
  2. 删除包内受管区中 canonical 不存在的文件；
  3. 删除包内独有的整技能目录（含 `SKILL.md` 的顶层目录）；
  4. 清理受管区 `__pycache__` / `*.pyc` / `*.pyo`，删除空目录；
  5. 不触碰 `easy_agent/skills/CANONICAL.md` 等非受管顶层文件。
- **安全保护**：canonical 不存在、或没有任何含 `SKILL.md` 的技能目录时 exit 2，
  且不写入（避免空源清空副本）；canonical 与 packaged 相同或互相嵌套时 exit 2。
- **默认动作**：`--check`（只读）。写操作必须显式 `--write`，避免误覆盖。

| 命令 | 作用 | 退出码 |
| --- | --- | --- |
| `sync_skills.py --check` | 默认；校验 mirror + office 一致性 | 0 一致 / 1 漂移 / 2 源错误 |
| `sync_skills.py --check --json` | 机器可读差异（CI/测试） | 同上 |
| `sync_skills.py --write` | canonical → packaged 单向生成并清理 | 0 一致 / 1 canonical 自身不一致 / 2 源错误 |
| `sync_skills.py --manifest` | 输出确定性 sha256 清单（JSON） | 0 / 2 |
| `--canonical PATH --packaged PATH` | 覆盖两侧根目录（测试专用） | — |

## 4. 哈希清单

`--manifest` 输出结构（无时间戳，跨平台稳定）：

```json
{
  "version": 1,
  "skills": ["docx", "expert-creator", "mcp-builder", "pdf", "pptx", "skill-creator", "xlsx"],
  "count": 215,
  "digest": "<sha256 of all entries>",
  "files": {
    "docx/SKILL.md": "<sha256>",
    "docx/scripts/office/validators/base.py": "<sha256>"
  }
}
```

- 每个条目是“相对技能根的 POSIX 路径 → 文件内容 sha256”。
- `digest = sha256(concat(sorted(path + "\0" + file_sha256 + "\n")))`，与文件系统
  枚举顺序、绝对路径、mtime 无关；两侧 digest 相同即整棵树逐字节相同。
- **不提交 manifest 文件**：避免出现第三份“真相”及每次技能改动都要重新生成的
  噪声。`--check` 现场重算（215 个小文件，毫秒级）；需要留档时把 `--manifest`
  输出作为 CI artifact 即可。

## 5. 校验门禁

`--check` 由两个门组成：

1. **mirror 门**：canonical 与 packaged 的文件集合与每个 sha256 全等；
2. **office 门**：`docx` / `pptx` / `xlsx` 的 12 个共享脚本逐字节一致；三格式
   目录必须同时存在，缺一即失败。

office 12 文件（相对 `<format>/scripts/office/`）：

```text
helpers/__init__.py          helpers/merge_runs.py      helpers/simplify_redlines.py
pack.py                      soffice.py                 unpack.py
validate.py                  validators/__init__.py     validators/base.py
validators/docx.py           validators/pptx.py         validators/redlining.py
```

测试矩阵：

| 文件 | 覆盖 |
| --- | --- |
| `tests/skills/test_skills_single_source.py` | 7 技能集合冻结；215 文件全量 hash 相等；`--check` 通过/漂移失败；单文件改动后 `--write` 修复；office 共享脚本漂移不被 `--write` 掩盖；清理包内孤儿；canonical 为空拒绝写入；manifest 确定性与可复算；忽略 `__pycache__`/`.DS_Store` |
| `tests/skills/test_office_canonical.py` | 三格式整树与 canonical 一致；office Python 脚本集合冻结为 12 个；12 个脚本跨格式全量 hash 一致 |

CI（`.github/workflows/ci.yml` 的 `architecture-gate`）新增一步，只调用标准库：

```yaml
- name: Skills single source (canonical → packaged)
  run: python scripts/skills/sync_skills.py --check
```

## 6. 落地记录

- 同步前 `--check`：`drift: 1` → `skill-creator/SKILL.md`（包内多 20 行项目约定）。
- `--write`：`1 updated, 0 stale files pruned, 0 stale skills removed, 16 __pycache__ dirs removed`。
- 同步后 `--check`：`7 skills, 215 files, digest 8f14dbef8394dd02…`。
- 运行时发现不变：`find_skills_root()` → 仓库根 `skills/`；`discover_skills()` 7 个，
  名称与顺序不变（`docx, expert-creator, mcp-builder, pdf, pptx, skill-creator, xlsx`）。
- 根 `skills/` 的 7 个技能目录未被修改（唯一变动是 `skills/CANONICAL.md` 说明文件）。

## 7. 未处理项与后续

1. **`skill-creator/SKILL.md` 的项目约定段落**：该段只存在于包内副本，按单向规则
   已被 canonical 覆盖（包内少了 20 行）。若要保留，应先把该段补进根 canonical
   （会改变运行时提示词），属于产品/提示词决策，未在本任务做。
2. **wheel 是否真正携带包内副本**：`pyproject.toml` 的 `package-data` 未显式列出
   `easy_agent/skills/**`，该目录也没有 `__init__.py`；未在本任务验证/修改打包
   配置，建议后续用 `uv build` 检查 wheel 内容后单独补。
3. 两个 `CANONICAL.md` 是策略文档而非技能载荷，故意不纳入 hash（两棵树文案不同）。
4. `.agents/skills` 按约定保留原状，不删除、不同步。
5. 本地 `easy_agent/config/config.yaml` 的 `skills_dir` 被部署环境覆盖为
   `/qts/skills`；仓库默认模板仍是 `./skills`，A3 不改变该部署配置。

## 8. 回滚

包内副本是纯生成物：`git checkout -- easy_agent/skills` 即可恢复；脚本、测试、
CI 步骤与文档可按文件回退，无数据迁移、无运行时状态变更。
