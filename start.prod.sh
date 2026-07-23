#!/bin/bash
# ============================================================
# Easy Agent - 生产环境启动脚本
#
# 用法:
#   ./start.prod.sh                 # 默认 prod 环境，端口 8000
#   AGENT_ENV=test ./start.prod.sh  # 指定环境
#   ./start.prod.sh --port 9000     # 指定端口
#   ./start.prod.sh --skip-build    # 跳过前端构建（已构建过）
#
# AGENT_ENV: prod (默认) | test | dev
# 前端构建后由后端 FastAPI 静态托管（frontend/dist/）
# ============================================================
set -e

# ---------- 变量默认值 ----------
export AGENT_ENV="${AGENT_ENV:-prod}"
# 根据操作系统选择默认监听地址：
#   Linux / macOS / WSL -> 0.0.0.0（允许外部访问，适合服务器/容器）
#   Windows (Git Bash/MINGW) -> 127.0.0.1（仅本机访问，避免防火墙弹窗）
_os_kernel="$(uname -s 2>/dev/null || echo Linux)"
case "$_os_kernel" in
    MINGW*|MSYS*|CYGWIN*) HOST="127.0.0.1" ;;
    *)                    HOST="0.0.0.0" ;;
esac
PORT=8000
WORKERS=1
SKIP_BUILD=false
SKIP_INSTALL=false

# ---------- 解析参数 ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)        HOST="$2"; shift 2 ;;
        --port)        PORT="$2"; shift 2 ;;
        --workers)     WORKERS="$2"; shift 2 ;;
        --skip-build)  SKIP_BUILD=true; shift ;;
        --skip-install) SKIP_INSTALL=true; shift ;;
        -h|--help)
            echo "用法: ./start.prod.sh [--host 0.0.0.0] [--port 8000] [--workers 1] [--skip-build] [--skip-install]"
            echo "环境变量: AGENT_ENV (prod|test|dev，默认 prod)"
            exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# 项目根目录（脚本所在目录）
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# ---------- 横幅 ----------
echo "============================================================"
echo "  Easy Agent - 生产环境启动"
echo "------------------------------------------------------------"
echo "  AGENT_ENV : $AGENT_ENV"
echo "  HOST      : $HOST"
echo "  PORT      : $PORT"
echo "  WORKERS   : $WORKERS"
echo "  项目目录   : $PROJECT_ROOT"
echo "  启动时间   : $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 前置检查 ----------
echo "[1/4] 环境检查..."

check_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "❌ 未找到命令: $1，请先安装。"
        echo "   提示: $2"
        exit 1
    fi
}

check_cmd python "需要 Python >= 3.11"
check_cmd uv "安装: pip install uv"
check_cmd node "需要 Node.js >= 20（仅构建前端需要）"
check_cmd npm "随 Node.js 一同安装"

echo "  ✅ python $(python --version 2>&1 | awk '{print $2}')"
echo "  ✅ uv $(uv --version 2>&1)"
echo "  ✅ node $(node --version)"

# ---------- 后端依赖 ----------
echo ""
echo "[2/4] 安装后端依赖..."
if [[ "$SKIP_INSTALL" == "true" ]]; then
    echo "  ⏭  已跳过（--skip-install）"
else
    uv sync
    echo "  ✅ 后端依赖安装完成"
fi

# ---------- 前端构建 ----------
echo ""
echo "[3/4] 构建前端..."
if [[ "$SKIP_BUILD" == "true" ]]; then
    echo "  ⏭  已跳过（--skip-build）"
else
    cd "$PROJECT_ROOT/frontend"
    npm install --registry=https://registry.npmmirror.com
    # AGENT_ENV 已在环境中，vite.config.js 会据此选择 .env.prod
    npm run build
    cd "$PROJECT_ROOT"
    echo "  ✅ 前端构建完成 -> frontend/dist/"
fi

# 校验前端产物存在
if [[ ! -f "$PROJECT_ROOT/frontend/dist/index.html" ]]; then
    echo "❌ 前端构建产物不存在: frontend/dist/index.html"
    echo "   请移除 --skip-build 或手动执行: cd frontend && npm run build"
    exit 1
fi

# ---------- 配置文件检查 ----------
CONFIG_FILE="$PROJECT_ROOT/easy_agent/config/config.${AGENT_ENV}.yaml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "⚠️  配置文件不存在: $CONFIG_FILE，将回退到 config.yaml"
    CONFIG_FILE="$PROJECT_ROOT/easy_agent/config/config.yaml"
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "❌ 配置文件不存在: $CONFIG_FILE"
    exit 1
fi
echo ""
echo "  配置文件: $CONFIG_FILE"

# ---------- 启动后端 ----------
echo ""
echo "[4/4] 启动服务..."
echo "============================================================"
echo "  🚀 Easy Agent 启动中... (http://${HOST}:${PORT})"
echo "  前端: 由后端静态托管 (frontend/dist/)"
echo "  停止: Ctrl+C"
echo "============================================================"

# 使用 uv run 启动，确保使用项目虚拟环境
exec uv run python -m uvicorn easy_agent.app:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level info \
    --no-access-log
