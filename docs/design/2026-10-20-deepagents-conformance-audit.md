# DeepAgents 规范一致性审计（2026-10-20）

审计对象：`easy-agent` 当前后端（`easy_agent/`）。
官方基准：Deep Agents **0.7.28**（本地已安装版本），文档取自 docs.langchain.com（抓取日期 2026-10-20；页面本身未标注更新时间）。
关联方案：[2026-10-20-da13-skills-sources.md](2026-10-20-da13-skills-sources.md)（技能多来源，S1 已实施，S2/S3 待实施）。

## 1. 结论摘要

1. **主干集成是合规的**：`create_deep_agent` 的 `skills / memory / permissions / backend / interrupt_on / subagents / checkpointer / context_schema` 都用了官方参数（`agent/core.py:562-581`），HITL 用的是官方 `InterruptOnConfig(when=...)`，摘要是官方 `SummarizationMiddleware` 的同名替换，todo 用官方 `TodoListMiddleware`。
2. **最大的结构性偏差在 skills 的「来源」**：只挂 `/user-skills/` 单源，公共技能靠复制进用户目录，副本可写、会分叉；官方推荐多源只读叠加。该问题已有 da13 方案，S2 未实施。
3. **`metadata.include_tools`（技能自带工具）未实现**：`save_expert` 仍靠「选中 expert-creator → 重建 agent」的私有机制（`services/agent_manager.py:51-54`），官方 0.7.22+ 的门控能力没用上。
4. **技能 frontmatter 校验只覆盖 name/description 非空**（`agent/skills.py:119-142`），缺官方硬约束：name 必须等于目录名、字符集与长度、description ≤1024、SKILL.md >10MB 跳过——不合规技能会静默不加载。
5. **MCP 工具不区分来源**：包装后保留原名（`services/mcp_evidence.py:407-412`），与内建工具重名直接丢弃、多 server 重名保留第一个（`services/mcp.py:462-470`）。官方未强制前缀，但多 server 下模型无法区分来源、且冲突是静默的。
6. **MCP 会话管理是自研池**：用官方 `client.session()` 公共 API，但长期持有上下文管理器、自管 TTL/重建/关闭与 per-key 锁（`services/mcp_sessions.py:1-20, 226-342, 462-517`）。已知约束（cancel scope 同 task、session 绑定 event loop）都在代码里注明，属可维护性风险而非错误。
7. **子代理使用了非官方字段** `use_tools` / `requires_tools`（`agent/subagents.py:8-9`），靠建图期解析成官方 `tools` 白名单（`agent/core.py:576-579`）；一旦解析遗漏，未知字段会直接传给框架。
8. 其余为「产品取舍」或「已有计划的优化」，见第 5 节，不构成违规。

## 2. 版本与门槛

| 依赖 | 版本 | 官方门槛 |
| --- | --- | --- |
| deepagents | 0.7.28 | `delete` 工具 / 文件工具白名单需 ≥0.7；`include_tools` ≥0.7.22；`pinned_skills` ≥0.7.23 |
| langchain | 1.4.6 | `InterruptOnConfig.when` 需 ≥1.3.3 |
| langgraph | 1.2.16 | HITL 需 checkpointer |
| langchain-mcp-adapters | 0.2.2 | `session()` + `load_mcp_tools(session=...)` |
| mcp | 1.27.2 | — |

## 3. 项目结构与功能地图

```
easy_agent/
├── api/             # chat / sessions / files / expert / skill_center / connectors / settings / terminal / scheduled_tasks
├── services/        # streaming(orchestrator) / agent_manager / mcp(+sessions/evidence/policy) / skill_library
│                    # connectors / hitl_state / session_uploads / scheduler / metrics
├── agent/           # core(建图) / backend(路由) / workspace / skills / subagents / hitl
│                    # summarization / memory / turn_context / graph_cache / middleware / run_config
├── backends/        # ContextBackend（运行期按 SessionContext 路由）+ LocalShellBackend(bwrap/Landlock)
├── tools/           # registry（内建工具登记/校验）+ session_file_reader / scheduled_task / expert_writer / web_search
├── persistence/     # checkpoints（Postgres/SQLite/legacy）+ stores
├── db/ models/ middleware/ initialization/ security/ sandboxing/ personnel/ knowledges/
```

| 官方能力 | 项目落点 |
| --- | --- |
| 建图与参数 | `agent/core.py:562-581`（唯一调用点） |
| 文件系统与执行 | `backends/context.py`、`agent/backend.py:69-220`、`backends/shell.py` |
| 技能 | `agent/workspace.py:354-365`（路径）、`agent/skills.py`（校验）、`services/skill_library.py`（存储） |
| 子代理 | `agent/subagents.py` + `agent/core.py:130-152` |
| HITL | `agent/hitl.py:334-434` + `services/hitl_state.py` + `services/streaming/orchestrator.py:287-292` |
| 上下文 | `agent/summarization.py`、`agent/turn_context.py`、`agent/memory.py` |
| MCP | `services/mcp.py`、`mcp_sessions.py`、`mcp_policy.py`、`mcp_evidence.py` |
| 持久化 | `persistence/checkpoints.py`、`stores.py`、`agent/graph_cache.py` |

## 4. 官方规范要点（本次核对）

- **Skills**：`skills` 指向「包含技能目录的源目录」，同名后加载者胜；frontmatter `name` 必须等于目录名、`description` ≤1024；单文件 >10MB 跳过；每线程只加载一次，重载用 `skills_metadata: None`；选中技能用 `pinned_skills`；技能工具用 `metadata.include_tools`（空格分隔字符串）并挂在 `SkillsMiddleware(tools=...)`。
- **Subagents**：`name/description` 必填；isolated 模式必须给 `system_prompt`；`tools` 是**替换**不是追加；`skills`/`middleware` **不继承**（general-purpose 除外）；`permissions` 设置即替换。
- **HITL**：`interrupt_on` → 自动注入 `HumanInTheLoopMiddleware`；`InterruptOnConfig(when=...)` 条件中断；决策 `approve/edit/reject/respond`；恢复用 `Command(resume={"decisions": [...]})`，多中断用 interrupt_id 映射；`FilesystemPermission(mode="interrupt")` 可对路径级写入审批。
- **MCP**：工具来自 `langchain-mcp-adapters`；`MultiServerMCPClient` 支持多 server；连接管理由 FastMCP 负责，`client.session()` 是公开的生命周期入口。
- **后端**：`backend` 是 `create_deep_agent` 的编译期参数，可选 State/Filesystem/Store/Composite/自定义；`permissions` first-match-wins。

## 5. 偏差清单与改造点

| # | 级别 | 问题 | 证据 | 影响 | 改造点 |
| --- | --- | --- | --- | --- | --- |
| D1 | P0 | skills 单源：公共技能未以只读源叠加，靠复制进 `/user-skills/` | `agent/workspace.py:354-365`；`agent/backend.py:136-141`；无 `/skills/builtin/` 路由 | 副本可被模型改写（仅靠 deny 兜底）、公共技能升级不生效、占用随用户数线性增长 | 按 da13 §6.1 增加 `/skills/builtin/` 只读路由 + `skills=["/skills/builtin/","/user-skills/"]`，权限 deny write，`builtin-skills.json` 记录启用清单 |
| D2 | P0 | `metadata.include_tools` 未实现，技能自带工具靠私有开关 | `agent/skills.py:119-142`（只校验 name/description）；`services/agent_manager.py:51-54` | 官方门控能力缺失；选中/取消 expert-creator 会重建 agent、换编译图 | da13 §6.3：`SkillsMiddleware(tools=...)` 或 agent `tools` + `extras={"defer_loading": True}`，删除 `_session_creator` 逻辑 |
| D3 | P1 | frontmatter 校验不全（name≠目录名、字符集、长度、10MB 上限、allowed-tools 均未校验） | `agent/skills.py:119-142` | 不合规技能静默不加载，排障困难 | 按官方限制补齐校验并在技能中心展示错误；CI 加 `skills-ref`/自定义校验 |
| D4 | P1 | MCP 工具无 server 前缀，重名静默丢弃 | `services/mcp_evidence.py:407-412`；`services/mcp.py:462-470` | 多 server 同名工具丢失；模型/审计无法区分来源；HITL 名字启发式受限 | 包装时加 `{server}__{tool}` 前缀并提供原名别名；冲突改为显式告警 + 配置项决定策略 |
| D5 | P1 | 自研 MCP 持久会话池（长期持有 `client.session()` 上下文） | `services/mcp_sessions.py:1-20, 226-342, 462-517` | 与适配器内部时序强耦合，升级 0.3+ 需回归；stdlib 子进程与事件循环绑定易泄漏 | 保留现状但在升级清单中标注；长期方案：每请求 session 或等官方连接池 API |
| D6 | P2 | 子代理 spec 使用非官方键 `use_tools`/`requires_tools` | `agent/subagents.py:8-9`；`agent/core.py:576-579` | 私有键若未被解析会传进框架；可读性差 | 建图期断言剥离未知键；文档标注为「项目扩展」，后续改为官方 `tools` 白名单 |
| D7 | P2 | 未使用路径级 `FilesystemPermission(mode="interrupt")`，敏感路径只做 deny | `agent/workspace.py:138-230` 只有 allow/deny | 保护路径的写入无法走审批，只能直接拒绝 | 视需求对 `/memories/**`、`/user-skills/**` 评估 `mode="interrupt"` |
| D8 | P2 | HITL 决策只有 `approve/reject`，无 `edit` | `tools/registry.py:291-355` 各工具声明 | 用户无法改参数后放行，只能拒绝后重来 | 产品决策项；中风险工具可加 `edit` |
| D9 | P2 | `SKILL.md` 超官方建议（docx 590 行、skill-creator 485 行，建议 <500 行 / <5000 tokens） | `skills/docx/SKILL.md`、`skills/skill-creator/SKILL.md` | 每次读取挤占上下文 | da13 §6.4：拆分到 `references/`，正文只留流程与判据 |
| D10 | P2 | 未使用 `FilesystemPermission` 之外的官方缓存/可观测扩展（`cache=` 为节点级缓存，与图缓存不是同一层） | `agent/core.py` 未传 `cache`；`agent/graph_cache.py` 自研 | 非合规问题，属可选优化 | 评估 LangGraph `cache=` 对模型节点是否适用，避免与自研图缓存混淆 |
| D11 | 观察 | MCP 鉴权完全来自 `mcp.json` 的静态 headers/env（用户级），无 OAuth/per-user token | `services/mcp.py:136-176`；`services/connectors.py:106` | 单机/内网形态可接受；多租户部署需 per-user 凭据 | 多租户前补 per-user 凭据注入与不落盘策略 |

## 5.1 实施状态（2026-10-20 15:2x 复核）

DA-13 S2 已由另一路开发提交：`c24d5ba feat(skills): share public skills read-only and expose save_expert through its skill`（40 文件，+1630/−201，含 2 个新测试文件）。
复核结论：

- **D1 已闭环**：双源顺序（builtin → user，后者覆盖）、只读三层（backend 拒写 / `FilesystemPermission` deny / 沙箱只读挂载）、按用户启用集合过滤、固定源不随技能数变化、旧副本迁移（哈希一致删除、改过的保留为用户技能）均落地；`tests/agent/test_builtin_skills*.py` 覆盖。
- **D2 已闭环**：`c24d5ba` 先跑通 `expert-creator` 的 `metadata.include_tools: save_expert` + `SkillsMiddleware(tools=...)`；`e27dae0` 补齐通用性——门控集合改为「注册表 `skill_gated` ∪ 全部技能 frontmatter 声明的 `include_tools`」（`agent/skills.py:declared_include_tools`，YAML 列表形式按官方语义忽略并告警），子代理用到的门控工具从 `tools` 摘出后各挂一份 `SkillsMiddleware(tools=...)`，并把门控集合纳入编译图指纹。
- **D3 已闭环（`68bfe4e`）**：`validate_skills` 按官方硬约束校验（name 等于目录名 / ≤64 / 小写字母数字连字符、description ≤1024、compatibility ≤500、`include_tools` 必须字符串），发现阶段跳过 >10MB 的 `SKILL.md`。注意：校验目前是**报告制**，不阻断加载；本机体检发现 1 个存量违规：`workspace/szm/skills/strategy_fx`（下划线，建议改成 `strategy-fx` 并同步目录名）。
- **D9 已闭环（`0cfa312`）**：按官方渐进式披露拆分——docx `SKILL.md` 590 → 175 行（版式 → `references/docx-js-layout.md`，OOXML → `references/ooxml-xml.md`），skill-creator 485 → 208 行（评测 → `references/evaluating-skills.md`，盲测/描述优化 → `references/advanced-evaluation.md`）；包内副本由 `sync_skills.py --write` 生成且 `--check` 一致。
- **D4 及以后未动**：MCP 命名与冲突、会话池风险、`mode="interrupt"`、HITL `edit`。

## 6. 已符合官方的部分（避免误改）

- `interrupt_on` 动态按本轮实际注入工具构造，条件谓词走官方 `when`（`agent/hitl.py:334-434`）；未自研 pre-check 中间件。
- 恢复路径用 `Command(resume=...)`，多中断用 interrupt_id 映射（`services/streaming/orchestrator.py:287-292`、`services/hitl_state.py:13-38`）。
- 摘要是官方 `SummarizationMiddleware` 同名替换；todo 是官方 `TodoListMiddleware`（0.7 起 opt-in，已显式挂载）。
- skills 重载用 `skills_metadata: None`、选中技能用 `pinned_skills`（`agent/turn_context.py:55-72`）——均为 0.7.23+ 官方机制。
- 自定义子代理显式补 `skills=["/user-skills/"]`，符合「自定义子代理不继承技能」的规则（`agent/core.py:145-152`）。
- 文件权限用官方 `FilesystemPermission`，未挂载前缀的规则会跳过以避免 `NotImplementedError`（`agent/workspace.py:206-212`）。
- 工具名唯一性在注册表导入期校验，MCP 与内建重名有防护（`tools/registry.py:387-390`、`services/mcp.py:462-467`）。

## 7. 建议实施顺序

1. **P0-a（不改行为）**：补齐 frontmatter 校验（D3）+ 子代理私有键剥离断言（D6）+ 本文档与 `docs/framework-integration.md` 的版本基线。
2. **P0-b（需产品确认）**：da13 S2 双源只读（D1）与 `include_tools`（D2）——两者互相依赖，建议同批实施；涉及迁移 `builtin-skills.json`。
3. **P1**：MCP 前缀与冲突策略（D4，需评估历史会话里已缓存的工具名）；MCP 会话池的升级风险登记（D5）。
4. **P2**：`SKILL.md` 精简（D9，只改文档）、`mode="interrupt"` 评估（D7）、HITL `edit` 决策（D8，产品决策）。

## 8. 验收方式

- skills：新增不合规技能样例（name 不匹配、超长 description、>10MB）→ 校验接口应报错；双源后「停用公共技能」应立即不可见。
- HITL：条件中断用例（`execute` 删除类 vs 目录删除）、多中断 resume 映射。
- MCP：两个 server 暴露同名工具 → 前缀后应同时可见且可区分；连接泄漏用 `lsof`/会话数断言。
- 子代理：未声明 `skills` 的自定义子代理不应看到技能；声明后能看到。
