#!/bin/bash
set -e

# ============================================================
# Easy Agent Entrypoint
# AGENT_ENV: prod (default) | test | dev
# app.py 内部也通过 AGENT_ENV 选择配置文件，
# 此脚本额外负责 envsubst（环境变量占位符替换）。
# ============================================================

MODE="${AGENT_ENV:-prod}"
CONFIG_DIR="/app/easy_agent/config"

case "$MODE" in
    prod)
        CONFIG_FILE="$CONFIG_DIR/config.prod.yaml"
        echo "==> 运行环境: PRODUCTION (AGENT_ENV=prod)"
        ;;
    test)
        CONFIG_FILE="$CONFIG_DIR/config.test.yaml"
        echo "==> 运行环境: TEST (AGENT_ENV=test)"
        ;;
    dev)
        CONFIG_FILE="$CONFIG_DIR/config.dev.yaml"
        echo "==> 运行环境: DEVELOPMENT (AGENT_ENV=dev)"
        ;;
    *)
        echo "==> 未知 AGENT_ENV: $MODE, 回退到 config.yaml"
        CONFIG_FILE="$CONFIG_DIR/config.yaml"
        ;;
esac

if [ ! -f "$CONFIG_FILE" ]; then
    echo "WARNING: 配置文件不存在: $CONFIG_FILE, 回退到 config.yaml"
    CONFIG_FILE="$CONFIG_DIR/config.yaml"
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

echo "==> 启动 Easy Agent (端口 8000)..."
echo "============================================================"

exec python -m uvicorn easy_agent.app:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --no-access-log
