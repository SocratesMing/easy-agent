# ============================================================
# Easy Agent - Multi-stage Dockerfile
# AGENT_ENV: prod (default) | test | dev
# ============================================================

# ---------- Stage 1: Frontend Build ----------
FROM node:20-slim AS frontend-builder

# 前端构建时需要 AGENT_ENV 来决定加载哪个 .env 文件
ARG AGENT_ENV=prod
ENV AGENT_ENV=${AGENT_ENV}

WORKDIR /build/frontend

# Install dependencies first (cache layer)
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --registry=https://registry.npmmirror.com

# Copy frontend source and build
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Backend Runtime ----------
FROM python:3.12-slim

# 环境变量: AGENT_ENV 决定后端配置文件和前端构建环境
ARG AGENT_ENV=prod
ENV AGENT_ENV=${AGENT_ENV}

# Install system dependencies for pty support and shell sandboxing.
# bubblewrap (bwrap) isolates agent shell commands to the workspace; if the
# container lacks user-namespace caps it auto-falls-back to Landlock
# (unprivileged filesystem isolation; requires kernel >= 5.13). If neither
# bwrap nor Landlock is available, shell execution is refused (fail-closed)
# to prevent cross-user workspace access.
# To enable full bwrap isolation, run the container with:
#   --cap-add SYS_ADMIN --security-opt apparmor=unconfined
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        procps \
        bubblewrap \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies with uv（项目统一使用 uv 管理 Python 依赖）
COPY pyproject.toml uv.lock ./
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /uvx /bin/
# 先用锁文件安装全部依赖（含 docs 扩展），便于 Docker 层缓存；
# 项目本体待源码就位后再做 editable 安装。
RUN uv export --frozen --no-dev --extra docs -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Copy backend source
COPY easy_agent/ ./easy_agent/

# Editable 安装项目本体（脚本入口 easy-web / 包发现）
RUN uv pip install --system --no-cache --no-deps -e .

# Copy frontend build output
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist/

# Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create data directories
RUN mkdir -p /app/data /app/workspace /app/memories /app/logs /app/skills /app/prompts

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Entrypoint starts uvicorn (app.py 内部已通过 AGENT_ENV 选择配置文件)
ENTRYPOINT ["/entrypoint.sh"]
