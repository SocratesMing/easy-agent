# Repository Guidelines

Contributor guide for **easy-agent**, an AI agent web app built on LangChain DeepAgents with a FastAPI backend and Vue 3 frontend.

## Project Structure & Module Organization

```
easy-agent/
├── easy_agent/          # Python backend (FastAPI)
│   ├── api/             # Routers: chat, auth, files, sessions, terminal, scheduled_tasks
│   ├── services/        # Business logic: streaming, scheduler, agent_manager, mcp
│   ├── domain/bloom/    # Bloomberg analysis domain logic
│   ├── db/              # SQLite/MySQL database layer
│   ├── models/          # Pydantic API & DB models
│   ├── middleware/      # JWT auth middleware
│   ├── tools/           # Agent-callable tools
│   ├── skills/          # SKILL.md-based skill bundles
│   └── config/          # YAML configs + system_prompt.md
├── frontend/            # Vue 3 + Vite + Tailwind CSS 4 SPA (src/components, src/api)
├── tests/               # pytest tests (test_*.py) and manual demo scripts
├── main.py              # uvicorn entry point
├── pyproject.toml       # Project metadata & deps (managed by uv)
└── Dockerfile           # Multi-stage build
```

Runtime dirs (`workspace/`, `data/`, `logs/`, `memories/`) are generated at runtime and gitignored.

## Build, Test, and Development Commands

```bash
uv sync                              # Install/sync Python dependencies
uv pip install -e ".[dev]"           # Editable install with dev deps (alternative)
easy-web --port 8000                 # Run backend (or: python main.py)
pytest tests/ -v                     # Run the test suite
pytest tests/test_basic.py -v        # Run a single test file
cd frontend && npm run dev           # Frontend dev server (proxies to backend :8000)
cd frontend && npm run build         # Build SPA into frontend/dist/ (required before easy-web serves UI)
./start.prod.sh                      # Production startup
```

## Coding Style & Naming Conventions

- **Python**: requires 3.11+. Four-space indentation, `snake_case` for functions/variables, `PascalCase` for classes. No project-wide linter is configured-match surrounding files.
- **Frontend**: Vue 3 Composition API with `<script setup>`. Components are `PascalCase.vue`; API modules in `frontend/src/api/` are camelCase `.js`.
- **Config**: YAML files in `easy_agent/config/`; sensitive values use `${ENV_VAR}` placeholders resolved at load time.

## Testing Guidelines

- Framework: `pytest` with `pytest-asyncio` (`asyncio_mode = "auto"`), configured in `pyproject.toml`.
- Test files live in `tests/` using the `test_*.py` pattern; test classes are `Test*`, methods `test_*`.
- Files without the `test_` prefix (e.g. `demo.py`, `interactive_agent.py`) are manual scripts, not collected by pytest.

## Commit & Pull Request Guidelines

- Follow **Conventional Commits**: `feat`, `fix`, `chore`, `refactor`, `docs`. Use a scope where helpful, e.g. `feat(ui):`, `fix(agent):`, `chore(config):`.
- Descriptions are concise and may be in Chinese, matching existing history (e.g. `feat(env): AGENT_ENV 多环境识别`).
- Keep PRs focused and link related issues. Build the frontend (`npm run build`) and run `pytest` before opening a PR.

## Configuration & Security Tips

- `config.yaml` is gitignored because it holds secrets-copy `config-example.yaml` as a template.
- Per-environment configs (`config.dev.yaml`, `config.prod.yaml`, `config.test.yaml`) are selected via the `AGENT_ENV` variable.
- Set `EASY_JWT_SECRET` for stable tokens across restarts; otherwise a random secret is generated each start, invalidating all sessions on restart.
- Override the config path with the `EASY_CONFIG` environment variable.
