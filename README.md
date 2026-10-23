# Easy Agent

> ℹ️ **2026-10-18 事实同步**：模块地图 / 数据流 / 命令以
> [`docs/architecture-current.md`](docs/architecture-current.md) 为准；本次后端目录
> 已按职责重组为 `agent/`、`bootstrap/`、`persistence/`、`sandboxing/`、`security/`
> 等子包（见 [`docs/archive/design/2026-10-18-backend-directory-reorg.md`](docs/archive/design/2026-10-18-backend-directory-reorg.md)）。
> 当前后端托管 `frontend-vue2/`（Vue 2.7 + Element UI），同时维护 `frontend/`
> （Vue 3.5 + Vite）；运行时技能 7 个；DeepAgents 0.7 兼容；已有 Dockerfile /
> docker-compose / k8s / CI。新增配置键请同步 `config-example.yaml` 并跑
> `python scripts/quality/check_config_schema.py --check`。


> 基于 [LangChain DeepAgents](https://docs.langchain.com/oss/python/deepagents) 框架的 AI Agent Web 应用。
> 支持多会话、文件预览与生成、网页终端、技能中心、定时任务、向量知识库与 HITL 人工审批。

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Vue](https://img.shields.io/badge/vue-3.5-brightgreen) ![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688) ![License](https://img.shields.io/badge/license-MIT-green)

---

## 一、项目简介

Easy Agent 是一个开箱即用的 AI Agent 平台，让大模型能够：

- 自主调用工具（文件读写、Shell 执行、网络搜索、MCP 协议）
- 执行多步任务（最多 100 步），自动进行上下文压缩
- 维护长期记忆（跨会话的用户偏好）
- 在危险操作（文件删除、目录删除）前暂停并请求人类确认（HITL）
- 由模型自行识别用户意图并创建定时任务
- 管理员可通过网页终端（Web Terminal，默认关闭）直接操作宿主机 shell

前后端一体化部署：后端 FastAPI 提供 REST + SSE 流式接口。当前后端托管 `frontend-vue2/`（Vue 2.7 + Element UI + Vue CLI）构建产物，同时维护 `frontend/`（Vue 3.5 + Vite）第二基线。

后端已针对**多副本容器部署**做了基础外置：SSE 事件持久化/跨 Pod 回放、run 租约与中断恢复、数据库迁移串行锁、上传原始文件可落 LangGraph Store；HITL checkpoint 用官方 SQLite saver，多副本时每个 pod 一个文件、靠 Ingress 粘性会话；`agent/` 负责运行时装配，`bootstrap/` 负责启动/配置，`persistence/` 负责持久化适配，职责边界清晰。

---

## 二、核心特性

| 模块 | 能力 |
|------|------|
| 多模型支持 | DeepSeek、Anthropic、OpenAI、火山方舟等，可热切换 |
| 多会话管理 | 会话隔离、上下文压缩、会话耗时/迭代统计 |
| 文件工具 | 上传/下载/预览/生成（docx、pdf、pptx、xlsx、图片、代码） |
| 网页终端 | 基于 xterm.js 的宿主机 Shell：默认关闭，`EASY_WEB_TERMINAL_ENABLED=1` 开启，仅 admin 可用 |
| 专家 · 技能 · 连接器 | 三 Tab：专家（创建/管理/导出运行）/ 公共与个人技能管理（自定义 SKILL.md）/ 连接器（MCP 服务卡片、mcp.json 原文编辑） |
| 定时任务 | LLM 自动识别调度意图，APScheduler 执行，可视化执行记录 |
| HITL 审批 | 文件删除前必须用户确认，目录删除直接拒绝 |
| 路径翻译 | 虚拟路径 ↔ 宿主机实际路径，保护主机目录结构 |
| 多副本基础 | `stream_events` 跨 Pod 回放、`agent_runs` 租约/中断恢复、迁移锁、HITL checkpoint 每个 pod 一个 SQLite 文件 + Ingress 粘性会话 |
| 对象存储（上传） | `EASY_UPLOAD_BACKEND=store`：上传原始文件落 LangGraph Store，按需 materialize；本地模式仍为默认 |
| 远端 Sandbox | `EASY_SANDBOX=langsmith`（实验性）：execute 走官方 `LangSmithSandbox`，文件工具仍本地 |
| 审计日志 | 定时任务全生命周期事件，记录到 `workspace/{user}/cron/` |

---

## 三、目录结构

```
easy-agent/
├── main.py                       # 启动入口（uvicorn）
├── pyproject.toml                # Python 依赖与项目元数据
├── uv.lock                       # uv 锁定依赖
├── Dockerfile / docker/          # 多阶段构建 + 容器入口
├── docker-compose.yml
├── deploy/                       # k8s / mysql / ragflow 部署资产
│
├── easy_agent/                   # 后端
│   ├── app.py                    # FastAPI 应用与 lifespan
│   ├── agent/                    # Agent 运行时（core/backend/workspace/middleware/hitl/skills/model）
│   ├── bootstrap/                # 配置/环境/初始化/日志/web_runner
│   ├── persistence/              # checkpoints / LangGraph stores
│   ├── sandboxing/               # bwrap/Landlock/fs_safety
│   ├── security/                 # prompt guard
│   ├── backends/                 # DeepAgents backend 适配（shell/context/langsmith）
│   ├── api/                      # REST/SSE 路由 + deps.py
│   ├── services/                 # 编排/流式/任务/连接器/记忆等
│   ├── db/                       # SQLite/MySQL + schema + repositories
│   ├── knowledges/               # 知识库 v2（RAGFlow）；facade.py 为知识域宿主统一出口
│   ├── tools/                    # Agent 工具
│   ├── middleware/               # JWT/限流/指标
│   ├── models/                   # API/DB Pydantic 模型
│   ├── utils/                    # 通用工具
│   ├── config/                   # config.yaml 模板 + prompts（资源目录）
│   └── skills/                   # 包内技能回退副本
│
├── frontend-vue2/                # 当前托管前端（Vue 2.7）
├── frontend/                     # 第二前端基线（Vue 3.5 + Vite）
├── skills/                       # canonical 运行时技能（7 个）
├── experts/                      # 预置专家
├── prompts/                      # 外部提示词资源
├── scripts/                      # 按功能分组：quality/db/deps/release/docs/skills/dev
├── tests/                        # pytest 单元/集成/契约测试
├── docs/                         # 架构/审计/设计/SSE/API 文档
├── data/                         # SQLite / 运行期数据
├── workspace/                    # 用户工作区（可按用户/会话隔离）
├── memories/                     # 长期记忆
└── logs/                         # 日志
```

---

## 四、环境要求

- **Python** ≥ 3.11（推荐 3.12）
- **Node.js** ≥ 20（仅本地开发前端需要）
- **MySQL** ≥ 5.7（可选，默认 SQLite）
- **uv**（推荐 Python 包管理器，统一用它安装 Python 依赖）：
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **npm** 或 **pnpm**

可选：
- Tesseract OCR（用于图片内容提取）

---

## 五、快速开始

### 方式一：本地开发

```bash
# 1. 克隆项目
git clone https://github.com/SocratesMing/easy-agent.git
cd easy-agent

# 2. 安装后端依赖（使用 uv）
uv sync

# 3. 安装前端依赖并构建
cd frontend-vue2
npm install
npm run build          # 生产构建到 frontend-vue2/dist/
cd ..

# 4. 配置 LLM（编辑 easy_agent/config/config.yaml）
#    修改 models.deepseek.api_key 等

# 5. 启动服务
uv run python main.py
# 或
uv run uvicorn easy_agent.app:app --host 0.0.0.0 --port 8000
```

打开浏览器访问 `http://localhost:8000`。

### 方式二：Docker 部署

```bash
# 构建镜像（默认 AGENT_ENV=prod）
docker build -t easy-agent:latest .

# 也可在构建时指定环境（会同时影响前端构建与后端配置）
# docker build --build-arg AGENT_ENV=test -t easy-agent:test .

# 运行容器
docker run -d \
  --name easy-agent \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/workspace:/app/workspace \
  -v $(pwd)/memories:/app/memories \
  -v $(pwd)/logs:/app/logs \
  -e AGENT_ENV=prod \
  easy-agent:latest
```

后端**只使用一份 `config.yaml`**，不再按环境拆分配置文件。

环境差异（工作区路径、上下文窗口、阈值、模型名等）通过 `config.yaml` 里的 `${VAR:-默认值}` 占位符注入，
不同环境只需提供不同的 env 文件 —— 由 `AGENT_ENV` 决定加载哪个：

- `AGENT_ENV=dev` -> 项目根 `.env.dev`（不存在则回退 `.env`）
- `AGENT_ENV=test` -> 项目根 `.env.test`（不存在则回退 `.env`）
- `AGENT_ENV=prod` -> 项目根 `.env.prod`（不存在则回退 `.env`）

变量清单见 `.env.dev.example` / `.env.test.example` / `.env.prod.example`。
配置文件路径可用 `EASY_CONFIG=/path/to/xxx.yaml` 覆盖（`Config.resolve_config_path()`），
终端会打印环境信息与加载的配置文件路径。
YAML 中的 `api_key`、`password` 等值支持 `${ENV_VAR}` / `${ENV_VAR:-默认值}` 占位符，在加载配置时从 `os.environ` 解析；项目根 `.env` 会在配置解析前自动注入（见 `easy_agent/utils/env_loader.py`），未取到值的变量名会在启动日志里以 WARNING 列出。

### 多副本 / 存储相关开关（可选）

```bash
# 生产/多副本建议：迁移 Job 先跑迁移，Pod 启动只校验
python scripts/db/migrate_db.py
EASY_AUTO_MIGRATE=0
EASY_MIGRATION_LOCK_TIMEOUT=120

# HITL checkpointer：官方 SQLite saver，文件在仓库根 data/ 下（不能放 NAS）
# 多副本时每个 pod 一个文件（{pod} 换成实例名），并在 Ingress 配好 cookie 粘性会话后打开 EASY_STICKY_SESSIONS
# LangGraph Store 固定用业务库 SqlStore，无需配置
EASY_CHECKPOINT_SQLITE_PATH=data/checkpoints/{pod}.db
EASY_STICKY_SESSIONS=1

# 上传原始文件落数据库 LangGraph Store（local 默认，不改变现有行为）
EASY_UPLOAD_BACKEND=store

# 实验性：execute 走官方 LangSmith Sandbox（需 LANGSMITH_API_KEY）
EASY_SANDBOX=langsmith

# 网页终端（宿主机真实 shell，不经沙箱）：默认关闭，开启后仅 admin 可用
EASY_WEB_TERMINAL_ENABLED=1

# 严格多副本门禁：Phase 2/3 外置未完成前保持 0，否则启动会 fail-fast
EASY_MULTI_REPLICA_STRICT=0
```

> `EASY_UPLOAD_BACKEND=store` 只覆盖 `/agent/files/upload` 的原始上传文件；workspace/生成文件仍是本地/RWX。
两套前端都用 `scripts/run-vite.mjs` 按 `AGENT_ENV` 映射构建 mode（dev→development / test→test / prod→production）：`frontend-vue2/` 实际调用 Vue CLI（webpack），`frontend/` 实际调用 Vite；两者都会加载对应的 `.env.[mode]` 文件并输出配置横幅。

---

## 六、配置说明

主要配置项（例如 `easy_agent/config/config.yaml`；本地环境配置已被 Git 忽略）：

```yaml
# 使用的模型（在 models 列表中选择一个）
model: "deepseek"

models:
  deepseek:
    provider: "deepseek"     # deepseek | anthropic | openai | ark | minimax
    protocol: "openai"       # openai | anthropic
    model: "deepseek-v4-flash"
    api_key: "sk-xxx"
    api_base: "https://api.deepseek.com"
    context_length: 200000        # 旧键 max_input_tokens 仍兼容

# Agent
max_steps: 100
workspace_dir: "./workspace"
memories_dir: "./memories"
prompt_path: "prompts"          # 旧键 system_prompt_path 仍兼容

# execute 沙箱（Linux：bwrap，不可用时降级 Landlock）
sandbox_enabled: true
sandbox_network: host           # host（默认，可联网）| none（bwrap 断网；Landlock 降级模式无法断网）
execute_max_timeout: 600        # 模型给 execute 指定的超时上限（秒）
session_thread: true            # 一个会话一个 thread（历史与摘要状态由 checkpointer 保存）；false = 每条消息重建历史
skill_write: new-only           # 已有技能在沙箱里也只读挂载；deny = 整个技能库只读

# 上下文压缩
summarization:
  enabled: true
  compression_threshold: 0.8

# 联网搜索（输入框地球按钮；地址与 api_key 复用 ${VAR} 环境变量注入逻辑）
web_search:
  enabled: true
  provider: "tavily"                                   # tavily（默认）| internal（内网 WebSearch 接口）
  api_url: ""                                          # 留空按 provider 取默认地址，可用 WEB_SEARCH_API_URL 覆盖
  api_key: "${WEB_SEARCH_API_KEY}"                     # Tavily key（tvly- 开头），为空则前端按钮置灰
  search_depth: "basic"                                # 仅 tavily：basic/advanced/fast/ultra-fast
  topic: "general"                                     # 仅 tavily：general/news/finance；带时间范围时自动用 news
  timeout_seconds: 30
  default_time_range: "NoLimit"                        # NoLimit/OneDay/OneWeek/OneMonth/OneYear 或 YYYY-MM-DD..YYYY-MM-DD
  max_results: 10                                      # 单次返回条数（tavily 上限 20）
  main_text_max_chars: 500                             # 单条结果摘要展示上限

# 数据库（sqlite 或 mysql）
database:
  type: "mysql"
  mysql:
    host: "127.0.0.1"
    port: 3306
    user: "root"
    password: "Test1234"
    database: "agent"

# 分布式锁（多 pod / 多 worker 部署开启，避免定时任务等被重复执行）
distributed_lock:
  enabled: false                                       # 对应 DISTRIBUTED_LOCK_ENABLED，单实例部署保持 false
  key_prefix: "easy_agent"                             # 多套系统共用一个库时用于区分
  ttl_seconds: 300                                     # 锁存活时间：持有者崩溃后其它实例最多等这么久即可接管
  renew_interval_seconds: 100                          # 长任务续约间隔，实际取 min(本值, ttl/3)
  retry_interval_seconds: 1                            # 等待抢锁（wait_seconds>0）时的轮询间隔

# 外部目录映射（虚拟路径 → 宿主机路径）
external_dirs:
  "/strategy-workspace": "/host/path/to/workspace"
```

配置校验：未知顶层/关键节键默认只 WARNING（旧配置可直接启动）；设置
`EASY_CONFIG_STRICT=1` 后改为启动失败。`python scripts/quality/check_config_schema.py --check`
会校验 `config-example.yaml` 的 schema，并在本地存在 `config.yaml` 时检查
「本地配置使用的键是否都已写入模板」。

---

## 七、API 接口速查

所有接口（除 `/agent/auth/*`、`/agent/health` 外）需要在请求头携带 `Authorization: Bearer <jwt>`。

### 认证
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/auth/register` | 注册（需 username + organization_id，绑定注册 IP） |
| POST | `/agent/auth/login` | 登录，返回 JWT |
| GET  | `/agent/health` | 健康检查 |

### 会话
| 方法 | 路径 | 说明 |
|------|------|------|
| GET    | `/agent/sessions` | 列出会话 |
| POST   | `/agent/sessions` | 新建会话 |
| GET    | `/agent/sessions/{id}` | 会话详情 |
| DELETE | `/agent/sessions/{id}` | 删除会话 |
| POST   | `/agent/sessions/{id}/switch` | 切换激活会话 |

### 聊天（SSE 流式）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/chat/stream` | 流式对话，SSE 事件：`step_thinking` / `token` / `tool_call` / `tool_result` / `approval_required` / `interrupt_handled` / `error` / `done` |
| POST | `/agent/chat/resume` | 人工审批后恢复执行 |

### 文件
| 方法 | 路径 | 说明 |
|------|------|------|
| GET    | `/agent/files/list` | 列出用户文件 |
| POST   | `/agent/files/users/files/upload` | 上传 |
| GET    | `/agent/files/download/{path}` | 下载 |
| DELETE | `/agent/files/users/files/{id}` | 删除 |
| GET    | `/agent/files/preview/{path}` | 预览（docx/pdf/pptx/图片/代码/...） |

### 技能中心
| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/agent/skill-center/public-skills` | 公共技能列表（含 `added` / `enabled` / 统计） |
| GET  | `/agent/skill-center/user-skills` | 用户技能列表（含已停用，`enabled` 区分） |
| GET  | `/agent/skill-center/skill-detail` | 技能详情：`scope=public\|user` + SKILL.md 正文 + 统计 |
| POST | `/agent/skill-center/add-skill` | 添加技能 |
| POST | `/agent/skill-center/remove-skill` | 移除技能（启用/停用目录里的都删） |
| POST | `/agent/skill-center/enable-skill` | 启用（移回 Agent 加载目录） |
| POST | `/agent/skill-center/disable-skill` | 停用（移出 Agent 加载目录） |

### 定时任务
| 方法 | 路径 | 说明 |
|------|------|------|
| GET    | `/agent/scheduled-tasks` | 列出当前用户任务 |
| GET    | `/agent/scheduled-tasks/{task_id}/runs` | 执行记录 |
| PATCH  | `/agent/scheduled-tasks/{task_id}/toggle` | 启用/暂停 |
| POST   | `/agent/scheduled-tasks/{task_id}/run` | 立即执行 |
| DELETE | `/agent/scheduled-tasks/{task_id}` | 删除（同步注销调度） |

> 任务的创建不由前端直接触发，而是由大模型在对话中识别用户调度意图后调用 `CreateScheduledTaskTool` 完成。

### 知识库（RAG）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/vector-store/documents` | 添加文档 |
| POST | `/agent/vector-store/search` | 检索 |
| GET  | `/agent/vector-store/collections` | 集合列表 |

### 网页终端（默认关闭：`EASY_WEB_TERMINAL_ENABLED=1`）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/terminal` | 终端页面（xterm.js）；未开启时 404 |
| WS   | `/agent/terminal/ws` | 首帧 `{"type": "auth", "token": "<JWT>"}`，仅 admin；鉴权失败以 4401 / 4403 关闭 |

### 其他
`/agent/prompts/*`、`/agent/settings/*`（含 `GET /agent/settings/web-search`：联网搜索可用性）

---

## 八、使用指南

### 1. 基础对话
在首页输入问题，Agent 会自动调用工具完成任务。流式输出包含"思考 → 调用工具 → 工具结果 → 总结"完整过程。

### 2. 文件操作
- **上传**：在对话中拖拽文件，或在"资产"页面点"上传文件"
- **生成**：让 Agent 帮你生成 docx/pdf/pptx/xlsx，生成的文件会出现在"资产"和当前会话的"生成文件"区
- **预览**：双击文件即可在线预览

### 3. 联网搜索（输入框地球按钮）
- 输入框左侧地球按钮可开启/关闭联网搜索（默认关闭，仅对开启后发送的消息生效）
- 开启后 Agent 获得 `web_search` 工具，遇到时效性内容会先检索公开网页，并在回答中给出来源链接
- 按钮置灰表示未配置：需在 `.env.{AGENT_ENV}` 中配置 `WEB_SEARCH_API_KEY`（Tavily key 在 https://app.tavily.com 申请，`tvly-` 开头）并重启后端
- 默认 provider 为 **Tavily**（`https://api.tavily.com/search`），支持 `search_depth` / `topic` / `max_results` / 时间范围 / 域名过滤；把 `WEB_SEARCH_PROVIDER` 设为 `internal` 可切回内网 WebSearch 接口（支持 `timeRange` / `sites` / `blockHosts`）
- 单次检索超时由 `web_search.timeout_seconds` 控制；接口地址可用 `WEB_SEARCH_API_URL` 覆盖

### 4. 定时任务（AI 自动创建）
直接用自然语言告诉 Agent：
> "每天早上 8 点检查一下 workspace 下的文件数量"
> "每周一上午 9 点汇总本周新增的文档"
> "每 5 分钟跑一次健康检查"

Agent 会自动解析调度意图并调用 `create_scheduled_task` 工具，参数包括：
- `name` 任务名
- `description` 任务描述
- `schedule_cron` cron 表达式（5 字段：`分 时 日 月 周`）
- `task_prompt` 每次执行时交给 Agent 的指令

创建后，Agent 会返回任务的 `task_id` 和下次执行时间。

在左侧导航栏点击 **定时任务** 即可看到所有任务、启用状态、cron 表达式、下次执行时间、过去执行记录（每条可折叠展开查看完整结果），也可手动执行、暂停或删除。

执行记录审计日志保存在：`workspace/{username}/cron/{task_id}.log`（JSONL 格式）。

### 5. 网页终端
- 宿主机上的真实 shell（以服务进程用户运行，不经沙箱、不做路径翻译或命令拦截），因此默认关闭
- 设置 `EASY_WEB_TERMINAL_ENABLED=1` 后访问 `/terminal`；只有 admin 能连接，页面复用主界面的登录态
- 前后端不同源的开发环境可用 `/terminal#token=<JWT>` 传入 token（fragment 不会发到服务器）

### 6. 专家 · 技能 · 连接器（原技能中心）
- 入口：左侧导航栏「专家 · 技能 · 连接器」；面板**首行即三个 Tab**（上方不放标题栏等任何内容），关闭按钮在 Tab 行最右侧
- 三个 Tab：
  - **专家**：创建/编辑/导出运行专家 Agent（`/agent/expert/*` 与专家工作台）
  - **技能**：顶部工具条（搜索 / 已安装(n) 筛选 / 添加技能）+ 分类 chips（全部 + 5 类）+ 技能卡片（图标 / 名称 / 描述 / 「N 个文件 · 大小 · 更新时间」真实指标）。
    - 卡片右上角：未安装显示 `+`（一键加入「我的技能」），已安装显示**启用开关**
    - 点卡片进**详情页**：大图标 / 名称 / 版本 / 描述（可展开）/ 去试试 / 移除技能 + 「概述」= SKILL.md 正文 + 「更多」= 版本/位置/文件数/大小/更新时间
    - 停用 = 把技能目录移到 `workspace/{username}/skills-disabled/`（Agent 只扫 `skills/`，因此停用后真的不加载；重新打开即移回）
    - 自定义技能把 `SKILL.md` 放进 `skills/your-skill/` 目录，或用「添加技能」导入 zip
  - **连接器**（= MCP）：卡片列表 + 搜索；点卡片弹出描述与对应的 mcp.json 片段（可复制），已安装的可在弹层里移除
- 自建 MCP 服务已拆分为独立子项目 `mcp-server/`（uv 管理，按业务分 URL 后缀
  如 `/mcp/market/`，配置 URL + API Key 即可接入），详见 `mcp-server/README.md`

### 7. MCP（连接器）配置
- 连接器 Tab 工具条有「已安装(n)」筛选：点开只显示已安装的 MCP（内置目录中已添加的 + 用户自定义的）
- 连接器 Tab 右上角「自定义连接器」进入 **MCP 服务管理**：直接编辑 `workspace/{username}/mcp.json` 原文（带行号的 JSON 编辑器，保存后即时生效）；添加自定义 MCP 也在这里改 JSON 完成，管理页不再单独提供「+ 添加 MCP」按钮（`POST /agent/connectors/mcp` 接口保留，支持 `servers` / `mcpServers` / 对象 / 单条四种格式）
- 后端接口：`/agent/connectors`（目录 + 自定义项）、`/agent/connectors/install|uninstall`、`/agent/connectors/mcp*`（增删改原文）
- 安全限制（SEC-04）：stdio 连接器会在服务器上、沙箱外直接执行命令，因此普通用户不能添加自定义 stdio 连接器，只能使用公共目录里的 stdio 连接器，且只能修改其声明的字段（admin 不受限）；违规配置保存时返回 403，已存在的违规项不会被加载或校验。用户 `mcp.json` 不做 `${VAR}` 展开（只有管理员维护的全局文件展开），避免把服务端密钥带出
- 公共 MCP 配置：`easy_agent/config/mcp.json`（与 `config.yaml` 同目录，查找顺序 `./easy_agent/config` → `~/.easy-agent/config` → 包内 `config`，由 `Config.find_config_file` 解析），每条 `{id,name,description,icon,config}`（`id` 即写入用户 mcp.json 的 server 名）；改这个文件不用重启后端（每次请求现读），当前预置 `mysql` / `sqlite` 两条（均为 stdio）
  - ⚠️ 该文件位于 config 目录，同时会被当作「全局 mcp.json」用于 Agent 加载。它没有 `servers` 字段，因此不会给任何用户自动加载 MCP；**不要往里面加 `servers`**（会让所有未自建 mcp.json 的用户自动加载这些服务）
  - 连接器页的「已安装」判断只读用户自己的 `workspace/{username}/mcp.json`，不受这份公共配置影响
- 格式规范（顶层 `connectors` 数组，字段非法/重复/缺 name 或缺 config 的条目会被跳过并记 warning，页面始终可用）
- API Key 已不在设置页签发（表 `mcp_api_keys` 仅由 mcp-server 只读校验），
  历史 key 迁移见 `scripts/db/migrate_market_mcp_keys.py`

---

## 九、HITL（人在环路）机制

为防止 Agent 执行危险操作，关键动作会暂停并请求用户确认：

| 操作 | 行为 |
|------|------|
| 删除文件（`rm <file>`） | 弹窗确认，显示待删文件路径列表 |
| 删除目录（`rmdir` / `rm -r` / `rm -rf` / `find -delete`） | **直接拒绝**，返回错误信息 |
| 删除项目根目录或 workspace 下子目录 | **直接拒绝**（受保护目录） |

前端交互：
1. 后端发出 SSE `approval_required` 事件（含待删文件路径列表）
2. 前端在对应工具调用卡片上显示"等待确认"按钮组
3. 用户点击"批准"或"拒绝"
4. 前端调用 `POST /agent/chat/resume` 继续执行
5. 后端继续流式输出

后台定时任务执行时，会自动以 `enable_hitl=False` 调用 Agent，绕过审批避免死锁。

---

## 十、开发

### 后端开发模式
```bash
uv run uvicorn easy_agent.app:app --host 0.0.0.0 --port 8000 --reload
```

### 前端开发模式（热更新）
```bash
# Vue2 基线（后端当前托管）
cd frontend-vue2
npm run dev
# 默认 http://localhost:5173，通过 webpack devServer 代理转发 /agent、/api 到后端（默认 :8080）

# Vue3 基线（Vite）
cd frontend
npm run dev
# 后端地址通过 VITE_API_BASE_URL 配置（见 .env.dev）
```

### 代码风格
- Python：遵循 PEP 8，使用 `ruff` 检查
- Vue：Vue 2.7 Options API（禁用 `<script setup>`，由 `src/utils/vue2Compat.test.js` 守卫），scoped 样式

### 测试与质量门禁

```bash
uv run pytest                         # 全量：当前 1607 passed / 1 skipped
uv run pytest tests/agent tests/api   # 指定目录
uvx ruff check .                      # 静态检查
python scripts/quality/import_graph.py --check-scc       # 模块 SCC=0
python scripts/quality/check_double_write.py             # 双写冻结
python scripts/quality/check_config_schema.py --check    # 配置键集合
python scripts/quality/check_env_usage.py --check        # 裸 env 检查
python scripts/quality/check_multi_replica.py            # 多副本能力门禁
python scripts/skills/sync_skills.py --check             # 技能单一源
```

测试目录：`tests/agent`、`tests/api`、`services`、`db`、`knowledges`、`integration`、`config`、`skills`、`utils`；SSE 契约在 `tests/services/test_sse_contract.py`。

### API 文档
```bash
python scripts/docs/generate_api_docs.py --check   # CI 冒烟检查：可收集、前缀/模块标签正确
python scripts/docs/generate_api_docs.py           # 生成 docs/API接口文档.xlsx
```
`docs/API接口文档.xlsx` 是生成物，不要求每次代码变更都提交二进制更新。

### 脚本目录

`scripts/` 已按功能分组，根目录不再放散脚本（详见 `scripts/README.md`）：

| 目录 | 用途 |
| --- | --- |
| `scripts/quality/` | import SCC、双写冻结、配置 schema/裸 env、多副本门禁 |
| `scripts/db/` | `migrate_db`、SQLite→MySQL、MCP key |
| `scripts/deps/` | 离线依赖下载、wheel 落盘 |
| `scripts/release/` | 发布预检、变更导出 |
| `scripts/docs/` | API 文档生成 |
| `scripts/skills/` | 技能单一源同步 |
| `scripts/dev/` | 本地开发 shell |

### 添加新工具
1. 在 `easy_agent/tools/` 下继承 `langchain_core.tools.BaseTool`
2. 在 `easy_agent/services/mcp.py` 注册或注入到 `mcp_tools` 列表
3. 在 `easy_agent/config/prompts/system.md` 中描述工具用途

### 添加新 API 路由
1. 在 `easy_agent/api/` 下新建 router 文件
2. 在 `easy_agent/api/__init__.py` 中导出
3. 在 `easy_agent/app.py` 中 `app.include_router(...)`

---

## 十一、常见问题

**Q: 启动时报 "ModuleNotFoundError: No module named 'xxx'"？**
A: 重新执行 `uv sync` 安装依赖。

**Q: 数据库连接失败？**
A: 默认使用 SQLite，无需额外配置。若配置为 MySQL，请确保 `database.mysql.host` 可达且账号有建表权限。

**Q: LLM 调用超时？**
A: 检查 `api_base` 是否可达，`api_key` 是否有效。日志文件 `logs/easy_agent.log` 包含详细错误。

**Q: 定时任务不触发？**
A: 查看日志中 `Scheduler started` 是否出现。`apscheduler` 必须随服务一起启动。可执行 `GET /agent/scheduled-tasks` 验证任务是否注册。

**Q: 多 pod / 多副本部署时定时任务被重复执行？**
A: 每个实例都会注册同一批 cron，需要开启分布式锁：在 `.env.{AGENT_ENV}` 中设置 `DISTRIBUTED_LOCK_ENABLED=true`（或直接改 `config.yaml` 的 `distributed_lock.enabled`）。开启后同一时刻只有一个实例真正执行，任务被跳过的触发会记录 `operation=skip_locked` 的审计日志。多实例下的其它注意事项见 [docs/distributed-lock.md](docs/distributed-lock.md)。

**Q: 如何添加自定义模型 provider？**
A: 在 `easy_agent/agent/model.py` 中扩展 `create_model()`，并在 `config.yaml` 的 `models.<name>` 中添加新条目。

---

## 十二、多副本部署与状态外置

服务部署在多个 Pod（或 uvicorn `--workers > 1`）时，需要把进程内状态外置；项目已提供的基础能力：

| 能力 | 实现 | 说明 |
| --- | --- | --- |
| SSE 事件跨 Pod 回放 | `stream_events` 表 + `StreamEventWriter` | `/agent/chat/stream/live` 按 `last_event_id` 从 DB 回放；不依赖本 Pod hub |
| run 租约/中断恢复 | `agent_runs` 表 + `run_registry` | 流锁心跳续租；Pod 崩溃后，启动时和之后每隔 `EASY_RUN_RECOVERY_INTERVAL` 秒（默认 60）把租约过期的 running 标记 `interrupted`、补 error 事件并释放它留下的会话流锁；**不自动重跑** |
| 会话流互斥 | `session_runtime` DB 流锁 | 跨 Pod 原子抢占/续约/取消 |
| 迁移串行化 | MySQL `GET_LOCK` / SQLite `flock` | `scripts/db/migrate_db.py`；`EASY_AUTO_MIGRATE=0` 时启动只校验 |
| LangGraph checkpointer/store | 官方 SQLite saver / 业务库 SqlStore | `EASY_CHECKPOINT_SQLITE_PATH`；多副本时路径带 `{pod}`、`EASY_STICKY_SESSIONS=1`，审批落错 pod 返回 409，删除会话后各 pod 后台补删 |
| 上传原始文件 | LangGraph StoreBackend（可选） | `EASY_UPLOAD_BACKEND=store`；只覆盖 `/agent/files/upload` |
| 定时任务重复执行 | 数据库分布式锁 | `distributed_lock.enabled=true`；同 cron 单实例执行 |

仍需注意：

- `workspace/`、生成文件、会话聊天 `uploadfiles/` 仍依赖本地/RWX；多 Pod 需共享存储或后续迁移到对象存储。
- Agent 实例与部分流任务缓存是进程内状态；跨 Pod 请求会重建 Agent，但不会共享内存缓存。
- Web Terminal 的 pty 绑定单个 Pod；多副本需要会话亲和或独立终端服务。
- `EASY_SANDBOX=langsmith` 为实验性，未做 sandbox destroy/完整状态同步；默认 local bwrap/Landlock。
- 严格门禁 `EASY_MULTI_REPLICA_STRICT=1` 在 Phase 2/3 全部外置完成前会 fail-fast；K8s 样例当前保持 `0`。

### 分布式锁（定时任务）

每个实例都会注册同一批 cron，必须开启数据库分布式锁：在 `.env.{AGENT_ENV}` 设置
`DISTRIBUTED_LOCK_ENABLED=true`（或 `config.yaml` 的 `distributed_lock.enabled`）。
锁实现/表见 `easy_agent/utils/distributed_lock.py`、`distributed_locks`；被跳过的触发记录
`operation=skip_locked` 审计日志。更多细节见 [docs/distributed-lock.md](docs/distributed-lock.md)。

---

## 十三、License

MIT License © 2026 Easy Agent Team
