# ============================================================
# Easy Agent - Multi-stage Dockerfile
# ENV_MODE: prod (default) | test | dev
# ============================================================

# ---------- Stage 1: Frontend Build ----------
FROM node:20-slim AS frontend-builder

WORKDIR /build/frontend

# Install dependencies first (cache layer)
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --registry=https://registry.npmmirror.com

# Copy frontend source and build
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Backend Runtime ----------
FROM python:3.12-slim

# Environment mode: prod | test | dev
ARG ENV_MODE=prod
ENV ENV_MODE=${ENV_MODE}

# Install system dependencies for pty support
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        procps \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[docs]" -i https://pypi.tuna.tsinghua.edu.cn/simple

# Copy backend source
COPY easy_agent/ ./easy_agent/

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

# Entrypoint selects config based on ENV_MODE
ENTRYPOINT ["/entrypoint.sh"]
