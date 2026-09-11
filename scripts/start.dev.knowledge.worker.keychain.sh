#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACCOUNT_NAME="${EASY_AGENT_KEYCHAIN_ACCOUNT:-$USER}"
DEEPSEEK_SERVICE="${EASY_AGENT_KEYCHAIN_SERVICE:-easy-agent-deepseek-api-key}"
RAGFLOW_SERVICE="${RAGFLOW_KEYCHAIN_SERVICE:-easy-agent-ragflow-api-key}"

read_keychain_secret() {
  local service="$1"
  local label="$2"
  local value
  if ! value="$(security find-generic-password -a "$ACCOUNT_NAME" -s "$service" -w 2>/dev/null)" || [[ -z "$value" ]]; then
    echo "Keychain 中未找到${label}"
    exit 1
  fi
  printf '%s' "$value"
}

cd "$PROJECT_ROOT"
export DEEPSEEK_API_KEY="$(read_keychain_secret "$DEEPSEEK_SERVICE" " DeepSeek API key")"
export RAGFLOW_API_KEY="$(read_keychain_secret "$RAGFLOW_SERVICE" " RAGFlow API key")"
export AGENT_ENV="${AGENT_ENV:-dev}"

exec .venv/bin/python -m easy_agent.knowledge.worker
