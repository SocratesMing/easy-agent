#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACCOUNT_NAME="${EASY_AGENT_KEYCHAIN_ACCOUNT:-$USER}"
DEEPSEEK_SERVICE="${EASY_AGENT_KEYCHAIN_SERVICE:-easy-agent-deepseek-api-key}"
RAGFLOW_SERVICE="${RAGFLOW_KEYCHAIN_SERVICE:-easy-agent-ragflow-api-key}"
JWT_SERVICE="${EASY_AGENT_JWT_KEYCHAIN_SERVICE:-easy-agent-jwt-secret}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

read_keychain_secret() {
  local service="$1"
  local label="$2"
  local value
  if ! value="$(security find-generic-password -a "$ACCOUNT_NAME" -s "$service" -w 2>/dev/null)" || [[ -z "$value" ]]; then
    echo "Keychain 中未找到${label}：service=$service account=$ACCOUNT_NAME"
    exit 1
  fi
  printf '%s' "$value"
}

read_or_create_keychain_secret() {
  local service="$1"
  local value
  if value="$(security find-generic-password -a "$ACCOUNT_NAME" -s "$service" -w 2>/dev/null)" && [[ -n "$value" ]]; then
    printf '%s' "$value"
    return
  fi
  value="$(openssl rand -base64 48)"
  security add-generic-password -U -a "$ACCOUNT_NAME" -s "$service" -w "$value" >/dev/null
  printf '%s' "$value"
}

if ! command -v security >/dev/null 2>&1; then
  echo "未找到 macOS security 命令，无法从 Keychain 读取密钥。"
  exit 1
fi

cd "$PROJECT_ROOT"

DEEPSEEK_API_KEY="$(read_keychain_secret "$DEEPSEEK_SERVICE" " DeepSeek API key")"
RAGFLOW_API_KEY="$(read_keychain_secret "$RAGFLOW_SERVICE" " RAGFlow API key")"
EASY_JWT_SECRET="$(read_or_create_keychain_secret "$JWT_SERVICE")"

export DEEPSEEK_API_KEY RAGFLOW_API_KEY EASY_JWT_SECRET
export AGENT_ENV="${AGENT_ENV:-dev}"

exec .venv/bin/python -m uvicorn easy_agent.app:app \
  --host "$HOST" \
  --port "$PORT" \
  --log-level info \
  --no-access-log
