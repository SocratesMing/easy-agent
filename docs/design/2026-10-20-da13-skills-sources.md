# DA-13 技能：公共技能只读共享、选中即加载、按需暴露工具

- 日期：2026-10-20
- 状态：已评审（2026-10-20，§12 均按推荐方案）；S1（pinned skills）已实现，见 §13；S2（公共技能只读共享与迁移、§6.3 `save_expert` 由技能带出）已实现，见 §14；S3 由规范一致性审计 D9 的 `0cfa312` 完成；S2 之后的变更与复核见 §15
- 范围：技能挂载与技能中心接口、本轮技能的加载方式、`save_expert` 的暴露方式、公共技能文档篇幅；前端技能中心的交互不变
- 相关：审计 DA-13（`docs/audit/2026-10-19-deepagents-best-practices-audit.md` §4）、C3 沙箱只读挂载（DA-07）、DA-03（技能每轮重载已在 P0 完成）
- 版本：deepagents 0.7.28（多源 ≥0.7、skill tools ≥0.7.22、pinned skills ≥0.7.23）

## 1. 结论先行

- **公共技能不再复制到每个用户目录**，而是从仓库的 canonical 目录以只读方式共享。用户「添加 / 停用」只改自己的清单，公共技能升级后所有人立刻用上新版本。
- **输入框选中的技能直接加载**：改用框架的 `pinned_skills`，模型在本轮第一次调用前就能拿到技能说明，不用再自己 `read_file` 一次。现在的 `/目录名` 文本指令改为兼容写法。
- **`save_expert` 改为「读到 expert-creator 技能才出现」**：用框架的 skill tools 替代现在「本轮选中 expert-creator 就重建 agent」的做法，省掉一次 agent 重建和编译图切换。
- **精简超长的 `SKILL.md`**：docx 590 行，超过官方建议的 500 行，细节拆到 `references/`。
- 评审决策见 §12。

## 2. 现状

| 方面 | 现状 | 位置 |
| --- | --- | --- |
| 公共技能 | 仓库 `skills/`（7 个），技能中心「添加」时用 `shutil.copytree` 复制到 `workspace/{user}/skills/` | `api/skill_center.py:483-559` |
| 启用 / 停用 | 在 `skills/` 与 `skills-disabled/` 两个目录之间移动 | `services/skill_library.py:207` |
| 技能源 | 只有 `/user-skills/` 一个；用户一个技能都没有时 `skills=None`，加第一个技能要让 agent 失效重建 | `agent/workspace.py:354` |
| 写保护 | `agent.skill_write`（默认 new-only）：文件工具按技能生成 deny 规则；C3 起 execute 沙箱里已有技能只读挂载 | `agent/workspace.py:237`、`agent/backend.py:37` |
| 本轮技能 | 输入框选中 → 请求的 `skills` 字段 → 在本轮模型输入前面拼 `/目录名` 指令（P0 起走 `turn_context`）；模型要自己 `read_file` 读 `SKILL.md` | `services/skill_library.py:416-458` |
| save_expert | 本轮 `skill_dirs` 含 `expert-creator` 才注入（`creator_requested`），值变化时重建 agent，D2 编译图换一张 | `services/agent_manager.py:276-310`、`tools/registry.py:167` |
| 技能重载 | P0 起每轮输入 `skills_metadata: None`，已完成 | `agent/turn_context.py` |
| 文档篇幅 | docx 590 行、skill-creator 485、pdf 314、xlsx 291、mcp-builder 236、pptx 232、expert-creator 110 | `skills/*/SKILL.md` |

## 3. 问题

1. **副本与公共版本分叉**：公共技能更新后，已经添加过的用户拿到的仍是旧副本，除非手动移除再添加；每个用户一份拷贝，docx/pptx/xlsx 带脚本，占用随用户数线性增长。
2. **公共技能能被改写**：副本在用户可写的目录里，只靠 new-only 的 deny 规则和 C3 的只读挂载拦着，一旦策略配成 `allow`，模型就能改坏它。
3. **选中技能后还要多一次往返**：模型看到 `/docx` 指令后，要先 `read_file` 读 `SKILL.md` 才开始干活；选中已停用的技能时，模型根本读不到（不在挂载目录）。
4. **`save_expert` 的开关会重建 agent**：选中、取消 expert-creator 都会让 agent 失效、换一张编译图，还要和会话缓存里的 `_session_creator` 标记保持一致。
5. **超长 `SKILL.md`**：模型读一次 docx 技能约 590 行，挤占上下文。

## 4. 框架能力（deepagents 0.7.28）

- **多源**：`skills=["/a/", "/b/"]`，同名技能以后面的源为准。
- **只读技能**：把源挂在单独的路由上，再用权限规则禁止写入（文件工具），execute 沙箱里只读挂载（C3 已具备）。
- **重载**：`skills_metadata: None`，已在 P0 使用。
- **pinned skills**：运行输入 `pinned_skills=[技能名]`，下一次模型调用之前把这些技能的 `SKILL.md` 作为 `HumanMessage` 追加进对话（`additional_kwargs.lc_source = "pinned_skill"`），用完即清空。名字按 `skills_metadata` 精确匹配，是 frontmatter 里的 `name`，不是目录名。
- **skill tools**：技能的 frontmatter 写 `metadata.include_tools: save_expert`。工具要么交给 `SkillsMiddleware(tools=...)`，要么作为普通工具交给 agent 并标记 `extras={"defer_loading": True}`。模型 `read_file` 读到这个技能（或技能被 pin）之后工具才出现。出现方式有两种：模型声明了 `mid_conversation_tools` 时，在对话中途插入工具定义（Anthropic、OpenAI Responses）；否则把工具追加进本次请求的工具列表（`_skill_tools.py:_bind_disclosures`）。项目用的 DeepSeek 等 OpenAI 兼容接口走后一种，不依赖供应商的特殊能力。

## 5. 目标与非目标

### 目标

- 公共技能只读共享，按用户启停，升级即时生效；用户可以把公共技能 fork 成自己的版本。
- 选中的技能在本轮直接进入上下文；`/目录名` 手打写法继续可用。
- `save_expert` 不再依赖重建 agent。
- 现有用户的技能清单、分类、启停状态无感迁移。

### 非目标

- 不改技能中心的页面交互（「添加 / 移除 / 启用 / 停用 / 分类」照旧）。
- 不改用户自建技能的写入规则（new-only 等）。
- 不引入远端技能仓库或多租户共享的用户技能。

## 6. 方案

### 6.1 双源：`/skills/builtin/`（只读）+ `/user-skills/`

- `skills=["/skills/builtin/", "/user-skills/"]`：同名时用户技能覆盖公共技能，所以「fork 公共技能」就是复制到用户目录后修改。
- `/skills/builtin/` 路由到 canonical 公共技能目录（`config.tools.skills_dir`），用一个只读、按用户过滤的视图 backend：
  - `ls` / `glob` / `grep` / `read_file` 只看得到该用户「已添加且启用」的公共技能，其余顶层目录一律当作不存在；
  - 所有写操作返回权限错误，权限规则里同时对 `/skills/builtin/**` 声明 deny write；
  - 过滤集合按运行期会话上下文解析：`SessionContext` 增加 `builtin_skills`，D2 共享编译图不变。
- **按用户的启用清单**：新文件 `workspace/{user}/builtin-skills.json`，形如 `{"docx": {"enabled": true, "added_at": "..."}}`，与 `skill-categories.json`、`mcp.json` 同级、同一套读写方式。「添加」写一条，「停用」改 `enabled`，「移除」删掉这条。不再复制，也不再在两个目录之间移动公共技能。
- **execute 沙箱**：bwrap 对每个已启用的公共技能逐个只读挂载到 `/skills/builtin/<name>`，没启用的不挂载；Landlock 降级时把 canonical 目录加入只读目录。C3 的 `readonly_binds` / `readonly_dirs` 已经支持这两种做法。
- **依赖**：技能目录里不带依赖。docx 等脚本依赖运行环境里全局安装的包（例如 docx 技能要求 `npm install -g docx`），技能本身只读共享不影响它们；技能自带的脚本只需要可读、可执行。
- 用户技能源 `/user-skills/` 不变；`skills` 参数改为固定的两个源，不再随「用户有没有技能」变化，加第一个技能也不必让 agent 失效。用户技能目录不存在时建一个空目录：源目录缺失虽然不会中断运行，但框架每轮都会记一条加载错误（实测 `Cannot load skills from ...: path_not_found`）。

### 6.2 选中的技能用 `pinned_skills` 加载

- 编排器把本轮选中的技能目录名映射成技能名（读 frontmatter 的 `name`，`skill_library.skill_metadata` 已有），放进运行输入的 `pinned_skills`。
- 请求体 `skills` 字段选中的技能不再拼 `/目录名` 指令；用户在正文开头手打的 `/目录名` 仍按现在的规则解析，同样转成 pin。
- 选中已停用或不存在的技能：与现在一样只记日志、不 pin。前端输入框只列出已启用的技能。
- 会话 thread（DA-03）下，pin 进来的 `SKILL.md` 会作为一条消息留在历史里（框架行为），后续轮次还能看到；超过阈值后由摘要处理。每消息 thread 下只在本轮可见，与现状一致。

### 6.3 `save_expert` 由 expert-creator 技能带出

> 实施顺序调整（S1 实施时发现）：已添加 expert-creator 的用户手里是复制出来的旧副本，没有 `include_tools`。如果现在就去掉 `creator_requested`，这些用户会失去 `save_expert`。所以本节与 S2 一起实施：公共技能改从 canonical 目录只读读取后，所有人看到的都是带 `include_tools` 的新版本。

- `skills/expert-creator/SKILL.md` 的 frontmatter 增加 `metadata.include_tools: save_expert`。
- `save_expert` 不进 agent 的工具列表，交给同名替换框架默认项的 `SkillsMiddleware(tools=[save_expert])`：模型读到或 pin 了 expert-creator 之后的那次模型调用才绑定它，之前调用会被当作未知工具。
  > 实施调整（S2）：评审稿这里写的是作为普通工具交给 agent 并标记 `extras={"defer_loading": True}`。实测在 OpenAI 兼容接口（DeepSeek 等）下该标记不起作用，工具每次都照常绑定，见 §14.2 第 1 条。
- 删除 `creator_requested` 相关逻辑：`agent_manager._session_creator`、注册表的 `_save_expert_enabled`，以及因此触发的 agent 重建。
- 专家会话（`expert_agent_id` 不为空）保留现在的限制：不提供 `save_expert`，避免领域专家被借来创建或修改别的专家（§12 第 3 条）。

### 6.4 精简 `SKILL.md`

- docx（590 行）、skill-creator（485 行）按官方「渐进式披露」拆分：`SKILL.md` 只保留何时使用、流程总览和关键约束，细节移到 `references/*.md`，由 `SKILL.md` 指路，模型需要时再读。
- 改 canonical `skills/` 后用 `scripts/skills/sync_skills.py` 同步包内副本，CI 的 `--check` 会校验。

## 7. 迁移

- **已复制的公共技能副本**（`workspace/{user}/skills/` 与 `skills-disabled/` 里，目录名和某个公共技能相同）：
  - 内容与 canonical 版本一致（按文件哈希比较）：删掉副本，在 `builtin-skills.json` 里记为已添加，启停状态沿用；
  - 内容被改过：保留为用户技能（同名覆盖公共技能，相当于用户 fork），技能中心照常显示。
- 迁移在用户第一次访问技能中心或第一次建 agent 时惰性执行，并提供一次性脚本 `scripts/skills/migrate_builtin_skills.py`（默认预览，`--apply` 执行）。
- 分类：`skill-categories.json` 按目录名记录归属，迁移不改目录名，分类不受影响。

## 8. 改动清单（实现阶段）

| 文件 | 改动 |
| --- | --- |
| `backends/builtin_skills.py`（新） | 只读、按启用集合过滤的公共技能视图 backend |
| `agent/backend.py` | 挂载 `/skills/builtin/`；沙箱逐个只读挂载已启用的公共技能 |
| `agent/workspace.py`、`agent/graph_cache.py` | 固定两个技能源；`SessionContext.builtin_skills`；deny write 规则 |
| `services/skill_library.py`、`api/skill_center.py` | 公共技能启用清单的读写；添加、停用、移除改清单；列表合并两个来源 |
| `services/streaming/orchestrator.py` | 本轮技能转 `pinned_skills`；不再拼 `/目录名` |
| `skills/expert-creator/SKILL.md`、`tools/registry.py`、`services/agent_manager.py` | `include_tools`、延迟暴露的 `save_expert`，删除 `creator_requested` |
| `skills/docx`、`skills/skill-creator` | 拆分 `SKILL.md` |
| `scripts/skills/migrate_builtin_skills.py`（新） | 存量副本迁移 |

## 9. 测试计划

1. 视图 backend：只列出已启用的公共技能；读未启用的返回不存在；写入一律拒绝。
2. 真实 `create_deep_agent`：两个源同名时用户技能覆盖公共技能；停用后技能列表里消失，下一轮生效（每轮重载已有）。
3. 沙箱（真实 bwrap / Landlock）：execute 能读、能运行已启用公共技能的脚本，不能改写，看不到未启用的。
4. pinned：选中技能后第一次模型调用的输入里已有该技能全文，没有额外的 `read_file`；手打 `/目录名` 同样生效；选中已停用的只记日志。
5. skill tools：不读 expert-creator 时 `save_expert` 不在工具列表里；读或 pin 之后出现并可调用；专家会话里始终没有。
6. 迁移：内容一致的副本被删除并记入清单；改过的副本保留为用户技能；启停状态与分类保持。

## 10. 分阶段

| 阶段 | 内容 |
| --- | --- |
| S1（✅ 2026-10-20） | pinned skills（6.2）：改动集中在编排器和接口层，不动存储 |
| S2（✅ 2026-10-20） | 公共技能只读共享与迁移（6.1、7），以及 `save_expert` 由技能带出（6.3）：动技能中心存储，需要迁移 |
| S3（✅ 2026-10-20，`0cfa312`） | `SKILL.md` 精简（6.4）：只改文档内容，可随时做 |

## 11. 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 视图 backend 漏过滤 | 未启用的公共技能被读到 | 公共技能本来就对所有用户可见（技能中心可浏览），影响有限；仍用测试 1、3 锁定 |
| pin 让历史变长（会话 thread） | 上下文占用增加 | 每个技能只在被选中的那一轮 pin 一次；超过阈值由摘要处理 |
| skill tools 依赖框架 beta 行为 | 升级后 `save_expert` 出现时机变化 | 测试 5 锁定；登记为框架适配点 |
| 迁移误删用户改过的副本 | 用户改动丢失 | 只删哈希完全一致的副本；脚本默认预览 |
| 公共技能升级影响正在进行的会话 | 同一会话前后两轮看到不同版本 | 可以接受；需要冻结版本的用户可以 fork |

## 12. 评审决策（2026-10-20，均按推荐方案）

1. 公共技能仍需「添加」才可用，保留添加 / 停用，交互不变。
2. 启用清单放在用户目录的 `builtin-skills.json`，与分类、`mcp.json` 一致。
3. 专家会话仍不提供 `save_expert`，维持现状。
4. 选中技能改用 `pinned_skills`；会话 thread 下技能全文留在历史里，可以接受。
5. 按 S1 → S2 → S3 实施。

## 13. S1 实施记录（2026-10-20）

| 改动 | 位置 | 回归测试 |
| --- | --- | --- |
| 本轮选中且已启用的技能按 frontmatter `name` 放进运行输入的 `pinned_skills`；请求体 `skills` 字段不再拼 `/目录名` 指令；正文开头手打的 `/目录名` 原样保留，同样会 pin；已停用的只记日志 | `services/skill_library.py::pinned_skill_names`、`api/chat.py`、`api/expert.py`、`services/streaming/orchestrator.py`、`agent/turn_context.py::turn_input` | `tests/api/test_chat.py` 的技能用例（含 `test_stream_disabled_skill_is_not_pinned`） |
| 删除不再使用的 `inject_skill_directives` / `pending_skill_directives` | `services/skill_library.py` | — |
| 真实 `create_deep_agent`：pin 的技能全文在第一次模型调用前进入对话（`lc_source = pinned_skill`），整轮只调用一次模型，没有先 `read_file`；经编排器跑一轮，技能全文既不出现在 SSE 事件里，也不写进助手回复 | — | `tests/services/test_pinned_skills.py` |

验证：项目 `.venv` 全量 1799 passed，1 skipped，1 failed。失败的是 `tests/test_scripts_layout.py::test_scripts_root_has_no_loose_scripts`，原因是 `7073a05`（前端重构）把 `sync-frontend-shared.mjs` 放在了 `scripts/` 根目录，与本次改动无关。ruff、import graph、double-write、multi-replica、API 文档门禁均通过。

## 14. S2 实施记录（2026-10-20）

### 14.1 改动

| 改动 | 位置 | 回归测试 |
| --- | --- | --- |
| 按用户的公共技能清单 `workspace/{user}/builtin-skills.json`：添加 / 启停 / 移除只改清单；目录名必须是单个路径段；canonical 目录里已下线的技能不再列出 | `agent/builtin_skills.py`（新） | `tests/agent/test_builtin_skills.py` |
| 只读视图 `/skills/builtin/`：`ls` / `read_file` / `grep` / `glob` / 下载只看得到已启用的公共技能，其余当作不存在，报错与真不存在的文件一致；写、改、删、上传一律拒绝，报错里是模型看到的完整路径 | `backends/builtin_skills.py`（新）、`agent/backend.py` | 同上 |
| 技能源固定为 `["/skills/builtin/", "/user-skills/"]`，同名时用户技能覆盖；`/skills/builtin/**` deny write；`SessionContext` 增加 `builtin_skills_dir` / `builtin_skills`，`ContextBackend` 的缓存键随之变化；子代理声明与主代理相同的技能源 | `agent/workspace.py`、`agent/graph_cache.py`、`backends/context.py`、`agent/core.py`、`agent/subagents.py` | `tests/agent/test_builtin_skills_agent.py`、`tests/agent/test_subagent_specs.py` |
| execute 沙箱：bwrap 逐个只读挂载已启用的公共技能；Landlock 把整个 canonical 目录设为只读。命令里的 `/skills/builtin/` 经新增的 `readonly_mappings` 翻译成真实路径（不进可写挂载与可写目录），输出再翻译回来 | `agent/backend.py`、`backends/shell.py` | `tests/integration/test_sandbox_builtin_skills.py`（真实 bwrap / Landlock） |
| 技能库与技能中心：列表合并用户技能与已添加的公共技能（行带 `source`），同名时用户技能优先；「添加」只记清单（409 文案不变，前端照旧识别「已添加」）；公共技能的启停、移除只改清单，canonical 目录不动；下载支持公共技能 | `services/skill_library.py`、`api/skill_center.py` | `tests/api/test_skill_center.py`、`tests/api/test_skill_categories.py` |
| 旧副本迁移（§7）：第一次访问技能库或建 agent 时执行一次；与 canonical 内容完全一致（忽略隐藏文件与 `__pycache__`）的副本删除并记入清单，沿用启停状态；改过的保留为用户技能；分类按目录名记录，不受影响 | `agent/builtin_skills.py`、`scripts/skills/migrate_builtin_skills.py`（新，默认预览） | `tests/agent/test_builtin_skills.py`、`tests/api/test_skill_center.py::test_first_listing_migrates_copies_made_before_s2` |
| `save_expert` 由 expert-creator 带出（§6.3）：注册表 `ToolSpec.skill_gated`；建图时把这类工具从 agent 工具列表拿出来，交给同名替换默认项的 `SkillsMiddleware(tools=...)`；删除 `creator_requested` / `_session_creator` 及随之的 agent 重建，`get_or_create_agent_for_session` 去掉 `skill_dirs` 参数；专家会话不注入 | `tools/registry.py`、`agent/core.py`、`services/agent_manager.py`、`api/chat.py`、`api/expert.py`、`skills/expert-creator/SKILL.md` | `tests/agent/test_builtin_skills_agent.py`、`tests/test_expert_writer.py`、`tests/services/test_agent_manager_tools.py`、`tests/agent/test_tool_registry.py` |
| 技能中心接口统一校验目录名：`remove-skill`、`enable-skill`、`disable-skill`、`add-skill`、`assign-category`、`skill-detail`、`download-skill` 拒绝不是单个路径段的名字（400） | `api/skill_center.py`、`services/skill_library.py` | `tests/api/test_skill_center.py::test_skill_endpoints_reject_path_traversal` |

### 14.2 实施中的发现与调整

1. **`defer_loading` 换成 `SkillsMiddleware(tools=...)`**：在 OpenAI 兼容接口（DeepSeek 等）下，`extras={"defer_loading": True}` 不起作用，工具每次都照常绑定，模型不读技能也能直接调用。改为交给 `SkillsMiddleware(tools=...)`：这些工具不注册到工具节点，读到技能之前调用会被当作未知工具（`status=error`，不执行）；读到或 pin 了之后的那次模型调用才绑定；开着审批时照常先审批。已登记为框架适配点（`docs/framework-integration.md` 第 6 项）。
2. **既有的路径穿越（改造前就有）**：`remove-skill` 等接口直接拼「用户技能目录 / dir_name」。用户技能目录存在时（真实环境里总是存在），`dir_name="../../别的用户/skills/x"` 能删掉别的用户的技能；在去掉校验的代码上复现过：返回 200，目标目录被删。S2 的清单名字还会拼进沙箱只读挂载的源路径，所以一并修复：接口层统一返回 400，技能库与清单层把这类名字当作不存在。
3. **清单路径跟随调用方的配置**：清单函数默认按运行期配置解析用户目录。`EasyAgent` 与迁移脚本持有自己的配置，起初没有传入，测试里清单落到了仓库的 `./workspace/`（运行期配置未初始化时的回退目录；只写了空清单，没有删除任何副本，已清理）。现在各函数都接受 `config`，`EasyAgent` 与脚本显式传入，有回归测试。
4. **Landlock 下没添加的公共技能可读**：Landlock 不能按子目录放行，只能把整个 canonical 目录设为只读，所以 execute 能读到用户没添加的公共技能；文件工具和 bwrap 下都看不到。公共技能本来在技能中心对所有用户可见，按 §11 第 1 条可以接受，测试里写明了这一点。
5. **专家会话**：改造前在专家会话里选中 expert-creator 也会注入 `save_expert`；现在专家会话一律不提供（§12 第 3 条），读了技能也带不出来。
6. **任何声明了 `include_tools: save_expert` 的技能都能带出它**，包括用户自建或导入的技能。`save_expert` 只在非专家会话里出现、调用前要审批、只能写本用户的专家，风险与用户直接要求创建专家相同。
7. **fork 的启停要连同名公共技能一起变**：用户技能与已添加的同名公共技能在技能库里只显示一行（用户技能）。如果停用只把 fork 移到停用目录，清单里的公共技能仍启用，agent 反而读到公共版本。现在启停用户技能时，清单里同名的公共技能条目设成同样的状态；移除只删 fork，公共技能留在清单里（`tests/api/test_skill_center.py::test_user_fork_shadows_the_builtin_skill`）。

### 14.3 迁移

- 惰性迁移：第一次访问技能库（技能中心、聊天解析本轮技能）或第一次建 agent 时，对该用户执行一次，结果记在清单的 `migrated` 字段。
- 一次性脚本：`python scripts/skills/migrate_builtin_skills.py`（默认预览，`--apply` 执行，`--user` 只处理指定用户；与 `easy-web` 一样先切到项目根，相对路径按项目根解析）。本地开发配置（工作区 `/qts/workspace`）的预览结果：7 个用户共 8 个副本与公共版本一致、可删除，没有改过的副本；尚未执行 `--apply`。

### 14.4 验证

- 项目 `.venv` 全量：1845 passed，1 skipped，1 failed。失败的仍是 `tests/test_scripts_layout.py::test_scripts_root_has_no_loose_scripts`（`7073a05` 放在 `scripts/` 根目录的 `sync-frontend-shared.mjs`，与本次改动无关）。真实模型集成用例 `tests/integration/test_v3_streaming.py::test_tool_calls` 未参与：它经 opencode 中转调用真实模型，此前返回 400（thinking 模式要求回传 `reasoning_content`），与本次改动无关。
- 真实沙箱：本机 bwrap、Landlock 均可用，`tests/integration/test_sandbox_builtin_skills.py` 两种模式都实际执行。
- 门禁：ruff、import graph（0 SCC）、`sync_skills --check`、double-write、multi-replica、API 文档、config schema、env usage 均通过。

## 15. S2 之后的变更与复核（2026-10-20）

S2 提交（`c24d5ba`）之后，另一路开发按规范一致性审计（`docs/design/2026-10-20-deepagents-conformance-audit.md`）继续改了技能相关代码。复核结论详见该文档 §5.2，与本方案有关的部分如下。

| 提交 | 内容 | 复核 |
| --- | --- | --- |
| `0cfa312`（审计 D9） | 即本方案 S3：docx 的 `SKILL.md` 从 590 行减到 175 行，版式细节移到 `references/docx-js-layout.md`，OOXML 移到 `references/ooxml-xml.md`；skill-creator 从 485 行减到 208 行，评测移到 `references/evaluating-skills.md`，盲测与描述优化移到 `references/advanced-evaluation.md`；包内副本已同步 | ✅ 引用文件齐全，`sync_skills --check` 一致 |
| `e27dae0`（审计 D2） | 门控集合从「注册表 `skill_gated`」扩展为「注册表 ∪ 所有技能 frontmatter 声明的 `include_tools`」；子代理用到的门控工具各挂一份 `SkillsMiddleware(tools=...)`；门控集合进编译图指纹 | ❌ 两个问题：没配公共技能目录时建图 `TypeError`（`builtin_skills_dir` 为 `None` 未过滤，所有对话失败）；扫描范围包含用户没添加的公共技能，它们声明的工具会被藏起来且读不到 |
| `e16f9e1`（审计 D4） | MCP 工具名 `{server}__{tool}`（开关 `EASY_MCP_TOOL_PREFIX`，默认关）；门控按「后缀唯一」解析技能里写的原名 | ❌ 开关打开后，交给 `SkillsMiddleware` 的工具按真实名精确匹配，技能里写的原名匹配不到：工具被摘掉、读了技能也带不出 |

三处问题现有测试都没覆盖到：`tests/agent/test_builtin_skills_agent.py` 只建了带公共技能目录的场景，门控也只测了注册表里的 `save_expert`。

修复（2026-10-20 晚）：

- 建图 `TypeError`：`declared_include_tools` 跳过未配置的目录。回归测试 `test_builtin_skills_agent.py::test_agent_builds_without_a_public_skills_dir`：`skills_dir` 为空或路径不存在时，技能源只剩 `/user-skills/`，能正常跑完一轮。
- 前缀下带不出工具：新增 `agent/skills.py::skill_tool_resolver`，主代理和子代理的 `SkillsMiddleware(tools=...)` 改传 resolver，技能里写的名字按「精确 → 后缀唯一」找到实际工具。不开前缀时行为不变：`save_expert` 走精确匹配。回归测试 `test_builtin_skills_agent.py::test_skill_brings_out_a_prefixed_mcp_tool_by_its_original_name`。
- 仍未处理：门控扫描范围包含用户没添加的公共技能。目前仓库里声明 `include_tools` 的公共技能只有 expert-creator，它带出的 `save_expert` 本来就在注册表里门控，所以暂无实际影响。
