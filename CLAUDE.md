# CLAUDE.md

> ℹ️ **2026-10-18 事实同步**：模块地图 / 数据流 / 命令以
> `docs/architecture-current.md` 为准。本文件已同步的关键事实：
> 双前端基线（`frontend-vue2/` Vue 2.7 当前托管、`frontend/` Vue 3.5 + Vite
> 并行维护）；运行时技能 7 个；agent 不挂载 `/skills/` 只读路由，用户技能在
> `/user-skills/`；`X-Username` 兼容回退已移除；JWT secret 在 dev/test
> 持久化于 `data/.jwt_secret`（prod 必须显式设置 `EASY_JWT_SECRET`）；
> 仓库已有 Dockerfile / docker-compose / k8s / `.github/workflows/ci.yml`。


This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run web server (serves frontend from frontend-vue2/dist/)
easy-web [--port 8000] [--reload]

# Alternative: run via main.py
python main.py

# Run tests
pytest tests/ -v

# Run single test
pytest tests/test_basic.py::TestConfig -v

# Install package (dev mode)
uv pip install -e ".[dev]"

# Vue2 baseline (currently served by easy-web)
cd frontend-vue2 && npm run dev      # dev server :5173, /agent proxies to :8080
cd frontend-vue2 && npm run build    # output frontend-vue2/dist/

# Vue3 baseline (Vite, maintained in parallel)
cd frontend && npm run dev
cd frontend && npm run build
```

## Configuration

Copy `easy_agent/config/config-example.yaml` to `easy_agent/config/config.yaml` and configure your models. The active config is `EASY_CONFIG` if set, otherwise `easy_agent/config/config.yaml` (legacy `config.{AGENT_ENV}.yaml` is only a fallback when the unified file is missing). `Config.find_config_file()` (cwd → `~/.easy-agent/config` → package) is used for auxiliary files such as `mcp.json`, not for the main config.

The YAML format uses a `models:` dict for multi-provider support and `model:` to select the active one. Top-level keys include `model`, `models`, `retry`, `max_steps`, `workspace_dir`, `memories_dir`, `sessions_dir`, `prompt_path` (legacy `system_prompt_path`), `app_welcome_title`. Nested sections: `tools`, `database`, `summarization`, `web_search`, `distributed_lock`, `personnel`, `upload_limits`, `file_context`, `expert`, `knowledge`. Parsed manually in `Config.from_yaml()`, not mapped from Pydantic models. Unknown keys only warn by default; set `EASY_CONFIG_STRICT=1` to fail fast, or run `python scripts/check_config_schema.py --check`.

Environment variables:
- `EASY_CONFIG` — override config file path (set by `web_runner.py` automatically)
- `EASY_CONFIG_STRICT` — `1` makes unknown config keys fail startup (default: warn)
- `EASY_JWT_SECRET` — JWT signing secret. Optional in dev/test (persisted in `data/.jwt_secret`); required when `AGENT_ENV=prod`

## Code Architecture

### Python Backend (`easy_agent/`)

**Agent layer** (`agent.py`) — `EasyAgent` class wraps `deepagents.create_deep_agent()`. Built-in tools: `write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`, `task` (subagent). Creates a `CompositeBackend` mapping `/workspace/` to the user/session-isolated directory (`workspace/{username}/{session_id}/`), `/user-skills/` to the user's skill library, and `/memories/` to per-user memory files (plus configured `external_dirs`). System prompt is dynamically augmented with OS-specific commands, workspace path, skills root, and shared dependency paths. Supports context compression via the summarization factory (configurable through `config.summarization`).

**Config** (`config.py`) — Pydantic models: `LLMConfig`, `AgentConfig`, `ToolsConfig`, `DatabaseConfig`, `ProviderConfig`. Multi-provider support via `ProviderConfig` entries.

**LLM factory** (`model.py`) — Supports `anthropic` (ChatAnthropic), `openai` (ChatOpenAI), `deepseek` (langchain-deepseek), `minimax` (OpenAI-compatible with 120s timeout). All use LangChain wrappers.

**Skills** (`skills.py`) — Discovers skills by scanning for directories containing `SKILL.md` or `README.md`. Canonical runtime skills live in the repo-root `skills/` (7: docx/pptx/xlsx/pdf/mcp-builder/skill-creator/expert-creator); `easy_agent/skills/` is the packaged fallback copy. User skills are mounted at `/user-skills/`.

**Web app** (`app.py`) — FastAPI app with lifespan that initializes config, database, knowledge base, prompt/skills discovery, checkpointers, and the scheduler (with multi-worker protection). Serves the Vue 2 SPA from `frontend-vue2/dist/` with fallback to `index.html`; `frontend/` is the parallel Vue 3 + Vite baseline. CORS is enabled (all origins by default; tighten via `EASY_CORS_ALLOW_ORIGINS`). Registers routers from `easy_agent/api/` and installs `LegacyHostRoutes` for old `/api/<prefix>` callers.

**Web runner** (`web_runner.py`) — Entry point for `easy-web` CLI command. Does `os.chdir(project_root)` and sets `EASY_CONFIG` env var before starting uvicorn.

### API Routes (`easy_agent/api/`, prefix `/agent`)

- `chat.py` — `POST /agent/chat/stream` creates/retrieves the session, parses uploaded files (PDF/DOCX/XLSX/images), and returns an SSE `StreamingResponse`; `/agent/chat/resume` continues HITL interruptions
- `sessions.py` — Session CRUD and workspace-name generation
- `auth.py` — Login/register and login-policy endpoints; single sign-on via `token_version`
- `files.py` — File upload/download/preview/management
- `expert.py` / `connectors.py` / `skill_center.py` — expert agents, MCP connectors, skill library
- `settings.py` / `prompts.py` / `scheduled_tasks.py` / `knowledge.py` / `personnel.py` / `completion.py` — remaining routers

### Services (`easy_agent/services/`)

- `streaming.py` — **Core SSE orchestrator** (`chat_stream_generator`). Processes DeepAgents `astream` output through `StreamProcessor` and emits typed events registered in `services/sse.py`; the full contract is `docs/sse-events.md` (15 events incl. `approval_required` / `knowledge_evidence`). Parses `<think>` tags and accumulates partial tool-call JSON.
- `agent_manager.py` — In-memory session-level agent cache (`OrderedDict` LRU) plus per-session stream locks. Lost on server restart; multi-instance deployments need sticky sessions.

### Other Backend Modules

- `middleware/auth.py` — JWT (python-jose) + bcrypt. 30-minute token expiry, idle-logout support, and single sign-on: a new login increments `token_version`, invalidating older tokens. `get_current_username()` only accepts a valid `Authorization: Bearer` token (no `X-Username` fallback). Login does not bind to an IP.
- `db/database.py` — SQLite (default) or MySQL (DBUtils pool); MySQL failures do **not** silently fall back to SQLite. Messages are stored in the `session_messages` row table; `sessions.messages` remains only as a legacy blob fallback for old sessions. Schema migrations run via `db/schema_registry.py` / `_ensure_column()`.
- `knowledges/` — Knowledge base v2 (RAGFlow service/API/runtime worker). `personnel/` — personnel management and Excel import.
- `utils/file_parser.py` — Extracts text from PDF, DOCX, XLSX, PPTX, images (OCR), CSV, code files. Auto-detects encoding.
- `utils/session_logger.py` — Per-session logging utility.
- `models/api.py` — Pydantic request/response models for API endpoints.
- `models/db.py` — Database model definitions.

### Frontend (`frontend-vue2/` + `frontend/`)

Two maintained baselines:

- `frontend-vue2/` — Vue 2.7 (Options API; built-in Composition API functions allowed, `<script setup>` forbidden — guarded by `src/utils/vue2Compat.test.js`) + Element UI + Vue CLI (webpack). Currently served by the backend.
- `frontend/` — Vue 3.5 + Vite. **No Vue Router** in the Vue2 baseline — routing is manual via boolean refs in `App.vue`; **no state management library** — state lives in `App.vue` and passes down as props. Both baselines share the same `src/api/sse.js` (`readSSE`) contract.

- `api/auth.js` — `authFetch()` wrapper adds the JWT header, dispatches an `auth-expired` event on 401. Tokens in `localStorage` keys `mini_agent_token` / `mini_agent_username`.
- `api/chat.js` — `sendMessage()` reads SSE via `src/api/sse.js` (`readSSE`), parses `data:` lines, and invokes the per-event callbacks
- `App.vue` — Root orchestrator: loads profile, gates on auth, manages sessions/messages
- `Chat.vue` / `ChatMessage.vue` — Message rendering with block-level layout (collapsible thinking, markdown content, tool call/result cards)
- `ChatInput.vue` — Input with file upload
- `SessionList.vue` — Sidebar with session CRUD
- `WorkspacePanel.vue` — Workspace file browser
- `CodePreview.vue` — Code preview with Monaco editor
- Document previews: `DocxPreview.vue`, `ExcelPreview.vue`, `PdfPreview.vue`, `PptPreview.vue` (via `@vue-office/*` packages)

### Data Flow

1. User input → `api/chat.py:chat_stream()` → persists user message → gets/creates per-session `EasyAgent`
2. EasyAgent invokes DeepAgents `astream` with `HumanMessage`
3. `services/streaming.py:chat_stream_generator()` processes streaming chunks: parses `<think>` tags, accumulates tool call JSON, persists thinking/tool records to DB
4. SSE events sent to frontend, rendered as blocks within assistant messages
5. Final response persisted to `session_messages` (row table; legacy `sessions.messages` blob only for old sessions)

### Skills (`skills/`)

7 runtime skills: office document manipulation (`docx`, `pptx`, `xlsx` with Python scripts + shared `node_modules`), PDF forms (`pdf`), MCP integration (`mcp-builder`), skill creation (`skill-creator`), and expert creation (`expert-creator`). Canonical copies live in the repo-root `skills/`; `easy_agent/skills/` is the packaged fallback. User skills live at `workspace/{username}/skills/` and are exposed as `/user-skills/` (not `/skills/`). Shared npm dependencies are installed once under `easy_agent/shared_deps/node_modules/` and symlinked into per-user/session workspaces.

### Gotchas

- **JWT secret persistence** — dev/test store the secret in `data/.jwt_secret`, so restarts keep tokens valid; `AGENT_ENV=prod` requires an explicit `EASY_JWT_SECRET`
- **Single sign-on** — a new login increments `token_version` and invalidates older tokens; login is not IP-bound
- **Messages are row-based** — `session_messages` is authoritative; `sessions.messages` is a legacy blob fallback
- **Context compression** via `SummarizationMiddleware` — configurable threshold/target in config, triggers on token count
- **Agent cache is in-memory only** — server restarts lose all cached agents
- **`web_runner.py` does `os.chdir(project_root)`** — important for relative path resolution
- **`frontend-vue2/dist/` not committed** — must `npm run build` (in `frontend-vue2/`) before `easy-web` can serve the UI
- **CI/Docker exist** — `.github/workflows/ci.yml` runs ruff + pytest + both frontend builds; Dockerfile/docker-compose/k8s manifests are in the repo
- **Per-user dependency isolation** — each user gets `workspace/{username}/.deps/` with shared `node_modules` and Python `.venv`, symlinked into session workspaces
- **Per-user memory files** — stored at `memories/{username}/AGENTS.md`, read/written by the agent for long-term context
