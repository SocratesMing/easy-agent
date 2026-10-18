# T4 · easy-agent 代码架构与组织审计报告

- 审计员：T4（代码架构与组织审计员）
- 审计日期：2026-10-17
- 审计对象：`\\wsl.localhost\Ubuntu-24.04\home\sututu\code\easy-agent`（Linux 侧 `/home/sututu/code/easy-agent`）
- 范围：后端分层/依赖/规模/重复/死代码/配置/依赖/API 一致性/测试/工程化/前端粗粒度/可扩展性与多实例可部署性
- 方法：AST 静态分析（自写脚本，无第三方依赖）+ grep 交叉验证 + `pytest --collect-only` + 依赖/配置/打包产物（egg-info SOURCES）实测
- 只读约束：**未修改任何业务代码/配置**；仅写入 `_analysis/` 下的分析脚本与本报告。仅执行了 `pytest --collect-only`（见 §4.9 关于其导入副作用的说明），未执行业务用例。

---

## 1. 结论摘要（12 条）

1. **分层名存实亡**：API 层 8 个路由文件直接 `from ..db import Database, get_database` 并调用 DB；基础层 `config.py` 反向 lazy-import `services.agent_manager`（4 处）；`db/database.py` 反向依赖 `personnel.*`（5 处）；`middleware/auth.py` 依赖 `services`；`knowledges/api.py` 反向 import `api.chat`。实测 177 模块/347 条内部依赖边。
2. **存在巨型循环依赖**：去掉包 `__init__` 聚合边后仍有 **2 个 SCC（39 模块 + 2 模块，41 个模块在环上，占 23%）**，典型闭环 `api.chat → knowledge_impl → knowledges.api → api.chat`、`config → services.agent_manager → agent → config`、`services.agent_manager ↔ tools.expert_writer`、`db.database ↔ knowledges.core.schema`。
3. **上帝类/巨型文件严重**：`db/database.py` 2558 行、单类 `Database` **114 个方法/2458 行**；`knowledges/service.py` 2668 行、`KnowledgeService` **49 个方法/2208 行**；`services/streaming.py` 1709 行、`api/expert.py` 1235 行、`config.py` 1032 行 19 个 Pydantic 类；最长函数 `resume_stream_generator` **489 行**、`chat_stream` 245 行、`_get_or_create_agent_unlocked` 255 行。
4. **装配逻辑自相矛盾**：`app.py:245` 与 `app.py:347` **两次调用 `startup_knowledge`**；`lifespan` 一个函数 182 行内完成配置/日志/知识库/DB/目录/提示词/skills/专家/checkpointer/调度器 12 类装配，且与 `initialization/` 模块职责重叠。
5. **执行路径分裂成 4 套 SSE 生产者**：`services/streaming.py`（chat/resume 两个 400–500 行生成器）、`knowledges/chat/streaming.py`、`api/expert.py`（`_refusal_event_stream`、`stream_export_run`）、`api/chat.py`（`_StreamHub` 广播）；事件契约在 `docs/requirements-agent.md`、`CLAUDE.md`、代码 docstring 三处互不一致。
6. **技能目录三套、office 校验器 6 份完全相同的副本**：根 `skills/`（canonical）与 `easy_agent/skills/`（fallback）各 7 个技能，`docx/pptx/xlsx/scripts/office/` 下 12 个文件在 3 个格式目录 × 2 棵树中 **md5 完全一致（共 72 文件、约 1.7 万行）**，唯一守护测试 `tests/skills/test_office_canonical.py` 只校验 `base.py` 一个文件。`.agents/skills` 还有 26 个 IDE 技能（运行时不用）。
7. **打包/部署结构性断裂（P0 级）**：`pyproject.toml` 无 MANIFEST.in、`include-package-data` 不足以收集数据文件，egg-info `SOURCES.txt` 中 **无 `config/`、`prompts/`、`static/` 资源**；console script `easy-knowledge-worker = easy_agent.knowledge.worker:run` 指向**不存在模块**（实测 ImportError）；Dockerfile 只 `COPY easy_agent/`，根 `skills/`、`prompts/`、`experts/` 未进镜像却 `mkdir` 空 `skills/`，而 `find_skills_root()` 对显式 `skills_dir` **不回落包内 skills** → 容器内技能数/预置专家数大概率为 0。
8. **配置体系分散、无校验、无热更**：`config.py` 手写 105 行 YAML 解析覆盖 136 个叶子键 + 51 个 `${VAR}` 占位；另有 **25 个 `os.getenv` 直接读取**绕过 `Config`（`EASY_*`、`RAGFLOW_*`、`KNOWLEDGE_CATALOG_SYNC_*` 等）；本机存在 `.env/.env.dev/.env.test/.env.prod` 四份（均未入库）；`config.yaml`（136 键）与 `config-example.yaml`（132 键）漂移 **27/23 个键、8 处同键不同值**；`knowledge.implementation` 为死配置（代码只支持 v2，仅打日志）。
9. **API 设计不统一**：137 条路由、三套前缀（`/agent/*` 12 个 router、`/api/personnel/*`、app 级 `/api/live|ready|health|config` 与 `/agent/live|ready|config` **重复别名**），`api/expert.py` 6 条路由裸挂根（`/agent/add`、`/agent/list`…）；`response_model` 仅约 50 处（≈36%），大量 handler 返回裸 dict；请求/响应模型碎片化（`models/api.py` 34 个 + API 层内联 20 个 + knowledge 57 个）；`LegacyHostRoutes` 中间件再叠一层 `/api/*→/agent/*` 重写。
10. **测试体系"能跑但不设防"**：900 用例可收集，但 `pytest tests/` 因 `tests/integration/test_sandbox_isolation.py:166` 导入已迁移的 `_is_within`（现位于 `easy_agent/fs_safety.py:8`）**整体收集失败**；`conftest.py:24` 永久 `collect_ignore` 两个坏用例；无并发测试（`threading/asyncio.gather` 在 tests 中 0 命中）；高风险模块覆盖薄（scheduler/memory_manager/session_uploads/landlock 各 1 个测试文件）；测试还向源码树写产物（`easy_agent/prompts/unit_test_prompt.md`）。
11. **工程化门禁缺失**：无 `.github/workflows`（无 CI）、无 ruff/black/mypy/pre-commit 配置（仅有 `.ruff_cache` 残留）、无覆盖率门禁；`scripts/release/release_preflight.py` 只有 3 条正则（本地 URL/绝对路径/字面密钥）；前端无 ESLint/Prettier，只有自研 `lint:vue2` 语法守卫。
12. **前端双基线 + 无路由/无状态库**：`frontend-vue2/`（Vue 2.7，88 文件/38.6k 行，被后端托管）与 `frontend/`（Vue 3.5 + Vite，55 文件/29.2k 行，`dist/` 也在构建）并存且都在维护；`App.vue` 3292 行 + 8 个视图布尔开关（无 vue-router），`ChatInput.vue` 4397 行、`ChatMessage.vue` 2694 行、`mixins/chatSession.js` 1663 行；状态靠 props/事件 + 2 个 `Vue.observable` 单例；API 封装分裂在 `src/api/`、`features/*/api.js`、`sdk/index.js` 三处。

> 上述结论均有 §3/§4 的实测数据与 `file:line` 支撑；未实测项（Docker 构建、外部客户端行为、前端业务取舍）列入 §6 不确定项。

---

## 2. 问题清单

| ID | 严重度 | 类别 | 位置(file:line) | 现象与证据 | 风险/影响 | 建议修法 | 工作量 |
|---|---|---|---|---|---|---|---|
| T4-01 | P0 | 测试/质量门禁 | `tests/integration/test_sandbox_isolation.py:166`；`easy_agent/fs_safety.py:8` | `from easy_agent.agent import _check_external_dir_safety, _is_within, EasyAgent` → `ImportError: cannot import name '_is_within'`；该符号已迁至 `fs_safety.py`。`pytest tests/ --collect-only` 结果：`900 tests collected, 1 error`，整包中断 | 测试基线不可收集，任何 CI/发布校验都会红；沙箱隔离用例实际从未运行 | 改为 `from easy_agent.fs_safety import _check_external_dir_safety, _is_within`；同时清理该文件重复的 import 块（第 160–166 行为后追加），加 `pytest --collect-only` 门禁 | S |
| T4-02 | P0 | 打包/部署 | `pyproject.toml:64-68`、`pyproject.toml:39`、`Dockerfile:59,72`、`easy_agent/skills.py:20-35`、`.dockerignore:43` | wheel SOURCES.txt（193 行）无 `config/`、`prompts/`、`static/` 数据文件；`easy-knowledge-worker` 入口实测 `ModuleNotFoundError: easy_agent.knowledge`（真实模块 `easy_agent.knowledges.runtime.worker`）；Docker 只 COPY `easy_agent/`，根 `skills/`、`prompts/`、`experts/` 不在镜像，却 `mkdir -p /app/skills /app/prompts`；`find_skills_root` 对显式配置目录不回退包内 fallback；`.dockerignore` 排除 `*.md` 仅放行 `easy_agent/config/**/*.md` | 镜像内技能=0、预置专家=0、专家引导词缺失；pip 安装场景缺 config/prompts/static 直接启动降级；console script 不可用 | ①修正/删除错误入口点；②补 `MANIFEST.in` 或 `[tool.setuptools.package-data]`（yaml/md/js/css/skills 资源）；③Dockerfile 显式 COPY `skills/`、`prompts/`、`experts/`；④`.dockerignore` 放行 `skills/**`、`prompts/**`、`experts/**` 的 md；⑤`find_skills_root` 保持"配置缺失才回落"并加启动校验（技能数 <1 告警 fail-fast） | M |
| T4-03 | P1 | 分层/依赖 | 见 §4.1（39 模块 SCC 全名单） | 非包模块图上仍有 39 模块 SCC + `db.database ↔ knowledges.core.schema` 2 环；闭环：`api.chat:17→knowledge_impl→knowledges.api:1113→api.chat`、`config:932/951/979/1008→services.agent_manager→agent:27→config`、`services.agent_manager:8↔tools.expert_writer:186,502`、`agent:41↔summarization:19` | 任何重构都牵一发动全身；循环导入当前靠 import 顺序"碰巧"成立，易被新 import 顺序打破；难以单测/替换实现 | 先砍 3 条回边：`config` 的 4 处 lazy import 改为调用方注入配置；`tools.expert_writer` 对 agent_manager 的依赖改为通过接口/参数；`knowledges.api` 不再 import `api.chat`（把知识面板 chat 流下沉到 `knowledges/chat`，路由层只做适配）。随后用 ports/DI 收口 | L |
| T4-04 | P1 | 分层/依赖 | `api/auth.py:11`、`api/chat.py:13`、`api/expert.py:27-28`、`api/files.py:13`、`api/scheduled_tasks.py:10`、`api/sessions.py:14`、`api/skill_center.py:17`；`config.py:932,951,979,1008`；`db/database.py:498,1724,1732,1813,2066`；`middleware/auth.py:10`；`knowledges/api.py:1113`；`agent.py:989` | API 层 8 个文件直接使用 `Database`（含 `expert_store`）；`config`（基础层）依赖 `services.agent_manager`；`db` 依赖 `personnel.user_fields/bootstrap`；`middleware` 依赖 `services`；`agent.py` 跨层 import `services.mcp` 的私有函数 `_parse_mcp_content`；`utils/session_logger.py:103` 反向依赖 services | 业务规则散落 API/DB 层，无法在不启动 FastAPI 的情况下测服务；替换存储/多租户改造需改 8+ 个路由文件；私有函数跨层调用使重构不安全 | 引入 `services/` 之外的 repository 接口（§5 Phase 2）；API 层只做鉴权+参数校验+调用 service；`config.py` 的目录方法改为纯函数（接收 `AgentConfig`）；`fs_safety/_is_within` 等已迁模块禁止再经 `agent.py` 暴露 | L |
| T4-05 | P1 | 巨型类 | `easy_agent/db/database.py:80-2538`；`easy_agent/knowledges/service.py:448-2656` | `class Database`：114 个方法、2458 行（用户/会话/消息/文件/专家/任务/HITL/知识权限全在一个类）；`class KnowledgeService`：49 方法、2208 行。`db/session_messages.py` 已抽出 2 个行级 helper，但 `Database` 内仍有同名重复方法（`database.py:1412 add_message_row` vs `session_messages.py:12`；`database.py:1547 get_messages_from_rows` vs `session_messages.py:35`） | 单类职责爆炸：任何 DB 改动都可能影响全站；SQLite/MySQL 双实现分支互相缠绕；无法按域拆分测试/并行开发 | 按域拆 repository（users/sessions/messages/files/experts/tasks），`Database` 只保留连接/事务/迁移；先加接口与适配层，逐个迁移调用点，禁止新增方法进 `Database` | L |
| T4-06 | P1 | 巨函数/巨文件 | `services/streaming.py:1221`（489 行）、`api/chat.py:384`（245 行）、`services/agent_manager.py:154`（255 行）、`app.py:185`（182 行）、`services/streaming.py:808`（413 行）、`knowledges/service.py:1697`（264 行）、`api/expert.py:997`（239 行）、`services/scheduler.py:229`（221 行） | 函数级 LOC 实测 Top8 见 §4.2；`streaming.py` 单文件 28 个顶层函数 + 2 个 400+ 行生成器，承担会话注册/持久化/HITL/记忆/技能/SSE 全流程 | 不可测、不可评审、并发/取消/异常路径极易漏；新人理解成本高 | 按"编排器 + 纯处理器 + 持久化 + 副作用适配器"拆分；先把 `_persist_*`/`_register_session_skills`/`_submit_memory_updates` 等副作用函数抽为独立模块（stream_processor 已是正确方向） | M |
| T4-07 | P1 | 装配/初始化 | `easy_agent/app.py:245`、`app.py:347`；`app.py:14-17`；`initialization/runtime.py:1-90`；`initialization/environment.py` | `lifespan` 内两次 `await startup_knowledge(app, config_path)`（无条件一次 + 条件判断一次）；`initialize_runtime()` 在 `app.py` 模块导入期执行（副作用）；lifespan 再重复打印/初始化 config/log/env 并做 `os.chdir`、`sys.path.insert` | 知识库启动重复（worker/连接池可能双份）；导入 `easy_agent.app` 即触发日志/环境初始化，测试与工具脚本无法按需加载；`initialization/` 只覆盖 env/log/config，与应用装配职责重叠 | 知识库启动只保留一次（统一在 `knowledge_started` 分支）；把装配逻辑移入 `initialization/app_factory.py`（build_app(config)），lifespan 只调用；移除模块导入期副作用 | S/M |
| T4-08 | P1 | SSE/契约 | `services/streaming.py:808,1221`；`knowledges/chat/streaming.py:20`；`api/expert.py:711,997`；`api/chat.py:177-184`；`docs/requirements-agent.md:98-100`；`CLAUDE.md:66` | 4 处独立 SSE 生产；实际事件 15 个（含 `tool_call_delta` `stream_processor.py:465`、`knowledge_evidence` `chat_bridge.py:40`），`requirements-agent.md` 列 13 个缺上述 2 个，`CLAUDE.md` 列旧事件 `user_input_required`（代码中已无）；前端 3 个独立 SSE 消费实现（`api/chat.js:83`、`api/expert.js:148`、`features/knowledge/api.js:231`） | 前后端协议漂移；新增事件需改 4 处后端 + 3 处前端；断线重连/回放语义在 4 套实现中不一致 | 抽出唯一 `format_sse` + 事件注册表（常量/Enum + JSON schema），golden test 断言 `stream_processor` 输出事件集合；补 `docs/sse-events.md`；把 expert/knowledge 的 SSE 复用同一事件管线 | M |
| T4-09 | P1 | 重复代码 | `easy_agent/skills/{docx,pptx,xlsx}/scripts/office/**`（各 12 文件）；根 `skills/{docx,pptx,xlsx}/scripts/office/**`；`.agents/skills/**`；`tests/skills/test_office_canonical.py:8`；`skills/CANONICAL.md`、`easy_agent/skills/CANONICAL.md` | 3 个格式目录下的 12 个 office 文件 md5 逐一致（`base.py` 854 行等），根树与包内树 `diff -rq` 仅差异 `__pycache__`，即 **6 份相同副本**；守护测试仅断言 `base.py` 一份；`.agents/skills` 26 个开发技能 592K 不进运行时 | 改一处需同步 6 份（CI 无校验），极易漂移；仓库体积/评审噪音；`easy_agent/skills` fallback 在显式配置下根本不会生效 | 单一源（根 `skills/`）+ 构建/安装期同步（`scripts/skills/sync_skills.py` 或 packaging copy），测试校验全部 12 文件哈希；`.agents/skills` 移出主仓或明确标注为非运行时工具 | M |
| T4-10 | P1 | 前端架构/重复 | `frontend-vue2/`（88 文件 38,609 行）；`frontend/`（55 文件 29,179 行，Vue 3.5）；`easy_agent/app.py:74-79`；`Dockerfile:7-21` | 后端只托管 `frontend-vue2/dist`，Docker 也只构建 frontend-vue2；但 `frontend/src`（Vue3）与 `frontend/dist` 仍在更新（dist mtime 2026-10-17 15:24），提交历史存在"同步到 Vue3"；两套组件/API 层高度重复 | 双基线维护成本≈2 倍、行为易分叉；新人不知改哪套；构建时间/仓库体积翻倍 | 做唯一基线决策（见 §6 不确定项），另一套归档/删除；期间在 CI 明确"只有 X 套被构建"，README/AGENTS 纠正 | L |
| T4-11 | P1 | API 设计 | 见 §4.6：`app.py:559-627`、`api/expert.py:763-997`、`api/connectors.py:176`、`api/sessions.py:139/177`、`api/connectors.py:193`、`knowledge_impl.py:59-84`、`app.py:541` | 137 路由三套前缀；`/agent/live`+`/api/live`、`/agent/ready`+`/api/ready` 叠装饰器重复别名，另有 `/api/health`、`/api/config`、`/agent/health`、`/agent/config` 语义重叠；expert 6 路由裸挂根；`/agent/connectors/` 与 `/agent/connectors/status` 风格不一；`response_model` ≈50/137，handler `return {` 72 处；API 层内联 Pydantic 模型 20 个；`LegacyHostRoutes` 用 ASGI 中间件把 `/api/*` 改写成 `/agent/*`；Pydantic 告警 `ConnectorInstallRequest.validate` 覆盖父类字段（`api/connectors.py:193`） | 客户端/文档/测试三套路径心智负担；响应模型缺失使契约不可校验、OpenAPI 不可信；别名中间件是隐性 ABI，删除困难 | 制定 API 规范（前缀/版本/错误体/分页/命名）；新增路由强制 `response_model`；把 `/api/*` 别名标 deprecated 并设移除版本；`api/expert.py` 拆为 `expert_crud.py`/`expert_chat.py`/`expert_export.py` 并加前缀；内联模型迁入 `models/` | M |
| T4-12 | P1 | 配置管理 | `easy_agent/config.py:523-628`；`easy_agent/config/config.yaml`（136 键）vs `config-example.yaml`（132 键）；`config.yaml:232`；`initialization/runtime.py:36-75`；`easy_agent/utils/env_loader.py:57-108` | 手写 `from_yaml`（105 行）+ `${VAR:-default}` 递归解析；25 个 `os.getenv` 直接读取绕过 Config（`app.py`/`knowledge_impl.py`/`knowledges/core/config.py`/`landlock.py`/`client_ip.py`/`env_loader.py` 等）；`config.yaml`/example 漂移：27/23 个键、8 处同键不同值（如 `models.deepseek.model`、`tools.skills_dir`、`personnel.passwordless_login_enabled`）；死配置 `knowledge.implementation: v2` 仅用于打印日志（`knowledge_impl.py:15-33`）；无热更/无 unknown-key 校验（knowledge 段除外） | 配置项爆炸+双份漂移；改配置需重启；错配静默降级（未解析变量按空串处理，仅启动 warning）；环境差异散落无法审计 | 引入单一 `Settings`（Pydantic Settings）集中环境变量与 YAML 映射，禁止 `os.getenv`；启动时校验未知键/必填项并 fail-fast；删死配置；长期支持热更（watch + 原子替换 config 对象） | M |
| T4-13 | P1 | 测试体系 | `tests/`（900 用例）；`tests/conftest.py:24`；`tests/integration/test_sandbox_isolation.py:166`；`tests/api/test_prompts.py:15`；`easy_agent/prompts/unit_test_prompt.md` | 收集失败 1 文件；`collect_ignore=["test_v3_streaming.py","test_basic.py"]` 永久隐藏两个坏/联网用例；无并发测试（`threading|ThreadPool|concurrent|asyncio.gather|multiprocess` 在 tests 0 命中）；覆盖矩阵（tests 中提及模块符号的文件数）：`stream_processor` 1、`scheduler` 1、`memory_manager` 1、`session_uploads` 1、`landlock` 1、`ragflow/client` 1；`tests/api/test_prompts.py` 向源码树写 `easy_agent/prompts/unit_test_prompt.md` 残留 | 高风险（并发/调度/沙箱/SSE resume/多实例缓存）无回归防护；测试写源码树会污染工作区/误提交；坏用例被隐藏而非治理 | 修 T4-01；collect_ignore 改为 `pytest.mark.skip(reason=...)` 或删除；补关键路径测试（scheduler 并发、resume/取消、session_uploads、沙箱降级、SSE 事件契约）；测试产物统一到 tmp_path；加 `--strict-markers` 与覆盖率阈值 | M |
| T4-14 | P2 | 依赖管理 | `pyproject.toml:19,33,32,22,49,34,43-44,80-81` | `httpx` 重复声明且范围冲突（L19 `>=0.28.0`、L33 `>=0.27,<1`）；`requests>=2.34.2` 与 `httpx` 并存；`openpyxl` 同时出现在 `dependencies`（L22）与 `docs` extra（L49）；dev 依赖两套版本（`[project.optional-dependencies].dev` pytest>=7/pytest-asyncio>=0.21 vs `[dependency-groups].dev` pytest>=9.0.2/pytest-asyncio>=1.3.0，L43-44 vs L80-81）；全项目无 upper bound（deepagents/langchain/langgraph 大版本漂移风险）；`mysql-mcp-server` 作为运行时依赖但项目本身是 MCP 客户端/服务端混合，必要性存疑（`uv lock --check` 通过，说明锁文件未坏） | 依赖解析语义混淆；两套 dev 版本可导致本地/CI 行为不一致；上游大版本升级可能静默破坏 | 去重 httpx（统一 `>=0.28,<1`）、统一 dev 依赖（保留 dependency-groups，删 optional dev 或反之）、`requests`→`httpx` 收敛；核心框架加兼容上界并用 dependabot/renovate 升级 | S |
| T4-15 | P2 | 工程化 | 仓库根：无 `.github/workflows`、无 ruff/black/mypy/pre-commit/tox/setup.cfg；`.ruff_cache/` 仅历史残留；`scripts/release/release_preflight.py:11-15`；前端无 eslint/prettier（只有 `frontend-vue2/scripts/check-vue2-syntax.mjs`） | 无 CI、无 lint/format/type 门禁；preflight 仅 3 条正则（本地 URL/开发机绝对路径/字面密钥），不做构建/测试/依赖校验；前端仅自研语法守卫 + 少量 `node --test` | 质量靠人肉；风格/类型问题长期累积；发布前门禁不可自动化 | Phase 1 加最小 CI：`ruff check` + `pytest`（含 collect 检查）+ 前端 `npm run build`/`lint:vue2`/`test:unit`；再逐步加 mypy（新模块白名单）与 pre-commit | M |
| T4-16 | P3 | 文档漂移 | `CLAUDE.md:52,66,78,100-102,113`；`AGENTS.md:18,44`；`README.md:6,21,90,207`；`docs/API接口文档.xlsx`；`docs/requirements-agent.md:98-100`；`plan.md`（0 字节） | CLAUDE.md：声称"无 Docker"（实际有 Dockerfile/compose/k8s）、"vector store/shared npm deps"（已无 `services/vector_store.py`、无 `easy_agent/shared_deps/`）、"17 skills"（实际运行时 7 个）、事件清单含已废弃 `user_input_required`；模块地图缺 knowledges/personnel/connectors/expert/checkpoints/sandbox/landlock/hitl/initialization/stores/subagents/backends/health/session_runtime 等；AGENTS.md 称 `frontend/` 为 Vue2.7（实际 Vue3.5+Vite）；README 同时出现 vue-3.5 徽章与"Vue 2.7/Element UI/Vue CLI"描述且目录树标注错；`docs/API接口文档.xlsx` 68 行、不含 connectors/skill-center/personnel/knowledge/chat 任一路径（现有 137 路由） | 新人/外部接入按文档操作会踩空；文档与代码互为噪音，降低可信度 | 把 CLAUDE.md/AGENTS.md 的模块清单改为"由脚本生成"（或至少补全）；README 统一前端事实；API xlsx 纳入构建产物并加 diff 校验；删除 plan.md 空文件 | S |
| T4-17 | P2 | 死代码/遗留 | `easy_agent/compat/`、`easy_agent/db/repositories/`、`easy_agent/services/stream/`（均只剩 `__pycache__`）；57 个孤儿 `.pyc`；`easy_agent/static/swagger-ui/`（1.8MB）；`easy_agent/prompts/unit_test_prompt.md`；`plan.md`；`easy_agent/tools/__init__.py`（0 字节） | 空目录 3 个；孤儿 bytecode 57 个（`db/repositories/*`、`services/stream/*`、`compat/deepagents`、`small_model`、`container_sandbox`、`stream_v3`、`vector_store`、`async_db/engine/mappers/schema/tables`、`checkpointer`、`mcp_api_keys`、`memory_store`、`mysql_mcp`、`rag_service`、knowledges 旧扁平布局 17 个、`utils/paths`）；`static/swagger-ui` 无任何 `StaticFiles` 挂载/引用（`app.py` 仅把 `/static` 放进 SPA 排除列表，`docs_url=None` 用 Redoc） | 误导读代码的人（以为存在某层/某实现）；仓库体积与打包噪音；死资源可能被误认为可维护契约 | 删除空目录与 `__pycache__`（配置 `.gitignore` 已覆盖）；删除 `static/swagger-ui` 或真正挂载 Swagger；清理测试写出的 prompts 文件；删除 `plan.md` | S |
| T4-18 | P2 | 重复实现 | `services/expert_service.py:208,687` vs `services/public_expert_service.py:245,191`；`api/connectors.py:50,73` vs `services/mcp.py:111,136`；`db/session_messages.py:12,35` vs `db/database.py:1412,1547` | `_as_str_list`、`_find_prompt_file` 在两个专家服务中各写一遍；版本目录解析逻辑两边各自实现（`read_active_version/list_version_dirs` vs `_parse_version_dirs/_resolve_content_dir`）；用户 `mcp.json` 的读在 service、写在 API 层（`_read_user_mcp_raw/_write_user_mcp_raw`）；消息行级 SQL 在 `session_messages.py` 与 `Database` 各一份 | 同一规则（专家目录格式/MCP 配置格式）两处维护，已经出现"注释承诺一致、实现各写各的"；拆分类时最易产生行为差异 | 抽 `expert_layout.py`（目录/版本/配置解析）被两个 expert service 共用；MCP 用户配置读写统一到 `services/mcp.py`；`Database` 消息方法改为委托 `session_messages` | M |
| T4-19 | P2 | 仓库结构/子项目 | `mcp/`（88K，`jmfx_mcp_server.py` 875 行）、`mcp-server/`（71MB，含 `.venv/`、`.pytest_cache/`、独立 `pyproject.toml`+`uv.lock`） | 两个 MCP 子项目与主仓混放，无 workspace/monorepo 声明；`jmfx` 在主代码中 0 引用（`grep -rn jmfx easy_agent/` 无结果）；`mcp-server/.venv` 71MB 未跟踪但存在于工作区 | 仓库边界不清；便携打包/扫描/CI 会误扫；子项目依赖独立于主工程（无统一 lock） | 拆到独立仓库或用 `pyproject` workspace（uv workspace）显式声明；`.gitignore` 已有 `.venv`，需清理工作区并加 pre-commit 体积检查 | S/M |
| T4-20 | P2 | 配置资源双角色 | `easy_agent/config/mcp.json`（tracked，2.8K）；`services/connectors.py:29-40`；`services/mcp.py:88-93`；文件内 `_comment` 自述 | 同一文件既作"公共连接器目录"（`connectors.py` 读 `connectors[]`）又被 `Config.find_config_file("mcp.json")` 当作全局 MCP 配置（`mcp.py` 读 `servers`）；靠"无 servers 字段所以不加载"的约定共存 | 任何人往该文件加 `servers` 会给所有无自定义 mcp.json 的用户自动启用服务（文件内注释也承认此风险）；双语义是配置层的隐患 | 拆分 `config/connectors.json`（目录）与 `config/mcp.json`（全局配置，可为空）；代码分别走不同配置键 | S |
| T4-21 | P2 | 前端结构 | `frontend-vue2/src/App.vue:255-291,268-278`；`components/ChatInput.vue`（4397 行）、`ChatMessage.vue`（2694）、`AiSidebar.vue`（1723）、`mixins/chatSession.js`（1663）；`store/chatStore.js`、`store/skillDirectoryStore.js`；`sdk/index.js`（388） | 无 vue-router，视图切换靠 8 个布尔开关（`showAssets/showKnowledge/showSkillCenter/showScheduledTasks/showSettingsPanel/showUserManagementPanel/showWelcome` 等）；无 Vuex/Pinia，仅 2 个 `Vue.observable` 单例；App.vue 26 个 data 键/49 个方法；3 处 API/状态封装层 | 深链/刷新丢视图；状态与副作用在根组件耦合，单测困难；超大 SFC 无法并行开发、评审困难 | 引入 `vue-router@3`（Vue2）做视图/深链；把根状态拆为 `store/` 模块（现有 observable 单例可保留，但按域拆分）；`ChatInput/ChatMessage/AiSidebar` 按功能拆子组件；API 统一到 `src/api/`（features 复用 `request.js`） | L |
| T4-22 | P2 | 可扩展性 | `services/agent_manager.py:323-395`；`hitl.py:34`；`frontend-vue2/src/utils/toolPresentation.js:44-95`；`skills.py:20-35`；`Dockerfile:59`；`experts/` 目录 | 新增一个 Agent 工具需改：tools/ 新文件、`agent_manager.py` 工具装配（硬编码 if/append）、`hitl.py` 审批名单（如需）、前端工具卡片映射、测试与文档（4–6 处）；新增技能需同步 2 棵树 + 分类关键词；新增预置专家只需目录但部署镜像未含 `experts/`（T4-02） | "改变成本"落在最核心的 agent_manager 上，扩展越多 god-function 越膨胀；部署阻断使"加专家"实际不可用 | 工具注册表化（列表/装饰器注册，含 schema、HITL 标记、前端呈现元数据集中到一处）；技能同步脚本；部署显式携带 experts/skills/prompts | M |
| T4-23 | P2 | 多实例/多租户 | `services/agent_manager.py:18,32,45`；`api/chat.py:177,184,264-280`；`services/scheduler.py:196-269`；`deploy/k8s/agent-deployment.yaml:27,67-75`；`db/database.py:455` | 进程内状态：`_session_agents`、`_session_stream_tasks`、`_session_expert/_session_connectors`、`_llm_instance`、`_session_stream_hubs`（SSE 订阅/广播/取消）；调度器有分布式锁但默认关闭（`scheduler.py:208` 仅告警）；k8s replicas=2 只挂 RWX `workspace/memories/data`，skills/experts 未挂载；DB 无 `tenant_id`，仅 users 有 `organization_id/department_id` | 多副本下：SSE 断线重连/取消可能落到无状态副本；HITL resume 依赖本进程缓存；调度器未开锁会重复执行；本地目录（uploads/skills）跨 pod 不一致；SaaS 多租户需改 schema+全查询 | 设计文档先行：会话/流式状态外置（Redis/DB+outbox）；SSE resume 走共享 pub/sub；scheduler 默认 fail-closed 要求分布式锁；k8s 增加 skills/experts PVC 或镜像内固化；多租户加 tenant_id + 行级隔离中间件 | L |
| T4-24 | P2 | 数据层演进 | `easy_agent/db/database.py`（`_ensure_column`/启动 ALTER）；`scripts/db/migrate_sqlite_to_mysql.py`（811 行） | 启动时 `ALTER TABLE ... ADD COLUMN` 自动迁移（`database.py:455-490` 附近），无版本化 migration 工具；跨库（SQLite/MySQL）手工迁移脚本 811 行 | schema 变更不可审计/不可回滚；多实例滚动发布时并发 ALTER 风险；SQLite/MySQL 行为差异靠人肉维护 | 引入 Alembic（或等价）版本化迁移，禁止启动期 DDL；迁移脚本纳入 CI 校验 | M |
| T4-25 | P3 | 日志/异常卫生 | 全后端：`except Exception` 269 处、`# noqa` 111 处、`print(` 141 处、`# type: ignore` 4 处、无 bare except（实测） | 大量宽泛 except + noqa 掩盖真实错误；print 与 logger 混用（141 处 print，多出现在测试/脚本/工具中） | 排障困难，异常被吞后表现为静默降级 | 统一 logger；`except Exception` 收窄或重抛；加 ruff 规则（BLE001/E722/print 检查）分阶段治理 | S |

> 统计口径：`except`/`print`/`noqa` 由 AST/正则统计（`_analysis/t4_metrics.py`）；路由数由 AST 装饰器解析（`_analysis/t4_dead_routes.py`）。

---

## 3. 详细分析

### 3.1 模块分层与 import 图（实测）

**方法与数据**：脚本 `_analysis/t4_import_graph4.py`（AST 解析 `easy_agent/**/*.py`，正确处理 `from __init__` 的相对导入与 `from pkg import submodule`，不依赖第三方包）。结果：**177 个模块、347 条内部依赖边**，JSON 落盘 `_analysis/t4_import_graph4.json`。

**（1）循环依赖（SCC，Tarjan）**

| SCC | 模块数 | 组成 |
|---|---|---|
| 大环 | **39** | `agent, api.chat, api.sessions, config, knowledge_impl, knowledges.api, knowledges.chat.agent_extension, knowledges.chat.chat_bridge, knowledges.chat.original_tool, knowledges.chat.streaming, knowledges.core.config, knowledges.core.file_validation, knowledges.runtime.catalog_sync, knowledges.runtime.lifecycle, knowledges.runtime.worker, knowledges.service, middleware, middleware.auth, model, services, services.agent_manager, services.connectors, services.expert_service, services.mcp, services.scheduler, services.session_uploads, services.skill_categories, services.skill_library, services.stream_processor, services.streaming, summarization, tools.expert_writer, tools.scheduled_task, tools.session_file_reader, tools.web_search, utils, utils.distributed_lock, utils.session_logger, utils.task_logger` |
| 小环 | **2** | `db.database ↔ knowledges.core.schema`（`db/database.py` import `knowledges.core.schema`；`knowledges/core/schema.py` import `db.database`） |

具体闭环（可复现的最小链，均带行号）：

```text
api.chat:17            → knowledge_impl            （from ..knowledge_impl import ...）
knowledge_impl         → knowledges.api            （from .knowledges.api import router）
knowledges.api:1113    → api.chat                  （from ..api.chat import chat_stream）
config:932/951/979/1008→ services.agent_manager    （lazy: from easy_agent.services.agent_manager import get_agent_config）
services.agent_manager:8 → agent                   （from ..agent import EasyAgent）
agent:27               → config                    （from .config import Config）
services.agent_manager:326 ↔ tools.expert_writer:186,502
agent:41               ↔ summarization:19
knowledges.api:81      ↔ knowledges.service:70
```

**（2）层间依赖矩阵（importer 层 → imported 层，Top 40）**

```text
knowledges→knowledges 49 | api→services 29 | skills→skills 18 | services→services 18
api→api 15 | api→middleware 12 | api→utils 10 | api→models 9 | knowledges→db 9
knowledge_impl→knowledges 8 | api→config 8 | api→db 8 | services→config 7
services→utils 6 | personnel→personnel 6 | initialization→initialization 5
utils→utils 5 | app→services 4 | services→db 4 | services→tools 4 | tools→services 4
tools→db 4 | agent→services 3 | db→db 3 | utils→config 3 | api→tools 2 | api→model 2
api→skills 2 | knowledges→models 2 | knowledges→utils 2 | knowledges→services 2
knowledges→config 2 | services→model 2 | services→models 2 | services→agent 2
db→personnel 2 | personnel→utils 2 | personnel→db 2 | utils→db 2 | utils→config 2
```

**（3）反向/违规依赖边（实测明细）**

```text
# api → db（8 个文件直连）
api.auth:11      from ..db import Database, get_database
api.chat:13      同上
api.expert:27    同上；api.expert:28 → db.expert_store
api.files:13    同上
api.scheduled_tasks:10 / api.sessions:14 / api.skill_center:17  同上
# config（基础层）→ services
config:932,951,979,1008  from easy_agent.services.agent_manager import get_agent_config
# db → personnel
db.database:498   from ..personnel.user_fields import ensure_user_fields
db.database:1724,1813  save_user_fields；:1732 read_user_fields
db.database:2066  from ..personnel.bootstrap import ensure_default_admin
# 其他
middleware.auth:10      from ..services import get_agent_config
knowledges.api:1113     from ..api.chat import chat_stream
agent:989               from .services.mcp import _parse_mcp_content   ← 跨层用私有函数
utils.session_logger:103 from easy_agent.services.agent_manager import get_agent_config
tools.expert_writer:36,37,44  → db / db.expert_store / services.expert_service
tools.scheduled_task:16,68    → db / services.scheduler
tools.session_file_reader:23,70 → services.session_uploads / db
```

**（4）扇入/扇出 Top（耦合热点）**

- 扇入 Top：`db` 27、`config` 26、`models.api` 11、`services` 11、`services.agent_manager` 10、`middleware` 10、`knowledges.core.config` 10、`model` 9、`models.db` 9、`knowledges.core.repository` 9；其后 `utils.auth` 7、`knowledges.service` 6、`db.database` 6。
- 扇出 Top：`api.expert` 17、`app` 16、`agent` 15、`api.chat` 13、`knowledges.api` 13、`api`（init）12、`services.agent_manager` 12、`api.sessions` 10、`api.auth` 10、`api.scheduled_tasks` 10、`services.streaming` 9。
- 解读：`config`/`db` 被 26/27 个模块依赖属正常基础设施，但 `config` 自己反向依赖 services 使"基础设施层"不再可信；`services.agent_manager` 同时扇入 10/扇出 12，是"什么都依赖、什么都依赖它"的枢纽，也是工具注册 god-function 所在地。

### 3.2 文件规模 Top20（后端，`_analysis/t4_metrics.py`，排除 skills 脚本）

| # | LOC | SLOC | 类/函数 | 文件 | 备注 |
|---|---|---|---|---|---|
| 1 | 2668 | 2447 | 6 / 70 | `easy_agent/knowledges/service.py` | `KnowledgeService` 49 方法 |
| 2 | 2558 | 2256 | 1 / 119 | `easy_agent/db/database.py` | `Database` 114 方法 |
| 3 | 1709 | 1419 | 0 / 37 | `easy_agent/services/streaming.py` | 2 个 400–500 行生成器 |
| 4 | 1470 | 1326 | 2 / 56 | `easy_agent/knowledges/ragflow/client.py` | `RagflowClient` 56 方法 |
| 5 | 1360 | 1144 | 1 / 49 | `easy_agent/knowledges/api.py` | 33 条路由、`upload_document` 70 行 |
| 6 | 1235 | 1006 | 4 / 26 | `easy_agent/api/expert.py` | 6 条路由 + `_relevance_gate` + SSE |
| 7 | 1032 | 819 | 19 / 30 | `easy_agent/config.py` | `Config.from_yaml` 105 行 |
| 8 | 1011 | 822 | 1 / 22 | `easy_agent/agent.py` | `_create_agent` 141 行 |
| 9 | 937 | 841 | 1 / 57 | `easy_agent/knowledges/core/repository.py` | |
| 10 | 798 | 640 | 0 / 29 | `easy_agent/services/expert_service.py` | |
| 11 | 782 | 599 | 8 / 22 | `easy_agent/api/skill_center.py` | 文件系统直操作（zip 导入 151 行） |
| 12 | 726 | 588 | 7 / 23 | `easy_agent/api/connectors.py` | 连接器 CRUD + mcp.json 读写 |
| 13 | 721 | 578 | 1 / 19 | `easy_agent/api/chat.py` | `chat_stream` 245 行 + `_StreamHub` |
| 14 | 677 | 538 | 0 / 20 | `easy_agent/app.py` | `lifespan` 182 行 |
| 15 | 660 | 506 | 0 / 19 | `easy_agent/services/agent_manager.py` | `_get_or_create_agent_unlocked` 255 行 |
| 16 | 639 | 500 | 1 / 25 | `easy_agent/services/stream_processor.py` | 事件处理器（较清爽，正确方向） |
| 17 | 634 | 513 | 0 / 20 | `easy_agent/services/session_uploads.py` | |
| 18 | 616 | 512 | 2 / 17 | `easy_agent/tools/expert_writer.py` | 工具内含 95 行写版本逻辑 |
| 19 | 613 | 452 | 57 / 12 | `easy_agent/knowledges/core/models.py` | 57 个模型类（数据契约中心） |
| 20 | 542 | 469 | 0 / 13 | `easy_agent/utils/file_parser.py` | |

补充：后端（不含 skills 脚本）合计 **42,286 行 / 221 类 / 1,441 函数**；`except Exception` 269、`# noqa` 111、`print(` 141、`# type: ignore` 4、bare except 0、TODO/FIXME 0（grep+AST 双查）。`easy_agent/knowledges/core/models.py` 57 个类是模型碎片化的最大来源。

**最长函数 Top10**

| 行数 | 函数 | 位置 |
|---|---|---|
| 489 | `resume_stream_generator` | `services/streaming.py:1221` |
| 413 | `chat_stream_generator` | `services/streaming.py:808` |
| 264 | `upload_document` | `knowledges/service.py:1697` |
| 255 | `_get_or_create_agent_unlocked` | `services/agent_manager.py:154` |
| 245 | `chat_stream` | `api/chat.py:384` |
| 245 | `initialize_knowledge_schema` | `knowledges/core/schema.py:43` |
| 239 | `stream_export_run` | `api/expert.py:997` |
| 221 | `_execute_task` | `services/scheduler.py:229` |
| 182 | `lifespan` | `app.py:185` |
| 151 | `import_skill_zip` | `api/skill_center.py:590` |

### 3.3 并行/重复实现清单

| # | 主题 | 重复点 | 证据 |
|---|---|---|---|
| 1 | SSE 生产 | 4 套：`chat_stream_generator`/`resume_stream_generator`、`knowledges/chat/streaming.py:20`、`api/expert.py:711/997`、`api/chat.py:177-184 _StreamHub` | `grep -rn format_sse` 命中 4 个文件 |
| 2 | 技能树 | 根 `skills/`（canonical）与 `easy_agent/skills/`（fallback）全量重复；`.agents/skills` 第三套（26 个，非运行时）；office 校验器 12 文件 × 3 格式 × 2 树 = **6 份** | `md5sum` 全一致；`diff -rq` 仅 `__pycache__`；`skills/CANONICAL.md` 自述"Do not add a third copy" |
| 3 | 专家服务 | `expert_service.py` 与 `public_expert_service.py` 各自实现 `_as_str_list`（L208 / L245）、`_find_prompt_file`（L687 / L191）与版本目录解析 | `grep -n "def _as_str_list\|def _find_prompt_file"` |
| 4 | MCP 用户配置 | 读在 `services/mcp.py:111,136`，写在 `api/connectors.py:50,73`（且 API 层直接 `write_text` JSON） | `drwx` 职责倒挂 |
| 5 | 消息持久化 | `db/session_messages.py:12,35` 与 `Database.add_message_row:1412`/`get_messages_from_rows:1547` 同实现两份 | `grep -n "def add_message_row"` |
| 6 | 知识库门面 | `knowledge_impl.py`（host facade，含 `LegacyHostRoutes`）与 `knowledges/__init__.py`（package facade）导出重叠 | 两个文件均 re-export `router/startup_knowledge/...` |
| 7 | 前端 API/状态 | `frontend-vue2/src/api/`（9 模块）、`features/{knowledge,personnel}/api.js`、`sdk/index.js` 三处封装 | 目录实测 |
| 8 | 遗留实现痕迹 | 57 个孤儿 `.pyc` 指向已删除模块（`db/repositories/*`、`services/stream/*`、`stream_v3`、`small_model`、`container_sandbox`、`vector_store`、`async_db/engine/mappers`…） | `_analysis/t4_orphans.sh` 输出 |

### 3.4 死代码候选（经 grep/AST 交叉验证）

| 类型 | 对象 | 判定依据 |
|---|---|---|
| 空目录 | `easy_agent/compat/`、`easy_agent/db/repositories/`、`easy_agent/services/stream/` | 目录下仅 `__pycache__`；无 `.py` 源文件、无 import |
| 孤儿字节码 | 57 个 `.pyc`（详细清单见 `_analysis` 脚本输出） | 同目录无对应 `.py`；全部对应已删除模块 |
| 死资源 | `easy_agent/static/swagger-ui/`（1.8MB） | `grep -rn StaticFiles easy_agent/` 无挂载；`app.py:406` 仅把 `/static` 放进 SPA 兜底排除；`docs_url=None`（用 Redoc） |
| 死文件 | `plan.md`（0 字节）、`easy_agent/tools/__init__.py`（0 字节，合法但可删）、`easy_agent/prompts/unit_test_prompt.md`（测试写入残留） | 文件大小/内容 + `tests/api/test_prompts.py:15` 证据 |
| 未引用子项目 | `mcp/`（jmfx） | `grep -rn "jmfx" easy_agent/` 0 命中；无 entry point/配置引用 |
| 死配置键 | `knowledge.implementation`（`config.yaml:232`） | `knowledge_impl.py:15-33` 只支持 v2，仅打日志 |
| 零内部引用者（非死） | `app`、`web_runner`（入口）、`knowledges.scripts.migrate_knowledge_to_shared`（独立脚本）、各包 `__init__`、skills 脚本（独立执行） | 逐个交叉验证，**不**作为死代码上报 |
| 近似死模型 | 无（`models/api.py` 34 类均有引用，最少 1 个文件） | AST 类名 × grep 文件数 |

> 注意：上表"孤儿 `.pyc`"包含 `db/repositories/*` 8 个、`services/stream/*` 7 个、knowledges 旧扁平布局 17 个——这说明历史上做过至少两次大重构（DB 仓储层、流式 v3、知识库扁平→分层）但**清理不彻底**，是架构演进风险的重要信号。

### 3.5 配置与环境差异（实测）

**（1）`config.yaml`（gitignore，本机 261 行）vs `config-example.yaml`（入库模板）**

| 指标 | config.yaml | config-example.yaml |
|---|---|---|
| 叶子键数 | 136 | 132 |
| `${VAR}` 占位符总数 | 51 | 62 |
| 去重环境变量 | 49 | 60 |

- **仅存在 config.yaml（27）**：`knowledge.audit.*`（2）、`knowledge.implementation`（1）、`knowledge.limits.*`（10，含 `allowed_extensions[0..9]`）、`knowledge.limits.max_*`（6）、`models.opencode.*`（8）。
- **仅存在 config-example（23）**：`expert.*`（4）、`file_context.*`（5）、`knowledge.shared_dataset_id`（1）、`models.ark.*`（6）、`models.qwen.*`（6）、`tools.experts_dir`（1）。
- **同键不同值（8）**：`app_welcome_title`（标点）、`knowledge.api_key/base_url`（`${X:-}` vs `${X}`）、`models.deepseek.api_base/model/context_length`、`personnel.passwordless_login_enabled`（true vs false）、`tools.skills_dir`（`/qts/skills` vs `./skills`）。
- 结论：example 已不能作为"可用模板"（缺 27 个当前生效键、23 个模板键在当前配置不存在），且默认模型/路径完全不同 → 新人复制 example 启动会得到与作者不同的行为。

**（2）环境变量读取散落（`os.getenv`/`os.environ.get`，共 25 个键，绕过 `Config`）**

```text
AGENT_ENV(app/config/initialization/personnel/services/utils)  AGENT_WIN(agent_manager)
EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD(personnel)  EASY_CHECKPOINT_POSTGRES_URI(app)
EASY_CONFIG(config, knowledge_impl)            EASY_CORS_ALLOW_ORIGINS(initialization)
EASY_DEFAULT_RESET_PASSWORD(auth)              EASY_ENV_FILE/EASY_ENV_OVERRIDE(env_loader)
EASY_JWT_SECRET(utils.auth)                    EASY_LANDLOCK_{CWD,RO,RW,RW_FILES}(landlock)
EASY_LLM_PROBE/EASY_SKIP_LLM_PROBE(app)        EASY_PROMPTS_DIR(prompt_loader)
EASY_TRUST_PROXY(client_ip)                    KNOWLEDGE_CATALOG_SYNC_{DATABASE,SQL_FILE}
PYTEST_CURRENT_TEST(rate_limit)                RAGFLOW_{API_KEY,BASE_URL,EMBEDDING_MODEL,SHARED_DATASET_ID}
```

**（3）`.env` 分层现状**

- 本机存在 4 份：`.env`(1492B)、`.env.dev`(2792B)、`.env.test`(2263B)、`.env.prod`(2288B)；**均未被 git 跟踪**（`git ls-files` 仅 example），密钥管理方向上安全（定性判断，未查看值）。
- 入库的 3 份模板键数不一致：dev 31、test 23、prod 24；dev 独有 8 个（`KNOWLEDGE_CATALOG_SYNC_*`、`KNOWLEDGE_ENABLED`、`RAGFLOW_*`），prod 独有 1 个（`EASY_CHECKPOINT_POSTGRES_URI`）。加载顺序（`utils/env_loader.py:57-108`）：`EASY_ENV_FILE` > `AGENT_ENV` 对应 `.env.{env}`（项目根→cwd）> `.env`（项目根→cwd），默认不覆盖进程环境变量，`EASY_ENV_OVERRIDE=1` 反转。
- 前端两份基线各有 4 个 `.env.{dev,test,prod,win}`（入库，仅含 `VUE_APP_AGENT_ENV`，无密钥）。

### 3.6 API 一致性与 SSE 事件差异

**（1）路由统计**：137 条路由装饰器 / 15 个文件：`knowledges/api.py` 33、`skill_center` 15、`connectors` 13、`app.py` 10、`sessions` 10、`auth` 10、`files` 8、`personnel` 7、`settings` 7、`scheduled_tasks` 7、`expert` 6、`chat` 5、`prompts` 3、`terminal` 2、`completion` 1。

| 维度 | 现状 | 问题 |
|---|---|---|
| 前缀 | 12 个 router 用 `/agent/*`；`/api/personnel/*`；`/agent/knowledge/v1/*`；`expert` 6 条裸挂根（`/agent/add`、`/agent/update`、`/agent/list`、`/agent/public-list`、`/agent/del`、`/agent/stream_export_run`）；app 级 10 条 | 无统一命名空间/资源风格；`/agent/add` 与 `/agent/auth/login` 语义层级混乱 |
| 重复别名 | `app.py:559-560` `/agent/live`+`/api/live`；`:600-601` `/agent/ready`+`/api/ready`；另有 `/api/health`、`/api/config`、`/agent/health`、`/agent/config` | 4 组同义端点；兼容层 `LegacyHostRoutes`（`app.py:541`，`knowledge_impl.py:59-84`）又把 `/api/{auth,chat,sessions,files,settings,prompts,skills,scheduled-tasks,terminal,knowledge}` 批量改写成 `/agent/*` | 
| 响应模型 | `response_model=` 约 50 处（多为 sessions/auth）；API handler 内 `return {` 72 处（含辅助函数）；API 层内联 Pydantic 模型 20 个（connectors 7、skill_center 8、expert 4、settings 1） | 契约不可校验；`models/api.py` 34 类的中心化被内联模型破坏 |
| 错误 | `HTTPException` 161 处 + 全局 handler（`app.py:530`）+ SSE `error` 事件 + 部分裸 dict | 错误体形状不统一（字符串 detail vs 结构化） |
| 文档 | `docs_url=None`（仅 ReDoc `/docs`）；`docs/API接口文档.xlsx` 68 行，不含 connectors/skill-center/personnel/knowledge/chat 任一路径 | OpenAPI 覆盖不足、文档严重过期 |
| 质量告警 | Pydantic：`api/connectors.py:193` `ConnectorInstallRequest` 字段 `validate` 覆盖父类属性（pytest 收集期 warning） | 模型继承设计问题，未来可能静默失效 |
| 版本/分页/幂等 | 仅 knowledge 带 `/v1`；sessions/files 等列表无统一分页；无幂等键（chat 有 `message_id`，其余无） | 客户端升级/重试语义不明确 |

**（2）SSE 事件清单差异表**

- 代码权威清单：`services/streaming.py:13-31` 模块 docstring 列 15 个事件：`start, thinking_start, thinking, thinking_end, content, content_end, tool_call, tool_call_delta, tool_result, todo_list, token_usage, done, error, approval_required`（另 `knowledges/chat/chat_bridge.py:40` 发 `knowledge_evidence`）。
- `docs/requirements-agent.md:98-100` 列 13 个：缺 `tool_call_delta`、`knowledge_evidence`。
- `CLAUDE.md:66` 列 11 个：含**已不存在**的 `user_input_required`（全仓 grep 仅 CLAUDE.md 命中），缺 `todo_list/token_usage/approval_required/tool_call_delta/knowledge_evidence`。
- 消费者：`frontend-vue2/src/api/chat.js:83`、`api/expert.js:148`、`features/knowledge/api.js:231` 三套独立解析；`src/utils/toolPresentation.js` 对 `tool_call/tool_call_delta` 渲染逻辑独立演进。

### 3.7 测试体系

- 规模：**900 个收集用例**，目录分布（test_*.py 数）：`tests/` 9、`services/` 27、`api/` 18、`integration/` 13、`db/` 9、`knowledges/` 6、`initialization/` 2、`agent/` 1、`config/` 1、`middleware/` 1、`skills/` 1（合计 88）。
- **收集错误（T4-01 复现）**：

```text
tests/integration/test_sandbox_isolation.py:166: in <module>
    from easy_agent.agent import _check_external_dir_safety, _is_within, EasyAgent
E   ImportError: cannot import name '_is_within' from 'easy_agent.agent'
→ 900 tests collected, 1 error
```

  `_is_within` 现位于 `easy_agent/fs_safety.py:8`；`agent.py:28` 只 re-export `_check_external_dir_safety`。该测试文件后半段（L164 起）是追加的第二个 import/test 块，与文件前半段重复，属重构后未同步的遗留。
- 收集期副作用：仅 `--collect-only` 即触发 `easy_agent.initialization` 日志/环境初始化（stderr 打印 `运行日志: logs/proc-*.log`、`.env.dev` 注入），因为 `tests/conftest.py` 顶层 `from easy_agent.app import app`。这使"只收集"也产生 IO/日志副作用（本次审计只跑 collect，未跑用例；`logs/` 为 gitignore 运行目录，未污染 `data/`）。
- 隔离设计（亮点）：`tests/conftest.py` 用临时目录 + `EASY_CONFIG` 指向生成的临时 YAML、每用例独立临时 SQLite、Mock LLM（继承 `ChatOpenAI` 以通过 deepagents 类型校验）、`app.dependency_overrides` 注入 `testuser`，设计是合理模板。
- 覆盖薄弱/空白（以"tests 中提及模块符号的文件数"为启发式，仅作方向性证据）：`services/stream_processor.py` 1 文件、`services/scheduler.py` 1、`memory_manager.py` 1、`session_uploads.py` 1、`landlock.py` 1、`knowledges/ragflow/client.py` 1；**无并发/多线程/多实例测试**；无打包/入口点/静态资源测试；无 API 契约（response_model）测试；`collect_ignore=["test_v3_streaming.py","test_basic.py"]`（`conftest.py:24`）把两个坏/联网用例永久隐藏。
- 测试污染源码树：`tests/api/test_prompts.py:15` 写 `unit_test_prompt`，源码树残留 `easy_agent/prompts/unit_test_prompt.md`（内容即测试文本）。

### 3.8 工程化、打包与部署

| 项 | 现状（证据） | 风险 |
|---|---|---|
| CI | 无 `.github/workflows`；无 GitLab/Jenkins 配置 | 无自动门禁 |
| Lint/format/type | `pyproject.toml` 无 `[tool.ruff|black|mypy|isort]`；仓库无 `.flake8/mypy.ini/.pre-commit-config.yaml/tox.ini`；仅 `.ruff_cache/` 残留 | 风格/类型/未使用导入全靠人眼 |
| 前端质量 | 无 ESLint/Prettier；`frontend-vue2` 有自研 `scripts/check-vue2-syntax.mjs`（`lint:vue2`）与 `node --test` 单测；无 CI 执行 | 前端规则只在本地手动跑 |
| 发布前检查 | `scripts/release/release_preflight.py:11-15` 三条正则：私网 URL、开发机绝对路径、字面密钥；不跑构建/测试/依赖校验 | 门禁能力弱，容易漏 |
| Python 打包 | `pyproject.toml:64-68` `include-package-data=true` + `packages.find include=["easy_agent*"]`（`namespaces` 默认 true，故 `easy_agent/skills/**/*.py` 以 namespace package 被打入，SOURCES 有 64 条 skills 路径），但无 MANIFEST.in；SOURCES.txt 无 `config/`、`prompts/`、`static/` 的 yaml/md/js 资源；无 `package-data` 声明 | wheel 安装后 `easy_agent/config/config-example.yaml`、`config/prompts/*.md`、`static/**`、`skills/**/SKILL.md` 缺失 → 启动降级/技能为空 |
| Console scripts | `easy-web` OK；`easy-knowledge-worker = easy_agent.knowledge.worker:run` **实测 ModuleNotFoundError**（真实模块 `easy_agent.knowledges.runtime.worker`） | 入口点不可用 |
| Dockerfile | 多阶段；`frontend-builder` 只构建 `frontend-vue2`；backend `COPY easy_agent/` + `uv export --frozen --no-dev --extra docs` + `pip install -e .`；`mkdir -p /app/{data,workspace,memories,logs,skills,prompts}`；**未 COPY 根 `skills/`、`prompts/`、`experts/`**；`.dockerignore:43` 排除 `*.md`（仅放行 `easy_agent/config/**/*.md`、`deploy/**/*.md`）；`config.yaml` 未在 `.dockerignore` 中排除（会把本机配置打进镜像；entrypoint 又强制要求 `/app/easy_agent/config/config.yaml` 存在，否则 exit） | 运行时无 skills/experts；镜像不可复现/可能夹带本机配置 |
| Entrypoint | `docker/entrypoint.sh` 用 `envsubst` 生成 `easy_agent/config/.config.resolved.yaml`（同目录保证相对 prompt 路径）、注入 `runtime-config.js`、`uvicorn easy_agent.app:app` | 依赖容器内可写 config 目录（k8s `readOnlyRootFilesystem: false` 才可） |
| k8s | `deploy/k8s/agent-deployment.yaml`：`replicas: 2`、RWX PVC 50Gi、`SYS_ADMIN` capability、仅挂 `/app/workspace`、`/app/memories`、`/app/data` | skills/experts 未挂载/未镜像 → 多副本配置不一致；`SYS_ADMIN` 与 bwrap 需要权衡（T3 议题） |
| DB 迁移 | 无 Alembic；`_ensure_column` 启动期 ALTER；`scripts/db/migrate_sqlite_to_mysql.py` 811 行手动迁移 | 滚动发布/回滚风险 |
| 启动脚本 | `start.prod.sh` 构建 `frontend-vue2` 后 `uv run uvicorn`；`start.dev.sh`、`docker-compose.yml`、`deploy/{ragflow,mysql-local}` 并存 | 入口多、行为不一致（dev/prod/docker 三套） |

### 3.9 文档漂移清单

| 文档 | 声明 | 实际（证据） |
|---|---|---|
| `CLAUDE.md:113` | "No CI/CD or Docker — deployment is manual via uvicorn" | `Dockerfile`、`docker-compose.yml`、`docker/`、`deploy/k8s/` 均存在（CI 确实没有） |
| `CLAUDE.md:52` | lifespan 初始化 "vector store, shared npm deps" | 无 `services/vector_store.py`（仅孤儿 `.pyc`）；无 `easy_agent/shared_deps/` 目录 |
| `CLAUDE.md:66` | 事件清单含 `user_input_required`，无 `tool_call_delta` 等 | 代码已无 `user_input_required`；实际有 `tool_call_delta/todo_list/token_usage/approval_required/knowledge_evidence` |
| `CLAUDE.md:78-90`、`AGENTS.md:18` | `frontend/` 为 Vue 2.7 + Element UI + Vue CLI | `frontend/package.json` 为 `vue ^3.5.25` + Vite（`scripts/run-vite.mjs`）；Vue2 基线是 `frontend-vue2/` |
| `CLAUDE.md:100-102` | "17 skills … shared_deps/node_modules 符号链接" | 运行时 7 个技能（`skills/` 与 `easy_agent/skills/` 各 7 个目录）；无 shared_deps |
| `AGENTS.md:10-20` | 目录树仅列 api/services/db/models/middleware/tools/skills/config | 缺 `knowledges/ personnel/ connectors/ expert/ checkpoints.py sandbox.py landlock.py hitl.py initialization/ stores.py subagents.py summarization.py backends/ db/expert_store.py db/session_messages.py` 及 `api/{expert,connectors,settings,skill_center,terminal,scheduled_tasks,completion}.py` |
| `README.md:6,21,90,207` | 徽章 vue-3.5；正文"前端 Vue 2.7"；目录树 `frontend/` 标注 Vue2.7；构建入口描述为 `frontend/scripts/run-vite.mjs`"实际调用 Vue CLI" | 两套前端并存；`frontend/` 是 Vue3+Vite，`frontend-vue2/` 才是 Vue2 托管基线；`frontend-vue2/package.json` scripts 混用 vite wrapper 与 `vue-cli-service preview` |
| `docs/API接口文档.xlsx` | 68 行接口清单 | 不含 `/agent/connectors*`、`/agent/skill-center*`、`/api/personnel*`、`/agent/knowledge/v1*`、`/agent/chat/stream` 等现有接口（现有 137 路由） |
| `docs/requirements-agent.md:98-100` | 13 个 SSE 事件 | 缺 `tool_call_delta`、`knowledge_evidence`（且无 FR/AC→代码/测试的可追溯矩阵，27 FR / 51 AC） |
| `plan.md` | （0 字节空文件） | 无内容，可删 |

### 3.10 前端架构（粗粒度）

- **规模与形态**：`frontend-vue2/`（Vue 2.7.16 + Element UI 2.15，Vite wrapper + Vue CLI preview 混用）88 个 vue/js 文件、38,609 行；`frontend/`（Vue 3.5 + Vite）55 文件、29,179 行。后端 `app.py:74-79` 固定托管 `frontend-vue2/dist`；`Dockerfile:7-21` 只构建 frontend-vue2。但 `frontend/dist` 也在 2026-10-17 更新（15:24），说明双基线仍在同时维护。
- **无路由**：`App.vue` 用 8 个布尔开关切视图（`App.vue:268-278`：`showAssets/showKnowledge/showSkillCenter/showScheduledTasks/showSettingsPanel/showUserManagementPanel/showWelcome` 等）。后果：深链/刷新恢复不可能、视图逻辑集中在根组件、每新增面板都要改 App.vue。
- **无状态库**：`package.json` 无 vuex/pinia/vue-router；`src/store/` 只有 `chatStore.js`（1.1K）与 `skillDirectoryStore.js`（3.1K）两个 `Vue.observable` 单例；大部分状态通过 `data()/props/emit` 在 `App.vue` ↔ 子组件间传递。
- **巨型组件**：`ChatInput.vue` 4397、`App.vue` 3292、`ChatMessage.vue` 2694、`KnowledgeWorkbench.vue` 1910、`AiSidebar.vue` 1723、`ExpertListPanel.vue` 1704、`ConnectorListPanel.vue` 1490、`SkillListPanel.vue` 1236、`mixins/chatSession.js` 1663。`features/knowledge/`、`features/personnel/` 已按 feature 拆分（正确方向），但 `src/api/`、`features/*/api.js`、`src/sdk/index.js` 三处 API 封装并存。
- **性能反模式（静态推断，需 T2 实测）**：单 `messages` 数组 + 每个 SSE chunk 触发根组件/消息渲染路径；`ChatMessage` 兼做 markdown/高亮/工具卡片渲染，chunk 级更新时无法只重渲染单条消息。建议消息列表按 id 做稳定 key + 子组件拆分 + 节流合并 chunk。
- **SDK**：`src/sdk/index.js`（388 行）+ `build:sdk` 脚本 → 存在"嵌入式聊天 SDK"这一第二产品形态，与主 SPA 共享 `api/` 与 `chatStore`，但无独立契约/版本管理。

### 3.11 可扩展性"改变成本"分析

| 扩展类型 | 必须修改的文件 | 耦合点 | 成本 |
|---|---|---|---|
| 新增一个 Agent 工具 | `easy_agent/tools/x.py`（新）→ `services/agent_manager.py:323-395`（硬编码装配）→ `hitl.py:34`（如高危需审批）→ `frontend-vue2/src/utils/toolPresentation.js:44-95`（卡片/图标）→ 测试/文档 | agent_manager god-function、前端呈现映射 | 4–6 文件，M |
| 新增一个技能 | `skills/<name>/SKILL.md`（canonical）→ 同步 `easy_agent/skills/`（fallback）→ `services/skill_categories.py`（自动分类关键词，可选）→ 若 office 类还需同步 office 脚本 | 双树手工同步、分类启发式 | 2–4 文件 + 手工同步，M |
| 新增预置专家 | `experts/<id>/`（`active` + `config.yaml` + `manifest.json` + 提示词 md + `README.md`），**零代码** | 部署未带 `experts/`（T4-02）→ 实际不可用 | 1 目录 + 修部署 2 文件，S（修 T4-02 后） |
| 新增连接器（MCP） | `easy_agent/config/mcp.json` 的 `connectors[]`（id/name/icon/fields/config） | 与全局 mcp.json 双语义（T4-20） | 1 文件，S |
| 新增 MCP Server | 用户 `mcp.json` 或全局 `mcp.json`；若随包分发需 `pyproject` + `Dockerfile` | `services/mcp.py` 动态加载，无需改代码 | 1–3 文件，S |
| 新增 HTTP API | `api/x.py`（新）→ `api/__init__.py` 导出 → `app.py:540-555 include_router` → `models/api.py`（如需 schema） | 路由注册表 + 模型 hub | 3–4 文件，S |
| 新增知识库实现 | `knowledge_impl.py`（facade 导出面）+ `knowledges/**` + 8 个 import 方 + `config knowledge.implementation`（当前硬编码 v2） | facade 硬编码，名存实亡 | L（需重设计） |
| 新增存储后端 | `db/database.py` 单类（+ 空目录 `db/repositories/`） | 无 repository 插件点 | L |
| 新增 HITL 审批工具 | `hitl.py:34` `DEFAULT_INTERRUPT_TOOLS` + 前端审批 UI 映射 | 工具名硬编码字符串 | S |

### 3.12 多实例/多租户结构性阻碍

1. **进程内状态**：`services/agent_manager.py:18 _session_agents`、`:32 _session_stream_tasks`、`:45 _llm_instance`、`:22-30 _session_expert/_session_connectors/_session_edit_target`；`api/chat.py:177 _session_stream_hubs`（SSE 订阅/广播/取消）。多副本下：SSE 断线重连（`/agent/chat/stream/live`）、cancel、HITL resume 可能落到无该会话状态的副本。
2. **调度器**：`services/scheduler.py:196-269` 有分布式锁实现，但默认关闭（`:208` 仅告警"单实例部署无需开启"）→ k8s `replicas:2` 下若不配 `distributed_lock.enabled` 会重复执行定时任务。
3. **本地文件系统**：`workspace/{username}/{session}/`、`data/uploads/`、`skills/`、`prompts/`、`memories/`；k8s 只挂 RWX `workspace/memories/data`，skills/experts 在镜像内（而镜像里又没有，见 T4-02）。
4. **Schema**：无 `tenant_id`，仅 `users.organization_id/department_id`（`db/database.py:455,1737,1743`）；知识库有团队空间/权限模型但基于 `username`/`user_id`。SaaS 化需全表 tenant 列 + 查询守卫 + 文件路径隔离。
5. **鉴权**：JWT + 首次登录 IP 绑定（`middleware/auth.py`，T3 详查）→ 与 LB/多入口/移动网络天然冲突。
6. **配置**：无配置中心/热更；多副本靠各自环境变量，靠 k8s ConfigMap/Secret（已有骨架，见 `deploy/k8s`）。

---

## 4. 分阶段重构路线建议

> 原则：先"止血"（可验证、低风险）→ 再"立规"（门禁与契约）→ 再"拆骨"（god class / 分层，需要设计文档与 characterization tests）→ 最后"换血"（多实例/多租户/前端基线，独立 RFC）。每阶段结束都应有可运行验证点。

### Phase 0 · 止血（0.5–1 天，风险极低）

1. 修 `tests/integration/test_sandbox_isolation.py:166` 的 import（改从 `easy_agent.fs_safety` 导入），删文件尾部重复 import 块；`pytest tests/` 必须全绿收集（目标：900 用例 0 error）。
2. 修/删 `easy-knowledge-worker` 入口（`pyproject.toml:39`）。
3. 删空目录 3 个 + 孤儿 `.pyc`（`__pycache__` 全清）+ `plan.md` + `easy_agent/prompts/unit_test_prompt.md`；`easy_agent/static/swagger-ui` 删除或真正挂载。
4. 打包：补 `MANIFEST.in`/`package-data`（yaml/md/js/css/SKILL.md）；Dockerfile 显式 `COPY skills/ prompts/ experts/`；`.dockerignore` 放行对应 `*.md`，并显式排除 `easy_agent/config/config.yaml`/`mcp.json` 的"本机版"（改为 entrypoint 显式要求挂载/环境注入）。验收：`pip install .` 后 `python -c "import easy_agent.config"` 与 skills/prompts 资源存在；`docker build` + 启动后 `discover_skills()` ≥7、`discover_public_experts()` ≥5。
5. 验收脚本化：`pytest --collect-only`（防再犯）+ 一条打包冒烟（wheel 解包检查资源存在）。

### Phase 1 · 立规（3–5 天，低风险高收益）

1. **CI 骨架**（GitHub Actions/GitLab CI）：`ruff check` + `pytest`（含 collect 校验）+ 前端 `npm run build`/`lint:vue2`/`test:unit`；缓存 uv/node。
2. **静态门禁**：新增 `[tool.ruff]`（E,F,I,B；先只对新增/改动文件）+ `pyproject` 去重 httpx/dev 依赖（T4-14）；`pre-commit` 跑 ruff + prettier?（前端无 eslint，先加 eslint 或至少语法守卫入 CI）。
3. **SSE 契约**：把 `services/streaming.py:13-31` 的 15 事件抽为常量/JSON Schema，补 `tests/` golden test 断言 `StreamProcessor` 输出事件集合；更新 `docs/requirements-agent.md`、`CLAUDE.md`；新增 `docs/sse-events.md`。
4. **止血分层回边**：`config.py:932/951/979/1008` 的 4 处 lazy import 改为调用方传入 `AgentConfig`（`get_user_*_dir(config=...)` 已有参数形态，直接要求必传）；`knowledges.api:1113` 不再 import `api.chat`（改为调用 `knowledges/chat/*` 或由 app 装配注入回调）。
5. **API 规范**：新增 `docs/api-conventions.md`（前缀/版本/错误体/分页/命名/response_model 要求）；`/api/*` 别名标记 deprecated 并写移除版本；`api/expert.py` 拆 3 文件 + 加前缀。
6. **测试补强**：scheduler（并发/锁）、resume/取消、session_uploads、landlock/sandbox 降级、SSE 契约各 ≥3 用例；加 `pytest-xdist` 可选；并发冒烟用 `asyncio.gather`。
7. **技能去重**：`scripts/skills/sync_skills.py`（根→包）+ 测试校验 **全部 12 个 office 文件**（现只查 `base.py`）；`.agents/skills` 明确归属（移出/标注）。
8. **文档**：CLAUDE/AGENTS/README 模块表与前端事实修正；API xlsx 改为脚本生成并入 CI diff。

### Phase 2 · 拆骨（设计文档 + 2–3 周，中高风险）

1. **DB 拆分**（先设计文档）：`Database` 按域拆 repository（users/sessions/messages/files/experts/tasks/hitl），先加接口与适配层（`Database` 委托新 repo，旧调用不动），再逐步迁移；删除 `session_messages.py` 与 `Database` 的重复方法。
2. **streaming 拆解**：`chat_stream_generator`/`resume_stream_generator` 拆为"编排器 + `StreamProcessor`（已有）+ persistence + hitl/approval + memory/skill 注册"；先补 characterization tests（用 synthetic AIMessageChunk 序列断言事件流），保证行为不变。
3. **知识库收口**：`knowledge_impl` 与 `knowledges/__init__` 二选一；`knowledge.implementation` 死键删除或真正支持多实现。
4. **专家服务合并**：抽 `expert_layout.py`（版本目录/配置/提示词解析）被 `expert_service` 与 `public_expert_service` 共用；MCP 用户配置读写统一到 `services/mcp.py`。
5. **前端**：引入 `vue-router@3` + 按域拆 store（现有 observable 可保留但拆分）+ `App.vue` 拆 layouts/views；`ChatInput/ChatMessage/AiSidebar` 按子组件拆分；统一 API 层到 `src/api/`（features 复用 `request.js`）。
6. **DB 迁移工具**：Alembic 替代启动期 ALTER，双写/校验/回滚方案。

### Phase 3 · 换血（高风险，需独立 RFC 与灰度）

1. **打破 39 模块 SCC**：以 ports/adapters 划定 `agent` / `tools` / `knowledge` / `api` 边界，DI 装配；逐条消除回边并加 import-linter 规则（如 `import-linter`/自写脚本）到 CI。
2. **多实例**：`_session_agents`/`_session_stream_hubs`/`_session_stream_tasks` 外置（Redis/DB+outbox）；SSE resume 走共享 pub/sub；scheduler 默认 fail-closed 要求分布式锁；k8s 增加 skills/experts PVC 或固化镜像。
3. **多租户**：schema 加 `tenant_id` + 全局查询守卫 + 路径注入 + 鉴权绑定（与 T3 协同）；JWT IP 绑定策略重审。
4. **前端基线收敛**：业务确认唯一基线后，另一套归档/删除；Docker/后端托管/文档同步。
5. **发布工程**：签名镜像、SBOM、迁移回滚演练。

**风险与迁移策略要点**：① 所有拆分先做 characterization test（尤其是 streaming/HITL/知识库）；② API 别名与旧路由用"双版本 + 访问日志"渐进下线；③ DB 拆分只换调用方式不改 schema，避免数据风险；④ 前端基线决策先冻结再迁移，禁止边迁移边加功能；⑤ SCC 拆分每砍一条回边就加 import-linter 规则锁死。

---

## 5. 与需求文档/官方规范的对照（T4 视角摘要）

- `docs/requirements-agent.md` 27 个 FR / 51 个 AC 有产品基线，但**无 FR/AC → 代码位置/测试用例的可追溯矩阵**，无法自动判断覆盖率（T1 负责规范一致性，这里仅指出文档工程缺口）。
- SSE 契约（FR-1.3）实际实现超出文档（多 2 个事件），但文档未更新；FR-1.3 AC2"断线重连可回放"依赖 `api/chat.py:_StreamHub`（进程内），**多副本下不满足**（见 §3.12）。
- 需求文档中"步数上限可配置""压缩阈值以实际模型窗口为基准"等在 `config.py`/`agent.py` 有实现，但配置项分散在 config.yaml 与 env 两套（T4-12），验收层面建议补一条"配置项文档生成"AC。

---

## 6. 不确定项 / 需 Lead 复核

1. **Docker 实际构建与运行未验证**（本环境无 docker 权限/未运行）：T4-02 的镜像缺 skills/experts、`.dockerignore` 的 `*.md` 递归语义、entrypoint 写配置目录等均为**静态推断**；建议 T5 或 Lead 在可 docker 环境跑 `docker build` + 启动探针（检查 `discover_skills()` 数量、`experts/` 是否存在、`/api/live`）。
2. **`frontend/`（Vue3）与 `frontend-vue2/` 的保留策略需业务确认**：提交历史（"同步到 Vue3"）显示两边仍在维护，但后端/Docker 只用 Vue2 构建；不能仅凭静态证据判定哪套是"死"的。
3. **外部客户端是否使用 `/api/*` legacy 别名**：`LegacyHostRoutes`（`knowledge_impl.py:59-84`）暗示历史上存在旧客户端；删除/下线前需访问日志确认（本报告只列为改进项，不建议直接删）。
4. **`.agents/skills`（26 个）定位**：`.codebuddy/memory` 说明是 Vercel CLI 安装的 IDE 技能，运行时不用；是否纳入主仓规范需团队确认。
5. **"无前端引用的路由"仅为候选**：约 30 条路由未在前端源码出现（含 `/agent/prompts/*`、`/agent/completion/chat`、`/terminal` 等），可能被外部脚本/旧 SDK/文档客户端使用；删除前需访问日志。
6. **`mcp/`（jmfx）与 `mcp-server/` 是否仍部署**：主代码 0 引用，但可能独立交付；`mcp-server/.venv`（71MB）只是本机工作区产物，不影响 git 仓库体积。
7. **重复代码 LOC 估算**（office 6 份 ≈1.7 万行）、`response_model` 比例（≈36%）、覆盖矩阵（"测试提及模块符号"）为 AST/正则近似值，用于方向判断，未做逐行人工核对。
8. **39 模块 SCC 当前不导致运行时错误**（导入顺序碰巧成立）；其严重性在"可维护/可重构"，不是"线上已坏"。拆分需以 characterization tests 为前提，不能直接大改。
9. **`easy_agent/prompts/unit_test_prompt.md` 残留来源**：可能是手工跑测试或 CI 历史产物，未能确认；建议直接清理并在测试侧改为 tmp 目录。
10. **`logs/` 副作用**：本次为定位收集错误执行了 `pytest --collect-only`，因 `tests/conftest.py` 顶层 import app 触发了日志/环境初始化（写入 `logs/`，gitignore 运行目录）；未执行业务用例、未触碰 `data/`。

---

## 附录 A · 审计脚本与可复现命令（均位于 `_analysis/`，只读分析用）

| 脚本 | 作用 |
|---|---|
| `_analysis/t4_import_graph4.py` | AST import 图（177 模块/347 边）、SCC 环检测、层矩阵、扇入扇出 → `t4_import_graph4.json` |
| `_analysis/t4_metrics.py` | 文件 LOC/类/函数/最长函数/except/noqa/print 统计 → `t4_metrics.json` |
| `_analysis/t4_dead_routes.py` | 全路由解析（137 条）+ 前端引用交叉比对 → `t4_routes.json` |
| `_analysis/t4_config_diff.py` | config.yaml vs config-example 键/值/占位符差异、散落 env 变量清单 |
| `_analysis/t4_orphans.sh` | 孤儿 `.pyc`、MANIFEST/SOURCES、目录/技能树核对 |
| `_analysis/t4_dup.sh` / `t4_deploy.sh` / `t4_events.sh` / `t4_tests_docs.sh` / `t4_ext.sh` / `t4_scale.sh` | 重复代码、部署、事件、测试/文档、扩展耦合、多实例阻碍的取证脚本 |

复现（WSL）：
```bash
cd /home/sututu/code/easy-agent
.venv/bin/python _analysis/t4_import_graph4.py
.venv/bin/python _analysis/t4_metrics.py
.venv/bin/python _analysis/t4_dead_routes.py
.venv/bin/python _analysis/t4_config_diff.py
bash _analysis/t4_orphans.sh
.venv/bin/python -m pytest tests/ --collect-only -q   # 现象：900 collected, 1 error
```

## 附录 B · 正向发现（避免只报问题）

- `tests/conftest.py` 的隔离设计（临时目录 + 独立 SQLite + Mock LLM + dependency_overrides）是良好模板，值得保留并在全仓推广。
- `services/stream_processor.py` 把"消息 chunk → SSE 事件"抽成独立可测模块，是 `streaming.py` 拆解的正确方向。
- 沙箱采用 bwrap→Landlock→fail-closed 的降级链（`sandbox.py`/`landlock.py`/`Dockerfile` 注释一致），并有 `tests/integration/test_sandbox.py`、`tests/services/test_hitl_destructive.py` 等防护（修好 T4-01 后隔离用例也可运行）。
- 调度器已实现分布式锁（`services/scheduler.py:196-269`），只是默认关闭；多实例化有现成积木。
- `docs/knowledge/*`、`docs/upload-limits-design.md`、`docs/distributed-lock.md` 等设计文档较完整；`skills/CANONICAL.md` 已意识到"不要再造第三份"（虽仍存在 6 份物理副本）。
- 本机密钥仅存在于未入库的 `.env*`；入库的 `config.yaml` 全部使用 `${VAR}` 占位（实测 5 处敏感键均为 placeholder）。
