#!/usr/bin/env bash
set -u

echo "== Docker containers =="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' \
  --filter 'name=ragflow-' 2>&1 || true

echo
echo "== HTTP endpoints =="
for endpoint in \
  "RAGFlow Web|http://127.0.0.1/" \
  "RAGFlow API|http://127.0.0.1:9380/api/v1/datasets" \
  "Ollama|http://127.0.0.1:11434/api/tags" \
  "EasyAgent|http://127.0.0.1:8000/api/knowledge/v1/capabilities"
do
  name="${endpoint%%|*}"
  url="${endpoint#*|}"
  code="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
  if [[ "$code" =~ ^2 ]]; then
    printf '%-14s ready (%s)\n' "$name" "$code"
  else
    printf '%-14s unavailable (%s)\n' "$name" "${code:-000}"
  fi
done

echo
echo "== Ollama models =="
ollama list 2>&1 || true
