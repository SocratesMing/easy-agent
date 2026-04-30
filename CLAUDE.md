# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run interactive CLI
easy-agent

# Execute single task non-interactively
easy-agent --task "your task"

# Run web server
easy-web [--port 8000] [--reload]

# Run tests
pytest tests/ -v

# Run single test
pytest tests/test_basic.py::TestConfig -v

# Install package (dev mode)
pip install -e ".[dev]"

# Start frontend dev server
cd frontend && npm run dev

# Build frontend
cd frontend && npm run build
```

## Code Architecture

### Python Backend (`easy_agent/`)

**Agent layer** — `agent.py` wraps DeepAgents framework. Creates a `CompositeBackend` that maps `/` to a user/session-isolated workspace directory and `/skills/` to project skill files. System prompts are dynamically augmented with OS info and workspace path. Supports both streaming (`astream`) and non-streaming (`ainvoke`) modes.

**CLI** — `cli.py` provides interactive mode (prompt_toolkit with history/completion/suggestions) and non-interactive mode. Discovers skills from `easy_agent/skills/` directories.

**Config** — `config.py` uses pydantic models loaded from `easy_agent/config/config.yaml`. Supports LLM (provider, model, api_key), agent (max_steps, workspace_dir, system_prompt_path), tools, database, and vector_store config sections.

**LLM** — `model.py` factory supports Anthropic, OpenAI, and MiniMax (OpenAI-compatible).

### Web Backend (`easy_agent/web/`)

FastAPI application organized as:
- `server.py` — app lifespan, CORS, static file serving, health check
- `database.py` — SQLite (default) or MySQL via DBUtils pool. Tables: sessions, tool_call_records, thinking_records, session_files, generated_files, users
- `routes/` — REST endpoints: chat (SSE streaming), sessions (CRUD), files (upload/download), user (auth/profile), vector_store (knowledge base)
- `service/__init__.py` — session-level agent caching, SSE streaming generator that emits typed events: `start`, `thinking_start`, `thinking`, `thinking_end`, `content`, `tool_call`, `tool_result`, `done`, `error`
- `vector_store.py` — ChromaDB with Sentence Transformers or ZhipuAI embeddings
- `utils/auth.py` — JWT tokens + bcrypt password hashing
- `dependencies.py` — FastAPI dependency injection for extracting username from JWT, X-Username header, or defaulting to "default"

### Frontend (`frontend/`)

Vue 3 + Vite + Tailwind CSS. Key components:
- `App.vue` — root orchestrator: routing between Welcome, SessionList, Chat, AssetsPanel, UserProfile
- `Chat.vue` / `ChatMessage.vue` / `ChatInput.vue` — message display with block-level rendering (thinking, content, tool_call, tool_result)
- `SessionList.vue` — sidebar with session CRUD
- `Welcome.vue` — login/register flow
- `FilePreview.vue` / `DocxPreview.vue` / `ExcelPreview.vue` / `PdfPreview.vue` / `PptPreview.vue` — document preview components

Streaming chat uses SSE via `ReadableStream` in `api/chat.js`.

### Data Flow

1. User input → `chat.py:chat_stream()` → creates/retrieves session → persists user message → gets/creates per-session EasyAgent
2. EasyAgent invokes DeepAgents astream with HumanMessage
3. `service/chat_stream_generator()` processes streaming chunks: parses `<think>` tags for thinking, tool_call_chunks for tool invocations, tool results
4. SSE events sent to frontend, rendered as blocks within assistant messages
5. Final response + thinking + tool_calls persisted to database

### Configuration

Copy `easy_agent/config/config.yaml.example` to `easy_agent/config/config.yaml` and set `api_key`. Vector store (ChromaDB) defaults to disabled.
