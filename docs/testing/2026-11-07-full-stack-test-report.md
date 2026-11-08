# easy-agent 全栈验收报告（2026-11-07）

> 测试对象：当前工作区（含未提交并行改动）。分支 `refactor/audit-improvements-2026-10-18`，HEAD `85719e0`。
> 验收形态：后端 `main.py` 实跑 8080 + `frontend-vue2/dist` 同源托管（生产形态），真实大模型调用。

## 1. 结论

| 维度 | 结果 |
| --- | --- |
| 自动化回归 | **2554 passed / 2 skipped / 0 failed**（466.93s，2555 collected） |
| 质量门禁 | `check_config_schema.py --check`、`check_env_usage.py --check` 均通过 |
| 后端接口冒烟 | 13 组共 107 项（去重后 104 项有效用例）**全部通过**；另 4 条认证负向用例通过 |
| 前端界面验收 | 17 项主流程 + 3 个技能中心 Tab **全部通过**；console error = 0，失败请求 = 0 |
| 前后端一致性 | 13 类业务接口路径逐条对齐；5 条死导出路径、3 个死事件分支、1 处 `done` 字段漂移（均不影响当前页面主流程） |
| 日志 | proc 全量收录 access（470 条 = comm 470 条），`llm_wire` DEBUG 生效（74 条）；本轮无业务告警/异常；仅 2×503（知识库未启用，预期）+ 2×500（xlsx→PDF，宿主缺 LibreOffice Calc） |
| 落库核对 | 12 条 run 全部 `completed`；会话/消息/改名/置顶/定时任务创建删除均与 MySQL 一致 |

**总体判定：前后端功能一致、运行正常。** 未发现阻断性缺陷；发现 1 个运维级问题（pytest 污染生产日志并触发轮转）、1 个契约漂移（`done` 事件字段）和若干代码卫生问题，见第 9 节。

## 2. 环境与方法

| 项 | 值 |
| --- | --- |
| 后端 | `.venv/bin/python main.py --log-level debug`，监听 `0.0.0.0:8080`（启动前 8080/5173 均空闲） |
| 前端 | `frontend-vue2` 重新构建（`vue-cli-service build`，20.6s）后由后端托管 `frontend-vue2/dist` |
| 产物一致性 | `GET /js/app.c027e27e.js` md5 = `397610f0…`，与 `frontend-vue2/dist/js/app.c027e27e.js` 完全一致 |
| Vue3 基线 | `frontend` 仅做构建检查：`npm run build` EXIT=0（22.53s），未做界面验收 |
| 数据库 | MySQL（`agent` 库，93 用户/92 active）；`DB_TYPE=mysql` |
| 模型 | `ACTIVE_MODEL=opencode` → 界面显示 `deepseek-v4.1-flash`；真实调用 8 次（含 2 次工具审批） |
| 外部依赖 | RAGFlow 未启用（`KNOWLEDGE_DISABLED`）；Web 终端默认关闭（`EASY_WEB_TERMINAL_ENABLED=0`）；LibreOffice 已装 writer/impress，**缺 calc** |
| 测试账号 | 一次性用户 `qa-20261107-a1`（API）、`qa-20261107-ui/-ui2/-ui3`（浏览器） |

证据文件（临时，不入库）：`/tmp/qa/pytest.log`、`api_results.json`、`ui_results.json`、`openapi.json`、`backend.log`、`shots/*.png`。

## 3. 功能点覆盖矩阵

| # | 功能点 | 前端入口 | 后端接口 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 免密登录/资料/配置 | URL `?username=` 自动登录 | `/agent/auth/login-passwordless`、`/profile` GET/PUT、`/config` | 通过 |
| 2 | 鉴权负向 | — | 无 token→401、非管理员→403、错误密码→401、自助注册→403 | 通过 |
| 3 | 会话创建/列表/详情 | 新建会话、侧边栏 | `POST/GET /agent/sessions`、`/count`、`/{id}` | 通过 |
| 4 | 会话改名/置顶/删除 | 会话 `…` 菜单 | `PUT /{id}/title`、`PUT /{id}/pin`、`DELETE /{id}` | 通过（UI 改名+置顶，删除走接口） |
| 5 | 历史/直写消息 | 打开会话 | `GET /{id}/history`、`POST /{id}/messages` | 通过 |
| 6 | SSE 对话主链路 | 输入框发送 | `POST /agent/chat/stream` | 通过（start/thinking/content/tool_call/tool_result/token_usage/done） |
| 7 | 停止生成 | 停止按钮 | `POST /agent/chat/cancel` | 通过（`{"status":"cancelled"}`） |
| 8 | 流状态/实时流 | 刷新恢复 | `GET /chat/stream/status`、`/stream/live` | 通过 |
| 9 | 深度思考渲染 | 对话气泡 | SSE `thinking*` | 通过（首个用例捕获 4 类 thinking 事件） |
| 10 | 联网搜索 | 地球按钮 | 工具 `web_search`（SSE `tool_call/tool_result`） | 通过（真实搜索 1 次） |
| 11 | 人工审批（拒绝） | 审批卡片 | `approval_required` → `POST /chat/resume` reject | 通过（恢复后 done） |
| 12 | 人工审批（通过） | 审批卡片 | resume approve | 通过（action_requests 8 字段齐全） |
| 13 | 定时任务生命周期 | 定时任务面板 | 工具 `create_scheduled_task` + `GET`/`PATCH toggle`/`POST run`/`GET runs`/`DELETE` | 通过（含二次删除 404） |
| 14 | 文件上传/列表/删除 | `+` → 添加文件 | `POST /agent/files/upload`、`/{id}` DELETE、`/session/{sid}` | 通过（超限 413、跨目录预览 400） |
| 15 | 会话工作区上传/预览/解析/下载 | 工作区面板 | `POST /sessions/{sid}/upload`、`/files/preview`、`/parse`、`/download` | 通过 |
| 16 | Office→PDF | 预览 | `GET /files/preview?target=pdf` | **xlsx 失败（500）**，见 9.7 |
| 17 | 技能中心·技能 | 侧栏 → 技能 | `public-skills`、`user-skills`、`categories`、`skill-detail`、`add/disable/enable/remove-skill` | 通过（8 张卡，写入链路后回到干净态） |
| 18 | 技能中心·专家 | 侧栏 → 专家 | `/agent/list`、`/public-list`、`/add`、`/update`、`/expert-md`、`/del` | 通过（7 张公共卡；创建→更新→详情→删除全链路，2 次真实生成） |
| 19 | 技能中心·连接器 | 侧栏 → 连接器 | `/agent/connectors` 全套（13 条） | 通过（2 个预置模板，mcp/raw、session、tools 404 负向） |
| 20 | 设置（记忆/提示词） | 头像 → 设置 | `/agent/settings/{memory,system-prompt,models,web-search,upload-limits,skills,knowledge}` | 通过 |
| 21 | 模型切换 | 输入框模型按钮 | `GET /agent/settings/models` | 通过（3 个选项） |
| 22 | 侧栏折叠/展开 | 左上角按钮 | — | 通过 |
| 23 | 刷新持久化 | 浏览器刷新 | `GET /agent/sessions` | 通过（会话与改名保留） |
| 24 | 暗色主题 | `data-theme=dark` | — | 通过（背景 `rgb(0,0,0)`，截图无错位） |
| 25 | 健康/就绪/指标 | — | `/agent/{live,health,ready}`、`/agent/metrics`（403 非管理员） | 通过 |
| 26 | 静态托管/SPA 回退 | 直接访问任意路径 | `GET /{full_path}` → index.html | 通过 |
| 27 | 非流式补全 | — | `POST /agent/completion/chat` | 通过（2.0s） |
| 28 | 人员管理 | 侧栏入口（管理员） | `/agent/personnel/*` | 环境受限（403，见 10） |
| 29 | 知识工程 | 侧栏入口（未启用则不展示） | `/agent/knowledge/v1/*` | 环境受限（503 `KNOWLEDGE_DISABLED`） |
| 30 | Web 终端 | — | `GET /terminal` | 未启用（404，安全默认） |

## 4. 后端接口明细（/tmp/qa/api_results.json）

| 组 | 用例数 | 覆盖要点 |
| --- | --- | --- |
| auth | 7 | 免密登录、profile 读写、auth/config、admin 接口 403、metrics 403、未认证 401 |
| sessions | 10 | 创建/列表/计数/详情/改名/置顶/历史/直写消息/404 |
| settings | 8 | 7 个 GET + memory PUT（`content` 字段） |
| files | 11 | 上传/会话上传/列表/树/预览/下载/解析/删除/413/目录穿越 400 |
| skills | 12 | 公共库/我的库/分类/详情(public)/坏 scope 400/增删启停 |
| experts | 18 | 列表/公共列表/required-skills(公共 200、自建 404)/expert-md/add/update/del |
| connectors | 6 | status/list/tools(404 负向)/mcp-raw/session |
| scheduled | 10 | 列表/创建(工具)/toggle×2/run-now/runs/删除/二次删除 404 |
| personnel | 3 | login-policy 200；users、import-template 403（非管理员） |
| knowledge | 3 | capabilities/status 200；bases 503（未启用） |
| misc | 7 | completion、terminal 404、prompts 403、health/ready/SPA/app.js |
| chat | 9 | 真实对话、内容非空、首帧 start、cancel、status、live |
| hitl | 3 | approval_required、拒绝恢复、通过恢复 + action_requests 字段 |

补充手工负向：`register`→403（自助注册关闭）、错误密码/未知用户→401、越权 admin→403。

## 5. 前端界面验收（/tmp/qa/ui_results.json，截图 `/tmp/qa/shots/`）

17 项全部通过：`landing`、`welcome-preset-cards(6)`、`preset-fills-composer`、`chat-reply-rendered`、`session-created`、`session-pin`、`session-rename`、`persist-after-reload`、`plus-panel(18 节点)`、`model-switcher(3)`、`skill-center-experts(49)`、`tab-技能`、`tab-连接器`、`scheduled-panel`、`settings-panel`、`sidebar-collapse`、`dark-theme`。

三 Tab 实际内容：技能 8 卡（文档处理 4/代码开发 3/设计创意 1）、连接器 2 卡（MySQL、SQLite MCP 模板）、专家 7 卡 + 12 个分类 chips。

- console error：**0**；`pageerror`：**0**；`/agent/**` 4xx/5xx：**0**。
- 交互行为核实：预置问题卡点击只填充输入框（`onPresetCardClick` → `setMessage`），需再点发送——与实现一致，非缺陷。
- 关键截图：`01-welcome.png`、`02-chat-done.png`、`06-plus-panel.png`、`09-skill-center-skills.png`、`10-skill-center-connectors.png`、`13-settings.png`、`15-dark.png`。

## 6. 持久化核对（MySQL，测试账号）

| 表 | 结果 |
| --- | --- |
| users | 一次性账号 4 个，均 active |
| sessions | `qa-20261107-a1` 4 条（含被改名+置顶的主会话、定时任务自动会话）；3 个 UI 账号各 1 条 |
| session_messages | 主账号 16 条；UI 账号各 2 条（user+assistant） |
| agent_runs | 12 条全部 `completed`，0 failed/interrupted |
| scheduled_tasks | 测试任务已删除，残留 0 |
| expert_agents | 测试专家已删除（hard），残留 0 |

## 7. 日志审查

- **三视图完整性**：今日 21 点后 `uvicorn.access` 在 proc 与 comm 中各 470 条（完全相等）→ proc 仍全量收录通信日志，符合既定约定。
- **llm_wire**：proc 中今日 74 条 `easy_agent.llm_wire` DEBUG（请求 URL/headers/body 与响应体），工作区并行改造的「DEBUG 报文只落文件」已生效。
- **本轮请求状态码**（21:06–21:39）：200×418、404×22、304×12、422×9、403×8、401×3、503×2、500×2、413×2、400×2。4xx 全部来自冒烟负向用例；5xx 仅 2×503（知识库未启用，契约内）+ 2×500（xlsx→PDF）。
- **业务告警**：以测试账号/会话 ID 过滤，无 WARNING/ERROR。
- **日志轮转**：验收期间 proc 触发两次轮转（`proc-v01-easy-agent.log.2026-11-07_21-19-42` 52.4MB、`…_21-20-22` 1.1MB），本轮早期日志被移出当前文件。见 9.1。

## 8. 前后端一致性

- **路径对齐**：sessions(10)、chat(7)、connectors(13)、settings(7)、skills(15)、scheduled(7)、expert(8)、personnel(7)、knowledge(28) 逐条与后端路由表一致；`/api/*` 由 `LegacyHostRoutes` 重写，属兼容。
- **SSE 对齐**：`SSE_EVENT_TYPES` 15 类中，宿主页 `EasyAgentPage.vue` 覆盖全部业务事件；`token_usage`、`tool_call`、`approval_required` 字段逐项匹配（审批 8 字段实测齐全）。
- **已删除能力残留**：`EASY_MCP_TOOL_PREFIX` 仅存在于模板反向断言；`VITE_*` 仅归档文档；`qwen/ark/minimax/anthropic` 模型键 0 命中；`runtime-config` 仅文档/注释与 `release_preflight.py` 白名单（见 9.5）。
- **失配项**：见 9.2–9.4。

## 9. 问题清单

### 9.1【P2·运维】pytest 直接写生产日志目录，并触发 proc 轮转
- 现象：全量 pytest 期间 `logs/err-v01-easy-agent.log` 从 420KB 涨到 533KB，内容含 `tests/services/test_stream_runner.py` 故意抛的 `RuntimeError("db down"/"boom")`、`disk full`、分布式锁丢失等；proc 同时被轮转两次（52.4MB + 1.1MB），本轮真实运行日志被挤出当前文件。
- 影响：排障时无法区分测试噪音与线上异常；日志轮转可能丢失正在排查的时间窗。
- 根因初判：`tests/conftest.py` 隔离了数据库/配置，但未把 logging 的 `logs/` 目录重定向到临时目录；并行改造把日志级别提升到 DEBUG 后单测日志量进一步放大。
- 关联：与 5 个提交无关；与工作区并行改动（日志级别/`llm_wire`）叠加。
- 建议：conftest 里设置日志目录/Pod 名到 `tmp_path`，或 pytest 时把 logging handler 指向 `/tmp`。

### 9.2【P3·契约】`done` 事件缺 `title`/`content`，前端读取落空
- 后端 `services/streaming/processor.py:718` 的 done 仅含 `session_id/elapsed_time/usage/blocks`；前端 `EasyAgentPage.vue:1482、1515`、`chunkHandler.js:367、400` 读 `done.content`/`done.title`。
- 影响：会话标题无法在流结束时即时更新（依赖后续 `loadSessions()` 回填）；正文末段若只存在于 `done.content` 会丢（当前由 `content_end` 兜底，未观察到丢失）。
- 建议：二选一——后端补字段，或删除前端死分支。

### 9.3【P3·代码卫生】5 条前端导出路径后端不存在（均无调用方）
`api/auth.js:142` `/agent/auth/me`、`api/auth.js:108` `DELETE /agent/auth/unregister`、`api/files.js:91` `/agent/files/content`、`api/files.js:47` `/agent/sessions/files/all`、`api/files.js:40` `/agent/sessions/{id}/files`。运行时无影响，建议清理以免误用。

### 9.4【P3·覆盖缺口】AI 侧边栏事件处理不全
`features/chat/services/chunkHandler.js` 未处理 `tool_call_delta`、`knowledge_evidence`（宿主页 `EasyAgentPage.vue:1232、1371` 有处理）；另有 3 个后端不存在的事件分支 `content_start`/`assistant_start`/`user_input_required`。影响范围仅 SDK 侧边栏，8080 宿主页正常。

### 9.5【P3·卫生】已删能力的两处残留口径
- 三份 `.env.*.example` 仍导出 `EASY_LLM_REQUEST_LOG=0`（`runtime.py:19` 已降级为「仅告警」），未列入 `tests/initialization/test_env_parity.py` 的 `REMOVED_KEYS`。
- `scripts/release/release_preflight.py:32` 仍把 `runtime-config.js` 放在发布白名单（该链路已删除）。

### 9.6【P3·文档】`docs/sse-events.md` 的 `knowledge_evidence` 字段落后
文档写 `items/session_id`，实现为 `evidence/warnings`（`sse.py:93`），代码与前端一致，仅文档需同步。

### 9.7【P3·环境】xlsx → PDF 预览 500
`GET /agent/files/preview?target=pdf` 对 xlsx 返回 `500 文档转换失败: … source file could not be loaded`。系统 `soffice` 直接转换同文件同样失败，且 `/usr/lib/libreoffice/program/` 只有 `swriter`/`simpress`、**无 `scalc`** → 宿主缺 LibreOffice Calc，非代码缺陷。前端 xlsx 预览走 `@vue-office/excel` 客户端渲染，不受此路径影响；doc/ppt 转换依赖的 writer/impress 已具备。

### 9.8【观察】`/agent/health` 与 `/agent/ready` 语义差异
liveness 返回 `agent_initialized=false` 而 readiness 全 true（启动完成后）。属 liveness/readiness 分工，非缺陷，记录备查。

### 9.9【功能缺口】技能类别管理无前端入口
后端提供 `create/rename/delete/reorder-category`、`assign-category` 5 个接口，前端只有读取分类。属产品功能缺口，非回归。

## 10. 环境受限与未覆盖

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 知识工程全链路 | 环境受限 | RAGFlow 未启用，`/bases` 503；前端在 `knowledgeEnabled=false` 时不展示入口 |
| 人员管理（列表/导入/策略） | 环境受限 | 普通用户 403；未提供 admin 凭据（未读取引导密码文件） |
| 管理员接口（admin users/reset/metrics 正路径） | 环境受限 | 同上，仅验证 403 负向 |
| 密码登录/自助注册/改密 | 部分覆盖 | 自助注册已关闭（403）；错误密码/未知用户 401 已验；无已知密码账号 |
| Web 终端 | 未启用 | `EASY_WEB_TERMINAL_ENABLED` 未开，按 SEC-01 默认 404 |
| MCP 连接器真实连通 | 未覆盖 | 当前用户无已安装 MCP 服务，仅验证列表/模板/CRUD 读接口 |
| 限流 429 | 未覆盖 | 避免触发真实限流影响后续用例，仅确认中间件存在 |
| 2 skipped 用例 | 未展开 | `pytest -q` 未打印 skip 原因 |

## 11. 清理与复现

- 后端仍在运行（用户要求启动服务）：`setsid nohup .venv/bin/python main.py --log-level debug > /tmp/qa/backend.log 2>&1 &`；停止：`fuser -k 8080/tcp`。
- 前端无需单独进程：8080 直接托管 `frontend-vue2/dist`；访问 `http://localhost:8080/?username=<一次性用户>`。
- 临时账号 `qa-20261107-*` 与数据保留在 dev 库，未删除（便于复现）；如需清理按 `users.username LIKE 'qa-20261107%'` 处理。
- 复现命令：
  ```bash
  .venv/bin/python -m pytest tests/ -q --tb=short -rf
  .venv/bin/python /tmp/qa/api_smoke.py --reset auth sessions settings files skills experts connectors scheduled personnel knowledge misc chat hitl hitl_approve expert_crud
  node /tmp/qa/ui_acceptance.cjs
  ```

## 12. 修复记录（2026-11-08）

按用户确认的范围修复第 9 节中的契约与卫生问题，**不改业务行为、不动并行编辑中的 `processor.py`**。

| 报告条目 | 修复内容 |
| --- | --- |
| 9.2 `done` 字段漂移 | `chunkHandler.js` / `EasyAgentPage.vue` 不再读 `done.title`、`done.content`；正文改从 `content` 块拼接，会话标题靠流结束后的 `loadSessions()` 回填；`docs/sse-events.md` 明确写明 `done` 不含这两个字段 |
| 9.3 死导出路径 | 删除 `api/auth.js` 的 `unregister`、`getCurrentUser`，`api/files.js` 的 `getSessionFiles`、`getAllFiles`、`getFileContent`，并移除不再使用的 `requestText` 导入；全仓确认无调用方 |
| 9.4 侧边栏事件缺口 | `chunkHandler.js` 新增 `tool_call_delta`（按 `tool_call_id` 合入 `arguments` 快照、置 `streaming`）与 `knowledge_evidence`（写 `knowledge_evidence` / `knowledge_warnings`）；同时删除 `content_start` / `assistant_start` / `user_input_required` 三个后端不存在的事件分支（知识库独立流 `KnowledgeWorkbench.vue` 未动） |
| 9.5 模板/发布残留 | 三份 `.env.*.example` 删除 `EASY_LLM_REQUEST_LOG=0`，统一基线由 122 项收敛为 **121** 项；`test_env_parity.py` 断言同步并把它加入 `REMOVED_KEYS`；`release_preflight.py` 去掉 `runtime-config.js` 白名单 |
| 9.6 文档滞后 | `docs/sse-events.md` 修正 `tool_call_delta`（`arguments` 快照）、`knowledge_evidence`（`evidence`/`warnings`）、`done`（`elapsed_time`/`usage`/`blocks`）三行；`sse.py` 注释标明 canonical 字段 |

**未修复（按用户决定）**：9.1 pytest 写生产日志并触发轮转（P2 运维项）、9.7 xlsx→PDF 500（宿主缺 LibreOffice Calc，仅记录）、9.8 liveness/readiness 语义差异（非缺陷）、9.9 技能类别管理无前端入口（产品功能缺口）。

## 13. 修复验证（2026-11-08）

| 验证项 | 结果 |
| --- | --- |
| `npm run lint:vue2` | 通过（扫描 145 个文件） |
| `npm run test:unit` | **236 passed / 0 failed**（新增 `chunkHandler.events.test.js` 3 例：delta→tool_call 覆盖、知识证据、done 拼正文/清 streaming/刷新标题） |
| `npm run build` | 通过（20.2s，Hash `f2071a4eecdd4c5f`）；`GET /` 与 `frontend-vue2/dist/index.html` md5 一致（`80ad9366…`） |
| pytest 子集 | `test_env_parity` / `test_sse_contract` / `test_architecture_gate` / `test_scripts_layout` / `test_docs_facts` → **78 passed** |
| 质量门禁 | `check_config_schema.py --check`、`check_env_usage.py --check` 通过 |
| 浏览器冒烟 | 新用户 `qa-20261108-fix`，发送「请只回复两个字：通过」→ done 后正文「通过」渲染、服务端会话标题回填（`请只回复两个字：通过`）、console error 0、失败请求 0；截图 `/tmp/qa/shots/30-fix-smoke.png` |
