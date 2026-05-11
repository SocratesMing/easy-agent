# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run web server (serves frontend from frontend/dist/)
easy-web [--port 8000] [--reload]

# Alternative: run via main.py
python main.py

# Run tests
pytest tests/ -v

# Run single test
pytest tests/test_basic.py::TestConfig -v

# Install package (dev mode)
pip install -e ".[dev]"

# Start frontend dev server (proxy to backend at localhost:8000)
cd frontend && npm run dev

# Build frontend (required before easy-web can serve it)
cd frontend && npm run build
```

## Configuration

Copy `easy_agent/config/config.yaml.example` to `easy_agent/config/config.yaml` and configure your models. Config is searched in cwd, home dir, and package dir (in that order).

The YAML format uses a `models:` dict for multi-provider support and `model:` to select the active one. Top-level keys: `model` (active model name), `models` (dict of provider configs), `max_steps`, `workspace_dir`, `system_prompt_path`. Nested sections: `retry`, `tools`, `database`, `vector_store`, `summarization`. Parsed manually in `Config.from_yaml()`, not mapped from Pydantic models.

Environment variables:
- `EASY_CONFIG` — override config file path (set by `web_runner.py` automatically)
- `EASY_JWT_SECRET` — JWT signing secret. If unset, a random secret is generated at startup (tokens invalidate on restart)

## Code Architecture

### Python Backend (`easy_agent/`)

**Agent layer** (`agent.py`) — `EasyAgent` class wraps `deepagents.create_deep_agent()`. Built-in tools: `write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`, `task` (subagent). Creates a `CompositeBackend` mapping `/workspace/` to user/session-isolated directory (`workspace/{username}/{session_id}/`), `/skills/` to read-only skill files, and `/memories/` to per-user memory files. System prompt is dynamically augmented with OS-specific commands, workspace path, skills root, and shared dependency paths. Supports `SummarizationMiddleware` for context compression (configurable via `config.summarization`).

**Config** (`config.py`) — Pydantic models: `LLMConfig`, `AgentConfig`, `ToolsConfig`, `DatabaseConfig`, `VectorStoreConfig`, `ProviderConfig`. Multi-provider support via `ProviderConfig` entries.

**LLM factory** (`model.py`) — Supports `anthropic` (ChatAnthropic), `openai` (ChatOpenAI), `deepseek` (langchain-deepseek), `minimax` (OpenAI-compatible with 120s timeout). All use LangChain wrappers.

**Skills** (`skills.py`) — Discovers skills by scanning for directories containing `SKILL.md` or `README.md`. Skills are mounted at `/skills/` virtual path in the agent backend.

**Web app** (`app.py`) — FastAPI app with lifespan that initializes config, database, vector store, system prompt, skills, shared npm deps, and Bloomberg scheduler. Serves Vue SPA from `frontend/dist/` with fallback to `index.html`. CORS is wide open. Registers routers from `easy_agent/api/`.

**Web runner** (`web_runner.py`) — Entry point for `easy-web` CLI command. Does `os.chdir(project_root)` and sets `EASY_CONFIG` env var before starting uvicorn.

### API Routes (`easy_agent/api/`)

- `chat.py` — `POST /api/chat/stream` creates/retrieves session, parses uploaded files (PDF/DOCX/XLSX/images), returns SSE `StreamingResponse`
- `sessions.py` — Session CRUD, workspace name generation
- `auth.py` — Login/register endpoints
- `files.py` — File upload/management
- `bloom.py` — Bloomberg analysis endpoints
- `forex.py` — Forex data endpoints
- `prompts.py` — Prompt template management
- `vector_store.py` — Vector store query endpoints

### Services (`easy_agent/services/`)

- `streaming.py` — **Core SSE generator** (`chat_stream_generator`). Processes DeepAgents `astream` output, emits typed events: `start`, `thinking_start`, `thinking`, `thinking_end`, `content`, `content_end`, `tool_call`, `tool_result`, `user_input_required`, `done`, `error`. Parses `<think>` tags. Accumulates partial tool call JSON across chunks.
- `agent_manager.py` — In-memory session-level agent cache (`_session_agents` dict). Lost on server restart.
- `vector_store.py` — ChromaDB with Sentence Transformers (default: `Qwen/Qwen3-Embedding-0.6B`) or ZhipuAI embeddings. Disabled by default.

### Other Backend Modules

- `middleware/auth.py` — JWT (python-jose) + bcrypt. IP binding on first login (rejects different IPs). 30min token expiry. `get_current_username()` extracts from JWT `Authorization: Bearer` header, then `X-Username` header, then defaults to `"default"`.
- `db/database.py` — SQLite (default) or MySQL (DBUtils pool, auto-fallback to SQLite). Sessions store messages as a JSON blob (not separate rows). Schema migration via `_ensure_column()` (ALTER TABLE on startup).
- `utils/file_parser.py` — Extracts text from PDF, DOCX, XLSX, PPTX, images (OCR), CSV, code files. Auto-detects encoding.
- `utils/session_logger.py` — Per-session logging utility.
- `models/api.py` — Pydantic request/response models for API endpoints.
- `models/db.py` — Database model definitions.
- `domain/bloom/` — Bloomberg analysis domain: classification, scheduling, repository, enums.

### Frontend (`frontend/`)

Vue 3 (Composition API `<script setup>`) + Vite + Tailwind CSS 4. **No Vue Router** — routing is manual via boolean refs in `App.vue`. **No state management library** — all state lives in `App.vue` and passes down as props.

- `api/auth.js` — `authFetch()` wrapper adds JWT header, dispatches `auth-expired` custom event on 401. Tokens in `localStorage` keys `mini_agent_token` / `mini_agent_username`.
- `api/chat.js` — `sendMessage()` reads SSE via `ReadableStream`, parses `data:` lines, calls `onChunk(data)` per event
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
5. Final response persisted to sessions table (JSON blob append)

### Skills (`easy_agent/skills/`)

17 skills covering: office document manipulation (`docx`, `pptx`, `xlsx` with Python scripts + shared `node_modules` via `shared_deps/`), PDF forms (`pdf`), web testing (`webapp-testing`), MCP integration (`mcp-builder`), skill creation meta-skill (`skill-creator`), and descriptive/creative skills (SKILL.md only). Skills are accessed by the agent via virtual paths like `/skills/pptx/SKILL.md`. Shared npm dependencies (`docx`, `pptxgenjs`) are installed once in `easy_agent/shared_deps/node_modules/` and symlinked into per-user/session workspaces.

### Gotchas

- **JWT secret is ephemeral** — restart invalidates all tokens unless `EASY_JWT_SECRET` env var is set
- **IP binding on login** — auth rejects logins from different IPs than the first login (can cause issues behind load balancers or in dev)
- **Messages stored as JSON blob** — not separate rows; every append reads/modifies the full array
- **Context compression** via `SummarizationMiddleware` — configurable threshold/target in config, triggers on token count
- **Agent cache is in-memory only** — server restarts lose all cached agents
- **`web_runner.py` does `os.chdir(project_root)`** — important for relative path resolution
- **`frontend/dist/` not committed** — must `npm run build` before `easy-web` can serve the UI
- **No CI/CD or Docker** — deployment is manual via uvicorn
- **Per-user dependency isolation** — each user gets `workspace/{username}/.deps/` with shared `node_modules` and Python `.venv`, symlinked into session workspaces
- **Per-user memory files** — stored at `memories/{username}/{username}_AGENTS.md`, read/written by agent for long-term context
