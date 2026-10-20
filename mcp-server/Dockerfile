# ============================================================
# Easy MCP Server - standalone image
# 默认监听 8174；对外映射由 compose / -p 决定（本机常用 22337:8174）
# ============================================================

FROM python:3.12-slim

WORKDIR /app

# uv 安装依赖（与主项目一致，走锁文件保证版本可复现）
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# 源码：包在 /app/easy_mcp_server，通过 PYTHONPATH 发现，无需 editable 安装
COPY easy_mcp_server/ ./easy_mcp_server/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app
ENV MCP_PORT=8174
EXPOSE 8174

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8174/health')" || exit 1

CMD ["sh", "-c", "uvicorn easy_mcp_server.main:app --host 0.0.0.0 --port ${MCP_PORT:-8174} --timeout-graceful-shutdown 5"]
