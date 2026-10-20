# MCP Server Subproject Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dedicated uv-managed MCP HTTP service with business-specific URL suffixes and Bearer API-key authentication.

**Architecture:** Create `mcp-server/` as an independent uv Python project. A FastAPI application mounts one FastMCP Streamable HTTP app per business type, with market exposed at `/mcp/market`. The main Easy Agent backend continues generating user API keys and writes only URL/header configuration to user MCP JSON files.

**Tech Stack:** Python 3.11+, uv, FastAPI, FastMCP Streamable HTTP, PyMySQL, pytest.

---

### Task 1: MCP Subproject Skeleton

**Files:**
- Create: `mcp-server/pyproject.toml`
- Create: `mcp-server/easy_mcp_server/__init__.py`
- Create: `mcp-server/easy_mcp_server/app.py`
- Create: `mcp-server/easy_mcp_server/auth.py`
- Create: `mcp-server/easy_mcp_server/mysql.py`
- Test: `mcp-server/tests/test_app.py`

- [ ] Write failing tests for `/health`, mounted market path, and missing/invalid Bearer authorization.
- [ ] Create the uv project, FastAPI composition app, API-key middleware, and MySQL config loader.
- [ ] Run `uv run pytest` inside `mcp-server/` and verify all tests pass.

### Task 2: Market Business MCP

**Files:**
- Create: `mcp-server/easy_mcp_server/market.py`
- Create: `mcp-server/easy_mcp_server/seed.py`
- Test: `mcp-server/tests/test_market.py`

- [ ] Write failing tests for quote parsing, user-scoped positions, API-key authentication, and SQL parameter binding.
- [ ] Move market business logic from `easy_agent/mcp_servers/market/` into the subproject.
- [ ] Use `Context.request_context.request.headers` to identify the user from the Bearer token.
- [ ] Run focused pytest tests.

### Task 3: Main Project Integration

**Files:**
- Modify: `easy_agent/api/settings.py`
- Modify: `easy_agent/services/mcp_api_keys.py` (new service)
- Modify: `easy_agent/mcp_servers/market/mcp.json.example` or replacement docs
- Test: `tests/test_settings.py`

- [ ] Write failing tests proving the backend no longer imports the MCP server package to issue keys.
- [ ] Move API-key generation into a backend service while preserving the existing endpoint response.
- [ ] Document HTTP MCP JSON configuration with URL and Authorization header.
- [ ] Run backend focused tests and frontend build.

### Task 4: Final Verification

**Files:**
- Modify: `README.md`
- Create: `mcp-server/README.md`
- Create: `mcp-server/mcp.json.example`

- [ ] Document installation, startup, suffix mapping, API-key generation, and example `mcp.json`.
- [ ] Run `uv run pytest` in `mcp-server/`, focused backend tests, and `npm run build`.
- [ ] Verify `git diff --check` passes.
