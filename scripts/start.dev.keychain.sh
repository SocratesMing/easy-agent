#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${EASY_AGENT_KEYCHAIN_SERVICE:-easy-agent-deepseek-api-key}"
JWT_SERVICE="${EASY_AGENT_JWT_KEYCHAIN_SERVICE:-easy-agent-jwt-secret}"
ACCOUNT_NAME="${EASY_AGENT_KEYCHAIN_ACCOUNT:-$USER}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

cd "$PROJECT_ROOT"

if ! command -v security >/dev/null 2>&1; then
  echo "未找到 macOS security 命令，无法从 Keychain 读取 API key。"
  exit 1
fi

if ! DEEPSEEK_API_KEY="$(security find-generic-password -a "$ACCOUNT_NAME" -s "$SERVICE_NAME" -w 2>/dev/null)"; then
  echo "未在 Keychain 中找到 DeepSeek API key。"
  echo "请先按 API_KEY_SETUP.md 保存密钥：service=$SERVICE_NAME account=$ACCOUNT_NAME"
  exit 1
fi

if [[ -z "$DEEPSEEK_API_KEY" ]]; then
  echo "Keychain 中的 DeepSeek API key 为空，请重新保存。"
  exit 1
fi

if ! EASY_JWT_SECRET="$(security find-generic-password -a "$ACCOUNT_NAME" -s "$JWT_SERVICE" -w 2>/dev/null)" || [[ -z "$EASY_JWT_SECRET" ]]; then
  EASY_JWT_SECRET="$(openssl rand -base64 48)"
  security add-generic-password -U -a "$ACCOUNT_NAME" -s "$JWT_SERVICE" -w "$EASY_JWT_SECRET" >/dev/null
fi

export DEEPSEEK_API_KEY EASY_JWT_SECRET
export AGENT_ENV="${AGENT_ENV:-dev}"

exec .venv/bin/python -m uvicorn easy_agent.app:app \
  --host "$HOST" \
  --port "$PORT" \
  --log-level info \
  --no-access-log
