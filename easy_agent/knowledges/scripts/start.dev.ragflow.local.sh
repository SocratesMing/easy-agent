#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EASY_AGENT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RAGFLOW_DIR="$(cd -- "$EASY_AGENT_DIR/../ragflow-v0.17.2" && pwd)"
RAGFLOW_DOCKER_DIR="$RAGFLOW_DIR/docker"
OLLAMA_LOG_DIR="$EASY_AGENT_DIR/logs"

command -v colima >/dev/null || { echo "缺少 colima，请先安装。" >&2; exit 1; }
command -v docker-compose >/dev/null || { echo "缺少 docker-compose。" >&2; exit 1; }
command -v ollama >/dev/null || { echo "缺少 ollama，请先安装。" >&2; exit 1; }

if ! docker info >/dev/null 2>&1; then
  echo "启动 Colima x86_64 运行时..."
  colima start --arch x86_64 --cpu 4 --memory 8 --disk 40
fi

echo "启动 RAGFlow v0.17.2-slim 及依赖组件..."
(
  cd "$RAGFLOW_DOCKER_DIR"
  docker-compose -f docker-compose.yml up -d
)

if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "启动宿主机 Ollama..."
  mkdir -p "$OLLAMA_LOG_DIR"
  nohup env OLLAMA_HOST=127.0.0.1:11434 ollama serve \
    >>"$OLLAMA_LOG_DIR/ollama.log" 2>&1 &
fi

for _ in {1..30}; do
  if curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! ollama list | awk 'NR > 1 {print $1}' | grep -qx 'bge-m3:latest'; then
  echo "首次下载 bge-m3 向量模型..."
  OLLAMA_HOST=http://127.0.0.1:11434 ollama pull bge-m3
fi

echo "等待 RAGFlow API 就绪..."
for _ in {1..60}; do
  if curl -fsS --max-time 3 http://127.0.0.1:9380/api/v1/datasets >/dev/null 2>&1; then
    echo "RAGFlow 已就绪：http://127.0.0.1/"
    echo "API 地址：http://127.0.0.1:9380/api/v1"
    echo "Ollama：http://127.0.0.1:11434"
    exit 0
  fi
  sleep 5
done

echo "RAGFlow 在 5 分钟内未就绪，请运行 scripts/status.dev.ragflow.local.sh 检查。" >&2
exit 1
