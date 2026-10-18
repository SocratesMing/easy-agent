# Easy-Agent 代码审计报告与改进清单

| 项目 | 内容 |
| --- | --- |
| 审计日期 | 2026-10-17 |
| 代码基线 | `1f04992 merge(zhq/dt-1016)` + 当前未提交改动（约 24 改 / 26 未跟踪） |
| 审计方式 | 只读审计；5 个并行专项审计 + Lead 交叉验证 + 真实服务/隔离环境运行取证 |
| 审计范围 | `easy_agent/`（177 个 Python 模块 / 5.1 万行）、`frontend-vue2/`、`frontend/`、`mcp/`、`mcp-server/`、`tests/`、`docs/`、构建与部署文件 |
| 运行环境 | Python 3.11.15、deepagents 0.6.8、langchain 1.3.4、langgraph 1.2.4、langchain-mcp-adapters 0.2.2、FastAPI 0.136.3、Pydantic 2.13.4 |
| 证据归档 | `docs/audit/evidence/T1~T5-*.md`（5 份专项报告，含逐条 file:line、代码片段、PoC/实测数据、复现脚本） |
| 声明 | 审计过程未修改任何业务代码/配置；PoC 均在隔离目录（`/tmp/t3test`、`/tmp/ea-audit`）或临时端口完成，未触碰业务数据 |

---

## 1. 总体结论

**一句话：功能完备度与工程底子不差（框架装配自洽、沙箱真实有效、890 个测试通过），但作为“多用户可公网访问的 Agent 平台”，安全边界存在 4 个可直接利用的 P0 缺口；性能上有一处 O(n²) 的流式内存放大和多处“事件循环里做重活”；架构上处于“单机原型 → 多实例平台”演进的中途，留下双份实现、循环依赖和部署断裂。**

分级统计（去重后）：**P0 × 6、P1 × 24、P2 × 25、P3 × 15**（详见第 7 章清单）。

十条核心判断：

1. **安全（最紧急）**：存在 4 条可直接利用的 P0 —— 未认证 Web Terminal 宿主 Shell、SPA 兜底路由未认证任意文件读取、免密登录账号接管、MCP 连接器用户可控 stdio 命令导致宿主机 RCE；另有 8 条 P1（文件 API 穿越、prompts 未授权读写、无鉴权 LLM 透传、JWT 永不过期等）。其中 3 条已被真实运行服务（uvicorn）独立复现。
2. **沙箱设计正确但边界不完整**：bwrap / Landlock / fail-closed 三档降级实测有效（宿主路径不可见、env 密钥被剥离、符号链接逃逸被拒）；但 Web Terminal 完全绕过了它；沙箱内网络全通且无 CPU/内存/进程数 rlimit；Landlock 降级时 venv 不可用、无法隔离网络。
3. **性能最严重的单点**：`stream_processor.py:373-385` 的 thinking 事件每个 chunk 重发全量 `full_content`，71K 字符思考实测产生 **365MB SSE** / 1.4 亿字符内存保留，属必须修复的 P0。
4. **性能系统性问题**：流式增量落库每 500 字全量序列化并重复 UPDATE（50KB 回复阻塞事件循环 0.75s）；SessionLogger 全量重写 JSON（120 条 → 798KB、累计写盘 ≈48MB）；自研 checkpointer 每次 put 全量 pickle 写库；PDF/OCR/LibreOffice/上传解析在 async 路由同步执行；SQLite 单连接 + 全局 RLock（8 线程写加速比 1.00×）。
5. **并发正确性**：同会话并发两条消息已复现“新流 0 条内容即 EOF、旧流被静默取消、两条流共享 agent 与同一 last-assistant 行”的串写缺陷（`api/chat.py:256-306` 等）；检查点 thread_id 每轮新增且永不裁剪。
6. **多实例/多用户**：agent 缓存、SSE hub、流任务表、调度器状态全部是进程内状态；`distributed_lock` 实现正确但默认关闭；k8s `replicas: 2` 却未强制 MySQL，`data` 用 `emptyDir`；DB 无 tenant 维度。
7. **框架规范（DeepAgents）**：当前装配在 deepagents 0.6.8 上自洽（`create_deep_agent` 的 skills/memory/permissions/interrupt_on/subagents/checkpointer/store 参数均在公开签名内），但 **依赖无上界 + 3 处私有/内部 API 绑定**（`FilesystemMiddleware.__init__` patch、`deepagents.graph.create_summarization_middleware` 替换、`MemorySaver` 私有属性 pickle）→ 一旦 `uv sync` 升级到 0.7+ 会断裂。
8. **技能与 MCP 规范**：技能发现/注入使用官方 `skills=` 机制，但公共技能不在 sources 中（靠复制到用户目录“启用”，更新不同步）；MCP 工具名无 server 前缀、无冲突校验（可静默顶掉内建 `execute`/`read_file`）；URL 连接器默认走旧 `sse` 传输而非 Streamable HTTP；`mcp-server/` 子项目方向正确但错误语义（isError）不规范。
9. **架构组织**：177 模块 / 347 条内部依赖边，仍有 39 模块强连通分量（循环依赖，靠 import 顺序“碰巧”成立）；`Database` 单类 114 方法 / 2458 行；`streaming.py` 有 `chat_stream_generator` 与 `resume_stream_generator` 两套 400+ 行近似实现；SSE 后端 4 处生产、前端 3 处消费；office 校验脚本 6 份相同副本；前端 Vue2/Vue3 双基线。
10. **工程化与部署**：无 CI、无 ruff/mypy/pre-commit；默认 `pytest` 因 1 个 import 错误在 0.78s 整体中断；Dockerfile 只 COPY `easy_agent/`，镜像内根 `skills/`、`prompts/`、`experts/` 缺失；`easy-knowledge-worker` console script 指向不存在的模块；CLAUDE.md/AGENTS.md/README 与实际代码全面漂移。

> 已证伪/需修正的旧说法（避免误导）：`X-Username` 回退已删除（带该头仍 401）；JWT secret 已持久化到 `data/.jwt_secret`（非“重启失效”）；“每条消息全量重写 JSON blob”不成立（`add_message` 是行级 INSERT，真正的写放大在流式增量持久化）；`startup_knowledge` 重复调用有幂等守卫，不会双份初始化；`sandbox.check_command_paths` 已是死代码。

---

## 2. 审计方法与证据说明

- **T1 框架规范一致性**：对照 DeepAgents 官方文档（quickstart / configuration / skills / subagents / backends / middleware / HITL / memory / MCP / persistence）、已安装 `deepagents==0.6.8` 与 `langchain-mcp-adapters==0.2.2` 源码，逐项核对 `agent.py` 装配。
- **T2 性能与并发**：静态分析 + 隔离基准（`_analysis/t2_bench*.py`）：SSE 字节量、事件循环阻塞、SQLite 并发、缓存增长、checkpoint 写放大、日志写放大。
- **T3 安全/多用户/沙箱**：攻击面清单 + 33 项 PoC（隔离 `/tmp/t3test` + 独立 SQLite + 真实 `easy_agent.app:app`）+ 对运行中 8080 的只读验证。
- **T4 架构组织**：AST import 图、函数/文件规模统计、路由清单、配置 diff、重复文件 md5、死代码扫描、部署文件核对。
- **T5 运行验证**：全量 pytest 基线、路由鉴权矩阵（139 路由）、真实 uvicorn 上的穿越/终端/鉴权/限流/沙箱/并发/冷启动实测，并交叉证伪团队假设。
- **Lead 交叉验证**：对全部 P0/P1 结论抽查代码原文；对 SPA 穿越、文件 API、MCP 配置链、终端、JWT、DB 连接、框架参数等关键链路独立复核。

证据强度约定：**【实测】**=运行验证；**【复现】**=有可重复 PoC；**【静态】**=代码路径推断（报告内均注明）。

---

## 3. 安全 / 多用户隔离 / 沙箱

### 3.1 关键结论

- 认证体系本身具备较多正确设计：会话归属 helper（`get_owned_session`）、单点登录 `token_version`、IP 绑定已移除、bootstrap 强制强口令、全局异常不泄堆栈、知识库独立 `get_knowledge_principal` 鉴权。
- 但**边界路由大量遗漏鉴权**：139 条路由中 78 条 JWT、33 条 knowledge 自定义鉴权、**27 条完全无鉴权 HTTP + 1 条无鉴权 WebSocket**（`T5` §3.1）。
- 多用户隔离在工作区/会话层总体正确（`workspace/{username}/session/{name}` + 归属校验），但文件 API 的路径校验不统一，形成多条绕过。

### 3.2 P0（立即修复，可被未认证者直接利用）

| ID | 位置 | 问题与证据 | 修复建议 | 工作量 |
| --- | --- | --- | --- | --- |
| SEC-01 | `easy_agent/api/terminal.py:225-239,255,279`；`app.py:69-70,550-551` | **未认证宿主 Shell**：`/terminal` 页面与 `/agent/terminal/ws` 无任何鉴权/Origin 校验；`pty.openpty()+os.fork()+execvpe("bash", ["--login"], os.environ.copy())`，cwd=`~`。实测无 token 连 WS 执行 `id` → `uid=1000(sututu)` 且属 sudo/docker 组【实测】 | 默认下线该功能（环境变量 `ENABLE_WEB_TERMINAL=false`）；如必须保留：`get_current_username` + 管理员白名单 + Origin 校验 + 一次性 ticket + 独立低权限用户/容器 + env 白名单 | S |
| SEC-02 | `easy_agent/app.py:644-666` | **未认证任意文件读取**：SPA catch-all `os.path.join(frontend_dist, full_path)` 无 `realpath` 校验。`GET /..%2f..%2f..%2f.env`、`/..%2f..%2fdata%2f.jwt_secret`、`/..%2f..%2fdata%2feasy_agent.db`、`/..%2f..%2feasy_agent%2fconfig%2fconfig.yaml`、`/etc/passwd` 实测均 200【实测，真实 uvicorn】 | 拒绝含 `..`/NUL/反斜杠路径；`Path.resolve()` 后强制 `is_relative_to(frontend_dist)`；用 `StaticFiles` 或仅允许白名单扩展名；SPA 回退只返回 `index.html`，不做任意文件映射；生产仅暴露反代、不直连后端 | S |
| SEC-03 | `easy_agent/api/auth.py:215-296`；`db/database.py:2008-2043`；`config/config.yaml:159-161` | **免密登录账号接管**（当前活动配置已开启）：只要策略开启，提交已存在用户名即直接签发该用户 token（`user_id` 被忽略）；用户名不存在则自动建号（`self_registration` 关闭也生效），仅硬编码拦截 `admin`。实测任意已存在用户名 → 200 + token → `/profile` 200【实测，隔离环境】 | 生产默认关闭；开启时必须校验外部 SSO 断言（HMAC + 时间戳/nonce）而非裸用户名；`user_id` 必填且必须匹配；admin/manager 一律禁止；加来源 IP 白名单与审计 | M |
| SEC-04 | `easy_agent/api/connectors.py:599-638,648-694`；`services/mcp.py:111-134,156-203`（`_expand_env_recursive` 在 163） | **普通登录用户 → 宿主机 RCE**：用户可通过 `PUT /agent/connectors/mcp/raw` 写入任意 `{"transport":"stdio","command":"bash","args":[...]}`，保存后 `_validate_servers → MultiServerMCPClient.get_tools()` 立即拉起该进程；且 `_expand_env_recursive` 会把 `mcp.json` 里的 `${EASY_JWT_SECRET}`/`${DEEPSEEK_API_KEY}` 展开进用户可控的 command/args。实测命令执行与密钥外带成功【实测】 | 用户侧禁止提交/编辑 `command/args/env/cwd`；stdio 连接器只允许管理员预置的 server id（固定配置）；自定义服务只允许 http/sse + 域名白名单；**用户 mcp.json 不做服务端 env 展开**；拉起进程放入沙箱/独立低权用户；校验前不执行命令，改为显式“测试连接”+审计 | M |

### 3.3 P1（高，需在本迭代修复）

| ID | 位置 | 问题与证据 | 修复建议 | 工作量 |
| --- | --- | --- | --- | --- |
| SEC-05 | `api/files.py:282-300` | `download_file` 直接 `upload_dir / file_path` + `FileResponse`，无 resolve 校验；`Path / 绝对路径` 会丢弃前缀。实测 `/etc/passwd` 200、跨用户 workspace 文件 200【实测】 | 统一走 `resolve_workspace_file()`；禁止绝对路径；按 DB 记录校验归属 | S |
| SEC-06 | `api/files.py:316-322`；`utils/file_parser.py:173-212` | `parse` 对任意 `file_path` 直接 `parse_file_content()` 返回正文（实测 `/etc/passwd` 1541B 200）【实测】 | 只允许本会话 `session_files` 记录过的路径；解析接口仅内部调用 | S |
| SEC-07 | `api/files.py:343-354,366-389` | `preview` 无有效 token 时不 401，而是 `get_or_create_default_user()`（即 admin）继续；`target=pdf` 分支在 `resolve` 校验前 `convert_to_pdf(full_path)` → `FileResponse`。实测未认证可读 default 用户工作区、绝对路径 docx→PDF 200【实测】 | 无有效身份直接 401；`token` 查询参数改一次性签名；`target=pdf` 前强制 resolve | S |
| SEC-08 | `api/prompts.py:27-39,42-55,58-71` | 三个路由**无鉴权**，`read/update` 无穿越校验。实测未认证读 base 外文件、在 base 外创建并写入 `.md`【实测】 | 加鉴权 + 管理员角色；`resolve()` 前缀校验；prompts 只读挂载；写操作审计/版本备份 | S |
| SEC-09 | `api/completion.py:69-110` | `POST /agent/completion/chat` **无鉴权、无限流**的 LLM 透传（可指定 system prompt、任意已配置模型、无 token 上限）【静态+路由矩阵】 | 加鉴权/API Key + 限流 + 输入输出长度上限；或仅内网监听 | S |
| SEC-10 | `middleware/auth.py:122`；`utils/auth.py:73-102` | 全部校验使用 `verify_exp=False`；`IDLE_LOGOUT_MINUTES=0`（默认）时签发**无 exp** token，仅 DB `token_version` 可失效。实测伪造 `exp=-2h` 的合法签名 token 仍 200【实测】 | `verify_exp=True` + 滑动续期；`never_expires` 仅白名单；短 TTL + refresh + jti 黑名单；登出后服务端拒绝 | S |
| SEC-11 | `config/mcp.json:24`（git 跟踪）；`README.md:259`；`tests/test_config.yaml:61`；`.env*`（644） | git 已跟踪文件含真实 MySQL 口令；`.env`/`.env.dev` 含真实 `DEEPSEEK_API_KEY`/`OPENCODE_API_KEY`/`WEB_SEARCH_API_KEY`，权限 644【实测】 | 立即轮换所有已暴露凭据；`git filter-repo`/BFG 清历史；示例只留占位；`.env*` chmod 600；公共连接器目录不含真实值 | M |
| SEC-12 | `personnel/repository.py:15,127,251`；`api/auth.py:463-493` | 人员新增/导入默认密码 `123456` 且回显；“公共空间管理员”可把任意非 admin 用户重置为固定默认口令 → 横向接管【实测/静态】 | 随机初始口令 + 首次登录强制改密；重置走一次性链接/审核；manager 重置需二次确认 + 审计 | M |
| SEC-13 | `middleware/rate_limit.py:16-25,44-61` | 仅 6 条路由受限；进程内计数（多 worker/pod 可绕过）；key 用 `request.client.host`（反代后所有用户共桶）；`raise HTTPException` 在 `BaseHTTPMiddleware` 中逃逸 → 实测第 20 次登录返回 **500**，随后新连接出现 ≥20s ReadTimeout（根因待专项复核）【实测】 | 返回 `JSONResponse 429 + Retry-After`；共享存储（Redis/DB）限流；按 `get_client_ip()`+账号双维度；覆盖全部写接口 | M |

### 3.4 多用户与沙箱专项

**多用户隔离评估：基本模型正确，局部路径未收口。**

- 正确项：会话 CRUD 用 `get_owned_session` 校验归属；聊天拒绝他人 session（404 不泄露存在性）；workspace/memories 按 `sanitize_username` 隔离；`external_dirs` 有 `_check_external_dir_safety` 防挂载他人子树；知识库有独立权限模型。
- 缺口：SEC-05/06/07（文件 API 路径未统一校验）、SEC-23（`/connectors/session`、`/chat/cancel` 未校验 session 归属）、SEC-24（skill_center/public_expert 目录名未校验）、SEC-20（本机其他 OS 用户可读 `.env`/日志/会话/DB）、SEC-11（凭据入库）、SEC-12（默认弱口令横向接管）。

**沙箱评估：设计正确、文件隔离有效，边界需补齐。**

| 项 | 结论 |
| --- | --- |
| bwrap 隔离 | 【实测】`/home`、`/root`、应用源码不可见；`/etc/shadow` 拒绝；env 与 `/proc/self/environ` 中秘密 0 行；workspace 可写；超时 124、输出截断生效 |
| Landlock 降级 | 【实测】宿主 `.env` 拒、写 `/tmp` 拒、symlink 逃逸拒；不可用时 fail-closed（127 未执行）。**缺陷**：使用系统 Python，venv 包不可用（office 技能脚本会失败）；无法隔离网络；无 PID namespace |
| 命令注入面 | 执行走 argv list + `shell=False`（`sandbox.py:245,363`）；无 `shell=True`/`os.system`/`eval`/`exec`（全仓静态扫描）【实测】 |
| 缺口 | 沙箱 `--share-net` 网络全通（可外带用户文件/探测内网）；无 CPU/内存/进程数 rlimit；模型可把每命令 timeout 设到 3600s；Web Terminal 直接绕过沙箱；`check_command_paths` 死代码且白名单过宽 |

### 3.5 P2/P3 安全项（择要）

- **SEC-14 P2** 存储型 XSS：workspace 内 SVG 经 `/files/preview` 以 `image/svg+xml` + `inline` 返回，且非静态分支缺 `X-Content-Type-Options`，可窃取 localStorage JWT（`files.py:35-76,128-147`；`app.py:434-437`）。
- **SEC-15 P2** 上传 DoS：`/files/upload` 无后端大小/类型限制；会话上传在 413 判断前 `await file.read()` 全量入内存（`files.py:238-279`；`sessions.py:388-428`）。
- **SEC-16 P2** SSRF：自定义连接器 `url` 由用户任意填写，保存/查看工具时服务端发起连接，可打内网/云元数据（`connectors.py:203-265,476-507`）。
- **SEC-17 P2** HITL 绕过：目录删除仅靠正则；实测 `rm -f -r`、`rm --recursive`、`python -c "os.remove(...)"`、`truncate`、`curl` 等不触发或可绕过审批（`hitl.py:10-32,65-68,121-132`）。
- **SEC-18 P2** 沙箱网络/资源（见 3.4）。
- **SEC-19 P2** JWT 进 URL 与访问日志（`files.py:330,349-351`；`scheduled_tasks.py:225-228`），日志 644。
- **SEC-20 P2** 本地权限：`.env*`、`logs/*.log`、`sessions/**/*.json`、`data/easy_agent.db` 均 644。
- **SEC-21 P2** 部署暴露：docker-compose 直曝后端 `22332:8000`；Dockerfile 无 `USER`（root）；k8s 加 `SYS_ADMIN`（bwrap 实际不需要）且 `replicas: 2` + `data: emptyDir`。
- **SEC-22 P3** 审计缺失：DB 审计只记知识库前缀，登录/文件/终端/连接器/prompt 写入均不入库（`knowledges/runtime/observability.py:93-101`）。
- **SEC-23 P3** IDOR：`/connectors/session` GET/POST、`/chat/cancel` 未校验 session 归属。
- **SEC-24 P3** 路径穿越（受限）：`skill_center.py:496,390-398`、`public_expert_service.py:393-412` 目录名未校验。
- **SEC-25 P3** 专家边界为软约束（关键词 + LLM 预检，fail-open 可选超时放行），不符合 FR 的“专家不执行高危操作”。
- **SEC-26 P3** 用户枚举（停用账号 403 vs 未知用户 401）与弱密码策略（min_length 3/4）。
- **SEC-27 P3** 安全头/CORS/文档暴露：CORS 默认 `*`；安全头只加在静态分支；`/docs`、`/openapi.json`、`/agent/config`、`/agent/settings/*` 未鉴权暴露配置。

---

## 4. 性能与并发

### 4.1 关键实测数据

| 测项 | 结果 | 来源 |
| --- | --- | --- |
| thinking 全量重发 | 35K 字符思考 → SSE 90.8MB；71K → **365MB**，hub 保留 1.4 亿字符（0.28~0.56GB） | T2 §3.1【实测】 |
| 同会话并发 | 新请求 0 条内容即被旧 hub EOF 结束；旧流被静默 cancel；共享 agent/同一 assistant 行 | T2 §3.2【复现】 |
| 流式落库 | 50KB 回复一轮阻塞事件循环 0.75s（重复 UPDATE 占 0.31s）；20K 字回复累计写 SQL 41.7× | T2/T5【实测】 |
| SessionLogger | 单会话 120 条 → 798KB 文件，累计写盘 ≈48MB（O(n²) 全量重写） | T2【实测】 |
| SQLite | 8 线程写加速比 1.00×；长写事务下另一进程 5.01s 后 `database is locked`；`journal_mode=delete` | T2/T5【实测】 |
| async 同步 DB | 50 次 `add_message` 阻塞事件循环 129.7ms（`to_thread` 对照 7.3ms） | T5【实测】 |
| 冷启动 | 3.0–3.4s；非 prod 默认真打 LLM 探测，失败时 lifespan +12.4s；首次 `/openapi.json` 283–390ms 阻塞 | T5【实测】 |
| Agent 缓存 | 250 会话后 `_session_agents`=100，但 4 个 side dict 各 250 条；FIFO 淘汰非 LRU | T2【实测】 |

### 4.2 问题清单

| ID | 级别 | 位置 | 问题与证据 | 修复建议 | 工作量 |
| --- | --- | --- | --- | --- | --- |
| PERF-01 | **P0** | `services/stream_processor.py:373-385`；`api/chat.py:221-238` | thinking 事件每 chunk 携带 `content`（增量）+ `full_content`（全量）；`_StreamHub.history` 保存每个 SSE frame。实测 71K 思考 → 365MB SSE / 数百 MB 内存，前端同量级 `JSON.parse` + 重渲染 | 去掉 `full_content`（前端已支持增量 append）；若需幂等重放，改 1–2s 节流快照或 `step+seq` 拉取；hub 加字节上限（如 4MB） | M |
| PERF-02 | P1 | `api/chat.py:256-306`；`session_runtime.py:41`；`agent_manager.py:540-557` | 同会话并发流：进程内 `acquire_stream` owner 相同不报忙；复用未 done 的 hub；`old.cancel()` 后 `hub.close()` 的 EOF 发给新订阅者。实测新流 0 条内容即结束 | 进程内互斥（409/排队）；每流独立 hub；`register_stream_task` 不再 cancel 旧任务；`/stream/live` 绑定流 id | S–M |
| PERF-03 | P1 | `checkpoints.py:63-93,127-145`；`streaming.py:940` | 每次 checkpoint 全量 pickle `storage/writes/blobs` 写库；`thread_id=session-message` 每轮新增 thread 且永不裁剪 → O(轮数²) 写 + blob 无界 | 只持久化最新 checkpoint；稳定 thread_id；会话结束裁剪；或启用官方 Postgres saver | M–L |
| PERF-04 | P1 | `streaming.py:962-966,1095-1096`；`database.py:1237-1238,1417-1473` | 每 500 字符全量序列化 + UPDATE；`update_last_assistant_message` 内部已转调 `_row`，调用方又调一次 → 同一条 SQL 执行两遍 | 删重复调用；内容增量列/结束一次写；按时间（≥2s）节流；DB 调用 `to_thread` | M |
| PERF-05 | P1 | `utils/session_logger.py:122-137`；`streaming.py:1099-1129` | 每条日志读整个 JSON → append → `json.dump(indent=2)` 全量重写；另每轮写一份全量会话快照且无清理 | 改 append-only JSONL；快照默认关；保留天数/大小清理；写文件移出事件循环 | S–M |
| PERF-06 | P1 | `api/sessions.py:406-462`；`api/files.py:238-279,316-322,374-389`；`services/document_convert.py:37-93` | async 路由里同步做 `await file.read()` 全量入内存、PDF/OCR 解析、LibreOffice `subprocess.run(timeout=180)`；一次请求卡住所有 SSE | 解析/转换丢 `asyncio.to_thread`/专用有界线程池；上传流式落盘 + 限额；结果缓存 | S–M |
| PERF-07 | P1 | `db/database.py:199-206,228-247`；`deploy/k8s/agent-deployment.yaml:27` | SQLite 单连接 + 全局 RLock 包住整个事务；未开 WAL/busy_timeout；多副本共享 SQLite 风险 | 立即 WAL + `busy_timeout` + `synchronous=NORMAL`；读路径独立连接；多 worker/多副本强制 MySQL；写路径批量化 | M |
| PERF-08 | P1 | `services/scheduler.py:102-148,193-252`；`deploy/k8s/*:27` | 每进程注册全量 cron；分布式锁默认 `false`；k8s replicas=2 → 同一任务执行 N 次 | 多副本强制开锁（启动校验）；持久 jobstore + leader 选举；文档/helm 标注 | S–M |
| PERF-09 | P2 | `agent_manager.py:19,93-107,397-400` | 淘汰为 FIFO 非 LRU；只清理 `_session_agents`，4 个 side dict + `_session_create_locks` 无界；记忆更新成功即驱逐 → 每轮重建 Agent | `OrderedDict` + move_to_end；同步清理 side dict；锁对象回收；记忆懒加载 | S |
| PERF-10 | P2 | `frontend-vue2/src/components/ChatMessage.vue:742-744`；`mixins/chatSession.js:39-46,960-984` | 每 chunk 替换消息对象触发全量重渲染；`renderMarkdown` 模板内直接调用、无 memo → 每帧全文 marked+highlight | 按内容缓存 markdown；流式期间纯文本；rAF/50ms 合帧；只更新变化 block | M |
| PERF-11 | P2 | `utils/session.py:25-37`；`db/database.py:984-1025`；`api/chat.py:313-376` | `get_owned_session` 用 `get_session()` 加载全部消息并逐条 `json.loads`；轻量接口（status/live/文件树）重复触发 | 新增 `get_session_owner()` 只查 username/workspace_name；`get_session` 不再默认 SELECT legacy messages | S |
| PERF-12 | P2 | `services/mcp.py:266-277,302-391` | `_mcp_clients` 无上限、从不 close；每新会话首次建 Agent 重新加载 MCP（stdio 起子进程/网络连接）→ 首消息延迟秒级 | 按单 server 缓存复用；LRU + close；预热/后台加载；建 Agent 路径可超时可缓存 | M |
| PERF-13 | P2 | `api/chat.py:199-238` | `_StreamHub` 只限条数不限字节；慢消费者 `QueueFull` 直接 EOF；单流 134MB 实测 | 见 PERF-01：字节上限 + 压缩 + 慢消费者重连策略 | S–M |
| PERF-14 | P2 | `knowledges/runtime/worker.py:102-126` | 每副本每 5s 轮询待处理文档，无 leader 选举 → N 副本 N 倍 Ragflow 调用 | 分布式锁/leader 选举；延长间隔；按 base 分片 | S |
| PERF-15 | P2 | `db/database.py:987-1004,1075-1091` | 消息已迁移行表，但 `get_session` 仍固定 SELECT legacy `sessions.messages`；改标题仍回写整列 blob | 不再 SELECT legacy；标题拆 `update_session_title`；message_count 维护列/GROUP BY | S |
| PERF-16 | P2 | `streaming.py:724-805`；`memory_manager.py:233-320` | 每轮无条件提交 2 个记忆更新任务（额外 2 次 LLM 调用），用默认无界线程池；成功后驱逐 Agent | 记忆更新节流/按需；独立有界线程池；不驱逐 Agent（mtime 懒加载） | M |
| PERF-17 | P3 | `app.py:14-17,184-353`；`initialization/log.py:68-88` | 导入 app 即初始化；日志 handler 每条 flush+fstat；启动重活同步 | 引入 app factory；`QueueHandler+QueueListener`；readiness 等初始化完成 | S–M |

### 4.3 并发与多实例

- 同会话并发（PERF-02）是**已复现的正确性缺陷**，不是理论风险；优先修。
- `_session_agents` / `_session_stream_tasks` / `_session_expert` / `_session_connectors` / SSE hub 均为进程内状态（`agent_manager.py:18,32`；`api/chat.py:177,184`）→ 多副本下 HITL 恢复、断线重连、取消可能落到“无状态”副本。
- `acquire_stream` 是“先读后写”的 TOCTOU（`session_runtime.py:37-50`），跨副本并发时两实例都可能认为自己拿到流锁；需要 DB 原子条件更新（`UPDATE ... WHERE stream_owner='' OR stream_until<now`）。
- `distributed_lock` 实现本身实测正确（互斥/不可重入/TTL 抢占/多进程唯一赢家），问题在默认关闭与部署未强制。
- `configured_summarization` 临时替换模块全局 + `os.fork()` 终端共存于多线程进程，属于潜在死锁/串扰面（P3，若未来建 Agent 移入线程池则升级）。

---

## 5. 框架规范一致性（DeepAgents / Skills / MCP）

### 5.1 合规性判定

| 维度 | 判定 | 说明 |
| --- | --- | --- |
| `create_deep_agent` 装配 | **符合**（0.6.8） | 使用公开参数 `skills/memory/permissions/interrupt_on/subagents/checkpointer/store/middleware/backend`；实测同参建图 `CREATE_OK` |
| Backend | **符合** | `CompositeBackend` + `LocalShellBackend` + 自定义 `PathTranslatingShell`（协议实现正确） |
| HITL | **部分符合** | 使用 `interrupt_on` + `HumanInTheLoopMiddleware`，但依赖正则、且通过 monkeypatch 注入工具描述 |
| Skills | **部分符合** | 使用官方 `skills=` + progressive disclosure，但公共技能不在 sources（复制式“启用”），frontmatter 未校验，subagent 不继承 skills 的语义处理不当 |
| Memory | **符合** | 官方 `memory=` + `MemoryMiddleware`，`/memories/AGENTS.md` 路由实现正确 |
| Summarization | **绕行** | 官方 0.6.8 未暴露阈值参数，仓库替换私有工厂（可用但脆弱） |
| Persistence | **部分符合** | 官方 Postgres saver已接一半；默认自研 pickle MemorySaver（私有属性依赖 + 写放大） |
| MCP client | **部分符合** | 使用官方 `langchain-mcp-adapters`；工具名无前缀/冲突校验、默认 sse 而非 streamable_http、无超时透传 |
| MCP server（`mcp-server/`） | **方向正确** | FastMCP + streamable_http + Bearer 鉴权；但错误用文本返回而非 `isError=true`，缺 structuredContent |

### 5.2 问题清单

| ID | 级别 | 位置 | 问题与证据 | 修复建议 | 工作量 |
| --- | --- | --- | --- | --- | --- |
| FW-01 | P1 | `pyproject.toml:14`；`agent.py:20-21`；`hitl.py:100`；`summarization.py:47` | `deepagents>=0.1.0` 无上界；代码绑定 3 处 0.6.8 内部实现。PyPI 0.7.x 要求 `langchain>=1.4.6`，且 0.7 起 `write_todos` 变 opt-in、`delete` 工具新增、`FilesystemMiddleware` 签名变化 | 锁 `deepagents>=0.6.8,<0.7`、`langchain>=1.3,<1.4`；加“框架适配冒烟测试”（建图 + 断言关键工具/中间件存在）进 CI；0.7 升级单独立项 | S |
| FW-02 | P1 | `api/chat.py:674,707`；`streaming.py:1352`；`checkpoints.py:127-131` | HITL resume 直接信任前端 `thread_id`，只校验 session 归属不校验 thread 绑定；Postgres saver 全局共享时可跨会话读取/续跑他人 checkpoint | resume 前强校验 `thread_id.startswith(f"{session_id}-")`；优先从 DB `pending_approval` 取 thread_id | S |
| FW-03 | P1 | `services/mcp.py:270-277,373`；`agent.py:487-493` | MCP 工具名直通（无 server 前缀、无冲突校验），ToolNode 同名 last-wins 静默覆盖；HITL 按工具名匹配 | `tool_name_prefix=True`；加载后与内建工具名（ls/read_file/write_file/edit_file/glob/grep/execute/task/write_todos）及跨 server 去重，冲突拒绝/重命名并告警 | S |
| FW-04 | P1 | `hitl.py:94-114`；`agent.py:92-94,412-413` | 全局 monkeypatch `FilesystemMiddleware.__init__` 注入 execute 描述；自检只证明 patch 执行过，不验证上游 kwarg 仍生效（假阳性） | 改用官方 `HarnessProfile(tool_description_overrides=...)`；或自检时真实实例化一次中间件断言描述生效 | S |
| FW-05 | P1 | `summarization.py:44-70` | 建图期间替换未导出的 `deepagents.graph.create_summarization_middleware`；丢掉官方 `summary_prompt/token_counter` 参数；并发建图存在还原竞态 | 保留绕行但加版本护栏 + 缺失时显式告警 + 单测断言阈值生效；向上游提议公开参数 | S |
| FW-06 | P2 | `agent.py:507,895-906`；`api/skill_center.py:490-560` | 公共 7 个技能不在 `skills=` sources 中；靠复制到 `workspace/{user}/skills/` 启用 → 更新不同步、未启用会话看不到技能元数据 | 官方多源 `skills=["/skills/", "/user-skills/"]`（后者 last-wins），公共技能只读挂载；“启用/停用”改偏好过滤；启动期校验 frontmatter | M |
| FW-07 | P2 | `subagents.py:3-28` | 自定义 subagent 未声明 skills（官方明确不继承）；`office-writer` 硬编码 `/user-skills/docx/...`；未收窄 tools（继承父代理全部工具，含 MCP 写工具） | subagent 显式 `skills`、显式 `tools` 收窄；描述改为“若技能存在” | S |
| FW-08 | P2 | `agent.py:267,662-674`；`streaming.py:941` | `max_steps` 只折算 call-limit（默认静默结束/continue）；框架 `recursion_limit=9999`，子代理不受 max_steps 约束 | 显式 `exit_behavior="error"` 或明确 SSE 错误；`stream_config["recursion_limit"]` 收敛；文档说明语义 | S |
| FW-09 | P2 | `checkpoints.py:26-93` | 自研 saver pickle `MemorySaver` 私有属性；无 `delete_thread`/裁剪；每次 put 同步全量写 | 生产用 `AsyncPostgresSaver`，单机用 `SqliteSaver`；删除自研或仅降级并告警 | M |
| FW-10 | P2 | `services/mcp.py:226-256,248-254` | URL-only 配置默认 `transport="sse"`（旧传输）；未透传 `timeout/sse_read_timeout`、未支持 OAuth | 默认 `streamable_http`（兼容别名）；透传超时；OAuth 单独立项 | S |
| FW-11 | P2 | `stores.py:40-52,99-132`；`agent.py:495-515` | 自研 `SqlStore` 语义不完整（filter/match/排序简化），当前几乎没有内建消费者 | 删除并传 `None`，或改用官方 store 并补全语义 + 一致性测试 | M |
| FW-12 | P3 | `hitl.py:34,121-132`；`agent.py:467-493` | `interrupt_on` 预置了可能未注册的工具（静默无效）；目录删除在 backend 直接拒绝、不走 HITL | 按实际注册工具动态构造；策略拒绝在文档/提示词中显式化 | S |
| FW-13 | P3 | `agent.py:654-661` | `LoggingMiddleware` 只加在主 agent；子代理无异常兜底，MCP `ToolException` 会中断 `task` | 给每个 subagent 配 `middleware=[LoggingMiddleware(...)]` 或 `handle_tool_errors` | S |
| FW-14 | P3 | `mcp/jmfx_mcp_server.py:691-707`；`mcp-server/easy_mcp_server/*` | MCP server 把异常包装成 JSON 文本“成功”返回（`isError=false`），模型会把失败当成功 | 改 `raise ToolError(...)`（FastMCP 输出 `isError=true`）；补 structuredContent | S |

---

## 6. 代码架构与工程化

### 6.1 问题清单

| ID | 级别 | 位置 | 问题与证据 | 修复建议 | 工作量 |
| --- | --- | --- | --- | --- | --- |
| ARCH-01 | **P0** | `tests/integration/test_sandbox_isolation.py:166`；`easy_agent/fs_safety.py:8` | 导入已迁移的 `_is_within` → 默认 `pytest` 0.78s 整体 Interrupted（900 collected / 1 error）。全量实测 904 testcase：890 passed / 10 failed / 13 errors；12 个 error 来自 `test_auth_session_expiry` 引用已删除的 `_user_activity_cache` | 修正 import；坏用例改显式 skip 或删除；CI 加 `pytest --collect-only` 门禁；修复测试漂移恢复绿色基线 | S |
| ARCH-02 | **P0** | `pyproject.toml:39,64-68`；`Dockerfile:59,72`；`easy_agent/skills.py:20-35` | `easy-knowledge-worker` 指向不存在模块（实测 `ModuleNotFoundError`）；无 MANIFEST/package-data（SOURCES.txt 无 config/prompts/static）；Docker 只 COPY `easy_agent/`，根 `skills/`、`prompts/`、`experts/` 不在镜像却 mkdir 空目录 → 镜像内技能/预置专家为 0 | 修正/删除错误入口；补 `MANIFEST.in`/`package-data`；Dockerfile 显式 COPY 根资源；`.dockerignore` 放行 `skills/**`、`prompts/**`、`experts/**` 的 md；启动期技能数 <1 告警 fail-fast | M |
| ARCH-03 | P1 | 见 T4 §4.1 全名单 | 非包模块图 177 模块/347 边，2 个 SCC（39+2 模块）：`api.chat → knowledge_impl → knowledges.api → api.chat`、`config → services.agent_manager → agent → config`、`agent_manager ↔ tools.expert_writer`、`db.database ↔ knowledges.core.schema` | 先砍 3 条回边（config 注入化、expert_writer 去反向依赖、knowledges 不 import api）；后续用 ports/DI 收口 | L |
| ARCH-04 | P1 | `db/database.py:80-2538`；`knowledges/service.py:448-2656` | `Database` 单类 114 方法 / 2458 行；`KnowledgeService` 49 方法 / 2208 行；`resume_stream_generator` 489 行、`chat_stream_generator` 413 行；`api/chat.py:chat_stream` 245 行；`app.py:lifespan` 182 行 | 按域拆 repository（users/sessions/messages/files/experts/tasks）；Database 只留连接/事务/迁移；streaming 拆“编排器 + 纯处理器 + 持久化 + 副作用适配器” | L |
| ARCH-05 | P1 | `streaming.py:808,1221`；`knowledges/chat/streaming.py:20`；`api/expert.py:711,997`；`api/chat.py:177-184`；前端 `api/chat.js:83`、`api/expert.js:148`、`features/knowledge/api.js:231` | 后端 4 处独立 SSE 生产、前端 3 处消费；实际 15 个事件（含 `tool_call_delta`、`knowledge_evidence`），`docs/requirements-agent.md` 缺 2 个；CLAUDE.md 仍列已废弃 `user_input_required` | 抽唯一 `format_sse` + 事件注册表（Enum/JSON Schema）+ golden test；补 `docs/sse-events.md`；expert/knowledge 复用同一管线 | M |
| ARCH-06 | P1 | `easy_agent/skills/{docx,pptx,xlsx}/scripts/office/**`；根 `skills/...`；`.agents/skills/**` | office 12 个文件在 3 个格式目录 × 2 棵树完全一致 = **6 份副本**；`.agents/skills` 26 个开发技能 592K 不进运行时；守护测试只校验 `base.py` | 单一源 + 构建/安装期同步脚本；哈希校验全部 12 文件；`.agents/skills` 移出主仓或标注非运行时 | M |
| ARCH-07 | P1 | `frontend-vue2/`（Vue 2.7，88 文件 38.6k 行）；`frontend/`（Vue 3.5，55 文件 29.2k 行）；`app.py:74-79`；`Dockerfile:7-21` | 后端/镜像只构建 `frontend-vue2/dist`，但 `frontend/`（Vue3）仍在更新；两套组件/API 层高度重复；`App.vue` 3292 行、8 个布尔视图开关、无 router/store；`ChatInput.vue` 4397 行 | 做唯一基线决策（见第 8 章）；另一套归档/删除；期间 CI 明确只构建一套；Vue2 引入 vue-router@3 + 按域拆 store | L |
| ARCH-08 | P1 | `app.py:558-628`；`api/expert.py:763-997`；`api/connectors.py:193`；`knowledge_impl.py:59-84` | 137 路由三套前缀（`/agent/*`、`/api/personnel`、app 级 `/api/*`）；`/agent/live`+`/api/live`、`/agent/ready`+`/api/ready` 重复别名；expert 6 路由裸挂根；`response_model` 约 50/137；handler `return {}` 72 处；内联 Pydantic 模型 20 个；`ConnectorInstallRequest.validate` 字段遮蔽告警 | 制定 API 规范（前缀/版本/错误体/分页/命名）；新增路由强制 `response_model`；`/api/*` 别名标 deprecated 并定移除版本；expert 路由拆文件加前缀 | M |
| ARCH-09 | P1 | `config.py:523-628`；`config/config.yaml` vs `config-example.yaml`；25 处 `os.getenv` | 手写 `from_yaml` + `${VAR:-default}` 递归解析；25 个 `os.getenv` 绕过 Config；config.yaml/example 漂移 27/23 键、8 处同键不同值；死配置 `knowledge.implementation`；无未知键/必填校验 | 引入 Pydantic Settings 统一映射，禁止 `os.getenv`；启动校验未知键/必填 fail-fast；删死配置；长期支持热更 | M |
| ARCH-10 | P1 | 仓库根（无 `.github/workflows`、无 ruff/black/mypy/pre-commit）；`frontend-vue2` 无 eslint | 无 CI、无 lint/format/type 门禁；`release_preflight.py` 仅 3 条正则 | 加最小 CI：`ruff check` + `pytest`（含 collect）+ 前端 build/lint/unit；逐步加 mypy 白名单与 pre-commit | M |
| ARCH-11 | P1 | `tests/conftest.py:24`；`tests/api/test_prompts.py:15`；`easy_agent/prompts/unit_test_prompt.md` | `collect_ignore` 永久隐藏 2 个坏/联网用例；无并发测试（threading/gather 在 tests 0 命中）；测试向源码树写文件残留；高风险模块（调度/沙箱/SSE resume/多实例缓存）缺回归 | 坏用例显式 skip 并登记 issue；补关键路径测试；测试产物统一 `tmp_path`；`--strict-markers` + 覆盖率阈值 | M |
| ARCH-12 | P2 | `easy_agent/compat/`、`db/repositories/`、`services/stream/`（空目录）；57 个孤儿 `.pyc`；`static/swagger-ui` 1.8MB；`plan.md` 0B | 死代码/死资源/空目录 | 删除；`.gitignore` 已覆盖 `__pycache__`；或真正挂载 Swagger | S |
| ARCH-13 | P2 | `CLAUDE.md`、`AGENTS.md`、`README.md`、`docs/API接口文档.xlsx` | 文档全面漂移：声称无 Docker/17 技能/`/skills/` 只读/Vue2.7 前端等，均与现状不符；API xlsx 68 行不含 connectors/skill-center/personnel/knowledge 等 | 修正模块地图与前端事实；API 文档纳入构建产物并 diff 校验；补“框架适配点清单” | S |
| ARCH-14 | P2 | `pyproject.toml:19,33,22,49,34,43-44,80-81` | `httpx` 重复声明且范围冲突；`requests` 与 `httpx` 并存；`openpyxl` 重复；dev 依赖两套版本；核心框架无上界；`mysql-mcp-server` 作为运行时依赖但实际用 `uvx` 拉起 | 依赖去重/收敛；统一 dev 依赖；核心框架加兼容上界；剔除不必要的运行时依赖 | S |
| ARCH-15 | P2 | `services/agent_manager.py:18-45`；`api/chat.py:177-306`；`deploy/k8s/agent-deployment.yaml:27,67-95`；`db/database.py`（无 tenant） | 多副本状态全在进程内；k8s replicas=2 未强制 MySQL、`data` 为 emptyDir、skills/experts 未挂载；DB 无 `tenant_id` | 设计文档先行：会话/流状态外置（Redis/DB+outbox）；SSE 走共享 pub/sub；k8s 增加 skills/experts PVC；多租户加 tenant_id + 行级隔离 | L |
| ARCH-16 | P2 | `db/database.py`（启动期 `_ensure_column`/ALTER）；`scripts/migrate_sqlite_to_mysql.py` 811 行 | 启动期 DDL 自动迁移，无版本化 migration，不可审计/回滚；滚动发布并发 ALTER 风险 | 引入 Alembic 或等价；禁止启动期 DDL；迁移脚本纳入 CI | M |
| ARCH-17 | P2 | `mcp/`（88K）、`mcp-server/`（71MB 含 `.venv`） | 两个 MCP 子项目与主仓混放，无 workspace 声明；`jmfx` 主代码 0 引用 | 拆独立仓库或 uv workspace；清理 `.venv`；加体积检查 | S–M |
| ARCH-18 | P2 | `services/agent_manager.py:323-395`；`hitl.py:34`；`frontend-vue2/src/utils/toolPresentation.js:44-95` | 新增一个 Agent 工具需改 4–6 处（tools/、agent_manager 硬编码 append、hitl 名单、前端卡片映射、测试文档）；新增技能需同步 2 棵树 | 工具注册表化（schema/HITL 标记/前端元数据集中）；技能同步脚本；部署携带资源 | M |
| ARCH-19 | P3 | 全后端 | `except Exception` 269 处、`# noqa` 111 处、`print(` 141 处（多测试/脚本）、`except-pass` 92 处；`pickle.loads` 1 处（checkpoints） | 统一 logger；收窄异常或重抛；ruff 规则分阶段治理 | S |

### 6.2 正面发现（建议保留）

- bwrap/Landlock/fail-closed 三档沙箱真实有效，且实现细节到位（env 白名单、`/etc/passwd` 归一化、venv 中性挂载、输出截断、超时处理）。
- 会话归属 helper、知识库独立鉴权、zip 导入校验、bootstrap admin 强口令、全局异常不泄堆栈、CORS 生产强制配置。
- `distributed_lock` 语义正确（原子插入 + 过期抢占 + owner 校验）。
- Agent 装配使用 0.6.8 公开 API（无“现在就崩”的装配错误）。
- 测试覆盖量可观（890 passed），关键业务路径有集成测试。

---

## 7. 改进清单与执行计划

> 原则：**先止血（P0）→ 再安全加固（P1）→ 再性能并发 → 最后架构/规范治理**。安全类 P0/P1 与性能 P0 建议优先于任何功能开发。

### Batch 0 · 止血（建议立即执行，约 1–2 天）

| 项 | 内容 | 验收标准 |
| --- | --- | --- |
| B0-1 | SEC-01 Web Terminal：默认下线（环境变量开关） | 无 `ENABLE_WEB_TERMINAL` 时 `/terminal`、`/agent/terminal/ws` 返回 404/403；PoC 失败 |
| B0-2 | SEC-02 SPA 兜底：realpath 校验 + 拒绝 `..` | 全部穿越 PoC（含 `.env`、`.jwt_secret`、DB、config.yaml、`/etc/passwd`）返回 404 |
| B0-3 | SEC-03 免密登录：默认关闭 + 已存在用户需 `user_id` 匹配 + admin/manager 禁止 | 未开启策略 403；开启时裸用户名不能登录；自动化测试覆盖 |
| B0-4 | SEC-04 MCP：用户 mcp.json 禁止 `command/args/env`；去掉用户配置的 `${ENV}` 展开；stdio 仅管理员白名单 | 普通用户提交 stdio 配置返回 400；`${EASY_JWT_SECRET}` 不再展开；PoC 失败 |
| B0-5 | ARCH-01 测试收集错误 + `_user_activity_cache` 漂移 | `pytest tests/ --collect-only` 通过；`pytest tests/` 无 error（允许已知环境性 skip） |

### Batch 1 · 安全加固 P1（约 3–5 天）

SEC-05/06/07（文件 API 统一 `resolve_workspace_file` + 401 不再降级）、SEC-08（prompts 鉴权+校验）、SEC-09（completion 鉴权+限流）、SEC-10（JWT 过期校验+滑动续期）、SEC-11（密钥轮换+清历史+600 权限）、SEC-12（随机初始口令+首次改密）、SEC-13（限流 429 + 共享存储 + 覆盖写接口）；顺带 SEC-19/20/21（日志脱敏、文件权限、部署最小暴露）。

验收：`_analysis` 的 T3 PoC harness 全部由 PASS 转为 FAIL（被拒）；路由鉴权矩阵中“无鉴权写接口”清零；安全头/CSP 覆盖所有响应。

### Batch 2 · 性能与并发（约 3–5 天）

PERF-01（thinking 去掉 full_content + hub 字节上限，最高优先）、PERF-02（同会话互斥 + 独立 hub）、PERF-03（checkpoint 只存最新/稳定 thread_id）、PERF-04（去重复 UPDATE + 时间节流 + to_thread）、PERF-05（JSONL + 清理）、PERF-06（解析/转换/上传移出事件循环 + 限额）、PERF-07（WAL/busy_timeout，生产强制 MySQL）、PERF-08/CONC（多副本强制分布式锁）、PERF-09/11/12/13。

验收：相同 71K thinking 场景 SSE 字节 < 2MB；事件循环 p95 阻塞 < 50ms；20 并发同会话返回 409/排队且消息不串写；SQLite 8 线程写加速比 > 3×（WAL）或明确强制 MySQL；`_session_*` side dict 有界。

### Batch 3 · 框架规范与依赖治理（约 3–5 天）

FW-01（锁版本 + 建图契约测试）、FW-02（HITL thread 绑定）、FW-03（MCP 前缀/冲突）、FW-04/05（HarnessProfile 替代 patch、摘要护栏）、FW-06/07（skills 多源、subagent 收窄）、FW-08（max_steps/recursion_limit）、FW-09/10（官方 saver、streamable_http）、ARCH-14（依赖去重）。

验收：CI 中新增 `framework-contract` 测试；`grep -r "deepagents.graph"` 仅剩受护栏包；MCP 同名工具冲突用例返回明确错误；依赖树无重复/无未知来源。

### Batch 4 · 架构与工程化（约 1–2 周，需设计文档）

ARCH-02（打包/部署修复）、ARCH-03/04（砍回边、拆 Database/streaming）、ARCH-05（SSE 契约统一）、ARCH-06（技能单一源）、ARCH-07（前端基线收敛）、ARCH-08（API 规范/response_model）、ARCH-09（配置收口）、ARCH-10/11（CI + 测试体系）、ARCH-15（多实例状态外置设计）、ARCH-16（版本化迁移）、ARCH-18（工具注册表）。

验收：CI 全绿（ruff + pytest + 前端 build/unit）；import 图 SCC 降到 0–1；新增工具/技能/专家的改动点 ≤3；Docker 镜像启动后 `discover_skills()` ≥ 7、预置专家 ≥ 5；README/CLAUDE/AGENTS 与代码一致。

### 建议的 PR 拆分

每个 Batch 内部按“一个 P0/P1 一个 PR”拆分，禁止混入重构；每个安全 PR 必须附回归测试（把对应 PoC 固化为测试用例）；架构类 PR 必须先出设计文档（`docs/design/`）再实施。

---

## 8. 需要你决策的事项

1. **Web Terminal 去留**：建议默认下线；若业务确需，按“鉴权 + 低权用户 + 独立端口 + env 白名单”加固。
2. **前端唯一基线**：后端当前只托管 `frontend-vue2/dist`，建议保留 Vue2.7 为唯一基线、归档 `frontend/`（Vue3）；或反向决策后同步修改后端/Docker。
3. **部署数据库路线**：生产多副本建议强制 MySQL（代码已禁止静默降级）；单机/内网可保留 SQLite + WAL，但需接受单写者吞吐上限。
4. **免密登录**：生产建议关闭；门户 SSO 场景改为“带 HMAC/时间戳的 SSO 断言”而非裸用户名。
5. **执行范围**：按 Batch 0→4 顺序，还是先做安全（0+1）再评估性能/架构。

---

## 9. 附录：证据索引

| 报告 | 内容 | 条数 |
| --- | --- | --- |
| `docs/audit/evidence/T1-framework-conformance.md` | DeepAgents 官方文档对照、安装包源码核对、框架适配/版本漂移、skills/MCP 规范 | 17 |
| `docs/audit/evidence/T2-performance-concurrency.md` | 性能基准、并发复现、缓存/DB/日志/checkpoint 写放大 | 18 |
| `docs/audit/evidence/T3-security-multiuser-sandbox.md` | 安全 PoC 33 项、多用户隔离、沙箱实测、部署暴露 | 27 |
| `docs/audit/evidence/T4-architecture.md` | import 图/规模统计/重复代码/单例/部署/文档漂移 | 25 |
| `docs/audit/evidence/T5-runtime-evidence.md` | 904 测试基线、139 路由鉴权矩阵、真实 uvicorn 穿越/终端/限流/沙箱/并发/冷启动实测 | 运行证据 |

原始工作稿与复现脚本：`_analysis/`（`T*.md`、`t*_*.py/sh`）；运行证据 JSON：`/tmp/ea-audit/`（临时目录，重启后可能丢失）。


---

## 10. 执行状态（2026-10-17 更新）

用户确认执行范围：**Batch 2（性能与并发）、Batch 3（框架规范治理）、Batch 4（架构与工程化）**；
Batch 0/1（安全 P0/P1）本轮未选择执行；Web Terminal 暂不处理；前端双基线同时维护；生产 MySQL + 单机 SQLite WAL。

### 10.1 Increment 1（已完成，详见 `docs/audit/2026-10-17-increment1-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-01 测试可收集性 | ✅ | `pytest --collect-only`：926 collected / 0 error（原 900 + 1 error）；沙箱测试 15 项恢复并通过 |
| PERF-01 thinking O(n²) | ✅ | full_content 改为首包/1.5s/8K 增长快照；hub 历史 4M 字符上限 + deque O(1) 淘汰 |
| PERF-02 同会话并发流 | ✅ | 第二并发流返回 409；不再 cancel 旧任务；新增并发互斥测试 |
| PERF-04 流式落库写放大 | ✅ | 删除 7 处重复 UPDATE；增量落库改「字符 + 时间」双阈值 |
| PERF-07 SQLite | ✅ | WAL + synchronous=NORMAL + busy_timeout=30000 |
| PERF-09/11 | ✅ | Agent 缓存 LRU + side dict 清理；轻量归属查询 `get_session_owner()` |
| FW-01 | ✅ | 锁 `deepagents 0.6.8 ~ <0.7` / `langchain 1.3 ~ <1.4`；新增框架契约测试 6 项 + `docs/framework-integration.md`；`uv lock --check` 通过 |
| FW-02 | ✅ | HITL resume 校验 `thread_id` 与 session 绑定 |
| FW-03 | ✅ | MCP 工具名与内建工具冲突显式跳过 + client 缓存清理 |
| ARCH-02 | ✅ | 修正 console script、package-data、MANIFEST、Dockerfile、.dockerignore |
| 全量回归 | ✅ | **914 passed / 1 skipped / 0 failed / 0 error（227.9s）**；基线为 890 passed / 10 failed / 13 errors + collection error |

> 说明：`test_auth_session_expiry.py` 因针对已移除的 `_user_activity_cache` 编写，本轮以**带原因的模块级 skip** 处理（避免隐藏用例式 collect_ignore），待按新 DB 持久化契约重写（ARCH-11）。

### 10.1b Increment 2（已完成，报告：`docs/audit/2026-10-17-increment2-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| PERF-05 会话日志 O(n²) | ✅ | SessionLogger 改 append-only JSONL（旧 JSON 自动迁移）；每轮全量 `logs/sessions/*.json` 快照默认关闭（`EASY_WRITE_SESSION_CONTEXT=1` 开启） |
| PERF-06 上传/解析阻塞事件循环 | ✅ | 上传改分块流式落盘 + 边读边限流（超限清理半成品）；parse/LibreOffice 转换/解析清单改 `asyncio.to_thread` |
| PERF-08 调度多副本重复执行 | ✅ | 多 worker/副本且分布式锁关闭时 **fail-closed 不注册定时任务**（`EASY_ALLOW_DUPLICATE_SCHEDULER=1` 可放开） |
| PERF-03 checkpoint 全量快照 | ✅ | 每 thread 保留最近 2 个 checkpoint、最多 3 个 thread，清理无引用 writes/blobs |
| FW-05 摘要工厂护栏 | ✅ | 工厂缺失时显式告警 + 降级，不阻断建图 |
| FW-07 subagent 规范 | ✅ | 显式 `skills` + 异常兜底 middleware，不再依赖“官方不继承”的隐含行为 |
| FW-08 预算逃逸 | ✅ | call-limit `exit_behavior="error"`；`recursion_limit=max(2*max_steps,50)` |
| 架构设计文档 | ✅ | 新增 `docs/design/`：模块重构、SSE 事件契约、多实例状态外置、技能多源（ARCH-03/04/05/15、FW-06 设计先行） |
| ARCH-10 CI/门禁 | ✅ | 新增 `.github/workflows/ci.yml`（ruff 致命规则 + pytest collect/test + 双前端 build）与 `[tool.ruff]` 配置；本地 `uvx ruff check .` 通过 |

### 10.1c Increment 3（已完成，报告：`docs/audit/2026-10-17-increment3-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-12 死代码清理 | ✅ | 删除计划文件/测试残留/3 个空目录/未挂载的 swagger-ui（1.8MB） |
| ARCH-14 依赖收敛 | ✅ | 移除未使用 `requests`、`mysql-mcp-server`；`uv lock` 117→115 包，check 通过 |
| ARCH-05 SSE 契约 | ✅（第一步） | 新增 `services/sse.py` 事件注册表 + 唯一 `format_sse`；`docs/sse-events.md`；契约测试扫描 6 个生产端并与文档同步 |
| ARCH-06 技能单一源 | ✅（第一步） | `scripts/sync_skills.py --check` + 3 格式全文件树 sha256 测试；包内副本保留为 pip 回退 |
| PERF-15 legacy JSON 读取 | ✅ | `get_session` 不再默认 SELECT 大字段；标题更新走 `update_session_meta` |
| ARCH-11 测试体系 | ✅（部分） | 移除失效 `collect_ignore`；确认测试不再写源码树 |
| 全量回归 | ✅ | **933 passed / 1 skipped / 0 failed / 0 error（236.9s）** |
| PERF-16/17 | ✅ | 记忆更新独立有界线程池；日志 handler 用 `stream.tell()` 增量计数（不再每条 flush+fstat） |

### 10.1d Increment 4（已完成，报告：`docs/audit/2026-10-17-increment4-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 1 砍回边 | ✅ | 新增 `runtime_config`；config/middleware/session_uploads/expert_writer 去反向依赖；`resolve_context_length` 迁至 `model.py` |
| 循环依赖度量 | ✅ | 环内模块 **25 → 18**（审计基线 39+2 → 当前 16+2，累计 -55%） |
| 新增测试 | ✅ | runtime_config 3 项（目录解析/显式 config/invalidator 注册表）；expert_writer 契约更新 |
| 全量回归 | ✅ | **936 passed / 1 skipped / 0 failed / 0 error（239.5s）** |

### 10.1e Increment 5（已完成，报告：`docs/audit/2026-10-17-increment5-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 收尾回边 | ✅ | 新增 `db/schema_registry.py` 断 `db.database ↔ knowledges.core.schema`；SCC **2 → 1**，环内模块 18→16（基线 41 累计 -61%） |
| ARCH-11 测试门禁 | ✅ | `--strict-markers`；DB 测试显式注册领域 schema |
| ARCH-13/09 文档治理 | ✅ | 新增 `docs/architecture-current.md`；CLAUDE.md/AGENTS.md 过时声明；requirements SSE 事件清单补齐 |
| 全量回归 | ✅ | **936 passed / 1 skipped / 0 failed / 0 error（237.9s）** |

### 10.1f Increment 6（已完成，报告：`docs/audit/2026-10-17-increment6-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-05 前端 SSE 统一 | ✅ | Vue2/Vue3 各新增唯一 `src/api/sse.js`；7 处重复解析器全部收敛；知识库错误事件语义保留 |
| 前端单测 | ✅ | Vue2 `npm run test:unit` 66 passed；Vue3 4 passed（跨 chunk/错误传播/abort） |
| 前端构建 | ✅ | `frontend-vue2` 与 `frontend` 的 `npm run build` 均成功 |
| 构建脚本修复 | ✅ | `frontend` 构建后运行期配置脚本改用 bash + LF，消除 `set: Illegal option` / CRLF 报错 |
| 文档同步 | ✅ | `architecture-current.md`、SSE 设计文档、requirements 事件清单同步 |

### 10.1g Increment 7（已完成，报告：`docs/audit/2026-10-17-increment7-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| 原子会话流锁 | ✅ | `try_acquire_session_stream` 条件 UPDATE + INSERT 回退；替换 TOCTOU，20 并发唯一赢家 |
| 生产部署校验 | ✅ | `utils/deployment.py` + app 启动 fail-fast：prod + workers>1 + SQLite 拒绝启动；dev 仅告警 |
| 测试 | ✅ | 新增流锁 5 项、部署 5 项；调度测试复用统一检测 |
| 全量回归 | ✅ | **946 passed / 1 skipped / 0 failed / 0 error（241.7s）** |

### 10.1h Increment 8（已完成，报告：`docs/audit/2026-10-17-increment8-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| 流锁 TTL heartbeat | ✅ | `renew_session_stream` 条件续约；流任务每 5 分钟续约，owner 被接管即停止 |
| PERF-09/11/15 回归测试 | ✅ | 新增缓存 LRU/side dict 4 项、轻量归属 + legacy 回退 2 项、续约 2 项 |
| 全量回归 | ✅ | **954 passed / 1 skipped / 0 failed / 0 error（236.8s）** |

### 10.1i Increment 9（已完成，报告：`docs/audit/2026-10-17-increment9-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-11 测试隔离 | ✅ | 记忆测试固定权威提示词目录 + 断言更新到当前契约；`pytest tests/services` 从 3 failed → **344 passed**，单独运行稳定 |
| ARCH-13 README 治理 | ✅ | 顶部审计声明 + 指向 `docs/architecture-current.md`；纠正前端/技能/Docker/CI 事实 |
| 全量回归 | ✅ | **954 passed / 1 skipped / 0 failed / 0 error（236.5s）** |

### 10.1j Increment 10（已完成，报告：`docs/audit/2026-10-17-increment10-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-05 后端常量收口 | ✅ | 新增 `SSEEvent` 15 常量；5 个生产端事件字面量全部替换；契约测试禁止字面量回归 |
| schema 注册时序修复 | ✅ | `knowledges/__init__.py` 导入即注册；修复只跑 `test_chat.py` 时 `no such table: session_knowledge_scopes` |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（245.5s）** |

### 10.1k Increment 11（已完成，报告：`docs/audit/2026-10-17-increment11-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 起步 | ✅ | 新增 `db/repositories/session_runtime.py`（159 行）；Database 4 方法改委托，`database.py` -112 行 |
| 兼容性 | ✅ | 既有调用方零迁移；81 项相关测试通过 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（238.5s）** |

### 10.1l Increment 12（已完成，报告：`docs/audit/2026-10-17-increment12-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 继续 | ✅ | 新增 `db/repositories/agent_state.py`（checkpoint + langgraph store，148 行）；Database 8 方法改委托，`database.py` 再 -98 行（累计 -210） |
| 兼容性 | ✅ | 61 项相关测试通过；调用方零迁移 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（239.3s）** |

### 10.1m Increment 13（已完成，报告：`docs/audit/2026-10-17-increment13-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 继续 | ✅ | 新增 `db/repositories/scheduled_tasks.py`（~170 行）；Database 11 方法改委托，`database.py` 再 -75 行（累计 -285） |
| 验证 | ✅ | 定时任务 API/DB/调度 67 项测试通过；调用方零迁移 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（239.8s）** |

### 10.1n Increment 14（已完成，报告：`docs/audit/2026-10-17-increment14-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 继续 | ✅ | 新增 `db/repositories/session_records.py`（~110 行）；Database 4 方法改委托，`database.py` 再 -77 行（累计 -362） |
| 验证 | ✅ | 106 项相关测试通过；调用方零迁移 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（236.9s）** |

### 10.1o Increment 15（已完成，报告：`docs/audit/2026-10-17-increment15-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 继续 | ✅ | 新增 `users.py`（~380 行）与 `mcp_api_keys.py`；Database 24 方法改委托，`database.py` 再 -238（累计 -600） |
| 兼容性 | ✅ | `_row_to_user` 静态包装保留；102 项 auth/personnel/多 worker 测试通过 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（235.8s）** |

### 10.1p Increment 16（已完成，报告：`docs/audit/2026-10-17-increment16-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 继续 | ✅ | 新增 `db/repositories/files.py`（~130 行）；Database 7 方法改委托，`database.py` 再 -104（累计 -704） |
| 验证 | ✅ | 文件 API/DB/会话 77 项测试通过；调用方零迁移 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（241.0s）** |

### 10.1q Increment 17（已完成，报告：`docs/audit/2026-10-17-increment17-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 收尾 | ✅ | 新增 `db/repositories/sessions.py`（716 行）；Database 24 方法改委托，`database.py` 2000 → 1375 行（累计 -1329） |
| 验证 | ✅ | DB/会话/聊天 85 项测试通过；调用方零迁移 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（240.7s）** |

### 10.1r Increment 18（已完成，报告：`docs/audit/2026-10-17-increment18-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 2 收尾 | ✅ | 新增 `db/schema.py`（751 行）；Database 26 个 DDL 方法改委托，`database.py` 1375 → 727 行（累计 -1977） |
| 验证 | ✅ | `tests/db` 59 项（schema 快速路径/自愈/知识库建表）通过 |
| 全量回归 | ✅ | **955 passed / 1 skipped / 0 failed / 0 error（242.2s）** |

### 10.1s Increment 19（已完成，报告：`docs/audit/2026-10-17-increment19-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| 解除 db → personnel 反向依赖 | ✅ | 新增 `db/user_fields_registry.py`；personnel import 时注册字段读写/默认管理员；db 层零静态 personnel import（测试守护） |
| 验证 | ✅ | 108 项相关测试 + 新增 registry 3 项通过 |
| 全量回归 | ✅ | **958 passed / 1 skipped / 0 failed / 0 error（244.8s）** |

### 10.1t Increment 20（已完成，报告：`docs/audit/2026-10-17-increment20-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-09 环境变量统一 | ✅ | 新增 `easy_agent/env.py`（env_str/bool/int/list）；替换 16 模块 20+ 处手写 os.getenv；landlock 保持原生读取（脚本执行场景） |
| 测试 | ✅ | 新增 env helper 3 项；相关 18 项通过 |
| 全量回归 | ✅ | **961 passed / 1 skipped / 0 failed / 0 error（240.4s）** |

### 10.1u Increment 21（已完成，报告：`docs/audit/2026-10-17-increment21-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-16 版本化迁移框架 | ✅ | 新增 `db/migrations.py` + `schema_migrations` 账本；SCHEMA_VERSION 5→6；首次 baseline=5，后续迁移启动时只执行一次 |
| 测试 | ✅ | 新增迁移测试 2 项；`tests/db` 64 passed |
| 全量回归 | ✅ | **963 passed / 1 skipped / 0 failed / 0 error（241.4s）** |

### 10.1v Increment 22（已完成，报告：`docs/audit/2026-10-17-increment22-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| PERF-14 知识库轮询 leader 选举 | ✅ | 每轮先抢 `knowledge:parse-poll` 分布式锁，未抢到跳过；单机（锁关闭）行为不变；TTL=max(30s, 3×间隔) |
| 测试 | ✅ | 新增 leader 测试 2 项；`tests/knowledges` 81 passed |
| 全量回归 | ✅ | **965 passed / 1 skipped / 0 failed / 0 error（239.4s）** |

### 10.1w Increment 23（已完成，报告：`docs/audit/2026-10-17-increment23-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| FW-10 MCP transport | ✅ | URL-only 默认改 `streamable_http`；`streamable-http`/`http` 别名归一化；显式 `sse` 兼容 |
| 测试 | ✅ | 新增 transport 测试 5 项；相关 44 passed |
| 全量回归 | ✅ | **970 passed / 1 skipped / 0 failed / 0 error（243.8s）** |

### 10.1x Increment 24（已完成，报告：`docs/audit/2026-10-17-increment24-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| FW-06 部分：frontmatter 校验 | ✅ | `parse_skill_frontmatter` + `validate_skills`；启动期逐条告警；新增测试 3 项 |
| 全量回归 | ✅ | **973 passed / 1 skipped / 0 failed / 0 error（238.1s）** |
| 未实施 | ⏳ | 官方 skills 多源与启用偏好过滤（产品行为变更，待确认） |

### 10.1y Increment 25（已完成，报告：`docs/audit/2026-10-17-increment25-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08 部分：统一错误体 | ✅ | HTTP 错误统一 `{detail, code, request_id?}`；422 统一 `VALIDATION_ERROR + errors`；401 headers 透传 |
| 连接器模型警告 | ✅ | `validate` 字段改 `validate_after_install` + alias，JSON 契约不变；测试 warnings 4→3 |
| 测试 | ✅ | 新增 error contract 2 项；相关 28+30 通过 |
| 全量回归 | ✅ | **975 passed / 1 skipped / 0 failed / 0 error（236.1s）** |

### 10.1z Increment 26（已完成，报告：`docs/audit/2026-10-17-increment26-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| FW-09 部分：官方 SQLite saver | ✅ | 新增可选依赖组 `sqlite-saver`；`EASY_CHECKPOINT_SQLITE_PATH` 启用 `AsyncSqliteSaver`，Postgres 优先，未设置时行为不变 |
| 测试 | ✅ | 新增 saver 接线测试；全量 976 passed |
| 全量回归 | ✅ | **976 passed / 1 skipped / 0 failed / 0 error（239.0s）** |

### 10.1aa Increment 27（已完成，报告：`docs/audit/2026-10-17-increment27-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| FW-11 部分：SqlStore 语义 | ✅ | 支持 `$eq/$ne/$gt/$gte/$lt/$lte/$in/$nin`；filter 后分页（有界扫描 5000）；新增测试 3 项 |
| 全量回归 | ✅ | **979 passed / 1 skipped / 0 failed / 0 error（244.4s）** |
| 未实施 | ⏳ | 语义检索 `query`、wildcard namespace 匹配（无消费者） |

### 10.1ac Increment 28（已完成，报告：`docs/audit/2026-10-17-increment28-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08 分批：Settings response_model | ✅ | 新增 9 个响应模型；settings 7 端点声明 `response_model` |
| 契约测试 | ✅ | 新增 OpenAPI schema 测试；settings 8 passed |
| 全量回归 | ✅ | **980 passed / 1 skipped / 0 failed / 0 error（243.0s）** |

### 10.1ab Cursor 复核项与规划映射（2026-10-17）

| 复核发现 | 对应审计项 | 当前状态 | 规划/约束 |
| --- | --- | --- | --- |
| 未配 `EASY_CHECKPOINT_POSTGRES_URI` 时生产 HITL 仍非官方 Postgres；K8s 样例未注入 | FW-09、ARCH-15 | ⏳ 部分：已支持官方 PG（有 URI）与官方 SQLite（`EASY_CHECKPOINT_SQLITE_PATH`），默认仍是自研快照；K8s 未注入 | ARCH-15 Phase B 首项：K8s 注入 + 生产 fail-fast；需改部署清单 |
| Store 仍是业务库 `SqlStore`，非官方 `PostgresStore` | FW-11 | ⏳ 部分：filter/分页语义已补；非官方 store、无 `query` 语义检索 | FW-11 完整项（L，需向量列/迁移） |
| 长期记忆仍走本机 `/memories/AGENTS.md` | ARCH-15 | ❌ 未实施 | 多副本 Phase B：共享存储/对象存储 |
| Agent 图按会话各编译一份，仅进程内 LRU，无“模型+工具哈希”复用 | PERF-09、ARCH-15 | ⏳ 部分：缓存 LRU 已修；图复用未做 | 多副本 Phase B / 图编译复用设计（L） |
| 工作区/技能/知识库原文件仍是本地目录；K8s RWX PVC 但代码未切远程 sandbox | ARCH-15 | ❌ 未实施 | 多副本 Phase C（远程 sandbox/对象存储） |
| WebTerminal pty 绑当前 Pod | SEC-01、ARCH-15 | ⏸ 用户明确“暂不处理”；且属 Batch 0 安全项 | 不执行；对外暴露前随 Batch 0 处理 |
| 流式仍是 `astream(mode=["messages","updates"])` 手工拆事件 | ARCH-04、ARCH-03 Phase 3 | ❌ 未实施；`sse.py` 仅统一对外事件名 | ARCH-03 Phase 3：编排器 + 纯处理器 + 持久化 + 副作用适配器（先设计） |
| 摘要阈值仍在建图期替换 `deepagents.graph.create_summarization_middleware` | FW-05 | ⏳ 已有缺失告警/降级；未迁移公开 API | 等 deepagents 0.7 公开参数（FW-01 升级立项）；当前窗口保留 |
| execute 描述仍包装 `FilesystemMiddleware.__init__` | FW-04 | ⏳ 未迁移；已有升级时显式失败契约测试 | 等 0.7 `HarnessProfile` 稳定（beta）；文档已记录 |
| `write_file` 没有 HITL | 未单列（HITL 策略） | ❌ 未实施 | 属产品/安全策略决策；若纳入需确认（会改交互与权限） |
| MCP 客户端按配置缓存，但每次工具调用新开 session；stdio MCP 仍在 | PERF-12、FW-10、FW-03 | ⏳ 部分：配置缓存已有；默认 transport 已改 streamable_http；stdio 仍兼容 | 彻底解决需部署改为 streamable HTTP + 管理员白名单（Batch 0/1 安全联动） |

**结论**：这些发现大多已在审计计划中（FW/ARCH 编号），但**并非都已执行**：本轮已完成的只是其中可安全落地、无需产品/部署决策的部分；上述 ❌/⏳ 项属于 L 级或需用户确认的架构/安全项，仍留在后续规划。

### 10.1ad Increment 29（已完成，报告：`docs/audit/2026-10-17-increment29-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-03 Phase 3 第一步 | ✅ | 新增 `services/hitl_state.py`（HITL interrupt 纯解析）；streaming 1741 → 1682 行；测试 3 项 |
| 全量回归 | ✅ | **983 passed / 1 skipped / 0 failed / 0 error（240.3s）** |
| 用户决策 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略调整暂缓 |

### 10.1ae Increment 30（已完成，报告：`docs/audit/2026-10-17-increment30-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08 继续：Auth response_model | ✅ | 新增 `StatusResponse/MessageResponse/AuthConfigResponse`；logout/reset/admin reset/config 声明 |
| 契约测试 | ✅ | `/agent/auth/config` OpenAPI schema 断言；auth 26 passed |
| 全量回归 | ✅ | **984 passed / 1 skipped / 0 failed / 0 error（242.2s）** |
| 暂缓项 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略（按用户指示） |

### 10.1af Increment 31（已完成，报告：`docs/audit/2026-10-17-increment31-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08 继续：Sessions response_model | ✅ | `SessionTitleUpdateResponse/SessionPinUpdateResponse/SessionUploadResponse`；3 个写端点声明 |
| 全量回归 | ✅ | **984 passed / 1 skipped / 0 failed / 0 error（245.8s）** |
| 暂缓 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略（用户指示） |

### 10.1ag Increment 32（已完成，报告：`docs/audit/2026-10-17-increment32-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08 继续：Files response_model | ✅ | 4 个模型；`/session/{id}` 列表、upload、delete、parse 声明 |
| 回归 | ✅ | `tests/api/test_files.py` 8 passed；全量 984 passed |
| 暂缓 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略（用户指示） |

### 10.1ah Increment 33（已完成，报告：`docs/audit/2026-10-17-increment33-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-13 部分：README 事实对齐 | ✅ | 双前端基线、专家能力、目录树描述修正；过期表述 grep 为 0 |
| 回归 | ✅ | 全量 **984 passed / 1 skipped / 0 failed / 0 error（242.6s）** |
| 未做 | ⏳ | API xlsx 自动生成、AGENTS/CLAUDE 正文完整重写 |

### 10.1ai Increment 34（已完成，报告：`docs/audit/2026-10-17-increment34-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08 继续：Connectors response_model | ✅ | status/install/uninstall 三端点 + 4 个模型；connectors 30 passed |
| 全量回归 | ✅ | **984 passed / 1 skipped / 0 failed / 0 error（239.9s）** |
| 暂缓 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略（用户指示） |

### 10.1aj Increment 35（已完成，报告：`docs/audit/2026-10-17-increment35-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08：Connectors 全端点 | ✅ | 新增 10 个模型；9 个端点补 `response_model`（toggle/session/tools/config/mcp raw/add/delete） |
| 回归 | ✅ | connectors 30 passed；全量 **984 passed / 1 skipped / 0 failed（237.7s）** |
| 暂缓 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略（用户指示） |

### 10.1ak Increment 36（已完成，报告：`docs/audit/2026-10-17-increment36-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| PERF-07 约束回归 | ✅ | 新增 `tests/db/test_sqlite_pragmas.py`：WAL / busy_timeout=30000 / synchronous=NORMAL |
| 全量回归 | ✅ | **985 passed / 1 skipped / 0 failed / 0 error（241.0s）** |
| 暂缓 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略（用户指示） |

### 10.1al Increment 37（已完成，报告：`docs/audit/2026-10-17-increment37-report.md`）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| ARCH-08：Knowledge ask/cancel 模型 | ✅ | `KnowledgeAskCancelResponse`；`/ask/cancel` 声明 response_model；knowledges 81 passed |
| 全量回归 | ✅ | **986 passed / 1 skipped / 0 failed / 0 error（238.3s）** |
| 暂缓 | ⏸ | 多副本 Phase B、streaming 深度拆分、HITL 策略（用户指示） |

### 10.1am 最终验收（2026-10-17）

- 汇总报告：`docs/audit/2026-10-17-final-acceptance.md`
- 最终基线：**986 passed / 1 skipped / 0 failed / 0 error（238.3s）**；`ruff` 通过；`uv lock --check` 通过。
- 已完成 Increment 1–37；用户明确暂缓多副本 Phase B、streaming 深度拆分、HITL 策略调整；Batch 0/1 安全项仍未执行。

### 10.1an 2026-10-18 三条专项（已完成）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| 提示词安全 | ✅ | 指令层级/外部不可信/保密/专家从属；`prompt_guard.py`；12 项测试 |
| MCP 证据链 | ✅ | `mcp_evidence.py` 信封 + `get_mcp_tools` 集成；18 项测试 |
| 记忆结构化 | ✅ | `memory_store.py` JSONL + 校验/脱敏/版本；26 项测试 |
| FW-09 官方 saver 默认化 | ✅ | `EASY_CHECKPOINT_BACKEND` auto/postgres/sqlite/legacy；23 项测试 |
| 全量回归 | ✅ | **1077 passed / 1 skipped / 0 failed / 0 error（241.9s）** |

报告：`docs/audit/2026-10-18-three-specialties-report.md`。

### 10.1ao 2026-10-18 下一批改造（已完成）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| 阶段 0 架构门禁 | ✅ | import SCC 白名单 + CI；双写冻结脚本 |
| A2 会话消息单写 | ✅ | 不再写 JSON；legacy 只读回退；双写门禁通过 |
| B3 限流/JWT | ✅ | 429 JSON/真实 IP/prod JWT fail-fast；DB 共享计数（第二批） |
| C1 sessions 域去门面 | ⏳ | `SessionRepository` + 10 路由迁移；其他域未做 |
| 全量回归 | ✅ | **1119 passed / 1 skipped / 0 failed / 0 error（243.7s）** |

报告：`docs/audit/2026-10-18-next-batch-report.md`、`docs/audit/2026-10-18-increment43-b3-shared-counter-report.md`。

### 10.1ap 2026-10-18 C1/C2/B1/D 批量改造（已完成）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| C1 剩余 API 去门面 | ✅ | auth/files/scheduled/expert/chat + 5 个域 repository；服务层边界保留 |
| C2 streaming 拆分 | ✅ | projector/persistence/side_effects/orchestrator；兼容垫片；golden 测试 |
| B1 checkpointer/Store | ✅ | prod fail-fast/逃生门/K8s 注入；官方 PostgresStore 可选 |
| D1/D3/D2 | ✅ | 工具注册表；MCP 持久 session；编译图复用（进程内，opencode/legacy 降级） |
| 全量回归 | ✅ | **1234 passed / 1 skipped / 0 failed / 0 error（237.6s）** |

报告：`docs/audit/2026-10-18-c1-c2-b1-d-report.md`。

### 10.1aq 2026-10-18 非安全剩余项批量（已完成）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| FW-04 HarnessProfile | ✅ | 官方 profile 替换 execute 描述 monkeypatch |
| A1 SCC=0 | ✅ | 知识流式端口化；import 图 0 SCC，白名单清空 |
| A3 技能单一源 | ✅ | 单向生成 + office 全量哈希 |
| C2/C3 | ✅ | finalize_stream；24/24 SSE frame 经统一 emitter |
| C1b 服务边界 | ✅ | 目标 API `repo.database` 19→7；剩余边界登记 |
| E2/E3/E4 | ✅ | 配置校验、RED 指标、文档同步 |
| FW-12/14 + 专项增强 | ✅ | 动态 HITL、MCP 错误语义、记忆锁/来源引用 |
| FW-11 语义 query | ⚠ | 无 embedding/向量索引，记录后续 |
| 全量回归 | ✅ | **1347 passed / 1 skipped / 0 failed / 0 error（256.4s）**；mcp-server 43 passed |

报告：`docs/audit/2026-10-18-remaining-batch-report.md`。

### 10.2 后续计划（Increment 2+）

> 上面 10.1 系列已完成的增量不再重复列出；以下只保留**尚未闭环**的项。

- **Batch 2 剩余**：PERF-03 官方 saver（PG 默认接入/K8s 注入）、PERF-12 MCP session 复用（需 streamable HTTP 部署）。
- **Batch 3 剩余**：FW-04/05（等 deepagents 0.7 公开 API）、FW-06 完整多源 skills（产品行为待确认）、FW-11 官方 store/语义检索。
- **Batch 4 剩余**：ARCH-03 Phase 3（streaming 拆分，先设计）、ARCH-04 大方法拆分、ARCH-08 其余 response_model/前缀统一、ARCH-15 多副本 Phase B/C（checkpoint/store/memory/workspace 外置、图编译复用）、ARCH-16 历史 ALTER 收编迁移。
- **安全**：Batch 0/1 未执行（4 P0 + 8 P1），对外暴露前必须单独排期。



- Batch 2 剩余：PERF-03（checkpoint 全量快照/thread 无界）、PERF-05（SessionLogger O(n²)）、PERF-06（解析/转换线程池 + 上传限额）、PERF-08（调度多副本强制锁）、PERF-12（MCP 首消息延迟）。
- Batch 3 剩余：FW-04/05（官方 HarnessProfile 替代 monkeypatch、摘要护栏）、FW-06/07（skills 多源、subagent 工具收窄）、FW-08（max_steps/recursion_limit）、FW-09/10（官方 saver、MCP transport 默认值）。
- Batch 4：ARCH-03/04（循环依赖、Database/streaming 拆分，先出设计文档）、ARCH-05（SSE 契约统一）、ARCH-06（技能单一源）、ARCH-08/09（API/配置收口）、ARCH-10/11（CI 与测试体系）、ARCH-15/16（多实例/迁移设计）。

### 10.3 安全提醒

Batch 0/1 未执行：**4 个 P0（未认证 Web Terminal、SPA 任意文件读、免密登录接管、MCP stdio RCE）与 8 个 P1 仍然存在**，对外暴露前需单独排期修复（详见本报告第 3 章与 `evidence/T3`）。
