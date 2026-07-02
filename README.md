# Easy Agent

> 基于 [LangChain DeepAgents](https://docs.langchain.com/oss/python/deepagents) 框架的 AI Agent Web 应用。
> 支持多会话、文件预览与生成、网页终端、技能中心、定时任务、向量知识库与 HITL 人工审批。

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Vue](https://img.shields.io/badge/vue-3.5-brightgreen) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688) ![License](https://img.shields.io/badge/license-MIT-green)

---

## 一、项目简介

Easy Agent 是一个开箱即用的 AI Agent 平台，让大模型能够：

- 自主调用工具（文件读写、Shell 执行、网络搜索、MCP 协议）
- 执行多步任务（最多 100 步），自动进行上下文压缩
- 维护长期记忆（跨会话的用户偏好）
- 在危险操作（文件删除、目录删除）前暂停并请求人类确认（HITL）
- 由模型自行识别用户意图并创建定时任务
- 通过网页终端（Web Terminal）进行交互式操作

前后端一体化部署：后端 FastAPI 提供 REST + SSE 流式接口，前端 Vue 3 单页应用。

---

## 二、核心特性

| 模块 | 能力 |
|------|------|
| 多模型支持 | DeepSeek、Anthropic、OpenAI、火山方舟等，可热切换 |
| 多会话管理 | 会话隔离、上下文压缩、会话耗时/迭代统计 |
| 文件工具 | 上传/下载/预览/生成（docx、pdf、pptx、xlsx、图片、代码） |
| 网页终端 | 基于 xterm.js 的 Web Shell，含路径翻译与命令拦截 |
| 技能中心 | 公共/个人技能分类管理，支持自定义 SKILL.md |
| 定时任务 | LLM 自动识别调度意图，APScheduler 执行，可视化执行记录 |
| 知识库（RAG） | ChromaDB + Sentence Transformers，本地向量检索 |
| HITL 审批 | 文件删除前必须用户确认，目录删除直接拒绝 |
| 路径翻译 | 虚拟路径 ↔ 宿主机实际路径，保护主机目录结构 |
| 审计日志 | 定时任务全生命周期事件，记录到 `workspace/{user}/cron/` |

---

## 三、目录结构

```
easy-agent/
├── main.py                       # 启动入口（uvicorn）
├── pyproject.toml                # Python 依赖与项目元数据
├── uv.lock                       # uv 锁定的依赖版本
├── Dockerfile                    # 多阶段构建（前端 + 后端）
├── docker/entrypoint.sh          # 容器入口（按 ENV_MODE 选配置）
│
├── easy_agent/                   # 后端核心
│   ├── app.py                    # FastAPI 应用与 lifespan（启动调度器）
│   ├── web_runner.py             # 进程启动器
│   ├── agent.py                  # EasyAgent 封装（DeepAgents + HITL）
│   ├── config.py                 # 配置加载（config.yaml）
│   ├── model.py                  # LLM 工厂（多 provider）
│   ├── logger.py
│   │
│   ├── api/                      # FastAPI 路由
│   │   ├── auth.py               #     POST /api/auth/{register,login}
│   │   ├── chat.py               #     POST /api/chat/stream  (SSE)
│   │   ├── sessions.py           #     /api/sessions/* (CRUD + 切换)
│   │   ├── files.py              #     /api/files/* (上传/下载/预览)
│   │   ├── skill_center.py       #     /api/skill-center/* (公共/用户技能)
│   │   ├── scheduled_tasks.py    #     /api/scheduled-tasks/* (定时任务)
│   │   ├── vector_store.py       #     /api/vector-store/* (RAG)
│   │   ├── terminal.py           #     /api/terminal/* (Web Shell)
│   │   ├── bloom.py              #     /api/bloom/* (Bloom 调度)
│   │   ├── forex.py              #     /api/forex/* (外汇行情)
│   │   ├── prompts.py            #     /api/prompts/* (提示词管理)
│   │   └── settings.py           #     /api/settings/* (用户设置)
│   │
│   ├── services/                 # 业务服务层
│   │   ├── agent_manager.py      #     会话级 Agent 缓存
│   │   ├── scheduler.py          #     定时任务调度器（APScheduler）
│   │   ├── streaming.py          #     SSE 流式输出 + 工具审批
│   │   ├── mcp.py                #     MCP 工具加载
│   │   ├── vector_store.py       #     向量数据库封装
│   │   └── ...
│   │
│   ├── tools/
│   │   └── scheduled_task.py     # CreateScheduledTaskTool（供 LLM 调用）
│   │
│   ├── db/database.py            # 数据访问层（SQLite + MySQL）
│   ├── models/                   # Pydantic / dataclass 模型
│   ├── middleware/               # JWT 鉴权等
│   ├── skills/                   # 公共技能目录
│   ├── config/
│   │   ├── config.yaml           # 运行时配置
│   │   └── system_prompt.md      # Agent 系统提示词
│   └── utils/task_logger.py      # 定时任务审计日志
│
├── frontend/                     # 前端（Vue 3 + Vite + Element-Free）
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── main.js
│       ├── App.vue               # 主容器（侧边栏 + 工作区）
│       ├── api/                  # 后端 API 客户端
│       │   ├── auth.js
│       │   ├── chat.js
│       │   ├── files.js
│       │   ├── skills.js
│       │   ├── scheduledTasks.js
│       │   └── settings.js
│       └── components/
│           ├── Welcome.vue
│           ├── Chat.vue / ChatMessage.vue / ChatInput.vue
│           ├── SessionList.vue
│           ├── AssetsPanel.vue
│           ├── SkillCenter.vue
│           ├── ScheduledTasksPanel.vue
│           ├── WorkspacePanel.vue
│           ├── SettingsPanel.vue
│           ├── FilePreview.vue / CodePreview.vue / *Preview.vue
│           └── ...
│
├── data/                         # SQLite 数据库 & ChromaDB
├── workspace/                    # 用户工作区（按用户/会话隔离）
│   └── {username}/
│       ├── {session_id}/         #     会话生成的文件
│       └── cron/                 #     定时任务审计日志
├── memories/                     # 长期记忆（按用户）
├── skills/                       # 自定义技能
├── prompts/                      # 提示词模板
├── logs/                         # 应用日志
└── tests/                        # 单元测试（pytest + pytest-asyncio）
```

---

## 四、环境要求

- **Python** ≥ 3.11（推荐 3.12）
- **Node.js** ≥ 20（仅本地开发前端需要）
- **MySQL** ≥ 5.7（可选，默认 SQLite）
- **uv**（推荐 Python 包管理器）：`pip install uv`
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
cd frontend
npm install
npm run build          # 生产构建到 frontend/dist/
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
# 构建镜像（默认 ENV_MODE=prod）
docker build -t easy-agent:latest .

# 运行容器
docker run -d \
  --name easy-agent \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/workspace:/app/workspace \
  -v $(pwd)/memories:/app/memories \
  -v $(pwd)/logs:/app/logs \
  -e ENV_MODE=prod \
  easy-agent:latest
```

容器入口脚本 `docker/entrypoint.sh` 会根据 `ENV_MODE` 选择配置：
- `prod` → `config/config.yaml`（生产）
- `test` → `config/config.test.yaml`（测试）
- `dev` → `config/config.dev.yaml`（开发）

---

## 六、配置说明

主要配置项（`easy_agent/config/config.yaml`）：

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
    max_input_tokens: 200000

# Agent
max_steps: 100
workspace_dir: "./workspace"
memories_dir: "./memories"
system_prompt_path: "system_prompt.md"

# 上下文压缩
summarization:
  enabled: true
  compression_threshold: 0.8

# 数据库（sqlite 或 mysql）
database:
  type: "mysql"
  mysql:
    host: "127.0.0.1"
    port: 3306
    user: "root"
    password: "Test1234"
    database: "agent"

# 向量库（可选）
vector_store:
  enabled: false
  embedding_provider: "sentence_transformers"

# 外部目录映射（虚拟路径 → 宿主机路径）
external_dirs:
  "/strategy-workspace": "/host/path/to/workspace"
```

---

## 七、API 接口速查

所有接口（除 `/api/auth/*`、`/api/health` 外）需要在请求头携带 `Authorization: Bearer <jwt>`。

### 认证
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册（需 username + organization_id，绑定注册 IP） |
| POST | `/api/auth/login` | 登录，返回 JWT |
| GET  | `/api/health` | 健康检查 |

### 会话
| 方法 | 路径 | 说明 |
|------|------|------|
| GET    | `/api/sessions` | 列出会话 |
| POST   | `/api/sessions` | 新建会话 |
| GET    | `/api/sessions/{id}` | 会话详情 |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| POST   | `/api/sessions/{id}/switch` | 切换激活会话 |

### 聊天（SSE 流式）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/stream` | 流式对话，SSE 事件：`step_thinking` / `token` / `tool_call` / `tool_result` / `approval_required` / `interrupt_handled` / `error` / `done` |
| POST | `/api/chat/resume` | 人工审批后恢复执行 |

### 文件
| 方法 | 路径 | 说明 |
|------|------|------|
| GET    | `/api/files/list` | 列出用户文件 |
| POST   | `/api/files/users/files/upload` | 上传 |
| GET    | `/api/files/download/{path}` | 下载 |
| DELETE | `/api/files/users/files/{id}` | 删除 |
| GET    | `/api/files/preview/{path}` | 预览（docx/pdf/pptx/图片/代码/...） |

### 技能中心
| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/skill-center/public-skills` | 公共技能列表 |
| GET  | `/api/skill-center/user-skills` | 用户技能列表 |
| POST | `/api/skill-center/add-skill` | 添加技能 |
| POST | `/api/skill-center/remove-skill` | 移除技能 |

### 定时任务
| 方法 | 路径 | 说明 |
|------|------|------|
| GET    | `/api/scheduled-tasks` | 列出当前用户任务 |
| GET    | `/api/scheduled-tasks/{task_id}/runs` | 执行记录 |
| PATCH  | `/api/scheduled-tasks/{task_id}/toggle` | 启用/暂停 |
| POST   | `/api/scheduled-tasks/{task_id}/run` | 立即执行 |
| DELETE | `/api/scheduled-tasks/{task_id}` | 删除（同步注销调度） |

> 任务的创建不由前端直接触发，而是由大模型在对话中识别用户调度意图后调用 `CreateScheduledTaskTool` 完成。

### 知识库（RAG）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/vector-store/documents` | 添加文档 |
| POST | `/api/vector-store/search` | 检索 |
| GET  | `/api/vector-store/collections` | 集合列表 |

### 网页终端
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/terminal/exec` | 执行命令（含路径翻译与拦截） |
| GET  | `/api/terminal/history` | 命令历史 |

### 其他
`/api/bloom/*`、`/api/forex/*`、`/api/prompts/*`、`/api/settings/*`

---

## 八、使用指南

### 1. 基础对话
在首页输入问题，Agent 会自动调用工具完成任务。流式输出包含"思考 → 调用工具 → 工具结果 → 总结"完整过程。

### 2. 文件操作
- **上传**：在对话中拖拽文件，或在"资产"页面点"上传文件"
- **生成**：让 Agent 帮你生成 docx/pdf/pptx/xlsx，生成的文件会出现在"资产"和当前会话的"生成文件"区
- **预览**：双击文件即可在线预览

### 3. 定时任务（AI 自动创建）
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

### 4. 网页终端
- 在对话中需要执行复杂命令时，可直接通过 Web Terminal 交互
- 终端支持虚拟路径（用户视角）→ 宿主机实际路径的透明翻译
- 危险命令（`rm` 删除文件）会触发审批弹窗，必须用户确认
- 目录删除命令（`rmdir` / `rm -r` / `rm -rf`）会被直接拒绝

### 5. 技能中心
- "公共技能"区展示系统预置技能（数据分析、文档处理、量化回测等）
- 点击 + 即可加入"我的技能"
- 在对话中 LLM 会自动识别可调用的技能并加载 SKILL.md
- 自定义技能：把 `SKILL.md` 放进 `skills/your-skill/` 目录即可

### 6. 知识库
- 启用 `vector_store.enabled: true`，点击对话输入框旁的"知识库"按钮上传文件
- 上传后系统会切片、向量化、入库
- 之后的对话中 LLM 会自动检索相关片段作为上下文

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
4. 前端调用 `POST /api/chat/resume` 继续执行
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
cd frontend
npm run dev
# 默认 http://localhost:5173，通过 Vite 代理转发 API 到 :8000
```

### 代码风格
- Python：遵循 PEP 8，使用 `ruff` 检查
- Vue：Composition API + `<script setup>`，scoped 样式

### 测试
```bash
uv run pytest
```

### 添加新工具
1. 在 `easy_agent/tools/` 下继承 `langchain_core.tools.BaseTool`
2. 在 `easy_agent/services/mcp.py` 注册或注入到 `mcp_tools` 列表
3. 在 `easy_agent/config/system_prompt.md` 中描述工具用途

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
A: 查看日志中 `Scheduler started` 是否出现。`apscheduler` 必须随服务一起启动。可执行 `GET /api/scheduled-tasks` 验证任务是否注册。

**Q: 如何添加自定义模型 provider？**
A: 在 `easy_agent/model.py` 中扩展 `create_model()`，并在 `config.yaml` 的 `models.<name>` 中添加新条目。

---

## 十二、License

MIT License © 2026 Easy Agent Team
