#!/bin/bash
set -e

# ============================================================
# Easy Agent Entrypoint
# AGENT_ENV: prod (default) | test | dev
# 后端只读唯一配置 config.yaml；AGENT_ENV 决定加载哪个 .env.{env}。
# 此脚本负责 envsubst（环境变量占位符替换）并导出 EASY_CONFIG。
# ============================================================

MODE="${AGENT_ENV:-prod}"
CONFIG_DIR="/app/easy_agent/config"

# 后端只使用唯一配置文件 config.yaml；AGENT_ENV 仅决定加载哪个 .env.{env}
CONFIG_FILE="$CONFIG_DIR/config.yaml"

case "$MODE" in
    prod) echo "==> 运行环境: PRODUCTION (AGENT_ENV=prod → .env.prod)" ;;
    test) echo "==> 运行环境: TEST (AGENT_ENV=test → .env.test)" ;;
    dev)  echo "==> 运行环境: DEVELOPMENT (AGENT_ENV=dev → .env.dev)" ;;
    *)    echo "==> 未知 AGENT_ENV: $MODE, 按 dev 处理（缺 .env.dev 时回退 .env）" ;;
esac

if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: 配置文件不存在: $CONFIG_FILE"
    exit 1
fi

echo "==> 使用配置文件: $CONFIG_FILE"

# Substitute environment variables in config file
# Replaces ${VAR} or ${VAR:-default} patterns with actual values
envsubst_args=$(grep -oP '\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}' "$CONFIG_FILE" 2>/dev/null | sort -u | tr '\n' ' ')
if [ -n "$envsubst_args" ]; then
    echo "==> 替换配置文件中的环境变量: $envsubst_args"
    var_names=$(echo "$envsubst_args" | grep -oP '\$\{[A-Za-z_][A-Za-z0-9_]*' | sed 's/\${//' | sort -u | while read var; do echo -n "\${$var} "; done)
    envsubst "$var_names" < "$CONFIG_FILE" > /tmp/config_resolved.yaml
    CONFIG_FILE="/tmp/config_resolved.yaml"
    echo "==> 环境变量替换完成"
fi

export EASY_CONFIG="$CONFIG_FILE"

# ---------- 运行期注入前端配置（后端地址 / AGENT_ENV 在容器启动时才知道）----------
# 前端在构建镜像时无法预知后端地址，这里根据运行期环境变量写出
# /app/frontend/dist/runtime-config.js（window.__RUNTIME_CONFIG__），
# 由前端在运行时读取，覆盖构建期固化的 VITE_API_BASE_URL。
RUNTIME_API_URL="${API_BASE_URL:-}"
RUNTIME_APP_TITLE="${APP_TITLE:-Easy Agent}"
if [ -z "$RUNTIME_API_URL" ]; then
  # 未显式指定后端地址时，默认相对路径 "/"：前端自动跟随当前访问入口（origin），
  # 适用于 uvicorn 同进程托管前端的部署；serve 分离部署请显式传 API_BASE_URL。
  RUNTIME_API_URL="/"
fi
cat > /app/frontend/dist/runtime-config.js <<EOF
// 本文件由启动脚本根据运行期环境变量自动生成，请勿手工编辑。
window.__RUNTIME_CONFIG__ = {
  API_BASE_URL: "${RUNTIME_API_URL}",
  APP_TITLE: "${RUNTIME_APP_TITLE}",
  AGENT_ENV: "${MODE}"
};
EOF
echo "==> 前端运行期配置: API_BASE_URL=${RUNTIME_API_URL}  AGENT_ENV=${MODE}"

echo "==> 启动 Easy Agent (端口 8000)..."
echo "============================================================"

# --timeout-graceful-shutdown: `docker stop` 默认 10s 后 SIGKILL，
# 而 uvicorn 默认无限等待现有连接（SSE 流式输出、终端 WebSocket），
# 不设上限会导致容器每次都走到强杀、来不及优雅收尾。
exec python -m uvicorn easy_agent.app:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --no-access-log \
    --timeout-graceful-shutdown "${EASY_GRACEFUL_SHUTDOWN_SECONDS:-5}"
