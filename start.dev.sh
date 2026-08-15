#!/bin/bash
# ============================================================
# Easy Agent - 开发环境启动脚本
#
# 用法:
#   ./start.dev.sh                 # 默认 dev 环境：后端 :8000 + 前端 :5173
#   ./start.dev.sh --port 8001     # 指定后端端口
#   ./start.dev.sh --skip-install  # 跳过依赖安装
#
# AGENT_ENV: dev (默认)
# 后端: uvicorn --reload（热重载），加载 config.dev.yaml
# 前端: vite dev server（HMR），按平台/AGENT_ENV 加载对应 .env 文件（.env.win / .env.dev / .env.test / .env.prod）
# ============================================================
set -e

# ---------- 变量默认值 ----------
export AGENT_ENV="${AGENT_ENV:-dev}"
_os_kernel="$(uname -s 2>/dev/null || echo Linux)"
case "$_os_kernel" in
    MINGW*|MSYS*|CYGWIN*) HOST="127.0.0.1" ;;
    *)                    HOST="0.0.0.0" ;;
esac
PORT=8000
SKIP_INSTALL=false

# ---------- 解析参数 ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)         HOST="$2"; shift 2 ;;
        --port)         PORT="$2"; shift 2 ;;
        --skip-install) SKIP_INSTALL=true; shift ;;
        -h|--help)
            echo "用法: ./start.dev.sh [--host 0.0.0.0] [--port 8000] [--skip-install]"
            echo "环境变量: AGENT_ENV (默认 dev)"
            exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# ---------- 横幅 ----------
echo "============================================================"
echo "  Easy Agent - 开发环境"
echo "------------------------------------------------------------"
echo "  AGENT_ENV : $AGENT_ENV"
echo "  后端       : http://${HOST}:${PORT} (uvicorn --reload)"
echo "  前端       : vite dev server (默认 :5173, HMR)"
echo "  项目目录   : $PROJECT_ROOT"
echo "  启动时间   : $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 前置检查 ----------
echo "[1/3] 环境检查..."
check_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "❌ 未找到命令: $1，请先安装。"
        echo "   提示: $2"
        exit 1
    fi
}
check_cmd python "需要 Python >= 3.11"
check_cmd uv "安装: curl -LsSf https://astral.sh/uv/install.sh | sh"
check_cmd node "需要 Node.js >= 20"
check_cmd npm "随 Node.js 一同安装"
echo "  ✅ python $(python --version 2>&1 | awk '{print $2}')"
echo "  ✅ uv $(uv --version 2>&1)"
echo "  ✅ node $(node --version)"

# ---------- 依赖安装 ----------
echo ""
echo "[2/3] 安装依赖..."
if [[ "$SKIP_INSTALL" == "true" ]]; then
    echo "  ⏭  已跳过（--skip-install）"
else
    uv sync
    echo "  ✅ 后端依赖安装完成"
    cd "$PROJECT_ROOT/frontend"
    npm install --registry=https://registry.npmmirror.com
    cd "$PROJECT_ROOT"
    echo "  ✅ 前端依赖安装完成"
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

# ---------- 启动服务 ----------
echo ""
echo "[3/3] 启动服务..."
echo "============================================================"
echo "  🚀 后端启动中... (http://${HOST}:${PORT}, --reload)"
echo "  🚀 前端启动中... (vite, 默认 :5173)"
echo "  停止: Ctrl+C（同时关闭前后端）"
echo "============================================================"

# 后端：后台启动（热重载），记录 PID
uv run python -m uvicorn easy_agent.app:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload &
BACKEND_PID=$!

# 退出时清理后端进程
cleanup() {
    echo ""
    echo "==> 停止后端服务 (PID: $BACKEND_PID)..."
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 前端：前台运行（Ctrl+C 退出后由 trap 关闭后端）
cd "$PROJECT_ROOT/frontend"
npm run dev
