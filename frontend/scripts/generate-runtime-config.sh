#!/bin/bash
# ============================================================
# 运行期前端配置生成器
#
# 解决“构建镜像时未知 AGENT_ENV / 后端地址”的问题：
# 前端在 build 时把 VITE_API_BASE_URL 固化进 bundle，无法预知部署环境的
# 后端地址。本脚本在 serve / 容器启动时根据环境变量写出 dist/runtime-config.js，
# 前端运行时读取 window.__RUNTIME_CONFIG__ 覆盖构建期值。
#
# 关键点：build 后前端按 AGENT_ENV 的值加载“该环境专属配置”。
#   本脚本读取各 .env.<mode> 中的 VITE_API_BASE_URL，构造 ENV_CONFIG 表写入
#   runtime-config.js，前端运行时用 AGENT_ENV 索引对应环境的后端地址。
#
# 环境变量：
#   API_BASE_URL  显式后端地址（推荐，容器/serve 分离部署必填，优先级最高）
#                例：http://easy-agent-backend:8000
#   AGENT_ENV     环境标识（dev/test/prod），决定加载哪份环境配置（默认 prod）
#   APP_TITLE     应用名称（可选）
#   DIST_DIR      输出目录（默认 dist）
#
# 后端地址选择优先级（高 -> 低）：
#   API_BASE_URL（显式，跨域部署用） > 构建期 VITE_API_BASE_URL（bundle 内）
#   > "/"（默认：同源相对路径，后端不设置跨域时的正确选择）
# 注：ENV_CONFIG 表仅作各环境地址参考/打印，默认不自动套用（避免产生跨域）。
# ============================================================
set -e

DIST_DIR="${DIST_DIR:-dist}"
OUT_FILE="$DIST_DIR/runtime-config.js"
mkdir -p "$DIST_DIR"

APP_TITLE="${APP_TITLE:-Easy Agent}"
AGENT_ENV_VAL="${AGENT_ENV:-prod}"

# 读取各环境 .env.<mode> 中的 VITE_API_BASE_URL，作为该环境默认后端地址。
# 这是“按 AGENT_ENV 加载不同环境配置”的唯一数据源（与构建期保持一致）。
read_env_api() {
  local env="$1"
  local env_file=".env.${env}"
  local url=""
  if [ -f "$env_file" ]; then
    url=$(grep -E "^VITE_API_BASE_URL=" "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- \
            | sed "s/^[\"']//;s/[\"']$//")
  fi
  echo "$url"
}

DEV_API=$(read_env_api dev)
TEST_API=$(read_env_api test)
PROD_API=$(read_env_api prod)

# 后端“不设置跨域(CORS)”时，前端必须与后端同源访问，否则浏览器会拦截请求。
# 故默认（未显式给定 API_BASE_URL）使用相对路径 "/"，让前端跟随当前访问入口
# （即由后端自身托管 dist/，访问 http://<host>:<port>/ 即可，无需跨域）。
# 仅当确实是“前端独立托管 + 跨域部署”且后端已开启 CORS 时，才显式传 API_BASE_URL。
if [ -n "$API_BASE_URL" ]; then
  RT_API="$API_BASE_URL"
  RT_API_SRC="显式 API_BASE_URL (跨域，需后端开启 CORS)"
else
  RT_API="/"
  RT_API_SRC="相对路径 / (同源访问，无需跨域)"
fi

cat > "$OUT_FILE" <<EOF
// 本文件由启动脚本根据运行期环境变量自动生成，请勿手工编辑。
// 前端运行时根据 AGENT_ENV 值从 ENV_CONFIG 加载对应环境配置。
window.__RUNTIME_CONFIG__ = {
  AGENT_ENV: "${AGENT_ENV_VAL}",
  API_BASE_URL: "${RT_API}",
  APP_TITLE: "${APP_TITLE}",
  ENV_CONFIG: {
    dev:  { API_BASE_URL: "${DEV_API:-}" },
    test: { API_BASE_URL: "${TEST_API:-}" },
    prod: { API_BASE_URL: "${PROD_API:-}" }
  }
};
EOF

echo "==> 已生成运行期前端配置: $OUT_FILE"
echo "    AGENT_ENV        : ${AGENT_ENV_VAL}"
echo "    生效后端地址     : ${RT_API}  (来源: ${RT_API_SRC})"
echo "    各环境默认地址   : dev=${DEV_API:-<空>}  test=${TEST_API:-<空>}  prod=${PROD_API:-<空>}"
