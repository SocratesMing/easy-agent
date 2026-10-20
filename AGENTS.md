# Repository Guidelines

> ℹ️ **2026-10-18 事实同步**：模块地图 / 数据流 / 命令以
> `docs/architecture-current.md` 为准。本文件已同步的关键事实：
> 双前端基线（`frontend-vue2/` Vue 2.7 当前托管、`frontend/` Vue 3.5 + Vite
> 并行维护）；运行时技能 7 个；公共技能经只读路由 `/skills/builtin/` 共享（按用户
> 添加 / 启停，不再复制，DA-13 S2），用户技能在 `/user-skills/`；`X-Username` 兼容回退已移除；JWT secret 在 dev/test
> 持久化于 `data/.jwt_secret`（prod 必须显式设置 `EASY_JWT_SECRET`）；
> 仓库已有 Dockerfile / docker-compose / k8s / `.github/workflows/ci.yml`。


Contributor guide for **easy-agent**, an AI agent web app built on LangChain DeepAgents with a FastAPI backend and two Vue baselines (`frontend-vue2/` Vue 2.7, `frontend/` Vue 3.5 + Vite).

## Project Structure & Module Organization

```
easy-agent/
├── easy_agent/          # Python backend (FastAPI)
│   ├── api/             # Routers: chat/auth/sessions/files/terminal/scheduled_tasks/
│   │                    #   settings/skill_center/connectors/expert/knowledge/personnel
│   ├── services/        # streaming (SSE), scheduler, agent_manager, mcp, session_uploads, sse
│   ├── db/              # SQLite/MySQL layer + schema_registry + session_messages
│   ├── models/          # Pydantic API & DB models
│   ├── middleware/      # JWT auth + in-process rate limit
│   ├── tools/           # Agent-callable tools (web_search/scheduled_task/...)
│   ├── knowledges/      # Knowledge base v2 (RAGFlow)
│   ├── personnel/       # Personnel admin / Excel import
│   ├── agent/            # EasyAgent 装配：core/backend/workspace/middleware/hitl/skills/model
│   ├── bootstrap/        # 配置/环境/初始化/日志/web_runner
│   ├── persistence/      # checkpoints / LangGraph stores
│   ├── sandboxing/       # bwrap/Landlock/fs_safety
│   ├── security/         # prompt guard
│   └── config/           # YAML configs + prompts/
├── frontend-vue2/       # 当前托管基线：Vue 2.7 + Element UI（构建产物 frontend-vue2/dist/）
├── frontend/            # 第二基线：Vue 3.5 + Vite（并行维护）
├── skills/              # canonical 运行时技能（7 个；easy_agent/skills/ 为包内回退副本）
├── tests/               # pytest tests (test_*.py) and manual demo scripts
├── scripts/             # 按功能分组：quality/db/deps/release/docs/skills/dev（见 scripts/README.md）
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
pytest tests/integration/test_basic.py -v        # Run a single test file
cd frontend-vue2 && npm run dev     # Vue2 dev server :5173（/agent 代理到后端 :8080）
cd frontend-vue2 && npm run build   # Build SPA into frontend-vue2/dist/（easy-web 托管所需）
cd frontend && npm run dev          # Vue3 + Vite dev server
cd frontend && npm run build        # Build the Vue3 baseline
./start.prod.sh                      # Production startup
```

## Coding Style & Naming Conventions

- **Python**: requires 3.11+. Four-space indentation, `snake_case` for functions/variables, `PascalCase` for classes. Ruff runs in CI with fatal rules only (`E9/F63/F7/F82`); match surrounding style for everything else.
- **Frontend**: Vue 2.7, primarily Options API. Vue 2.7 built-in Composition API functions (`ref`/`computed`/`watch`) are allowed; `<script setup>` and Vue 3-only syntax are forbidden (enforced by `src/utils/vue2Compat.test.js`). Components are `PascalCase.vue`; API modules in `frontend/src/api/` are camelCase `.js`.
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

- `config.yaml` is the **single** backend config file, and it is gitignored — copy `config-example.yaml` as a template.
- Environment differences are injected through `${VAR:-default}` placeholders, whose values come from `.env.{AGENT_ENV}` (`.env.dev` / `.env.test` / `.env.prod`, falling back to `.env`). See `.env.*.example` for the full variable list.
- Legacy per-environment files (`config.dev.yaml`, `config.prod.yaml`, `config.test.yaml`) are no longer read by the backend.
- Unknown top-level/section keys only warn by default; set `EASY_CONFIG_STRICT=1` to fail fast. `python scripts/quality/check_config_schema.py --check` verifies the template/local key set.
- `EASY_JWT_SECRET` is optional in dev/test: the secret is persisted in `data/.jwt_secret` so restarts keep tokens valid. `AGENT_ENV=prod` requires an explicit `EASY_JWT_SECRET`.
- Override the config path with the `EASY_CONFIG` environment variable.
