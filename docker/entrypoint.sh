#!/bin/bash
set -e

# ============================================================
# Easy Agent Entrypoint
# Selects config file based on ENV_MODE (prod|test|dev)
# ============================================================

MODE="${ENV_MODE:-prod}"
CONFIG_DIR="/app/easy_agent/config"

case "$MODE" in
    prod)
        CONFIG_FILE="$CONFIG_DIR/config.prod.yaml"
        echo "==> Running in PRODUCTION mode"
        ;;
    test)
        CONFIG_FILE="$CONFIG_DIR/config.test.yaml"
        echo "==> Running in TEST mode"
        ;;
    dev)
        CONFIG_FILE="$CONFIG_DIR/config.dev.yaml"
        echo "==> Running in DEVELOPMENT mode"
        ;;
    *)
        echo "==> Unknown ENV_MODE: $MODE, falling back to PRODUCTION"
        CONFIG_FILE="$CONFIG_DIR/config.prod.yaml"
        ;;
esac

if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config file not found: $CONFIG_FILE"
    echo "Falling back to default config"
    CONFIG_FILE="$CONFIG_DIR/config.yaml"
fi

echo "==> Using config: $CONFIG_FILE"

# Substitute environment variables in config file
# Replaces ${VAR} or ${VAR:-default} patterns with actual values
envsubst_args=$(grep -oP '\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}' "$CONFIG_FILE" 2>/dev/null | sort -u | tr '\n' ' ')
if [ -n "$envsubst_args" ]; then
    echo "==> Substituting env vars in config: $envsubst_args"
    # Get unique variable names for envsubst
    var_names=$(echo "$envsubst_args" | grep -oP '\$\{[A-Za-z_][A-Za-z0-9_]*' | sed 's/\${//' | sort -u | while read var; do echo -n "\${$var} "; done)
    envsubst "$var_names" < "$CONFIG_FILE" > /tmp/config_resolved.yaml
    CONFIG_FILE="/tmp/config_resolved.yaml"
    echo "==> Config resolved with env vars"
fi

export EASY_CONFIG="$CONFIG_FILE"

echo "==> Starting Easy Agent on port 8000..."
echo "============================================================"

exec python -m uvicorn easy_agent.app:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --no-access-log
